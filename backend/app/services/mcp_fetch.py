from __future__ import annotations

import json
from typing import Any

import httpx

from app.core.config import get_settings


class MCPFetchError(RuntimeError):
    pass


def _json_or_empty(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text or "{}")
        if isinstance(payload, dict):
            return payload
        return {}
    except Exception:
        return {}


class MCPFetchClient:
    def __init__(self) -> None:
        settings = get_settings()
        self.url = settings.mcp_fetch_url
        self.timeout = float(settings.mcp_fetch_timeout_sec)
        self.default_max_length = int(settings.mcp_fetch_default_max_length)
        self._http_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }

    async def _post_rpc(
        self,
        *,
        method: str,
        params: dict[str, Any] | None = None,
        request_id: int | None = None,
        session_id: str | None = None,
    ) -> tuple[dict[str, Any], httpx.Headers]:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        if request_id is not None:
            payload["id"] = request_id

        headers = dict(self._http_headers)
        if session_id:
            headers["mcp-session-id"] = session_id

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                self.url,
                headers=headers,
                content=json.dumps(payload, ensure_ascii=False),
            )

        if response.status_code >= 400:
            raise MCPFetchError(f"mcp http {response.status_code}")
        data = _json_or_empty(response.text)
        if data.get("error"):
            err = data.get("error") or {}
            raise MCPFetchError(str(err.get("message") or "mcp rpc error"))
        return data, response.headers

    async def _open_session(self) -> str:
        init_params = {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "pai-backend", "version": "1.0.0"},
        }
        _, headers = await self._post_rpc(
            method="initialize",
            params=init_params,
            request_id=1,
        )
        session_id = (
            headers.get("Mcp-Session-Id")
            or headers.get("mcp-session-id")
            or ""
        ).strip()
        if not session_id:
            raise MCPFetchError("mcp session id missing")
        await self._post_rpc(
            method="notifications/initialized",
            params=None,
            request_id=None,
            session_id=session_id,
        )
        return session_id

    async def list_tools(self) -> list[dict[str, Any]]:
        session_id = await self._open_session()
        data, _ = await self._post_rpc(
            method="tools/list",
            params={},
            request_id=2,
            session_id=session_id,
        )
        result = data.get("result") or {}
        tools = result.get("tools") or []
        return tools if isinstance(tools, list) else []

    async def fetch(
        self,
        *,
        url: str,
        max_length: int | None = None,
        start_index: int = 0,
        raw: bool = False,
    ) -> str:
        args = {
            "url": (url or "").strip(),
            "max_length": int(max_length or self.default_max_length),
            "start_index": int(start_index),
            "raw": bool(raw),
        }
        return await self.call_tool(name="fetch", arguments=args)

    async def call_tool(
        self,
        *,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> str:
        session_id = await self._open_session()
        tool_name = (name or "").strip()
        if not tool_name:
            raise MCPFetchError("missing tool name")
        args = arguments if isinstance(arguments, dict) else {}
        data, _ = await self._post_rpc(
            method="tools/call",
            params={"name": tool_name, "arguments": args},
            request_id=3,
            session_id=session_id,
        )
        result = data.get("result") or {}
        content = result.get("content") or []
        if bool(result.get("isError")):
            message = "mcp tool returned error"
            if isinstance(content, list):
                texts: list[str] = []
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        texts.append(text.strip())
                if texts:
                    message = texts[0][:500]
            raise MCPFetchError(message)
        if not isinstance(content, list):
            raise MCPFetchError("mcp tool invalid content payload")
        texts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                texts.append(text)
        if not texts:
            raise MCPFetchError("mcp tool empty content")
        return "\n\n".join(texts).strip()


def get_mcp_fetch_client() -> MCPFetchClient:
    return MCPFetchClient()
