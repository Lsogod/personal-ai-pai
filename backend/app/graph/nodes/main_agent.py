# -*- coding: utf-8 -*-
"""Single ReAct agent node that replaces the Router + chat_manager pipeline.

The main agent has access to ALL tools (ledger, schedule, profile, MCP,
conversation, memory) and decides autonomously which tools to call.
Pending multi-turn flows (receipt OCR confirmation, schedule plan
confirmation) are short-circuited before invoking the LLM to avoid
unnecessary latency.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.prebuilt import create_react_agent
from sqlalchemy import select

from app.graph.context import render_conversation_context
from app.graph.state import GraphState
from app.models.message import Message
from app.models.user import User
from app.services.langchain_tools import ToolInvocationContext
from app.services.ledger_pending import has_pending_ledger
from app.services.llm import get_llm
from app.services.runtime_context import get_session, get_llm_streamer
from app.services.skills import load_skills
from app.services.toolsets import build_node_langchain_tools

logger = logging.getLogger(__name__)

LLM_NODE_NAME = "main_agent"

# Tool name → user-friendly Chinese label
TOOL_DISPLAY_NAMES: dict[str, str] = {
    "now_time": "获取当前时间",
    "fetch_url": "抓取网页内容",
    "maps_weather": "查询天气",
    "mcp_list_tools": "查看外部工具",
    "mcp_call_tool": "调用外部工具",
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


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _log(msg: str) -> None:
    print(msg, flush=True)


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
) -> str:
    nickname = str(user.nickname or "").strip() or "用户"
    ai_name = str(user.ai_name or "").strip() or "AI 助手"
    ai_emoji = str(user.ai_emoji or "").strip()

    image_section = ""
    if image_count > 0:
        image_section = (
            "## 当前可用于记账识别的图片\n"
            f"- 当前消息或最近上下文中有 {image_count} 张图片可供记账识别。\n"
            "- 只有在用户要记账、补录账单、识别小票/支付截图金额时，才调用 analyze_receipt。\n"
            "- 不要把图片工具用于通用看图问答、描述图片内容或 OCR 问答。\n\n"
        )

    return (
        f"你是{nickname}的私人助理{ai_name} {ai_emoji}。\n"
        "你必须结合会话上下文连续对话，不要声称自己无法回忆当前会话。\n\n"

        "## 工具使用原则\n"
        "你具备工具调用能力，由你自主判断是否需要调用工具。\n"
        "- 时间查询：调用 now_time。\n"
        "- 天气查询：优先 maps_weather；避免用 fetch_url 替代。\n"
        "- 外部信息/网页抓取：调用 fetch_url。\n"
        "- 会话/记忆查询：调用 conversation_current / conversation_list / memory_list。\n"
        "- 当用户明确要求你记住某件事、某偏好、某规则、某长期约束时，调用 memory_save 直接写入长期记忆。\n"
        "- 当用户要求在现有记忆上补充信息时，先用 memory_list 找到目标，再调用 memory_append。\n"
        "- 当用户要求忘记、删除某条长期记忆时，先用 memory_list 找到目标，再调用 memory_delete。\n"
        "- 简单记账（如'午饭35元'）：直接调用 ledger_insert，"
        "分类参考：餐饮/交通/购物/娱乐/医疗/教育/居家/通讯/社交/服饰/其他。\n"
        "- 账单查询/修改/删除：使用 ledger_list / ledger_update / ledger_delete / ledger_text2sql。\n"
        "- 小票/支付截图仅用于记账：调用 analyze_receipt 获取结构化数据后调用 ledger_insert。\n"
        "- 创建提醒：先调用 now_time 获取当前时间，再计算绝对时间，调用 schedule_insert。"
        "trigger_time 格式：YYYY-MM-DD HH:MM:SS（服务器时区）。\n"
        "- 查看提醒：使用 schedule_list / schedule_list_recent / schedule_get_latest。\n"
        "- 修改/删除提醒：使用 schedule_update / schedule_delete。\n"
        "- 修改用户昵称(叫我xxx)：调用 update_user_profile(nickname=...)。\n"
        "- 修改助手名称(你叫xxx)：调用 update_user_profile(ai_name=...)。\n"
        "- 查询用户档案：调用 query_user_profile。\n"
        "- MCP 外部工具：先 mcp_list_tools 查看，再 mcp_call_tool 调用。\n\n"

        "## 限制\n"
        "- 最多调用 6 次工具；若连续失败，给出失败原因和建议。\n"
        "- 技能文档是写作/回答参考，不是可调用工具。\n"
        "- 不要暴露内部链路与调试信息。\n"
        "- 必须严格遵循用户请求的时间/数量范围。\n"
        "- 回答简洁、可执行、使用中文。\n\n"

        f"{image_section}"
        f"## 当前可用工具\n{runtime_tools_summary}\n\n"
        f"## 会话上下文\n{context_text}\n\n"
        f"## 技能文档\n{skills}"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_tool_catalog_from_langchain(tools: list) -> str:
    """Build tool catalog string from already-constructed LangChain tool objects."""
    if not tools:
        return "无可用工具。"
    names: list[str] = []
    for t in tools:
        name = getattr(t, "name", "")
        if name:
            names.append(name)
    if not names:
        return "无可用工具。"
    return "可用工具: " + ", ".join(sorted(names))


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

async def _emit_tool_event(name: str, status: str) -> None:
    """Push a tool_call SSE event through the streaming queue."""
    streamer = get_llm_streamer()
    if streamer is None:
        return
    label = TOOL_DISPLAY_NAMES.get(name, name)
    payload = json.dumps(
        {"tool_call": {"name": name, "label": label, "status": status}},
        ensure_ascii=False,
    )
    # Wrap in a special marker so the SSE layer can distinguish it from text chunks
    try:
        await streamer(f"\x00TOOL_EVENT:{payload}")
    except Exception:
        pass


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

    _log(f"[main_agent] start user={user_id} images={len(image_urls)} content={content[:80]!r}")

    # ── Short-circuit: pending ledger state (receipt OCR / preview confirm) ──
    if conversation_id and await has_pending_ledger(user_id, int(conversation_id)):
        _log("[main_agent] short-circuit → ledger_pending")
        return await _handle_ledger_pending(state)

    # ── Short-circuit: pending schedule plan ──
    if _has_pending_reminder_plan(state):
        _log("[main_agent] short-circuit → schedule_pending")
        return await _handle_schedule_pending(state)

    # ── Build tools & context ──
    t1 = time.monotonic()
    ctx = ToolInvocationContext(
        user_id=user_id,
        platform=platform,
        conversation_id=conversation_id,
        image_urls=image_urls,
        audit_hook=_audit_hook_factory(user_id, platform, conversation_id),
    )
    tools = build_node_langchain_tools(context=ctx, node_name="main_agent")

    context_text = render_conversation_context(state)
    skills = await load_skills(session=session, user_id=user_id, query=content)
    runtime_tools_summary = _format_tool_catalog_from_langchain(tools)

    t2 = time.monotonic()
    _log(f"[main_agent] context total: {(t2 - t1)*1000:.0f}ms")

    system_prompt = _build_system_prompt(
        user=user,
        context_text=context_text,
        skills=skills,
        runtime_tools_summary=runtime_tools_summary,
        image_count=len(image_urls),
    )

    # ── Create & stream ReAct agent ──
    agent = create_react_agent(
        model=get_llm(node_name=LLM_NODE_NAME),
        tools=tools,
        name=f"main_agent_{user_id}_{conversation_id or 0}",
    )

    t3 = time.monotonic()

    # Use astream_events to capture tool call start/end events
    final_text = ""
    streamed_text_parts: list[str] = []
    streamer = get_llm_streamer()
    pending_tool_calls: dict[str, str] = {}  # call_id → tool_name
    _accumulated_tokens: dict[str, int] = {"prompt": 0, "completion": 0, "total": 0}

    try:
        async for event in agent.astream_events(
            {
                "messages": [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=content),
                ]
            },
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
        # Fallback: try ainvoke
        result = await agent.ainvoke(
            {
                "messages": [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=content),
                ]
            },
            config={"recursion_limit": 12},
        )
        if isinstance(result, dict):
            final_text = _extract_ai_text(result.get("messages") or [])

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

    if not final_text:
        final_text = "抱歉，我暂时无法回答。请换个方式描述你的需求。"

    _log(f"[main_agent] total: {(time.monotonic() - t0)*1000:.0f}ms")
    return {**state, "responses": [final_text]}
