import re
from datetime import datetime, timedelta
from uuid import uuid4

from app.graph.state import GraphState
from app.models.schedule import Schedule
from app.services.scheduler_tasks import send_reminder_job
from app.services.runtime_context import get_session, get_scheduler
from app.models.user import User


MINUTES_PATTERN = re.compile(r"(\d+)\s*分钟后")
HOURS_PATTERN = re.compile(r"(\d+)\s*小时后")


def _parse_relative_time(text: str) -> datetime | None:
    if match := MINUTES_PATTERN.search(text):
        return datetime.utcnow() + timedelta(minutes=int(match.group(1)))
    if match := HOURS_PATTERN.search(text):
        return datetime.utcnow() + timedelta(hours=int(match.group(1)))
    return None


async def secretary_node(state: GraphState) -> GraphState:
    message = state["message"]
    session = get_session()
    scheduler = get_scheduler()
    user = await session.get(User, state["user_id"])
    if not user:
        return {**state, "responses": ["未找到用户信息。"]}
    content = message.content or ""
    trigger_time = _parse_relative_time(content)
    if not trigger_time:
        return {**state, "responses": ["请告诉我具体提醒时间，例如：10分钟后提醒我喝水。"]}

    job_id = str(uuid4())
    reminder_content = content

    schedule = Schedule(
        user_id=user.id,
        job_id=job_id,
        content=reminder_content,
        trigger_time=trigger_time,
    )
    session.add(schedule)
    await session.commit()
    await session.refresh(schedule)

    scheduler.add_job(job_id, trigger_time, send_reminder_job, schedule.id)

    try:
        from app.services.audit import log_event

        await log_event(
            session,
            action="schedule_created",
            platform=user.platform,
            user_id=user.id,
            detail={"content": reminder_content, "trigger_time": trigger_time.isoformat()},
        )
    except Exception:
        pass

    return {**state, "responses": ["好的，提醒已设置。"]}
