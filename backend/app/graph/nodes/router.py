from __future__ import annotations

import asyncio

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.graph.context import render_conversation_context
from app.graph.state import GraphState
from app.services.commands.router import route_command_intent
from app.services.ledger_pending import has_pending_ledger
from app.services.llm import get_llm
from app.services.tool_registry import list_runtime_tool_metas


VALID_INTENTS = {
    "complex_task",
    "skill_manager",
    "ledger_manager",
    "schedule_manager",
    "chat_manager",
    "help_center",
    "unknown",
}


class RouterIntentExtraction(BaseModel):
    intent: str = Field(default="unknown")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = Field(default="")


class ComplexRouteDecision(BaseModel):
    use_complex: bool = Field(default=False)
    reason: str = Field(default="")


async def _classify_intent_with_llm(
    *,
    content: str,
    has_image: bool,
    has_pending_ledger: bool,
    conversation_context: str,
    runtime_tools: str,
) -> str:
    llm = get_llm(node_name="router")
    runnable = llm.with_structured_output(RouterIntentExtraction)
    system = SystemMessage(
        content=(
            "You are a routing classifier for an agent graph. Output structured JSON only.\n"
            "Allowed intents: complex_task, skill_manager, ledger_manager, schedule_manager, chat_manager, help_center, unknown.\n"
            "Routing rules:\n"
            "1) complex_task: multi-goal, cross-domain workflow, dependency ordering, or conditional execution.\n"
            "   Examples: '先查天气再给建议最后设提醒', '如果明天下雨就提醒我带伞'.\n"
            "2) skill_manager: skill CRUD/list/show/publish/disable.\n"
            "3) ledger_manager: ledger create/update/delete/query, receipt/bill accounting.\n"
            "4) schedule_manager: reminder/calendar CRUD and schedule query.\n"
            "5) help_center: usage docs/help/command manual/capabilities intro.\n"
            "6) chat_manager: general Q&A, writing, translation, external lookup, weather query only.\n"
            "7) unknown only when evidence is insufficient.\n"
            "Use user_message as primary evidence. conversation_context and runtime_tools are auxiliary only.\n"
            "If has_pending_ledger=true, prefer ledger_manager unless user clearly requests another domain."
        )
    )
    human = HumanMessage(
        content=(
            f"has_image={str(has_image).lower()}\n"
            f"has_pending_ledger={str(has_pending_ledger).lower()}\n"
            f"runtime_tools={runtime_tools}\n\n"
            f"conversation_context:\n{conversation_context}\n\n"
            f"user_message:\n{content}"
        )
    )
    result = await asyncio.wait_for(runnable.ainvoke([system, human]), timeout=25)
    intent = str(getattr(result, "intent", "") or "unknown").strip().lower()
    if intent in VALID_INTENTS:
        return intent
    return "unknown"


async def _should_route_complex_with_llm(
    *,
    content: str,
    conversation_context: str,
    primary_intent: str,
) -> bool:
    llm = get_llm(node_name="router")
    runnable = llm.with_structured_output(ComplexRouteDecision)
    system = SystemMessage(
        content=(
            "You are a complex-routing verifier. Output structured JSON only.\n"
            "Decide whether this user request MUST go to complex_task.\n"
            "use_complex=true when at least one is true:\n"
            "1) multi-goal workflow with ordering/dependencies,\n"
            "2) conditional execution based on tool evidence,\n"
            "3) cross-domain orchestration where one step result drives another.\n"
            "Examples that SHOULD be complex_task:\n"
            "- 先查天气，再给建议，最后设提醒\n"
            "- 如果明天下午下雨，就提醒我带伞\n"
            "Simple single-shot CRUD/query should keep primary intent.\n"
            "Be conservative: choose false unless clear complex orchestration is required."
        )
    )
    human = HumanMessage(
        content=(
            f"primary_intent={primary_intent}\n\n"
            f"user_message:\n{content}\n\n"
            f"conversation_context:\n{conversation_context}"
        )
    )
    result = await asyncio.wait_for(runnable.ainvoke([system, human]), timeout=25)
    return bool(getattr(result, "use_complex", False))


async def router_node(state: GraphState) -> GraphState:
    if state.get("user_setup_stage", 0) < 3:
        return state

    user_id = int(state.get("user_id") or 0)
    conversation_id = int(state.get("conversation_id") or 0)
    message = state["message"]
    content = (message.content or "").strip()
    if not content and not message.image_urls:
        return {**state, "intent": "chat_manager"}

    has_pending = False
    if user_id > 0 and conversation_id > 0:
        has_pending = await has_pending_ledger(user_id, conversation_id)
    context_text = render_conversation_context(state)

    try:
        runtime_tools = ", ".join(
            [
                f"{str(item.get('source') or '')}:{str(item.get('name') or '')}"
                for item in await list_runtime_tool_metas()
            ]
        )
    except Exception:
        runtime_tools = ""

    try:
        intent = await _classify_intent_with_llm(
            content=content,
            has_image=bool(message.image_urls),
            has_pending_ledger=has_pending,
            conversation_context=context_text,
            runtime_tools=runtime_tools,
        )
    except Exception:
        intent = "unknown"

    if intent in {"ledger_manager", "schedule_manager", "chat_manager", "help_center", "unknown"}:
        try:
            use_complex = await _should_route_complex_with_llm(
                content=content,
                conversation_context=context_text,
                primary_intent=intent,
            )
            if use_complex:
                return {**state, "intent": "complex_task"}
        except Exception:
            pass

    return {**state, "intent": intent}


def route_intent(state: GraphState) -> str:
    message = state["message"]

    if state.get("user_setup_stage", 0) < 3:
        return "onboarding"

    routed = str(state.get("intent") or "").strip().lower()
    if routed in {
        "complex_task",
        "skill_manager",
        "ledger_manager",
        "schedule_manager",
        "chat_manager",
        "help_center",
    }:
        return routed

    command_route = route_command_intent(
        content=(message.content or ""),
        has_images=bool(message.image_urls),
    )
    if command_route:
        return command_route

    return "chat_manager"
