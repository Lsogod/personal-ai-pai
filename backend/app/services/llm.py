from __future__ import annotations

import time
from typing import Any

from langchain_openai import ChatOpenAI

from app.core.config import get_settings
from app.services.runtime_context import (
    get_tool_conversation_id,
    get_tool_platform,
    get_tool_user_id,
)
from app.services.usage import log_llm_usage


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def _extract_token_usage(output: Any) -> tuple[int, int, int]:
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0

    usage_metadata = getattr(output, "usage_metadata", None)
    if isinstance(usage_metadata, dict):
        prompt_tokens = _safe_int(usage_metadata.get("input_tokens"))
        completion_tokens = _safe_int(usage_metadata.get("output_tokens"))
        total_tokens = _safe_int(usage_metadata.get("total_tokens"))

    response_metadata = getattr(output, "response_metadata", None)
    if isinstance(response_metadata, dict):
        token_usage = response_metadata.get("token_usage")
        if isinstance(token_usage, dict):
            prompt_tokens = max(prompt_tokens, _safe_int(token_usage.get("prompt_tokens")))
            completion_tokens = max(completion_tokens, _safe_int(token_usage.get("completion_tokens")))
            total_tokens = max(total_tokens, _safe_int(token_usage.get("total_tokens")))
        # Some OpenAI-compatible providers return usage directly under response_metadata["usage"].
        usage = response_metadata.get("usage")
        if isinstance(usage, dict):
            prompt_tokens = max(
                prompt_tokens,
                _safe_int(usage.get("prompt_tokens")) or _safe_int(usage.get("input_tokens")),
            )
            completion_tokens = max(
                completion_tokens,
                _safe_int(usage.get("completion_tokens")) or _safe_int(usage.get("output_tokens")),
            )
            total_tokens = max(total_tokens, _safe_int(usage.get("total_tokens")))

    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens
    return prompt_tokens, completion_tokens, total_tokens


class TrackingChatOpenAI(ChatOpenAI):
    """带用量自动追踪的 ChatOpenAI 包装。

    当前代码库的所有调用路径（直接 ainvoke + create_react_agent 内部）
    最终都会经过 ainvoke，因此仅在 ainvoke 中做追踪即可。
    同步 invoke 做兜底防护，agenerate 不覆写以避免双计。
    """

    def __init__(self, *args, node_name: str = "unknown", **kwargs):
        super().__init__(*args, **kwargs)
        self._node_name = (node_name or "unknown").strip().lower() or "unknown"

    def _get_model_name(self) -> str:
        return str(getattr(self, "model_name", "") or getattr(self, "model", "") or "")

    async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        started = time.perf_counter()
        user_id = get_tool_user_id()
        platform = get_tool_platform() or ""
        conversation_id = get_tool_conversation_id()
        model_name = self._get_model_name()
        try:
            output = await super().ainvoke(input, config=config, **kwargs)
            prompt_tokens, completion_tokens, total_tokens = _extract_token_usage(output)
            await log_llm_usage(
                user_id=user_id,
                platform=platform,
                conversation_id=conversation_id,
                node=self._node_name,
                model=model_name,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                latency_ms=int((time.perf_counter() - started) * 1000),
                success=True,
            )
            return output
        except Exception as exc:
            await log_llm_usage(
                user_id=user_id,
                platform=platform,
                conversation_id=conversation_id,
                node=self._node_name,
                model=model_name,
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                latency_ms=int((time.perf_counter() - started) * 1000),
                success=False,
                error=str(exc),
            )
            raise


def get_llm(model: str | None = None, node_name: str = "unknown") -> TrackingChatOpenAI:
    settings = get_settings()
    return TrackingChatOpenAI(
        model=model or settings.openai_model,
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        temperature=0.2,
        node_name=node_name,
    )
