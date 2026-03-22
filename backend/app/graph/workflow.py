import asyncio

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import InMemorySaver

from app.graph.state import GraphState
from app.graph.nodes.router import route_intent, router_node
from app.graph.nodes.onboarding import onboarding_node
from app.graph.nodes.complex_task import complex_task_node
from app.graph.nodes.ledger_manager import ledger_manager_node
from app.graph.nodes.schedule_manager import schedule_manager_node
from app.graph.nodes.chat_manager import chat_manager_node
from app.graph.nodes.skill_manager import skill_manager_node
from app.graph.nodes.help_center import help_center_node

_graph = None
_graph_lock = asyncio.Lock()
_redis_cm_sync = None
_redis_cm_async = None


def _route_after_ledger(state: GraphState) -> str:
    routed = str(state.get("intent") or "").strip().lower()
    if routed == "chat_manager":
        return "chat_manager"
    return "end"


def _build_graph(checkpointer):
    graph = StateGraph(GraphState)

    graph.add_node("router", router_node)
    graph.add_node("onboarding", onboarding_node)
    graph.add_node("complex_task", complex_task_node)
    graph.add_node("ledger_manager", ledger_manager_node)
    graph.add_node("schedule_manager", schedule_manager_node)
    graph.add_node("chat_manager", chat_manager_node)
    graph.add_node("skill_manager", skill_manager_node)
    graph.add_node("help_center", help_center_node)

    graph.set_entry_point("router")

    graph.add_conditional_edges(
        "router",
        route_intent,
        {
            "onboarding": "onboarding",
            "complex_task": "complex_task",
            "ledger_manager": "ledger_manager",
            "schedule_manager": "schedule_manager",
            "chat_manager": "chat_manager",
            "skill_manager": "skill_manager",
            "help_center": "help_center",
        },
    )

    graph.add_edge("onboarding", END)
    graph.add_edge("complex_task", END)
    graph.add_conditional_edges(
        "ledger_manager",
        _route_after_ledger,
        {
            "chat_manager": "chat_manager",
            "end": END,
        },
    )
    graph.add_edge("schedule_manager", END)
    graph.add_edge("chat_manager", END)
    graph.add_edge("skill_manager", END)
    graph.add_edge("help_center", END)

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
        checkpointer = InMemorySaver()
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
                checkpointer = InMemorySaver()
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
