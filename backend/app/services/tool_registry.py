from __future__ import annotations

from typing import TypedDict

from app.core.config import get_settings
from app.db.session import AsyncSessionLocal
from app.services.customization import get_user_tool_policy_map, merge_tool_catalog_with_policy
from app.services.mcp_fetch import MCPFetchError, get_mcp_fetch_client


class ToolMeta(TypedDict):
    name: str
    source: str
    description: str
    enabled: bool


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


async def list_runtime_tool_metas(
    *,
    user_id: int | None = None,
    include_disabled: bool = False,
) -> list[ToolMeta]:
    settings = get_settings()
    rows = list_builtin_tool_metas()
    if not settings.mcp_fetch_enabled:
        if not user_id:
            return rows
    else:
        try:
            mcp_tools = await get_mcp_fetch_client().list_tools()
        except MCPFetchError:
            mcp_tools = []
        for item in mcp_tools:
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
                    "enabled": True,
                }
            )

    if not user_id:
        return rows

    async with AsyncSessionLocal() as session:
        policy_map = await get_user_tool_policy_map(session, user_id)
    merged = merge_tool_catalog_with_policy(catalog=rows, policy_map=policy_map)
    if include_disabled:
        return [ToolMeta(**row) for row in merged]
    return [ToolMeta(**row) for row in merged if bool(row.get("enabled"))]
