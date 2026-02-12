from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_session
from app.models.admin_tool import AdminToolSwitch
from app.models.audit import AuditLog
from app.models.conversation import Conversation
from app.models.feedback import UserFeedback
from app.models.app_setting import AppSetting
from app.models.identity import UserIdentity
from app.models.ledger import Ledger
from app.models.llm_usage import LLMUsageLog
from app.models.memory import LongTermMemory
from app.models.message import Message
from app.models.reminder_delivery import ReminderDelivery
from app.models.schedule import Schedule
from app.models.skill import Skill, SkillStatus, SkillVersion
from app.models.tool_usage import ToolUsageLog
from app.models.user import User
from app.services.admin_tools import set_tool_enabled
from app.services.tool_registry import list_runtime_tool_metas


def require_admin(x_admin_token: str = Header(default="")) -> None:
    settings = get_settings()
    if not settings.admin_token:
        raise HTTPException(status_code=403, detail="admin token not set")
    if x_admin_token != settings.admin_token:
        raise HTTPException(status_code=401, detail="invalid admin token")


class UserBlockPayload(BaseModel):
    is_blocked: bool
    reason: str = ""


class UserQuotaPayload(BaseModel):
    daily_message_limit: int | None = Field(default=None, ge=0, le=100000)
    monthly_message_limit: int | None = Field(default=None, ge=0, le=1000000)


class ToolSwitchPayload(BaseModel):
    enabled: bool


class MiniappHomePopupPayload(BaseModel):
    enabled: bool = False
    title: str = "系统公告"
    content: str = ""
    show_mode: str = "once_per_day"
    start_at: str | None = ""
    end_at: str | None = ""
    version: int = Field(default=1, ge=1, le=100000)
    primary_button_text: str = "我知道了"


router = APIRouter(prefix="/api/admin/v1", dependencies=[Depends(require_admin)])
MINIAPP_HOME_POPUP_KEY = "miniapp_home_popup"


def _default_miniapp_home_popup() -> dict:
    return {
        "enabled": False,
        "title": "系统公告",
        "content": "",
        "show_mode": "once_per_day",
        "start_at": "",
        "end_at": "",
        "version": 1,
        "primary_button_text": "我知道了",
    }


def _today_start_utc_naive() -> datetime:
    settings = get_settings()
    tz = ZoneInfo(settings.timezone)
    now_local = datetime.now(tz)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def _window_start_utc_naive(days: int) -> datetime:
    start = _today_start_utc_naive() - timedelta(days=max(1, days) - 1)
    return start


def _local_naive_to_tz_iso(value: datetime | None) -> str:
    if not value:
        return ""
    tz = ZoneInfo(get_settings().timezone)
    if value.tzinfo is None:
        value = value.replace(tzinfo=tz)
    else:
        value = value.astimezone(tz)
    return value.isoformat(timespec="seconds")


def _parse_detail(value: str) -> object:
    raw = (value or "").strip()
    if not raw:
        return ""
    try:
        return json.loads(raw)
    except Exception:
        return raw


def _page_offset(page: int, size: int) -> int:
    return (max(1, page) - 1) * max(1, size)


@router.get("/dashboard")
async def dashboard(
    days: int = Query(default=30, ge=1, le=120),
    session: AsyncSession = Depends(get_session),
):
    today_start = _today_start_utc_naive()
    window_start = _window_start_utc_naive(days)

    total_users = int((await session.execute(select(func.count(User.id)))).scalar_one() or 0)
    new_users_today = int(
        (
            await session.execute(
                select(func.count(User.id)).where(User.created_at >= today_start)
            )
        ).scalar_one()
        or 0
    )
    dau_today = int(
        (
            await session.execute(
                select(func.count(func.distinct(Message.user_id))).where(
                    Message.created_at >= today_start
                )
            )
        ).scalar_one()
        or 0
    )
    total_messages = int((await session.execute(select(func.count(Message.id)))).scalar_one() or 0)
    window_messages = int(
        (
            await session.execute(
                select(func.count(Message.id)).where(Message.created_at >= window_start)
            )
        ).scalar_one()
        or 0
    )
    token_summary = (
        (
            await session.execute(
                select(
                    func.coalesce(func.sum(LLMUsageLog.prompt_tokens), 0),
                    func.coalesce(func.sum(LLMUsageLog.completion_tokens), 0),
                    func.coalesce(func.sum(LLMUsageLog.total_tokens), 0),
                    func.count(LLMUsageLog.id),
                    func.sum(case((LLMUsageLog.total_tokens > 0, 1), else_=0)),
                ).where(LLMUsageLog.created_at >= window_start)
            )
        ).one()
    )
    total_prompt_tokens = int(token_summary[0] or 0)
    total_completion_tokens = int(token_summary[1] or 0)
    total_tokens = int(token_summary[2] or 0)
    llm_calls = int(token_summary[3] or 0)
    metered_calls = int(token_summary[4] or 0)
    unmetered_calls = max(0, llm_calls - metered_calls)

    users_rows = await session.execute(
        select(func.date(User.created_at), func.count(User.id))
        .where(User.created_at >= window_start)
        .group_by(func.date(User.created_at))
    )
    msg_rows = await session.execute(
        select(func.date(Message.created_at), func.count(Message.id))
        .where(Message.created_at >= window_start)
        .group_by(func.date(Message.created_at))
    )
    token_rows = await session.execute(
        select(
            func.date(LLMUsageLog.created_at),
            func.coalesce(func.sum(LLMUsageLog.prompt_tokens), 0),
            func.coalesce(func.sum(LLMUsageLog.completion_tokens), 0),
            func.coalesce(func.sum(LLMUsageLog.total_tokens), 0),
        )
        .where(LLMUsageLog.created_at >= window_start)
        .group_by(func.date(LLMUsageLog.created_at))
    )

    users_map = {str(k): int(v or 0) for k, v in users_rows.all()}
    msg_map = {str(k): int(v or 0) for k, v in msg_rows.all()}
    token_map = {
        str(k): {
            "prompt_tokens": int(prompt or 0),
            "completion_tokens": int(completion or 0),
            "tokens": int(total or 0),
        }
        for k, prompt, completion, total in token_rows.all()
    }
    trend: list[dict] = []
    start_date = window_start.date()
    for i in range(days):
        d = (start_date + timedelta(days=i)).isoformat()
        trend.append(
            {
                "date": d,
                "new_users": users_map.get(d, 0),
                "messages": msg_map.get(d, 0),
                "tokens": token_map.get(d, {}).get("tokens", 0),
                "prompt_tokens": token_map.get(d, {}).get("prompt_tokens", 0),
                "completion_tokens": token_map.get(d, {}).get("completion_tokens", 0),
            }
        )

    intent_rows = await session.execute(
        select(LLMUsageLog.node, func.count(LLMUsageLog.id))
        .where(LLMUsageLog.created_at >= window_start)
        .group_by(LLMUsageLog.node)
        .order_by(func.count(LLMUsageLog.id).desc())
        .limit(20)
    )
    intent_distribution = [
        {"name": str(node or "unknown"), "count": int(count or 0)}
        for node, count in intent_rows.all()
    ]

    platform_rows = await session.execute(
        select(UserIdentity.platform, func.count(func.distinct(UserIdentity.user_id)))
        .group_by(UserIdentity.platform)
        .order_by(func.count(func.distinct(UserIdentity.user_id)).desc())
    )
    platform_distribution = [
        {"platform": str(platform or "unknown"), "count": int(count or 0)}
        for platform, count in platform_rows.all()
    ]

    return {
        "cards": {
            "total_users": total_users,
            "new_users_today": new_users_today,
            "dau_today": dau_today,
            "total_messages": total_messages,
            "window_messages": window_messages,
            "total_prompt_tokens": total_prompt_tokens,
            "total_completion_tokens": total_completion_tokens,
            "total_tokens": total_tokens,
            "llm_calls": llm_calls,
            "metered_calls": metered_calls,
            "unmetered_calls": unmetered_calls,
        },
        "trend_days": days,
        "trend": trend,
        "intent_distribution": intent_distribution,
        "platform_distribution": platform_distribution,
    }


@router.get("/users")
async def admin_users(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
    platform: str | None = Query(default=None),
    q: str | None = Query(default=None),
    blocked: bool | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(User)
    if platform:
        stmt = stmt.where(User.platform == platform.strip().lower())
    if blocked is not None:
        stmt = stmt.where(User.is_blocked == blocked)
    if q and q.strip():
        keyword = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                User.nickname.ilike(keyword),
                User.email.ilike(keyword),
                User.platform_id.ilike(keyword),
                User.ai_name.ilike(keyword),
            )
        )

    total = int(
        (
            await session.execute(
                select(func.count()).select_from(stmt.subquery())
            )
        ).scalar_one()
        or 0
    )
    rows = (
        (
            await session.execute(
                stmt.order_by(User.id.desc()).offset(_page_offset(page, size)).limit(size)
            )
        )
        .scalars()
        .all()
    )
    ids = [row.id for row in rows if row.id]

    msg_count_map: dict[int, int] = {}
    ledger_count_map: dict[int, int] = {}
    skill_count_map: dict[int, int] = {}
    identity_count_map: dict[int, int] = {}
    last_active_map: dict[int, str] = {}
    if ids:
        msg_counts = await session.execute(
            select(Message.user_id, func.count(Message.id))
            .where(Message.user_id.in_(ids))
            .group_by(Message.user_id)
        )
        msg_count_map = {int(uid): int(count) for uid, count in msg_counts.all()}

        ledger_counts = await session.execute(
            select(Ledger.user_id, func.count(Ledger.id))
            .where(Ledger.user_id.in_(ids))
            .group_by(Ledger.user_id)
        )
        ledger_count_map = {int(uid): int(count) for uid, count in ledger_counts.all()}

        skill_counts = await session.execute(
            select(Skill.user_id, func.count(Skill.id))
            .where(Skill.user_id.in_(ids))
            .group_by(Skill.user_id)
        )
        skill_count_map = {int(uid): int(count) for uid, count in skill_counts.all()}

        identity_counts = await session.execute(
            select(UserIdentity.user_id, func.count(func.distinct(UserIdentity.platform)))
            .where(UserIdentity.user_id.in_(ids))
            .group_by(UserIdentity.user_id)
        )
        identity_count_map = {int(uid): int(count) for uid, count in identity_counts.all()}

        last_active = await session.execute(
            select(Message.user_id, func.max(Message.created_at))
            .where(Message.user_id.in_(ids))
            .group_by(Message.user_id)
        )
        last_active_map = {
            int(uid): (ts.isoformat() if ts else "")
            for uid, ts in last_active.all()
        }

    return {
        "page": page,
        "size": size,
        "total": total,
        "items": [
            {
                "id": row.id,
                "uuid": row.uuid,
                "nickname": row.nickname,
                "platform": row.platform,
                "platform_id": row.platform_id,
                "email": row.email,
                "setup_stage": row.setup_stage,
                "binding_stage": row.binding_stage,
                "is_blocked": bool(row.is_blocked),
                "blocked_reason": row.blocked_reason or "",
                "daily_message_limit": int(row.daily_message_limit or 0),
                "monthly_message_limit": int(row.monthly_message_limit or 0),
                "message_count": msg_count_map.get(row.id, 0),
                "ledger_count": ledger_count_map.get(row.id, 0),
                "skill_count": skill_count_map.get(row.id, 0),
                "identity_platform_count": identity_count_map.get(row.id, 0),
                "last_active_at": last_active_map.get(row.id, ""),
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ],
    }


@router.get("/users/{user_id}")
async def admin_user_detail(
    user_id: int,
    session: AsyncSession = Depends(get_session),
):
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="user not found")

    identities = (
        (
            await session.execute(
                select(UserIdentity).where(UserIdentity.user_id == user_id).order_by(UserIdentity.id.asc())
            )
        )
        .scalars()
        .all()
    )

    message_count = int(
        (
            await session.execute(
                select(func.count(Message.id)).where(Message.user_id == user_id)
            )
        ).scalar_one()
        or 0
    )
    conversation_count = int(
        (
            await session.execute(
                select(func.count(Conversation.id)).where(Conversation.user_id == user_id)
            )
        ).scalar_one()
        or 0
    )
    ledger_count = int(
        (
            await session.execute(
                select(func.count(Ledger.id)).where(Ledger.user_id == user_id)
            )
        ).scalar_one()
        or 0
    )
    schedule_count = int(
        (
            await session.execute(
                select(func.count(Schedule.id)).where(Schedule.user_id == user_id)
            )
        ).scalar_one()
        or 0
    )
    skill_count = int(
        (
            await session.execute(
                select(func.count(Skill.id)).where(Skill.user_id == user_id)
            )
        ).scalar_one()
        or 0
    )
    memory_count = int(
        (
            await session.execute(
                select(func.count(LongTermMemory.id)).where(LongTermMemory.user_id == user_id)
            )
        ).scalar_one()
        or 0
    )
    token_total = int(
        (
            await session.execute(
                select(func.coalesce(func.sum(LLMUsageLog.total_tokens), 0)).where(
                    LLMUsageLog.user_id == user_id
                )
            )
        ).scalar_one()
        or 0
    )

    last_active_at = (
        (
            await session.execute(
                select(func.max(Message.created_at)).where(Message.user_id == user_id)
            )
        ).scalar_one()
        or None
    )

    return {
        "id": user.id,
        "uuid": user.uuid,
        "nickname": user.nickname,
        "platform": user.platform,
        "platform_id": user.platform_id,
        "email": user.email,
        "ai_name": user.ai_name,
        "ai_emoji": user.ai_emoji,
        "setup_stage": user.setup_stage,
        "binding_stage": user.binding_stage,
        "is_blocked": bool(user.is_blocked),
        "blocked_reason": user.blocked_reason or "",
        "daily_message_limit": int(user.daily_message_limit or 0),
        "monthly_message_limit": int(user.monthly_message_limit or 0),
        "created_at": user.created_at.isoformat(),
        "updated_at": user.updated_at.isoformat(),
        "last_active_at": last_active_at.isoformat() if last_active_at else "",
        "identities": [
            {
                "platform": row.platform,
                "platform_id": row.platform_id,
                "created_at": row.created_at.isoformat(),
            }
            for row in identities
        ],
        "stats": {
            "messages": message_count,
            "conversations": conversation_count,
            "ledgers": ledger_count,
            "schedules": schedule_count,
            "skills": skill_count,
            "memories": memory_count,
            "token_total": token_total,
        },
    }


@router.patch("/users/{user_id}/block")
async def admin_user_block(
    user_id: int,
    payload: UserBlockPayload,
    session: AsyncSession = Depends(get_session),
):
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="user not found")
    user.is_blocked = bool(payload.is_blocked)
    user.blocked_reason = (payload.reason or "").strip() if payload.is_blocked else ""
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return {
        "ok": True,
        "user_id": user.id,
        "is_blocked": bool(user.is_blocked),
        "blocked_reason": user.blocked_reason or "",
    }


@router.patch("/users/{user_id}/quota")
async def admin_user_quota(
    user_id: int,
    payload: UserQuotaPayload,
    session: AsyncSession = Depends(get_session),
):
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="user not found")
    if payload.daily_message_limit is not None:
        user.daily_message_limit = int(payload.daily_message_limit)
    if payload.monthly_message_limit is not None:
        user.monthly_message_limit = int(payload.monthly_message_limit)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return {
        "ok": True,
        "user_id": user.id,
        "daily_message_limit": int(user.daily_message_limit or 0),
        "monthly_message_limit": int(user.monthly_message_limit or 0),
    }


@router.get("/conversations")
async def admin_conversations(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
    user_id: int | None = Query(default=None),
    q: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(Conversation)
    if user_id is not None:
        stmt = stmt.where(Conversation.user_id == user_id)
    if q and q.strip():
        stmt = stmt.where(Conversation.title.ilike(f"%{q.strip()}%"))

    total = int(
        (
            await session.execute(
                select(func.count()).select_from(stmt.subquery())
            )
        ).scalar_one()
        or 0
    )
    rows = (
        (
            await session.execute(
                stmt.order_by(Conversation.last_message_at.desc())
                .offset(_page_offset(page, size))
                .limit(size)
            )
        )
        .scalars()
        .all()
    )
    ids = [row.id for row in rows if row.id]
    user_ids = [row.user_id for row in rows if row.user_id]

    msg_count_map: dict[int, int] = {}
    user_name_map: dict[int, str] = {}
    if ids:
        msg_counts = await session.execute(
            select(Message.conversation_id, func.count(Message.id))
            .where(Message.conversation_id.in_(ids))
            .group_by(Message.conversation_id)
        )
        msg_count_map = {int(cid): int(count) for cid, count in msg_counts.all()}
    if user_ids:
        users = await session.execute(select(User.id, User.nickname).where(User.id.in_(user_ids)))
        user_name_map = {int(uid): str(name or "") for uid, name in users.all()}

    return {
        "page": page,
        "size": size,
        "total": total,
        "items": [
            {
                "id": row.id,
                "user_id": row.user_id,
                "user_nickname": user_name_map.get(row.user_id, ""),
                "title": row.title,
                "summary": row.summary,
                "message_count": msg_count_map.get(row.id, 0),
                "created_at": row.created_at.isoformat(),
                "last_message_at": row.last_message_at.isoformat(),
                "updated_at": row.updated_at.isoformat(),
            }
            for row in rows
        ],
    }


@router.get("/conversations/{conversation_id}/messages")
async def admin_conversation_messages(
    conversation_id: int,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
):
    conv = await session.get(Conversation, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="conversation not found")
    stmt = select(Message).where(Message.conversation_id == conversation_id)
    total = int(
        (
            await session.execute(
                select(func.count()).select_from(stmt.subquery())
            )
        ).scalar_one()
        or 0
    )
    rows = (
        (
            await session.execute(
                stmt.order_by(Message.id.asc()).offset(_page_offset(page, size)).limit(size)
            )
        )
        .scalars()
        .all()
    )
    return {
        "conversation_id": conversation_id,
        "page": page,
        "size": size,
        "total": total,
        "items": [
            {
                "id": row.id,
                "role": row.role,
                "platform": row.platform,
                "content": row.content,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ],
    }


@router.get("/conversations/stats")
async def admin_conversation_stats(
    days: int = Query(default=30, ge=1, le=120),
    session: AsyncSession = Depends(get_session),
):
    start_at = _window_start_utc_naive(days)
    by_day_rows = await session.execute(
        select(func.date(Message.created_at), Message.role, func.count(Message.id))
        .where(Message.created_at >= start_at)
        .group_by(func.date(Message.created_at), Message.role)
    )
    by_day_map: dict[str, dict] = {}
    for d, role, count in by_day_rows.all():
        key = str(d)
        row = by_day_map.setdefault(key, {"date": key, "user": 0, "assistant": 0, "system": 0})
        role_key = str(role or "user").lower()
        if role_key not in row:
            row[role_key] = 0
        row[role_key] += int(count or 0)

    platform_rows = await session.execute(
        select(Message.platform, func.count(Message.id))
        .where(Message.created_at >= start_at)
        .group_by(Message.platform)
        .order_by(func.count(Message.id).desc())
    )
    by_platform = [
        {"platform": str(platform or "unknown"), "count": int(count or 0)}
        for platform, count in platform_rows.all()
    ]

    return {
        "days": days,
        "by_day": [by_day_map[key] for key in sorted(by_day_map.keys())],
        "by_platform": by_platform,
    }


@router.get("/skills")
async def admin_skills(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
    user_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
    q: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(Skill)
    if user_id is not None:
        stmt = stmt.where(Skill.user_id == user_id)
    if status and status.strip():
        stmt = stmt.where(Skill.status == status.strip().upper())
    if q and q.strip():
        keyword = f"%{q.strip()}%"
        stmt = stmt.where(or_(Skill.name.ilike(keyword), Skill.slug.ilike(keyword), Skill.description.ilike(keyword)))

    total = int((await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one() or 0)
    rows = (
        (
            await session.execute(
                stmt.order_by(Skill.id.desc()).offset(_page_offset(page, size)).limit(size)
            )
        )
        .scalars()
        .all()
    )
    ids = [row.id for row in rows if row.id]
    user_ids = [row.user_id for row in rows if row.user_id]

    version_count_map: dict[int, int] = {}
    user_name_map: dict[int, str] = {}
    if ids:
        version_rows = await session.execute(
            select(SkillVersion.skill_id, func.count(SkillVersion.id))
            .where(SkillVersion.skill_id.in_(ids))
            .group_by(SkillVersion.skill_id)
        )
        version_count_map = {int(skill_id): int(count) for skill_id, count in version_rows.all()}
    if user_ids:
        users = await session.execute(select(User.id, User.nickname).where(User.id.in_(user_ids)))
        user_name_map = {int(uid): str(name or "") for uid, name in users.all()}

    return {
        "page": page,
        "size": size,
        "total": total,
        "items": [
            {
                "id": row.id,
                "user_id": row.user_id,
                "user_nickname": user_name_map.get(row.user_id, ""),
                "slug": row.slug,
                "name": row.name,
                "description": row.description,
                "status": row.status,
                "active_version": row.active_version,
                "version_count": version_count_map.get(row.id, 0),
                "created_at": row.created_at.isoformat(),
                "updated_at": row.updated_at.isoformat(),
            }
            for row in rows
        ],
    }


@router.get("/skills/{skill_id}/versions")
async def admin_skill_versions(
    skill_id: int,
    session: AsyncSession = Depends(get_session),
):
    skill = await session.get(Skill, skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="skill not found")
    rows = (
        (
            await session.execute(
                select(SkillVersion)
                .where(SkillVersion.skill_id == skill_id)
                .order_by(SkillVersion.version.desc())
            )
        )
        .scalars()
        .all()
    )
    return {
        "skill": {
            "id": skill.id,
            "slug": skill.slug,
            "name": skill.name,
            "status": skill.status,
            "active_version": skill.active_version,
        },
        "versions": [
            {
                "id": row.id,
                "version": row.version,
                "content_md": row.content_md,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ],
    }


@router.post("/skills/{skill_id}/disable")
async def admin_skill_disable(
    skill_id: int,
    session: AsyncSession = Depends(get_session),
):
    skill = await session.get(Skill, skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="skill not found")
    skill.status = SkillStatus.DISABLED
    session.add(skill)
    await session.commit()
    await session.refresh(skill)
    return {"ok": True, "skill_id": skill.id, "status": skill.status}


@router.get("/tools")
async def admin_tools(
    days: int = Query(default=30, ge=1, le=120),
    session: AsyncSession = Depends(get_session),
):
    runtime = await list_runtime_tool_metas()
    start_at = _window_start_utc_naive(days)

    usage_rows = await session.execute(
        select(
            ToolUsageLog.tool_source,
            ToolUsageLog.tool_name,
            func.count(ToolUsageLog.id),
            func.coalesce(
                func.sum(case((ToolUsageLog.success.is_(True), 1), else_=0)),
                0,
            ),
            func.coalesce(func.avg(ToolUsageLog.latency_ms), 0),
        )
        .where(ToolUsageLog.created_at >= start_at)
        .group_by(ToolUsageLog.tool_source, ToolUsageLog.tool_name)
    )
    usage_map: dict[str, dict] = {}
    for source, name, total, success_count, avg_latency in usage_rows.all():
        key = f"{str(source)}::{str(name)}"
        usage_map[key] = {
            "calls": int(total or 0),
            "success": int(success_count or 0),
            "avg_latency_ms": int(float(avg_latency or 0)),
        }

    switch_rows = await session.execute(select(AdminToolSwitch))
    switch_map = {
        f"{row.tool_source}::{row.tool_name}": bool(row.enabled)
        for row in switch_rows.scalars().all()
    }

    merged: dict[str, dict] = {}
    for item in runtime:
        key = f"{item['source']}::{item['name']}"
        usage = usage_map.get(key, {"calls": 0, "success": 0, "avg_latency_ms": 0})
        calls = usage["calls"]
        merged[key] = {
            "source": item["source"],
            "name": item["name"],
            "description": item["description"],
            "enabled": bool(switch_map.get(key, item["enabled"])),
            "calls": calls,
            "success_rate": (usage["success"] / calls) if calls else 0.0,
            "avg_latency_ms": usage["avg_latency_ms"],
        }
    for key, usage in usage_map.items():
        if key in merged:
            continue
        source, name = key.split("::", 1)
        calls = usage["calls"]
        merged[key] = {
            "source": source,
            "name": name,
            "description": "",
            "enabled": bool(switch_map.get(key, True)),
            "calls": calls,
            "success_rate": (usage["success"] / calls) if calls else 0.0,
            "avg_latency_ms": usage["avg_latency_ms"],
        }

    items = sorted(merged.values(), key=lambda row: row["calls"], reverse=True)
    return {"days": days, "items": items}


@router.patch("/tools/{tool_source}/{tool_name}")
async def admin_tool_switch(
    tool_source: str,
    tool_name: str,
    payload: ToolSwitchPayload,
):
    row = await set_tool_enabled(tool_source, tool_name, bool(payload.enabled))
    return {
        "ok": True,
        "source": row.tool_source,
        "name": row.tool_name,
        "enabled": bool(row.enabled),
        "updated_at": row.updated_at.isoformat(),
    }


@router.get("/schedules")
async def admin_schedules(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
    user_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(Schedule)
    if user_id is not None:
        stmt = stmt.where(Schedule.user_id == user_id)
    if status and status.strip():
        stmt = stmt.where(Schedule.status == status.strip().upper())
    total = int((await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one() or 0)
    rows = (
        (
            await session.execute(
                stmt.order_by(Schedule.trigger_time.desc()).offset(_page_offset(page, size)).limit(size)
            )
        )
        .scalars()
        .all()
    )
    user_ids = [row.user_id for row in rows if row.user_id]
    user_map: dict[int, str] = {}
    if user_ids:
        users = await session.execute(select(User.id, User.nickname).where(User.id.in_(user_ids)))
        user_map = {int(uid): str(name or "") for uid, name in users.all()}

    return {
        "page": page,
        "size": size,
        "total": total,
        "items": [
            {
                "id": row.id,
                "user_id": row.user_id,
                "user_nickname": user_map.get(row.user_id, ""),
                "job_id": row.job_id,
                "content": row.content,
                "trigger_time": _local_naive_to_tz_iso(row.trigger_time),
                "status": row.status,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ],
    }


@router.get("/schedules/delivery")
async def admin_schedule_delivery(
    days: int = Query(default=30, ge=1, le=120),
    session: AsyncSession = Depends(get_session),
):
    start_at = _window_start_utc_naive(days)
    rows = await session.execute(
        select(ReminderDelivery.platform, ReminderDelivery.status, func.count(ReminderDelivery.id))
        .where(ReminderDelivery.created_at >= start_at)
        .group_by(ReminderDelivery.platform, ReminderDelivery.status)
    )
    data: dict[str, dict] = {}
    for platform, status, count in rows.all():
        key = str(platform or "unknown")
        item = data.setdefault(
            key,
            {"platform": key, "total": 0, "delivered": 0, "failed": 0, "pending": 0},
        )
        c = int(count or 0)
        item["total"] += c
        status_key = str(status or "").upper()
        if status_key == "DELIVERED":
            item["delivered"] += c
        elif status_key in {"FAILED", "ERROR"}:
            item["failed"] += c
        else:
            item["pending"] += c
    items = []
    for row in data.values():
        total = row["total"] or 1
        row["success_rate"] = row["delivered"] / total
        items.append(row)
    items.sort(key=lambda x: x["total"], reverse=True)
    return {"days": days, "items": items}


@router.get("/audit")
async def admin_audit(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=30, ge=1, le=300),
    user_id: int | None = Query(default=None),
    action: str | None = Query(default=None),
    q: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(AuditLog)
    if user_id is not None:
        stmt = stmt.where(AuditLog.user_id == user_id)
    if action and action.strip():
        stmt = stmt.where(AuditLog.action == action.strip())
    if q and q.strip():
        stmt = stmt.where(AuditLog.detail.ilike(f"%{q.strip()}%"))

    total = int((await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one() or 0)
    rows = (
        (
            await session.execute(
                stmt.order_by(AuditLog.id.desc()).offset(_page_offset(page, size)).limit(size)
            )
        )
        .scalars()
        .all()
    )
    return {
        "page": page,
        "size": size,
        "total": total,
        "items": [
            {
                "id": row.id,
                "user_id": row.user_id,
                "platform": row.platform,
                "action": row.action,
                "detail": _parse_detail(row.detail),
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ],
    }


@router.get("/feedbacks")
async def admin_feedbacks(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=30, ge=1, le=300),
    user_id: int | None = Query(default=None),
    platform: str | None = Query(default=None),
    q: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(UserFeedback)
    if user_id is not None:
        stmt = stmt.where(UserFeedback.user_id == user_id)
    if platform and platform.strip():
        stmt = stmt.where(UserFeedback.platform == platform.strip())
    if q and q.strip():
        keyword = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                UserFeedback.content.ilike(keyword),
                UserFeedback.client_page.ilike(keyword),
                UserFeedback.app_version.ilike(keyword),
                UserFeedback.env_version.ilike(keyword),
            )
        )

    total = int((await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one() or 0)
    rows = (
        (
            await session.execute(
                stmt.order_by(UserFeedback.id.desc()).offset(_page_offset(page, size)).limit(size)
            )
        )
        .scalars()
        .all()
    )
    user_ids = [row.user_id for row in rows if row.user_id]
    user_map: dict[int, str] = {}
    if user_ids:
        users = await session.execute(select(User.id, User.nickname).where(User.id.in_(user_ids)))
        user_map = {int(uid): str(name or "") for uid, name in users.all()}

    return {
        "page": page,
        "size": size,
        "total": total,
        "items": [
            {
                "id": row.id,
                "user_id": row.user_id,
                "user_nickname": user_map.get(row.user_id, ""),
                "platform": row.platform,
                "content": row.content,
                "app_version": row.app_version,
                "env_version": row.env_version,
                "client_page": row.client_page,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ],
    }


@router.get("/miniapp/home-popup")
async def admin_miniapp_home_popup(
    session: AsyncSession = Depends(get_session),
):
    data = _default_miniapp_home_popup()
    row = (
        (
            await session.execute(
                select(AppSetting).where(AppSetting.key == MINIAPP_HOME_POPUP_KEY).limit(1)
            )
        )
        .scalars()
        .first()
    )
    if row:
        try:
            payload = json.loads(row.value or "{}")
            if isinstance(payload, dict):
                data.update(payload)
        except Exception:
            pass
    return data


@router.put("/miniapp/home-popup")
async def admin_miniapp_home_popup_upsert(
    payload: MiniappHomePopupPayload,
    session: AsyncSession = Depends(get_session),
):
    mode = str(payload.show_mode or "").strip().lower()
    if mode not in {"always", "once_per_day", "once_per_version"}:
        raise HTTPException(status_code=400, detail="invalid show_mode")
    for field_name in ("start_at", "end_at"):
        raw = str(getattr(payload, field_name) or "").strip()
        if not raw:
            continue
        try:
            datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            raise HTTPException(status_code=400, detail=f"{field_name} invalid datetime format")
    data = {
        "enabled": bool(payload.enabled),
        "title": str(payload.title or "").strip()[:80],
        "content": str(payload.content or "").strip()[:1000],
        "show_mode": mode,
        "start_at": str(payload.start_at or "").strip(),
        "end_at": str(payload.end_at or "").strip(),
        "version": int(payload.version or 1),
        "primary_button_text": str(payload.primary_button_text or "我知道了").strip()[:20] or "我知道了",
    }
    row = (
        (
            await session.execute(
                select(AppSetting).where(AppSetting.key == MINIAPP_HOME_POPUP_KEY).limit(1)
            )
        )
        .scalars()
        .first()
    )
    if not row:
        row = AppSetting(key=MINIAPP_HOME_POPUP_KEY, value=json.dumps(data, ensure_ascii=False))
    else:
        row.value = json.dumps(data, ensure_ascii=False)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return {
        "ok": True,
        "updated_at": row.updated_at.isoformat(),
        "config": data,
    }
