"""Per-user MCP tool discovery, caching, and dynamic LangChain tool generation.

Cache strategy:
- In-memory dict keyed by user_id.
- Each entry stores a config_hash (SHA-256 of serialised server configs) and a
  monotonic expiry timestamp.
- On cache hit with matching hash and non-expired TTL → return cached tools.
- On config change (hash mismatch) or TTL expiry → re-discover.
- Active invalidation via invalidate_user_mcp_cache() called from CRUD endpoints.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, create_model
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_mcp_server import UserMcpServer
from app.services.mcp_fetch import MCPFetchClient, MCPFetchError

logger = logging.getLogger(__name__)

USER_MCP_CACHE_TTL_SEC = 300.0  # 5 minutes
USER_MCP_DISCOVERY_TIMEOUT_SEC = 15.0

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

@dataclass
class _CacheEntry:
    config_hash: str
    tools: list[BaseTool] = field(default_factory=list)
    tool_routing: dict[str, _ToolRoute] = field(default_factory=dict)
    expire_at: float = 0.0


@dataclass
class _ToolRoute:
    server_name: str
    server_url: str
    server_api_key: str
    original_name: str


_cache: dict[int, _CacheEntry] = {}
_cache_lock = asyncio.Lock()


def invalidate_user_mcp_cache(user_id: int) -> None:
    _cache.pop(user_id, None)


# ---------------------------------------------------------------------------
# Config hash
# ---------------------------------------------------------------------------

def _compute_config_hash(servers: list[UserMcpServer]) -> str:
    parts = []
    for s in sorted(servers, key=lambda s: s.id or 0):
        parts.append(f"{s.id}:{s.url}:{s.api_key}:{s.is_enabled}:{s.headers_json}:{s.env_json}")
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# JSON Schema → Pydantic model
# ---------------------------------------------------------------------------

_TYPE_MAP: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _json_schema_to_pydantic(tool_name: str, schema: dict[str, Any]) -> type[BaseModel]:
    properties = schema.get("properties") or {}
    required_set = set(schema.get("required") or [])
    fields: dict[str, Any] = {}
    for prop_name, prop_def in properties.items():
        py_type = _TYPE_MAP.get(str(prop_def.get("type", "string")), str)
        description = str(prop_def.get("description", ""))
        if prop_name in required_set:
            fields[prop_name] = (py_type, ...)
        else:
            default = prop_def.get("default")
            if default is None:
                default = "" if py_type is str else None
            fields[prop_name] = (py_type, default)
    if not fields:
        fields["__placeholder__"] = (str, "")
    model_name = f"UserMcp_{tool_name}_Input"
    return create_model(model_name, **fields)  # type: ignore[call-overload]


# ---------------------------------------------------------------------------
# Dynamic tool generation
# ---------------------------------------------------------------------------

def _make_user_mcp_tool(
    *,
    tool_def: dict[str, Any],
    server_name: str,
    server_url: str,
    server_api_key: str,
    server_id: int,
) -> tuple[BaseTool, _ToolRoute]:
    original_name = str(tool_def.get("name") or "").strip()
    description = str(tool_def.get("description") or "").strip() or "MCP tool"
    input_schema = tool_def.get("inputSchema") or {}

    # Namespace to avoid collisions
    safe_name = f"umcp_{server_id}_{original_name}"
    route = _ToolRoute(
        server_name=server_name,
        server_url=server_url,
        server_api_key=server_api_key,
        original_name=original_name,
    )

    args_model = _json_schema_to_pydantic(safe_name, input_schema)

    async def _run(**kwargs: Any) -> str:
        # Strip the placeholder field
        kwargs.pop("__placeholder__", None)
        client = MCPFetchClient(url=server_url, api_key=server_api_key or None)
        try:
            return await client.call_tool(name=original_name, arguments=kwargs)
        except MCPFetchError as exc:
            return f"MCP tool error: {exc}"

    tool = StructuredTool.from_function(
        func=None,
        coroutine=_run,
        name=safe_name,
        description=description,
        args_schema=args_model,
    )
    return tool, route


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

async def _discover_tools_for_server(
    server: UserMcpServer,
) -> list[dict[str, Any]]:
    if not server.url:
        return []

    api_key = (server.api_key or "").strip() or None

    # Build extra headers from server config
    extra_headers: dict[str, str] = {}
    try:
        headers_list = json.loads(server.headers_json or "[]")
        if isinstance(headers_list, list):
            for item in headers_list:
                if isinstance(item, dict) and item.get("key"):
                    extra_headers[item["key"]] = str(item.get("value", ""))
    except (json.JSONDecodeError, TypeError):
        pass

    client = MCPFetchClient(url=server.url, api_key=api_key)
    # Inject extra headers
    if extra_headers:
        client._http_headers.update(extra_headers)

    try:
        tools = await asyncio.wait_for(
            client.list_tools(),
            timeout=USER_MCP_DISCOVERY_TIMEOUT_SEC,
        )
        return tools if isinstance(tools, list) else []
    except Exception as exc:
        logger.warning(
            "user mcp discovery failed for %s (server %s): %s: %r",
            server.name,
            server.url,
            type(exc).__name__,
            exc,
        )
        return []


async def _discover_all(servers: list[UserMcpServer]) -> tuple[list[BaseTool], dict[str, _ToolRoute]]:
    tools: list[BaseTool] = []
    routing: dict[str, _ToolRoute] = {}
    seen_names: set[str] = set()

    tasks = []
    enabled_servers = [s for s in servers if s.is_enabled and s.url]
    for server in enabled_servers:
        tasks.append(_discover_tools_for_server(server))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    for server, result in zip(enabled_servers, results):
        if isinstance(result, Exception):
            logger.warning(
                "user mcp discovery error for %s: %s: %r",
                server.name,
                type(result).__name__,
                result,
            )
            continue
        if not isinstance(result, list):
            continue
        api_key = (server.api_key or "").strip()
        for tool_def in result:
            if not isinstance(tool_def, dict):
                continue
            name = str(tool_def.get("name") or "").strip()
            if not name:
                continue
            safe_name = f"umcp_{server.id}_{name}"
            if safe_name in seen_names:
                continue
            seen_names.add(safe_name)
            try:
                lc_tool, route = _make_user_mcp_tool(
                    tool_def=tool_def,
                    server_name=server.name,
                    server_url=server.url,
                    server_api_key=api_key,
                    server_id=server.id or 0,
                )
                tools.append(lc_tool)
                routing[safe_name] = route
            except Exception as exc:
                logger.warning(
                    "failed to build user mcp tool %s: %s: %r",
                    name,
                    type(exc).__name__,
                    exc,
                )

    return tools, routing


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def get_user_mcp_langchain_tools(
    user_id: int,
    session: AsyncSession,
) -> list[BaseTool]:
    """Return cached or freshly discovered LangChain tools for this user's MCP servers."""
    stmt = select(UserMcpServer).where(UserMcpServer.user_id == user_id)
    result = await session.execute(stmt)
    servers = list(result.scalars().all())

    if not servers:
        _cache.pop(user_id, None)
        return []

    config_hash = _compute_config_hash(servers)
    now = time.monotonic()

    # Fast path: cache hit
    entry = _cache.get(user_id)
    if entry and entry.config_hash == config_hash and now < entry.expire_at:
        return list(entry.tools)

    # Slow path: discover
    async with _cache_lock:
        # Double-check after acquiring lock
        entry = _cache.get(user_id)
        if entry and entry.config_hash == config_hash and now < entry.expire_at:
            return list(entry.tools)

        tools, routing = await _discover_all(servers)
        _cache[user_id] = _CacheEntry(
            config_hash=config_hash,
            tools=tools,
            tool_routing=routing,
            expire_at=time.monotonic() + USER_MCP_CACHE_TTL_SEC,
        )
        logger.info("user %d mcp tools discovered: %d tools from %d servers", user_id, len(tools), len(servers))
        return list(tools)


def get_user_mcp_tool_display_names(user_id: int) -> dict[str, str]:
    """Return tool_name → display label mapping for user MCP tools (from cache)."""
    entry = _cache.get(user_id)
    if not entry:
        return {}
    result: dict[str, str] = {}
    for tool in entry.tools:
        name = getattr(tool, "name", "")
        desc = getattr(tool, "description", "")
        route = entry.tool_routing.get(name)
        if route:
            server_name = str(route.server_name or "").strip()
            if server_name:
                result[name] = f"{server_name} / {route.original_name}"
            else:
                result[name] = f"MCP / {route.original_name}"
        else:
            result[name] = desc[:30] if desc else name
    return result
