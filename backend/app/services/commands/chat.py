from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable


@dataclass
class ChatCommand:
    kind: str
    argument: str = ""


def parse_chat_command(content: str) -> ChatCommand | None:
    text = (content or "").strip()
    if not text.startswith("/"):
        return None
    lower = text.lower()
    if lower.startswith("/mcp list"):
        return ChatCommand(kind="mcp_list")
    if lower.startswith("/fetch"):
        return ChatCommand(kind="fetch")
    if lower.startswith("/weather"):
        return ChatCommand(kind="weather", argument=text[8:].strip())
    return None


async def execute_chat_command(
    *,
    command: ChatCommand,
    mcp_fetch_enabled: bool,
    on_mcp_list: Callable[[], Awaitable[str]],
    on_fetch: Callable[[], Awaitable[str]],
    on_weather: Callable[[str], Awaitable[str]],
) -> str | None:
    if command.kind == "mcp_list":
        if not mcp_fetch_enabled:
            return "系统级 MCP 未启用。"
        return await on_mcp_list()
    if command.kind == "fetch":
        if not mcp_fetch_enabled:
            return "系统级 MCP 未启用。"
        return await on_fetch()
    if command.kind == "weather":
        if not mcp_fetch_enabled:
            return "系统级 MCP 未启用。"
        return await on_weather(command.argument)
    return None

