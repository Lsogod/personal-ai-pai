from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_or_create_user
from app.schemas.unified import UnifiedMessage
from app.services.llm import get_llm
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
    set_sender,
    reset_sender,
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
UNSUPPORTED_REBIND_TEXT = "当前版本暂不支持换绑/解绑。你仍可使用 `/bind new` 与 `/bind <6位码>` 进行账号绑定合并。"


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
    llm = get_llm()
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
                "created_at": row.created_at.isoformat() if row.created_at else "",
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
    session.add(
        Message(
            user_id=user_id,
            conversation_id=conversation.id,
            role="user",
            content=message.content,
            platform=platform,
        )
    )
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
                "created_at": datetime.utcnow().isoformat(),
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
            state["extra"] = {
                "conversation_summary": (conversation.summary or "").strip(),
                "context_messages": await _load_context_messages(
                    session=session,
                    user_id=user_id,
                    conversation_id=conversation.id,
                    limit=20,
                ),
            }
            graph = await get_graph()
            session_token = set_session(session)
            scheduler_token = set_scheduler(_scheduler)
            sender_token = set_sender(_sender)
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
                reset_sender(sender_token)
                reset_scheduler(scheduler_token)
                reset_session(session_token)

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
            conversation.last_message_at = datetime.utcnow()
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
        if platform != "web":
            await get_notification_hub().send_to_user(
                user_id,
                {
                    "type": "message",
                    "role": "assistant",
                    "content": text,
                    "platform": platform,
                    "conversation_id": conversation.id,
                    "created_at": datetime.utcnow().isoformat(),
                },
            )

    return {"ok": True, "responses": responses}
