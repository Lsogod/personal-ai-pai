from __future__ import annotations

from typing import Any, Optional

import httpx

from app.core.config import get_settings


async def parse_event(data: dict[str, Any]) -> Optional[dict[str, Any]]:
    platform_id = (
        data.get("from_wxid")
        or data.get("from")
        or data.get("sender")
        or data.get("wxid")
        or data.get("user_id")
    )
    if not platform_id:
        return None

    content = data.get("content") or data.get("text") or data.get("msg") or ""
    image_urls = []
    for key in ("image_url", "img_url", "image", "picture"):
        if data.get(key):
            image_urls.append(data.get(key))
            break
    if isinstance(data.get("image_urls"), list):
        image_urls = data.get("image_urls")

    return {
        "platform_id": str(platform_id),
        "content": content,
        "image_urls": image_urls,
        "message_id": str(data.get("msg_id") or data.get("msgid") or ""),
        "event_ts": data.get("timestamp") or data.get("ts"),
    }


async def send_text(platform_id: str, text: str) -> None:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(
            f"{settings.gewechat_base_url}/sendText",
            json={
                "app_id": settings.gewechat_app_id,
                "to_wxid": platform_id,
                "content": text,
            },
        )


async def send_image(platform_id: str, image_url: str) -> None:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(
            f"{settings.gewechat_base_url}/sendImage",
            json={
                "app_id": settings.gewechat_app_id,
                "to_wxid": platform_id,
                "image_url": image_url,
            },
        )
