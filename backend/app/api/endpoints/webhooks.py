from typing import Any

from fastapi import APIRouter, Request, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.services.sender import UnifiedSender
from app.services.scheduler import get_scheduler
from app.services.platforms import telegram as telegram_platform
from app.services.platforms import feishu as feishu_platform
from app.services.platforms import onebot as onebot_platform
from app.services.platforms import gewechat as gewechat_platform
from app.core.config import get_settings
from app.services.message_handler import handle_message


router = APIRouter()

_sender = UnifiedSender()
_scheduler = get_scheduler()
_settings = get_settings()


async def _handle(platform: str, normalized: dict[str, Any], session: AsyncSession):
    return await handle_message(platform, normalized, session)


@router.post("/webhook/telegram")
async def telegram_webhook(request: Request, session: AsyncSession = Depends(get_session)):
    if _settings.telegram_webhook_secret:
        header = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if header != _settings.telegram_webhook_secret:
            return {"ok": False, "error": "invalid secret"}
    data = await request.json()
    parsed = await telegram_platform.parse_update(data)
    if not parsed:
        return {"ok": False}
    return await _handle("telegram", {**parsed, "raw_data": data}, session)


@router.post("/webhook/wechat")
async def wechat_webhook(request: Request, session: AsyncSession = Depends(get_session)):
    data = await request.json()
    parsed = await gewechat_platform.parse_event(data)
    if not parsed:
        return {"ok": False}
    return await _handle("wechat", {**parsed, "raw_data": data}, session)


@router.post("/webhook/qq")
async def qq_webhook(request: Request, session: AsyncSession = Depends(get_session)):
    data = await request.json()
    parsed = await onebot_platform.parse_event(data)
    if not parsed:
        return {"ok": False}
    return await _handle("qq", {**parsed, "raw_data": data}, session)


@router.post("/webhook/feishu")
async def feishu_webhook(request: Request, session: AsyncSession = Depends(get_session)):
    data = await request.json()
    parsed = await feishu_platform.parse_event(data)
    if parsed and "challenge" in parsed:
        return {"challenge": parsed["challenge"]}
    if not parsed:
        return {"ok": False}
    return await _handle("feishu", {**parsed, "raw_data": data}, session)
