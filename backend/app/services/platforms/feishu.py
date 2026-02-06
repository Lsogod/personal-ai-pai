from __future__ import annotations

import json
import time
import base64
from typing import Any, Optional

import httpx

from app.core.config import get_settings


class FeishuTokenManager:
    def __init__(self) -> None:
        self._token: str | None = None
        self._expires_at: float = 0.0

    async def get_token(self) -> Optional[str]:
        settings = get_settings()
        if not settings.feishu_app_id or not settings.feishu_app_secret:
            return None

        now = time.time()
        if self._token and now < self._expires_at - 60:
            return self._token

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                json={
                    "app_id": settings.feishu_app_id,
                    "app_secret": settings.feishu_app_secret,
                },
            )
        if resp.status_code != 200:
            return None
        data = resp.json()
        token = data.get("tenant_access_token")
        expire = data.get("expire", 0)
        if not token:
            return None
        self._token = token
        self._expires_at = now + int(expire)
        return token


_token_manager = FeishuTokenManager()


async def fetch_image_data_url(message_id: str, image_key: str) -> Optional[str]:
    token = await _token_manager.get_token()
    if not token:
        return None
    url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/resources/{image_key}"
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            params={"type": "image"},
        )
    if resp.status_code != 200 or not resp.content:
        return None
    content_type = resp.headers.get("content-type", "image/jpeg")
    image_b64 = base64.b64encode(resp.content).decode("utf-8")
    return f"data:{content_type};base64,{image_b64}"


async def parse_event(data: dict[str, Any]) -> Optional[dict[str, Any]]:
    # URL verification handshake
    if data.get("type") == "url_verification":
        return {"challenge": data.get("challenge")}

    # Optional token verification
    settings = get_settings()
    token = data.get("token")
    if settings.feishu_verification_token and token != settings.feishu_verification_token:
        return None

    event = data.get("event") or {}
    message = event.get("message") or {}
    sender = event.get("sender") or {}
    sender_id = sender.get("sender_id") or {}

    # Only handle p2p chats by default
    chat_type = message.get("chat_type")
    if chat_type and chat_type != "p2p":
        return None

    platform_id = (
        sender_id.get("open_id")
        or sender_id.get("user_id")
        or sender_id.get("union_id")
    )
    if not platform_id:
        return None

    msg_type = message.get("message_type")
    content_raw = message.get("content") or ""
    text = ""
    image_urls: list[str] = []

    if msg_type == "text":
        try:
            text = json.loads(content_raw).get("text", "")
        except json.JSONDecodeError:
            text = content_raw
    elif msg_type == "image":
        text = "[image]"
        try:
            image_key = json.loads(content_raw).get("image_key")
        except json.JSONDecodeError:
            image_key = None
        message_id = message.get("message_id")
        if image_key and message_id:
            image_urls = [f"feishu://{message_id}/{image_key}"]
    else:
        text = content_raw

    return {
        "platform_id": platform_id,
        "content": text,
        "image_urls": image_urls,
        "message_id": message.get("message_id"),
        "event_ts": message.get("create_time"),
    }


async def send_text(platform_id: str, text: str) -> None:
    settings = get_settings()
    token = await _token_manager.get_token()
    if not token:
        return

    receive_id_type = settings.feishu_receive_id_type or "open_id"
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(
            f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_id_type}",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "receive_id": platform_id,
                "msg_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
            },
        )


async def send_image(platform_id: str, image_url: str) -> None:
    settings = get_settings()
    token = await _token_manager.get_token()
    if not token:
        return

    # Feishu requires uploading binary first to get image_key.
    image_key: str | None = None
    async with httpx.AsyncClient(timeout=20) as client:
        image_resp = await client.get(image_url)
        if image_resp.status_code == 200:
            content_type = image_resp.headers.get("content-type", "application/octet-stream")
            upload_resp = await client.post(
                "https://open.feishu.cn/open-apis/im/v1/images",
                headers={"Authorization": f"Bearer {token}"},
                data={"image_type": "message"},
                files={"image": ("upload", image_resp.content, content_type)},
            )
            if upload_resp.status_code == 200:
                image_key = upload_resp.json().get("data", {}).get("image_key")

        if not image_key:
            # Fallback to text if upload fails.
            await send_text(platform_id, image_url)
            return

        receive_id_type = settings.feishu_receive_id_type or "open_id"
        await client.post(
            f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_id_type}",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "receive_id": platform_id,
                "msg_type": "image",
                "content": json.dumps({"image_key": image_key}, ensure_ascii=False),
            },
        )
