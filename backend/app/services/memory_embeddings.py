from __future__ import annotations

from functools import lru_cache

from openai import AsyncOpenAI

from app.core.config import get_settings


@lru_cache
def _get_embeddings_client() -> AsyncOpenAI:
    settings = get_settings()
    return AsyncOpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
    )


async def embed_memory_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    settings = get_settings()
    response = await _get_embeddings_client().embeddings.create(
        model=settings.memory_embedding_model,
        input=texts,
        dimensions=max(1, int(settings.memory_embedding_dim or 1024)),
        encoding_format="float",
    )
    return [list(item.embedding or []) for item in response.data]


async def embed_memory_query(text: str) -> list[float]:
    settings = get_settings()
    response = await _get_embeddings_client().embeddings.create(
        model=settings.memory_embedding_model,
        input=text,
        dimensions=max(1, int(settings.memory_embedding_dim or 1024)),
        encoding_format="float",
    )
    if not response.data:
        return []
    return list(response.data[0].embedding or [])
