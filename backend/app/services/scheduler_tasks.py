from __future__ import annotations

import asyncio
from datetime import datetime

from sqlalchemy import select

from app.models.conversation import Conversation
from app.models.message import Message
from app.models.identity import UserIdentity
from app.db.session import AsyncSessionLocal
from app.models.schedule import Schedule
from app.models.user import User
from app.services.conversations import apply_assistant_message_updates
from app.services.realtime import get_notification_hub
from app.services.sender import UnifiedSender


async def send_reminder_job(schedule_id: int) -> None:
    async with AsyncSessionLocal() as session:
        schedule = await session.get(Schedule, schedule_id)
        if not schedule or schedule.status != "PENDING":
            return
        user = await session.get(User, schedule.user_id)
        if not user:
            return

        text = f"提醒：{schedule.content}"
        sender = UnifiedSender()
        result = await session.execute(
            select(UserIdentity).where(UserIdentity.user_id == user.id)
        )
        identities = list(result.scalars().all())

        targets: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for identity in identities:
            key = ((identity.platform or "").strip(), (identity.platform_id or "").strip())
            if not key[0] or not key[1]:
                continue
            if key in seen:
                continue
            seen.add(key)
            targets.append(key)
        primary_key = ((user.platform or "").strip(), (user.platform_id or "").strip())
        if primary_key[0] and primary_key[1] and primary_key not in seen:
            targets.append(primary_key)

        tasks = [
            sender.send_text(platform, platform_id, text)
            for platform, platform_id in targets
            if platform != "web"
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        await get_notification_hub().send_to_user(
            user.id,
            {
                "type": "reminder",
                "content": text,
                "schedule_id": schedule.id,
                "trigger_time": schedule.trigger_time.isoformat(),
                "created_at": datetime.utcnow().isoformat(),
            },
        )

        conversation_id = user.active_conversation_id
        if not conversation_id:
            conv_result = await session.execute(
                select(Conversation)
                .where(Conversation.user_id == user.id)
                .order_by(Conversation.last_message_at.desc(), Conversation.id.desc())
                .limit(1)
            )
            conversation = conv_result.scalar_one_or_none()
            if conversation:
                conversation_id = conversation.id

        if conversation_id:
            conversation = await session.get(Conversation, conversation_id)
            if conversation:
                apply_assistant_message_updates(conversation, text)
                session.add(conversation)
            session.add(
                Message(
                    user_id=user.id,
                    conversation_id=conversation_id,
                    role="assistant",
                    content=text,
                    platform="system",
                )
            )

        schedule.status = "EXECUTED"
        session.add(schedule)
        await session.commit()
