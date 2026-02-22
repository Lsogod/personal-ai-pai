from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_or_create_user
from app.core.config import get_settings
from app.schemas.unified import UnifiedMessage
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
    create_new_conversation,
    delete_conversation,
    ensure_active_conversation,
    list_conversations,
    rename_conversation,
    switch_conversation,
)
from app.services.binding import consume_bind_code, create_bind_code
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


def _format_history_lines(current_id: int | None, rows: list) -> str:
    if not rows:
        return "暂无历史会话。发送 /new 创建新会话。"
    lines = ["历史会话："]
    for item in rows:
        marker = "*" if current_id and item.id == current_id else " "
        time_str = item.last_message_at.strftime("%m-%d %H:%M")
        summary = (item.summary or "（暂无摘要）").strip()
        lines.append(f"{marker} #{item.id} | {item.title} | {time_str} | {summary}")
    return "\n".join(lines)


def _to_client_tz_iso(value: datetime | None) -> str:
    if value is None:
        return ""
    tz = ZoneInfo(settings.timezone)
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo("UTC"))
    return value.astimezone(tz).isoformat(timespec="seconds")


def _parse_json_object(content: str) -> dict:
    text = (content or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


async def _is_rebind_natural_intent(text: str) -> bool:
    content = (text or "").strip()
    if not content or content.startswith("/"):
        return False
    llm = get_llm(node_name="message_handler")
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
        response = await llm.ainvoke([system, human])
        data = _parse_json_object(str(response.content))
        return bool(data.get("block") is True)
    except Exception:
        return False


async def _handle_conversation_command(
    session: AsyncSession,
    user,
    conversation,
    text: str,
) -> tuple[list[str], object] | None:
    content = (text or "").strip()
    if not content.startswith("/"):
        return None

    parts = content.split(maxsplit=1)
    command = parts[0].lower()
    argument = parts[1].strip() if len(parts) > 1 else ""

    if command == "/bind":
        arg = argument.strip()
        if not arg or arg.lower() == "help":
            return (
                ["绑定命令：`/bind new` 生成绑定码；`/bind <6位码>` 绑定到已有账号。"],
                conversation,
            )
        if arg.lower() == "new":
            code = await create_bind_code(session, owner_user_id=user.id, ttl_minutes=10)
            return (
                [f"你的绑定码是：`{code}`（10分钟内有效）。请在另一个客户端发送：`/bind {code}`。"],
                conversation,
            )
        code = arg.strip()
        if not code.isdigit() or len(code) != 6:
            return (
                ["绑定码格式错误。请发送：`/bind <6位数字>`。"],
                conversation,
            )
        ok, msg, canonical_user_id = await consume_bind_code(
            session,
            code=code,
            current_user_id=user.id,
        )
        if not ok:
            return ([msg], conversation)
        if canonical_user_id:
            canonical = await session.get(User, canonical_user_id)
            if canonical:
                canonical.binding_stage = 2
                session.add(canonical)
                await session.commit()
        return ([msg], conversation)

    if command == "/new":
        title = argument if argument else "新会话"
        new_conversation = await create_new_conversation(session, user, title=title)
        return (
            [f"已创建并切换到新会话 #{new_conversation.id}（{new_conversation.title}）。"],
            new_conversation,
        )

    if command == "/history":
        rows = await list_conversations(session, user, limit=20)
        return ([_format_history_lines(user.active_conversation_id, rows)], conversation)

    if command == "/switch":
        target = argument.lstrip("#")
        if not target.isdigit():
            return (["用法：`/switch <会话ID>`，例如 `/switch 3`。"], conversation)
        switched = await switch_conversation(session, user, int(target))
        if not switched:
            return ([f"未找到会话 #{target}，或该会话不属于你。"], conversation)
        return ([f"已切换到会话 #{switched.id}（{switched.title}）。"], switched)

    if command == "/rename":
        if not argument:
            return (["用法：`/rename <新标题>` 或 `/rename <会话ID> <新标题>`。"], conversation)
        parts = argument.split(maxsplit=1)
        target_conversation_id = conversation.id
        new_title = argument
        first = parts[0].lstrip("#")
        if first.isdigit() and len(parts) > 1:
            target_conversation_id = int(first)
            new_title = parts[1]
        renamed = await rename_conversation(
            session=session,
            user=user,
            conversation_id=target_conversation_id,
            title=new_title,
        )
        if not renamed:
            return ([f"未找到会话 #{target_conversation_id}，或该会话不属于你。"], conversation)
        if target_conversation_id == conversation.id:
            conversation = renamed
        return ([f"已将会话 #{renamed.id} 重命名为「{renamed.title}」。"], conversation)

    if command == "/delete":
        target = argument.lstrip("#") if argument else str(conversation.id)
        if not target.isdigit():
            return (["用法：`/delete`（删除当前会话）或 `/delete <会话ID>`。"], conversation)
        replacement, deleted_title = await delete_conversation(
            session=session,
            user=user,
            conversation_id=int(target),
        )
        if not replacement or not deleted_title:
            return ([f"未找到会话 #{target}，或该会话不属于你。"], conversation)
        return (
            [f"已删除会话 #{target}（{deleted_title}），当前会话已切换到 #{replacement.id}（{replacement.title}）。"],
            replacement,
        )

    return None


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
        command_result = await _handle_conversation_command(
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
            session_token = set_session(session)
            scheduler_token = set_scheduler(_scheduler)
            tool_user_token = set_tool_user_id(user_id)
            tool_platform_token = set_tool_platform(platform)
            tool_conv_token = set_tool_conversation_id(conversation.id)
            try:
                try:
                    result = await graph.ainvoke(
                        state,
                        config={"configurable": {"thread_id": f"{user_uuid}:{conversation.id}"}},
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
        candidates = await extract_memory_candidates(
            user_text=message.content,
            assistant_text="\n".join(assistant_outputs),
            conversation_summary=(conversation.summary or "").strip(),
        )
        try:
            await upsert_long_term_memories(
                session=session,
                user_id=user_id,
                conversation_id=conversation.id,
                source_message_id=user_message_row.id,
                candidates=candidates,
                user_text=message.content,
                user_nickname=user.nickname or "",
                user_ai_name=user.ai_name or "",
                user_ai_emoji=user.ai_emoji or "",
            )
        except Exception:
            logger.exception("long-term memory upsert failed: user_id=%s", user_id)

    return {"ok": True, "responses": responses}
