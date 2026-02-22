from __future__ import annotations

from typing import TYPE_CHECKING, cast

from app.graph.state import GraphRuntime, GraphState
from app.services.runtime_context import (
    get_scheduler as get_ctx_scheduler,
    get_sender as get_ctx_sender,
    get_session as get_ctx_session,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.services.scheduler import SchedulerService
    from app.services.sender import UnifiedSender


def _runtime_from_state(state: GraphState) -> GraphRuntime:
    runtime = state.get("runtime")
    if isinstance(runtime, dict):
        return cast(GraphRuntime, runtime)
    return {}


def get_runtime_session(state: GraphState) -> "AsyncSession":
    runtime = _runtime_from_state(state)
    session = runtime.get("session")
    if session is not None:
        return cast("AsyncSession", session)
    return get_ctx_session()


def get_runtime_scheduler(state: GraphState) -> "SchedulerService":
    runtime = _runtime_from_state(state)
    scheduler = runtime.get("scheduler")
    if scheduler is not None:
        return cast("SchedulerService", scheduler)
    return get_ctx_scheduler()


def get_runtime_sender(state: GraphState) -> "UnifiedSender":
    runtime = _runtime_from_state(state)
    sender = runtime.get("sender")
    if sender is not None:
        return cast("UnifiedSender", sender)
    return get_ctx_sender()

