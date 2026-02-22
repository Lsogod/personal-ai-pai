from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.services.binding import consume_bind_code, create_bind_code
from app.services.conversations import (
    create_new_conversation,
    delete_conversation,
    list_conversations,
    rename_conversation,
    switch_conversation,
)


def _format_history_lines(current_id: int | None, rows: list[Any]) -> str:
    if not rows:
        return "暂无历史会话。发送 /new 创建新会话。"
    lines = ["历史会话："]
    for item in rows:
        marker = "*" if current_id and item.id == current_id else " "
        time_str = item.last_message_at.strftime("%m-%d %H:%M")
        summary = (item.summary or "（暂无摘要）").strip()
        lines.append(f"{marker} #{item.id} | {item.title} | {time_str} | {summary}")
    return "\n".join(lines)


async def handle_conversation_command(
    session: AsyncSession,
    user: User,
    conversation: Any,
    text: str,
) -> tuple[list[str], Any] | None:
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
        arg_parts = argument.split(maxsplit=1)
        target_conversation_id = conversation.id
        new_title = argument
        first = arg_parts[0].lstrip("#")
        if first.isdigit() and len(arg_parts) > 1:
            target_conversation_id = int(first)
            new_title = arg_parts[1]
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

