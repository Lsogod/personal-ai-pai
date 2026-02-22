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
RELATIVE_SECONDS_PATTERN = re.compile(r"(\d+)\s*秒(?:钟)?后")
RELATIVE_MINUTES_PATTERN = re.compile(r"(\d+)\s*分(?:钟)?后")
RELATIVE_HOURS_PATTERN = re.compile(r"(\d+)\s*小?时后")
EXPLICIT_OFFSET_PATTERN = re.compile(r"(?:提前|前)\s*(\d+)\s*(分钟|分|小时|时|天)")
TECHNICAL_LEAK_PATTERN = re.compile(r"(json|payload|字段|数组|schema|schedules|ledgers)", re.IGNORECASE)
REMINDER_CONTENT_CLEAN_PATTERNS = (
    re.compile(r"^\s*(请|请你|麻烦|帮我|帮忙|记得|到时候|之后|然后|再)\s*"),
    re.compile(r"^\s*(今天|明天|后天|今晚|今早|明早|中午|下午|晚上|早上|夜里|凌晨)\s*"),
    re.compile(r"^\s*\d+\s*秒(?:钟)?后\s*"),
    re.compile(r"^\s*\d+\s*分(?:钟)?后\s*"),
    re.compile(r"^\s*\d+\s*小?时后\s*"),
    re.compile(r"^\s*\d{1,2}\s*点(?:\s*\d{1,2}\s*分?)?\s*"),
    re.compile(r"^\s*(提醒(?:一下)?我?|叫我|通知我)\s*"),
    re.compile(r"^\s*(去|要|把)\s*"),
)
REMINDER_PLACEHOLDER_WORDS = {
    "我",
    "一下",
    "一下子",
    "提醒",
    "事项",
    "这个",
    "这件事",
    "那个",
    "它",
}
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
    "failed": "failed",
    "失败": "failed",
    "未送达": "failed",
}
SCHEDULE_STATUS_DB = {
    "all": None,
    "pending": "PENDING",
    "executed": "EXECUTED",
    "cancelled": "CANCELLED",
    "failed": "FAILED",
}
PRIORITY_SET = {"low", "medium", "high", "critical"}
EVENT_TYPE_DEFAULT_OFFSETS = {
    "meeting": [60, 15, 0],
    "travel": [180, 60, 0],
    "deadline": [1440, 120, 0],
    "appointment": [120, 30, 0],
    "payment": [1440, 60, 0],
    "study": [60, 15, 0],
    "work": [30, 0],
    "family": [60, 15, 0],
    "task": [10, 0],
    "other": [10, 0],
}
EVENT_TYPE_ALIASES = {
    "meeting": "meeting",
    "会议": "meeting",
    "开会": "meeting",
    "面试": "meeting",
    "travel": "travel",
    "出行": "travel",
    "航班": "travel",
    "高铁": "travel",
    "火车": "travel",
    "deadline": "deadline",
    "截止": "deadline",
    "到期": "deadline",
    "提交": "deadline",
    "appointment": "appointment",
    "约会": "appointment",
    "就医": "appointment",
    "看医生": "appointment",
    "payment": "payment",
    "缴费": "payment",
    "还款": "payment",
    "账单日": "payment",
    "study": "study",
    "学习": "study",
    "上课": "study",
    "work": "work",
    "工作": "work",
    "family": "family",
    "家庭": "family",
    "task": "task",
    "todo": "task",
    "待办": "task",
    "other": "other",
    "其他": "other",
}
MAX_REMINDER_COUNT = 3
MIN_OFFSET_GAP_MINUTES = 10


def _schedule_status_label(value: str | None) -> str:
    key = str(value or "").upper()
    return {
        "PENDING": "未完成",
        "EXECUTED": "已完成",
        "CANCELLED": "已取消",
        "FAILED": "失败",
    }.get(key, key or "未知")


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
    normalized = raw.replace("T", " ").replace("/", "-")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    patterns = (
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
    )
    for pattern in patterns:
        try:
            dt = datetime.strptime(normalized, pattern)
            local_tz = ZoneInfo(timezone)
            # Keep naive local datetime to match scheduler DateTrigger timezone handling.
            _ = dt.replace(tzinfo=local_tz)
            return dt
        except Exception:
            continue
    try:
        dt = datetime.fromisoformat(normalized)
        local_tz = ZoneInfo(timezone)
        if dt.tzinfo is not None:
            return dt.astimezone(local_tz).replace(tzinfo=None)
        _ = dt.replace(tzinfo=local_tz)
        return dt
    except Exception:
        return None


def _parse_relative_reminder_local(content: str, timezone: str) -> datetime | None:
    text = (content or "").strip()
    if not text:
        return None
    now_local = datetime.now(ZoneInfo(timezone)).replace(tzinfo=None)
    if match := RELATIVE_SECONDS_PATTERN.search(text):
        seconds = int(match.group(1))
        if seconds <= 0:
            return None
        # Keep second precision when user explicitly asks for seconds.
        return (now_local + timedelta(seconds=seconds)).replace(microsecond=0)
    if match := RELATIVE_MINUTES_PATTERN.search(text):
        minutes = int(match.group(1))
        if minutes <= 0:
            return None
        # Relative minute reminders are also second-precision by requirement.
        return (now_local + timedelta(minutes=minutes)).replace(microsecond=0)
    if match := RELATIVE_HOURS_PATTERN.search(text):
        hours = int(match.group(1))
        if hours <= 0:
            return None
        return (now_local + timedelta(hours=hours)).replace(second=0, microsecond=0)
    return None


def _relative_precision_mode(content: str) -> str:
    text = (content or "").strip()
    if not text:
        return "none"
    if RELATIVE_SECONDS_PATTERN.search(text) or RELATIVE_MINUTES_PATTERN.search(text):
        return "second"
    if RELATIVE_HOURS_PATTERN.search(text):
        return "minute"
    return "none"


def _format_reminder_time(dt: datetime) -> str:
    if dt.second != 0:
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    return dt.strftime("%Y-%m-%d %H:%M")


def _clean_reminder_content(text: str) -> str:
    cleaned = (text or "").strip().strip("`\"' ")
    if not cleaned:
        return ""

    changed = True
    while changed and cleaned:
        changed = False
        for pattern in REMINDER_CONTENT_CLEAN_PATTERNS:
            updated = pattern.sub("", cleaned, count=1).strip()
            if updated != cleaned:
                cleaned = updated
                changed = True

    cleaned = cleaned.strip(" ：:，,。.!！？?;；")
    return cleaned


def _is_placeholder_reminder_content(text: str) -> bool:
    normalized = re.sub(r"\s+", "", (text or "").strip().lower())
    if not normalized:
        return True
    if normalized in REMINDER_PLACEHOLDER_WORDS:
        return True
    if len(normalized) <= 1:
        return True
    return False


def _resolve_reminder_content(llm_value: str, user_content: str) -> str:
    candidates = (
        _clean_reminder_content(llm_value),
        _clean_reminder_content(user_content),
    )
    for candidate in candidates:
        if candidate and not _is_placeholder_reminder_content(candidate):
            return candidate
    return "待办提醒"


def _normalize_event_type(value: str | None) -> str:
    key = (value or "").strip().lower()
    if not key:
        return "task"
    mapped = EVENT_TYPE_ALIASES.get(key, key)
    if mapped in EVENT_TYPE_DEFAULT_OFFSETS:
        return mapped
    return "other"


def _normalize_priority(value: str | None) -> str:
    key = (value or "").strip().lower()
    return key if key in PRIORITY_SET else "medium"


def _to_minutes(amount: int, unit: str) -> int:
    if unit in {"分钟", "分"}:
        return amount
    if unit in {"小时", "时"}:
        return amount * 60
    if unit == "天":
        return amount * 24 * 60
    return amount


def _parse_offset_values(value: Any) -> list[int]:
    values: list[int] = []
    if value is None:
        return values
    if isinstance(value, str):
        parts = [item for item in re.split(r"[,\s，、]+", value.strip()) if item]
        iterable = parts
    elif isinstance(value, list):
        iterable = value
    else:
        iterable = [value]
    for item in iterable:
        try:
            minutes = int(float(str(item).strip()))
        except Exception:
            continue
        if minutes <= 0 or minutes > 60 * 24 * 30:
            continue
        values.append(minutes)
    return values


def _extract_offsets_from_text(content: str) -> list[int]:
    values: list[int] = []
    text = (content or "").strip()
    if not text:
        return values
    for amount_text, unit in EXPLICIT_OFFSET_PATTERN.findall(text):
        try:
            amount = int(amount_text)
        except Exception:
            continue
        if amount <= 0:
            continue
        minutes = _to_minutes(amount, unit)
        if 0 < minutes <= 60 * 24 * 30:
            values.append(minutes)
    return values


def _resolve_explicit_offsets(parsed: dict, content: str) -> list[int]:
    values = _parse_offset_values(parsed.get("explicit_offsets_minutes"))
    values.extend(_extract_offsets_from_text(content))
    return _dedupe_desc(values)


def _dedupe_desc(values: list[int]) -> list[int]:
    return sorted({int(item) for item in values if int(item) >= 0}, reverse=True)


def _cap_offsets(values: list[int], max_count: int) -> list[int]:
    offsets = _dedupe_desc(values)
    if 0 not in offsets:
        offsets.append(0)
    offsets = _dedupe_desc(offsets)
    if len(offsets) <= max_count:
        return offsets
    non_zero = [item for item in offsets if item > 0]
    kept = non_zero[: max_count - 1] + [0]
    return _dedupe_desc(kept)


def _enforce_offset_gap(values: list[int], min_gap_minutes: int) -> list[int]:
    offsets = _dedupe_desc(values)
    if not offsets:
        return [0]
    kept: list[int] = []
    for item in offsets:
        if not kept:
            kept.append(item)
            continue
        if abs(kept[-1] - item) >= min_gap_minutes:
            kept.append(item)
    if 0 not in kept:
        kept.append(0)
    return _dedupe_desc(kept)


def _priority_extra_offset(priority: str) -> int:
    return {
        "critical": 120,
        "high": 30,
        "medium": 0,
        "low": 0,
    }.get(priority, 0)


def _resolve_reminder_offsets(
    *,
    event_type: str,
    priority: str,
    explicit_offsets: list[int],
    relative_mode: str,
) -> list[int]:
    if relative_mode != "none":
        return [0]

    if explicit_offsets:
        offsets = list(explicit_offsets)
    else:
        offsets = list(EVENT_TYPE_DEFAULT_OFFSETS.get(event_type, EVENT_TYPE_DEFAULT_OFFSETS["other"]))
        extra = _priority_extra_offset(priority)
        if extra > 0:
            offsets.append(extra)

    offsets = _enforce_offset_gap(offsets, MIN_OFFSET_GAP_MINUTES)
    offsets = _cap_offsets(offsets, MAX_REMINDER_COUNT)
    return offsets


def _offset_label(minutes: int) -> str:
    if minutes <= 0:
        return "准点"
    if minutes % (24 * 60) == 0:
        days = minutes // (24 * 60)
        return f"提前{days}天"
    if minutes % 60 == 0:
        hours = minutes // 60
        return f"提前{hours}小时"
    return f"提前{minutes}分钟"


def _decorate_reminder_content(content: str, offset_minutes: int) -> str:
    if offset_minutes <= 0:
        return content
    return f"{content}（{_offset_label(offset_minutes)}）"


def _format_offsets_for_reply(offsets: list[int], event_time: datetime) -> str:
    if not offsets:
        return _format_reminder_time(event_time)
    labels: list[str] = []
    for offset in _dedupe_desc(offsets):
        if offset > 0:
            labels.append(_offset_label(offset))
        else:
            labels.append(f"准点（{_format_reminder_time(event_time)}）")
    return "、".join(labels)


async def _understand_reminder_fallback(content: str, conversation_context: str) -> dict:
    settings = get_settings()
    tz = settings.timezone
    now_local = datetime.now(ZoneInfo(tz)).strftime("%Y-%m-%d %H:%M")
    llm = get_llm(node_name="schedule_manager")
    system = SystemMessage(
        content=(
            "你是提醒专用解析器。只输出 JSON。"
            "字段: is_reminder, confidence, run_at_local, reminder_content, event_type, priority, explicit_offsets_minutes, event_tags。"
            "is_reminder 只可为 true/false。"
            "如果用户表达了提醒诉求（例如“1分钟后提醒我测试”“明早9点提醒我开会”），is_reminder=true。"
            "run_at_local 必须换算为用户时区时间，格式 YYYY-MM-DD HH:MM[:SS]。"
            "若用户明确到秒（如“30秒后”），必须保留秒；否则秒填 00。"
            "reminder_content 提炼提醒事项本体。"
            "event_type 仅可为 meeting/travel/deadline/appointment/payment/study/work/family/task/other。"
            "priority 仅可为 low/medium/high/critical。"
            "若用户明确说“提前X分钟/小时/天”，explicit_offsets_minutes 输出分钟整数数组；否则 []。"
            "event_tags 是可选标签数组，用于补充细分场景。"
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


async def _create_reminder(
    *,
    session,
    scheduler,
    user: User,
    reminder_content: str,
    event_time: datetime,
    event_type: str,
    priority: str,
    offsets_minutes: list[int],
) -> str:
    now_local = datetime.now(ZoneInfo(get_settings().timezone)).replace(tzinfo=None)
    created_offsets: list[int] = []

    for offset in _dedupe_desc(offsets_minutes):
        trigger_time = event_time - timedelta(minutes=offset)
        if trigger_time <= now_local + timedelta(seconds=1):
            continue
        job_id = str(uuid4())
        schedule = Schedule(
            user_id=user.id,
            job_id=job_id,
            content=_decorate_reminder_content(reminder_content, offset),
            trigger_time=trigger_time,
        )
        session.add(schedule)
        await session.flush()
        scheduler.add_job(job_id, trigger_time, send_reminder_job, schedule.id)
        created_offsets.append(offset)

    if not created_offsets:
        return ""

    await session.commit()

    try:
        from app.services.audit import log_event

        await log_event(
            session,
            action="schedule_created",
            platform=user.platform,
            user_id=user.id,
            detail={
                "content": reminder_content,
                "event_time": event_time.isoformat(),
                "event_type": event_type,
                "priority": priority,
                "offsets_minutes": _dedupe_desc(created_offsets),
                "created_count": len(created_offsets),
                "via": "llm",
            },
        )
    except Exception:
        pass

    normalized_offsets = _dedupe_desc(created_offsets)
    if normalized_offsets == [0]:
        return f"好的，提醒已设置：{_format_reminder_time(event_time)}。"
    return f"好的，已设置{len(normalized_offsets)}次提醒：{_format_offsets_for_reply(normalized_offsets, event_time)}。"


async def _understand_schedule_message(content: str, conversation_context: str) -> dict:
    settings = get_settings()
    tz = settings.timezone
    now_local = datetime.now(ZoneInfo(tz)).strftime("%Y-%m-%d %H:%M")
    llm = get_llm(node_name="schedule_manager")
    system = SystemMessage(
        content=(
            "你是提醒与日历意图解析器。只输出 JSON。"
            "字段: intent, confidence, run_at_local, reminder_content, event_type, priority, explicit_offsets_minutes, event_tags, calendar_scope, calendar_date, schedule_status_filter。"
            "intent 仅可为 reminder, calendar, time_query, context_recall, unknown。"
            "如果用户是提醒请求（例如‘明天中午12点提醒我开会’），intent=reminder，"
            "run_at_local 必须输出用户时区时间，格式 YYYY-MM-DD HH:MM[:SS]。"
            "若用户明确到秒（如“30秒后”），必须保留秒；否则秒填 00。"
            "reminder_content 要提炼提醒事项本体，去掉时间描述。"
            "event_type 仅可为 meeting/travel/deadline/appointment/payment/study/work/family/task/other。"
            "priority 仅可为 low/medium/high/critical。"
            "如果用户明确说“提前X分钟/小时/天”，explicit_offsets_minutes 输出分钟整数数组；否则 []。"
            "event_tags 是可选标签数组，用于补充细分场景。"
            "如果用户是查看日历/日程，intent=calendar，calendar_scope 用 today/week/month/date/yesterday/day_before_yesterday/last_week。"
            "当 scope=date 时输出 calendar_date=YYYY-MM-DD。"
            "当用户说昨天/前天时，优先用 calendar_scope=yesterday/day_before_yesterday；"
            "当用户说上周时，用 calendar_scope=last_week。"
            "如果用户在问现在几点/今天几号/星期几，intent=time_query。"
            "如果用户在问之前聊了什么/刚才问了什么，intent=context_recall。"
            "schedule_status_filter 仅可为 all/pending/executed/cancelled。"
            "当用户询问已完成/未完成/已取消/失败时必须提取该字段；若未提及则 all。"
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
    llm = get_llm(node_name="schedule_manager")
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


def _fmt_dt(value: datetime | None, pattern: str = "%m-%d %H:%M", assume_utc: bool = False) -> str:
    if not value:
        return "-"
    tz = ZoneInfo(get_settings().timezone)
    if value.tzinfo is not None:
        value = value.astimezone(tz)
    elif assume_utc:
        value = value.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
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
                "datetime": _fmt_dt(item.transaction_date, "%Y-%m-%d %H:%M", assume_utc=True),
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
    llm = get_llm(node_name="schedule_manager")
    payload = _build_calendar_payload(ledgers, schedules)
    system = SystemMessage(
        content=(
            "你是日历与日程答复助手。你只能基于给定数据回答，不得编造。"
            "回答时要贴合用户问题，不要固定模板。"
            "如果用户问已完成/未完成/已取消，请按 status 进行筛选或分组。"
            "status 定义: PENDING=未完成, EXECUTED=已完成, CANCELLED=已取消。"
            "FAILED=推送失败。"
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
        "failed": "失败",
    }.get(schedule_status_filter, "全部状态")
    lines = [f"{label}日历："]
    lines.append(f"- 账单 {len(ledgers)} 笔")
    lines.append(f"- 日程 {len(filtered_schedules)} 条（{filter_title}）")

    if ledgers:
        lines.append("账单：")
        for row in ledgers[:8]:
            lines.append(
                f"- {_fmt_dt(row.transaction_date, assume_utc=True)} #{row.id} {row.item} {row.amount:.2f} {row.currency} ({row.category})"
            )
        if len(ledgers) > 8:
            lines.append(f"- ... 其余 {len(ledgers) - 8} 笔")

    if filtered_schedules:
        lines.append("日程：")
        for row in filtered_schedules[:8]:
            lines.append(
                f"- {_fmt_dt(row.trigger_time)} #{row.id} {row.content} [{_schedule_status_label(row.status)}]"
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


async def schedule_manager_node(state: GraphState) -> GraphState:
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
        parsed = await _understand_schedule_message(content, context_text)
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

    settings = get_settings()
    reminder_candidate = parsed
    reminder_intent = intent == "reminder"
    reminder_confidence = confidence
    if intent == "unknown":
        try:
            reminder_candidate = await _understand_reminder_fallback(content, context_text)
            reminder_intent = bool(reminder_candidate.get("is_reminder"))
            reminder_confidence = float(reminder_candidate.get("confidence") or 0.0)
        except Exception:
            reminder_intent = False

    relative_trigger = _parse_relative_reminder_local(content, settings.timezone)
    relative_mode = _relative_precision_mode(content)
    if not reminder_intent and relative_trigger is not None:
        reminder_intent = True
        reminder_confidence = max(reminder_confidence, 0.5)
        reminder_candidate = {
            "run_at_local": relative_trigger.strftime("%Y-%m-%d %H:%M:%S"),
            "reminder_content": "",
        }

    if reminder_intent and reminder_confidence >= 0.35:
        trigger_time = _parse_run_at_local(str(reminder_candidate.get("run_at_local") or ""), settings.timezone)
        if relative_mode == "second" and relative_trigger is not None:
            # For "X秒后/X分钟后", always use second-precision relative scheduling.
            trigger_time = relative_trigger
        elif trigger_time is None:
            trigger_time = relative_trigger
        elif relative_mode in {"minute", "none"}:
            # Absolute/bigger-time reminders keep minute precision.
            trigger_time = trigger_time.replace(second=0, microsecond=0)
        now_local = datetime.now(ZoneInfo(settings.timezone)).replace(tzinfo=None)
        if trigger_time and trigger_time > now_local + timedelta(seconds=1):
            reminder_content = _resolve_reminder_content(
                str(reminder_candidate.get("reminder_content") or ""),
                content,
            )
            event_type = _normalize_event_type(str(reminder_candidate.get("event_type") or ""))
            priority = _normalize_priority(str(reminder_candidate.get("priority") or "medium"))
            explicit_offsets = _resolve_explicit_offsets(reminder_candidate, content)
            offsets_minutes = _resolve_reminder_offsets(
                event_type=event_type,
                priority=priority,
                explicit_offsets=explicit_offsets,
                relative_mode=relative_mode,
            )
            text = await _create_reminder(
                session=session,
                scheduler=scheduler,
                user=user,
                reminder_content=reminder_content,
                event_time=trigger_time,
                event_type=event_type,
                priority=priority,
                offsets_minutes=offsets_minutes,
            )
            if text:
                return {**state, "responses": [text]}
            return {
                **state,
                "responses": ["提醒时间已过近，无法安排提前提醒。请给我一个稍晚一点的时间。"],
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

    if reminder_intent:
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
