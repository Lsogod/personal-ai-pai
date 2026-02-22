from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_or_create_user
from app.core.config import get_settings
from app.schemas.unified import UnifiedMessage
from app.services.commands.conversation import handle_conversation_command
from app.services.llm import get_llm
from app.services.memory import (
    extract_memory_candidates,
    retrieve_relevant_long_term_memories,
    upsert_long_term_memories,
)
from app.services.sender import UnifiedSender
from app.services.scheduler import get_scheduler
from app.graph.workflow import get_graph
from app.services.dedup import is_duplicate
from app.services.audit import log_event
from app.models.message import Message
from app.models.user import User
from app.services.conversations import (
    apply_assistant_message_updates,
    apply_user_message_updates,
    ensure_active_conversation,
)
from app.services.realtime import get_notification_hub
from app.services.runtime_context import (
    set_session,
    reset_session,
    set_scheduler,
    reset_scheduler,
    set_tool_user_id,
    reset_tool_user_id,
    set_tool_platform,
    reset_tool_platform,
    set_tool_conversation_id,
    reset_tool_conversation_id,
)


_sender = UnifiedSender()
_scheduler = get_scheduler()
logger = logging.getLogger(__name__)
settings = get_settings()
UNSUPPORTED_REBIND_TEXT = "当前版本暂不支持换绑/解绑。你仍可使用 `/bind new` 与 `/bind <6位码>` 进行账号绑定合并。"
MEMORY_EXTRACT_TIMEOUT_SEC = 20
MEMORY_UPSERT_TIMEOUT_SEC = 20


def _today_start_utc_naive() -> datetime:
    tz = ZoneInfo(settings.timezone)
    now_local = datetime.now(tz)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def _month_start_utc_naive() -> datetime:
    tz = ZoneInfo(settings.timezone)
    now_local = datetime.now(tz)
    start_local = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start_local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def _to_client_tz_iso(value: datetime | None) -> str:
    if value is None:
        return ""
    tz = ZoneInfo(settings.timezone)
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo("UTC"))
    return value.astimezone(tz).isoformat(timespec="seconds")


class RebindIntentExtraction(BaseModel):
    block: bool = Field(default=False)


async def _is_rebind_natural_intent(text: str) -> bool:
    content = (text or "").strip()
    if not content or content.startswith("/"):
        return False
    llm = get_llm(node_name="message_handler")
    runnable = llm.with_structured_output(RebindIntentExtraction)
    system = SystemMessage(
        content=(
            "你是意图分类器。判断用户是否在表达“换绑/解绑/重新绑定账号”的诉求。"
            "只输出 JSON：{\"block\": true|false}。"
            "block=true 仅在用户明确要求更换绑定关系、解除绑定、改绑账号时。"
            "咨询一般功能、普通绑定、记账、提醒等都应是 block=false。"
        )
    )
    human = HumanMessage(content=content)
    try:
        parsed = await runnable.ainvoke([system, human])
        return bool(getattr(parsed, "block", False) is True)
    except Exception:
        return False


async def _load_context_messages(
    session: AsyncSession,
    user_id: int,
    conversation_id: int,
    limit: int = 20,
) -> list[dict[str, str]]:
    result = await session.execute(
        select(Message)
        .where(
            Message.user_id == user_id,
            Message.conversation_id == conversation_id,
        )
        .order_by(Message.id.desc())
        .limit(limit)
    )
    rows = list(result.scalars().all())
    rows.reverse()

    context_messages: list[dict[str, str]] = []
    for row in rows:
        content = (row.content or "").strip()
        if not content:
            continue
        context_messages.append(
            {
                "role": (row.role or "user").strip().lower(),
                "content": content,
                "created_at": _to_client_tz_iso(row.created_at),
            }
        )
    return context_messages


async def handle_message(
    platform: str,
    normalized: dict[str, Any],
    session: AsyncSession,
) -> dict[str, Any]:
    if not normalized.get("platform_id"):
        return {"ok": False, "error": "missing platform_id"}

    if await is_duplicate(platform, normalized.get("message_id")):
        return {"ok": True, "dedup": True}

    user, _ = await get_or_create_user(session, platform, normalized["platform_id"])
    conversation = await ensure_active_conversation(session, user)
    user_id = user.id
    user_uuid = user.uuid
    user_setup_stage = user.setup_stage
    reply_platform = platform
    reply_platform_id = normalized["platform_id"]

    message = UnifiedMessage(
        platform=platform,
        user_uuid=user_uuid,
        content=normalized.get("content") or "",
        image_urls=normalized.get("image_urls") or [],
        raw_data=normalized.get("raw_data", {}),
        message_id=normalized.get("message_id"),
        event_ts=normalized.get("event_ts"),
    )

    async def _emit_block_notice(text: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        await _sender.send_text(reply_platform, reply_platform_id, text)
        await log_event(
            session,
            action="message_sent",
            platform=platform,
            user_id=user_id,
            detail={"content": text, "conversation_id": conversation.id, **(extra or {})},
        )
        conversation.last_message_at = datetime.now(timezone.utc)
        session.add(conversation)
        session.add(
            Message(
                user_id=user_id,
                conversation_id=conversation.id,
                role="assistant",
                content=text,
                platform=platform,
            )
        )
        await session.commit()
        if platform != "web":
            await get_notification_hub().send_to_user(
                user_id,
                {
                    "type": "message",
                    "role": "assistant",
                    "content": text,
                    "platform": platform,
                    "conversation_id": conversation.id,
                    "created_at": _to_client_tz_iso(datetime.utcnow()),
                },
            )
        return {"ok": True, "responses": [text]}

    if bool(user.is_blocked):
        blocked_msg = "你的账号已被管理员禁用。"
        reason = (user.blocked_reason or "").strip()
        if reason:
            blocked_msg = f"{blocked_msg} 原因：{reason}"
        await log_event(
            session,
            action="message_blocked",
            platform=platform,
            user_id=user_id,
            detail={
                "reason": reason,
                "message_id": message.message_id,
                "conversation_id": conversation.id,
            },
        )
        result = await _emit_block_notice(blocked_msg, {"blocked": True})
        result["blocked"] = True
        return result

    if int(user.daily_message_limit or 0) > 0:
        today_start_utc = _today_start_utc_naive()
        count_result = await session.execute(
            select(func.count(Message.id)).where(
                Message.user_id == user_id,
                Message.role == "user",
                Message.created_at >= today_start_utc,
            )
        )
        sent_today = int(count_result.scalar_one() or 0)
        if sent_today >= int(user.daily_message_limit):
            limit_msg = f"你今日消息配额已用完（{int(user.daily_message_limit)} 条），请明天再试。"
            await log_event(
                session,
                action="message_quota_blocked",
                platform=platform,
                user_id=user_id,
                detail={
                    "daily_message_limit": int(user.daily_message_limit),
                    "sent_today": sent_today,
                    "message_id": message.message_id,
                    "conversation_id": conversation.id,
                },
            )
            result = await _emit_block_notice(limit_msg, {"quota_scope": "daily", "quota_blocked": True})
            result["quota_blocked"] = True
            return result

    if int(user.monthly_message_limit or 0) > 0:
        month_start_utc = _month_start_utc_naive()
        month_count_result = await session.execute(
            select(func.count(Message.id)).where(
                Message.user_id == user_id,
                Message.role == "user",
                Message.created_at >= month_start_utc,
            )
        )
        sent_this_month = int(month_count_result.scalar_one() or 0)
        if sent_this_month >= int(user.monthly_message_limit):
            limit_msg = f"你本月消息配额已用完（{int(user.monthly_message_limit)} 条），请下月再试。"
            await log_event(
                session,
                action="message_quota_blocked",
                platform=platform,
                user_id=user_id,
                detail={
                    "scope": "monthly",
                    "monthly_message_limit": int(user.monthly_message_limit),
                    "sent_this_month": sent_this_month,
                    "message_id": message.message_id,
                    "conversation_id": conversation.id,
                },
            )
            result = await _emit_block_notice(limit_msg, {"quota_scope": "monthly", "quota_blocked": True})
            result["quota_blocked"] = True
            return result

    state = {
        "user_id": user_id,
        "conversation_id": conversation.id,
        "user_setup_stage": user_setup_stage,
        "message": message,
        "responses": [],
    }

    await log_event(
        session,
        action="message_received",
        platform=platform,
        user_id=user_id,
        detail={
            "message_id": message.message_id,
            "content": message.content,
            "conversation_id": conversation.id,
        },
    )
    apply_user_message_updates(conversation, message.content)
    session.add(conversation)
    user_message_row = Message(
        user_id=user_id,
        conversation_id=conversation.id,
        role="user",
        content=message.content,
        platform=platform,
    )
    session.add(user_message_row)
    await session.commit()
    if platform != "web":
        await get_notification_hub().send_to_user(
            user_id,
            {
                "type": "message",
                "role": "user",
                "content": message.content,
                "platform": platform,
                "conversation_id": conversation.id,
                "created_at": _to_client_tz_iso(datetime.utcnow()),
            },
        )

    if await _is_rebind_natural_intent(message.content):
        responses = [UNSUPPORTED_REBIND_TEXT]
        skip_summary_update = False
    else:
        command_result = await handle_conversation_command(
            session=session,
            user=user,
            conversation=conversation,
            text=message.content,
        )

        if command_result is not None:
            responses, response_conversation = command_result
            conversation = response_conversation
            skip_summary_update = True
        else:
            skip_summary_update = False
            long_term_memories = await retrieve_relevant_long_term_memories(
                session=session,
                user_id=user_id,
                query=message.content or "",
                limit=settings.long_term_memory_retrieve_limit,
            )
            state["extra"] = {
                "conversation_summary": (conversation.summary or "").strip(),
                "context_messages": await _load_context_messages(
                    session=session,
                    user_id=user_id,
                    conversation_id=conversation.id,
                    limit=20,
                ),
                "long_term_memories": long_term_memories,
            }
            graph = await get_graph()
            graph_config = {"configurable": {"thread_id": f"{user_uuid}:{conversation.id}"}}
            # Preserve node-produced conversation-scoped context (e.g. last query result ids)
            # across turns, while always refreshing runtime context fields for this turn.
            try:
                state_snapshot = await graph.aget_state(graph_config)
                prev_values = getattr(state_snapshot, "values", {}) or {}
                prev_extra = prev_values.get("extra")
                if isinstance(prev_extra, dict) and isinstance(state.get("extra"), dict):
                    merged_extra = dict(prev_extra)
                    merged_extra.update(dict(state.get("extra") or {}))
                    state["extra"] = merged_extra
            except Exception:
                pass
            session_token = set_session(session)
            scheduler_token = set_scheduler(_scheduler)
            tool_user_token = set_tool_user_id(user_id)
            tool_platform_token = set_tool_platform(platform)
            tool_conv_token = set_tool_conversation_id(conversation.id)
            try:
                try:
                    result = await graph.ainvoke(
                        state,
                        config=graph_config,
                    )
                    responses = result.get("responses") or []
                except Exception as exc:
                    logger.exception("graph invoke failed: platform=%s user_id=%s", platform, user_id)
                    responses = ["我处理这条消息时失败了。请重试，或先用文字描述金额/分类/事项。"]
            finally:
                reset_tool_conversation_id(tool_conv_token)
                reset_tool_platform(tool_platform_token)
                reset_tool_user_id(tool_user_token)
                reset_scheduler(scheduler_token)
                reset_session(session_token)

    assistant_outputs: list[str] = []
    for text in responses:
        await _sender.send_text(reply_platform, reply_platform_id, text)
        await log_event(
            session,
            action="message_sent",
            platform=platform,
            user_id=user_id,
            detail={"content": text, "conversation_id": conversation.id},
        )
        if skip_summary_update:
            conversation.last_message_at = datetime.now(timezone.utc)
        else:
            apply_assistant_message_updates(conversation, text)
        session.add(conversation)
        session.add(
            Message(
                user_id=user_id,
                conversation_id=conversation.id,
                role="assistant",
                content=text,
                platform=platform,
            )
        )
        await session.commit()
        assistant_outputs.append(text)
        if platform != "web":
            await get_notification_hub().send_to_user(
                user_id,
                {
                    "type": "message",
                    "role": "assistant",
                    "content": text,
                    "platform": platform,
                    "conversation_id": conversation.id,
                    "created_at": _to_client_tz_iso(datetime.utcnow()),
                },
            )

    if (
        settings.long_term_memory_enabled
        and assistant_outputs
        and message.content
        and not str(message.content).strip().startswith("/")
    ):
        candidates: list[dict[str, Any]] = []
        try:
            candidates = await asyncio.wait_for(
                extract_memory_candidates(
                    user_text=message.content,
                    assistant_text="\n".join(assistant_outputs),
                    conversation_summary=(conversation.summary or "").strip(),
                ),
                timeout=MEMORY_EXTRACT_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "long-term memory extract timed out: user_id=%s conversation_id=%s",
                user_id,
                conversation.id,
            )
        except Exception:
            logger.exception("long-term memory extract failed: user_id=%s", user_id)

        if candidates:
            try:
                await asyncio.wait_for(
                    upsert_long_term_memories(
                        session=session,
                        user_id=user_id,
                        conversation_id=conversation.id,
                        source_message_id=user_message_row.id,
                        candidates=candidates,
                        user_text=message.content,
                        user_nickname=user.nickname or "",
                        user_ai_name=user.ai_name or "",
                        user_ai_emoji=user.ai_emoji or "",
                    ),
                    timeout=MEMORY_UPSERT_TIMEOUT_SEC,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "long-term memory upsert timed out: user_id=%s conversation_id=%s",
                    user_id,
                    conversation.id,
                )
            except Exception:
                logger.exception("long-term memory upsert failed: user_id=%s", user_id)

    return {"ok": True, "responses": responses}
