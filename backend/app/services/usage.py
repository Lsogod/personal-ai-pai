from __future__ import annotations

import asyncio

from app.db.session import AsyncSessionLocal
from app.models.llm_usage import LLMUsageLog
from app.models.tool_usage import ToolUsageLog

_usage_tasks: set[asyncio.Task[None]] = set()


def _track_usage_task(task: asyncio.Task[None]) -> None:
    _usage_tasks.add(task)

    def _on_done(done: asyncio.Task[None]) -> None:
        _usage_tasks.discard(done)
        try:
            done.result()
        except asyncio.CancelledError:
            return
        except Exception:
            return

    task.add_done_callback(_on_done)


async def log_llm_usage(
    *,
    user_id: int | None,
    platform: str,
    conversation_id: int | None,
    node: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    latency_ms: int,
    success: bool,
    error: str = "",
) -> None:
    async with AsyncSessionLocal() as session:
        row = LLMUsageLog(
            user_id=user_id,
            platform=(platform or "").strip(),
            conversation_id=conversation_id,
            node=(node or "unknown").strip().lower() or "unknown",
            model=(model or "").strip(),
            prompt_tokens=max(0, int(prompt_tokens)),
            completion_tokens=max(0, int(completion_tokens)),
            total_tokens=max(0, int(total_tokens)),
            latency_ms=max(0, int(latency_ms)),
            success=bool(success),
            error=(error or "").strip()[:2000] or None,
        )
        session.add(row)
        await session.commit()


def enqueue_llm_usage(**kwargs) -> None:
    _track_usage_task(asyncio.create_task(log_llm_usage(**kwargs)))


async def log_tool_usage(
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
    async with AsyncSessionLocal() as session:
        row = ToolUsageLog(
            user_id=user_id,
            platform=(platform or "").strip(),
            conversation_id=conversation_id,
            tool_source=(tool_source or "builtin").strip().lower() or "builtin",
            tool_name=(tool_name or "").strip()[:120],
            success=bool(success),
            latency_ms=max(0, int(latency_ms)),
            error=(error or "").strip()[:2000] or None,
        )
        session.add(row)
        await session.commit()


def enqueue_tool_usage(**kwargs) -> None:
    _track_usage_task(asyncio.create_task(log_tool_usage(**kwargs)))
