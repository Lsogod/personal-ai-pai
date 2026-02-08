from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage

from app.graph.context import render_conversation_context
from app.graph.state import GraphState
from app.services.ledger_pending import has_pending_ledger
from app.services.llm import get_llm
from app.services.tool_registry import list_runtime_tool_metas


VALID_INTENTS = {"skill_manager", "finance", "secretary", "writer", "guide", "unknown"}


def _extract_json_object(text: str) -> dict:
    payload = (text or "").strip()
    if payload.startswith("```"):
        lines = payload.splitlines()
        if len(lines) >= 3:
            payload = "\n".join(lines[1:-1]).strip()
    try:
        obj = json.loads(payload)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


async def _classify_intent_with_llm(
    content: str,
    has_image: bool,
    has_pending_ledger: bool,
    conversation_context: str,
    runtime_tools: str,
) -> str:
    llm = get_llm()
    system = SystemMessage(
        content=(
            "你是消息路由器。请将用户消息分类到一个意图，且只输出 JSON。"
            "可选 intent 仅限: skill_manager, finance, secretary, writer, guide, unknown。"
            "当用户在管理技能（新增/创建/更新/发布/停用/列出技能）时，intent=skill_manager。"
            "当用户记账、消费统计、小票识别、账单增删改查时，intent=finance。"
            "当用户提醒、日程、定时任务、日历查询时，intent=secretary。"
            "当用户在询问怎么用、教程、帮助、命令说明、手册时，intent=guide。"
            "当用户询问“你能做什么/有哪些功能”时，也归类为 guide（但应是简洁能力说明，不是命令手册）。"
            "写作/翻译/润色/普通问答时，intent=writer。"
            "当用户要抓取网页、总结链接、调用 /fetch 或 /mcp 时，intent=writer。"
            "当用户在查询天气（如“现在武汉天气”）时，intent=writer。"
            "当用户询问“我刚才问了什么/之前聊了什么/你记得什么”这类回忆上下文问题时，intent=writer。"
            "必须优先依据 user_message 本身判断；conversation_context 仅作辅助。"
            "可用工具信息可帮助判断是否属于 writer（工具调用/外部信息查询）。"
            "若不确定，intent=unknown。"
            "如果 has_pending_ledger=true，优先判断为 finance，除非用户明确在取消该流程。"
            "如果 has_image=true，优先判断为 finance 或 writer（取决于用户是否在做账单/票据/支付分析）。"
        )
    )
    human = HumanMessage(
        content=(
            f"has_image={str(has_image).lower()}\n"
            f"has_pending_ledger={str(has_pending_ledger).lower()}\n"
            f"runtime_tools={runtime_tools}\n"
            f"conversation_context=\n{conversation_context}\n\n"
            f"user_message={content}"
        )
    )
    response = await llm.ainvoke([system, human])
    data = _extract_json_object(str(response.content))
    intent = str(data.get("intent") or "unknown").strip().lower()
    return intent if intent in VALID_INTENTS else "unknown"


async def router_node(state: GraphState) -> GraphState:
    if state.get("user_setup_stage", 0) < 3:
        return state
    user_id = int(state.get("user_id") or 0)
    conversation_id = int(state.get("conversation_id") or 0)
    message = state["message"]
    content = (message.content or "").strip()
    if not content and not message.image_urls:
        return {**state, "intent": "writer"}
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
    if intent != "unknown":
        return {**state, "intent": intent}
    # Rule fallback when LLM is uncertain/unavailable.
    if message.image_urls or has_pending:
        return {**state, "intent": "finance"}
    return {**state, "intent": intent}

def route_intent(state: GraphState) -> str:
    message = state["message"]

    if state.get("user_setup_stage", 0) < 3:
        return "onboarding"

    routed = str(state.get("intent") or "").strip().lower()
    if routed in {"skill_manager", "finance", "secretary", "writer", "guide"}:
        return routed

    content = (message.content or "").lower()
    # Command fallback when LLM route fails.
    if content.startswith("/help"):
        return "guide"
    if content.startswith("/mcp") or content.startswith("/fetch") or content.startswith("/weather"):
        return "writer"
    if content.startswith("/skill"):
        return "skill_manager"
    if message.image_urls or content.startswith("/ledger"):
        return "finance"
    if content.startswith("/calendar"):
        return "secretary"
    # Default route fallback.
    return "writer"
