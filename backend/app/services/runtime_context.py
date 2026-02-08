from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.scheduler import SchedulerService
from app.services.sender import UnifiedSender


_session_ctx: ContextVar[Optional[AsyncSession]] = ContextVar("pai_session_ctx", default=None)
_scheduler_ctx: ContextVar[Optional[SchedulerService]] = ContextVar("pai_scheduler_ctx", default=None)
_sender_ctx: ContextVar[Optional[UnifiedSender]] = ContextVar("pai_sender_ctx", default=None)
_tool_user_id_ctx: ContextVar[Optional[int]] = ContextVar("pai_tool_user_id_ctx", default=None)
_tool_platform_ctx: ContextVar[Optional[str]] = ContextVar("pai_tool_platform_ctx", default=None)
_tool_conversation_id_ctx: ContextVar[Optional[int]] = ContextVar("pai_tool_conversation_id_ctx", default=None)


def set_session(session: AsyncSession):
    return _session_ctx.set(session)


def reset_session(token) -> None:
    _session_ctx.reset(token)


def get_session() -> AsyncSession:
    session = _session_ctx.get()
    if session is None:
        raise RuntimeError("session context not set")
    return session


def set_scheduler(scheduler: SchedulerService):
    return _scheduler_ctx.set(scheduler)


def reset_scheduler(token) -> None:
    _scheduler_ctx.reset(token)


def get_scheduler() -> SchedulerService:
    scheduler = _scheduler_ctx.get()
    if scheduler is None:
        raise RuntimeError("scheduler context not set")
    return scheduler


def set_sender(sender: UnifiedSender):
    return _sender_ctx.set(sender)


def reset_sender(token) -> None:
    _sender_ctx.reset(token)


def get_sender() -> UnifiedSender:
    sender = _sender_ctx.get()
    if sender is None:
        raise RuntimeError("sender context not set")
    return sender


def set_tool_user_id(user_id: int | None):
    return _tool_user_id_ctx.set(user_id)


def reset_tool_user_id(token) -> None:
    _tool_user_id_ctx.reset(token)


def get_tool_user_id() -> int | None:
    return _tool_user_id_ctx.get()


def set_tool_platform(platform: str | None):
    return _tool_platform_ctx.set(platform)


def reset_tool_platform(token) -> None:
    _tool_platform_ctx.reset(token)


def get_tool_platform() -> str | None:
    return _tool_platform_ctx.get()


def set_tool_conversation_id(conversation_id: int | None):
    return _tool_conversation_id_ctx.set(conversation_id)


def reset_tool_conversation_id(token) -> None:
    _tool_conversation_id_ctx.reset(token)


def get_tool_conversation_id() -> int | None:
    return _tool_conversation_id_ctx.get()
