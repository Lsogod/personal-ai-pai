from __future__ import annotations

from typing import Iterable

from langchain_core.tools import BaseTool

from app.services.langchain_tools import ToolInvocationContext, build_langchain_tools

# Shared tools usable across multiple nodes.
SHARED_TOOL_NAMES: set[str] = {
    "now_time",
    "fetch_url",
}

# MCP-facing tool surface.
MCP_TOOL_NAMES: set[str] = {
    "mcp_list_tools",
    "mcp_call_tool",
    "maps_weather",
}

LEDGER_TOOL_NAMES: set[str] = {
    "analyze_receipt",
    "ledger_text2sql",
    "ledger_insert",
    "ledger_update",
    "ledger_delete",
    "ledger_get_latest",
    "ledger_list_recent",
    "ledger_list",
}

SCHEDULE_TOOL_NAMES: set[str] = {
    "ledger_list",
    "schedule_insert",
    "schedule_update",
    "schedule_delete",
    "schedule_list",
}

# Node-scoped tool visibility. Nodes should consume tools from this registry
# instead of embedding ad-hoc tool name sets in node files.
NODE_TOOL_NAMES: dict[str, set[str]] = {
    "chat_manager": SHARED_TOOL_NAMES | MCP_TOOL_NAMES,
    "schedule_manager": SHARED_TOOL_NAMES | MCP_TOOL_NAMES | SCHEDULE_TOOL_NAMES,
    "ledger_manager": LEDGER_TOOL_NAMES,
}


def get_shared_tool_names() -> set[str]:
    return set(SHARED_TOOL_NAMES)


def get_mcp_tool_names() -> set[str]:
    return set(MCP_TOOL_NAMES)


def get_node_tool_names(node_name: str) -> set[str]:
    key = (node_name or "").strip().lower()
    base = NODE_TOOL_NAMES.get(key, set())
    return set(base)


def build_node_langchain_tools(
    *,
    context: ToolInvocationContext,
    node_name: str,
    extra_tool_names: Iterable[str] | None = None,
) -> list[BaseTool]:
    enabled = get_node_tool_names(node_name)
    if extra_tool_names:
        enabled.update(str(item).strip().lower() for item in extra_tool_names if str(item).strip())
    return build_langchain_tools(
        context=context,
        enabled_tool_names=enabled,
    )


def find_tool_by_name(tools: list[BaseTool], name: str) -> BaseTool | None:
    target = (name or "").strip()
    if not target:
        return None
    for item in tools:
        if getattr(item, "name", "") == target:
            return item
    return None


async def invoke_node_tool(
    *,
    context: ToolInvocationContext,
    node_name: str,
    tool_name: str,
    args: dict | None = None,
) -> str:
    tools = build_node_langchain_tools(
        context=context,
        node_name=node_name,
    )
    selected = find_tool_by_name(tools, tool_name)
    if selected is None:
        return f"tool `{tool_name}` not available"
    return str(await selected.ainvoke(dict(args or {})))
