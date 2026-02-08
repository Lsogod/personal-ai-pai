import json
import re
from datetime import date, datetime, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import select

from app.core.config import get_settings
from app.graph.context import render_conversation_context
from app.graph.state import GraphState
from app.models.ledger import Ledger
from app.models.schedule import Schedule
from app.services.llm import get_llm
from app.services.scheduler_tasks import send_reminder_job
from app.services.runtime_context import get_session, get_scheduler
from app.models.user import User


CALENDAR_CMD_PATTERN = re.compile(r"^/calendar(?:\s+(.+))?$", re.IGNORECASE)
DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")
TECHNICAL_LEAK_PATTERN = re.compile(r"(json|payload|字段|数组|schema|schedules|ledgers)", re.IGNORECASE)
SCHEDULE_STATUS_MAP = {
    "all": "all",
    "全部": "all",
    "所有": "all",
    "pending": "pending",
    "todo": "pending",
    "未完成": "pending",
    "待办": "pending",
    "待执行": "pending",
    "未执行": "pending",
    "executed": "executed",
    "completed": "executed",
    "done": "executed",
    "已完成": "executed",
    "完成": "executed",
    "已执行": "executed",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "已取消": "cancelled",
    "取消": "cancelled",
}
SCHEDULE_STATUS_DB = {
    "all": None,
    "pending": "PENDING",
    "executed": "EXECUTED",
    "cancelled": "CANCELLED",
}


def _local_today() -> date:
    tz_name = get_settings().timezone
    return datetime.now(ZoneInfo(tz_name)).date()


def _to_utc_naive(local_dt: datetime) -> datetime:
    tz_name = get_settings().timezone
    return local_dt.replace(tzinfo=ZoneInfo(tz_name)).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def _format_window_label(name: str, start_date: date, end_date_exclusive: date) -> str:
    if end_date_exclusive == start_date + timedelta(days=1):
        return f"{name}（{start_date.isoformat()}）"
    end_date = end_date_exclusive - timedelta(days=1)
    return f"{name}（{start_date.isoformat()} ~ {end_date.isoformat()}）"


def _resolve_calendar_window(text: str) -> tuple[datetime, datetime, str] | None:
    content = (text or "").strip().lower()
    match = CALENDAR_CMD_PATTERN.match(content)
    target = (match.group(1).strip().lower() if match and match.group(1) else content)

    today = _local_today()
    if target in {"yesterday", "昨天"}:
        start = today - timedelta(days=1)
        end = start + timedelta(days=1)
        return datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time()), _format_window_label("昨天", start, end)
    if target in {"前天"}:
        start = today - timedelta(days=2)
        end = start + timedelta(days=1)
        return datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time()), _format_window_label("前天", start, end)
    if target in {"", "today", "今日", "今天"}:
        start = today
        end = today + timedelta(days=1)
        return datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time()), _format_window_label("今天", start, end)
    if target in {"week", "本周", "这周"}:
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=7)
        return datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time()), _format_window_label("本周", start, end)
    if target in {"last week", "上周"}:
        start = today - timedelta(days=today.weekday() + 7)
        end = start + timedelta(days=7)
        return datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time()), _format_window_label("上周", start, end)
    if target in {"month", "本月", "这个月"}:
        start = today.replace(day=1)
        if start.month == 12:
            end = date(start.year + 1, 1, 1)
        else:
            end = date(start.year, start.month + 1, 1)
        return datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time()), _format_window_label("本月", start, end)

    raw_text = (text or "").strip()
    date_match = DATE_PATTERN.search(raw_text)
    if date_match:
        try:
            start = date.fromisoformat(date_match.group(0))
            end = start + timedelta(days=1)
            return datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time()), _format_window_label(start.isoformat(), start, end)
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


def _sanitize_llm_text(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    lines: list[str] = []
    prev = ""
    for row in raw.splitlines():
        line = row.rstrip()
        norm = re.sub(r"\s+", " ", line).strip()
        if not norm:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        if norm == prev:
            continue
        if TECHNICAL_LEAK_PATTERN.search(norm):
            continue
        lines.append(line)
        prev = norm
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines).strip()


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


async def _understand_secretary_message(content: str, conversation_context: str) -> dict:
    settings = get_settings()
    tz = settings.timezone
    now_local = datetime.now(ZoneInfo(tz)).strftime("%Y-%m-%d %H:%M")
    llm = get_llm()
    system = SystemMessage(
        content=(
            "你是提醒与日历意图解析器。只输出 JSON。"
            "字段: intent, confidence, run_at_local, reminder_content, calendar_scope, calendar_date, schedule_status_filter。"
            "intent 仅可为 reminder, calendar, time_query, context_recall, unknown。"
            "如果用户是提醒请求（例如‘明天中午12点提醒我开会’），intent=reminder，"
            "run_at_local 必须输出 YYYY-MM-DD HH:MM（基于用户时区）。"
            "reminder_content 要提炼提醒事项本体，去掉时间描述。"
            "如果用户是查看日历/日程，intent=calendar，calendar_scope 用 today/week/month/date/yesterday/day_before_yesterday/last_week。"
            "当 scope=date 时输出 calendar_date=YYYY-MM-DD。"
            "当用户说昨天/前天时，优先用 calendar_scope=yesterday/day_before_yesterday；"
            "当用户说上周时，用 calendar_scope=last_week。"
            "如果用户在问现在几点/今天几号/星期几，intent=time_query。"
            "如果用户在问之前聊了什么/刚才问了什么，intent=context_recall。"
            "schedule_status_filter 仅可为 all/pending/executed/cancelled。"
            "当用户询问已完成/未完成/已取消时必须提取该字段；若未提及则 all。"
            f"用户时区: {tz}。当前本地时间: {now_local}。"
        )
    )
    human = HumanMessage(
        content=(
            f"会话上下文:\n{conversation_context}\n\n"
            f"用户输入:\n{content}"
        )
    )
    response = await llm.ainvoke([system, human])
    return _parse_json_object(str(response.content))


async def _answer_context_recall(content: str, conversation_context: str) -> str:
    llm = get_llm()
    system = SystemMessage(
        content=(
            "你是会话回忆助手。请仅依据给定会话上下文回答用户问题。"
            "若上下文不足，明确说明缺失点，不要假装没有记忆能力。"
            "回答简洁。"
        )
    )
    human = HumanMessage(
        content=(
            f"会话上下文:\n{conversation_context}\n\n"
            f"用户问题:\n{content}"
        )
    )
    response = await llm.ainvoke([system, human])
    text = str(response.content or "").strip()
    return text or "我这边没有提取到足够的历史内容，请你再说一次具体要回忆的点。"


def _resolve_calendar_window_from_fields(
    scope: str,
    calendar_date: str | None,
) -> tuple[datetime, datetime, str] | None:
    today = _local_today()
    scope_key = (scope or "").strip().lower()
    if scope_key == "yesterday":
        start = today - timedelta(days=1)
        end = start + timedelta(days=1)
        return datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time()), _format_window_label("昨天", start, end)
    if scope_key == "day_before_yesterday":
        start = today - timedelta(days=2)
        end = start + timedelta(days=1)
        return datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time()), _format_window_label("前天", start, end)
    if scope_key in {"today", "day"}:
        start = today
        end = today + timedelta(days=1)
        return datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time()), _format_window_label("今天", start, end)
    if scope_key == "last_week":
        start = today - timedelta(days=today.weekday() + 7)
        end = start + timedelta(days=7)
        return datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time()), _format_window_label("上周", start, end)
    if scope_key == "week":
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=7)
        return datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time()), _format_window_label("本周", start, end)
    if scope_key == "month":
        start = today.replace(day=1)
        if start.month == 12:
            end = date(start.year + 1, 1, 1)
        else:
            end = date(start.year, start.month + 1, 1)
        return datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time()), _format_window_label("本月", start, end)
    if scope_key == "date" and calendar_date:
        try:
            start = date.fromisoformat(calendar_date.strip())
            end = start + timedelta(days=1)
            return datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time()), _format_window_label(start.isoformat(), start, end)
        except Exception:
            return None
    return None


def _normalize_schedule_status_filter(value: str | None) -> str:
    key = (value or "").strip().lower()
    return SCHEDULE_STATUS_MAP.get(key, "all")


def _resolve_schedule_status_filter(parsed: dict) -> str:
    return _normalize_schedule_status_filter(str(parsed.get("schedule_status_filter") or "all"))


def _fmt_dt(value: datetime | None, pattern: str = "%m-%d %H:%M") -> str:
    if not value:
        return "-"
    if value.tzinfo is not None:
        value = value.astimezone(ZoneInfo(get_settings().timezone))
    return value.strftime(pattern)


async def _query_calendar_rows(session, user_id: int, start_at: datetime, end_at: datetime) -> tuple[list[Ledger], list[Schedule]]:
    ledger_start = _to_utc_naive(start_at)
    ledger_end = _to_utc_naive(end_at)
    ledgers_result = await session.execute(
        select(Ledger)
        .where(
            Ledger.user_id == user_id,
            Ledger.transaction_date >= ledger_start,
            Ledger.transaction_date < ledger_end,
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
    return list(ledgers_result.scalars().all()), list(schedules_result.scalars().all())


def _filter_schedules_by_status(schedules: list[Schedule], status_filter: str) -> list[Schedule]:
    target = SCHEDULE_STATUS_DB.get(status_filter, None)
    if not target:
        return schedules
    return [item for item in schedules if str(item.status or "").upper() == target]


def _build_calendar_payload(ledgers: list[Ledger], schedules: list[Schedule]) -> dict[str, Any]:
    return {
        "ledgers": [
            {
                "id": item.id,
                "datetime": _fmt_dt(item.transaction_date, "%Y-%m-%d %H:%M"),
                "amount": round(float(item.amount), 2),
                "currency": item.currency,
                "category": item.category,
                "item": item.item,
            }
            for item in ledgers[:50]
        ],
        "schedules": [
            {
                "id": item.id,
                "datetime": _fmt_dt(item.trigger_time, "%Y-%m-%d %H:%M"),
                "content": item.content,
                "status": str(item.status or "").upper(),
            }
            for item in schedules[:50]
        ],
    }


async def _answer_calendar_with_llm(
    content: str,
    conversation_context: str,
    label: str,
    schedule_status_filter: str,
    ledgers: list[Ledger],
    schedules: list[Schedule],
) -> str:
    llm = get_llm()
    payload = _build_calendar_payload(ledgers, schedules)
    system = SystemMessage(
        content=(
            "你是日历与日程答复助手。你只能基于给定数据回答，不得编造。"
            "回答时要贴合用户问题，不要固定模板。"
            "如果用户问已完成/未完成/已取消，请按 status 进行筛选或分组。"
            "status 定义: PENDING=未完成, EXECUTED=已完成, CANCELLED=已取消。"
            "涉及明细时请给出时间。"
            "若数据为空，明确说没有相关记录。"
            "默认给出简洁自然的中文回答，不要输出技术说明。"
            "不要提到 JSON、数组、字段、数据源、会话上下文。"
            "除非用户明确要求按状态统计，否则不要枚举各状态计数。"
            "不要重复句子或段落。"
        )
    )
    human = HumanMessage(
        content=(
            f"查询范围: {label}\n"
            f"状态筛选: {schedule_status_filter}\n\n"
            f"会话上下文:\n{conversation_context}\n\n"
            f"用户问题:\n{content}\n\n"
            f"数据(JSON):\n{json.dumps(payload, ensure_ascii=False)}"
        )
    )
    response = await llm.ainvoke([system, human])
    return _sanitize_llm_text(str(response.content or ""))


def _render_calendar_text(ledgers: list[Ledger], schedules: list[Schedule], label: str, schedule_status_filter: str) -> str:
    filtered_schedules = _filter_schedules_by_status(schedules, schedule_status_filter)
    filter_title = {
        "all": "全部状态",
        "pending": "未完成",
        "executed": "已完成",
        "cancelled": "已取消",
    }.get(schedule_status_filter, "全部状态")
    lines = [f"{label}日历："]
    lines.append(f"- 账单 {len(ledgers)} 笔")
    lines.append(f"- 日程 {len(filtered_schedules)} 条（{filter_title}）")

    if ledgers:
        lines.append("账单：")
        for row in ledgers[:8]:
            lines.append(
                f"- {_fmt_dt(row.transaction_date)} #{row.id} {row.item} {row.amount:.2f} {row.currency} ({row.category})"
            )
        if len(ledgers) > 8:
            lines.append(f"- ... 其余 {len(ledgers) - 8} 笔")

    if filtered_schedules:
        lines.append("日程：")
        for row in filtered_schedules[:8]:
            lines.append(
                f"- {_fmt_dt(row.trigger_time)} #{row.id} {row.content} [{row.status}]"
            )
        if len(filtered_schedules) > 8:
            lines.append(f"- ... 其余 {len(filtered_schedules) - 8} 条")

    if not ledgers and not filtered_schedules:
        lines.append("这段时间没有账单和日程。")

    lines.append("可用：`/calendar today`、`/calendar week`、`/calendar month`、`/calendar 2026-02-07`")
    return "\n".join(lines)


async def _answer_calendar(
    session,
    user_id: int,
    content: str,
    conversation_context: str,
    start_at: datetime,
    end_at: datetime,
    label: str,
    schedule_status_filter: str,
) -> str:
    ledgers, schedules = await _query_calendar_rows(session, user_id, start_at, end_at)
    filtered_schedules = _filter_schedules_by_status(schedules, schedule_status_filter)
    try:
        llm_text = await _answer_calendar_with_llm(
            content=content,
            conversation_context=conversation_context,
            label=label,
            schedule_status_filter=schedule_status_filter,
            ledgers=ledgers,
            schedules=filtered_schedules,
        )
        if llm_text:
            return llm_text
    except Exception:
        pass
    return _render_calendar_text(ledgers, schedules, label, schedule_status_filter)


async def secretary_node(state: GraphState) -> GraphState:
    message = state["message"]
    session = get_session()
    scheduler = get_scheduler()
    user = await session.get(User, state["user_id"])
    if not user:
        return {**state, "responses": ["未找到用户信息。"]}
    content = (message.content or "").strip()
    context_text = render_conversation_context(state)

    parsed: dict = {}
    try:
        parsed = await _understand_secretary_message(content, context_text)
    except Exception:
        parsed = {}

    intent = str(parsed.get("intent") or "").strip().lower()
    confidence = float(parsed.get("confidence") or 0.0)
    schedule_status_filter = _resolve_schedule_status_filter(parsed)

    if intent == "context_recall" and confidence >= 0.55:
        try:
            recall_text = await _answer_context_recall(content, context_text)
            return {**state, "responses": [recall_text]}
        except Exception:
            return {
                **state,
                "responses": ["我能读取当前会话上下文，但这次回忆失败了，请重试一次。"],
            }

    if intent == "time_query" and confidence >= 0.55:
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

    if intent == "calendar" and confidence >= 0.55:
        scope = str(parsed.get("calendar_scope") or "").strip().lower()
        calendar_date = str(parsed.get("calendar_date") or "").strip() or None
        llm_window = _resolve_calendar_window_from_fields(scope, calendar_date)
        if llm_window:
            start_at, end_at, label = llm_window
            response = await _answer_calendar(
                session=session,
                user_id=user.id,
                content=content,
                conversation_context=context_text,
                start_at=start_at,
                end_at=end_at,
                label=label,
                schedule_status_filter=schedule_status_filter,
            )
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

    # Deterministic command fallback.
    calendar_window = _resolve_calendar_window(content)
    if CALENDAR_CMD_PATTERN.match(content.strip()):
        if not calendar_window:
            return {
                **state,
                "responses": ["日历命令格式：`/calendar today|week|month|YYYY-MM-DD`。"],
            }
        start_at, end_at, label = calendar_window
        response = await _answer_calendar(
            session=session,
            user_id=user.id,
            content=content,
            conversation_context=context_text,
            start_at=start_at,
            end_at=end_at,
            label=label,
            schedule_status_filter=schedule_status_filter,
        )
        return {**state, "responses": [response]}

    if intent == "reminder":
        return {
            **state,
            "responses": ["我收到了提醒需求，但时间还不够明确。请给我具体时间，例如：`明天中午12点提醒我开会`。"],
        }

    return {
        **state,
        "responses": [
            "我这边主要负责提醒和日历。可直接说：`明天中午12点提醒我开会`，或 `看下本周日程和账单`。"
        ],
    }
