from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable


@dataclass
class ChatCommand:
    kind: str
    argument: str = ""


def parse_chat_command(content: str) -> ChatCommand | None:
    _ = content
    return None


async def execute_chat_command(
    *,
    command: ChatCommand,
    mcp_fetch_enabled: bool,
    on_mcp_list: Callable[[], Awaitable[str]],
    on_fetch: Callable[[], Awaitable[str]],
    on_weather: Callable[[str], Awaitable[str]],
) -> str | None:
    _ = command
    _ = mcp_fetch_enabled
    _ = on_mcp_list
    _ = on_fetch
    _ = on_weather
    return None
