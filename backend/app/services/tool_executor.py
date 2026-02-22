from __future__ import annotations

import time
from datetime import datetime
from typing import Any, TypedDict
from zoneinfo import ZoneInfo

from app.core.config import get_settings
from app.services.admin_tools import is_tool_enabled
from app.services.mcp_fetch import get_mcp_client_for_tool, get_mcp_fetch_client
from app.services.tool_registry import (
    get_allowed_mcp_tool_names_for,
    is_mcp_tool_allowed,
    list_runtime_tool_metas,
)
from app.services.usage import log_tool_usage


class ToolExecResult(TypedDict):
    ok: bool
    source: str
    name: str
    output: str
    error: str
    latency_ms: int


BUILTIN_TOOL_ALIAS: dict[str, str] = {
    "mcp_list_tools": "tool_list",
    "mcp_call_tool": "tool_call",
}


def _render_now_time(timezone: str) -> str:
    tz = (timezone or "").strip() or "Asia/Shanghai"
    try:
        now = datetime.now(ZoneInfo(tz))
        return f"{tz} current time: {now.strftime('%Y-%m-%d %H:%M:%S')}"
    except Exception:
        now = datetime.utcnow()
        return f"UTC current time: {now.strftime('%Y-%m-%d %H:%M:%S')}"


def _render_mcp_tool_rows(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No external tools available."
    lines: list[str] = []
    for item in rows:
        name = str(item.get("name") or "").strip() or "unknown"
        desc = str(item.get("description") or "").strip() or "no description"
        enabled = bool(item.get("enabled") is True)
        lines.append(f"- {name} | enabled={str(enabled).lower()} | {desc}")
    return "\n".join(lines)


async def _log_tool_usage_safe(
    *,
    user_id: int | None,
    platform: str,
    conversation_id: int | None,
    tool_source: str,
    tool_name: str,
    success: bool,
    latency_ms: int,
    error: str = "",
) -> None:
    try:
        await log_tool_usage(
            user_id=user_id,
            platform=platform,
            conversation_id=conversation_id,
            tool_source=tool_source,
            tool_name=tool_name,
            success=success,
            latency_ms=latency_ms,
            error=error,
        )
    except Exception:
        return


async def execute_capability(
    *,
    source: str,
    name: str,
    args: dict[str, Any] | None = None,
    user_id: int | None = None,
    platform: str = "",
    conversation_id: int | None = None,
) -> ToolExecResult:
    started = time.perf_counter()
    src = str(source or "").strip().lower()
    raw_tool = str(name or "").strip()
    tool_l = raw_tool.lower()
    tool_l = BUILTIN_TOOL_ALIAS.get(tool_l, tool_l)
    tool = tool_l
    params = dict(args or {})
    settings = get_settings()

    def _result(ok: bool, output: str = "", error: str = "") -> ToolExecResult:
        latency_ms = int((time.perf_counter() - started) * 1000)
        return {
            "ok": ok,
            "source": src,
            "name": tool,
            "output": output if ok else "",
            "error": error if not ok else "",
            "latency_ms": latency_ms,
        }

    if src not in {"builtin", "mcp"}:
        return _result(False, error=f"unsupported tool source: {src}")

    if not tool_l:
        return _result(False, error="missing tool name")

    try:
        if src == "builtin":
            if not await is_tool_enabled("builtin", tool_l):
                return _result(False, error=f"tool `{tool_l}` is disabled by admin.")

            if tool_l == "now_time":
                timezone = str(params.get("timezone") or settings.timezone or "Asia/Shanghai").strip()
                return _result(True, output=_render_now_time(timezone))

            if tool_l == "fetch_url":
                if not settings.mcp_fetch_enabled:
                    return _result(False, error="MCP fetch is disabled.")
                target = str(params.get("url") or "").strip()
                if not target:
                    return _result(False, error="missing required arg: url")
                max_length = max(500, min(20000, int(params.get("max_length") or settings.mcp_fetch_default_max_length)))
                start_index = max(0, int(params.get("start_index") or 0))
                raw = bool(params.get("raw"))
                output = await get_mcp_fetch_client().fetch(
                    url=target,
                    max_length=max_length,
                    start_index=start_index,
                    raw=raw,
                )
                return _result(True, output=output)

            if tool_l == "tool_list":
                if not settings.mcp_fetch_enabled:
                    return _result(False, error="MCP fetch is disabled.")
                runtime_tools = await list_runtime_tool_metas()
                mcp_tools = [dict(item) for item in runtime_tools if str(item.get("source") or "") == "mcp"]
                return _result(True, output=_render_mcp_tool_rows(mcp_tools))

            if tool_l == "tool_call":
                if not settings.mcp_fetch_enabled:
                    return _result(False, error="MCP fetch is disabled.")
                target_name = str(params.get("tool_name") or params.get("name") or "").strip()
                if not target_name:
                    return _result(False, error="missing required arg: tool_name")
                if not is_mcp_tool_allowed(target_name):
                    allowed = sorted(get_allowed_mcp_tool_names_for(target_name))
                    allowed_text = ", ".join(allowed) if allowed else "none"
                    return _result(False, error=f"MCP tool `{target_name}` is blocked by allowlist. Allowed tools: {allowed_text}.")
                if not await is_tool_enabled("mcp", target_name):
                    return _result(False, error=f"MCP tool `{target_name}` is disabled by admin.")
                target_args = params.get("arguments")
                if not isinstance(target_args, dict):
                    target_args = {}
                output = await get_mcp_client_for_tool(target_name).call_tool(name=target_name, arguments=target_args)
                return _result(True, output=output)

            return _result(False, error=f"unsupported builtin tool: {tool_l}")

        # src == "mcp"
        target_name = tool.strip()
        target_norm = target_name.lower()
        if not settings.mcp_fetch_enabled:
            return _result(False, error="MCP fetch is disabled.")
        if not is_mcp_tool_allowed(target_norm):
            allowed = sorted(get_allowed_mcp_tool_names_for(target_norm))
            allowed_text = ", ".join(allowed) if allowed else "none"
            return _result(False, error=f"MCP tool `{target_name}` is blocked by allowlist. Allowed tools: {allowed_text}.")
        if not await is_tool_enabled("mcp", target_norm):
            return _result(False, error=f"MCP tool `{target_name}` is disabled by admin.")
        output = await get_mcp_client_for_tool(target_name).call_tool(name=target_name, arguments=params)
        return _result(True, output=output)

    except Exception as exc:
        return _result(False, error=str(exc))


async def execute_capability_with_usage(
    *,
    source: str,
    name: str,
    args: dict[str, Any] | None = None,
    user_id: int | None = None,
    platform: str = "",
    conversation_id: int | None = None,
) -> ToolExecResult:
    result = await execute_capability(
        source=source,
        name=name,
        args=args,
        user_id=user_id,
        platform=platform,
        conversation_id=conversation_id,
    )
    await _log_tool_usage_safe(
        user_id=user_id,
        platform=platform,
        conversation_id=conversation_id,
        tool_source=str(result["source"] or source),
        tool_name=str(result["name"] or name),
        success=bool(result["ok"]),
        latency_ms=int(result["latency_ms"] or 0),
        error=str(result.get("error") or ""),
    )
    return result
