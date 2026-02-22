from __future__ import annotations

from typing import Any

from app.graph.state import GraphState


def _normalize_text(value: Any, limit: int = 220) -> str:
    text = str(value or "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "..."


def render_conversation_context(
    state: GraphState,
    max_messages: int = 16,
    *,
    include_summary: bool = True,
    include_assistant_messages: bool = True,
    include_long_term_memories: bool = True,
) -> str:
    extra = state.get("extra") or {}
    summary = _normalize_text(extra.get("conversation_summary") or "", 300)
    raw_messages = extra.get("context_messages") or []
    raw_memories = extra.get("long_term_memories") or []

    lines: list[str] = []
    if include_summary and summary:
        lines.append(f"会话摘要: {summary}")

    normalized_messages: list[dict[str, str]] = []
    if isinstance(raw_messages, list):
        for item in raw_messages[-max_messages:]:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip().lower()
            content = _normalize_text(item.get("content") or "")
            if not content:
                continue
            if role not in {"user", "assistant", "system"}:
                role = "user"
            if not include_assistant_messages and role == "assistant":
                continue
            normalized_messages.append({"role": role, "content": content})

    if normalized_messages:
        lines.append("最近对话:")
        for item in normalized_messages:
            lines.append(f"- {item['role']}: {item['content']}")

    normalized_memories: list[str] = []
    if include_long_term_memories and isinstance(raw_memories, list):
        for item in raw_memories[:8]:
            if not isinstance(item, dict):
                continue
            memory_type = str(item.get("memory_type") or "fact").strip().lower()
            importance = item.get("importance")
            prefix = f"[{memory_type}]"
            if isinstance(importance, int):
                prefix = f"[{memory_type}|P{max(1, min(5, importance))}]"
            content = _normalize_text(item.get("content") or "", 180)
            if not content:
                continue
            normalized_memories.append(f"- {prefix} {content}")

    if normalized_memories:
        lines.append("长期记忆:")
        lines.extend(normalized_memories)

    if not lines:
        return "（当前会话暂无可用上下文）"
    return "\n".join(lines)
