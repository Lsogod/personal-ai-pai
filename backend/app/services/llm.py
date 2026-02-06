from langchain_openai import ChatOpenAI

from app.core.config import get_settings


def get_llm(model: str | None = None) -> ChatOpenAI:
    settings = get_settings()
    return ChatOpenAI(
        model=model or settings.openai_model,
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        temperature=0.2,
    )
