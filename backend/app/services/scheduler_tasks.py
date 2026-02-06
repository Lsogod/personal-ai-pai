from __future__ import annotations

from app.db.session import AsyncSessionLocal
from app.models.schedule import Schedule
from app.models.user import User
from app.services.sender import UnifiedSender


async def send_reminder_job(schedule_id: int) -> None:
    async with AsyncSessionLocal() as session:
        schedule = await session.get(Schedule, schedule_id)
        if not schedule or schedule.status != "PENDING":
            return
        user = await session.get(User, schedule.user_id)
        if not user:
            return
        sender = UnifiedSender()
        await sender.send_text(user.platform, user.platform_id, f"提醒：{schedule.content}")
        schedule.status = "EXECUTED"
        session.add(schedule)
        await session.commit()
