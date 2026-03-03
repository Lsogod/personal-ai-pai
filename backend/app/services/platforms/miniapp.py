from __future__ import annotations

import time
from datetime import datetime
from typing import Optional

import httpx

from app.core.config import get_settings


class MiniappTokenManager:
    def __init__(self) -> None:
        self._token: str | None = None
        self._expires_at: float = 0.0

    async def get_token(self) -> Optional[str]:
        settings = get_settings()
        if not settings.miniapp_app_id or not settings.miniapp_app_secret:
            return None

        now = time.time()
        if self._token and now < self._expires_at - 60:
            return self._token

        params = {
            "grant_type": "client_credential",
            "appid": settings.miniapp_app_id,
            "secret": settings.miniapp_app_secret,
        }
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get("https://api.weixin.qq.com/cgi-bin/token", params=params)
        if resp.status_code != 200:
            return None
        data = resp.json()
        token = data.get("access_token")
        expires_in = int(data.get("expires_in") or 0)
        if not token or expires_in <= 0:
            return None
        self._token = token
        self._expires_at = now + expires_in
        return token


_token_manager = MiniappTokenManager()


async def exchange_code_for_openid(code: str) -> Optional[str]:
    settings = get_settings()
    if not settings.miniapp_app_id or not settings.miniapp_app_secret:
        return None
    params = {
        "appid": settings.miniapp_app_id,
        "secret": settings.miniapp_app_secret,
        "js_code": code,
        "grant_type": "authorization_code",
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get("https://api.weixin.qq.com/sns/jscode2session", params=params)
    if resp.status_code != 200:
        return None
    data = resp.json()
    return data.get("openid")


async def send_text(openid: str, text: str) -> bool:
    token = await _token_manager.get_token()
    if not token:
        return False
    payload = {
        "touser": openid,
        "msgtype": "text",
        "text": {"content": text},
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            "https://api.weixin.qq.com/cgi-bin/message/custom/send",
            params={"access_token": token},
            json=payload,
        )
    if resp.status_code != 200:
        return False
    data = resp.json()
    return int(data.get("errcode", -1)) == 0


async def send_image(openid: str, image_url: str) -> bool:
    # Mini-program customer service image send needs media upload first.
    # Fallback to plain text with URL for predictable behavior.
    return await send_text(openid, image_url)


async def send_subscribe_reminder(
    *,
    openid: str,
    content: str,
    trigger_time: datetime,
) -> tuple[bool, str]:
    settings = get_settings()
    if not settings.miniapp_subscribe_template_id:
        return False, "miniapp_subscribe_template_id not configured"

    token = await _token_manager.get_token()
    if not token:
        return False, "miniapp access_token unavailable"

    content_key = settings.miniapp_subscribe_content_key or "thing1"
    time_key = settings.miniapp_subscribe_time_key or "time2"
    payload = {
        "touser": openid,
        "template_id": settings.miniapp_subscribe_template_id,
        "lang": settings.miniapp_lang or "zh_CN",
        "page": settings.miniapp_page_path or "pages/chat/index",
        "data": {
            content_key: {"value": (content or "提醒事项")[:20]},
            time_key: {"value": trigger_time.strftime("%Y-%m-%d %H:%M:%S")},
        },
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            "https://api.weixin.qq.com/cgi-bin/message/subscribe/send",
            params={"access_token": token},
            json=payload,
        )
    if resp.status_code != 200:
        return False, f"http_{resp.status_code}"
    data = resp.json()
    errcode = int(data.get("errcode", -1))
    if errcode != 0:
        errmsg = str(data.get("errmsg") or "").strip()
        if errmsg:
            return False, f"wx_err_{errcode}:{errmsg}"
        return False, f"wx_err_{errcode}"
    return True, ""
