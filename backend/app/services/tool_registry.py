from __future__ import annotations

from typing import Any
from typing import TypedDict

from app.core.config import get_settings
from app.services.admin_tools import load_tool_enabled_map, make_tool_key
from app.services.mcp_fetch import MCPFetchError, get_mcp_fetch_client


class ToolMeta(TypedDict):
    name: str
    source: str
    description: str
    enabled: bool


def get_allowed_mcp_tool_names() -> set[str]:
    raw = str(get_settings().mcp_allowed_tool_names or "").strip()
    if not raw:
        return set()
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def is_mcp_tool_allowed(name: str) -> bool:
    allowed = get_allowed_mcp_tool_names()
    tool_name = (name or "").strip().lower()
    if not tool_name:
        return False
    if not allowed:
        return True
    return tool_name in allowed


def filter_allowed_mcp_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in tools:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not is_mcp_tool_allowed(name):
            continue
        rows.append(item)
    return rows


def list_builtin_tool_metas() -> list[ToolMeta]:
    return [
        {
            "name": "now_time",
            "source": "builtin",
            "description": "按时区返回当前时间。",
            "enabled": True,
        },
        {
            "name": "fetch_url",
            "source": "builtin",
            "description": "通过 MCP fetch 抓取网页或 JSON 内容。",
            "enabled": True,
        },
        {
            "name": "mcp_list_tools",
            "source": "builtin",
            "description": "列出当前可用 MCP 工具。",
            "enabled": True,
        },
        {
            "name": "mcp_call_tool",
            "source": "builtin",
            "description": "按名称调用 MCP 工具（通用入口）。",
            "enabled": True,
        },
    ]


async def list_runtime_tool_metas() -> list[ToolMeta]:
    settings = get_settings()
    enabled_map = await load_tool_enabled_map()
    rows = list_builtin_tool_metas()
    for row in rows:
        key = make_tool_key(row["source"], row["name"])
        row["enabled"] = bool(enabled_map.get(key, True))
    if not settings.mcp_fetch_enabled:
        return rows
    try:
        mcp_tools = await get_mcp_fetch_client().list_tools()
    except Exception:
        return rows
    for item in filter_allowed_mcp_tools(mcp_tools):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        desc = str(item.get("description") or "").strip() or "无描述"
        rows.append(
            {
                "name": name,
                "source": "mcp",
                "description": desc,
                "enabled": bool(enabled_map.get(make_tool_key("mcp", name), True)),
            }
        )
    return rows
