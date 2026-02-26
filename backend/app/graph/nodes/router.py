from __future__ import annotations

import asyncio
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.graph.context import render_conversation_context
from app.graph.state import GraphState
from app.services.commands.router import route_command_intent
from app.services.ledger_pending import has_pending_ledger
from app.services.llm import get_llm
from app.services.skills import parse_skill_intent
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

SKILL_MANAGEMENT_ACTIONS = {"create", "update", "publish", "disable", "delete", "list", "show"}
SKILL_MANAGEMENT_TEXT_PATTERN = re.compile(
    r"(/skill\b|技能\s*(列表|清单|详情|信息)|"
    r"(创建|新建|更新|修改|发布|停用|禁用|删除|查看|展示|列出)\s*.*技能|"
    r"skill\s*(list|show|create|update|publish|disable|delete))",
    re.IGNORECASE,
)
SKILL_FOLLOWUP_UPDATE_PATTERN = re.compile(
    r"(改为|改成|修改|调整|限制|收紧|放宽|change|update|revise)",
    re.IGNORECASE,
)


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
            "你是智能体图路由分类器。请仅返回 schema 定义的结构化字段。\n"
            "允许的 intent: complex_task, skill_manager, ledger_manager, schedule_manager, "
            "chat_manager, help_center, unknown。\n"
            "输出字段: intent, use_complex, confidence, reason。\n"
            "路由规则：\n"
            "1) complex_task: 多目标工作流，且涉及跨节点编排。\n"
            "2) skill_manager: 仅技能管理 CRUD/list/show/publish/disable/delete。\n"
            "3) ledger_manager: 账单新增/修改/删除/查询。\n"
            "4) schedule_manager: 提醒/日历新增/修改/删除/查询。\n"
            "5) help_center: 帮助、手册、能力说明。\n"
            "6) chat_manager: 通用问答、写作、翻译、外部查询、天气问答。\n"
            "7) unknown: 仅在证据不足时使用。\n"
            "当用户要求“使用某个已有技能生成内容”（如“使用xx技能写文案/写诗/翻译”）时，路由到 chat_manager，不是 skill_manager。\n"
            "身份类问题（如“你是谁/我是谁/我叫什么/你叫什么”）应路由到 chat_manager，不是 help_center。\n"
            "仅当请求确实需要两个及以上领域节点时才设置 use_complex=true "
            "（例如 ledger_manager + schedule_manager，或 skill_manager + chat_manager）。\n"
            "若一个节点即可完整处理（如 schedule_manager 内部条件工具判断），应保持 use_complex=false。\n"
            "以用户消息为主要证据，会话上下文与运行时工具目录仅作辅助。\n"
            "若 has_pending_ledger=true，除非用户明确请求其它域，否则优先 ledger_manager。"
        )
    )
    human = HumanMessage(
        content=(
            f"has_image={str(has_image).lower()}\n"
            f"has_pending_ledger={str(has_pending_ledger).lower()}\n"
            f"runtime_tools={runtime_tools}\n\n"
            f"会话上下文:\n{conversation_context}\n\n"
            f"用户消息:\n{content}"
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


async def _should_keep_skill_manager_route(*, content: str, conversation_context: str) -> bool:
    # Keep natural-language skill operations in skill_manager only when the parsed action
    # is an actual management action; otherwise let chat_manager consume published skills.
    text = (content or "").strip()
    if not text:
        return False
    if text.lower().startswith("/skill"):
        return True
    try:
        parsed = await parse_skill_intent(content, conversation_context=conversation_context)
    except Exception:
        return False
    action = str(parsed.get("action") or "").strip().lower()
    if action in SKILL_MANAGEMENT_ACTIONS:
        return True
    if action == "help":
        ctx = str(conversation_context or "")
        if SKILL_FOLLOWUP_UPDATE_PATTERN.search(text) and (
            "技能草稿" in ctx or "/skill publish" in ctx or "skill-" in ctx
        ):
            return True
    return False


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
            "你是复杂任务资格检查器。请仅返回 schema 定义的结构化字段。\n"
            "字段: use_complex, required_nodes, reason。\n"
            "required_nodes 允许值: ledger_manager, schedule_manager, chat_manager, skill_manager, help_center。\n"
            "仅当请求确实需要至少 2 个不同领域节点时，use_complex=true。\n"
            "如果单节点即可端到端完成（即便包含内部工具/条件判断），use_complex=false。\n"
            "required_nodes 需给出完成任务所需的最小节点集合。"
        )
    )
    human = HumanMessage(
        content=(
            f"primary_intent={primary_intent}\n\n"
            f"用户消息:\n{content}\n\n"
            f"会话上下文:\n{conversation_context}"
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
            "你是复杂路由校验器。请仅返回 schema 定义的结构化字段。\n"
            "判断该请求是否必须进入 complex_task。\n"
            "满足任一条件时 use_complex=true：\n"
            "1) 多目标且有顺序/依赖关系，并涉及至少 2 个领域节点；\n"
            "2) 存在跨域编排，一个节点结果驱动另一个节点；\n"
            "3) 必须使用多节点计划，单节点无法完成。\n"
            "若属于单节点内部条件执行，应保持原主路由。\n"
            "若只是单次简单 CRUD/查询，应保持原主路由。\n"
            "采取保守策略：除非有明确多节点编排需求，否则 use_complex=false。"
        )
    )
    human = HumanMessage(
        content=(
            f"primary_intent={primary_intent}\n\n"
            f"用户消息:\n{content}\n\n"
            f"会话上下文:\n{conversation_context}"
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

    if intent == "skill_manager" and not content.lower().startswith("/skill"):
        try:
            keep_skill_mgr = await _should_keep_skill_manager_route(
                content=content,
                conversation_context=context_text,
            )
            if not keep_skill_mgr:
                intent = "chat_manager"
        except Exception:
            intent = "chat_manager"

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
