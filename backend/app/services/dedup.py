from __future__ import annotations

from typing import Optional

import redis.asyncio as redis

from app.core.config import get_settings

_settings = get_settings()
_redis = redis.from_url(_settings.redis_url, decode_responses=True)


async def is_duplicate(platform: str, message_id: Optional[str]) -> bool:
    if not message_id:
        return False
    key = f"dedup:{platform}:{message_id}"
    try:
        if await _redis.exists(key):
            return True
        await _redis.set(key, "1", ex=_settings.dedup_ttl_seconds)
    except Exception:
        return False
    return False
