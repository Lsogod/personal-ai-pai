from langchain_core.messages import SystemMessage, HumanMessage

from app.graph.context import render_conversation_context
from app.graph.state import GraphState
from app.services.llm import get_llm
from app.services.skills import load_skills
from app.services.runtime_context import get_session
from app.models.user import User


async def writer_node(state: GraphState) -> GraphState:
    message = state["message"]
    session = get_session()
    user = await session.get(User, state["user_id"])
    if not user:
        return {**state, "responses": ["未找到用户信息。"]}

    context_text = render_conversation_context(state)
    skills = await load_skills(
        session=session,
        user_id=user.id,
        query=message.content or "",
    )
    system = SystemMessage(
        content=(
            f"你是{user.nickname}的私人助理{user.ai_name} {user.ai_emoji}。"
            "你必须结合会话上下文连续对话，不要声称自己无法回忆当前会话。\n"
            "根据技能文档完成写作、翻译、润色和一般问答请求。\n"
            f"会话上下文:\n{context_text}\n\n"
            f"技能文档:\n{skills}"
        )
    )
    human = HumanMessage(content=message.content or "")

    llm = get_llm()
    response = await llm.ainvoke([system, human])

    return {**state, "responses": [response.content]}
