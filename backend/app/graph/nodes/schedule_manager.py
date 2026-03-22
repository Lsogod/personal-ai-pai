import json
import asyncio
import re
from datetime import date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from langchain.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.graph.context import render_conversation_context
from app.graph.prompts.schedule_manager_prompts import (
    build_schedule_intent_messages,
)
from app.graph.state import GraphState
from app.models.ledger import Ledger
from app.models.schedule import Schedule
from app.services.langchain_tools import ToolInvocationContext
from app.services.llm import get_llm
from app.services.toolsets import invoke_node_tool_typed
from app.services.runtime_context import get_session
from app.models.user import User


NOW_TIME_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}")
TECHNICAL_LEAK_PATTERN = re.compile(r"(json|payload|字段|数组|schema|schedules|ledgers)", re.IGNORECASE)
SCHEDULE_DECORATION_PATTERN = re.compile(r"\s*[（(][^）)]*提前[^）)]*[）)]\s*$")
SCHEDULE_STATUS_MAP = {
    "all": "all",
    "全部": "all",
    "所有": "all",
    "pending": "pending",
    "todo": "pending",
    "未完成": "pending",
    "待办": "pending",
    "待执行": "pending",
    "未执行": "pending",
    "executed": "executed",
    "completed": "executed",
    "done": "executed",
    "已完成": "executed",
    "完成": "executed",
    "已执行": "executed",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "已取消": "cancelled",
    "取消": "cancelled",
    "failed": "failed",
    "失败": "failed",
    "未送达": "failed",
}
SCHEDULE_STATUS_DB = {
    "all": None,
    "pending": "PENDING",
    "executed": "EXECUTED",
    "cancelled": "CANCELLED",
    "failed": "FAILED",
}
PRIORITY_SET = {"low", "medium", "high", "critical"}
EVENT_TYPE_SET = {
    "meeting",
    "travel",
    "deadline",
    "appointment",
    "payment",
    "study",
    "work",
    "family",
    "task",
    "other",
}


class ScheduleIntentExtraction(BaseModel):
    intent: Literal[
        "reminder",
        "update_by_name",
        "update_by_scope",
        "delete_by_name",
        "delete_by_scope",
        "calendar",
        "time_query",
        "context_recall",
        "unknown",
    ] = Field(default="unknown")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    confirmation_action: Literal["confirm", "cancel", "none"] = Field(default="none")
    needs_clarification: bool = Field(default=False)
    clarify_question: str = Field(default="")
    run_at_local: str = Field(default="")
    time_precision: Literal["second", "minute", "none"] = Field(default="none")
    reminder_content: str = Field(default="")
    offsets_minutes: list[int] = Field(default_factory=list)
    condition_type: Literal["weather_good", "weather_rain", "none"] = Field(default="none")
    condition_city: str = Field(default="")
    condition_date: str = Field(default="")
    target_content: str = Field(default="")
    target_ids: list[int] = Field(default_factory=list)
    reference_mode: Literal["by_id", "by_name", "by_scope", "latest", "last_result_set", "auto"] = Field(
        default="auto"
    )
    selection_mode: Literal["all", "single", "subset", "auto"] = Field(default="auto")
    event_type: str = Field(default="")
    priority: str = Field(default="")
    calendar_scope: str = Field(default="")
    calendar_date: str = Field(default="")
    schedule_status_filter: Literal["all", "pending", "executed", "cancelled", "failed"] = Field(default="all")


class ScheduleTargetSelection(BaseModel):
    schedule_ids: list[int] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


MAX_OFFSET_MINUTES = 60 * 24 * 30


def _schedule_status_label(value: str | None) -> str:
    key = str(value or "").upper()
    return {
        "PENDING": "未完成",
        "EXECUTED": "已完成",
        "CANCELLED": "已取消",
        "FAILED": "失败",
    }.get(key, key or "未知")


def _local_today() -> date:
    tz_name = get_settings().timezone
    return datetime.now(ZoneInfo(tz_name)).date()


def _to_utc_naive(local_dt: datetime) -> datetime:
    tz_name = get_settings().timezone
    return local_dt.replace(tzinfo=ZoneInfo(tz_name)).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def _format_window_label(name: str, start_date: date, end_date_exclusive: date) -> str:
    if end_date_exclusive == start_date + timedelta(days=1):
        return f"{name}（{start_date.isoformat()}）"
    end_date = end_date_exclusive - timedelta(days=1)
    return f"{name}（{start_date.isoformat()} ~ {end_date.isoformat()}）"


def _parse_json_object(content: Any) -> dict:
    if isinstance(content, dict):
        return content
    if content is None:
        return {}
    if isinstance(content, list):
        return {}
    text = str(content).strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _parse_json_list(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, list):
        return [item for item in content if isinstance(item, dict)]
    if content is None:
        return []
    if isinstance(content, dict):
        return []
    text = str(content).strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()
    try:
        data = json.loads(text)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _coerce_iso_date(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return date.fromisoformat(raw).isoformat()
    except Exception:
        return ""


def _normalize_condition_type(value: str | None) -> str:
    key = (value or "").strip().lower()
    if key in {"weather_good", "weather_rain"}:
        return key
    return "none"


def _pick_clarify_question(parsed: dict[str, Any], default: str) -> str:
    need_clarify = bool(parsed.get("needs_clarification"))
    if not need_clarify:
        return default
    question = str(parsed.get("clarify_question") or "").strip()
    return question or default


def _normalize_confirmation_action(value: str | None) -> str:
    key = (value or "").strip().lower()
    if key in {"confirm", "cancel"}:
        return key
    return "none"


def _read_pending_reminder_plan(state: GraphState) -> dict[str, Any] | None:
    extra = dict(state.get("extra") or {})
    payload = extra.get("pending_reminder_plan")
    if isinstance(payload, dict):
        return payload
    return None


def _with_pending_reminder_plan(state: GraphState, plan: dict[str, Any]) -> GraphState:
    extra = dict(state.get("extra") or {})
    extra["pending_reminder_plan"] = dict(plan)
    return {**state, "extra": extra}


def _clear_pending_reminder_plan(state: GraphState) -> GraphState:
    extra = dict(state.get("extra") or {})
    if "pending_reminder_plan" in extra:
        extra.pop("pending_reminder_plan", None)
    return {**state, "extra": extra}


def _compose_reminder_plan(parsed: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_at_local": str(parsed.get("run_at_local") or "").strip(),
        "time_precision": _normalize_time_precision(str(parsed.get("time_precision") or "")),
        "reminder_content": _normalize_reminder_content(str(parsed.get("reminder_content") or "")),
        "offsets_minutes": _read_offsets_from_plan(parsed),
        "event_type": _normalize_event_type(str(parsed.get("event_type") or "")),
        "priority": _normalize_priority(str(parsed.get("priority") or "medium")),
        "condition_type": _normalize_condition_type(str(parsed.get("condition_type") or "none")),
        "condition_city": str(parsed.get("condition_city") or "").strip(),
        "condition_date": _coerce_iso_date(str(parsed.get("condition_date") or "")),
    }


def _has_new_reminder_payload(parsed: dict[str, Any]) -> bool:
    if str(parsed.get("run_at_local") or "").strip():
        return True
    if _normalize_reminder_content(str(parsed.get("reminder_content") or "")):
        return True
    if _read_offsets_from_plan(parsed):
        return True
    condition_type = _normalize_condition_type(str(parsed.get("condition_type") or ""))
    if condition_type != "none":
        return True
    if str(parsed.get("condition_city") or "").strip():
        return True
    if _coerce_iso_date(str(parsed.get("condition_date") or "")):
        return True
    return False


def _schedule_from_payload(payload: dict[str, Any]) -> Schedule | None:
    if not isinstance(payload, dict):
        return None
    try:
        schedule_id = int(payload.get("id") or 0)
        user_id = int(payload.get("user_id") or 0)
    except Exception:
        return None
    if schedule_id <= 0 or user_id <= 0:
        return None
    trigger_raw = str(payload.get("trigger_time") or "").strip().replace("T", " ")
    trigger_time = None
    if trigger_raw:
        try:
            trigger_time = datetime.fromisoformat(trigger_raw)
        except Exception:
            trigger_time = None
    if trigger_time is None:
        trigger_time = datetime.utcnow()
    return Schedule(
        id=schedule_id,
        user_id=user_id,
        job_id=str(payload.get("job_id") or ""),
        content=str(payload.get("content") or ""),
        trigger_time=trigger_time,
        status=str(payload.get("status") or "PENDING"),
    )


def _ledger_from_payload(payload: dict[str, Any]) -> Ledger | None:
    if not isinstance(payload, dict):
        return None
    try:
        ledger_id = int(payload.get("id") or 0)
        user_id = int(payload.get("user_id") or 0)
        amount = float(payload.get("amount") or 0.0)
    except Exception:
        return None
    if ledger_id <= 0 or user_id <= 0:
        return None
    transaction_raw = str(payload.get("transaction_date") or "").strip().replace("T", " ")
    transaction_date = None
    if transaction_raw:
        try:
            transaction_date = datetime.fromisoformat(transaction_raw)
        except Exception:
            transaction_date = None
    if transaction_date is None:
        transaction_date = datetime.utcnow()
    return Ledger(
        id=ledger_id,
        user_id=user_id,
        amount=amount,
        currency=str(payload.get("currency") or "CNY"),
        category=str(payload.get("category") or ""),
        item=str(payload.get("item") or ""),
        image_url=str(payload.get("image_url") or "") or None,
        transaction_date=transaction_date,
    )


async def _invoke_schedule_tool(
    *,
    user_id: int,
    platform: str,
    conversation_id: int | None,
    tool_name: str,
    args: dict[str, Any] | None = None,
) -> Any:
    return await invoke_node_tool_typed(
        context=ToolInvocationContext(
            user_id=user_id,
            platform=platform,
            conversation_id=conversation_id,
        ),
        node_name="schedule_manager",
        tool_name=tool_name,
        args=dict(args or {}),
    )


def _parse_now_local_from_tool_output(output: Any) -> datetime | None:
    text = str(output or "").strip()
    match = NOW_TIME_PATTERN.search(text)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(0), "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


async def _read_now_local_from_tool(
    *,
    user_id: int,
    platform: str,
    conversation_id: int | None,
) -> datetime:
    settings = get_settings()
    tz = settings.timezone
    try:
        output = await _invoke_schedule_tool(
            user_id=user_id,
            platform=platform,
            conversation_id=conversation_id,
            tool_name="now_time",
            args={"timezone": tz},
        )
        parsed = _parse_now_local_from_tool_output(output)
        if parsed is not None:
            return parsed
    except Exception:
        pass
    return datetime.now(ZoneInfo(tz)).replace(tzinfo=None)


def _sanitize_llm_text(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    lines: list[str] = []
    prev = ""
    for row in raw.splitlines():
        line = row.rstrip()
        norm = re.sub(r"\s+", " ", line).strip()
        if not norm:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        if norm == prev:
            continue
        if TECHNICAL_LEAK_PATTERN.search(norm):
            continue
        lines.append(line)
        prev = norm
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines).strip()


def _parse_run_at_local(value: str | None, timezone: str) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    normalized = raw.replace("T", " ").replace("/", "-")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    patterns = (
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
    )
    for pattern in patterns:
        try:
            dt = datetime.strptime(normalized, pattern)
            local_tz = ZoneInfo(timezone)
            # Keep naive local datetime to match scheduler DateTrigger timezone handling.
            _ = dt.replace(tzinfo=local_tz)
            return dt
        except Exception:
            continue
    try:
        dt = datetime.fromisoformat(normalized)
        local_tz = ZoneInfo(timezone)
        if dt.tzinfo is not None:
            return dt.astimezone(local_tz).replace(tzinfo=None)
        _ = dt.replace(tzinfo=local_tz)
        return dt
    except Exception:
        return None


def _normalize_time_precision(value: str | None) -> str:
    key = (value or "").strip().lower()
    if key in {"second", "seconds", "sec"}:
        return "second"
    if key in {"minute", "minutes", "min"}:
        return "minute"
    return "none"


def _resolve_time_precision(parsed: dict[str, Any], trigger_time: datetime | None) -> str:
    precision = _normalize_time_precision(str(parsed.get("time_precision") or ""))
    if precision != "none":
        return precision
    if trigger_time is None:
        return "none"
    return "second" if int(trigger_time.second or 0) != 0 else "minute"


def _format_reminder_time(dt: datetime) -> str:
    if dt.second != 0:
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    return dt.strftime("%Y-%m-%d %H:%M")


def _normalize_reminder_content(value: str | None) -> str:
    return str(value or "").strip().strip("`\"' ")


def _schedule_content_root(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    return SCHEDULE_DECORATION_PATTERN.sub("", raw).strip()


def _normalize_event_type(value: str | None) -> str:
    key = (value or "").strip().lower()
    if key in EVENT_TYPE_SET:
        return key
    return "other"


def _normalize_priority(value: str | None) -> str:
    key = (value or "").strip().lower()
    return key if key in PRIORITY_SET else "medium"


def _parse_offset_values(value: Any) -> list[int]:
    values: list[int] = []
    if value is None:
        return values
    if isinstance(value, str):
        parts = [item for item in re.split(r"[,\s，、]+", value.strip()) if item]
        iterable = parts
    elif isinstance(value, list):
        iterable = value
    else:
        iterable = [value]
    for item in iterable:
        try:
            minutes = int(float(str(item).strip()))
        except Exception:
            continue
        if minutes < 0 or minutes > MAX_OFFSET_MINUTES:
            continue
        values.append(minutes)
    return values


def _dedupe_desc(values: list[int]) -> list[int]:
    return sorted({int(item) for item in values if int(item) >= 0}, reverse=True)


def _read_offsets_from_plan(parsed: dict[str, Any]) -> list[int]:
    return _dedupe_desc(_parse_offset_values(parsed.get("offsets_minutes")))


def _offset_label(minutes: int) -> str:
    if minutes <= 0:
        return "准点"
    if minutes % (24 * 60) == 0:
        days = minutes // (24 * 60)
        return f"提前{days}天"
    if minutes % 60 == 0:
        hours = minutes // 60
        return f"提前{hours}小时"
    return f"提前{minutes}分钟"


def _decorate_reminder_content(content: str, offset_minutes: int) -> str:
    if offset_minutes <= 0:
        return content
    return f"{content}（{_offset_label(offset_minutes)}）"


def _format_offsets_for_reply(offsets: list[int], event_time: datetime) -> str:
    if not offsets:
        return _format_reminder_time(event_time)
    labels: list[str] = []
    for offset in _dedupe_desc(offsets):
        if offset > 0:
            labels.append(_offset_label(offset))
        else:
            labels.append(f"准点（{_format_reminder_time(event_time)}）")
    return "、".join(labels)


async def _create_reminder(
    *,
    session,
    user: User,
    platform: str,
    conversation_id: int | None,
    reminder_content: str,
    event_time: datetime,
    event_type: str,
    priority: str,
    offsets_minutes: list[int],
    now_local: datetime,
) -> str:
    created_offsets: list[int] = []

    for offset in _dedupe_desc(offsets_minutes):
        trigger_time = event_time - timedelta(minutes=offset)
        if trigger_time <= now_local + timedelta(seconds=1):
            continue
        output = await _invoke_schedule_tool(
            user_id=user.id,
            platform=platform,
            conversation_id=conversation_id,
            tool_name="schedule_insert",
            args={
                "user_id": user.id,
                "content": _decorate_reminder_content(reminder_content, offset),
                "trigger_time": trigger_time.isoformat(sep=" ", timespec="seconds"),
                "status": "PENDING",
            },
        )
        created = _schedule_from_payload(_parse_json_object(output))
        if created is not None:
            created_offsets.append(offset)

    if not created_offsets:
        return ""

    try:
        from app.services.audit import log_event

        await log_event(
            session,
            action="schedule_created",
            platform=user.platform,
            user_id=user.id,
            detail={
                "content": reminder_content,
                "event_time": event_time.isoformat(),
                "event_type": event_type,
                "priority": priority,
                "offsets_minutes": _dedupe_desc(created_offsets),
                "created_count": len(created_offsets),
                "via": "llm",
            },
        )
    except Exception:
        pass

    normalized_offsets = _dedupe_desc(created_offsets)
    if normalized_offsets == [0]:
        return f"好的，提醒已设置：{_format_reminder_time(event_time)}。"
    return f"好的，已设置{len(normalized_offsets)}次提醒：{_format_offsets_for_reply(normalized_offsets, event_time)}。"


async def _execute_reminder_plan(
    *,
    state: GraphState,
    session,
    user: User,
    platform: str,
    conversation_id: int | None,
    plan: dict[str, Any],
    now_local: datetime,
) -> GraphState:
    settings = get_settings()
    trigger_time = _parse_run_at_local(str(plan.get("run_at_local") or ""), settings.timezone)
    if trigger_time is None:
        return {
            **state,
            "responses": ["请补充明确时间，例如：明天早上8点。"],
        }

    time_precision = _normalize_time_precision(str(plan.get("time_precision") or ""))
    if time_precision == "second":
        trigger_time = trigger_time.replace(microsecond=0)
    else:
        trigger_time = trigger_time.replace(second=0, microsecond=0)

    if trigger_time <= now_local + timedelta(seconds=1):
        return {
            **state,
            "responses": ["提醒时间已过近，无法安排提前提醒。请给我一个稍晚一点的时间。"],
        }

    reminder_content = _normalize_reminder_content(str(plan.get("reminder_content") or ""))
    if not reminder_content:
        return {
            **state,
            "responses": ["请告诉我要提醒的具体事项。"],
        }

    event_type = _normalize_event_type(str(plan.get("event_type") or ""))
    priority = _normalize_priority(str(plan.get("priority") or "medium"))

    offsets_minutes = _dedupe_desc(_parse_offset_values(plan.get("offsets_minutes")))
    if not offsets_minutes:
        return {
            **state,
            "responses": ["提醒计划缺少提醒次数，请再说一次，我会先给出建议并请你确认。"],
        }

    text = await _create_reminder(
        session=session,
        user=user,
        platform=platform,
        conversation_id=conversation_id,
        reminder_content=reminder_content,
        event_time=trigger_time,
        event_type=event_type,
        priority=priority,
        offsets_minutes=offsets_minutes,
        now_local=now_local,
    )
    if text:
        return {**state, "responses": [text]}
    return {
        **state,
        "responses": ["提醒时间已过近，无法安排提前提醒。请给我一个稍晚一点的时间。"],
    }


async def _understand_schedule_message(content: str, conversation_context: str, now_local: str) -> dict:
    settings = get_settings()
    tz = settings.timezone
    llm = get_llm(node_name="schedule_manager")
    runnable = llm.with_structured_output(ScheduleIntentExtraction)
    messages = build_schedule_intent_messages(
        content=content,
        conversation_context=conversation_context,
        timezone=tz,
        now_local=now_local,
    )
    try:
        parsed = await runnable.ainvoke(messages)
    except Exception:
        return ScheduleIntentExtraction().model_dump()
    if isinstance(parsed, ScheduleIntentExtraction):
        return parsed.model_dump()
    if isinstance(parsed, dict):
        try:
            return ScheduleIntentExtraction.model_validate(parsed).model_dump()
        except Exception:
            return ScheduleIntentExtraction().model_dump()
    return ScheduleIntentExtraction().model_dump()


async def _answer_context_recall(content: str, conversation_context: str) -> str:
    llm = get_llm(node_name="schedule_manager")
    system = SystemMessage(
        content=(
            "你是会话回忆助手。请仅依据给定会话上下文回答用户问题。"
            "若上下文不足，明确说明缺失点，不要假装没有记忆能力。"
            "回答简洁。"
        )
    )
    human = HumanMessage(
        content=(
            f"会话上下文:\n{conversation_context}\n\n"
            f"用户问题:\n{content}"
        )
    )
    response = await llm.ainvoke([system, human])
    text = str(response.content or "").strip()
    return text or "我这边没有提取到足够的历史内容，请你再说一次具体要回忆的点。"


def _resolve_calendar_window_from_fields(
    scope: str,
    calendar_date: str | None,
) -> tuple[datetime, datetime, str] | None:
    today = _local_today()
    scope_key = (scope or "").strip().lower()
    if scope_key == "yesterday":
        start = today - timedelta(days=1)
        end = start + timedelta(days=1)
        return datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time()), _format_window_label("昨天", start, end)
    if scope_key == "day_before_yesterday":
        start = today - timedelta(days=2)
        end = start + timedelta(days=1)
        return datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time()), _format_window_label("前天", start, end)
    if scope_key in {"today", "day"}:
        start = today
        end = today + timedelta(days=1)
        return datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time()), _format_window_label("今天", start, end)
    if scope_key in {"tomorrow"}:
        start = today + timedelta(days=1)
        end = start + timedelta(days=1)
        return datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time()), _format_window_label("明天", start, end)
    if scope_key == "last_week":
        start = today - timedelta(days=today.weekday() + 7)
        end = start + timedelta(days=7)
        return datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time()), _format_window_label("上周", start, end)
    if scope_key == "week":
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=7)
        return datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time()), _format_window_label("本周", start, end)
    if scope_key == "month":
        start = today.replace(day=1)
        if start.month == 12:
            end = date(start.year + 1, 1, 1)
        else:
            end = date(start.year, start.month + 1, 1)
        return datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time()), _format_window_label("本月", start, end)
    if scope_key == "date" and calendar_date:
        try:
            start = date.fromisoformat(calendar_date.strip())
            end = start + timedelta(days=1)
            return datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time()), _format_window_label(start.isoformat(), start, end)
        except Exception:
            return None
    return None


def _normalize_schedule_status_filter(value: str | None) -> str:
    key = (value or "").strip().lower()
    return SCHEDULE_STATUS_MAP.get(key, "all")


def _resolve_schedule_status_filter(parsed: dict) -> str:
    return _normalize_schedule_status_filter(str(parsed.get("schedule_status_filter") or "all"))


def _fmt_dt(value: datetime | None, pattern: str = "%m-%d %H:%M", assume_utc: bool = False) -> str:
    if not value:
        return "-"
    tz = ZoneInfo(get_settings().timezone)
    if value.tzinfo is not None:
        value = value.astimezone(tz)
    elif assume_utc:
        value = value.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
    return value.strftime(pattern)


async def _query_calendar_rows(
    *,
    user_id: int,
    platform: str,
    conversation_id: int | None,
    start_at: datetime,
    end_at: datetime,
) -> tuple[list[Ledger], list[Schedule]]:
    ledger_start = _to_utc_naive(start_at)
    ledger_end = _to_utc_naive(end_at)
    ledger_output = await _invoke_schedule_tool(
        user_id=user_id,
        platform=platform,
        conversation_id=conversation_id,
        tool_name="ledger_list",
        args={
            "user_id": user_id,
            "start_at": ledger_start.isoformat(sep=" ", timespec="seconds"),
            "end_at": ledger_end.isoformat(sep=" ", timespec="seconds"),
            "limit": 500,
            "order": "asc",
        },
    )
    schedule_output = await _invoke_schedule_tool(
        user_id=user_id,
        platform=platform,
        conversation_id=conversation_id,
        tool_name="schedule_list",
        args={
            "user_id": user_id,
            "start_at": start_at.isoformat(sep=" ", timespec="seconds"),
            "end_at": end_at.isoformat(sep=" ", timespec="seconds"),
            "limit": 500,
            "order": "asc",
        },
    )
    ledgers = [
        row
        for row in (
            _ledger_from_payload(item)
            for item in _parse_json_list(ledger_output)
        )
        if row is not None
    ]
    schedules = [
        row
        for row in (
            _schedule_from_payload(item)
            for item in _parse_json_list(schedule_output)
        )
        if row is not None
    ]
    return ledgers, schedules


def _filter_schedules_by_status(schedules: list[Schedule], status_filter: str) -> list[Schedule]:
    target = SCHEDULE_STATUS_DB.get(status_filter, None)
    if not target:
        return schedules
    return [item for item in schedules if str(item.status or "").upper() == target]


def _build_calendar_payload(ledgers: list[Ledger], schedules: list[Schedule]) -> dict[str, Any]:
    return {
        "ledgers": [
            {
                "id": item.id,
                "datetime": _fmt_dt(item.transaction_date, "%Y-%m-%d %H:%M", assume_utc=True),
                "amount": round(float(item.amount), 2),
                "currency": item.currency,
                "category": item.category,
                "item": item.item,
            }
            for item in ledgers[:50]
        ],
        "schedules": [
            {
                "id": item.id,
                "datetime": _fmt_dt(item.trigger_time, "%Y-%m-%d %H:%M"),
                "content": item.content,
                "status": str(item.status or "").upper(),
            }
            for item in schedules[:50]
        ],
    }


async def _answer_calendar_with_llm(
    content: str,
    conversation_context: str,
    label: str,
    schedule_status_filter: str,
    ledgers: list[Ledger],
    schedules: list[Schedule],
) -> str:
    llm = get_llm(node_name="schedule_manager")
    payload = _build_calendar_payload(ledgers, schedules)
    system = SystemMessage(
        content=(
            "你是日历与日程答复助手。你只能基于给定数据回答，不得编造。"
            "回答时要贴合用户问题，不要固定模板。"
            "如果用户问已完成/未完成/已取消，请按 status 进行筛选或分组。"
            "status 定义: PENDING=未完成, EXECUTED=已完成, CANCELLED=已取消。"
            "FAILED=推送失败。"
            "涉及明细时请给出时间。"
            "若数据为空，明确说没有相关记录。"
            "默认给出简洁自然的中文回答，不要输出技术说明。"
            "不要提到 JSON、数组、字段、数据源、会话上下文。"
            "除非用户明确要求按状态统计，否则不要枚举各状态计数。"
            "不要重复句子或段落。"
        )
    )
    human = HumanMessage(
        content=(
            f"查询范围: {label}\n"
            f"状态筛选: {schedule_status_filter}\n\n"
            f"会话上下文:\n{conversation_context}\n\n"
            f"用户问题:\n{content}\n\n"
            f"数据(JSON):\n{json.dumps(payload, ensure_ascii=False)}"
        )
    )
    response = await llm.ainvoke([system, human])
    return _sanitize_llm_text(str(response.content or ""))


def _render_calendar_text(ledgers: list[Ledger], schedules: list[Schedule], label: str, schedule_status_filter: str) -> str:
    filtered_schedules = _filter_schedules_by_status(schedules, schedule_status_filter)
    filter_title = {
        "all": "全部状态",
        "pending": "未完成",
        "executed": "已完成",
        "cancelled": "已取消",
        "failed": "失败",
    }.get(schedule_status_filter, "全部状态")
    lines = [f"{label}日历："]
    lines.append(f"- 账单 {len(ledgers)} 笔")
    lines.append(f"- 日程 {len(filtered_schedules)} 条（{filter_title}）")

    if ledgers:
        lines.append("账单：")
        for row in ledgers[:8]:
            lines.append(
                f"- {_fmt_dt(row.transaction_date, assume_utc=True)} #{row.id} {row.item} {row.amount:.2f} {row.currency} ({row.category})"
            )
        if len(ledgers) > 8:
            lines.append(f"- ... 其余 {len(ledgers) - 8} 笔")

    if filtered_schedules:
        lines.append("日程：")
        for row in filtered_schedules[:8]:
            lines.append(
                f"- {_fmt_dt(row.trigger_time)} #{row.id} {row.content} [{_schedule_status_label(row.status)}]"
            )
        if len(filtered_schedules) > 8:
            lines.append(f"- ... 其余 {len(filtered_schedules) - 8} 条")

    if not ledgers and not filtered_schedules:
        lines.append("这段时间没有账单和日程。")

    lines.append("你可以直接说：今天日程、这周日程、本月日程，或查询某天（例如 2026-02-07）。")
    return "\n".join(lines)


async def _answer_calendar(
    user_id: int,
    platform: str,
    conversation_id: int | None,
    content: str,
    conversation_context: str,
    start_at: datetime,
    end_at: datetime,
    label: str,
    schedule_status_filter: str,
) -> str:
    ledgers, schedules = await _query_calendar_rows(
        user_id=user_id,
        platform=platform,
        conversation_id=conversation_id,
        start_at=start_at,
        end_at=end_at,
    )
    filtered_schedules = _filter_schedules_by_status(schedules, schedule_status_filter)
    try:
        llm_text = await _answer_calendar_with_llm(
            content=content,
            conversation_context=conversation_context,
            label=label,
            schedule_status_filter=schedule_status_filter,
            ledgers=ledgers,
            schedules=filtered_schedules,
        )
        if llm_text:
            return llm_text
    except Exception:
        pass
    return _render_calendar_text(ledgers, schedules, label, schedule_status_filter)


async def _query_editable_schedules(
    user_id: int,
    platform: str,
    conversation_id: int | None,
    limit: int = 120,
    target_hint: str = "",
) -> list[Schedule]:
    hint = (target_hint or "").strip()
    if hint:
        output = await _invoke_schedule_tool(
            user_id=user_id,
            platform=platform,
            conversation_id=conversation_id,
            tool_name="schedule_list",
            args={
                "user_id": user_id,
                "status": "PENDING",
                "content_like": hint,
                "limit": min(limit, 40),
                "order": "asc",
            },
        )
        hinted_rows = [
            row
            for row in (
                _schedule_from_payload(item)
                for item in _parse_json_list(output)
            )
            if row is not None
        ]
        if hinted_rows:
            return hinted_rows

    output = await _invoke_schedule_tool(
        user_id=user_id,
        platform=platform,
        conversation_id=conversation_id,
        tool_name="schedule_list",
        args={
            "user_id": user_id,
            "status": "PENDING",
            "limit": limit,
            "order": "desc",
        },
    )
    return [
        row
        for row in (
            _schedule_from_payload(item)
            for item in _parse_json_list(output)
        )
        if row is not None
    ]


def _build_schedule_target_payload(rows: list[Schedule]) -> dict[str, Any]:
    return {
        "candidates": [
            {
                "id": int(row.id or 0),
                "content": str(row.content or ""),
                "trigger_time": _fmt_dt(row.trigger_time, pattern="%Y-%m-%d %H:%M"),
                "status": str(row.status or "").upper(),
            }
            for row in rows[:120]
            if int(row.id or 0) > 0
        ]
    }


def _parse_int_list(value: Any) -> list[int]:
    if isinstance(value, list):
        raw = value
    elif value is None:
        raw = []
    else:
        raw = [value]
    seen: set[int] = set()
    picked: list[int] = []
    for item in raw:
        try:
            num = int(item)
        except Exception:
            continue
        if num <= 0 or num in seen:
            continue
        seen.add(num)
        picked.append(num)
    return picked


def _read_last_schedule_ids_from_state(state: GraphState) -> list[int]:
    extra = dict(state.get("extra") or {})
    payload = extra.get("schedule_last_query")
    if not isinstance(payload, dict):
        return []
    return _parse_int_list(payload.get("ids"))


def _with_last_schedule_query(
    state: GraphState,
    *,
    rows: list[Schedule],
    label: str,
    scope: str,
    status_filter: str,
) -> GraphState:
    extra = dict(state.get("extra") or {})
    extra["schedule_last_query"] = {
        "ids": [int(row.id) for row in rows if int(row.id or 0) > 0][:300],
        "label": label,
        "scope": scope or "",
        "status_filter": status_filter or "all",
        "updated_at": datetime.utcnow().isoformat(),
    }
    return {**state, "extra": extra}


async def _query_schedules_by_ids(
    *,
    user_id: int,
    platform: str,
    conversation_id: int | None,
    schedule_ids: list[int],
    status_filter: str = "all",
) -> list[Schedule]:
    ids = _parse_int_list(schedule_ids)
    if not ids:
        return []
    db_status = SCHEDULE_STATUS_DB.get(status_filter, None) or "ALL"
    output = await _invoke_schedule_tool(
        user_id=user_id,
        platform=platform,
        conversation_id=conversation_id,
        tool_name="schedule_list",
        args={
            "user_id": user_id,
            "schedule_ids": ids,
            "status": db_status,
            "limit": max(100, len(ids) * 3),
            "order": "asc",
        },
    )
    rows = [
        row
        for row in (
            _schedule_from_payload(item)
            for item in _parse_json_list(output)
        )
        if row is not None
    ]
    index_map = {int(row.id): row for row in rows if int(row.id or 0) > 0}
    ordered: list[Schedule] = []
    for sid in ids:
        row = index_map.get(sid)
        if row is not None:
            ordered.append(row)
    return ordered


async def _query_schedules_by_window(
    *,
    user_id: int,
    platform: str,
    conversation_id: int | None,
    start_at: datetime,
    end_at: datetime,
    status_filter: str = "all",
    limit: int = 400,
) -> list[Schedule]:
    db_status = SCHEDULE_STATUS_DB.get(status_filter, None) or "ALL"
    output = await _invoke_schedule_tool(
        user_id=user_id,
        platform=platform,
        conversation_id=conversation_id,
        tool_name="schedule_list",
        args={
            "user_id": user_id,
            "status": db_status,
            "start_at": start_at.isoformat(sep=" ", timespec="seconds"),
            "end_at": end_at.isoformat(sep=" ", timespec="seconds"),
            "limit": limit,
            "order": "asc",
        },
    )
    return [
        row
        for row in (
            _schedule_from_payload(item)
            for item in _parse_json_list(output)
        )
        if row is not None
    ]


async def _expand_related_schedule_rows(
    *,
    user_id: int,
    platform: str,
    conversation_id: int | None,
    target_rows: list[Schedule],
    status_filter: str,
) -> list[Schedule]:
    if not target_rows:
        return []
    roots = { _schedule_content_root(str(row.content or "")) for row in target_rows }
    roots.discard("")
    if not roots:
        return target_rows
    candidates = await _query_editable_schedules(
        user_id,
        platform=platform,
        conversation_id=conversation_id,
        limit=300,
        target_hint="",
    )
    picked: list[Schedule] = []
    seen: set[int] = set()
    for row in target_rows + candidates:
        sid = int(row.id or 0)
        if sid <= 0 or sid in seen:
            continue
        if status_filter != "all":
            db_status = SCHEDULE_STATUS_DB.get(status_filter, None)
            if db_status and str(row.status or "").upper() != db_status:
                continue
        root = _schedule_content_root(str(row.content or ""))
        if root in roots:
            seen.add(sid)
            picked.append(row)
    return picked


async def _select_schedule_ids_by_llm(
    *,
    content: str,
    conversation_context: str,
    target_content: str,
    operation: str,
    selection_mode: str,
    candidates: list[Schedule],
) -> list[int]:
    if not candidates:
        return []
    llm = get_llm(node_name="schedule_manager")
    runnable = llm.with_structured_output(ScheduleTargetSelection)
    payload = _build_schedule_target_payload(candidates)
    system = SystemMessage(
        content=(
            "你是提醒目标选择器，请按 schema 输出结构化字段。"
            "只输出一个 JSON 对象，不要输出解释文本。"
            "字段: schedule_ids, confidence。"
            "根据用户输入，从候选提醒中选择应被操作的提醒ID列表。"
            "operation 仅为 update 或 delete。"
            "selection_mode 为 all/single/subset/auto。"
            "当用户说‘这几个/这些/刚才那些’时，需要结合会话上下文里的最近结果进行引用解析。"
            "若用户表达“都/全部”，尽量返回多个ID。"
            "若无法确定，返回空数组。"
        )
    )
    human = HumanMessage(
        content=(
            f"operation: {operation}\n"
            f"selection_mode: {selection_mode}\n"
            f"target_content_hint: {target_content or ''}\n\n"
            f"会话上下文:\n{conversation_context}\n\n"
            f"用户输入:\n{content}\n\n"
            f"候选提醒(JSON):\n{json.dumps(payload, ensure_ascii=False)}"
        )
    )
    parsed = await asyncio.wait_for(runnable.ainvoke([system, human]), timeout=45)
    raw_ids = getattr(parsed, "schedule_ids", None)
    if raw_ids is None and isinstance(parsed, dict):
        raw_ids = parsed.get("schedule_ids")
    if not isinstance(raw_ids, list):
        return []
    valid_ids = {int(row.id) for row in candidates if int(row.id or 0) > 0}
    picked: list[int] = []
    for value in raw_ids:
        try:
            schedule_id = int(value)
        except Exception:
            continue
        if schedule_id in valid_ids and schedule_id not in picked:
            picked.append(schedule_id)
    if selection_mode == "single" and len(picked) > 1:
        return picked[:1]
    return picked


async def _select_schedule_ids_from_context(
    *,
    content: str,
    conversation_context: str,
    candidates: list[Schedule],
) -> list[int]:
    if not candidates:
        return []
    llm = get_llm(node_name="schedule_manager")
    runnable = llm.with_structured_output(ScheduleTargetSelection)
    payload = _build_schedule_target_payload(candidates)
    system = SystemMessage(
        content=(
            "你是提醒上下文引用解析器，请按 schema 输出结构化字段。"
            "只输出一个 JSON 对象，不要输出解释文本。"
            "字段: schedule_ids, confidence。"
            "当用户说‘这几个/这些/刚才那些提醒’时，需依据会话上下文中最近助手列举的提醒项目，映射到候选ID。"
            "若仍不确定，返回空数组。"
        )
    )
    human = HumanMessage(
        content=(
            f"会话上下文:\n{conversation_context}\n\n"
            f"用户输入:\n{content}\n\n"
            f"候选提醒(JSON):\n{json.dumps(payload, ensure_ascii=False)}"
        )
    )
    parsed = await asyncio.wait_for(runnable.ainvoke([system, human]), timeout=45)
    raw_ids = getattr(parsed, "schedule_ids", None)
    if raw_ids is None and isinstance(parsed, dict):
        raw_ids = parsed.get("schedule_ids")
    return _parse_int_list(raw_ids)


async def _delete_schedules_by_ids(
    *,
    user_id: int,
    platform: str,
    conversation_id: int | None,
    schedule_ids: list[int],
) -> list[Schedule]:
    if not schedule_ids:
        return []
    rows = await _query_schedules_by_ids(
        user_id=user_id,
        platform=platform,
        conversation_id=conversation_id,
        schedule_ids=schedule_ids,
        status_filter="all",
    )
    if not rows:
        return []
    snapshots = list(rows)
    for row in rows:
        output = await _invoke_schedule_tool(
            user_id=user_id,
            platform=platform,
            conversation_id=conversation_id,
            tool_name="schedule_delete",
            args={"user_id": user_id, "schedule_id": int(row.id or 0)},
        )
        _ = _schedule_from_payload(_parse_json_object(output))
    return snapshots


async def schedule_manager_node(state: GraphState) -> GraphState:
    message = state["message"]
    session = get_session()
    user = await session.get(User, state["user_id"])
    if not user:
        return {**state, "responses": ["未找到用户信息。"]}
    content = (message.content or "").strip()
    context_text = render_conversation_context(state)
    platform = (message.platform or "unknown")
    conversation_id = state.get("conversation_id")
    now_local_dt = await _read_now_local_from_tool(
        user_id=user.id,
        platform=platform,
        conversation_id=conversation_id,
    )
    now_local_text = now_local_dt.strftime("%Y-%m-%d %H:%M")

    parsed: dict = {}
    try:
        parsed = await _understand_schedule_message(content, context_text, now_local_text)
    except Exception:
        parsed = {}

    intent = str(parsed.get("intent") or "").strip().lower()
    confidence = float(parsed.get("confidence") or 0.0)
    schedule_status_filter = _resolve_schedule_status_filter(parsed)
    reference_mode = str(parsed.get("reference_mode") or "auto").strip().lower()
    selection_mode = str(parsed.get("selection_mode") or "auto").strip().lower()
    target_ids = _parse_int_list(parsed.get("target_ids"))
    confirmation_action = _normalize_confirmation_action(str(parsed.get("confirmation_action") or "none"))
    pending_plan = _read_pending_reminder_plan(state)
    intent_is_explicit_operation = intent in {
        "update_by_name",
        "update_by_scope",
        "delete_by_name",
        "delete_by_scope",
        "calendar",
        "time_query",
        "context_recall",
    }
    has_new_reminder_payload = _has_new_reminder_payload(parsed)

    if confirmation_action == "cancel" and not intent_is_explicit_operation and not has_new_reminder_payload:
        if pending_plan is None:
            return {**state, "responses": ["当前没有待确认的提醒计划。"]}
        next_state = _clear_pending_reminder_plan(state)
        return {**next_state, "responses": ["已取消这次提醒计划。"]}

    if confirmation_action == "confirm" and not intent_is_explicit_operation and not has_new_reminder_payload:
        if pending_plan is None:
            return {**state, "responses": ["当前没有待确认的提醒计划。"]}
        base_state = _clear_pending_reminder_plan(state)
        executed_state = await _execute_reminder_plan(
            state=base_state,
            session=session,
            user=user,
            platform=platform,
            conversation_id=conversation_id,
            plan=pending_plan,
            now_local=now_local_dt,
        )
        return _clear_pending_reminder_plan(executed_state)

    if intent == "context_recall":
        try:
            recall_text = await _answer_context_recall(content, context_text)
            return {**state, "responses": [recall_text]}
        except Exception:
            return {
                **state,
                "responses": ["我能读取当前会话上下文，但这次回忆失败了，请重试一次。"],
            }

    if intent == "time_query":
        settings = get_settings()
        now_local = now_local_dt
        weekday_map = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        weekday_text = weekday_map[now_local.weekday()]
        return {
            **state,
            "responses": [
                f"现在时间：{now_local.strftime('%Y-%m-%d %H:%M')}（{weekday_text}，时区 {settings.timezone}）。"
            ],
        }

    if intent == "calendar":
        scope = str(parsed.get("calendar_scope") or "").strip().lower()
        calendar_date = str(parsed.get("calendar_date") or "").strip() or None
        llm_window = _resolve_calendar_window_from_fields(scope, calendar_date)
        if llm_window:
            start_at, end_at, label = llm_window
            ledgers, schedules = await _query_calendar_rows(
                user_id=user.id,
                platform=(message.platform or "unknown"),
                conversation_id=state.get("conversation_id"),
                start_at=start_at,
                end_at=end_at,
            )
            filtered_schedules = _filter_schedules_by_status(schedules, schedule_status_filter)
            response = await _answer_calendar(
                user_id=user.id,
                platform=(message.platform or "unknown"),
                conversation_id=state.get("conversation_id"),
                content=content,
                conversation_context=context_text,
                start_at=start_at,
                end_at=end_at,
                label=label,
                schedule_status_filter=schedule_status_filter,
            )
            next_state = _with_last_schedule_query(
                state,
                rows=filtered_schedules,
                label=label,
                scope=scope or "date",
                status_filter=schedule_status_filter,
            )
            return {**next_state, "responses": [response]}

    if intent in {"update_by_name", "update_by_scope", "delete_by_name", "delete_by_scope"}:
        target_content = str(parsed.get("target_content") or parsed.get("reminder_content") or "").strip()

        async def _resolve_target_schedules(operation: str) -> tuple[list[Schedule], str]:
            if target_ids and reference_mode in {"by_id", "auto"}:
                rows = await _query_schedules_by_ids(
                    user_id=user.id,
                    platform=platform,
                    conversation_id=conversation_id,
                    schedule_ids=target_ids,
                    status_filter=schedule_status_filter,
                )
                return rows, "by_id"

            if reference_mode == "last_result_set":
                last_ids = _read_last_schedule_ids_from_state(state)
                rows = await _query_schedules_by_ids(
                    user_id=user.id,
                    platform=platform,
                    conversation_id=conversation_id,
                    schedule_ids=last_ids,
                    status_filter=schedule_status_filter,
                )
                return rows, "last_result_set"

            if intent in {"update_by_scope", "delete_by_scope"} or reference_mode == "by_scope":
                scope = str(parsed.get("calendar_scope") or "").strip().lower()
                calendar_date = str(parsed.get("calendar_date") or "").strip() or None
                window = _resolve_calendar_window_from_fields(scope, calendar_date)
                if window:
                    start_at, end_at, _ = window
                    rows = await _query_schedules_by_window(
                        user_id=user.id,
                        platform=platform,
                        conversation_id=conversation_id,
                        start_at=start_at,
                        end_at=end_at,
                        status_filter=schedule_status_filter,
                    )
                    return rows, "by_scope"
                return [], "by_scope"

            candidates = await _query_editable_schedules(
                user.id,
                platform=platform,
                conversation_id=conversation_id,
                limit=120,
                target_hint=target_content,
            )
            picked_ids = await _select_schedule_ids_by_llm(
                content=content,
                conversation_context=context_text,
                target_content=target_content,
                operation=operation,
                selection_mode=selection_mode,
                candidates=candidates,
            )
            if not picked_ids:
                picked_ids = await _select_schedule_ids_from_context(
                    content=content,
                    conversation_context=context_text,
                    candidates=candidates,
                )
            if selection_mode == "all" and not picked_ids and candidates:
                picked_ids = [int(row.id) for row in candidates if int(row.id or 0) > 0]
            rows = await _query_schedules_by_ids(
                user_id=user.id,
                platform=platform,
                conversation_id=conversation_id,
                schedule_ids=picked_ids,
                status_filter=schedule_status_filter,
            )
            if rows:
                return rows, "by_name"
            return rows, "by_name"

        operation = "update" if intent.startswith("update_") else "delete"
        target_rows, source = await _resolve_target_schedules(operation)
        if target_rows:
            target_rows = await _expand_related_schedule_rows(
                user_id=user.id,
                platform=(message.platform or "unknown"),
                conversation_id=state.get("conversation_id"),
                target_rows=target_rows,
                status_filter=schedule_status_filter,
            )
        matched_count = len(target_rows)
        target_schedule_ids = [int(row.id) for row in target_rows if int(row.id or 0) > 0]

        if matched_count == 0:
            return {
                **state,
                "responses": ["我还不能定位要操作的提醒。请补充名称、范围、ID，或先查询后说“删这几个”。"],
            }

        if operation == "delete":
            deleted_rows = await _delete_schedules_by_ids(
                user_id=user.id,
                platform=platform,
                conversation_id=conversation_id,
                schedule_ids=target_schedule_ids,
            )
            deleted_count = len(deleted_rows)
            return {
                **state,
                "responses": [f"已定位 {matched_count} 条（来源：{source}），成功删除 {deleted_count} 条提醒。"],
            }

        trigger_time = _parse_run_at_local(str(parsed.get("run_at_local") or ""), get_settings().timezone)
        if trigger_time is None:
            return {
                **state,
                "responses": [_pick_clarify_question(parsed, "我理解到你要修改提醒时间，但缺少明确时间。请补充具体时间。")],
            }
        time_precision = _resolve_time_precision(parsed, trigger_time)
        if time_precision == "second":
            trigger_time = trigger_time.replace(microsecond=0)
        else:
            trigger_time = trigger_time.replace(second=0, microsecond=0)
        if trigger_time <= now_local_dt + timedelta(seconds=1):
            return {
                **state,
                "responses": ["这个目标时间已经太近或已过去，请给我一个稍晚的时间。"],
            }

        event_type = _normalize_event_type(str(parsed.get("event_type") or ""))
        reminder_content = _normalize_reminder_content(
            str(parsed.get("reminder_content") or target_content or (target_rows[0].content if target_rows else ""))
        )
        if not reminder_content:
            return {
                **state,
                "responses": [_pick_clarify_question(parsed, "请告诉我修改后的提醒内容。")],
            }
        priority = _normalize_priority(str(parsed.get("priority") or "medium"))
        offsets_minutes = _read_offsets_from_plan(parsed)
        if not offsets_minutes:
            return {
                **state,
                "responses": [_pick_clarify_question(parsed, "请补充提醒次数，例如“提前30分钟并准点提醒”。")],
            }
        create_text = await _create_reminder(
            session=session,
            user=user,
            platform=platform,
            conversation_id=conversation_id,
            reminder_content=reminder_content,
            event_time=trigger_time,
            event_type=event_type,
            priority=priority,
            offsets_minutes=offsets_minutes,
            now_local=now_local_dt,
        )
        if not create_text:
            return {
                **state,
                "responses": ["新时间下未能成功创建提醒，请确认时间后重试。"],
            }

        deleted_rows = await _delete_schedules_by_ids(
                user_id=user.id,
                platform=platform,
                conversation_id=conversation_id,
                schedule_ids=target_schedule_ids,
            )
        return {
            **state,
            "responses": [f"{create_text}（已定位 {matched_count} 条，替换原提醒 {len(deleted_rows)} 条，来源：{source}）"],
        }

    reminder_intent = intent == "reminder"
    if reminder_intent:
        plan = _compose_reminder_plan(parsed)
        if bool(parsed.get("needs_clarification")):
            question = _pick_clarify_question(parsed, "我需要补充一点信息后再创建提醒。")
            has_core_fields = bool(plan.get("run_at_local") and plan.get("reminder_content") and plan.get("offsets_minutes"))
            next_state = _with_pending_reminder_plan(state, plan) if has_core_fields else _clear_pending_reminder_plan(state)
            return {**next_state, "responses": [question]}

        execute_base = _clear_pending_reminder_plan(state)
        return await _execute_reminder_plan(
            state=execute_base,
            session=session,
            user=user,
            platform=platform,
            conversation_id=conversation_id,
            plan=plan,
            now_local=now_local_dt,
        )

    if reminder_intent:
        return {
            **state,
            "responses": ["我收到了提醒需求，但时间还不够明确。请给我具体时间，例如：`明天中午12点提醒我开会`。"],
        }

    return {
        **state,
        "responses": [
            "我这边主要负责提醒和日历。可直接说：`明天中午12点提醒我开会`，或 `看下本周日程和账单`。"
        ],
    }
