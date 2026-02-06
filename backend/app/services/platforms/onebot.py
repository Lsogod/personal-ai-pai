from __future__ import annotations

from typing import Any, Optional

import httpx

from app.core.config import get_settings


def _extract_text(message: Any) -> str:
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        texts = []
        for segment in message:
            if segment.get("type") == "text":
                texts.append(segment.get("data", {}).get("text", ""))
        return "".join(texts)
    return ""


def _extract_images(message: Any) -> list[str]:
    urls: list[str] = []
    if isinstance(message, list):
        for segment in message:
            if segment.get("type") == "image":
                data = segment.get("data", {})
                url = data.get("url") or data.get("file")
                if url:
                    urls.append(url)
    return urls


async def parse_event(data: dict[str, Any]) -> Optional[dict[str, Any]]:
    if data.get("post_type") != "message":
        return None

    if data.get("message_type") != "private":
        # ignore group messages for now
        return None

    user_id = data.get("user_id")
    if user_id is None:
        return None

    message = data.get("message")
    text = _extract_text(message)
    images = _extract_images(message)

    return {
        "platform_id": str(user_id),
        "content": text,
        "image_urls": images,
        "message_id": str(data.get("message_id") or ""),
        "event_ts": data.get("time"),
    }


async def send_text(user_id: str, text: str) -> None:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=10) as client:
        headers = {}
        if settings.onebot_access_token:
            headers["Authorization"] = f"Bearer {settings.onebot_access_token}"
        await client.post(
            f"{settings.onebot_base_url}/send_private_msg",
            headers=headers,
            json={"user_id": int(user_id), "message": text},
        )


async def send_image(user_id: str, image_url: str) -> None:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=10) as client:
        headers = {}
        if settings.onebot_access_token:
            headers["Authorization"] = f"Bearer {settings.onebot_access_token}"
        await client.post(
            f"{settings.onebot_base_url}/send_private_msg",
            headers=headers,
            json={"user_id": int(user_id), "message": [{"type": "image", "data": {"url": image_url}}]},
        )
