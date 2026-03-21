from __future__ import annotations

import asyncio
import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.graph.context import render_conversation_context
from app.graph.state import GraphState
from app.core.config import get_settings
from app.services.ledger_pending import has_pending_ledger
from app.services.llm import get_llm


VALID_INTENTS = {
    "complex_task",
    "skill_manager",
    "ledger_manager",
    "schedule_manager",
    "chat_manager",
    "help_center",
    "unknown",
}

BOOKKEEPING_IMAGE_HINTS = (
    "记账",
    "入账",
    "账单",
    "小票",
    "发票",
    "支付截图",
    "付款截图",
    "消费截图",
    "金额",
    "花了",
    "支出",
    "收入",
    "报销",
)


class RouterIntentExtraction(BaseModel):
    route_intent: str = Field(default="unknown")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = Field(default="")


def _is_bookkeeping_image_request(content: str) -> bool:
    text = str(content or "").strip().lower()
    if not text:
        return False
    return any(token in text for token in BOOKKEEPING_IMAGE_HINTS)


async def _route_intent_with_llm(
    *,
    content: str,
    has_image: bool,
    has_pending_ledger: bool,
    conversation_context: str,
    pending_complex: dict[str, Any] | None = None,
) -> dict[str, Any]:
    llm = get_llm(node_name="router")
    runnable = llm.with_structured_output(RouterIntentExtraction)
    system = SystemMessage(
        content=(
            "你是主路由分类器。只做一次路由判断，并仅返回一个 JSON 对象（schema 字段）。\n"
            "允许的 route_intent: complex_task, skill_manager, ledger_manager, "
            "schedule_manager, chat_manager, help_center, unknown。\n"
            "路由原则：\n"
            "1) complex_task: 需要跨节点/跨工具编排与任务拆解时使用。\n"
            "   例如：先判断天气是否满足条件，再决定是否创建提醒。\n"
            "   仅陈述事实/偏好（如“我在武汉”“我喜欢爬山”）不属于 complex_task。\n"
            "   若存在未完成复杂任务，且用户消息是该任务的参数补充/继续执行，继续路由 complex_task。\n"
            "   若用户明确发起无关新任务，必须路由到新任务对应节点，不要被未完成任务绑死。\n"
            "2) skill_manager: 仅技能管理类元操作。\n"
            "3) ledger_manager: 仅账单类元操作。\n"
            "   例如：“今天爬山120元”“午饭35元记一笔”“把昨天咖啡改成28元”都应路由 ledger_manager。\n"
            "4) schedule_manager: 仅日程/提醒类元操作。\n"
            "5) help_center: 产品帮助、能力说明、使用手册。\n"
            "6) chat_manager: 通用问答、创作、信息查询（不直接执行账单/提醒写操作）。\n"
            "   用户身份/信息/档案相关也必须路由 chat_manager，"
            "例如“我叫什么”“你叫什么”“叫我xxx”“以后你叫xxx”“请输出我的用户档案”。\n"
            "7) unknown: 仅在证据不足时使用。\n"
            "不要输出额外文本，不要输出多方案，只输出 JSON。"
        )
    )
    human = HumanMessage(
        content=(
            f"has_image={str(has_image).lower()}\n"
            f"has_pending_ledger={str(has_pending_ledger).lower()}\n\n"
            f"pending_complex={json.dumps(pending_complex or {}, ensure_ascii=False)}\n\n"
            f"会话上下文:\n{conversation_context}\n\n"
            f"用户消息:\n{content}"
        )
    )
    timeout_sec = max(2, int(get_settings().router_intent_timeout_sec or 10))
    result = await asyncio.wait_for(runnable.ainvoke([system, human]), timeout=timeout_sec)
    route_intent = str(getattr(result, "route_intent", "") or "unknown").strip().lower()
    if route_intent not in VALID_INTENTS:
        route_intent = "unknown"
    return {
        "route_intent": route_intent,
        "confidence": float(getattr(result, "confidence", 0.0) or 0.0),
        "reason": str(getattr(result, "reason", "") or ""),
    }


async def router_node(state: GraphState) -> GraphState:
    if state.get("user_setup_stage", 0) < 3:
        return state

    extra = dict(state.get("extra") or {})
    pending_complex = extra.get("complex_task_pending")

    user_id = int(state.get("user_id") or 0)
    conversation_id = int(state.get("conversation_id") or 0)
    message = state["message"]
    content = (message.content or "").strip()
    if not content and not message.image_urls:
        return {**state, "intent": "chat_manager"}
    if message.image_urls and not _is_bookkeeping_image_request(content):
        return {**state, "intent": "chat_manager"}

    has_pending = False
    if user_id > 0 and conversation_id > 0:
        has_pending = await has_pending_ledger(user_id, conversation_id)
    context_text = render_conversation_context(
        state,
        max_messages=8,
        include_summary=True,
        include_assistant_messages=True,
        include_long_term_memories=False,
    )

    try:
        routed = await _route_intent_with_llm(
            content=content,
            has_image=bool(message.image_urls),
            has_pending_ledger=has_pending,
            conversation_context=context_text,
            pending_complex=(dict(pending_complex) if isinstance(pending_complex, dict) else None),
        )
        intent = str(routed.get("route_intent") or "unknown")
    except Exception:
        intent = "unknown"

    next_state: GraphState = {**state, "intent": intent}
    if isinstance(pending_complex, dict) and bool(pending_complex.get("active")) and intent != "complex_task":
        next_extra = dict(extra)
        next_extra["complex_task_pending"] = {"active": False, "reason": "", "topic": ""}
        next_state["extra"] = next_extra
    return next_state


def route_intent(state: GraphState) -> str:
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

    # When router evidence is insufficient, delegate to complex_task for a
    # second structured decomposition pass instead of defaulting to chat.
    return "complex_task"
