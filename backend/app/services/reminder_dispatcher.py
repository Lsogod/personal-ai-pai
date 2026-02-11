from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import UserIdentity
from app.models.reminder_delivery import ReminderDelivery
from app.models.schedule import Schedule
from app.models.user import User
from app.services.platforms import miniapp
from app.services.realtime import get_notification_hub
from app.services.sender import UnifiedSender


@dataclass(frozen=True)
class DeliveryTarget:
    platform: str
    platform_id: str


def _dedupe_targets(raw_targets: list[DeliveryTarget]) -> list[DeliveryTarget]:
    seen: set[tuple[str, str]] = set()
    out: list[DeliveryTarget] = []
    for target in raw_targets:
        key = ((target.platform or "").strip(), (target.platform_id or "").strip())
        if not key[0] or not key[1]:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(DeliveryTarget(platform=key[0], platform_id=key[1]))
    return out


async def _load_targets(session: AsyncSession, user: User) -> list[DeliveryTarget]:
    rows = await session.execute(select(UserIdentity).where(UserIdentity.user_id == user.id))
    identities = list(rows.scalars().all())
    targets = [DeliveryTarget(platform=row.platform, platform_id=row.platform_id) for row in identities]
    targets.append(DeliveryTarget(platform=user.platform or "", platform_id=user.platform_id or ""))
    return _dedupe_targets(targets)


async def _upsert_delivery_row(
    session: AsyncSession,
    *,
    schedule: Schedule,
    target: DeliveryTarget,
) -> ReminderDelivery:
    result = await session.execute(
        select(ReminderDelivery).where(
            ReminderDelivery.schedule_id == schedule.id,
            ReminderDelivery.platform == target.platform,
            ReminderDelivery.platform_id == target.platform_id,
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        row = ReminderDelivery(
            schedule_id=schedule.id,
            user_id=schedule.user_id,
            platform=target.platform,
            platform_id=target.platform_id,
            status="PENDING",
        )
    session.add(row)
    await session.flush()
    return row


async def _send_target_once(
    sender: UnifiedSender,
    user: User,
    schedule: Schedule,
    target: DeliveryTarget,
    text: str,
) -> tuple[bool, str]:
    try:
        if target.platform == "web":
            # Web reminder is delivered through realtime channel once per schedule.
            return True, ""

        if target.platform == "miniapp":
            ok, err = await miniapp.send_subscribe_reminder(
                openid=target.platform_id,
                content=schedule.content,
                trigger_time=schedule.trigger_time,
            )
            if ok:
                return True, ""
            return False, err or "miniapp subscribe send failed"

        await sender.send_text(target.platform, target.platform_id, text)
        return True, ""
    except Exception as exc:
        return False, str(exc)


async def _send_target_with_retry(
    sender: UnifiedSender,
    user: User,
    schedule: Schedule,
    target: DeliveryTarget,
    text: str,
) -> tuple[bool, str, int]:
    delays = (0, 1, 3)
    last_error = ""
    for idx, delay in enumerate(delays, start=1):
        if delay > 0:
            await asyncio.sleep(delay)
        ok, err = await _send_target_once(sender, user, schedule, target, text)
        if ok:
            return True, "", idx
        last_error = err or "unknown error"
    return False, last_error, len(delays)


async def dispatch_reminder(
    *,
    session: AsyncSession,
    sender: UnifiedSender,
    user: User,
    schedule: Schedule,
) -> tuple[bool, int, int]:
    text = f"提醒：{schedule.content}"
    targets = await _load_targets(session, user)
    if not targets:
        return False, 0, 0

    # Push realtime reminder once to connected clients, independent from per-target retry.
    try:
        await get_notification_hub().send_to_user(
            user.id,
            {
                "type": "reminder",
                "content": text,
                "schedule_id": schedule.id,
                "trigger_time": schedule.trigger_time.isoformat() + "Z",
                "created_at": datetime.utcnow().isoformat() + "Z",
            },
        )
    except Exception:
        pass

    success_count = 0
    total_count = len(targets)

    for target in targets:
        row = await _upsert_delivery_row(session, schedule=schedule, target=target)
        row.status = "SENDING"
        row.updated_at = datetime.utcnow()
        session.add(row)
    await session.commit()

    for target in targets:
        row = await _upsert_delivery_row(session, schedule=schedule, target=target)
        ok, err, attempts = await _send_target_with_retry(sender, user, schedule, target, text)
        row.attempt_count = int(row.attempt_count or 0) + attempts
        if ok:
            row.status = "SENT"
            row.last_error = None
            row.delivered_at = datetime.utcnow()
            success_count += 1
        else:
            row.status = "FAILED"
            row.last_error = (err or "delivery failed")[:500]
        row.updated_at = datetime.utcnow()
        session.add(row)
        await session.commit()

    return success_count > 0, success_count, total_count
