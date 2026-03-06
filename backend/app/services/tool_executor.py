from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any, TypedDict
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select

from app.core.config import get_settings
from app.models.ledger import Ledger
from app.models.reminder_delivery import ReminderDelivery
from app.models.schedule import Schedule
from app.models.user import User
from app.services.admin_tools import is_tool_enabled
from app.services.conversations import ensure_active_conversation, list_conversations
from app.services.memory import list_long_term_memories
from app.services.mcp_fetch import get_mcp_client_for_tool, get_mcp_fetch_client
from app.services.runtime_context import get_scheduler, get_session
from app.services.scheduler_tasks import send_reminder_job
from app.services.tool_registry import (
    get_allowed_mcp_tool_names_for,
    is_mcp_tool_allowed,
    list_runtime_tool_metas,
)
from app.services.usage import enqueue_tool_usage
from app.tools.finance import (
    delete_ledger,
    get_latest_ledger,
    insert_ledger,
    list_recent_ledgers,
    update_ledger,
)
from app.tools.ledger_text2sql import (
    commit_write_by_ids_text2sql,
    plan_write_preview_text2sql,
    try_execute_ledger_text2sql,
)
from app.tools.vision import analyze_receipt


class ToolExecResult(TypedDict):
    ok: bool
    source: str
    name: str
    output: str
    output_data: Any | None
    error: str
    latency_ms: int


BUILTIN_TOOL_ALIAS: dict[str, str] = {
    "mcp_list_tools": "tool_list",
    "mcp_call_tool": "tool_call",
}


def _render_now_time(timezone: str) -> str:
    tz = (timezone or "").strip() or "Asia/Shanghai"
    try:
        now = datetime.now(ZoneInfo(tz))
        return f"{tz} 当前时间：{now.strftime('%Y-%m-%d %H:%M:%S')}"
    except Exception:
        now = datetime.utcnow()
        return f"UTC 当前时间：{now.strftime('%Y-%m-%d %H:%M:%S')}"


def _render_mcp_tool_rows(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "当前无可用外部工具。"
    lines: list[str] = []
    for item in rows:
        name = str(item.get("name") or "").strip() or "unknown"
        desc = str(item.get("description") or "").strip() or "无描述"
        enabled = bool(item.get("enabled") is True)
        lines.append(f"- {name} | enabled={str(enabled).lower()} | {desc}")
    return "\n".join(lines)


def _try_parse_json_payload(text: str) -> Any | None:
    payload = (text or "").strip()
    if not payload:
        return None
    try:
        return json.loads(payload)
    except Exception:
        return None


def _resolve_user_id(value: Any) -> int:
    try:
        user_id = int(value or 0)
    except Exception:
        user_id = 0
    return user_id


def _parse_datetime_arg(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.replace("T", " ").replace("/", "-")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(normalized)
    except Exception:
        return None
    if dt.tzinfo is None:
        return dt
    local_tz = ZoneInfo(get_settings().timezone)
    return dt.astimezone(local_tz).replace(tzinfo=None)


def _ledger_to_payload(row: Ledger) -> dict[str, Any]:
    return {
        "id": int(row.id or 0),
        "user_id": int(row.user_id),
        "amount": float(row.amount),
        "currency": str(row.currency or "CNY"),
        "category": str(row.category or ""),
        "item": str(row.item or ""),
        "image_url": str(row.image_url or ""),
        "transaction_date": row.transaction_date.isoformat(sep=" ", timespec="seconds") if row.transaction_date else "",
    }


def _schedule_to_payload(row: Schedule) -> dict[str, Any]:
    return {
        "id": int(row.id or 0),
        "user_id": int(row.user_id),
        "job_id": str(row.job_id or ""),
        "content": str(row.content or ""),
        "status": str(row.status or ""),
        "trigger_time": row.trigger_time.isoformat(sep=" ", timespec="seconds") if row.trigger_time else "",
    }


def _conversation_to_payload(row: Any, active_id: int | None) -> dict[str, Any]:
    row_id = int(getattr(row, "id", 0) or 0)
    return {
        "id": row_id,
        "title": str(getattr(row, "title", "") or ""),
        "summary": str(getattr(row, "summary", "") or ""),
        "last_message_at": (
            getattr(row, "last_message_at").isoformat(sep=" ", timespec="seconds")
            if getattr(row, "last_message_at", None) is not None
            else ""
        ),
        "active": bool(active_id and row_id == int(active_id)),
    }


async def _log_tool_usage_safe(
    *,
    user_id: int | None,
    platform: str,
    conversation_id: int | None,
    tool_source: str,
    tool_name: str,
    success: bool,
    latency_ms: int,
    error: str = "",
) -> None:
    try:
        enqueue_tool_usage(
            user_id=user_id,
            platform=platform,
            conversation_id=conversation_id,
            tool_source=tool_source,
            tool_name=tool_name,
            success=success,
            latency_ms=latency_ms,
            error=error,
        )
    except Exception:
        return


async def execute_capability(
    *,
    source: str,
    name: str,
    args: dict[str, Any] | None = None,
    user_id: int | None = None,
    platform: str = "",
    conversation_id: int | None = None,
) -> ToolExecResult:
    started = time.perf_counter()
    src = str(source or "").strip().lower()
    raw_tool = str(name or "").strip()
    tool_l = raw_tool.lower()
    tool_l = BUILTIN_TOOL_ALIAS.get(tool_l, tool_l)
    tool = tool_l
    params = dict(args or {})
    settings = get_settings()

    def _result(
        ok: bool,
        output: str = "",
        output_data: Any | None = None,
        error: str = "",
    ) -> ToolExecResult:
        latency_ms = int((time.perf_counter() - started) * 1000)
        parsed_output = output_data
        if ok and parsed_output is None:
            parsed_output = _try_parse_json_payload(output)
        return {
            "ok": ok,
            "source": src,
            "name": tool,
            "output": output if ok else "",
            "output_data": parsed_output if ok else None,
            "error": error if not ok else "",
            "latency_ms": latency_ms,
        }

    if src not in {"builtin", "mcp"}:
        return _result(False, error=f"unsupported tool source: {src}")

    if not tool_l:
        return _result(False, error="missing tool name")

    try:
        if src == "builtin":
            if not await is_tool_enabled("builtin", tool_l):
                return _result(False, error=f"tool `{tool_l}` is disabled by admin.")

            if tool_l == "now_time":
                timezone = str(params.get("timezone") or settings.timezone or "Asia/Shanghai").strip()
                return _result(True, output=_render_now_time(timezone))

            if tool_l == "fetch_url":
                if not settings.mcp_fetch_enabled:
                    return _result(False, error="MCP fetch is disabled.")
                target = str(params.get("url") or "").strip()
                if not target:
                    return _result(False, error="missing required arg: url")
                max_length = max(500, min(20000, int(params.get("max_length") or settings.mcp_fetch_default_max_length)))
                start_index = max(0, int(params.get("start_index") or 0))
                raw = bool(params.get("raw"))
                output = await get_mcp_fetch_client().fetch(
                    url=target,
                    max_length=max_length,
                    start_index=start_index,
                    raw=raw,
                )
                return _result(True, output=output)

            if tool_l == "tool_list":
                if not settings.mcp_fetch_enabled:
                    return _result(False, error="MCP fetch is disabled.")
                runtime_tools = await list_runtime_tool_metas()
                mcp_tools = [dict(item) for item in runtime_tools if str(item.get("source") or "") == "mcp"]
                return _result(True, output=_render_mcp_tool_rows(mcp_tools))

            if tool_l == "tool_call":
                if not settings.mcp_fetch_enabled:
                    return _result(False, error="MCP fetch is disabled.")
                target_name = str(params.get("tool_name") or params.get("name") or "").strip()
                if not target_name:
                    return _result(False, error="missing required arg: tool_name")
                if not is_mcp_tool_allowed(target_name):
                    allowed = sorted(get_allowed_mcp_tool_names_for(target_name))
                    allowed_text = ", ".join(allowed) if allowed else "none"
                    return _result(False, error=f"MCP tool `{target_name}` is blocked by allowlist. Allowed tools: {allowed_text}.")
                if not await is_tool_enabled("mcp", target_name):
                    return _result(False, error=f"MCP tool `{target_name}` is disabled by admin.")
                target_args = params.get("arguments")
                if not isinstance(target_args, dict):
                    target_args = {}
                output = await get_mcp_client_for_tool(target_name).call_tool(name=target_name, arguments=target_args)
                return _result(True, output=output)

            if tool_l == "analyze_receipt":
                image_url = str(params.get("image_url") or params.get("image_ref") or "").strip()
                if not image_url:
                    return _result(False, error="missing required arg: image_url")
                output = await analyze_receipt(image_url)
                payload = output if isinstance(output, dict) else {"result": str(output)}
                return _result(
                    True,
                    output=json.dumps(payload, ensure_ascii=False),
                    output_data=payload,
                )

            if tool_l == "ledger_text2sql":
                uid_raw = params.get("user_id", user_id)
                try:
                    uid = int(uid_raw or 0)
                except Exception:
                    uid = 0
                if uid <= 0:
                    return _result(False, error="missing required arg: user_id")
                message = str(params.get("message") or "").strip()
                if not message:
                    return _result(False, error="missing required arg: message")
                conversation_context = str(params.get("conversation_context") or "").strip()
                mode = str(params.get("mode") or "execute").strip().lower()
                if mode == "preview_write":
                    operation = str(params.get("operation") or "").strip().lower()
                    preview_limit = int(params.get("preview_limit") or 50)
                    preview_hints = params.get("preview_hints")
                    if not isinstance(preview_hints, dict):
                        preview_hints = {}
                    update_fields = params.get("update_fields")
                    if not isinstance(update_fields, dict):
                        update_fields = {}
                    output_data = await plan_write_preview_text2sql(
                        user_id=uid,
                        message=message,
                        operation=operation,
                        conversation_context=conversation_context,
                        preview_limit=preview_limit,
                        preview_hints=preview_hints,
                        update_fields=update_fields,
                    )
                    return _result(
                        True,
                        output=json.dumps(output_data or {}, ensure_ascii=False),
                        output_data=output_data or {},
                    )
                if mode == "commit_write_by_ids":
                    operation = str(params.get("operation") or "").strip().lower()
                    raw_ids = params.get("target_ids")
                    target_ids = raw_ids if isinstance(raw_ids, list) else []
                    expected_count = int(params.get("expected_count") or 0)
                    update_fields = params.get("update_fields")
                    if not isinstance(update_fields, dict):
                        update_fields = {}
                    output_data = await commit_write_by_ids_text2sql(
                        user_id=uid,
                        operation=operation,
                        target_ids=target_ids,
                        expected_count=expected_count,
                        update_fields=update_fields,
                    )
                    return _result(
                        True,
                        output=json.dumps(output_data or {}, ensure_ascii=False),
                        output_data=output_data or {},
                    )

                output = await try_execute_ledger_text2sql(
                    user_id=uid,
                    message=message,
                    conversation_context=conversation_context,
                )
                return _result(True, output=str(output or ""))

            if tool_l == "ledger_insert":
                uid = _resolve_user_id(params.get("user_id", user_id))
                if uid <= 0:
                    return _result(False, error="missing required arg: user_id")
                amount_raw = params.get("amount")
                try:
                    amount = float(amount_raw)
                except Exception:
                    amount = 0.0
                if amount <= 0:
                    return _result(False, error="invalid amount")
                category = str(params.get("category") or "其他").strip() or "其他"
                item = str(params.get("item") or "消费").strip() or "消费"
                transaction_date = _parse_datetime_arg(params.get("transaction_date")) or datetime.utcnow()
                image_url = str(params.get("image_url") or "").strip() or None
                session = get_session()
                row = await insert_ledger(
                    session=session,
                    user_id=uid,
                    amount=amount,
                    category=category,
                    item=item,
                    transaction_date=transaction_date,
                    image_url=image_url,
                    platform=platform,
                )
                payload = _ledger_to_payload(row)
                return _result(
                    True,
                    output=json.dumps(payload, ensure_ascii=False),
                    output_data=payload,
                )

            if tool_l == "ledger_update":
                uid = _resolve_user_id(params.get("user_id", user_id))
                if uid <= 0:
                    return _result(False, error="missing required arg: user_id")
                ledger_id = _resolve_user_id(params.get("ledger_id"))
                if ledger_id <= 0:
                    return _result(False, error="missing required arg: ledger_id")
                amount_value = params.get("amount")
                amount = None
                if amount_value is not None and str(amount_value).strip() != "":
                    try:
                        amount = float(amount_value)
                    except Exception:
                        return _result(False, error="invalid amount")
                category = str(params.get("category") or "").strip() or None
                item = str(params.get("item") or "").strip() or None
                transaction_date = _parse_datetime_arg(params.get("transaction_date"))
                session = get_session()
                row = await update_ledger(
                    session=session,
                    user_id=uid,
                    ledger_id=ledger_id,
                    amount=amount,
                    category=category,
                    item=item,
                    transaction_date=transaction_date,
                    platform=platform,
                )
                if row is None:
                    return _result(False, error="ledger not found")
                payload = _ledger_to_payload(row)
                return _result(
                    True,
                    output=json.dumps(payload, ensure_ascii=False),
                    output_data=payload,
                )

            if tool_l == "ledger_delete":
                uid = _resolve_user_id(params.get("user_id", user_id))
                if uid <= 0:
                    return _result(False, error="missing required arg: user_id")
                ledger_id = _resolve_user_id(params.get("ledger_id"))
                if ledger_id <= 0:
                    return _result(False, error="missing required arg: ledger_id")
                session = get_session()
                row = await delete_ledger(
                    session=session,
                    user_id=uid,
                    ledger_id=ledger_id,
                    platform=platform,
                )
                if row is None:
                    return _result(False, error="ledger not found")
                payload = _ledger_to_payload(row)
                return _result(
                    True,
                    output=json.dumps(payload, ensure_ascii=False),
                    output_data=payload,
                )

            if tool_l == "ledger_get_latest":
                uid = _resolve_user_id(params.get("user_id", user_id))
                if uid <= 0:
                    return _result(False, error="missing required arg: user_id")
                session = get_session()
                row = await get_latest_ledger(session=session, user_id=uid)
                if row is None:
                    return _result(True, output=json.dumps({}, ensure_ascii=False), output_data={})
                payload = _ledger_to_payload(row)
                return _result(
                    True,
                    output=json.dumps(payload, ensure_ascii=False),
                    output_data=payload,
                )

            if tool_l == "ledger_list_recent":
                uid = _resolve_user_id(params.get("user_id", user_id))
                if uid <= 0:
                    return _result(False, error="missing required arg: user_id")
                limit = max(1, min(200, int(params.get("limit") or 10)))
                session = get_session()
                rows = await list_recent_ledgers(session=session, user_id=uid, limit=limit)
                payload = [_ledger_to_payload(item) for item in rows]
                return _result(
                    True,
                    output=json.dumps(payload, ensure_ascii=False),
                    output_data=payload,
                )

            if tool_l == "ledger_list":
                uid = _resolve_user_id(params.get("user_id", user_id))
                if uid <= 0:
                    return _result(False, error="missing required arg: user_id")
                limit = max(1, min(500, int(params.get("limit") or 100)))
                stmt = select(Ledger).where(Ledger.user_id == uid)

                raw_ids = params.get("ledger_ids")
                if isinstance(raw_ids, list):
                    picked_ids: list[int] = []
                    for item in raw_ids:
                        try:
                            num = int(item)
                        except Exception:
                            continue
                        if num > 0 and num not in picked_ids:
                            picked_ids.append(num)
                    if picked_ids:
                        stmt = stmt.where(Ledger.id.in_(picked_ids))

                start_at = _parse_datetime_arg(params.get("start_at"))
                end_at = _parse_datetime_arg(params.get("end_at"))
                if start_at is not None:
                    stmt = stmt.where(Ledger.transaction_date >= start_at)
                if end_at is not None:
                    stmt = stmt.where(Ledger.transaction_date < end_at)

                category = str(params.get("category") or "").strip()
                if category:
                    stmt = stmt.where(Ledger.category == category)

                item_like = str(params.get("item_like") or "").strip()
                if item_like:
                    stmt = stmt.where(Ledger.item.ilike(f"%{item_like}%"))

                order = str(params.get("order") or "desc").strip().lower()
                if order == "asc":
                    stmt = stmt.order_by(Ledger.transaction_date.asc(), Ledger.id.asc())
                else:
                    stmt = stmt.order_by(Ledger.transaction_date.desc(), Ledger.id.desc())
                stmt = stmt.limit(limit)

                session = get_session()
                result = await session.execute(stmt)
                rows = list(result.scalars().all())
                payload = [_ledger_to_payload(item) for item in rows]
                return _result(
                    True,
                    output=json.dumps(payload, ensure_ascii=False),
                    output_data=payload,
                )

            if tool_l == "conversation_current":
                uid = _resolve_user_id(params.get("user_id", user_id))
                if uid <= 0:
                    return _result(False, error="missing required arg: user_id")
                session = get_session()
                user_row = await session.get(User, uid)
                if user_row is None:
                    return _result(False, error="user not found")
                current = await ensure_active_conversation(session, user_row)
                payload = _conversation_to_payload(current, int(current.id or 0))
                return _result(
                    True,
                    output=json.dumps(payload, ensure_ascii=False),
                    output_data=payload,
                )

            if tool_l == "conversation_list":
                uid = _resolve_user_id(params.get("user_id", user_id))
                if uid <= 0:
                    return _result(False, error="missing required arg: user_id")
                limit = max(1, min(100, int(params.get("limit") or 20)))
                session = get_session()
                user_row = await session.get(User, uid)
                if user_row is None:
                    return _result(False, error="user not found")
                await ensure_active_conversation(session, user_row)
                rows = await list_conversations(session, user_row, limit=limit)
                active_id = int(user_row.active_conversation_id or 0) or None
                payload = [_conversation_to_payload(item, active_id) for item in rows]
                return _result(
                    True,
                    output=json.dumps(payload, ensure_ascii=False),
                    output_data=payload,
                )

            if tool_l == "memory_list":
                uid = _resolve_user_id(params.get("user_id", user_id))
                if uid <= 0:
                    return _result(False, error="missing required arg: user_id")
                limit = max(1, min(500, int(params.get("limit") or 120)))
                session = get_session()
                payload = await list_long_term_memories(
                    session=session,
                    user_id=uid,
                    limit=limit,
                )
                return _result(
                    True,
                    output=json.dumps(payload or [], ensure_ascii=False),
                    output_data=payload or [],
                )

            if tool_l == "schedule_insert":
                uid = _resolve_user_id(params.get("user_id", user_id))
                if uid <= 0:
                    return _result(False, error="missing required arg: user_id")
                content = str(params.get("content") or "").strip()
                if not content:
                    return _result(False, error="missing required arg: content")
                trigger_time = _parse_datetime_arg(params.get("trigger_time"))
                if trigger_time is None:
                    return _result(False, error="missing or invalid arg: trigger_time")
                status = str(params.get("status") or "PENDING").strip().upper() or "PENDING"
                job_id = str(params.get("job_id") or "").strip() or str(uuid4())
                session = get_session()
                scheduler = get_scheduler()
                row = Schedule(
                    user_id=uid,
                    job_id=job_id,
                    content=content,
                    trigger_time=trigger_time,
                    status=status,
                )
                session.add(row)
                await session.flush()
                if status == "PENDING":
                    scheduler.add_job(job_id, trigger_time, send_reminder_job, int(row.id or 0))
                await session.commit()
                await session.refresh(row)
                payload = _schedule_to_payload(row)
                return _result(
                    True,
                    output=json.dumps(payload, ensure_ascii=False),
                    output_data=payload,
                )

            if tool_l == "schedule_update":
                uid = _resolve_user_id(params.get("user_id", user_id))
                if uid <= 0:
                    return _result(False, error="missing required arg: user_id")
                schedule_id = _resolve_user_id(params.get("schedule_id"))
                if schedule_id <= 0:
                    return _result(False, error="missing required arg: schedule_id")
                session = get_session()
                scheduler = get_scheduler()
                row = await session.get(Schedule, schedule_id)
                if row is None or int(row.user_id or 0) != uid:
                    return _result(False, error="schedule not found")
                content_value = str(params.get("content") or "").strip()
                if content_value:
                    row.content = content_value
                trigger_time = _parse_datetime_arg(params.get("trigger_time"))
                if trigger_time is not None:
                    row.trigger_time = trigger_time
                status_value = str(params.get("status") or "").strip().upper()
                if status_value:
                    row.status = status_value
                try:
                    scheduler.remove_job(str(row.job_id))
                except Exception:
                    pass
                if str(row.status or "").upper() == "PENDING":
                    scheduler.add_job(str(row.job_id), row.trigger_time, send_reminder_job, int(row.id or 0))
                session.add(row)
                await session.commit()
                await session.refresh(row)
                payload = _schedule_to_payload(row)
                return _result(
                    True,
                    output=json.dumps(payload, ensure_ascii=False),
                    output_data=payload,
                )

            if tool_l == "schedule_delete":
                uid = _resolve_user_id(params.get("user_id", user_id))
                if uid <= 0:
                    return _result(False, error="missing required arg: user_id")
                schedule_id = _resolve_user_id(params.get("schedule_id"))
                if schedule_id <= 0:
                    return _result(False, error="missing required arg: schedule_id")
                session = get_session()
                scheduler = get_scheduler()
                row = await session.get(Schedule, schedule_id)
                if row is None or int(row.user_id or 0) != uid:
                    return _result(False, error="schedule not found")
                payload = _schedule_to_payload(row)
                try:
                    scheduler.remove_job(str(row.job_id))
                except Exception:
                    pass
                await session.execute(delete(ReminderDelivery).where(ReminderDelivery.schedule_id == schedule_id))
                await session.delete(row)
                await session.commit()
                return _result(
                    True,
                    output=json.dumps(payload, ensure_ascii=False),
                    output_data=payload,
                )

            if tool_l == "schedule_list":
                uid = _resolve_user_id(params.get("user_id", user_id))
                if uid <= 0:
                    return _result(False, error="missing required arg: user_id")
                limit = max(1, min(500, int(params.get("limit") or 100)))
                stmt = select(Schedule).where(Schedule.user_id == uid)

                raw_ids = params.get("schedule_ids")
                if isinstance(raw_ids, list):
                    picked_ids: list[int] = []
                    for item in raw_ids:
                        try:
                            num = int(item)
                        except Exception:
                            continue
                        if num > 0 and num not in picked_ids:
                            picked_ids.append(num)
                    if picked_ids:
                        stmt = stmt.where(Schedule.id.in_(picked_ids))

                start_at = _parse_datetime_arg(params.get("start_at"))
                end_at = _parse_datetime_arg(params.get("end_at"))
                if start_at is not None:
                    stmt = stmt.where(Schedule.trigger_time >= start_at)
                if end_at is not None:
                    stmt = stmt.where(Schedule.trigger_time < end_at)

                content_like = str(params.get("content_like") or "").strip()
                if content_like:
                    stmt = stmt.where(Schedule.content.ilike(f"%{content_like}%"))

                status = str(params.get("status") or "").strip().upper()
                if status and status != "ALL":
                    stmt = stmt.where(Schedule.status == status)
                order = str(params.get("order") or "asc").strip().lower()
                if order == "desc":
                    stmt = stmt.order_by(Schedule.trigger_time.desc(), Schedule.id.desc())
                else:
                    stmt = stmt.order_by(Schedule.trigger_time.asc(), Schedule.id.asc())
                stmt = stmt.limit(limit)
                session = get_session()
                result = await session.execute(stmt)
                rows = list(result.scalars().all())
                payload = [_schedule_to_payload(item) for item in rows]
                return _result(
                    True,
                    output=json.dumps(payload, ensure_ascii=False),
                    output_data=payload,
                )

            return _result(False, error=f"unsupported builtin tool: {tool_l}")

        # src == "mcp"
        target_name = tool.strip()
        target_norm = target_name.lower()
        if not settings.mcp_fetch_enabled:
            return _result(False, error="MCP fetch is disabled.")
        if not is_mcp_tool_allowed(target_norm):
            allowed = sorted(get_allowed_mcp_tool_names_for(target_norm))
            allowed_text = ", ".join(allowed) if allowed else "none"
            return _result(False, error=f"MCP tool `{target_name}` is blocked by allowlist. Allowed tools: {allowed_text}.")
        if not await is_tool_enabled("mcp", target_norm):
            return _result(False, error=f"MCP tool `{target_name}` is disabled by admin.")
        output = await get_mcp_client_for_tool(target_name).call_tool(name=target_name, arguments=params)
        return _result(True, output=output)

    except Exception as exc:
        return _result(False, error=str(exc))


async def execute_capability_with_usage(
    *,
    source: str,
    name: str,
    args: dict[str, Any] | None = None,
    user_id: int | None = None,
    platform: str = "",
    conversation_id: int | None = None,
) -> ToolExecResult:
    result = await execute_capability(
        source=source,
        name=name,
        args=args,
        user_id=user_id,
        platform=platform,
        conversation_id=conversation_id,
    )
    await _log_tool_usage_safe(
        user_id=user_id,
        platform=platform,
        conversation_id=conversation_id,
        tool_source=str(result["source"] or source),
        tool_name=str(result["name"] or name),
        success=bool(result["ok"]),
        latency_ms=int(result["latency_ms"] or 0),
        error=str(result.get("error") or ""),
    )
    return result
