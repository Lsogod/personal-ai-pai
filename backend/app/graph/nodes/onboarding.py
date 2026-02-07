import json
import re

from langchain_core.messages import HumanMessage, SystemMessage

from app.graph.context import render_conversation_context
from app.graph.state import GraphState
from app.models.user import SetupStage, User
from app.services.llm import get_llm
from app.services.runtime_context import get_session


EMOJI_PATTERN = re.compile(r"[\U0001F300-\U0001FAFF\U00002700-\U000027BF]")


def _extract_emoji(text: str) -> str | None:
    match = EMOJI_PATTERN.search(text)
    return match.group(0) if match else None


def _parse_json_object(content: str) -> dict:
    text = (content or "").strip()
    if not text:
        return {}
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


async def _extract_nickname_with_llm(raw: str, conversation_context: str) -> str:
    text = (raw or "").strip()
    if not text:
        return "主人"

    llm = get_llm()
    system = SystemMessage(
        content=(
            "你是字段提取器。请从用户消息中提取用户昵称。"
            "无论用户是自然语言还是'昵称: xxx'格式，都只返回昵称值。"
            "必须去掉语气和动词前缀，例如'叫我'、'我叫'、'我是'。"
            "示例1: 输入'昵称：叫我Lsogod'，输出 {\"nickname\":\"Lsogod\"}。"
            "示例2: 输入'我叫大卫'，输出 {\"nickname\":\"大卫\"}。"
            "只输出 JSON: {\"nickname\":\"...\"}，不要输出其他文本。"
        )
    )
    human = HumanMessage(
        content=(
            f"会话上下文:\n{conversation_context}\n\n"
            f"用户消息:\n{text}"
        )
    )

    try:
        response = await llm.ainvoke([system, human])
        data = _parse_json_object(str(response.content))
        nickname = str(data.get("nickname") or "").strip()
        if nickname:
            return nickname
    except Exception:
        pass

    return text.splitlines()[0].strip() or "主人"


async def _extract_ai_profile_with_llm(raw: str, conversation_context: str) -> tuple[str, str]:
    text = (raw or "").strip()
    if not text:
        return "PAI", "🤖"

    llm = get_llm()
    system = SystemMessage(
        content=(
            "你是字段提取器。请从用户消息中提取 AI 名称和 AI 表情。"
            "无论用户是自然语言还是'AI 名称: xxx'、'AI 表情: yyy'格式，都只返回值。"
            "AI 名称必须去掉语气和动词前缀，例如'叫你'、'你是'、'就叫'。"
            "示例: 输入'AI 名称：叫你贾维斯\\nAI 表情：👻'，输出 {\"ai_name\":\"贾维斯\",\"ai_emoji\":\"👻\"}。"
            "只输出 JSON: {\"ai_name\":\"...\",\"ai_emoji\":\"...\"}，不要输出其他文本。"
        )
    )
    human = HumanMessage(
        content=(
            f"会话上下文:\n{conversation_context}\n\n"
            f"用户消息:\n{text}"
        )
    )

    ai_name = ""
    ai_emoji = ""
    try:
        response = await llm.ainvoke([system, human])
        data = _parse_json_object(str(response.content))
        ai_name = str(data.get("ai_name") or "").strip()
        ai_emoji = str(data.get("ai_emoji") or "").strip()
    except Exception:
        pass

    emoji = _extract_emoji(ai_emoji) or _extract_emoji(text) or "🤖"
    if not ai_name:
        ai_name = text.replace(emoji, "").strip() or "PAI"
    return ai_name, emoji


async def _understand_binding_answer(raw: str, conversation_context: str) -> str:
    text = (raw or "").strip()
    if not text:
        return "unknown"
    llm = get_llm()
    system = SystemMessage(
        content=(
            "你是绑定引导意图解析器。只输出 JSON。"
            "字段: decision。"
            "decision 仅可为: has_account, no_account, continue, unknown。"
            "当用户表示在其他客户端已有账号时，decision=has_account。"
            "当用户表示没有其他账号时，decision=no_account。"
            "当用户表示继续下一步（如“继续”）时，decision=continue。"
            "无法确定时 decision=unknown。"
        )
    )
    human = HumanMessage(
        content=(
            f"会话上下文:\n{conversation_context}\n\n"
            f"用户输入:\n{text}"
        )
    )
    try:
        response = await llm.ainvoke([system, human])
        data = _parse_json_object(str(response.content))
        decision = str(data.get("decision") or "").strip().lower()
        if decision in {"has_account", "no_account", "continue", "unknown"}:
            return decision
    except Exception:
        pass
    return "unknown"


async def onboarding_node(state: GraphState) -> GraphState:
    message = state["message"]
    session = get_session()
    user = await session.get(User, state["user_id"])
    if not user:
        return {**state, "responses": ["未找到用户信息。"]}
    context_text = render_conversation_context(state)

    # Optional cross-platform account binding prompt for first-time users.
    if user.setup_stage == SetupStage.NEW and int(user.binding_stage or 0) == 0:
        user.binding_stage = 1
        session.add(user)
        await session.commit()
        return {
            **state,
            "responses": [
                "在其他客户端有账号吗？回复“有”或“没有”。有的话可稍后用 `/bind new` 与 `/bind <6位码>` 绑定数据。"
            ],
        }

    if user.setup_stage == SetupStage.NEW and int(user.binding_stage or 0) == 1:
        answer = (message.content or "").strip().lower()
        decision = await _understand_binding_answer(answer, context_text)
        yes_tokens = ("有", "有的", "有账号", "yes", "y")
        no_tokens = ("没有", "没", "无", "no", "n")
        continue_tokens = ("继续", "continue", "go on", "next")
        if decision in {"no_account", "continue"}:
            user.binding_stage = 2
            session.add(user)
            await session.commit()
        elif decision == "has_account":
            user.binding_stage = 2
            session.add(user)
            await session.commit()
            return {
                **state,
                "responses": [
                    "好的。你可以先在已有账号所在客户端发送 `/bind new` 获取6位绑定码，再回到这里发送 `/bind <code>`。完成后回复“继续”。"
                ],
            }
        elif any(token in answer for token in no_tokens) or any(token in answer for token in continue_tokens):
            user.binding_stage = 2
            session.add(user)
            await session.commit()
        elif any(token in answer for token in yes_tokens):
            user.binding_stage = 2
            session.add(user)
            await session.commit()
            return {
                **state,
                "responses": [
                    "好的。你可以先在已有账号所在客户端发送 `/bind new` 获取6位绑定码，再回到这里发送 `/bind <code>`。完成后回复“继续”。"
                ],
            }
        else:
            return {
                **state,
                "responses": [
                    "请回复“有”或“没有”。如果要立即绑定，也可直接使用 `/bind new` 或 `/bind <6位码>`。"
                ],
            }

    if user.setup_stage == SetupStage.NEW:
        user.setup_stage = SetupStage.USER_NAMED
        session.add(user)
        await session.commit()
        return {
            **state,
            "responses": ["你好！我是您的私人助理 PAI。初次见面，请问我该怎么称呼您？"],
        }

    if user.setup_stage == SetupStage.USER_NAMED:
        nickname = await _extract_nickname_with_llm(message.content or "", context_text)
        user.nickname = nickname
        user.setup_stage = SetupStage.AI_NAMED
        session.add(user)
        await session.commit()
        return {
            **state,
            "responses": [f"好的{nickname}，请给我起个名字（带上 Emoji 更好哦）？"],
        }

    if user.setup_stage == SetupStage.AI_NAMED:
        ai_name, emoji = await _extract_ai_profile_with_llm(message.content or "", context_text)
        user.ai_name = ai_name
        user.ai_emoji = emoji
        user.setup_stage = SetupStage.COMPLETED
        session.add(user)
        await session.commit()
        return {
            **state,
            "responses": [f"设置完成！我是{ai_name} {emoji}，随时待命。"],
        }

    return {**state, "responses": ["已完成设置，正在为您服务。"]}
