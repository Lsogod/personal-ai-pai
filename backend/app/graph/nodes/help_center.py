from __future__ import annotations

from pathlib import Path
import re

from langchain.messages import HumanMessage, SystemMessage

from app.graph.context import render_conversation_context
from app.graph.nodes.chat_manager import _try_handle_profile_intent
from app.graph.state import GraphState
from app.models.user import User
from app.services.llm import get_llm
from app.services.runtime_context import get_session
from app.services.skills import list_skills_with_source
from app.services.tool_registry import list_runtime_tool_metas


HELP_DOC_PATH = Path(__file__).resolve().parents[2] / "knowledge" / "AGENT_GUIDE.md"
IDENTITY_USER_PATTERN = re.compile(r"(我是谁|我叫什么|who\s+am\s+i)", re.IGNORECASE)
IDENTITY_ASSISTANT_PATTERN = re.compile(r"(你是谁|你叫什么|who\s+are\s+you)", re.IGNORECASE)


def _skill_status_label(value: str | None) -> str:
    key = str(value or "").upper()
    return {
        "BUILTIN": "内置",
        "DRAFT": "草稿",
        "PUBLISHED": "已发布",
        "DISABLED": "已停用",
    }.get(key, key or "未知")


def _load_help_doc() -> str:
    try:
        return HELP_DOC_PATH.read_text(encoding="utf-8").strip()
    except Exception:
        return (
            "PAI 使用说明缺失。你可以直接说需求：记账、提醒、日历、技能管理。"
            "若要命令示例，可输入 /help。"
        )


def _build_skill_context(skills: list[dict]) -> str:
    if not skills:
        return "无技能信息。"
    lines: list[str] = []
    for item in skills:
        source = str(item.get("source") or "")
        name = str(item.get("name") or item.get("slug") or "")
        slug = str(item.get("slug") or "")
        status = _skill_status_label(str(item.get("status") or ""))
        description = str(item.get("description") or "")
        lines.append(f"- [{source}] {name} ({slug}) | {status} | {description}")
    return "\n".join(lines)


def _build_tool_context(tools: list[dict]) -> str:
    if not tools:
        return "无可用工具信息。"
    lines: list[str] = []
    for item in tools:
        source = str(item.get("source") or "")
        name = str(item.get("name") or "")
        desc = str(item.get("description") or "")
        enabled = bool(item.get("enabled") is True)
        lines.append(f"- [{source}] {name} | enabled={str(enabled).lower()} | {desc}")
    return "\n".join(lines)


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


async def help_center_node(state: GraphState) -> GraphState:
    message = state["message"]
    content = (message.content or "").strip()
    platform = (message.platform or "").strip().lower()
    context_text = render_conversation_context(state)

    session = get_session()
    user = await session.get(User, state["user_id"])
    if not user:
        return {**state, "responses": ["未找到用户信息。"]}

    profile_reply = await _try_handle_profile_intent(
        session=session,
        user=user,
        content=content,
        context_text=context_text,
    )
    if profile_reply:
        return {**state, "responses": [profile_reply]}

    ask_user, ask_assistant = _detect_identity_query(content)
    if ask_user or ask_assistant:
        return {
            **state,
            "responses": [_render_identity_reply(user=user, ask_user=ask_user, ask_assistant=ask_assistant)],
        }

    skills = await list_skills_with_source(session, user.id)
    skill_context = _build_skill_context(skills)
    tool_context = _build_tool_context(await list_runtime_tool_metas())
    help_doc = _load_help_doc()

    llm = get_llm(node_name="help_center")
    system = SystemMessage(
        content=(
            f"你是{user.nickname}的私人助理{user.ai_name} {user.ai_emoji}，负责帮助与能力说明。"
            "你必须基于提供的《平台说明文档》与《当前用户技能上下文》回答。"
            "如果用户问到之前聊过什么，必须优先参考会话上下文作答。"
            "不要编造文档外功能。"
            "涉及命令时，只能使用以下命令族："
            "/new /history /switch /rename /delete /bind new /bind <6-digit-code> "
            "/skill(list|show|create|update|publish|disable|delete) /help。"
            "严禁输出不存在的命令（例如 /skill use、/ledger --limit、/mcp list、/fetch、/weather、/tool call）。"
            "记账/提醒/天气/抓取等场景优先给自然语言用法，不要强制用户记命令。"
            "按用户问题自动决定回答粒度："
            "1) 若用户问“怎么用/帮助/命令/教程/手册”，给结构化使用说明；"
            "2) 若用户问“你能做什么/有哪些功能”，给简洁能力清单；"
            "3) 其它导向类问题，给短引导并给1-2个可执行示例。"
            "避免整段照抄；请按问题选取相关内容。"
            f"当前平台: {platform or 'unknown'}。"
        )
    )
    human = HumanMessage(
        content=(
            f"《平台说明文档》:\n{help_doc}\n\n"
            f"《当前用户技能上下文》:\n{skill_context}\n\n"
            f"《当前可用工具上下文》:\n{tool_context}\n\n"
            f"《当前会话上下文》:\n{context_text}\n\n"
            f"用户提问:\n{content}"
        )
    )

    try:
        response = await llm.ainvoke([system, human])
        text = str(response.content).strip()
        if text:
            return {**state, "responses": [text]}
    except Exception:
        pass

    return {
        **state,
        "responses": [
            "你可以直接说目标，例如：`今天晚饭30元`、`明天中午12点提醒我开会`、`看下本周日程和账单`。"
        ],
    }
