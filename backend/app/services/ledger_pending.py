from __future__ import annotations

import json
from typing import Any

import redis.asyncio as redis

from app.core.config import get_settings

_settings = get_settings()
_redis = redis.from_url(_settings.redis_url, decode_responses=True)
_DEFAULT_TTL_SECONDS = 20 * 60


def _key(user_id: int, conversation_id: int) -> str:
    return f"ledger:pending:{user_id}:{conversation_id}"


async def set_pending_ledger(
    user_id: int,
    conversation_id: int,
    payload: dict[str, Any],
    ttl_seconds: int = _DEFAULT_TTL_SECONDS,
) -> None:
    key = _key(user_id, conversation_id)
    body = json.dumps(payload, ensure_ascii=False)
    try:
        await _redis.set(key, body, ex=ttl_seconds)
    except Exception:
        return


async def get_pending_ledger(
    user_id: int,
    conversation_id: int,
) -> dict[str, Any] | None:
    key = _key(user_id, conversation_id)
    try:
        value = await _redis.get(key)
    except Exception:
        return None
    if not value:
        return None
    try:
        data = json.loads(value)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


async def clear_pending_ledger(
    user_id: int,
    conversation_id: int,
) -> None:
    key = _key(user_id, conversation_id)
    try:
        await _redis.delete(key)
    except Exception:
        return


async def has_pending_ledger(
    user_id: int,
    conversation_id: int,
) -> bool:
    key = _key(user_id, conversation_id)
    try:
        return bool(await _redis.exists(key))
    except Exception:
        return False
