from __future__ import annotations

import time
from typing import Any

from langchain_openai import ChatOpenAI

from app.core.config import get_settings
from app.services.runtime_context import (
    get_llm_stream_nodes,
    get_llm_streamer,
    get_tool_conversation_id,
    get_tool_platform,
    get_tool_user_id,
)
from app.services.usage import enqueue_llm_usage


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


def _is_reasoning_chunk(chunk: Any) -> bool:
    """Check if this chunk contains reasoning/thinking content (e.g. GLM5)."""
    # LangChain wraps additional_kwargs from the raw delta
    additional = getattr(chunk, "additional_kwargs", None)
    if isinstance(additional, dict) and additional.get("reasoning_content"):
        return True
    # Some providers put it directly on the chunk
    if getattr(chunk, "reasoning_content", None):
        return True
    return False


def _extract_stream_text(chunk: Any) -> str:
    # Skip reasoning/thinking tokens – don't send to user
    if _is_reasoning_chunk(chunk):
        return ""
    content = getattr(chunk, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts: list[str] = []
        for item in content:
            if isinstance(item, str):
                texts.append(item)
                continue
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    texts.append(text)
        return "".join(texts)
    if isinstance(chunk, str):
        return chunk
    return ""


class TrackingChatOpenAI(ChatOpenAI):
    """带用量自动追踪的 ChatOpenAI 包装。

    覆写 ainvoke 和 agenerate 两条路径：
    - ainvoke: 直接调用和 memory worker 等走此路径
    - agenerate: create_react_agent + astream_events 内部走此路径
    两者互不重复，各自独立追踪。
    """

    def __init__(self, *args, node_name: str = "unknown", **kwargs):
        super().__init__(*args, **kwargs)
        self._node_name = (node_name or "unknown").strip().lower() or "unknown"

    def _get_model_name(self) -> str:
        return str(getattr(self, "model_name", "") or getattr(self, "model", "") or "")

    def _is_stream_enabled_for_current_call(self) -> bool:
        streamer = get_llm_streamer()
        if streamer is None:
            return False
        allowed_nodes = get_llm_stream_nodes()
        if not allowed_nodes:
            return False
        return self._node_name in allowed_nodes

    def _enqueue(
        self,
        output: Any,
        started: float,
        *,
        success: bool = True,
        error: str = "",
    ) -> None:
        if success:
            prompt_tokens, completion_tokens, total_tokens = _extract_token_usage(output)
        else:
            prompt_tokens = completion_tokens = total_tokens = 0
        enqueue_llm_usage(
            user_id=get_tool_user_id(),
            platform=get_tool_platform() or "",
            conversation_id=get_tool_conversation_id(),
            node=self._node_name,
            model=self._get_model_name(),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            latency_ms=int((time.perf_counter() - started) * 1000),
            success=success,
            error=error,
        )

    async def _ainvoke_with_stream(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        streamer = get_llm_streamer()
        if streamer is None:
            return await super().ainvoke(input, config=config, **kwargs)

        full_output = None
        async for chunk in super().astream(input, config=config, **kwargs):
            text = _extract_stream_text(chunk)
            if text:
                try:
                    await streamer(text)
                except Exception:
                    pass
            if full_output is None:
                full_output = chunk
            else:
                try:
                    full_output = full_output + chunk
                except Exception:
                    full_output = chunk

        if full_output is not None:
            return full_output
        return await super().ainvoke(input, config=config, **kwargs)

    async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            if self._is_stream_enabled_for_current_call():
                output = await self._ainvoke_with_stream(input, config=config, **kwargs)
            else:
                output = await super().ainvoke(input, config=config, **kwargs)
            self._enqueue(output, started)
            return output
        except Exception as exc:
            self._enqueue(None, started, success=False, error=str(exc))
            raise

    async def agenerate(self, messages: Any, stop: Any = None, callbacks: Any = None, **kwargs: Any) -> Any:
        """覆写 agenerate 以追踪 create_react_agent 内部的 LLM 调用。"""
        started = time.perf_counter()
        try:
            result = await super().agenerate(messages, stop=stop, callbacks=callbacks, **kwargs)
            # agenerate returns LLMResult; extract token usage from llm_output
            llm_output = getattr(result, "llm_output", None) or {}
            token_usage = llm_output.get("token_usage", {}) if isinstance(llm_output, dict) else {}
            prompt_tokens = _safe_int(token_usage.get("prompt_tokens"))
            completion_tokens = _safe_int(token_usage.get("completion_tokens"))
            total_tokens = _safe_int(token_usage.get("total_tokens"))
            # Also try per-generation usage_metadata
            if total_tokens <= 0:
                for gen_list in getattr(result, "generations", []):
                    for gen in gen_list:
                        msg = getattr(gen, "message", None)
                        if msg is not None:
                            p, c, t = _extract_token_usage(msg)
                            prompt_tokens = max(prompt_tokens, p)
                            completion_tokens = max(completion_tokens, c)
                            total_tokens = max(total_tokens, t)
            enqueue_llm_usage(
                user_id=get_tool_user_id(),
                platform=get_tool_platform() or "",
                conversation_id=get_tool_conversation_id(),
                node=self._node_name,
                model=self._get_model_name(),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                latency_ms=int((time.perf_counter() - started) * 1000),
                success=True,
            )
            return result
        except Exception as exc:
            self._enqueue(None, started, success=False, error=str(exc))
            raise


def get_llm(model: str | None = None, node_name: str = "unknown") -> TrackingChatOpenAI:
    settings = get_settings()
    resolved_model = model or settings.openai_model

    return TrackingChatOpenAI(
        model=resolved_model,
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        temperature=0.2,
        max_tokens=16384,
        node_name=node_name,
    )
