from __future__ import annotations

from typing import Any, Optional

import httpx

from app.core.config import get_settings


async def _get_file_url(file_id: str) -> Optional[str]:
    settings = get_settings()
    if not settings.telegram_bot_token:
        return None
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/getFile",
            params={"file_id": file_id},
        )
    if resp.status_code != 200:
        return None
    data = resp.json()
    if not data.get("ok"):
        return None
    file_path = data.get("result", {}).get("file_path")
    if not file_path:
        return None
    return f"https://api.telegram.org/file/bot{settings.telegram_bot_token}/{file_path}"


async def parse_update(data: dict[str, Any]) -> Optional[dict[str, Any]]:
    message = data.get("message") or data.get("edited_message") or data.get("channel_post")
    if not message:
        return None

    chat = message.get("chat") or {}
    if chat.get("type") not in {None, "private"}:
        # Ignore group/channel messages for now
        return None

    chat_id = chat.get("id")
    if chat_id is None:
        return None

    text = message.get("text") or message.get("caption") or ""
    photos = message.get("photo") or []
    document = message.get("document") or {}
    image_urls: list[str] = []
    if photos:
        file_id = photos[-1].get("file_id")
        if file_id:
            url = await _get_file_url(file_id)
            if url:
                image_urls.append(url)
    if not image_urls and document:
        mime_type = str(document.get("mime_type") or "")
        file_id = document.get("file_id")
        if file_id and mime_type.startswith("image/"):
            url = await _get_file_url(file_id)
            if url:
                image_urls.append(url)

    return {
        "platform_id": str(chat_id),
        "content": text,
        "image_urls": image_urls,
        "message_id": str(message.get("message_id") or data.get("update_id") or ""),
        "event_ts": message.get("date"),
    }


async def send_text(chat_id: str, text: str) -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        return
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
        )


async def send_image(chat_id: str, image_url: str) -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        return
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendPhoto",
            json={"chat_id": chat_id, "photo": image_url},
        )
