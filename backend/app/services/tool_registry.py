from __future__ import annotations

import asyncio
import time
from typing import Any
from typing import TypedDict

from app.core.config import get_settings
from app.services.admin_tools import load_tool_enabled_map, make_tool_key
from app.services.mcp_fetch import get_mcp_fetch_client


class ToolMeta(TypedDict):
    name: str
    source: str
    description: str
    enabled: bool


_RUNTIME_TOOL_CACHE_TTL_SEC_DEFAULT = 30.0
_runtime_tool_cache_lock = asyncio.Lock()
_runtime_tool_cache_rows: list[ToolMeta] | None = None
_runtime_tool_cache_expire_at: float = 0.0


def _parse_allowlist(raw: str) -> set[str]:
    text = str(raw or "").strip()
    if not text:
        return set()
    return {part.strip().lower() for part in text.split(",") if part.strip()}


def get_allowed_mcp_tool_names() -> set[str]:
    # Non-maps MCP tools.
    allowed = _parse_allowlist(get_settings().mcp_allowed_tool_names)
    return {
        name
        for name in allowed
        if not name.startswith("maps_") and not name.startswith("bing_") and name != "crawl_webpage"
    }


def get_allowed_mcp_search_tool_names() -> set[str]:
    return _parse_allowlist(get_settings().mcp_search_allowed_tool_names)


def get_allowed_mcp_maps_tool_names() -> set[str]:
    # maps_* MCP tools.
    return _parse_allowlist(get_settings().mcp_maps_allowed_tool_names)


def is_maps_mcp_tool(name: str) -> bool:
    return str(name or "").strip().lower().startswith("maps_")


def is_search_mcp_tool(name: str) -> bool:
    tool_name = str(name or "").strip().lower()
    return tool_name.startswith("bing_") or tool_name in {"crawl_webpage", "web_search_prime"}


def get_allowed_mcp_tool_names_for(name: str) -> set[str]:
    if is_maps_mcp_tool(name):
        return get_allowed_mcp_maps_tool_names()
    if is_search_mcp_tool(name):
        return get_allowed_mcp_search_tool_names()
    return get_allowed_mcp_tool_names()


def is_mcp_tool_allowed(name: str) -> bool:
    tool_name = (name or "").strip().lower()
    if not tool_name:
        return False
    allowed = get_allowed_mcp_tool_names_for(tool_name)
    if not allowed:
        return True
    return tool_name in allowed


def list_builtin_tool_metas() -> list[ToolMeta]:
    return [
        {
            "name": "now_time",
            "source": "builtin",
            "description": "按时区返回当前本地时间。",
            "enabled": True,
        },
        {
            "name": "web_search",
            "source": "builtin",
            "description": "统一联网查询工具：自动搜索、按需抓取正文，并返回结构化来源、摘要与状态。",
            "enabled": True,
        },
        {
            "name": "fetch_url",
            "source": "builtin",
            "description": "通过 MCP 或直连回退方式抓取网页或 JSON 内容；若长页面首段只有导航，可用 start_index 继续读取后续片段。",
            "enabled": True,
        },
        {
            "name": "tool_list",
            "source": "builtin",
            "description": "列出当前可用的外部工具。",
            "enabled": True,
        },
        {
            "name": "tool_call",
            "source": "builtin",
            "description": "按名称调用外部工具。",
            "enabled": True,
        },
        {
            "name": "analyze_receipt",
            "source": "builtin",
            "description": "分析小票或支付图片，并返回结构化字段。",
            "enabled": True,
        },
        {
            "name": "analyze_image",
            "source": "builtin",
            "description": "分析当前图片内容，适合回答图中是什么、图片里写了什么等问题。",
            "enabled": True,
        },
        {
            "name": "ledger_text2sql",
            "source": "builtin",
            "description": "通过受保护的 SQL 流程执行自然语言账单增删改查；适合复杂批量修改、删除或按范围查询。",
            "enabled": True,
        },
        {
            "name": "ledger_insert",
            "source": "builtin",
            "description": "创建一条账单记录；适合单笔、信息明确的简单记账。",
            "enabled": True,
        },
        {
            "name": "ledger_update",
            "source": "builtin",
            "description": "按 id 更新一条账单记录。",
            "enabled": True,
        },
        {
            "name": "ledger_delete",
            "source": "builtin",
            "description": "按 id 删除一条账单记录。",
            "enabled": True,
        },
        {
            "name": "ledger_get_latest",
            "source": "builtin",
            "description": "获取最新一条账单记录。",
            "enabled": True,
        },
        {
            "name": "ledger_list_recent",
            "source": "builtin",
            "description": "列出最近几条账单记录；不适用于今天/本月/指定日期等时间范围查询。",
            "enabled": True,
        },
        {
            "name": "ledger_list",
            "source": "builtin",
            "description": "按日期范围、分类、摘要或指定 id 列出账单记录；今天/本周/本月等时间范围查询优先使用它。",
            "enabled": True,
        },
        {
            "name": "conversation_current",
            "source": "builtin",
            "description": "获取当前激活会话。",
            "enabled": True,
        },
        {
            "name": "conversation_list",
            "source": "builtin",
            "description": "列出带有激活标记的会话记录。",
            "enabled": True,
        },
        {
            "name": "memory_list",
            "source": "builtin",
            "description": "列出当前用户的长期记忆。",
            "enabled": True,
        },
        {
            "name": "memory_save",
            "source": "builtin",
            "description": "将用户明确要求记住的信息直接写入长期记忆。",
            "enabled": True,
        },
        {
            "name": "memory_append",
            "source": "builtin",
            "description": "向已有长期记忆追加信息。",
            "enabled": True,
        },
        {
            "name": "memory_delete",
            "source": "builtin",
            "description": "删除一条已有长期记忆。",
            "enabled": True,
        },
        {
            "name": "schedule_insert",
            "source": "builtin",
            "description": "创建一条日程或提醒记录，并安排触发任务。",
            "enabled": True,
        },
        {
            "name": "schedule_update",
            "source": "builtin",
            "description": "更新一条日程或提醒记录。",
            "enabled": True,
        },
        {
            "name": "schedule_delete",
            "source": "builtin",
            "description": "删除一条日程或提醒记录。",
            "enabled": True,
        },
        {
            "name": "schedule_list",
            "source": "builtin",
            "description": "按 id、状态、时间窗口、内容等条件列出日程或提醒记录。",
            "enabled": True,
        },
    ]


def _candidate_mcp_urls() -> list[str]:
    settings = get_settings()
    rows: list[str] = []
    default_url = str(settings.mcp_fetch_url or "").strip()
    search_url = str(settings.mcp_search_url or "").strip()
    search_fallback_url = str(settings.mcp_search_fallback_url or "").strip()
    maps_url = str(settings.mcp_maps_url or "").strip()
    if default_url:
        rows.append(default_url)
    if search_url and search_url not in rows:
        rows.append(search_url)
    if search_fallback_url and search_fallback_url not in rows:
        rows.append(search_fallback_url)
    if maps_url and maps_url not in rows:
        rows.append(maps_url)
    return rows


def _api_key_for_mcp_url(url: str) -> str | None:
    settings = get_settings()
    target = str(url or "").strip()
    if not target:
        return None
    if target == str(settings.mcp_search_url or "").strip():
        return str(settings.mcp_search_api_key or "").strip() or None
    if target == str(settings.mcp_search_fallback_url or "").strip():
        return str(settings.mcp_search_fallback_api_key or "").strip() or None
    if target == str(settings.mcp_fetch_url or "").strip():
        return str(settings.mcp_fetch_api_key or "").strip() or None
    return None


async def list_runtime_tool_metas() -> list[ToolMeta]:
    global _runtime_tool_cache_rows, _runtime_tool_cache_expire_at
    now = time.monotonic()
    if _runtime_tool_cache_rows is not None and now < _runtime_tool_cache_expire_at:
        return [dict(item) for item in _runtime_tool_cache_rows]

    async with _runtime_tool_cache_lock:
        now = time.monotonic()
        if _runtime_tool_cache_rows is not None and now < _runtime_tool_cache_expire_at:
            return [dict(item) for item in _runtime_tool_cache_rows]

        rows = await _list_runtime_tool_metas_uncached()
        _runtime_tool_cache_rows = [dict(item) for item in rows]
        ttl = float(get_settings().runtime_tool_cache_ttl_sec or _RUNTIME_TOOL_CACHE_TTL_SEC_DEFAULT)
        if ttl <= 0:
            ttl = _RUNTIME_TOOL_CACHE_TTL_SEC_DEFAULT
        _runtime_tool_cache_expire_at = now + ttl
        return [dict(item) for item in _runtime_tool_cache_rows]


async def warm_runtime_tool_cache() -> None:
    try:
        await list_runtime_tool_metas()
    except Exception:
        return


async def _list_runtime_tool_metas_uncached() -> list[ToolMeta]:
    settings = get_settings()
    enabled_map = await load_tool_enabled_map()
    rows = list_builtin_tool_metas()
    for row in rows:
        key = make_tool_key(row["source"], row["name"])
        row["enabled"] = bool(enabled_map.get(key, True))
    if not settings.mcp_fetch_enabled:
        return rows

    all_mcp_tools: list[dict[str, Any]] = []
    for url in _candidate_mcp_urls():
        try:
            tools = await get_mcp_fetch_client(url=url, api_key=_api_key_for_mcp_url(url)).list_tools()
        except Exception:
            continue
        if isinstance(tools, list):
            all_mcp_tools.extend(tools)

    seen_names: set[str] = set()
    for item in all_mcp_tools:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        if not is_mcp_tool_allowed(name):
            continue
        normalized = name.lower()
        if normalized in seen_names:
            continue
        seen_names.add(normalized)
        desc = str(item.get("description") or "").strip() or "no description"
        rows.append(
            {
                "name": name,
                "source": "mcp",
                "description": desc,
                "enabled": bool(enabled_map.get(make_tool_key("mcp", name), True)),
            }
        )
    return rows
