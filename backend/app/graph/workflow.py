import asyncio

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from app.graph.state import GraphState
from app.graph.nodes.onboarding import onboarding_node
from app.graph.nodes.main_agent import main_agent_node

_graph = None
_graph_lock = asyncio.Lock()
_redis_cm_sync = None
_redis_cm_async = None


def _route_setup(state: GraphState) -> str:
    """Pure logic gate — no LLM call."""
    if state.get("user_setup_stage", 0) < 3:
        return "onboarding"
    return "agent"


async def _entry_node(state: GraphState) -> GraphState:
    """Pass-through entry; only used for the conditional edge."""
    return state


def _build_graph(checkpointer):
    graph = StateGraph(GraphState)

    graph.add_node("entry", _entry_node)
    graph.add_node("onboarding", onboarding_node)
    graph.add_node("agent", main_agent_node)

    graph.set_entry_point("entry")

    graph.add_conditional_edges(
        "entry",
        _route_setup,
        {
            "onboarding": "onboarding",
            "agent": "agent",
        },
    )

    graph.add_edge("onboarding", END)
    graph.add_edge("agent", END)

    return graph.compile(checkpointer=checkpointer)


async def get_graph():
    global _graph, _redis_cm_sync, _redis_cm_async
    if _graph is not None:
        return _graph
    async with _graph_lock:
        if _graph is not None:
            return _graph
        from app.core.config import get_settings

        settings = get_settings()
        checkpointer = MemorySaver()
        try:
            from langgraph.checkpoint.redis import AsyncRedisSaver, RedisSaver  # type: ignore

            if hasattr(AsyncRedisSaver, "from_conn_string"):
                candidate = AsyncRedisSaver.from_conn_string(settings.redis_url)
            elif hasattr(AsyncRedisSaver, "from_conn_info"):
                candidate = AsyncRedisSaver.from_conn_info(settings.redis_url)
            elif hasattr(RedisSaver, "from_conn_string"):
                candidate = RedisSaver.from_conn_string(settings.redis_url)
            else:
                candidate = RedisSaver.from_conn_info(settings.redis_url)

            # langgraph-checkpoint-redis has version differences:
            # some return saver directly, some return context managers.
            if hasattr(candidate, "__aenter__"):
                checkpointer = await candidate.__aenter__()
                _redis_cm_async = candidate
            elif hasattr(candidate, "__enter__") and not hasattr(candidate, "asetup"):
                checkpointer = candidate.__enter__()
                _redis_cm_sync = candidate
            else:
                checkpointer = candidate

            if hasattr(checkpointer, "asetup"):
                await checkpointer.asetup()
            elif hasattr(checkpointer, "setup"):
                checkpointer.setup()
        except Exception as exc:
            _redis_cm_sync = None
            _redis_cm_async = None
            if settings.allow_memory_checkpointer_fallback:
                checkpointer = MemorySaver()
            else:
                raise RuntimeError("Redis checkpointer unavailable") from exc
        _graph = _build_graph(checkpointer)
        return _graph


async def close_graph() -> None:
    global _graph, _redis_cm_sync, _redis_cm_async
    _graph = None
    if _redis_cm_async is not None:
        try:
            await _redis_cm_async.__aexit__(None, None, None)
        finally:
            _redis_cm_async = None
    if _redis_cm_sync is not None:
        try:
            _redis_cm_sync.__exit__(None, None, None)
        finally:
            _redis_cm_sync = None
