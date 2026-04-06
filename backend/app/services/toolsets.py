from __future__ import annotations

import json
from typing import Iterable

from langchain_core.tools import BaseTool
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.langchain_tools import ToolInvocationContext, build_langchain_tools
from app.services.tool_executor import execute_capability_with_usage
from app.services.user_mcp_tools import get_user_mcp_langchain_tools

# Shared tools usable across multiple nodes.
SHARED_TOOL_NAMES: set[str] = {
    "now_time",
}

WEB_TOOL_NAMES: set[str] = {
    "web_search",
}

VISION_TOOL_NAMES: set[str] = {
    "analyze_image",
}

# MCP-facing tool surface.
MCP_TOOL_NAMES: set[str] = {
    "maps_weather",
}

CONVERSATION_TOOL_NAMES: set[str] = {
    "conversation_current",
    "conversation_list",
    "memory_list",
    "memory_save",
    "memory_append",
    "memory_delete",
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
    "schedule_insert",
    "schedule_update",
    "schedule_delete",
    "schedule_get_latest",
    "schedule_list_recent",
    "schedule_list",
}

PROFILE_TOOL_NAMES: set[str] = {
    "update_user_profile",
    "query_user_profile",
}

# Main agent: single tool-calling agent with full tool access.
MAIN_AGENT_TOOL_NAMES: set[str] = (
    SHARED_TOOL_NAMES
    | WEB_TOOL_NAMES
    | VISION_TOOL_NAMES
    | MCP_TOOL_NAMES
    | CONVERSATION_TOOL_NAMES
    | LEDGER_TOOL_NAMES
    | SCHEDULE_TOOL_NAMES
    | PROFILE_TOOL_NAMES
)

# Node-scoped tool visibility. Nodes should consume tools from this registry
# instead of embedding ad-hoc tool name sets in node files.
NODE_TOOL_NAMES: dict[str, set[str]] = {
    "main_agent": MAIN_AGENT_TOOL_NAMES,
    # Short-circuit nodes (pending state handling in main_agent)
    "schedule_manager": SHARED_TOOL_NAMES | MCP_TOOL_NAMES | SCHEDULE_TOOL_NAMES,
    "ledger_manager": LEDGER_TOOL_NAMES,
}


def get_node_tool_names(node_name: str) -> set[str]:
    key = (node_name or "").strip().lower()
    base = NODE_TOOL_NAMES.get(key, set())
    return set(base)


async def build_node_langchain_tools(
    *,
    node_name: str,
    extra_tool_names: Iterable[str] | None = None,
    user_id: int | None = None,
    session: AsyncSession | None = None,
) -> list[BaseTool]:
    enabled = get_node_tool_names(node_name)
    if extra_tool_names:
        enabled.update(str(item).strip().lower() for item in extra_tool_names if str(item).strip())
    tools = build_langchain_tools(
        enabled_tool_names=enabled,
    )
    if user_id and session:
        try:
            user_mcp_tools = await get_user_mcp_langchain_tools(user_id, session)
            if user_mcp_tools:
                tools.extend(user_mcp_tools)
        except Exception:
            pass  # Don't block agent on user MCP failures
    return tools


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
    result = await invoke_node_tool_typed(
        context=context,
        node_name=node_name,
        tool_name=tool_name,
        args=args,
    )
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False)
    except Exception:
        return str(result)


def _resolve_tool_source(tool_name: str) -> str:
    target = (tool_name or "").strip().lower()
    if target in {"maps_weather", "bing_search", "crawl_webpage"}:
        return "mcp"
    return "builtin"


async def invoke_node_tool_typed(
    *,
    context: ToolInvocationContext,
    node_name: str,
    tool_name: str,
    args: dict | None = None,
):
    enabled = get_node_tool_names(node_name)
    target = (tool_name or "").strip()
    if not target or target.lower() not in enabled:
        return f"tool `{tool_name}` not available"

    payload = dict(args or {})
    source = _resolve_tool_source(target)
    result = await execute_capability_with_usage(
        source=source,
        name=target,
        args=payload,
        user_id=context.user_id,
        platform=context.platform,
        conversation_id=context.conversation_id,
    )
    ok = bool(result.get("ok"))
    output_text = str(result.get("output") or "")
    error_text = str(result.get("error") or "")
    latency_ms = int(result.get("latency_ms") or 0)
    if context.audit_hook is not None:
        await context.audit_hook(
            source,
            target,
            payload,
            ok,
            latency_ms,
            output_text,
            error_text,
        )
    if not ok:
        return error_text or f"tool `{target}` failed"

    output_data = result.get("output_data")
    if output_data is not None:
        return output_data
    if not output_text:
        return ""
    try:
        return json.loads(output_text)
    except Exception:
        return output_text
