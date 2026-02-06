from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage

from app.graph.state import GraphState
from app.services.llm import get_llm


VALID_INTENTS = {"skill_manager", "finance", "secretary", "writer", "unknown"}


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


async def _classify_intent_with_llm(content: str) -> str:
    llm = get_llm()
    system = SystemMessage(
        content=(
            "你是消息路由器。请将用户消息分类到一个意图，且只输出 JSON。"
            "可选 intent 仅限: skill_manager, finance, secretary, writer, unknown。"
            "当用户在管理技能（新增/创建/更新/发布/停用/列出技能）时，intent=skill_manager。"
            "当用户记账、消费统计、小票识别、账单增删改查时，intent=finance。"
            "当用户提醒、日程、定时任务时，intent=secretary。"
            "写作/翻译/润色/普通问答时，intent=writer。"
            "若不确定，intent=unknown。"
        )
    )
    human = HumanMessage(content=content)
    response = await llm.ainvoke([system, human])
    data = _extract_json_object(str(response.content))
    intent = str(data.get("intent") or "unknown").strip().lower()
    return intent if intent in VALID_INTENTS else "unknown"


async def router_node(state: GraphState) -> GraphState:
    if state.get("user_setup_stage", 0) < 3:
        return state
    message = state["message"]
    content = (message.content or "").strip()
    if not content and not message.image_urls:
        return {**state, "intent": "writer"}
    if message.image_urls:
        # Image receipts should still strongly prefer finance.
        return {**state, "intent": "finance"}
    try:
        intent = await _classify_intent_with_llm(content)
    except Exception:
        intent = "unknown"
    return {**state, "intent": intent}

def route_intent(state: GraphState) -> str:
    message = state["message"]

    if state.get("user_setup_stage", 0) < 3:
        return "onboarding"

    routed = str(state.get("intent") or "").strip().lower()
    if routed in {"skill_manager", "finance", "secretary", "writer"}:
        return routed

    content = (message.content or "").lower()
    if "/skill" in content or "新增技能" in content or "创建技能" in content:
        return "skill_manager"
    if (
        message.image_urls
        or "记账" in content
        or "消费" in content
        or "账单" in content
        or "/ledger" in content
    ):
        return "finance"
    if "提醒" in content or "日程" in content or "schedule" in content:
        return "secretary"
    if "翻译" in content or "润色" in content or "写" in content:
        return "writer"
    return "writer"
