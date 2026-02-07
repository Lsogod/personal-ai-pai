import json
import re
from datetime import date, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import select

from app.core.config import get_settings
from app.graph.state import GraphState
from app.models.ledger import Ledger
from app.models.schedule import Schedule
from app.services.llm import get_llm
from app.services.scheduler_tasks import send_reminder_job
from app.services.runtime_context import get_session, get_scheduler
from app.models.user import User


MINUTES_PATTERN = re.compile(r"(\d+)\s*分钟后")
HOURS_PATTERN = re.compile(r"(\d+)\s*小时后")
DAY_OFFSET_PATTERN = re.compile(r"(今天|明天|后天)")
HOUR_MIN_PATTERN = re.compile(r"(\d{1,2})\s*点(?:\s*(\d{1,2})\s*分)?")
HALF_PATTERN = re.compile(r"(\d{1,2})\s*点半")
CALENDAR_CMD_PATTERN = re.compile(r"^/calendar(?:\s+(.+))?$", re.IGNORECASE)
CALENDAR_HINT_PATTERN = re.compile(r"(日历|日程|行程|安排)")
DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")
TIME_QUERY_PATTERN = re.compile(
    r"(现在|此刻|当前).*(几点|时间|日期|几号|星期)|^(几点|时间|日期|今天几号|今天星期几)$"
)


def _parse_relative_time(text: str) -> datetime | None:
    now_local = datetime.now()
    if match := MINUTES_PATTERN.search(text):
        return now_local + timedelta(minutes=int(match.group(1)))
    if match := HOURS_PATTERN.search(text):
        return now_local + timedelta(hours=int(match.group(1)))
    day_offset = None
    if match := DAY_OFFSET_PATTERN.search(text):
        token = match.group(1)
        if token == "今天":
            day_offset = 0
        elif token == "明天":
            day_offset = 1
        elif token == "后天":
            day_offset = 2
    hour = None
    minute = 0
    if half := HALF_PATTERN.search(text):
        hour = int(half.group(1))
        minute = 30
    elif hm := HOUR_MIN_PATTERN.search(text):
        hour = int(hm.group(1))
        minute = int(hm.group(2)) if hm.group(2) else 0
    if hour is not None and day_offset is not None:
        if any(token in text for token in ("下午", "晚上", "晚间", "傍晚")) and hour < 12:
            hour += 12
        target_date = (now_local + timedelta(days=day_offset)).date()
        return datetime(
            year=target_date.year,
            month=target_date.month,
            day=target_date.day,
            hour=hour,
            minute=minute,
        )
    return None


def _resolve_calendar_window(text: str) -> tuple[datetime, datetime, str] | None:
    content = (text or "").strip().lower()
    match = CALENDAR_CMD_PATTERN.match(content)
    target = (match.group(1).strip().lower() if match and match.group(1) else content)

    today = datetime.utcnow().date()
    if target in {"", "today", "今日", "今天"}:
        start = today
        end = today + timedelta(days=1)
        return datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time()), "今天"
    if target in {"week", "本周", "这周"}:
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=7)
        return datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time()), "本周"
    if target in {"month", "本月", "这个月"}:
        start = today.replace(day=1)
        if start.month == 12:
            end = date(start.year + 1, 1, 1)
        else:
            end = date(start.year, start.month + 1, 1)
        return datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time()), "本月"

    raw_text = (text or "").strip()
    date_match = DATE_PATTERN.search(raw_text)
    if date_match:
        try:
            start = date.fromisoformat(date_match.group(0))
            end = start + timedelta(days=1)
            return datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time()), start.isoformat()
        except Exception:
            return None
    return None


def _parse_json_object(content: str) -> dict:
    text = (content or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _parse_run_at_local(value: str | None, timezone: str) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    patterns = ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S")
    for pattern in patterns:
        try:
            dt = datetime.strptime(raw, pattern)
            local_tz = ZoneInfo(timezone)
            # Keep naive local datetime to match scheduler DateTrigger timezone handling.
            _ = dt.replace(tzinfo=local_tz)
            return dt
        except Exception:
            continue
    return None


async def _understand_secretary_message(content: str) -> dict:
    settings = get_settings()
    tz = settings.timezone
    now_local = datetime.now(ZoneInfo(tz)).strftime("%Y-%m-%d %H:%M")
    llm = get_llm()
    system = SystemMessage(
        content=(
            "你是提醒与日历意图解析器。只输出 JSON。"
            "字段: intent, confidence, run_at_local, reminder_content, calendar_scope, calendar_date。"
            "intent 仅可为 reminder, calendar, unknown。"
            "如果用户是提醒请求（例如‘明天中午12点提醒我开会’），intent=reminder，"
            "run_at_local 必须输出 YYYY-MM-DD HH:MM（基于用户时区）。"
            "reminder_content 要提炼提醒事项本体，去掉时间描述。"
            "如果用户是查看日历/日程，intent=calendar，calendar_scope 用 today/week/month/date。"
            "当 scope=date 时输出 calendar_date=YYYY-MM-DD。"
            f"用户时区: {tz}。当前本地时间: {now_local}。"
        )
    )
    human = HumanMessage(content=content)
    response = await llm.ainvoke([system, human])
    return _parse_json_object(str(response.content))


def _resolve_calendar_window_from_fields(
    scope: str,
    calendar_date: str | None,
) -> tuple[datetime, datetime, str] | None:
    today = datetime.now().date()
    scope_key = (scope or "").strip().lower()
    if scope_key in {"today", "day"}:
        start = today
        end = today + timedelta(days=1)
        return datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time()), "今天"
    if scope_key == "week":
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=7)
        return datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time()), "本周"
    if scope_key == "month":
        start = today.replace(day=1)
        if start.month == 12:
            end = date(start.year + 1, 1, 1)
        else:
            end = date(start.year, start.month + 1, 1)
        return datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time()), "本月"
    if scope_key == "date" and calendar_date:
        try:
            start = date.fromisoformat(calendar_date.strip())
            end = start + timedelta(days=1)
            return datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time()), start.isoformat()
        except Exception:
            return None
    return None


async def _render_calendar_text(session, user_id: int, start_at: datetime, end_at: datetime, label: str) -> str:
    ledgers_result = await session.execute(
        select(Ledger)
        .where(
            Ledger.user_id == user_id,
            Ledger.transaction_date >= start_at,
            Ledger.transaction_date < end_at,
        )
        .order_by(Ledger.transaction_date.asc(), Ledger.id.asc())
    )
    schedules_result = await session.execute(
        select(Schedule)
        .where(
            Schedule.user_id == user_id,
            Schedule.trigger_time >= start_at,
            Schedule.trigger_time < end_at,
        )
        .order_by(Schedule.trigger_time.asc(), Schedule.id.asc())
    )
    ledgers = list(ledgers_result.scalars().all())
    schedules = list(schedules_result.scalars().all())

    lines = [f"{label}日历："]
    lines.append(f"- 账单 {len(ledgers)} 笔")
    lines.append(f"- 日程 {len(schedules)} 条")

    if ledgers:
        lines.append("账单：")
        for row in ledgers[:8]:
            lines.append(
                f"- {row.transaction_date.strftime('%m-%d %H:%M')} #{row.id} {row.item} {row.amount:.2f} {row.currency} ({row.category})"
            )
        if len(ledgers) > 8:
            lines.append(f"- ... 其余 {len(ledgers) - 8} 笔")

    if schedules:
        lines.append("日程：")
        for row in schedules[:8]:
            lines.append(
                f"- {row.trigger_time.strftime('%m-%d %H:%M')} #{row.id} {row.content} [{row.status}]"
            )
        if len(schedules) > 8:
            lines.append(f"- ... 其余 {len(schedules) - 8} 条")

    if not ledgers and not schedules:
        lines.append("这段时间没有账单和日程。")

    lines.append("可用：`/calendar today`、`/calendar week`、`/calendar month`、`/calendar 2026-02-07`")
    return "\n".join(lines)


async def secretary_node(state: GraphState) -> GraphState:
    message = state["message"]
    session = get_session()
    scheduler = get_scheduler()
    user = await session.get(User, state["user_id"])
    if not user:
        return {**state, "responses": ["未找到用户信息。"]}
    content = (message.content or "").strip()

    parsed: dict = {}
    try:
        parsed = await _understand_secretary_message(content)
    except Exception:
        parsed = {}

    intent = str(parsed.get("intent") or "").strip().lower()
    confidence = float(parsed.get("confidence") or 0.0)

    if intent == "calendar" and confidence >= 0.55:
        scope = str(parsed.get("calendar_scope") or "").strip().lower()
        calendar_date = str(parsed.get("calendar_date") or "").strip() or None
        llm_window = _resolve_calendar_window_from_fields(scope, calendar_date)
        if llm_window:
            start_at, end_at, label = llm_window
            response = await _render_calendar_text(session, user.id, start_at, end_at, label)
            return {**state, "responses": [response]}

    if intent == "reminder" and confidence >= 0.55:
        settings = get_settings()
        trigger_time = _parse_run_at_local(str(parsed.get("run_at_local") or ""), settings.timezone)
        if trigger_time and trigger_time > datetime.now() + timedelta(seconds=5):
            reminder_content = str(parsed.get("reminder_content") or "").strip() or content
            job_id = str(uuid4())
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
                    detail={"content": reminder_content, "trigger_time": trigger_time.isoformat(), "via": "llm"},
                )
            except Exception:
                pass

            return {
                **state,
                "responses": [f"好的，提醒已设置：{trigger_time.strftime('%Y-%m-%d %H:%M')}。"],
            }

    # Non-reminder factual query handled here to avoid poor reminder fallback UX.
    if TIME_QUERY_PATTERN.search(content):
        settings = get_settings()
        now_local = datetime.now(ZoneInfo(settings.timezone))
        weekday_map = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        weekday_text = weekday_map[now_local.weekday()]
        return {
            **state,
            "responses": [
                f"现在时间：{now_local.strftime('%Y-%m-%d %H:%M')}（{weekday_text}，时区 {settings.timezone}）。"
            ],
        }

    # Deterministic command fallback when LLM is uncertain.
    calendar_window = _resolve_calendar_window(content)
    if CALENDAR_CMD_PATTERN.match(content.strip()):
        if not calendar_window:
            return {
                **state,
                "responses": ["日历命令格式：`/calendar today|week|month|YYYY-MM-DD`。"],
            }
        start_at, end_at, label = calendar_window
        response = await _render_calendar_text(session, user.id, start_at, end_at, label)
        return {**state, "responses": [response]}

    # Non-command fallback when LLM is uncertain.
    if CALENDAR_HINT_PATTERN.search(content):
        fallback_window = _resolve_calendar_window(content)
        if fallback_window:
            start_at, end_at, label = fallback_window
            response = await _render_calendar_text(session, user.id, start_at, end_at, label)
            return {**state, "responses": [response]}

    trigger_time = _parse_relative_time(content)
    if not trigger_time:
        return {
            **state,
            "responses": [
                "我这边主要负责提醒和日历。可直接说：`明天中午12点提醒我开会`，或 `看下本周日程和账单`。"
            ],
        }

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
