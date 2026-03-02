# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
import time
from typing import Any
from typing import Literal
from urllib.parse import quote

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.db.session import AsyncSessionLocal
from app.graph.context import render_conversation_context
from app.graph.state import GraphState
from app.models.user import User
from app.services.audit import log_event
from app.services.llm import get_llm
from app.services.memory import deactivate_identity_memories_for_user
from app.services.runtime_context import get_session
from app.services.skills import load_skills
from app.services.langchain_tools import ToolInvocationContext
from app.services.toolsets import build_node_langchain_tools, invoke_node_tool
from app.services.tool_registry import list_runtime_tool_metas
from app.services.usage import log_tool_usage

VALID_CHAT_KINDS = {"general", "time", "external", "weather", "tooling", "unknown"}
INVALID_TOOL_ERROR_PATTERN = re.compile(
    r"(not a valid tool|invalid tool|工具不存在|未知工具|no such tool)",
    re.IGNORECASE,
)
PROFILE_HINT_PATTERN = re.compile(
    r"(叫我|我叫|称呼我|昵称|名字|我叫什么|你叫什么|你叫|AI名|ai名|assistant name|用户档案|个人档案|个人资料|profile)",
    re.IGNORECASE,
)
LLM_NODE_CLASSIFIER = "chat_manager_classifier"
LLM_NODE_TOOL_AGENT = "chat_manager_tool_agent"
LLM_NODE_FINAL = "chat_manager_final"


class ChatClassificationExtraction(BaseModel):
    kind: str = Field(default="unknown")
    tool_required: bool = Field(default=False)
    weather_location: str = Field(default="")
    confidence: float = Field(default=0.0)


class ProfileIntentExtraction(BaseModel):
    action: Literal[
        "none",
        "update_nickname",
        "update_ai_name",
        "update_ai_emoji",
        "update_ai_profile",
        "query_identity",
        "query_profile",
    ] = Field(default="none")
    nickname: str | None = Field(default="")
    ai_name: str | None = Field(default="")
    ai_emoji: str | None = Field(default="")
    ask_user_name: bool = Field(default=False)
    ask_ai_name: bool = Field(default=False)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


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


def _render_identity_reply(*, user: User, ask_user_name: bool, ask_ai_name: bool) -> str:
    lines: list[str] = []
    if ask_user_name:
        nickname = str(user.nickname or "").strip()
        if nickname:
            lines.append(f"你叫{nickname}。")
        else:
            lines.append("我这边还没有记录你的昵称，你可以说“以后叫我xxx”。")
    if ask_ai_name:
        ai_name = str(user.ai_name or "").strip() or "AI 助手"
        ai_emoji = str(user.ai_emoji or "").strip()
        suffix = f" {ai_emoji}" if ai_emoji else ""
        lines.append(f"我是{ai_name}{suffix}。")
    return "\n".join(lines).strip()


def _render_profile_reply(*, user: User) -> str:
    nickname = str(user.nickname or "").strip() or "未设置"
    ai_name = str(user.ai_name or "").strip() or "AI 助手"
    ai_emoji = str(user.ai_emoji or "").strip() or "🤖"
    platform = str(user.platform or "").strip() or "unknown"
    setup_stage = int(user.setup_stage or 0)
    email = str(user.email or "").strip() or "未绑定"
    return (
        "你的用户档案如下：\n"
        f"- 昵称：{nickname}\n"
        f"- 助手名称：{ai_name}\n"
        f"- 助手表情：{ai_emoji}\n"
        f"- 平台：{platform}\n"
        f"- 邮箱：{email}\n"
        f"- 引导阶段：{setup_stage}"
    )


async def _try_handle_profile_intent(
    *,
    session: Any,
    user: User,
    content: str,
    context_text: str,
) -> str | None:
    text = (content or "").strip()
    if not text:
        return None
    # Fast gate for latency: only invoke LLM when text likely mentions profile/name ops.
    if not PROFILE_HINT_PATTERN.search(text):
        return None

    llm = get_llm(node_name=LLM_NODE_CLASSIFIER)
    runnable = llm.with_structured_output(ProfileIntentExtraction)
    system = SystemMessage(
        content=(
            "你是用户档案意图识别器，只输出 JSON 结构化字段。"
            "字段: action, nickname, ai_name, ai_emoji, ask_user_name, ask_ai_name, confidence。"
            "action 仅可为: none, update_nickname, update_ai_name, update_ai_emoji, update_ai_profile, query_identity, query_profile。"
            "规则："
            "1) 用户表达“叫我/以后叫我/把我名字改成” => update_nickname，提取干净昵称。"
            "2) 用户表达“你叫/以后你叫/把你名字改成” => update_ai_name 或 update_ai_profile。"
            "3) 用户问“我叫什么/你叫什么” => query_identity，并设置 ask_user_name/ask_ai_name。"
            "4) 用户请求“输出我的用户档案/个人资料/profile” => query_profile。"
            "5) 不相关请求返回 action=none。"
            "6) 提取值必须去掉动词前缀，如“叫我”“我叫”“你叫”。"
            "不要输出解释文本。"
        )
    )
    human = HumanMessage(
        content=(
            f"当前档案: nickname={user.nickname}, ai_name={user.ai_name}, ai_emoji={user.ai_emoji}\n\n"
            f"会话上下文:\n{context_text}\n\n"
            f"用户消息:\n{text}"
        )
    )
    try:
        parsed = await runnable.ainvoke([system, human])
    except Exception:
        return None

    action = str(getattr(parsed, "action", "none") or "none").strip().lower()
    nickname = str(getattr(parsed, "nickname", "") or "").strip()
    ai_name = str(getattr(parsed, "ai_name", "") or "").strip()
    ai_emoji = str(getattr(parsed, "ai_emoji", "") or "").strip()
    ask_user_name = bool(getattr(parsed, "ask_user_name", False))
    ask_ai_name = bool(getattr(parsed, "ask_ai_name", False))

    if action == "query_identity":
        reply = _render_identity_reply(user=user, ask_user_name=ask_user_name, ask_ai_name=ask_ai_name)
        return reply or None

    if action == "query_profile":
        return _render_profile_reply(user=user)

    if action == "update_nickname" and nickname:
        if nickname != str(user.nickname or "").strip():
            user.nickname = nickname
            await deactivate_identity_memories_for_user(session, user_id=int(user.id or 0))
            session.add(user)
            await session.commit()
        return f"好的，已把你的称呼更新为{nickname}。"

    if action == "update_ai_name" and ai_name:
        if ai_name != str(user.ai_name or "").strip():
            user.ai_name = ai_name
            await deactivate_identity_memories_for_user(session, user_id=int(user.id or 0))
            session.add(user)
            await session.commit()
        emoji = str(user.ai_emoji or "").strip()
        suffix = f" {emoji}" if emoji else ""
        return f"好的，我的名字已更新为{ai_name}{suffix}。"

    if action == "update_ai_emoji" and ai_emoji:
        if ai_emoji != str(user.ai_emoji or "").strip():
            user.ai_emoji = ai_emoji
            await deactivate_identity_memories_for_user(session, user_id=int(user.id or 0))
            session.add(user)
            await session.commit()
        return f"好的，我的表情已更新为{ai_emoji}。"

    if action == "update_ai_profile" and (ai_name or ai_emoji):
        changed = False
        if ai_name and ai_name != str(user.ai_name or "").strip():
            user.ai_name = ai_name
            changed = True
        if ai_emoji and ai_emoji != str(user.ai_emoji or "").strip():
            user.ai_emoji = ai_emoji
            changed = True
        if changed:
            await deactivate_identity_memories_for_user(session, user_id=int(user.id or 0))
            session.add(user)
            await session.commit()
        final_name = str(user.ai_name or "").strip() or "AI 助手"
        final_emoji = str(user.ai_emoji or "").strip()
        suffix = f" {final_emoji}" if final_emoji else ""
        return f"好的，我的档案已更新：{final_name}{suffix}。"

    return None


async def _classify_chat_request_with_llm(
    *,
    content: str,
    context_text: str,
    runtime_tools: str,
) -> dict[str, Any]:
    llm = get_llm(node_name=LLM_NODE_CLASSIFIER)
    runnable = llm.with_structured_output(ChatClassificationExtraction)
    system = SystemMessage(
        content=(
            "你是 chat_manager 节点的请求分析器。请仅返回 JSON 结构化字段。"
            "字段: kind, tool_required, weather_location, confidence。"
            "kind 仅可为: general, time, external, weather, tooling, unknown。"
            "tool_required 必须是布尔值。"
            "当问题依赖实时信息、网页抓取、外部事实验证、MCP工具调用时，tool_required=true。"
            "当用户仅请求一般写作/润色/翻译/闲聊时，tool_required=false。"
            "当用户要求“使用某个技能/按某种风格写文案、写诗、翻译、改写”时，kind=general 且 tool_required=false。"
            "tooling 仅用于‘列出工具/如何调用工具/工具是否可用’等工具管理问题。"
            "weather_location 仅在 kind=weather 时填写城市名，否则留空字符串。"
            "confidence 范围 0~1。"
            "不要输出额外解释，只输出 JSON。"
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
    names: list[str] = []
    for item in tools:
        name = str(item.get("name") or "").strip()
        source = str(item.get("source") or "").strip()
        enabled = bool(item.get("enabled") is True)
        if not name:
            continue
        if not enabled:
            continue
        names.append(f"{source or 'unknown'}:{name}")
    if not names:
        return "无可用工具。"
    return "可用工具: " + ", ".join(sorted(dict.fromkeys(names)))


def _render_fetched_preview(source_url: str, fetched_markdown: str) -> str:
    text = (fetched_markdown or "").strip()
    if len(text) > 1200:
        text = text[:1200].rstrip() + "\n...(已截断)"
    return f"已抓取网页内容：{source_url}\n\n{text}"


async def _answer_with_fetched_content(
    *,
    user: User,
    content: str,
    context_text: str,
    skills: str,
    fetched_markdown: str,
    source_url: str,
) -> str:
    llm = get_llm(node_name=LLM_NODE_FINAL)
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
    llm = get_llm(node_name=LLM_NODE_FINAL)
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
    llm = get_llm(node_name=LLM_NODE_FINAL)
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
        model=get_llm(node_name=LLM_NODE_TOOL_AGENT),
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
        "你具备 LangGraph 工具调用能力。原则：由 LLM 自主判断是否需要工具并执行。\n"
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
    location = (weather_location or "").strip()
    if not location:
        return "请补充城市后我再查询天气，例如：武汉。"
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
    context_text = render_conversation_context(state)
    profile_reply = await _try_handle_profile_intent(
        session=session,
        user=user,
        content=content,
        context_text=context_text,
    )
    if profile_reply:
        return {**state, "responses": [profile_reply]}

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

    if tool_required and kind in {"external", "tooling"} and not tool_path_invalid_tool_error:
        return {
            **state,
            "responses": [
                "这个问题需要工具抓取实时/外部数据后才能可靠回答。"
                "请提供可抓取 URL，或允许我换一个公开来源继续。"
            ],
        }

    # Final plain-LLM fallback.
    llm = get_llm(node_name=LLM_NODE_FINAL)
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
