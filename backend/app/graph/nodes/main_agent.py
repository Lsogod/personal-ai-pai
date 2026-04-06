# -*- coding: utf-8 -*-
"""Single agent node that replaces the Router + chat_manager pipeline.

The main agent has access to ALL tools (ledger, schedule, profile, MCP,
conversation, memory) and decides autonomously which tools to call.
Pending multi-turn flows (receipt OCR confirmation, schedule plan
confirmation) are short-circuited before invoking the LLM to avoid
unnecessary latency.
"""
from __future__ import annotations

from datetime import datetime
import json
import logging
import re
import time
from typing import Any, Literal
from zoneinfo import ZoneInfo

from langchain.agents import create_agent
from langchain.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.config import get_settings
from app.graph.context import render_conversation_context
from app.graph.state import GraphState
from app.models.message import Message
from app.models.user import User
from app.services.langchain_tools import AgentToolContext, ToolInvocationContext
from app.services.ledger_pending import has_pending_ledger
from app.services.llm import get_llm
from app.services.memory import deactivate_identity_memories_for_user, list_long_term_memories
from langgraph.errors import GraphRecursionError

from app.services.runtime_context import (
    get_session,
    get_llm_streamer,
    reset_crawl_webpage_call_count,
    reset_fetch_url_call_count,
    reset_mcp_tool_call_count,
    reset_tool_audit_hook,
    set_crawl_webpage_call_count,
    set_fetch_url_call_count,
    set_mcp_tool_call_count,
    set_tool_audit_hook,
)
from app.services.skills import load_skills
from app.services.toolsets import build_node_langchain_tools, invoke_node_tool_typed

logger = logging.getLogger(__name__)

LLM_NODE_NAME = "main_agent"
FAILURE_LLM_NODE_NAME = "main_agent_failure"

COMMUNITY_SOURCE_HINTS: tuple[str, ...] = (
    "zhihu.com",
    "tieba.baidu.com",
    "xiaohongshu.com",
    "weibo.com",
    "bilibili.com",
    "douyin.com",
)

AUTHORITATIVE_SOURCE_HINTS: tuple[str, ...] = (
    ".gov.cn",
    ".edu.cn",
    "gov.cn",
    "edu.cn",
    "people.com.cn",
    "xinhuanet.com",
    "cctv.com",
)

# Tool name → user-friendly Chinese label
TOOL_DISPLAY_NAMES: dict[str, str] = {
    "now_time": "获取当前时间",
    "web_search": "联网搜索",
    "maps_weather": "查询天气",
    "bing_search": "搜索网页",
    "crawl_webpage": "抓取网页正文",
    "analyze_image": "分析图片",
    "analyze_receipt": "识别小票",
    "ledger_insert": "记账",
    "ledger_update": "更新账单",
    "ledger_delete": "删除账单",
    "ledger_get_latest": "查询最新账单",
    "ledger_list_recent": "查询近期账单",
    "ledger_list": "查询账单",
    "ledger_text2sql": "查询账单",
    "schedule_insert": "创建提醒",
    "schedule_update": "更新提醒",
    "schedule_delete": "删除提醒",
    "schedule_get_latest": "查询最新日程",
    "schedule_list_recent": "查询近期日程",
    "schedule_list": "查询日程",
    "conversation_current": "查看当前会话",
    "conversation_list": "查看会话列表",
    "memory_list": "查看记忆",
    "memory_save": "写入记忆",
    "memory_append": "追加记忆",
    "memory_delete": "删除记忆",
    "update_user_profile": "更新档案",
    "query_user_profile": "查询档案",
}

MULTI_LEDGER_DELIMITERS = ("，", ",", "、", ";", "；", "\n", "  ")
MULTI_LEDGER_BLOCKERS = (
    "提醒",
    "日程",
    "查",
    "查询",
    "多少",
    "总共",
    "合计",
    "统计",
    "如果",
    "超过",
    "达到",
    "预算",
    "限制",
    "删除",
    "删掉",
    "修改",
    "改成",
    "更新",
)
MULTI_SCHEDULE_DELIMITERS = ("，", ",", "、", ";", "；", "\n", "  ", "然后", "再")
MULTI_SCHEDULE_BLOCKERS = (
    "查询",
    "查看",
    "多少",
    "总共",
    "统计",
    "删除",
    "删掉",
    "取消",
    "修改",
    "改成",
    "更新",
    "已完成",
    "完成",
)
SCHEDULE_CREATE_HINTS = ("提醒", "日程", "待办", "安排", "记得", "通知", "叫我")
TIME_MARKER_PATTERN = re.compile(
    r"(今天|明天|后天|今晚|今早|今晨|明早|明晚|中午|下午|晚上|早上|上午|下周|本周|"
    r"周[一二三四五六日天]|星期[一二三四五六日天]|\d{4}-\d{1,2}-\d{1,2}|"
    r"\d{1,2}[:：]\d{2}|\d{1,2}点(?:半|一刻|三刻)?)"
)
MULTI_ACTION_CONNECTORS = ("，", ",", "、", ";", "；", "\n", "然后", "再", "并且", "同时", "顺便")
MULTI_ACTION_SAFE_TOOLS: set[str] = {
    "now_time",
    "ledger_insert",
    "ledger_update",
    "ledger_delete",
    "ledger_list",
    "ledger_list_recent",
    "ledger_get_latest",
    "ledger_text2sql",
    "schedule_insert",
    "schedule_update",
    "schedule_delete",
    "schedule_list",
    "schedule_list_recent",
    "schedule_get_latest",
}


class MultiActionStepDecision(BaseModel):
    mode: Literal["not_multi", "tool", "done", "clarify"] = Field(default="not_multi")
    tool_name: str = Field(default="")
    args: dict[str, Any] = Field(default_factory=dict)
    reply: str = Field(default="")


MEMORY_TYPE_LABELS: dict[str, str] = {
    "preference": "偏好",
    "fact": "事实",
    "goal": "目标",
    "project": "项目",
    "constraint": "约束",
}
EXPLICIT_MEMORY_LIST_PATTERNS: tuple[str, ...] = (
    "我的长期记忆有哪些",
    "长期记忆有哪些",
    "列出我的长期记忆",
    "查看我的长期记忆",
    "看看我的长期记忆",
)
PROFILE_STATEMENT_BLOCKERS: tuple[str, ...] = (
    "天气",
    "气温",
    "预报",
    "提醒",
    "日程",
    "路线",
    "导航",
    "附近",
    "吗",
    "?",
    "？",
)
RESIDENCE_CITY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(?:我(?:现在)?住在|我(?:现在)?居住在|我的居住城市(?:是|改成)|居住城市(?:是|改成)|我所在城市(?:是|在))(?P<city>[\u4e00-\u9fffA-Za-z·]{2,24})(?:市)?[。！!，, ]*$"),
    re.compile(r"^(?:我家在)(?P<city>[\u4e00-\u9fffA-Za-z·]{2,24})(?:市)?[。！!，, ]*$"),
)


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _log(msg: str) -> None:
    print(msg, flush=True)


def _normalize_compact_text(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def _looks_like_explicit_memory_list_query(content: str) -> bool:
    compact = _normalize_compact_text(content)
    if not compact:
        return False
    return any(pattern in compact for pattern in EXPLICIT_MEMORY_LIST_PATTERNS)


def _extract_explicit_profile_update_args(content: str) -> dict[str, str]:
    raw = str(content or "").strip()
    compact = _normalize_compact_text(raw)
    if not compact:
        return {}
    if any(marker in compact for marker in PROFILE_STATEMENT_BLOCKERS):
        return {}

    for pattern in RESIDENCE_CITY_PATTERNS:
        match = pattern.fullmatch(raw)
        if not match:
            continue
        city = str(match.group("city") or "").strip()
        city = re.sub(r"[，。！？!?,]+$", "", city).strip()
        if city:
            return {"residence_city": city}

    if compact in {"我有其他客户端账号", "我在其他客户端有账号", "我有其他账号", "我已有其他客户端账号", "我有别的客户端账号"}:
        return {"has_other_client_accounts": "有"}
    if compact in {"我没有其他客户端账号", "我在其他客户端没有账号", "我没有其他账号", "我没有别的客户端账号"}:
        return {"has_other_client_accounts": "没有"}

    return {}


def _render_memory_list_reply(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "当前还没有可用的长期记忆。"
    lines = [f"当前共有 {len(rows)} 条长期记忆："]
    for index, row in enumerate(rows, start=1):
        memory_type = str(row.get("memory_type") or "fact").strip().lower()
        label = MEMORY_TYPE_LABELS.get(memory_type, memory_type or "记忆")
        content = str(row.get("content") or "").strip()
        if not content:
            continue
        lines.append(f"{index}. {label}：{content}")
    if len(lines) == 1:
        return "当前还没有可用的长期记忆。"
    return "\n".join(lines)


def _parse_profile_bool_flag(value: str) -> bool | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    lowered = raw.lower()
    truthy = {"有", "是", "true", "1", "yes", "y"}
    falsy = {"没有", "无", "否", "false", "0", "no", "n"}
    if raw in truthy or lowered in truthy:
        return True
    if raw in falsy or lowered in falsy:
        return False
    return None


async def _handle_profile_and_memory_shortcuts(
    *,
    session,
    user: User,
    content: str,
    user_id: int,
    platform: str,
    conversation_id: int | None,
    image_urls: list[str],
    audit_hook,
) -> str | None:
    _ = (platform, conversation_id, image_urls, audit_hook)

    if _looks_like_explicit_memory_list_query(content):
        rows = await list_long_term_memories(
            session=session,
            user_id=user_id,
            limit=120,
        )
        return _render_memory_list_reply(rows)

    profile_args = _extract_explicit_profile_update_args(content)
    if profile_args:
        parts: list[str] = []
        changed = False
        residence_city = str(profile_args.get("residence_city") or "").strip()
        if residence_city:
            if residence_city != str(user.residence_city or "").strip():
                user.residence_city = residence_city
                changed = True
            parts.append(f"居住城市已更新为{residence_city}")

        has_other_accounts = _parse_profile_bool_flag(profile_args.get("has_other_client_accounts") or "")
        if has_other_accounts is not None:
            if has_other_accounts != user.has_other_client_accounts:
                user.has_other_client_accounts = has_other_accounts
                changed = True
            parts.append(f"其他客户端账号状态已更新为{'有' if has_other_accounts else '没有'}")

        if changed:
            await deactivate_identity_memories_for_user(session, user_id=user_id)
            session.add(user)
            await session.commit()
        return "，".join(parts) + "。" if parts else "档案已更新。"

    return None


def _looks_like_multi_ledger_insert_request(content: str) -> bool:
    text = re.sub(r"\s+", " ", str(content or "")).strip()
    if not text:
        return False
    if any(token in text for token in MULTI_LEDGER_BLOCKERS):
        return False
    amount_matches = re.findall(r"(?<!\d)(\d+(?:\.\d{1,2})?)(?:\s*(?:元|块|人民币|rmb))?", text, flags=re.IGNORECASE)
    return len(amount_matches) >= 2


def _looks_like_multi_schedule_insert_request(content: str) -> bool:
    text = re.sub(r"\s+", " ", str(content or "")).strip()
    if not text:
        return False
    if any(token in text for token in MULTI_SCHEDULE_BLOCKERS):
        return False
    if not any(token in text for token in SCHEDULE_CREATE_HINTS):
        return False
    time_markers = TIME_MARKER_PATTERN.findall(text)
    if len(time_markers) >= 2:
        return True
    return sum(1 for delimiter in MULTI_SCHEDULE_DELIMITERS if delimiter in text) >= 1 and len(time_markers) >= 1


def _looks_like_multi_action_request(content: str) -> bool:
    text = re.sub(r"\s+", " ", str(content or "")).strip()
    if not text:
        return False
    if _looks_like_multi_ledger_insert_request(text) or _looks_like_multi_schedule_insert_request(text):
        return True
    if not any(token in text for token in MULTI_ACTION_CONNECTORS):
        return False
    ledger_signal = bool(re.search(r"\d+(?:\.\d{1,2})?\s*(?:元|块|人民币|rmb)", text, flags=re.IGNORECASE))
    schedule_signal = bool(TIME_MARKER_PATTERN.search(text)) and any(token in text for token in SCHEDULE_CREATE_HINTS)
    return ledger_signal and schedule_signal


def _current_local_time_context() -> tuple[str, str]:
    tz_name = str(get_settings().timezone or "Asia/Shanghai").strip() or "Asia/Shanghai"
    try:
        now_local = datetime.now(ZoneInfo(tz_name)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        tz_name = "UTC"
        now_local = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    return tz_name, now_local


def _json_preview(value: Any, *, limit: int = 1200) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = str(value)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        return text[:limit] + "..."
    return text


def _extract_llm_text(output: Any) -> str:
    content = getattr(output, "content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        texts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    texts.append(text.strip())
        return "\n".join(texts).strip()
    return str(content or "").strip()


def _parse_multi_action_decision_text(text: str) -> MultiActionStepDecision:
    raw = str(text or "").strip()
    if not raw:
        return MultiActionStepDecision()
    cleaned = raw
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    candidates = [cleaned]
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if match:
        candidates.insert(0, match.group(0))
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
            return MultiActionStepDecision.model_validate(payload)
        except Exception:
            continue
    return MultiActionStepDecision()


def _is_tool_error_result(result: Any) -> bool:
    if not isinstance(result, str):
        return False
    lowered = result.strip().lower()
    if not lowered:
        return False
    markers = (
        "not available",
        "failed",
        "error",
        "invalid",
        "missing required arg",
        "session context not set",
        "无法解析",
        "未找到",
        "请选择",
        "失败",
        "错误",
    )
    return any(marker in lowered for marker in markers)


def _is_tool_step_success(tool_name: str, result: Any) -> bool:
    name = str(tool_name or "").strip().lower()
    if name == "now_time":
        return isinstance(result, str) and bool(str(result).strip()) and not _is_tool_error_result(result)
    if name in {
        "ledger_insert",
        "ledger_update",
        "ledger_delete",
        "ledger_get_latest",
        "schedule_insert",
        "schedule_update",
        "schedule_delete",
        "schedule_get_latest",
    }:
        return isinstance(result, dict)
    return not _is_tool_error_result(result)


async def _decide_multi_action_step(
    *,
    content: str,
    executed_steps: list[dict[str, Any]],
    force_multi: bool = False,
) -> MultiActionStepDecision:
    tz_name, now_local = _current_local_time_context()
    llm = get_llm(node_name="main_agent_multi_action")
    tool_list = ", ".join(sorted(MULTI_ACTION_SAFE_TOOLS))
    system = SystemMessage(
        content=(
            "你是多操作受控执行器。你的任务是：面对一条可能包含多个独立动作的用户消息，"
            "每次只决定下一步该执行的一个工具动作，或者结束并生成最终回复，或者要求澄清。\n"
            f"只允许使用这些工具：{tool_list}\n"
            "规则：\n"
            f"1. 如果这条消息其实不是多操作请求，mode=not_multi。当前 force_multi={json.dumps(force_multi, ensure_ascii=False)}；"
            "如果 force_multi=true，则不允许输出 not_multi，只能输出 tool、done 或 clarify。\n"
            "2. 如果是多操作请求，每次只能输出一个 next tool，不要一次规划多步，也不要假装某步已经成功。\n"
            "3. 写操作必须按用户原始顺序串行执行。\n"
            "4. 你的判断只能基于当前用户消息和已执行步骤的真实结果；不要依赖历史上下文去猜后续结果。\n"
            "5. 对 ledger_insert：如果用户没有明确给出时间，不要传 transaction_date。\n"
            "6. 对 schedule_insert：尽量直接给出 trigger_time；如需相对时间，可基于当前本地时间换算，必要时才使用 now_time。\n"
            "7. 只有在确实需要先查询再修改/删除时，才先调用 list/get_latest/text2sql 类工具。\n"
            "8. 当所有独立动作都完成时，mode=done，并在 reply 中只基于已执行结果给出最终答复。\n"
            "9. 如果缺少关键信息无法继续，mode=clarify，并在 reply 中明确缺什么。\n"
            "10. 不要重复已经成功执行过的相同动作。\n"
            "示例：\n"
            "- 用户：借出5000 转账1300\n"
            "  第一步应输出：mode=tool, tool_name=ledger_insert, args={\"item\":\"借出\",\"amount\":5000,\"category\":\"其他\"}\n"
            "- 用户：明天上午9点提醒我开会，后天下午3点提醒我取快递\n"
            "  第一步应输出：mode=tool, tool_name=schedule_insert, args 包含第一条提醒的 content 和 trigger_time\n"
            "- 用户：午饭20\n"
            "  如果 force_multi=false，应输出 mode=not_multi。\n"
            "输出要求：只返回一个 JSON 对象，不要输出任何解释、注释、Markdown 或代码块。"
        )
    )
    human = HumanMessage(
        content=(
            f"当前本地时间（{tz_name}）：{now_local}\n"
            f"用户消息：{content}\n"
            f"已执行步骤：{_json_preview(executed_steps, limit=3000)}"
        )
    )
    try:
        output = await llm.ainvoke([system, human])
        return _parse_multi_action_decision_text(_extract_llm_text(output))
    except Exception:
        return MultiActionStepDecision()


async def _render_multi_action_reply(
    *,
    content: str,
    executed_steps: list[dict[str, Any]],
    limit_reached: bool = False,
) -> str:
    prompt = (
        "你是中文助手。请根据已经真实执行过的多步工具结果，为用户生成最终回复。\n"
        "要求：\n"
        "1. 只能根据 executed_steps 中真实存在的结果描述成功或失败。\n"
        "2. 不要说某一步成功，除非对应步骤里 ok=true。\n"
        "3. 若部分成功、部分失败，要分别说明。\n"
        "4. 回复简洁、自然、直接。\n"
        "5. 如果 limit_reached=true，要明确说明已停止继续执行，避免误导用户认为所有步骤都已完成。\n\n"
        f"用户消息：{content}\n"
        f"limit_reached={json.dumps(limit_reached, ensure_ascii=False)}\n"
        f"executed_steps={_json_preview(executed_steps, limit=5000)}"
    )
    try:
        output = await get_llm(node_name="main_agent_multi_action_reply").ainvoke(prompt)
        content_value = getattr(output, "content", "")
        if isinstance(content_value, str) and content_value.strip():
            return content_value.strip()
        if isinstance(content_value, list):
            texts: list[str] = []
            for item in content_value:
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        texts.append(text.strip())
            if texts:
                return "\n".join(texts).strip()
    except Exception:
        pass

    if not executed_steps:
        return "这次没有完成可确认的操作。"
    success_count = sum(1 for item in executed_steps if bool(item.get("ok")))
    if success_count <= 0:
        return "这次没有完成可确认的操作，请换个更明确的说法再试一次。"
    lines = [f"已完成 {success_count} 个操作："]
    for item in executed_steps:
        if not bool(item.get("ok")):
            continue
        tool_name = str(item.get("tool_name") or "")
        result = item.get("result")
        if tool_name == "ledger_insert" and isinstance(result, dict):
            lines.append(
                f"- 记账：{result.get('item', '消费')} {float(result.get('amount') or 0):.2f}元（{result.get('category') or '其他'}）"
            )
        elif tool_name == "schedule_insert" and isinstance(result, dict):
            lines.append(f"- 提醒：{result.get('content', '提醒')}（{result.get('trigger_time') or ''}）")
        else:
            lines.append(f"- {TOOL_DISPLAY_NAMES.get(tool_name, tool_name)}")
    if limit_reached:
        lines.append("已停止继续执行剩余步骤，请检查是否还需要补充信息。")
    return "\n".join(lines)


async def _handle_controlled_multi_action(
    *,
    content: str,
    user_id: int,
    platform: str,
    conversation_id: int | None,
    image_urls: list[str],
    audit_hook,
) -> str | None:
    if not _looks_like_multi_action_request(content):
        return None

    ctx = ToolInvocationContext(
        user_id=user_id,
        platform=platform,
        conversation_id=conversation_id,
        image_urls=image_urls,
        audit_hook=audit_hook,
    )
    executed_steps: list[dict[str, Any]] = []
    seen_signatures: set[str] = set()

    for _ in range(6):
        step_index = len(executed_steps) + 1
        planning_event_name = f"multi_action_plan_{step_index}"
        await _emit_tool_event(planning_event_name, "start", label=f"分析第{step_index}个操作")
        decision = await _decide_multi_action_step(
            content=content,
            executed_steps=executed_steps,
        )
        if decision.mode == "not_multi" and not executed_steps:
            decision = await _decide_multi_action_step(
                content=content,
                executed_steps=executed_steps,
                force_multi=True,
            )
        await _emit_tool_event(planning_event_name, "done", label=f"分析第{step_index}个操作")
        mode = str(decision.mode or "not_multi").strip().lower()
        if mode == "not_multi":
            return None
        if mode == "clarify":
            reply = str(decision.reply or "").strip()
            return reply or "这条消息里包含多个动作，但还缺少继续执行所需的信息。"
        if mode == "done":
            return await _render_multi_action_reply(
                content=content,
                executed_steps=executed_steps,
            )

        tool_name = str(decision.tool_name or "").strip()
        args = dict(decision.args or {})
        if tool_name not in MULTI_ACTION_SAFE_TOOLS:
            return await _render_multi_action_reply(
                content=content,
                executed_steps=executed_steps,
                limit_reached=True,
            )

        signature = f"{tool_name}:{_json_preview(args, limit=600)}"
        if signature in seen_signatures:
            return await _render_multi_action_reply(
                content=content,
                executed_steps=executed_steps,
                limit_reached=True,
            )
        seen_signatures.add(signature)

        step_event_name = f"{tool_name}#{step_index}"
        step_label = _build_multi_action_step_label(step_index, tool_name, args)
        await _emit_tool_event(step_event_name, "start", label=step_label)
        result = await invoke_node_tool_typed(
            context=ctx,
            node_name="main_agent",
            tool_name=tool_name,
            args=args,
        )
        await _emit_tool_event(step_event_name, "done", label=step_label)
        executed_steps.append(
            {
                "tool_name": tool_name,
                "args": args,
                "ok": _is_tool_step_success(tool_name, result),
                "result": result,
            }
        )

    return await _render_multi_action_reply(
        content=content,
        executed_steps=executed_steps,
        limit_reached=True,
    )


async def _load_recent_image_urls(
    *,
    session,
    user_id: int,
    conversation_id: int | None,
    limit: int = 3,
) -> list[str]:
    if not conversation_id:
        return []
    rows = (
        (
            await session.execute(
                select(Message)
                .where(
                    Message.user_id == user_id,
                    Message.conversation_id == int(conversation_id),
                )
                .order_by(Message.id.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    image_urls: list[str] = []
    seen: set[str] = set()
    for row in rows:
        candidates = row.image_urls if isinstance(row.image_urls, list) else []
        for item in candidates:
            image_ref = str(item or "").strip()
            if not image_ref or image_ref in seen:
                continue
            seen.add(image_ref)
            image_urls.append(image_ref)
            if len(image_urls) >= limit:
                return image_urls
    return image_urls


def _render_image_analysis_context(result: Any) -> str:
    if isinstance(result, dict):
        image_kind = str(result.get("image_kind") or "other").strip() or "other"
        answer = str(result.get("answer") or "").strip()
        summary = str(result.get("summary") or "").strip()
        ocr_text = str(result.get("ocr_text") or "").strip()
        confidence = result.get("confidence")
        parts = [f"- 预分析类型：{image_kind}"]
        if summary:
            parts.append(f"- 预分析摘要：{summary}")
        if answer:
            parts.append(f"- 预分析结论：{answer}")
        if ocr_text:
            parts.append(f"- 预分析文字：{ocr_text}")
        if confidence is not None:
            parts.append(f"- 预分析置信度：{confidence}")
        if parts:
            return "\n".join(dict.fromkeys(parts))
    text = str(result or "").strip()
    return f"- 预分析结果：{text}" if text else ""


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

def _build_system_prompt(
    *,
    user: User,
    context_text: str,
    skills: str,
    runtime_tools_summary: str,
    image_count: int,
    image_analysis_text: str,
) -> str:
    nickname = str(user.nickname or "").strip() or "用户"
    ai_name = str(user.ai_name or "").strip() or "AI 助手"
    ai_emoji = str(user.ai_emoji or "").strip()
    tz_name = str(get_settings().timezone or "Asia/Shanghai").strip() or "Asia/Shanghai"
    try:
        now_local = datetime.now(ZoneInfo(tz_name)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        tz_name = "UTC"
        now_local = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    image_section = ""
    if image_count > 0:
        image_section = (
            "## 当前可分析图片\n"
            f"- 当前消息或最近上下文中有 {image_count} 张可分析图片。\n"
            "- 先结合下方图片预分析结果理解图片内容，再决定下一步操作。\n"
            "- 如果用户要根据小票、发票、支付截图记账，优先调用 analyze_receipt，再决定是否 ledger_insert。\n"
            "- 如果用户是在追问图片内容、图片文字、翻译图片文字、总结截图信息，优先基于预分析结果回答；只有信息不足时再调用 analyze_image。\n"
        )
        if image_analysis_text:
            image_section += f"\n## 图片预分析结果\n{image_analysis_text}\n\n"

    return (
        f"你是{nickname}的私人助理{ai_name} {ai_emoji}。\n"
        "你必须结合会话上下文连续对话，不要声称自己无法回忆当前会话。\n\n"
        f"## 当前时间基准\n- 当前本地时间（{tz_name}）：{now_local}\n\n"

        "## 工具使用原则\n"
        "你具备工具调用能力，由你自主判断是否需要调用工具。\n"
        "- 默认先判断你是否可以仅凭稳定知识直接回答；如果可以，就直接回答，不要为了形式化而调用工具。\n"
        "- 只有在以下情况才调用工具：需要查询外部状态、需要执行外部动作、需要读取会话/记忆/账单/日程、需要处理图片，或你无法根据现有知识可靠回答。\n"
        "- 时间查询：调用 now_time。\n"
        "- 天气查询使用 maps_weather，不要走通用联网搜索。\n"
        "- 外部联网查询统一使用 web_search；它会自动完成搜索、必要时抓取正文，并返回结构化结果。\n"
        "- 当用户询问“有哪些 MCP 工具 / 我的 MCP 工具”时，只列系统外部工具和用户自定义 MCP 工具；不要把记账、记忆、图片分析、档案、时间等内置工具误称为 MCP。\n"
        "- 当用户询问“有哪些工具 / 现在能做什么”时，必须明确区分：内置工具、系统外部工具、我的 MCP 工具。\n"
        "- 展示用户自定义 MCP 工具时，优先使用“服务名 / 原始工具名”的形式，不要把 `umcp_数字_` 这样的内部工具名直接当成面向用户的名称。\n"
        "- 只有在你无法根据现有知识可靠回答，或用户明确要求你查询、搜索、核实外部公开信息时，才调用 web_search；不要按固定问题类别机械联网。\n"
        "- web_search 返回结构化 JSON，包含 status、answer_ready、summary、sources。正常情况下不要再自行编排底层搜索工具。\n"
        "- 如果 web_search 的 sources 标题、摘要或正文预览已经给出明确答案，就直接回答，并附上 1 到 3 个实际来源。\n"
        "- 如果 web_search 返回的 status 表示 no_results、results_low_authority、results_insufficient 或 network_error，就根据该结果如实向用户说明，不要自编失败原因。\n"
        "- 只有当 web_search 的 status 明确是 network_error 时，才能说“连接失败 / 超时 / 联网异常”；如果 status 是 results_low_authority 或 results_insufficient，必须明确说“已搜到结果，但来源不够权威或信息不足”。\n"
        "- 当 web_search 已成功返回结果但 answer_ready=false 时，不要把失败归因成网络问题，也不要假装工具没有返回内容。\n"
        "- 对学校、医院、公司、机构、地名等短实体查询，如果 web_search 没有返回足够可靠的结果，不要根据上一轮对话或记忆去猜这个实体的全称、身份或归属；只能依据当前轮工具结果说明“搜到了什么”或“缺少什么”。\n"
        "- 通用图片理解、识别截图内容、提取图片文字：调用 analyze_image。\n"
        "- 会话/记忆查询：调用 conversation_current / conversation_list / memory_list。\n"
        "- 只要用户在问“我的长期记忆有哪些 / 列出长期记忆 / 查看长期记忆”，必须调用 memory_list，不要只根据上下文里注入的记忆自行概括。\n"
        "- 当用户明确要求你记住某件事、某偏好、某规则、某长期约束时，调用 memory_save 直接写入长期记忆。\n"
        "- 当用户要求在现有记忆上补充信息时，先用 memory_list 找到目标，再调用 memory_append。\n"
        "- 当用户要求忘记、删除某条长期记忆时，先用 memory_list 找到目标，再调用 memory_delete。\n"
        "- 用户用陈述句直接提供稳定个人档案时，不要转成天气、搜索或泛化问答；应优先更新档案。"
        "例如“我住在武汉 / 我居住在武汉 / 我有其他客户端账号 / 我没有其他客户端账号”都属于档案更新。\n"
        "- 简单记账（如'午饭35元'）：直接调用 ledger_insert，"
        "分类参考：餐饮/交通/购物/娱乐/医疗/教育/居家/通讯/社交/服饰/其他。\n"
        "- 如果同一句里包含多笔彼此独立的账单相关操作，必须一步一步处理：每次只执行一个下一步动作，并基于上一步的真实工具结果再决定下一步。\n"
        "- 记账时，如果用户没有明确给出日期或时间，不要自行猜测、补写或硬传 transaction_date；直接省略该参数，让系统按当前时间记录。\n"
        "- 只有当用户明确说了昨天/今天上午/某月某日/某个时刻时，才给 ledger_insert 或 ledger_update 传 transaction_date。\n"
        "- 记账成功后的日期/时间说明，必须以 ledger_insert / ledger_update 返回的 transaction_date 为准；不要自己编造日期。\n"
        "- 账单查询/修改/删除：使用 ledger_list / ledger_update / ledger_delete / ledger_text2sql。\n"
        "- 只有在用户明确说“最近几条 / 近几笔 / 最新几笔账单”时，才使用 ledger_list_recent。\n"
        "- 只要用户说“今天 / 昨天 / 本周 / 上周 / 本月 / 上月 / 今年 / 某一天 / 某个时间段”，必须使用 ledger_list 或 ledger_text2sql，并传准确的时间范围；不要用 ledger_list_recent 代替。\n"
        "- 账单查询回答默认逐条列出数据库记录，不要擅自把同名账单合并成一条“修正后”的结果。\n"
        "- 账单查询、修改、删除的最终回答必须以工具返回结果为准：工具没返回成功，就不能说“已记录 / 已更新 / 已删除”。\n"
        "- 如果 ledger_list / ledger_text2sql 返回了 N 条记录，回答时默认按这 N 条记录逐条说明；不能根据上下文自行补充、合并、去重或改写成另一组结果。\n"
        "- 如果 ledger_update / ledger_delete 没有明确返回目标 id、项目、金额或失败原因，就直接说明信息不足或操作失败，不要根据上下文猜测哪条账单被改了。\n"
        "- 小票/支付截图：调用 analyze_receipt 获取结构化数据后调用 ledger_insert。\n"
        "- 若用户上传了图片，先利用图片预分析结果判断图片是什么、用户想做什么，再决定是直接回答、翻译/总结，还是继续调用记账等工具。\n"
        "- 创建提醒：先调用 now_time 获取当前时间，再计算绝对时间，调用 schedule_insert。"
        "trigger_time 格式：YYYY-MM-DD HH:MM:SS（服务器时区）。\n"
        "- 如果同一句里包含多条彼此独立的日程相关操作，也必须一步一步处理：每次只执行一个下一步动作，并基于真实工具结果继续。\n"
        "- 查看提醒：使用 schedule_list / schedule_list_recent / schedule_get_latest。\n"
        "- 修改/删除提醒：使用 schedule_update / schedule_delete。\n"
        "- 日程查询、修改、删除也必须以工具返回结果为准；不能因为上下文里提到过某条提醒，就假定它已经存在、已经更新或已经删除。\n"
        "- 修改用户昵称(叫我xxx)：调用 update_user_profile(nickname=...)。\n"
        "- 修改助手名称(你叫xxx)：调用 update_user_profile(ai_name=...)。\n"
        "- 修改居住城市/省份/国家或其他客户端账号状态：调用 update_user_profile(residence_city / residence_province / residence_country / has_other_client_accounts)。\n"
        "- 查询用户档案：调用 query_user_profile。\n"
        "## 限制\n"
        "- 最多调用 6 次工具；若连续失败，给出失败原因和建议。\n"
        "- 技能文档是写作/回答参考，不是可调用工具。\n"
        "- 不要暴露内部链路与调试信息。\n"
        "- 必须严格遵循用户请求的时间/数量范围。\n"
        "- 基于外部工具的回答，缺少来源链接时不算完成。\n"
        "- 回答简洁、可执行、使用中文。\n\n"

        f"{image_section}"
        f"## 当前可用工具\n{runtime_tools_summary}\n\n"
        f"## 会话上下文\n{context_text}\n\n"
        f"## 技能文档\n{skills}"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_tool_catalog_from_langchain(tools: list, *, user_id: int | None = None) -> str:
    """Build tool catalog string from already-constructed LangChain tool objects."""
    if not tools:
        return "无可用工具。"
    user_labels: dict[str, str] = {}
    if user_id:
        from app.services.user_mcp_tools import get_user_mcp_tool_display_names
        user_labels = get_user_mcp_tool_display_names(user_id)

    builtin_names: list[str] = []
    system_external_names: list[str] = []
    user_mcp_names: list[str] = []

    for t in tools:
        name = getattr(t, "name", "")
        if not name:
            continue
        if name.startswith("umcp_"):
            user_mcp_names.append(user_labels.get(name, name))
        elif name in {"maps_weather", "bing_search", "crawl_webpage", "tool_list", "tool_call", "mcp_list_tools", "mcp_call_tool"}:
            system_external_names.append(name)
        else:
            builtin_names.append(name)

    if not builtin_names and not system_external_names and not user_mcp_names:
        return "无可用工具。"

    lines: list[str] = []
    if builtin_names:
        lines.append("- 内置工具: " + ", ".join(sorted(builtin_names)))
    if system_external_names:
        lines.append("- 系统外部工具: " + ", ".join(sorted(system_external_names)))
    if user_mcp_names:
        lines.append("- 我的 MCP 工具: " + ", ".join(sorted(user_mcp_names)))
    return "\n".join(lines)


def _extract_ai_text(messages: list[Any]) -> str:
    for msg in reversed(messages or []):
        if isinstance(msg, AIMessage):
            content = msg.content
            if isinstance(content, str) and content.strip():
                return content.strip()
            if isinstance(content, list):
                chunks: list[str] = []
                for item in content:
                    if isinstance(item, dict):
                        text = item.get("text")
                        if isinstance(text, str) and text.strip():
                            chunks.append(text.strip())
                if chunks:
                    return "\n".join(chunks)
    return ""


def _audit_hook_factory(
    user_id: int | None,
    platform: str,
    conversation_id: int | None,
    attempt_log: list[dict[str, Any]] | None = None,
):
    async def _hook(
        source: str,
        name: str,
        args: dict[str, Any],
        ok: bool,
        latency_ms: int,
        output: str,
        error: str,
    ) -> None:
        from app.db.session import AsyncSessionLocal
        from app.services.audit import log_event
        from app.services.usage import log_tool_usage

        detail = {
            "tool_name": name,
            "arguments": args,
            "ok": ok,
            "latency_ms": latency_ms,
            "conversation_id": conversation_id,
        }
        if output:
            detail["result_preview"] = output[:800]
        if error:
            detail["error"] = error[:800]
        if attempt_log is not None:
            attempt_log.append(
                {
                    "source": source,
                    "name": name,
                    "ok": ok,
                    "error": error[:300] if error else "",
                    "output_preview": output[:300] if output else "",
                }
            )
            if len(attempt_log) > 8:
                del attempt_log[:-8]
        try:
            async with AsyncSessionLocal() as session:
                await log_event(
                    session=session,
                    action="tool_called",
                    platform=platform,
                    user_id=user_id,
                    detail=detail,
                )
            await log_tool_usage(
                user_id=user_id,
                platform=platform,
                conversation_id=conversation_id,
                tool_source=source,
                tool_name=name,
                success=ok,
                latency_ms=latency_ms,
                error=error,
            )
        except Exception:
            return

    return _hook


def _summarize_tool_attempts(attempt_log: list[dict[str, Any]]) -> str:
    if not attempt_log:
        return "未记录到有效的工具调用结果。"
    parts: list[str] = []
    for item in attempt_log[-4:]:
        name = str(item.get("name") or "unknown")
        ok = bool(item.get("ok"))
        error = str(item.get("error") or "").strip()
        output_preview = str(item.get("output_preview") or "").strip()
        if error:
            parts.append(f"{name}：{error}")
            continue
        if ok and output_preview:
            preview = output_preview.replace("\n", " ").strip()
            if len(preview) > 80:
                preview = preview[:80] + "..."
            parts.append(f"{name}：返回了结果，但未形成可用答案（{preview}）")
            continue
        parts.append(f"{name}：未得到可用结果")
    return "；".join(parts)


def _extract_domains_from_attempt_log(attempt_log: list[dict[str, Any]]) -> list[str]:
    domains: list[str] = []
    seen: set[str] = set()
    for item in attempt_log:
        preview = str(item.get("output_preview") or "")
        for match in re.findall(r"https?://([^/\\s\"'>]+)", preview):
            host = match.strip().lower()
            if host.startswith("www."):
                host = host[4:]
            if host and host not in seen:
                seen.add(host)
                domains.append(host)
    return domains


def _extract_last_web_search_meta(attempt_log: list[dict[str, Any]]) -> dict[str, Any] | None:
    for item in reversed(attempt_log):
        if str(item.get("name") or "") != "web_search":
            continue
        preview = str(item.get("output_preview") or "").strip()
        if not preview:
            continue
        status_match = re.search(r'"status"\s*:\s*"([^"]+)"', preview)
        answer_ready_match = re.search(r'"answer_ready"\s*:\s*(true|false)', preview, flags=re.IGNORECASE)
        if not status_match:
            continue
        status = status_match.group(1).strip()
        answer_ready = False
        if answer_ready_match:
            answer_ready = answer_ready_match.group(1).lower() == "true"
        return {
            "status": status,
            "answer_ready": answer_ready,
        }
    return None


def _needs_failure_rewrite(
    *,
    final_text: str,
    attempt_log: list[dict[str, Any]],
) -> bool:
    text = str(final_text or "").strip()
    if not text:
        return False
    meta = _extract_last_web_search_meta(attempt_log)
    if not meta:
        return False
    status = str(meta.get("status") or "").strip()
    answer_ready = bool(meta.get("answer_ready"))
    lowered = text.lower()

    if status in {"results_low_authority", "results_insufficient", "no_results"}:
        network_markers = (
            "连接问题",
            "联网失败",
            "搜索失败",
            "网络异常",
            "超时",
            "无法获取",
        )
        if any(marker in text for marker in network_markers):
            return True
        if not answer_ready:
            guess_markers = (
                "根据之前的上下文",
                "根据您之前",
                "根据上下文推测",
                "通常指",
                "很可能指",
            )
            if any(marker in text for marker in guess_markers):
                return True

    if status == "network_error":
        if "已搜到结果" in text or "社区讨论" in text:
            return True

    return False


def _classify_failure_reason(
    *,
    attempt_log: list[dict[str, Any]],
    exception: Exception | None = None,
) -> dict[str, str]:
    message = str(exception or "").strip()
    lowered_exc = message.lower()
    if isinstance(exception, GraphRecursionError) or "recursion limit" in lowered_exc:
        return {
            "code": "search_loop",
            "summary": "系统在多次检索后仍未收敛到可用答案。",
            "suggestion": "可换一个更具体的关键词、来源站点，或让我只查某个细分方向。",
        }
    if any(token in lowered_exc for token in ("mcp request failed", "connection", "timeout", "http ", "timed out")):
        return {
            "code": "network_error",
            "summary": "外部搜索工具连接异常或超时。",
            "suggestion": "可稍后重试，或改用更具体的关键词。",
        }

    errors = [str(item.get("error") or "").strip() for item in attempt_log if str(item.get("error") or "").strip()]
    lowered_errors = " | ".join(errors).lower()
    if any(token in lowered_errors for token in ("mcp request failed", "connection", "timeout", "http ", "timed out")):
        return {
            "code": "network_error",
            "summary": "外部搜索工具连接异常或超时。",
            "suggestion": "可稍后重试，或改用更具体的关键词。",
        }

    domains = _extract_domains_from_attempt_log(attempt_log)
    if domains:
        has_authoritative = any(any(hint in domain for hint in AUTHORITATIVE_SOURCE_HINTS) for domain in domains)
        non_community_domains = [
            domain for domain in domains
            if not any(hint in domain for hint in COMMUNITY_SOURCE_HINTS)
        ]
        if not has_authoritative and not non_community_domains:
            return {
                "code": "results_low_authority",
                "summary": "搜索已返回结果，但当前结果主要来自社区讨论或问答站点，缺少足够权威的来源。",
                "suggestion": "可补充“官网、官方、教育局、招生”等关键词再查一次，或换一个更具体的问题。",
            }

    if attempt_log:
        return {
            "code": "results_insufficient",
            "summary": "工具已经返回结果，但这些结果不足以支撑稳定回答。",
            "suggestion": "可换一个更具体的关键词、来源站点，或告诉我你想了解的具体字段。",
        }

    return {
        "code": "unknown_failure",
        "summary": "这次请求未成功完成。",
        "suggestion": "请换个方式描述你的需求，或稍后重试。",
    }


async def _render_failure_message_with_llm(
    *,
    user_query: str,
    attempt_log: list[dict[str, Any]],
    reason: dict[str, str],
) -> str:
    prompt = (
        "你是中文 AI 助手的失败说明生成器。请根据结构化失败原因，用自然、简洁、准确的中文向用户解释为什么这次没能给出结果，并给出下一步建议。\n"
        "要求：\n"
        "1. 必须严格依据给定原因，不要编造连接失败、权限失败或工具异常。\n"
        "2. 不要提及提示词、递归限制、token、内部链路。\n"
        "3. 长度控制在 2 到 4 句。\n"
        "4. 如果 reason.code=results_low_authority，要明确说明“已经搜到结果，但主要来自社区讨论/问答，缺少官网或官方来源”。\n"
        "5. 如果 reason.code=network_error，才能说明连接或超时问题。\n"
        "6. 可以结合最近工具尝试做简短说明，但不要逐条复读所有日志。\n\n"
        f"用户问题：{user_query}\n"
        f"结构化失败原因：{json.dumps(reason, ensure_ascii=False)}\n"
        f"最近工具尝试：{_summarize_tool_attempts(attempt_log)}"
    )
    try:
        output = await get_llm(node_name=FAILURE_LLM_NODE_NAME).ainvoke(prompt)
        content = getattr(output, "content", "")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            texts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        texts.append(text.strip())
            if texts:
                return "\n".join(texts).strip()
    except Exception:
        pass
    summary = str(reason.get("summary") or "这次请求未成功完成。").strip()
    suggestion = str(reason.get("suggestion") or "请换个方式描述你的需求。").strip()
    return f"{summary}{suggestion}"


# ---------------------------------------------------------------------------
# Pending state short-circuits
# ---------------------------------------------------------------------------

async def _handle_ledger_pending(state: GraphState) -> GraphState:
    """Short-circuit: delegate to ledger_manager_node when pending state exists."""
    from app.graph.nodes.ledger_manager import ledger_manager_node
    return await ledger_manager_node(state)


async def _handle_schedule_pending(state: GraphState) -> GraphState:
    """Short-circuit: delegate to schedule_manager_node when pending state exists."""
    from app.graph.nodes.schedule_manager import schedule_manager_node
    return await schedule_manager_node(state)


def _has_pending_reminder_plan(state: GraphState) -> bool:
    extra = state.get("extra") or {}
    plan = extra.get("pending_reminder_plan")
    return isinstance(plan, dict) and bool(plan)


# ---------------------------------------------------------------------------
# SSE tool-call event helper
# ---------------------------------------------------------------------------

async def _emit_tool_event(name: str, status: str, label: str | None = None) -> None:
    """Push a tool_call SSE event through the streaming queue."""
    streamer = get_llm_streamer()
    if streamer is None:
        return
    from app.services.user_mcp_tools import get_user_mcp_tool_display_names
    from app.services.runtime_context import get_tool_user_id
    fallback = name
    if name.startswith("umcp_"):
        uid = get_tool_user_id()
        if uid:
            user_labels = get_user_mcp_tool_display_names(uid)
            fallback = user_labels.get(name, name)
    resolved_label = str(label or "").strip() or TOOL_DISPLAY_NAMES.get(name, fallback)
    payload = json.dumps(
        {"tool_call": {"name": name, "label": resolved_label, "status": status}},
        ensure_ascii=False,
    )
    # Wrap in a special marker so the SSE layer can distinguish it from text chunks
    try:
        await streamer(f"\x00TOOL_EVENT:{payload}")
    except Exception:
        pass


def _build_multi_action_step_label(step_index: int, tool_name: str, args: dict[str, Any]) -> str:
    prefix = f"第{step_index}步："
    name = str(tool_name or "").strip().lower()
    if name == "ledger_insert":
        item = str(args.get("item") or "记账").strip() or "记账"
        amount = args.get("amount")
        amount_text = ""
        try:
            amount_text = f" {float(amount):g}元"
        except Exception:
            amount_text = ""
        return f"{prefix}记账 {item}{amount_text}"
    if name == "schedule_insert":
        content = str(args.get("content") or "创建提醒").strip() or "创建提醒"
        return f"{prefix}创建提醒 {content}"
    return prefix + TOOL_DISPLAY_NAMES.get(tool_name, tool_name)


# ---------------------------------------------------------------------------
# Main agent node
# ---------------------------------------------------------------------------

async def main_agent_node(state: GraphState) -> GraphState:
    t0 = time.monotonic()
    session = get_session()
    user = await session.get(User, state["user_id"])
    if not user:
        return {**state, "responses": ["未找到用户信息。"]}

    user_id: int = user.id  # type: ignore[assignment]
    conversation_id = state.get("conversation_id")
    message = state["message"]
    content = (message.content or "").strip()
    current_image_urls = [str(item).strip() for item in (message.image_urls or []) if str(item).strip()]
    recent_image_urls = await _load_recent_image_urls(
        session=session,
        user_id=user_id,
        conversation_id=conversation_id,
    )
    image_urls = current_image_urls[:]
    for image_ref in recent_image_urls:
        if image_ref not in image_urls:
            image_urls.append(image_ref)
    platform = message.platform or "unknown"
    tool_attempt_log: list[dict[str, Any]] = []
    audit_hook = _audit_hook_factory(user_id, platform, conversation_id, tool_attempt_log)

    _log(f"[main_agent] start user={user_id} images={len(image_urls)} content={content[:80]!r}")

    # ── Short-circuit: pending ledger state (receipt OCR / preview confirm) ──
    if not current_image_urls and conversation_id and await has_pending_ledger(user_id, int(conversation_id)):
        _log("[main_agent] short-circuit → ledger_pending")
        return await _handle_ledger_pending(state)

    # ── Short-circuit: pending schedule plan ──
    if not current_image_urls and _has_pending_reminder_plan(state):
        _log("[main_agent] short-circuit → schedule_pending")
        return await _handle_schedule_pending(state)

    if not current_image_urls:
        shortcut_reply = await _handle_profile_and_memory_shortcuts(
            session=session,
            user=user,
            content=content,
            user_id=user_id,
            platform=platform,
            conversation_id=conversation_id,
            image_urls=image_urls,
            audit_hook=audit_hook,
        )
        if shortcut_reply:
            _log("[main_agent] short-circuit → profile_or_memory")
            return {**state, "responses": [shortcut_reply]}

        multi_action_reply = await _handle_controlled_multi_action(
            content=content,
            user_id=user_id,
            platform=platform,
            conversation_id=conversation_id,
            image_urls=image_urls,
            audit_hook=audit_hook,
        )
        if multi_action_reply:
            _log("[main_agent] short-circuit → controlled_multi_action")
            return {**state, "responses": [multi_action_reply]}

    # ── Build tools & context ──
    t1 = time.monotonic()
    ctx = AgentToolContext(
        user_id=user_id,
        platform=platform,
        conversation_id=conversation_id,
        image_urls=image_urls,
    )
    image_analysis_text = ""
    if image_urls:
        _log("[main_agent] pre-analyze image")
        image_result = await invoke_node_tool_typed(
            context=ToolInvocationContext(
                user_id=user_id,
                platform=platform,
                conversation_id=conversation_id,
                image_urls=image_urls,
                audit_hook=audit_hook,
            ),
            node_name="main_agent",
            tool_name="analyze_image",
            args={
                "image_ref": image_urls[0],
                "question": content or "请概括图中主要内容，并识别图中的关键文字。",
            },
        )
        image_analysis_text = _render_image_analysis_context(image_result)
    tools = await build_node_langchain_tools(node_name="main_agent", user_id=user_id, session=session)

    context_text = render_conversation_context(state)
    skills = await load_skills(session=session, user_id=user_id, query=content)
    runtime_tools_summary = _format_tool_catalog_from_langchain(tools, user_id=user_id)

    t2 = time.monotonic()
    _log(f"[main_agent] context total: {(t2 - t1)*1000:.0f}ms")

    system_prompt = _build_system_prompt(
        user=user,
        context_text=context_text,
        skills=skills,
        runtime_tools_summary=runtime_tools_summary,
        image_count=len(image_urls),
        image_analysis_text=image_analysis_text,
    )

    effective_content = content or ("请根据图片内容继续处理用户请求。" if image_urls else "")

    # ── Create & stream agent ──
    agent = create_agent(
        model=get_llm(node_name=LLM_NODE_NAME),
        tools=tools,
        system_prompt=system_prompt,
        context_schema=AgentToolContext,
        name=f"main_agent_{user_id}_{conversation_id or 0}",
    )

    t3 = time.monotonic()

    # Use astream_events to capture tool call start/end events
    final_text = ""
    streamed_text_parts: list[str] = []
    streamer = get_llm_streamer()
    pending_tool_calls: dict[str, str] = {}  # call_id → tool_name
    _accumulated_tokens: dict[str, int] = {"prompt": 0, "completion": 0, "total": 0}

    audit_hook_token = set_tool_audit_hook(audit_hook)
    fetch_url_count_token = set_fetch_url_call_count(0)
    mcp_tool_call_count_token = set_mcp_tool_call_count(0)
    crawl_webpage_count_token = set_crawl_webpage_call_count(0)
    try:
        async for event in agent.astream_events(
            {
                "messages": [{"role": "user", "content": effective_content}],
            },
            context=ctx,
            config={"recursion_limit": 12},
            version="v2",
        ):
            kind = event.get("event", "")

            # Tool call start: LLM decided to call a tool
            if kind == "on_tool_start":
                tool_name = event.get("name", "")
                run_id = event.get("run_id", "")
                if tool_name:
                    pending_tool_calls[run_id] = tool_name
                    _log(f"[main_agent] tool_start: {tool_name}")
                    await _emit_tool_event(tool_name, "start")

            # Tool call end: tool returned result
            elif kind == "on_tool_end":
                run_id = event.get("run_id", "")
                tool_name = pending_tool_calls.pop(run_id, "")
                if tool_name:
                    _log(f"[main_agent] tool_end: {tool_name}")
                    await _emit_tool_event(tool_name, "done")

            # Stream LLM text tokens
            elif kind == "on_chat_model_stream":
                data = event.get("data", {})
                # data can be an AIMessageChunk or a dict with "chunk" key
                chunk_obj = data.get("chunk", data) if isinstance(data, dict) else data
                # Skip reasoning/thinking chunks (e.g. MiniMax, GLM)
                ak = getattr(chunk_obj, "additional_kwargs", None) or {}
                if ak.get("reasoning_content"):
                    continue
                if getattr(chunk_obj, "reasoning_content", None):
                    continue
                chunk_content = getattr(chunk_obj, "content", None)
                if isinstance(chunk_content, str) and chunk_content:
                    streamed_text_parts.append(chunk_content)
                    if streamer:
                        try:
                            await streamer(chunk_content)
                        except Exception:
                            pass

            # Capture final AI message
            elif kind == "on_chat_model_end":
                data = event.get("data", {})
                output = data.get("output", data) if isinstance(data, dict) else data
                # Extract token usage from each LLM turn
                _usage_meta = getattr(output, "usage_metadata", None)
                if isinstance(_usage_meta, dict):
                    _accumulated_tokens["prompt"] += _safe_int(_usage_meta.get("input_tokens"))
                    _accumulated_tokens["completion"] += _safe_int(_usage_meta.get("output_tokens"))
                    _accumulated_tokens["total"] += _safe_int(_usage_meta.get("total_tokens"))
                _resp_meta = getattr(output, "response_metadata", None)
                if isinstance(_resp_meta, dict):
                    for _ukey in ("token_usage", "usage"):
                        _tu = _resp_meta.get(_ukey)
                        if isinstance(_tu, dict):
                            _p = _safe_int(_tu.get("prompt_tokens")) or _safe_int(_tu.get("input_tokens"))
                            _c = _safe_int(_tu.get("completion_tokens")) or _safe_int(_tu.get("output_tokens"))
                            _t = _safe_int(_tu.get("total_tokens"))
                            if _t > 0:
                                _accumulated_tokens["prompt"] = max(_accumulated_tokens["prompt"], _p)
                                _accumulated_tokens["completion"] = max(_accumulated_tokens["completion"], _c)
                                _accumulated_tokens["total"] = max(_accumulated_tokens["total"], _t)
                msg_content = getattr(output, "content", None)
                if msg_content is None and isinstance(output, dict):
                    msg_content = output.get("content")
                if isinstance(msg_content, str) and msg_content.strip():
                    # Only capture if it has no tool_calls (= final response, not intermediate)
                    tool_calls = getattr(output, "tool_calls", None)
                    if not tool_calls:
                        final_text = msg_content.strip()
                elif isinstance(msg_content, list):
                    parts = []
                    for item in msg_content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            parts.append(item.get("text", ""))
                    joined = "\n".join(parts).strip()
                    if joined:
                        tool_calls = getattr(output, "tool_calls", None)
                        if not tool_calls:
                            final_text = joined

    except Exception as exc:
        _log(f"[main_agent] astream_events error: {exc}")
        if isinstance(exc, GraphRecursionError) or "recursion limit" in str(exc).lower():
            reason = _classify_failure_reason(attempt_log=tool_attempt_log, exception=exc)
            final_text = await _render_failure_message_with_llm(
                user_query=effective_content,
                attempt_log=tool_attempt_log,
                reason=reason,
            )
            streamed_text_parts.clear()
        else:
        # Fallback: try ainvoke
            result = await agent.ainvoke(
                {
                    "messages": [{"role": "user", "content": effective_content}],
                },
                context=ctx,
                config={"recursion_limit": 12},
            )
            if isinstance(result, dict):
                final_text = _extract_ai_text(result.get("messages") or [])
    finally:
        reset_tool_audit_hook(audit_hook_token)
        reset_fetch_url_call_count(fetch_url_count_token)
        reset_mcp_tool_call_count(mcp_tool_call_count_token)
        reset_crawl_webpage_call_count(crawl_webpage_count_token)

    t4 = time.monotonic()
    agent_ms = int((t4 - t3) * 1000)
    _log(f"[main_agent] LLM agent: {agent_ms}ms | tokens: prompt={_accumulated_tokens['prompt']} completion={_accumulated_tokens['completion']} total={_accumulated_tokens['total']}")

    # Enqueue accumulated token usage from astream_events (fallback for when
    # TrackingChatOpenAI.agenerate doesn't capture tokens in streaming mode)
    if _accumulated_tokens["total"] > 0:
        from app.services.usage import enqueue_llm_usage
        from app.core.config import get_settings as _get_settings
        enqueue_llm_usage(
            user_id=user_id,
            platform=platform,
            conversation_id=conversation_id,
            node="main_agent",
            model=_get_settings().openai_model,
            prompt_tokens=_accumulated_tokens["prompt"],
            completion_tokens=_accumulated_tokens["completion"],
            total_tokens=_accumulated_tokens["total"],
            latency_ms=agent_ms,
            success=True,
        )

    # Fallback: use streamed text if on_chat_model_end didn't capture
    if not final_text and streamed_text_parts:
        final_text = "".join(streamed_text_parts).strip()

    if final_text and tool_attempt_log and _needs_failure_rewrite(final_text=final_text, attempt_log=tool_attempt_log):
        reason = _classify_failure_reason(attempt_log=tool_attempt_log)
        final_text = await _render_failure_message_with_llm(
            user_query=effective_content,
            attempt_log=tool_attempt_log,
            reason=reason,
        )

    if not final_text:
        if tool_attempt_log:
            reason = _classify_failure_reason(attempt_log=tool_attempt_log)
            final_text = await _render_failure_message_with_llm(
                user_query=effective_content,
                attempt_log=tool_attempt_log,
                reason=reason,
            )
        else:
            final_text = "抱歉，我暂时无法回答。请换个方式描述你的需求。"

    _log(f"[main_agent] total: {(time.monotonic() - t0)*1000:.0f}ms")
    return {**state, "responses": [final_text]}
