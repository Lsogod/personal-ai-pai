from __future__ import annotations

import asyncio
from typing import Any

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
    use_complex: bool = Field(default=False)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = Field(default="")


class ComplexRouteDecision(BaseModel):
    use_complex: bool = Field(default=False)
    reason: str = Field(default="")


class ComplexEligibilityExtraction(BaseModel):
    use_complex: bool = Field(default=False)
    required_nodes: list[str] = Field(default_factory=list)
    reason: str = Field(default="")


async def _classify_intent_with_llm(
    *,
    content: str,
    has_image: bool,
    has_pending_ledger: bool,
    conversation_context: str,
    runtime_tools: str,
) -> dict[str, Any]:
    llm = get_llm(node_name="router")
    runnable = llm.with_structured_output(RouterIntentExtraction)
    system = SystemMessage(
        content=(
            "You are a routing classifier for an agent graph. Output structured JSON only.\n"
            "Allowed intents: complex_task, skill_manager, ledger_manager, schedule_manager, "
            "chat_manager, help_center, unknown.\n"
            "Output fields: intent, use_complex, confidence, reason.\n"
            "Routing rules:\n"
            "1) complex_task: multi-goal workflow with cross-node orchestration.\n"
            "2) skill_manager: skill CRUD/list/show/publish/disable.\n"
            "3) ledger_manager: ledger create/update/delete/query.\n"
            "4) schedule_manager: reminder/calendar create/update/delete/query.\n"
            "5) help_center: help/manual/capabilities introduction.\n"
            "6) chat_manager: general Q&A/writing/translation/external lookup/weather query only.\n"
            "7) unknown only when evidence is insufficient.\n"
            "Identity questions such as '你是谁/我是谁/我叫什么/你叫什么' should route to chat_manager, not help_center.\n"
            "Set use_complex=true only when the request requires two or more domain nodes "
            "(for example ledger_manager + schedule_manager, or skill_manager + chat_manager).\n"
            "A conditional request that can be fully handled inside a single node "
            "(e.g. schedule_manager with internal tool checks) should keep use_complex=false.\n"
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
    if intent not in VALID_INTENTS:
        intent = "unknown"
    use_complex = bool(getattr(result, "use_complex", False))
    try:
        confidence = float(getattr(result, "confidence", 0.0) or 0.0)
    except Exception:
        confidence = 0.0
    return {
        "intent": intent,
        "use_complex": use_complex,
        "confidence": confidence,
    }


async def _verify_complex_eligibility_with_llm(
    *,
    content: str,
    conversation_context: str,
    primary_intent: str,
) -> ComplexEligibilityExtraction:
    llm = get_llm(node_name="router")
    runnable = llm.with_structured_output(ComplexEligibilityExtraction)
    system = SystemMessage(
        content=(
            "You are a complex-task eligibility checker. Output structured JSON only.\n"
            "Fields: use_complex, required_nodes, reason.\n"
            "Domain nodes allowed in required_nodes: "
            "ledger_manager, schedule_manager, chat_manager, skill_manager, help_center.\n"
            "Set use_complex=true only if the request requires >=2 distinct domain nodes.\n"
            "If one node can complete the task end-to-end (even with internal tools/conditions), use_complex=false.\n"
            "required_nodes should contain the minimal node set needed."
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
    normalized_nodes: list[str] = []
    for item in list(getattr(result, "required_nodes", []) or []):
        node = str(item or "").strip().lower()
        if node in {"ledger_manager", "schedule_manager", "chat_manager", "skill_manager", "help_center"}:
            if node not in normalized_nodes:
                normalized_nodes.append(node)
    use_complex = bool(getattr(result, "use_complex", False))
    if len(normalized_nodes) < 2:
        use_complex = False
    return ComplexEligibilityExtraction(
        use_complex=use_complex,
        required_nodes=normalized_nodes,
        reason=str(getattr(result, "reason", "") or ""),
    )


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
            "1) multi-goal workflow with ordering/dependencies and >=2 domain nodes,\n"
            "2) cross-domain orchestration where one node result drives another node,\n"
            "3) true multi-node plan is required (not solvable within one node).\n"
            "Single-node conditional execution based on internal tools should keep primary intent.\n"
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

    runtime_tool_rows: list[dict[str, Any]] = []
    try:
        runtime_tool_rows = [dict(item) for item in await list_runtime_tool_metas()]
        runtime_tools = ", ".join(
            [
                f"{str(item.get('source') or '')}:{str(item.get('name') or '')}"
                for item in runtime_tool_rows
            ]
        )
    except Exception:
        runtime_tools = ""

    try:
        routed = await _classify_intent_with_llm(
            content=content,
            has_image=bool(message.image_urls),
            has_pending_ledger=has_pending,
            conversation_context=context_text,
            runtime_tools=runtime_tools,
        )
        intent = str(routed.get("intent") or "unknown")
        use_complex = bool(routed.get("use_complex") is True)
    except Exception:
        intent = "unknown"
        use_complex = False

    if use_complex:
        try:
            eligibility = await _verify_complex_eligibility_with_llm(
                content=content,
                conversation_context=context_text,
                primary_intent=intent,
            )
            if bool(eligibility.use_complex):
                extra = dict(state.get("extra") or {})
                if runtime_tool_rows:
                    extra["tool_catalog"] = runtime_tool_rows
                return {**state, "intent": "complex_task", "extra": extra}
        except Exception:
            pass

    # Safety net for uncertain/general-chat requests.
    if intent in {"chat_manager", "unknown"}:
        try:
            use_complex = await _should_route_complex_with_llm(
                content=content,
                conversation_context=context_text,
                primary_intent=intent,
            )
            if use_complex:
                extra = dict(state.get("extra") or {})
                if runtime_tool_rows:
                    extra["tool_catalog"] = runtime_tool_rows
                return {**state, "intent": "complex_task", "extra": extra}
        except Exception:
            pass
    extra = dict(state.get("extra") or {})
    if runtime_tool_rows:
        extra["tool_catalog"] = runtime_tool_rows
    return {**state, "intent": intent, "extra": extra}


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
