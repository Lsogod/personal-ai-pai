from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user import User


def _trim_text(text: str, size: int) -> str:
    value = (text or "").strip().replace("\n", " ")
    if len(value) <= size:
        return value
    return value[: size - 1] + "…"


async def ensure_active_conversation(
    session: AsyncSession,
    user: User,
) -> Conversation:
    if user.active_conversation_id:
        existing = await session.get(Conversation, user.active_conversation_id)
        if existing and existing.user_id == user.id:
            return existing

    result = await session.execute(
        select(Conversation)
        .where(Conversation.user_id == user.id)
        .order_by(Conversation.last_message_at.desc(), Conversation.id.desc())
        .limit(1)
    )
    conversation = result.scalar_one_or_none()

    if not conversation:
        conversation = Conversation(user_id=user.id, title="默认会话")
        session.add(conversation)
        await session.flush()
        await session.execute(
            update(Message)
            .where(Message.user_id == user.id, Message.conversation_id.is_(None))
            .values(conversation_id=conversation.id)
        )

    user.active_conversation_id = conversation.id
    session.add(user)
    await session.commit()
    await session.refresh(conversation)
    return conversation


async def create_new_conversation(
    session: AsyncSession,
    user: User,
    title: str | None = None,
) -> Conversation:
    clean_title = _trim_text(title or "新会话", 60) or "新会话"
    conversation = Conversation(
        user_id=user.id,
        title=clean_title,
        summary="",
    )
    session.add(conversation)
    await session.flush()
    user.active_conversation_id = conversation.id
    session.add(user)
    await session.commit()
    await session.refresh(conversation)
    return conversation


async def list_conversations(
    session: AsyncSession,
    user: User,
    limit: int = 20,
) -> list[Conversation]:
    result = await session.execute(
        select(Conversation)
        .where(Conversation.user_id == user.id)
        .order_by(Conversation.last_message_at.desc(), Conversation.id.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def switch_conversation(
    session: AsyncSession,
    user: User,
    conversation_id: int,
) -> Conversation | None:
    conversation = await session.get(Conversation, conversation_id)
    if not conversation or conversation.user_id != user.id:
        return None

    user.active_conversation_id = conversation.id
    session.add(user)
    await session.commit()
    await session.refresh(conversation)
    return conversation


async def rename_conversation(
    session: AsyncSession,
    user: User,
    conversation_id: int,
    title: str,
) -> Conversation | None:
    conversation = await session.get(Conversation, conversation_id)
    if not conversation or conversation.user_id != user.id:
        return None
    clean_title = _trim_text(title or "", 60) or "未命名会话"
    conversation.title = clean_title
    conversation.updated_at = datetime.now(timezone.utc)
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)
    return conversation


async def delete_conversation(
    session: AsyncSession,
    user: User,
    conversation_id: int,
) -> tuple[Conversation | None, str | None]:
    conversation = await session.get(Conversation, conversation_id)
    if not conversation or conversation.user_id != user.id:
        return None, None
    deleted_title = conversation.title

    await session.execute(
        delete(Message).where(
            Message.user_id == user.id,
            Message.conversation_id == conversation.id,
        )
    )
    await session.delete(conversation)
    await session.flush()

    result = await session.execute(
        select(Conversation)
        .where(Conversation.user_id == user.id)
        .order_by(Conversation.last_message_at.desc(), Conversation.id.desc())
        .limit(1)
    )
    replacement = result.scalar_one_or_none()
    if not replacement:
        replacement = Conversation(user_id=user.id, title="默认会话", summary="")
        session.add(replacement)
        await session.flush()
    user.active_conversation_id = replacement.id
    session.add(user)
    await session.commit()
    await session.refresh(replacement)
    return replacement, deleted_title


def apply_user_message_updates(conversation: Conversation, content: str) -> None:
    conversation.last_message_at = datetime.now(timezone.utc)
    if conversation.title in {"新会话", "默认会话"}:
        preview = _trim_text(content, 24)
        if preview and not preview.startswith("/"):
            conversation.title = preview


def apply_assistant_message_updates(conversation: Conversation, content: str) -> None:
    conversation.last_message_at = datetime.now(timezone.utc)
    preview = _trim_text(content, 120)
    if preview:
        conversation.summary = preview
