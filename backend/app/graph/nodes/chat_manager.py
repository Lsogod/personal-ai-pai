# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.db.session import AsyncSessionLocal
from app.graph.context import render_conversation_context
from app.graph.state import GraphState
from app.models.user import User
from app.services.commands.chat import execute_chat_command, parse_chat_command
from app.services.audit import log_event
from app.services.llm import get_llm
from app.services.runtime_context import get_session
from app.services.skills import load_skills
from app.services.langchain_tools import ToolInvocationContext
from app.services.toolsets import build_node_langchain_tools, invoke_node_tool
from app.services.tool_registry import list_runtime_tool_metas
from app.services.usage import log_tool_usage

URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
VALID_CHAT_KINDS = {"general", "time", "external", "weather", "tooling", "unknown"}
IDENTITY_USER_PATTERN = re.compile(r"(我是谁|我叫什么|who\s+am\s+i)", re.IGNORECASE)
IDENTITY_ASSISTANT_PATTERN = re.compile(r"(你是谁|你叫什么|who\s+are\s+you)", re.IGNORECASE)
INVALID_TOOL_ERROR_PATTERN = re.compile(
    r"(not a valid tool|invalid tool|工具不存在|未知工具|no such tool)",
    re.IGNORECASE,
)


class ChatClassificationExtraction(BaseModel):
    kind: str = Field(default="unknown")
    tool_required: bool = Field(default=False)
    weather_location: str = Field(default="")
    confidence: float = Field(default=0.0)


def _shorten_text(value: str, limit: int = 500) -> str:
    text = (value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "...(truncated)"


async def _audit_tool_call(
    *,
    user_id: int | None,
    platform: str,
    conversation_id: int | None,
    tool_name: str,
    tool_source: str,
    arguments: dict[str, Any],
    ok: bool,
    latency_ms: int,
    result_preview: str = "",
    error: str = "",
) -> None:
    detail = {
        "tool_name": tool_name,
        "arguments": arguments,
        "ok": ok,
        "latency_ms": latency_ms,
        "conversation_id": conversation_id,
    }
    if result_preview:
        detail["result_preview"] = _shorten_text(result_preview, 800)
    if error:
        detail["error"] = _shorten_text(error, 800)
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
            tool_source=tool_source,
            tool_name=tool_name,
            success=ok,
            latency_ms=latency_ms,
            error=error,
        )
    except Exception:
        return


def _extract_first_url(text: str) -> str:
    match = URL_PATTERN.search(text or "")
    return match.group(0).strip() if match else ""


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        raw = value.strip().lower()
        if raw in {"true", "1", "yes", "y", "on"}:
            return True
        if raw in {"false", "0", "no", "n", "off"}:
            return False
    return default


async def _classify_chat_request_with_llm(
    *,
    content: str,
    context_text: str,
    runtime_tools: str,
) -> dict[str, Any]:
    llm = get_llm(node_name="chat_manager")
    runnable = llm.with_structured_output(ChatClassificationExtraction)
    system = SystemMessage(
        content=(
            "你是 chat_manager 节点的请求分析器。请仅返回结构化字段。"
            "字段: kind, tool_required, weather_location, confidence。"
            "kind 仅可为: general, time, external, weather, tooling, unknown。"
            "tool_required 必须是布尔值。"
            "当问题依赖实时信息、网页抓取、外部事实验证、MCP工具调用时，tool_required=true。"
            "当用户仅请求一般写作/润色/翻译/闲聊时，tool_required=false。"
            "当用户要求“使用某个技能/按某种风格写文案、写诗、翻译、改写”时，kind=general 且 tool_required=false。"
            "tooling 仅用于‘列出工具/如何调用工具/工具是否可用’等工具管理问题。"
            "weather_location 仅在 kind=weather 时填写城市名，否则留空字符串。"
            "confidence 范围 0~1。"
            "不要输出额外解释。"
        )
    )
    human = HumanMessage(
        content=(
            f"会话上下文:\n{context_text}\n\n"
            f"可用工具:\n{runtime_tools}\n\n"
            f"用户消息:\n{content}"
        )
    )
    try:
        parsed = await runnable.ainvoke([system, human])
    except Exception:
        parsed = ChatClassificationExtraction()

    kind = str(getattr(parsed, "kind", "") or "unknown").strip().lower()
    if kind not in VALID_CHAT_KINDS:
        kind = "unknown"
    weather_location = str(getattr(parsed, "weather_location", "") or "").strip()
    confidence_raw = getattr(parsed, "confidence", 0.0)
    try:
        confidence = float(confidence_raw)
    except Exception:
        confidence = 0.0
    if confidence < 0:
        confidence = 0.0
    if confidence > 1:
        confidence = 1.0
    return {
        "kind": kind,
        "tool_required": _coerce_bool(getattr(parsed, "tool_required", False), default=False),
        "weather_location": weather_location,
        "confidence": confidence,
    }


def _pick_timezone(content: str) -> str:
    text = (content or "").strip().lower()
    if any(tag in text for tag in ["london", "uk", "英国"]):
        return "Europe/London"
    if any(tag in text for tag in ["new york", "ny", "美国东部"]):
        return "America/New_York"
    if any(tag in text for tag in ["tokyo", "日本", "东京"]):
        return "Asia/Tokyo"
    return "Asia/Shanghai"


def _parse_json_object(text: str) -> dict[str, Any]:
    payload = (text or "").strip()
    if payload.startswith("```"):
        lines = payload.splitlines()
        if len(lines) >= 3:
            payload = "\n".join(lines[1:-1]).strip()
    try:
        result = json.loads(payload)
        return result if isinstance(result, dict) else {}
    except Exception:
        return {}


def _format_runtime_tool_catalog(tools: list[dict]) -> str:
    if not tools:
        return "无可用工具。"
    lines: list[str] = []
    for item in tools:
        name = str(item.get("name") or "").strip()
        source = str(item.get("source") or "").strip()
        description = str(item.get("description") or "").strip()
        enabled = bool(item.get("enabled") is True)
        if not name:
            continue
        lines.append(
            f"- [{source or 'unknown'}] {name} | enabled={str(enabled).lower()} | {description}"
        )
    return "\n".join(lines) or "无可用工具。"


def _render_fetched_preview(source_url: str, fetched_markdown: str) -> str:
    text = (fetched_markdown or "").strip()
    if len(text) > 1200:
        text = text[:1200].rstrip() + "\n...(已截断)"
    return f"已抓取网页内容：{source_url}\n\n{text}"


def _render_time_reply(content: str) -> str:
    tz = _pick_timezone(content)
    try:
        now = datetime.now(ZoneInfo(tz))
    except Exception:
        now = datetime.utcnow()
        tz = "UTC"
    return f"当前时间（{tz}）：{now.strftime('%Y-%m-%d %H:%M:%S')}"


def _detect_identity_query(content: str) -> tuple[bool, bool]:
    text = (content or "").strip()
    if not text:
        return False, False
    ask_user = bool(IDENTITY_USER_PATTERN.search(text))
    ask_assistant = bool(IDENTITY_ASSISTANT_PATTERN.search(text))
    return ask_user, ask_assistant


def _render_identity_reply(*, user: User, ask_user: bool, ask_assistant: bool) -> str:
    lines: list[str] = []
    if ask_user:
        nickname = str(user.nickname or "").strip()
        if nickname:
            lines.append(f"你叫{nickname}。")
        else:
            lines.append("我这边还没有记录你的昵称，你可以说“以后叫我xxx”。")
    if ask_assistant:
        ai_name = str(user.ai_name or "").strip() or "AI 助手"
        ai_emoji = str(user.ai_emoji or "").strip()
        suffix = f" {ai_emoji}" if ai_emoji else ""
        lines.append(f"我是{ai_name}{suffix}。")
    return "\n".join(lines).strip()


async def _answer_with_fetched_content(
    *,
    user: User,
    content: str,
    context_text: str,
    skills: str,
    fetched_markdown: str,
    source_url: str,
) -> str:
    llm = get_llm(node_name="chat_manager")
    system = SystemMessage(
        content=(
            f"你是{user.nickname}的私人助理{user.ai_name} {user.ai_emoji}。"
            "你必须结合会话上下文连续对话，不要声称自己无法回忆当前会话。\n"
            "你将基于网页抓取内容回答用户请求，要求准确、简洁。\n"
            "如果抓取内容不足以回答，要明确缺失点，并提示下一步。\n"
            f"会话上下文:\n{context_text}\n\n"
            f"技能文档:\n{skills}\n\n"
            f"抓取来源: {source_url}\n"
            f"抓取内容(markdown):\n{fetched_markdown}"
        )
    )
    human = HumanMessage(content=content or "")
    response = await llm.ainvoke([system, human])
    text = str(response.content or "").strip()
    return text or _render_fetched_preview(source_url, fetched_markdown)


async def _answer_weather_with_time_context(
    *,
    user: User,
    content: str,
    context_text: str,
    skills: str,
    now_time_text: str,
    weather_output: str,
    source_url: str,
) -> str:
    llm = get_llm(node_name="chat_manager")
    system = SystemMessage(
        content=(
            f"你是{user.nickname}的私人助理{user.ai_name} {user.ai_emoji}。"
            "你必须结合会话上下文连续对话，不要声称自己无法回忆当前会话。\n"
            "你将基于两份工具证据回答天气问题：now_time 与 maps_weather。\n"
            "请以 now_time 作为“今天”的时间基准，对天气日期进行相对判断（今天/明天/后天）。\n"
            "只允许依据提供的工具结果回答，禁止补充工具结果中不存在的事实。\n"
            "禁止输出“模拟/测试数据”“未来日期异常”“系统此前限制”等主观推断。\n"
            "如果某字段缺失，明确说“工具未返回该字段”。\n"
            "输出简洁中文，可给出 2~4 天预报要点。\n"
            f"会话上下文:\n{context_text}\n\n"
            f"技能文档:\n{skills}\n\n"
            f"now_time 结果:\n{now_time_text}\n\n"
            f"maps_weather 来源:\n{source_url}\n"
            f"maps_weather 原始结果:\n{weather_output}"
        )
    )
    human = HumanMessage(content=content or "")
    response = await llm.ainvoke([system, human])
    text = str(response.content or "").strip()
    if text:
        return text
    return (
        f"时间基准：{now_time_text}\n"
        f"天气来源：{source_url}\n"
        f"天气原始结果：\n{_shorten_text(weather_output, 2400)}"
    )


def _build_chat_tools(
    *,
    user_id: int | None,
    platform: str,
    conversation_id: int | None,
) -> list:
    return build_node_langchain_tools(
        context=ToolInvocationContext(
            user_id=user_id,
            platform=platform,
            conversation_id=conversation_id,
            audit_hook=_audit_tool_call_bridge(user_id, platform, conversation_id),
        ),
        node_name="chat_manager",
    )


async def _invoke_chat_tool(
    *,
    user_id: int | None,
    platform: str,
    conversation_id: int | None,
    tool_name: str,
    args: dict[str, Any] | None = None,
) -> str:
    return await invoke_node_tool(
        context=ToolInvocationContext(
            user_id=user_id,
            platform=platform,
            conversation_id=conversation_id,
            audit_hook=_audit_tool_call_bridge(user_id, platform, conversation_id),
        ),
        node_name="chat_manager",
        tool_name=tool_name,
        args=dict(args or {}),
    )


def _audit_tool_call_bridge(
    user_id: int | None,
    platform: str,
    conversation_id: int | None,
):
    async def _audit_bridge(
        source: str,
        name: str,
        args: dict[str, Any],
        ok: bool,
        latency_ms: int,
        output: str,
        error: str,
    ) -> None:
        await _audit_tool_call(
            user_id=user_id,
            platform=platform,
            conversation_id=conversation_id,
            tool_name=name,
            tool_source=source,
            arguments=args,
            ok=ok,
            latency_ms=latency_ms,
            result_preview=output,
            error=error,
        )

    return _audit_bridge


def _extract_ai_text_from_messages(messages: list[Any]) -> str:
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


def _stringify_message_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, str):
                if item.strip():
                    chunks.append(item.strip())
                continue
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    chunks.append(text.strip())
        return "\n".join(chunks).strip()
    return str(content or "").strip()


def _extract_tool_outputs(messages: list[Any]) -> list[str]:
    rows: list[str] = []
    for msg in messages or []:
        msg_type = str(getattr(msg, "type", "") or "").strip().lower()
        if msg_type != "tool":
            continue
        content = _stringify_message_content(getattr(msg, "content", ""))
        if content:
            rows.append(content)
    return rows


def _looks_like_invalid_tool_error(text: str) -> bool:
    return bool(INVALID_TOOL_ERROR_PATTERN.search((text or "").strip()))


async def _ground_answer_with_tool_outputs(
    *,
    user: User,
    content: str,
    context_text: str,
    skills: str,
    tool_outputs: list[str],
    draft_answer: str,
) -> str:
    llm = get_llm(node_name="chat_manager")
    merged = "\n\n---\n\n".join(tool_outputs)
    if len(merged) > 16000:
        merged = merged[:16000] + "\n...(工具输出已截断)"
    system = SystemMessage(
        content=(
            f"你是{user.nickname}的私人助理{user.ai_name} {user.ai_emoji}。"
            "你必须结合会话上下文连续对话，不要声称自己无法回忆当前会话。"
            "只允许依据本轮提供的工具输出作答，严禁补充工具输出中不存在的事实。"
            "若工具输出不足，必须明确说信息不足并给下一步建议。"
            "若用户要求特定网站但工具未成功抓到该站点，请明确说明这一点。"
            "禁止引用历史轮次信息和训练记忆补充事实。"
            "回答简洁、可执行。"
            f"工具输出:\\n{merged}\\n\\n"
            f"草稿回答(仅供参考，不可越过工具证据):\\n{draft_answer}"
        )
    )
    human = HumanMessage(content=content)
    response = await llm.ainvoke([system, human])
    text = _stringify_message_content(response.content)
    return text or draft_answer


async def _run_tool_agent(
    *,
    user: User,
    platform: str,
    conversation_id: int | None,
    content: str,
    context_text: str,
    skills: str,
    runtime_tools: str,
) -> tuple[str, int]:
    agent = create_react_agent(
        model=get_llm(node_name="chat_manager"),
        tools=_build_chat_tools(
            user_id=user.id,
            platform=platform,
            conversation_id=conversation_id,
        ),
        name=f"chat_tool_agent_{user.id}_{conversation_id or 0}",
    )
    system_prompt = (
        f"你是{user.nickname}的私人助理{user.ai_name} {user.ai_emoji}。"
        "你必须结合会话上下文连续对话，不要声称自己无法回忆当前会话。\n"
        "你具备 LangGraph 工具调用能力。原则：LLM 先判断是否需要工具，规则仅兜底。\n"
        "技能文档是写作/回答参考，不是可调用工具；严禁把 writer/translator/skill 名称当作 tool 调用。\n"
        "只能调用“当前可用工具目录”里出现的工具名。\n"
        "当问题依赖实时/外部信息时：\n"
        "1) 优先调用 mcp_list_tools 查看可用工具；\n"
        "2) 选择最匹配工具执行；\n"
        "3) 基于工具结果给出最终中文答案。\n"
        "若用户提到特定站点/品牌（例如 OpenAI），应优先抓取该官方站点；失败后再征求用户是否允许替代来源。\n"
        "最多调用 3 次工具；若连续失败，直接输出失败原因与下一步建议，不要盲目尝试无关站点。\n"
        "若用户要抓取网页但未给 URL：你可自行选择可公开抓取来源并调用 fetch_url。\n"
        "天气查询优先使用 MCP 天气工具（如 maps_weather）；时间查询优先调用 now_time。\n"
        "对天气类查询，不要用 fetch_url 代替 MCP 天气工具。\n"
        "若站点禁止抓取，请明确告知并给替代来源或让用户提供 URL。\n"
        "不要暴露内部链路与调试信息。\n"
        f"当前可用工具目录:\n{runtime_tools}\n\n"
        f"会话上下文:\n{context_text}\n\n"
        f"技能文档:\n{skills}"
    )
    result = await agent.ainvoke(
        {
            "messages": [
                SystemMessage(content=system_prompt),
                HumanMessage(content=content),
            ]
        },
        config={"recursion_limit": 8},
    )
    if isinstance(result, dict):
        messages = result.get("messages") or []
        text = _extract_ai_text_from_messages(messages)
        tool_outputs = _extract_tool_outputs(messages)
        tool_count = len(tool_outputs)
        if text and tool_outputs:
            text = await _ground_answer_with_tool_outputs(
                user=user,
                content=content,
                context_text=context_text,
                skills=skills,
                tool_outputs=tool_outputs,
                draft_answer=text,
            )
        if text:
            return text, tool_count
    return "", 0


async def _weather_fallback_fetch_and_answer(
    *,
    user: User,
    platform: str,
    conversation_id: int | None,
    content: str,
    context_text: str,
    skills: str,
    weather_location: str,
) -> str | None:
    location = (weather_location or "").strip() or "Wuhan"
    try:
        fetched = await _invoke_chat_tool(
            user_id=user.id,
            platform=platform,
            conversation_id=conversation_id,
            tool_name="maps_weather",
            args={"city": location},
        )
        if "not available" in fetched.lower() or "调用失败" in fetched:
            return None
        now_time_text = await _invoke_chat_tool(
            user_id=user.id,
            platform=platform,
            conversation_id=conversation_id,
            tool_name="now_time",
            args={"timezone": get_settings().timezone},
        )
        return await _answer_weather_with_time_context(
            user=user,
            content=content,
            context_text=context_text,
            skills=skills,
            now_time_text=now_time_text,
            weather_output=fetched,
            source_url=f"mcp:maps_weather?city={quote(location)}",
        )
    except Exception:
        return None


async def chat_manager_node(state: GraphState) -> GraphState:
    message = state["message"]
    session = get_session()
    user = await session.get(User, state["user_id"])
    if not user:
        return {**state, "responses": ["未找到用户信息。"]}

    content = (message.content or "").strip()
    ask_user, ask_assistant = _detect_identity_query(content)
    if ask_user or ask_assistant:
        return {
            **state,
            "responses": [_render_identity_reply(user=user, ask_user=ask_user, ask_assistant=ask_assistant)],
        }

    context_text = render_conversation_context(state)
    skills = await load_skills(
        session=session,
        user_id=user.id,
        query=content,
    )
    try:
        runtime_tool_rows = await list_runtime_tool_metas()
        runtime_tools = _format_runtime_tool_catalog(runtime_tool_rows)
    except Exception:
        runtime_tools = "无可用工具。"

    # Deterministic command fallback.
    parsed_cmd = parse_chat_command(content)
    if parsed_cmd is not None:
        settings = get_settings()

        async def _on_mcp_list() -> str:
            try:
                return await _invoke_chat_tool(
                    user_id=user.id,
                    platform=(message.platform or "unknown"),
                    conversation_id=state.get("conversation_id"),
                    tool_name="mcp_list_tools",
                )
            except Exception as exc:
                return f"MCP 工具列表获取失败：{exc}"

        async def _on_fetch() -> str:
            url = _extract_first_url(content)
            if not url:
                return "请提供要抓取的网址，例如：`/fetch https://example.com`。"
            try:
                fetched = await _invoke_chat_tool(
                    user_id=user.id,
                    platform=(message.platform or "unknown"),
                    conversation_id=state.get("conversation_id"),
                    tool_name="fetch_url",
                    args={"url": url},
                )
                return await _answer_with_fetched_content(
                    user=user,
                    content=content,
                    context_text=context_text,
                    skills=skills,
                    fetched_markdown=fetched,
                    source_url=url,
                )
            except Exception as exc:
                return f"网页抓取失败：{exc}"

        async def _on_weather(location_arg: str) -> str:
            location = location_arg or "Wuhan"
            try:
                fetched = await _invoke_chat_tool(
                    user_id=user.id,
                    platform=(message.platform or "unknown"),
                    conversation_id=state.get("conversation_id"),
                    tool_name="maps_weather",
                    args={"city": location},
                )
                if "not available" in fetched.lower() or "调用失败" in fetched:
                    return f"天气抓取失败：{fetched}"
                return await _answer_weather_with_time_context(
                    user=user,
                    content=content,
                    context_text=context_text,
                    skills=skills,
                    now_time_text=await _invoke_chat_tool(
                        user_id=user.id,
                        platform=(message.platform or "unknown"),
                        conversation_id=state.get("conversation_id"),
                        tool_name="now_time",
                        args={"timezone": get_settings().timezone},
                    ),
                    weather_output=fetched,
                    source_url=f"mcp:maps_weather?city={quote(location)}",
                )
            except Exception as exc:
                return f"天气抓取失败：{exc}"

        cmd_text = await execute_chat_command(
            command=parsed_cmd,
            mcp_fetch_enabled=settings.mcp_fetch_enabled,
            on_mcp_list=_on_mcp_list,
            on_fetch=_on_fetch,
            on_weather=_on_weather,
        )
        if cmd_text is not None:
            return {**state, "responses": [cmd_text]}

    try:
        classification = await _classify_chat_request_with_llm(
            content=content,
            context_text=context_text,
            runtime_tools=runtime_tools,
        )
    except Exception:
        classification = {
            "kind": "unknown",
            "tool_required": False,
            "weather_location": "",
            "confidence": 0.0,
        }
    kind = str(classification.get("kind") or "unknown")
    tool_required = bool(classification.get("tool_required"))
    weather_location = str(classification.get("weather_location") or "").strip()
    should_try_tools = tool_required or (kind in {"time", "external", "weather", "tooling"})
    tool_path_invalid_tool_error = False

    # Weather should be deterministic: call MCP weather first, avoid web-fetch fallback drift.
    platform = (message.platform or "unknown")
    conversation_id = state.get("conversation_id")
    if kind == "weather":
        weather_text = await _weather_fallback_fetch_and_answer(
            user=user,
            platform=platform,
            conversation_id=conversation_id,
            content=content,
            context_text=context_text,
            skills=skills,
            weather_location=weather_location,
        )
        if weather_text:
            return {**state, "responses": [weather_text]}
        return {
            **state,
            "responses": [
                "天气 MCP 工具当前不可用（超时或服务异常），本轮未改用网页抓取。"
                "请稍后重试，或检查 MCP 服务 URL/网络连通性。"
            ],
        }

    # LangGraph tool-call path.
    if should_try_tools:
        try:
            tool_answer, tool_count = await _run_tool_agent(
                user=user,
                platform=platform,
                conversation_id=conversation_id,
                content=content,
                context_text=context_text,
                skills=skills,
                runtime_tools=runtime_tools,
            )
            if _looks_like_invalid_tool_error(tool_answer):
                tool_path_invalid_tool_error = True
                tool_answer = ""
                tool_count = 0
            if tool_answer and (tool_count > 0 or not tool_required):
                return {**state, "responses": [tool_answer]}
        except Exception:
            pass

    # Rule fallback after LLM intent classification.
    if kind == "time":
        return {**state, "responses": [_render_time_reply(content)]}

    if tool_required and kind in {"external", "tooling"} and not tool_path_invalid_tool_error:
        return {
            **state,
            "responses": [
                "这个问题需要工具抓取实时/外部数据后才能可靠回答。"
                "请提供可抓取 URL，或允许我换一个公开来源继续。"
            ],
        }

    # Final plain-LLM fallback.
    llm = get_llm(node_name="chat_manager")
    system = SystemMessage(
        content=(
            f"你是{user.nickname}的私人助理{user.ai_name} {user.ai_emoji}。"
            "你必须结合会话上下文连续对话，不要声称自己无法回忆当前会话。"
            "根据技能文档完成写作、翻译、润色和一般问答请求。"
            f"\n会话上下文:\n{context_text}\n\n"
            f"技能文档:\n{skills}"
        )
    )
    human = HumanMessage(content=content)
    response = await llm.ainvoke([system, human])
    return {**state, "responses": [str(response.content)]}
