from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config import get_settings
from app.graph.state import GraphState
from app.services.runtime_context import get_session
from app.services.skills import (
    disable_skill,
    get_builtin_skill,
    get_skill,
    get_skill_version_content,
    list_skills_with_source,
    publish_skill,
)
from app.tools.finance import (
    delete_ledger,
    get_latest_ledger,
    insert_ledger,
    list_recent_ledgers,
    update_ledger,
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _amount(value: Any) -> float | None:
    try:
        number = float(value)
    except Exception:
        return None
    if number <= 0:
        return None
    return number


def _to_local_text(dt: datetime | None) -> str:
    if dt is None:
        return ""
    tz_name = get_settings().timezone
    local_tz = ZoneInfo(tz_name)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(local_tz).strftime("%Y-%m-%d %H:%M")


def _parse_datetime(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace(" ", "T"))
    except Exception:
        return None


def _format_skill_status(value: str | None) -> str:
    key = str(value or "").upper()
    return {
        "BUILTIN": "内置",
        "DRAFT": "草稿",
        "PUBLISHED": "已发布",
        "DISABLED": "已停用",
    }.get(key, key or "未知")


async def execute_direct_atomic_action(
    *,
    action: str,
    args: dict[str, Any],
    state: GraphState,
) -> dict[str, Any] | None:
    key = _text(action).lower()
    if not key.startswith("atomic."):
        return None

    session = get_session()
    user_id = int(state.get("user_id") or 0)
    platform = _text(getattr(state.get("message"), "platform", "")) or "unknown"
    if user_id <= 0:
        raise RuntimeError("invalid user id in graph state")

    if key == "atomic.ledger.create":
        amount = _amount(args.get("amount"))
        item = _text(args.get("item"))
        if amount is None or not item:
            return None
        category = _text(args.get("category")) or "其他"
        dt = _parse_datetime(args.get("transaction_time"))
        row = await insert_ledger(
            session,
            user_id=user_id,
            amount=amount,
            category=category,
            item=item,
            transaction_date=dt,
            platform=platform,
        )
        return {
            "kind": "atomic.direct.ledger",
            "action": key,
            "response_text": (
                f"已记账：{row.item} {float(row.amount):.2f} {row.currency}，"
                f"分类 {row.category}，时间 {_to_local_text(row.transaction_date)}。"
            ),
            "row_id": int(row.id or 0),
        }

    if key == "atomic.ledger.list_recent":
        limit = int(args.get("limit") or 10)
        limit = max(1, min(50, limit))
        rows = await list_recent_ledgers(session, user_id=user_id, limit=limit)
        if not rows:
            return {
                "kind": "atomic.direct.ledger",
                "action": key,
                "response_text": "最近没有账单记录。",
                "rows": [],
            }
        lines = ["最近账单："]
        for row in rows:
            lines.append(
                f"#{row.id} | {_to_local_text(row.transaction_date)} | {float(row.amount):.2f} {row.currency} | {row.category} | {row.item}"
            )
        return {
            "kind": "atomic.direct.ledger",
            "action": key,
            "response_text": "\n".join(lines),
            "rows": [int(row.id or 0) for row in rows],
        }

    if key == "atomic.ledger.update_by_id":
        ledger_id = int(args.get("id") or 0)
        amount = _amount(args.get("amount"))
        if ledger_id <= 0 or amount is None:
            return None
        row = await update_ledger(
            session,
            user_id=user_id,
            ledger_id=ledger_id,
            amount=amount,
            category=(_text(args.get("category")) or None),
            item=(_text(args.get("item")) or None),
            platform=platform,
        )
        if not row:
            return {
                "kind": "atomic.direct.ledger",
                "action": key,
                "response_text": f"未找到账单 #{ledger_id}，或它不属于你。",
            }
        return {
            "kind": "atomic.direct.ledger",
            "action": key,
            "response_text": (
                f"已更新账单：#{row.id} {row.item} {float(row.amount):.2f} {row.currency}，"
                f"分类 {row.category}，时间 {_to_local_text(row.transaction_date)}。"
            ),
            "row_id": int(row.id or 0),
        }

    if key == "atomic.ledger.delete_by_id":
        ledger_id = int(args.get("id") or 0)
        if ledger_id <= 0:
            return None
        row = await delete_ledger(
            session,
            user_id=user_id,
            ledger_id=ledger_id,
            platform=platform,
        )
        if not row:
            return {
                "kind": "atomic.direct.ledger",
                "action": key,
                "response_text": f"未找到账单 #{ledger_id}，或它不属于你。",
            }
        return {
            "kind": "atomic.direct.ledger",
            "action": key,
            "response_text": (
                f"已删除账单：#{row.id} {row.item} {float(row.amount):.2f} {row.currency}，"
                f"时间 {_to_local_text(row.transaction_date)}。"
            ),
            "row_id": int(row.id or 0),
        }

    if key == "atomic.ledger.delete_latest":
        latest = await get_latest_ledger(session, user_id=user_id)
        if not latest:
            return {
                "kind": "atomic.direct.ledger",
                "action": key,
                "response_text": "暂无可删除的账单。",
            }
        row = await delete_ledger(
            session,
            user_id=user_id,
            ledger_id=int(latest.id or 0),
            platform=platform,
        )
        if not row:
            return {
                "kind": "atomic.direct.ledger",
                "action": key,
                "response_text": "删除失败，请稍后重试。",
            }
        return {
            "kind": "atomic.direct.ledger",
            "action": key,
            "response_text": (
                f"已删除最近一笔：#{row.id} {row.item} {float(row.amount):.2f} {row.currency}，"
                f"时间 {_to_local_text(row.transaction_date)}。"
            ),
            "row_id": int(row.id or 0),
        }

    if key == "atomic.skill.list":
        rows = await list_skills_with_source(session, user_id)
        if not rows:
            return {
                "kind": "atomic.direct.skill",
                "action": key,
                "response_text": "当前没有可用技能。",
            }
        lines = [f"当前可用技能（{len(rows)}个）："]
        for row in rows:
            source = "内置" if str(row.get("source")) == "builtin" else "用户"
            lines.append(
                f"- {row.get('name') or row.get('slug')}（{source} | v{row.get('active_version') or 1}）"
            )
        return {
            "kind": "atomic.direct.skill",
            "action": key,
            "response_text": "\n".join(lines),
        }

    if key == "atomic.skill.show":
        slug = _text(args.get("slug"))
        source = _text(args.get("source")).lower()
        if not slug:
            return None
        if source == "builtin":
            doc = get_builtin_skill(slug)
            if not doc:
                return {
                    "kind": "atomic.direct.skill",
                    "action": key,
                    "response_text": f"未找到内置技能 `{slug}`。",
                }
            preview = _text(doc.content)
            if len(preview) > 1200:
                preview = preview[:1200] + "\n...\n(已截断)"
            return {
                "kind": "atomic.direct.skill",
                "action": key,
                "response_text": f"技能 `builtin:{slug}` | 内置\n\n{preview}",
            }
        skill = await get_skill(session, user_id, slug)
        if not skill:
            return {
                "kind": "atomic.direct.skill",
                "action": key,
                "response_text": f"未找到技能 `{slug}`。",
            }
        content = _text(await get_skill_version_content(session, skill))
        if len(content) > 1200:
            content = content[:1200] + "\n...\n(已截断)"
        return {
            "kind": "atomic.direct.skill",
            "action": key,
            "response_text": f"技能 `user:{slug}` | {_format_skill_status(str(skill.status))}\n\n{content}",
        }

    if key == "atomic.skill.publish":
        slug = _text(args.get("slug"))
        if not slug:
            return None
        skill = await publish_skill(session, user_id, slug)
        if not skill:
            return {
                "kind": "atomic.direct.skill",
                "action": key,
                "response_text": f"未找到技能 `{slug}`，或该技能没有可发布版本。",
            }
        return {
            "kind": "atomic.direct.skill",
            "action": key,
            "response_text": f"已发布技能 `{slug}` (v{skill.active_version})。",
        }

    if key == "atomic.skill.disable":
        slug = _text(args.get("slug"))
        if not slug:
            return None
        skill = await disable_skill(session, user_id, slug)
        if not skill:
            return {
                "kind": "atomic.direct.skill",
                "action": key,
                "response_text": f"未找到技能 `{slug}`。",
            }
        return {
            "kind": "atomic.direct.skill",
            "action": key,
            "response_text": f"已停用技能 `{slug}`。",
        }

    return None
