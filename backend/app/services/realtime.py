from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

from fastapi import WebSocket


class NotificationHub:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._clients: dict[int, set[WebSocket]] = defaultdict(set)

    async def connect(self, user_id: int, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._clients[user_id].add(websocket)

    async def disconnect(self, user_id: int, websocket: WebSocket) -> None:
        async with self._lock:
            clients = self._clients.get(user_id)
            if not clients:
                return
            clients.discard(websocket)
            if not clients:
                self._clients.pop(user_id, None)

    async def send_to_user(self, user_id: int, payload: dict[str, Any]) -> None:
        async with self._lock:
            targets = list(self._clients.get(user_id, set()))
        stale: list[WebSocket] = []
        for ws in targets:
            try:
                await ws.send_json(payload)
            except Exception:
                stale.append(ws)
        for ws in stale:
            await self.disconnect(user_id, ws)


_hub: NotificationHub | None = None


def get_notification_hub() -> NotificationHub:
    global _hub
    if _hub is None:
        _hub = NotificationHub()
    return _hub
