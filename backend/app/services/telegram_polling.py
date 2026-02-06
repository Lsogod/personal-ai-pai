from __future__ import annotations

import asyncio
from typing import Optional

import httpx
import redis.asyncio as redis

from app.core.config import get_settings
from app.db.session import AsyncSessionLocal
from app.services.platforms import telegram as telegram_platform
from app.services.message_handler import handle_message


async def _load_offset(r: redis.Redis, key: str) -> int:
    try:
        value = await r.get(key)
        return int(value) if value else 0
    except Exception:
        return 0


async def _save_offset(r: redis.Redis, key: str, offset: int) -> None:
    try:
        await r.set(key, str(offset))
    except Exception:
        return


async def telegram_polling_loop() -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        return

    redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    offset_key = "telegram:offset"
    offset = await _load_offset(redis_client, offset_key)

    timeout = max(1, settings.telegram_polling_timeout)
    interval = max(1, settings.telegram_polling_interval)

    async with httpx.AsyncClient(timeout=timeout + 10) as client:
        while True:
            try:
                resp = await client.get(
                    f"https://api.telegram.org/bot{settings.telegram_bot_token}/getUpdates",
                    params={
                        "timeout": timeout,
                        "offset": offset,
                        "limit": settings.telegram_polling_limit,
                        "allowed_updates": "message,edited_message",
                    },
                )
                if resp.status_code != 200:
                    await asyncio.sleep(interval)
                    continue
                data = resp.json()
                if not data.get("ok"):
                    await asyncio.sleep(interval)
                    continue

                updates = data.get("result", [])
                for update in updates:
                    update_id = update.get("update_id")
                    if update_id is None:
                        continue
                    offset = max(offset, int(update_id) + 1)
                    await _save_offset(redis_client, offset_key, offset)

                    parsed = await telegram_platform.parse_update(update)
                    if not parsed:
                        continue
                    async with AsyncSessionLocal() as session:
                        await handle_message("telegram", {**parsed, "raw_data": update}, session)

                if not updates:
                    await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(interval)
