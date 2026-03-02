from __future__ import annotations

from datetime import datetime
import logging
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.config import get_settings
from app.models.conversation import Conversation
from app.models.message import Message
from app.db.session import AsyncSessionLocal
from app.models.schedule import Schedule
from app.models.user import User
from app.services.conversations import apply_assistant_message_updates
from app.services.reminder_dispatcher import dispatch_reminder
from app.services.sender import UnifiedSender
from app.services.scheduler import get_scheduler

logger = logging.getLogger(__name__)


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
        ok, success_count, total_count = await dispatch_reminder(
            session=session,
            sender=sender,
            user=user,
            schedule=schedule,
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

        schedule.status = "EXECUTED" if ok else "FAILED"
        try:
            from app.services.audit import log_event

            await log_event(
                session,
                action="reminder_dispatched",
                platform=user.platform,
                user_id=user.id,
                detail={
                    "schedule_id": schedule.id,
                    "content": schedule.content,
                    "status": schedule.status,
                    "success_count": success_count,
                    "target_count": total_count,
                },
            )
        except Exception:
            pass
        session.add(schedule)
        await session.commit()


async def restore_pending_reminder_jobs() -> None:
    scheduler = get_scheduler()
    settings = get_settings()
    now = datetime.now(ZoneInfo(settings.timezone)).replace(tzinfo=None)
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Schedule).where(Schedule.status == "PENDING")
        )
        pending = list(result.scalars().all())
        for row in pending:
            run_at = row.trigger_time
            if run_at <= now:
                run_at = now
            scheduler.add_job(row.job_id, run_at, send_reminder_job, row.id)


async def scan_unprocessed_memory_messages_job() -> None:
    settings = get_settings()
    if not settings.long_term_memory_enabled or not settings.long_term_memory_scan_enabled:
        return
    try:
        from app.services.message_handler import scan_unprocessed_memory_messages

        await scan_unprocessed_memory_messages(
            max_conversations=max(1, int(settings.long_term_memory_scan_max_conversations or 80)),
            max_messages_per_conversation=max(
                1,
                int(settings.long_term_memory_scan_max_messages_per_conversation or 30),
            ),
        )
    except Exception:
        logger.exception("memory scan job failed")


def ensure_memory_scan_job() -> None:
    settings = get_settings()
    if not settings.long_term_memory_enabled or not settings.long_term_memory_scan_enabled:
        return
    scheduler = get_scheduler()
    scheduler.add_interval_job(
        "long_term_memory_scan",
        max(30, int(settings.long_term_memory_scan_interval_sec or 120)),
        scan_unprocessed_memory_messages_job,
    )
