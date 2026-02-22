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
    def __init__(self, *, url: str | None = None) -> None:
        settings = get_settings()
        resolved_url = (url if url is not None else settings.mcp_fetch_url) or ""
        self.url = str(resolved_url).strip()
        self.timeout = float(settings.mcp_fetch_timeout_sec)
        self.default_max_length = int(settings.mcp_fetch_default_max_length)
        self._fetch_tool_name_cache: str | None = None
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
        if not self.url:
            raise MCPFetchError("MCP_FETCH_URL is empty")
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        if request_id is not None:
            payload["id"] = request_id

        headers = dict(self._http_headers)
        if session_id:
            headers["mcp-session-id"] = session_id

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    self.url,
                    headers=headers,
                    content=json.dumps(payload, ensure_ascii=False),
                )
        except httpx.HTTPError as exc:
            raise MCPFetchError(f"mcp request failed: {exc}") from exc

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

    async def _discover_fetch_tool_name(self) -> str | None:
        if self._fetch_tool_name_cache is not None:
            return self._fetch_tool_name_cache or None
        preferred = [
            "fetch",
            "fetch_url",
            "url_fetch",
            "web_fetch",
            "fetch_markdown",
            "webpage_fetch",
        ]
        try:
            tools = await self.list_tools()
        except Exception:
            # Unknown if MCP is reachable; keep cache empty and let caller fallback.
            self._fetch_tool_name_cache = ""
            return None

        name_map: dict[str, str] = {}
        for item in tools:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            name_map[name.lower()] = name

        for key in preferred:
            if key in name_map:
                self._fetch_tool_name_cache = name_map[key]
                return self._fetch_tool_name_cache

        for key, original in name_map.items():
            if "fetch" in key:
                self._fetch_tool_name_cache = original
                return self._fetch_tool_name_cache

        self._fetch_tool_name_cache = ""
        return None

    async def _direct_http_fetch(
        self,
        *,
        url: str,
        max_length: int,
        start_index: int,
        raw: bool,
    ) -> str:
        target = (url or "").strip()
        if not target:
            raise MCPFetchError("missing url")

        async def _do_get(verify: bool) -> httpx.Response:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=True,
                headers={"User-Agent": "pai-backend/1.0"},
                verify=verify,
            ) as client:
                return await client.get(target)

        try:
            response = await _do_get(True)
        except httpx.HTTPError as exc:
            # Some minimal containers may not have complete CA trust store.
            # Retry once without certificate verification to keep /fetch usable.
            if "CERTIFICATE_VERIFY_FAILED" in str(exc).upper():
                try:
                    response = await _do_get(False)
                except httpx.HTTPError as retry_exc:
                    raise MCPFetchError(f"direct fetch request failed: {retry_exc}") from retry_exc
            else:
                raise MCPFetchError(f"direct fetch request failed: {exc}") from exc

        if response.status_code >= 400:
            raise MCPFetchError(f"direct fetch http {response.status_code}")

        content_type = str(response.headers.get("content-type") or "").lower()
        if "application/json" in content_type:
            try:
                text = json.dumps(response.json(), ensure_ascii=False, indent=2)
            except Exception:
                text = response.text
        else:
            text = response.text

        payload = text or ""
        start = max(0, int(start_index))
        if start >= len(payload):
            return ""
        end = start + max(1, int(max_length))
        sliced = payload[start:end]
        if not raw and len(payload) > end:
            sliced += "\n\n...(truncated)"
        return sliced.strip()

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
        tool_name = await self._discover_fetch_tool_name()
        if tool_name:
            return await self.call_tool(name=tool_name, arguments=args)
        return await self._direct_http_fetch(
            url=args["url"],
            max_length=int(args["max_length"]),
            start_index=int(args["start_index"]),
            raw=bool(args["raw"]),
        )

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


def get_mcp_fetch_client(*, url: str | None = None) -> MCPFetchClient:
    return MCPFetchClient(url=url)


def get_mcp_maps_client() -> MCPFetchClient:
    settings = get_settings()
    maps_url = str(settings.mcp_maps_url or "").strip()
    if maps_url:
        return MCPFetchClient(url=maps_url)
    return MCPFetchClient(url=(settings.mcp_fetch_url or "").strip())


def get_mcp_client_for_tool(tool_name: str) -> MCPFetchClient:
    name = str(tool_name or "").strip().lower()
    if name.startswith("maps_"):
        return get_mcp_maps_client()
    return get_mcp_fetch_client()
