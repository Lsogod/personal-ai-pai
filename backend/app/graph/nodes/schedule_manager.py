import json
import asyncio
import re
from datetime import date, datetime, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.core.config import get_settings
from app.graph.context import render_conversation_context
from app.graph.state import GraphState
from app.models.ledger import Ledger
from app.models.reminder_delivery import ReminderDelivery
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
    re.compile(r"^\s*我(?:有|要|想|得)?\s*(?:一个|个)?\s*"),
    re.compile(r"^\s*(去|要|把)\s*"),
)
SCHEDULE_DECORATION_PATTERN = re.compile(r"\s*[（(][^）)]*提前[^）)]*[）)]\s*$")
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
EVENT_TYPE_DEFAULT_TITLES = {
    "meeting": "开会",
    "travel": "出行",
    "deadline": "截止事项",
    "appointment": "预约事项",
    "payment": "缴费",
    "study": "学习",
    "work": "工作事项",
    "family": "家庭事项",
    "task": "待办",
    "other": "待办提醒",
}


class ReminderTitleExtraction(BaseModel):
    title: str = Field(default="")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
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
    if target in {"tomorrow", "明天"}:
        start = today + timedelta(days=1)
        end = start + timedelta(days=1)
        return datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time()), _format_window_label("明天", start, end)
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


def _resolve_reminder_content(llm_value: str, user_content: str, event_type: str = "task") -> str:
    candidates = (
        _clean_reminder_content(llm_value),
        _clean_reminder_content(user_content),
    )
    for candidate in candidates:
        if candidate and not _is_placeholder_reminder_content(candidate):
            return candidate
    normalized_event_type = _normalize_event_type(event_type)
    return EVENT_TYPE_DEFAULT_TITLES.get(normalized_event_type, "待办提醒")


async def _generate_reminder_title_with_llm(
    *,
    user_content: str,
    conversation_context: str,
    hint_title: str,
    event_type: str,
) -> str:
    llm = get_llm(node_name="schedule_manager")
    runnable = llm.with_structured_output(ReminderTitleExtraction)
    system = SystemMessage(
        content=(
            "你是提醒标题生成器，只输出结构化字段 title/confidence。"
            "title 必须是 2-12 字的简洁事项名，不要包含时间、称谓、语气词。"
            "尽量使用可执行动词或名词短语。"
            "若 event_type=meeting，优先生成“开会/会议/项目评审会”等标题。"
            "示例："
            "“明天中午12点我有个会” -> “开会”；"
            "“下周一下午3点项目评审” -> “项目评审会”；"
            "“明早提醒我交报告” -> “交报告”。"
            "若无法判断，返回 hint_title。"
        )
    )
    human = HumanMessage(
        content=(
            f"event_type: {event_type}\n"
            f"hint_title: {hint_title}\n\n"
            f"会话上下文:\n{conversation_context}\n\n"
            f"用户原话:\n{user_content}"
        )
    )
    try:
        result = await asyncio.wait_for(runnable.ainvoke([system, human]), timeout=20)
    except Exception:
        return hint_title
    candidate = _clean_reminder_content(str(getattr(result, "title", "") or ""))
    if candidate and not _is_placeholder_reminder_content(candidate):
        return candidate
    return hint_title


def _schedule_content_root(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    return SCHEDULE_DECORATION_PATTERN.sub("", raw).strip()


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
            "reminder_content 必须是提醒标题本体（2-12字），去掉人称、时间、语气词。"
            "例如“明天中午12点我有个会” -> reminder_content=“开会”。"
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
            "你是提醒与日历意图解析器，只输出 JSON。"
            "字段: intent, confidence, run_at_local, reminder_content, target_content, target_ids, reference_mode, selection_mode, event_type, priority, explicit_offsets_minutes, event_tags, calendar_scope, calendar_date, schedule_status_filter。"
            "intent 仅可为 reminder, update_by_name, update_by_scope, delete_by_name, delete_by_scope, calendar, time_query, context_recall, unknown。"
            "创建提醒时 intent=reminder。"
            "按名称修改提醒（如‘把开会改到明天11点’）intent=update_by_name，并给 target_content 与 run_at_local。"
            "按名称删除提醒（如‘删除开会这个提醒’）intent=delete_by_name，并给 target_content。"
            "按范围删除提醒（如‘删除明天所有未完成提醒’）intent=delete_by_scope。"
            "按范围修改提醒（如‘把明天所有提醒改到11点’）intent=update_by_scope。"
            "run_at_local 使用用户时区，格式 YYYY-MM-DD HH:MM[:SS]。"
            "若用户明确到秒，保留秒；否则秒可为00。"
            "reminder_content 必须是提醒标题本体（2-12字），去掉“我/你/提醒我”、时间词和语气词。"
            "例如“明天中午12点我有个会” -> reminder_content=“开会”。"
            "event_type 仅可为 meeting/travel/deadline/appointment/payment/study/work/family/task/other。"
            "priority 仅可为 low/medium/high/critical。"
            "explicit_offsets_minutes 为分钟整数数组，未提及则 []。"
            "查看日历/日程时 intent=calendar，calendar_scope 仅可为 today/tomorrow/week/month/date/yesterday/day_before_yesterday/last_week。"
            "若 scope=date，calendar_date 输出 YYYY-MM-DD。"
            "询问当前时间/日期/星期 intent=time_query。"
            "询问上下文回顾 intent=context_recall。"
            "schedule_status_filter 仅可为 all/pending/executed/cancelled，未提及时 all。"
            "reference_mode 仅可为 by_id/by_name/by_scope/latest/last_result_set/auto。"
            "当用户说‘这几个/这些/刚才那些’时，reference_mode=last_result_set。"
            "selection_mode 仅可为 all/single/subset/auto。"
            "confidence 范围 0~1。"
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
    if scope_key in {"tomorrow"}:
        start = today + timedelta(days=1)
        end = start + timedelta(days=1)
        return datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time()), _format_window_label("明天", start, end)
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


async def _query_editable_schedules(
    session,
    user_id: int,
    limit: int = 120,
    target_hint: str = "",
) -> list[Schedule]:
    hint = (target_hint or "").strip()
    if hint:
        hinted = await session.execute(
            select(Schedule)
            .where(
                Schedule.user_id == user_id,
                Schedule.status == "PENDING",
                Schedule.content.ilike(f"%{hint}%"),
            )
            .order_by(Schedule.trigger_time.asc(), Schedule.id.asc())
            .limit(min(limit, 40))
        )
        hinted_rows = list(hinted.scalars().all())
        if hinted_rows:
            return hinted_rows

    result = await session.execute(
        select(Schedule)
        .where(Schedule.user_id == user_id, Schedule.status == "PENDING")
        .order_by(Schedule.id.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


def _build_schedule_target_payload(rows: list[Schedule]) -> dict[str, Any]:
    return {
        "candidates": [
            {
                "id": int(row.id or 0),
                "content": str(row.content or ""),
                "trigger_time": _fmt_dt(row.trigger_time, pattern="%Y-%m-%d %H:%M"),
                "status": str(row.status or "").upper(),
            }
            for row in rows[:120]
            if int(row.id or 0) > 0
        ]
    }


def _parse_int_list(value: Any) -> list[int]:
    if isinstance(value, list):
        raw = value
    elif value is None:
        raw = []
    else:
        raw = [value]
    seen: set[int] = set()
    picked: list[int] = []
    for item in raw:
        try:
            num = int(item)
        except Exception:
            continue
        if num <= 0 or num in seen:
            continue
        seen.add(num)
        picked.append(num)
    return picked


def _read_last_schedule_ids_from_state(state: GraphState) -> list[int]:
    extra = dict(state.get("extra") or {})
    payload = extra.get("schedule_last_query")
    if not isinstance(payload, dict):
        return []
    return _parse_int_list(payload.get("ids"))


def _with_last_schedule_query(
    state: GraphState,
    *,
    rows: list[Schedule],
    label: str,
    scope: str,
    status_filter: str,
) -> GraphState:
    extra = dict(state.get("extra") or {})
    extra["schedule_last_query"] = {
        "ids": [int(row.id) for row in rows if int(row.id or 0) > 0][:300],
        "label": label,
        "scope": scope or "",
        "status_filter": status_filter or "all",
        "updated_at": datetime.utcnow().isoformat(),
    }
    return {**state, "extra": extra}


async def _query_schedules_by_ids(
    session,
    *,
    user_id: int,
    schedule_ids: list[int],
    status_filter: str = "all",
) -> list[Schedule]:
    ids = _parse_int_list(schedule_ids)
    if not ids:
        return []
    stmt = select(Schedule).where(Schedule.user_id == user_id, Schedule.id.in_(ids))
    db_status = SCHEDULE_STATUS_DB.get(status_filter, None)
    if db_status:
        stmt = stmt.where(Schedule.status == db_status)
    stmt = stmt.order_by(Schedule.trigger_time.asc(), Schedule.id.asc())
    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    index_map = {int(row.id): row for row in rows if int(row.id or 0) > 0}
    ordered: list[Schedule] = []
    for sid in ids:
        row = index_map.get(sid)
        if row is not None:
            ordered.append(row)
    return ordered


async def _query_schedules_by_window(
    session,
    *,
    user_id: int,
    start_at: datetime,
    end_at: datetime,
    status_filter: str = "all",
    limit: int = 400,
) -> list[Schedule]:
    stmt = (
        select(Schedule)
        .where(
            Schedule.user_id == user_id,
            Schedule.trigger_time >= start_at,
            Schedule.trigger_time < end_at,
        )
        .order_by(Schedule.trigger_time.asc(), Schedule.id.asc())
        .limit(limit)
    )
    db_status = SCHEDULE_STATUS_DB.get(status_filter, None)
    if db_status:
        stmt = stmt.where(Schedule.status == db_status)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _expand_related_schedule_rows(
    session,
    *,
    user_id: int,
    target_rows: list[Schedule],
    status_filter: str,
) -> list[Schedule]:
    if not target_rows:
        return []
    roots = { _schedule_content_root(str(row.content or "")) for row in target_rows }
    roots.discard("")
    if not roots:
        return target_rows
    candidates = await _query_editable_schedules(session, user_id, limit=300, target_hint="")
    picked: list[Schedule] = []
    seen: set[int] = set()
    for row in target_rows + candidates:
        sid = int(row.id or 0)
        if sid <= 0 or sid in seen:
            continue
        if status_filter != "all":
            db_status = SCHEDULE_STATUS_DB.get(status_filter, None)
            if db_status and str(row.status or "").upper() != db_status:
                continue
        root = _schedule_content_root(str(row.content or ""))
        if root in roots:
            seen.add(sid)
            picked.append(row)
    return picked


async def _select_schedule_ids_by_llm(
    *,
    content: str,
    conversation_context: str,
    target_content: str,
    operation: str,
    selection_mode: str,
    candidates: list[Schedule],
) -> list[int]:
    if not candidates:
        return []
    llm = get_llm(node_name="schedule_manager")
    payload = _build_schedule_target_payload(candidates)
    system = SystemMessage(
        content=(
            "你是提醒目标选择器，只输出 JSON。"
            "字段: schedule_ids, confidence。"
            "根据用户输入，从候选提醒中选择应被操作的提醒ID列表。"
            "operation 仅为 update 或 delete。"
            "selection_mode 为 all/single/subset/auto。"
            "当用户说‘这几个/这些/刚才那些’时，需要结合会话上下文里的最近结果进行引用解析。"
            "若用户表达“都/全部”，尽量返回多个ID。"
            "若无法确定，返回空数组。"
        )
    )
    human = HumanMessage(
        content=(
            f"operation: {operation}\n"
            f"selection_mode: {selection_mode}\n"
            f"target_content_hint: {target_content or ''}\n\n"
            f"会话上下文:\n{conversation_context}\n\n"
            f"用户输入:\n{content}\n\n"
            f"候选提醒(JSON):\n{json.dumps(payload, ensure_ascii=False)}"
        )
    )
    response = await asyncio.wait_for(llm.ainvoke([system, human]), timeout=45)
    parsed = _parse_json_object(str(response.content))
    raw_ids = parsed.get("schedule_ids")
    if not isinstance(raw_ids, list):
        return []
    valid_ids = {int(row.id) for row in candidates if int(row.id or 0) > 0}
    picked: list[int] = []
    for value in raw_ids:
        try:
            schedule_id = int(value)
        except Exception:
            continue
        if schedule_id in valid_ids and schedule_id not in picked:
            picked.append(schedule_id)
    if selection_mode == "single" and len(picked) > 1:
        return picked[:1]
    return picked


async def _select_schedule_ids_from_context(
    *,
    content: str,
    conversation_context: str,
    candidates: list[Schedule],
) -> list[int]:
    if not candidates:
        return []
    llm = get_llm(node_name="schedule_manager")
    payload = _build_schedule_target_payload(candidates)
    system = SystemMessage(
        content=(
            "你是提醒上下文引用解析器，只输出 JSON。"
            "字段: schedule_ids, confidence。"
            "当用户说‘这几个/这些/刚才那些提醒’时，需依据会话上下文中最近助手列举的提醒项目，映射到候选ID。"
            "若仍不确定，返回空数组。"
        )
    )
    human = HumanMessage(
        content=(
            f"会话上下文:\n{conversation_context}\n\n"
            f"用户输入:\n{content}\n\n"
            f"候选提醒(JSON):\n{json.dumps(payload, ensure_ascii=False)}"
        )
    )
    response = await asyncio.wait_for(llm.ainvoke([system, human]), timeout=45)
    parsed = _parse_json_object(str(response.content))
    return _parse_int_list(parsed.get("schedule_ids"))


async def _delete_schedules_by_ids(
    *,
    session,
    scheduler,
    user_id: int,
    schedule_ids: list[int],
) -> list[Schedule]:
    if not schedule_ids:
        return []
    result = await session.execute(
        select(Schedule).where(Schedule.user_id == user_id, Schedule.id.in_(schedule_ids))
    )
    rows = list(result.scalars().all())
    if not rows:
        return []
    snapshots = list(rows)
    for row in rows:
        try:
            scheduler.remove_job(str(row.job_id))
        except Exception:
            pass
        await session.execute(
            delete(ReminderDelivery).where(ReminderDelivery.schedule_id == int(row.id or 0))
        )
        await session.delete(row)
    await session.commit()
    return snapshots


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
    reference_mode = str(parsed.get("reference_mode") or "auto").strip().lower()
    if reference_mode not in {"by_id", "by_name", "by_scope", "latest", "last_result_set", "auto"}:
        reference_mode = "auto"
    selection_mode = str(parsed.get("selection_mode") or "auto").strip().lower()
    if selection_mode not in {"all", "single", "subset", "auto"}:
        selection_mode = "auto"
    target_ids = _parse_int_list(parsed.get("target_ids"))

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
            ledgers, schedules = await _query_calendar_rows(session, user.id, start_at, end_at)
            filtered_schedules = _filter_schedules_by_status(schedules, schedule_status_filter)
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
            next_state = _with_last_schedule_query(
                state,
                rows=filtered_schedules,
                label=label,
                scope=scope or "date",
                status_filter=schedule_status_filter,
            )
            return {**next_state, "responses": [response]}

    if intent in {"update_by_name", "update_by_scope", "delete_by_name", "delete_by_scope"} and confidence >= 0.35:
        target_content = str(parsed.get("target_content") or parsed.get("reminder_content") or "").strip()

        async def _resolve_target_schedules(operation: str) -> tuple[list[Schedule], str]:
            if target_ids and reference_mode in {"by_id", "auto"}:
                rows = await _query_schedules_by_ids(
                    session,
                    user_id=user.id,
                    schedule_ids=target_ids,
                    status_filter=schedule_status_filter,
                )
                return rows, "by_id"

            if reference_mode == "last_result_set":
                last_ids = _read_last_schedule_ids_from_state(state)
                rows = await _query_schedules_by_ids(
                    session,
                    user_id=user.id,
                    schedule_ids=last_ids,
                    status_filter=schedule_status_filter,
                )
                return rows, "last_result_set"

            if intent in {"update_by_scope", "delete_by_scope"} or reference_mode == "by_scope":
                scope = str(parsed.get("calendar_scope") or "").strip().lower()
                calendar_date = str(parsed.get("calendar_date") or "").strip() or None
                window = _resolve_calendar_window_from_fields(scope, calendar_date) or _resolve_calendar_window(content)
                if window:
                    start_at, end_at, _ = window
                    rows = await _query_schedules_by_window(
                        session,
                        user_id=user.id,
                        start_at=start_at,
                        end_at=end_at,
                        status_filter=schedule_status_filter,
                    )
                    return rows, "by_scope"
                # scope parse failed: fallback to last result set
                last_ids = _read_last_schedule_ids_from_state(state)
                rows = await _query_schedules_by_ids(
                    session,
                    user_id=user.id,
                    schedule_ids=last_ids,
                    status_filter=schedule_status_filter,
                )
                return rows, "last_result_set"

            candidates = await _query_editable_schedules(
                session,
                user.id,
                limit=120,
                target_hint=target_content,
            )
            picked_ids = await _select_schedule_ids_by_llm(
                content=content,
                conversation_context=context_text,
                target_content=target_content,
                operation=operation,
                selection_mode=selection_mode,
                candidates=candidates,
            )
            if not picked_ids:
                picked_ids = await _select_schedule_ids_from_context(
                    content=content,
                    conversation_context=context_text,
                    candidates=candidates,
                )
            if selection_mode == "all" and not picked_ids and candidates:
                picked_ids = [int(row.id) for row in candidates if int(row.id or 0) > 0]
            rows = await _query_schedules_by_ids(
                session,
                user_id=user.id,
                schedule_ids=picked_ids,
                status_filter=schedule_status_filter,
            )
            if rows:
                return rows, "by_name"

            # Fallback: for deictic utterances like "删除这个提醒", parser may miss
            # last_result_set. Reuse previous query ids before failing.
            last_ids = _read_last_schedule_ids_from_state(state)
            if last_ids:
                fallback_rows = await _query_schedules_by_ids(
                    session,
                    user_id=user.id,
                    schedule_ids=last_ids,
                    status_filter=schedule_status_filter,
                )
                if fallback_rows:
                    return fallback_rows, "last_result_set"
            return rows, "by_name"

        operation = "update" if intent.startswith("update_") else "delete"
        target_rows, source = await _resolve_target_schedules(operation)
        if target_rows:
            target_rows = await _expand_related_schedule_rows(
                session,
                user_id=user.id,
                target_rows=target_rows,
                status_filter=schedule_status_filter,
            )
        matched_count = len(target_rows)
        target_schedule_ids = [int(row.id) for row in target_rows if int(row.id or 0) > 0]

        if matched_count == 0:
            return {
                **state,
                "responses": ["我还不能定位要操作的提醒。请补充名称、范围、ID，或先查询后说“删这几个”。"],
            }

        if operation == "delete":
            deleted_rows = await _delete_schedules_by_ids(
                session=session,
                scheduler=scheduler,
                user_id=user.id,
                schedule_ids=target_schedule_ids,
            )
            deleted_count = len(deleted_rows)
            return {
                **state,
                "responses": [f"已定位 {matched_count} 条（来源：{source}），成功删除 {deleted_count} 条提醒。"],
            }

        trigger_time = _parse_run_at_local(str(parsed.get("run_at_local") or ""), get_settings().timezone)
        if trigger_time is None:
            return {
                **state,
                "responses": ["我理解到你要修改提醒时间，但缺少明确时间。请补充具体时间，例如：明天 11:00。"],
            }
        trigger_time = trigger_time.replace(second=0, microsecond=0)
        now_local = datetime.now(ZoneInfo(get_settings().timezone)).replace(tzinfo=None)
        if trigger_time <= now_local + timedelta(seconds=1):
            return {
                **state,
                "responses": ["这个目标时间已经太近或已过去，请给我一个稍晚的时间。"],
            }

        fallback_content = target_rows[0].content if target_rows else (target_content or "")
        event_type = _normalize_event_type(str(parsed.get("event_type") or ""))
        reminder_content_seed = _resolve_reminder_content(
            str(parsed.get("reminder_content") or target_content or ""),
            fallback_content,
            event_type,
        )
        reminder_content = await _generate_reminder_title_with_llm(
            user_content=content,
            conversation_context=context_text,
            hint_title=reminder_content_seed,
            event_type=event_type,
        )
        priority = _normalize_priority(str(parsed.get("priority") or "medium"))
        explicit_offsets = _resolve_explicit_offsets(parsed, content)
        offsets_minutes = _resolve_reminder_offsets(
            event_type=event_type,
            priority=priority,
            explicit_offsets=explicit_offsets,
            relative_mode="none",
        )
        create_text = await _create_reminder(
            session=session,
            scheduler=scheduler,
            user=user,
            reminder_content=reminder_content,
            event_time=trigger_time,
            event_type=event_type,
            priority=priority,
            offsets_minutes=offsets_minutes,
        )
        if not create_text:
            return {
                **state,
                "responses": ["新时间下未能成功创建提醒，请确认时间后重试。"],
            }

        deleted_rows = await _delete_schedules_by_ids(
            session=session,
            scheduler=scheduler,
            user_id=user.id,
            schedule_ids=target_schedule_ids,
        )
        return {
            **state,
            "responses": [f"{create_text}（已定位 {matched_count} 条，替换原提醒 {len(deleted_rows)} 条，来源：{source}）"],
        }

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
            event_type = _normalize_event_type(str(reminder_candidate.get("event_type") or ""))
            reminder_content_seed = _resolve_reminder_content(
                str(reminder_candidate.get("reminder_content") or ""),
                content,
                event_type,
            )
            reminder_content = await _generate_reminder_title_with_llm(
                user_content=content,
                conversation_context=context_text,
                hint_title=reminder_content_seed,
                event_type=event_type,
            )
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
        ledgers, schedules = await _query_calendar_rows(session, user.id, start_at, end_at)
        filtered_schedules = _filter_schedules_by_status(schedules, schedule_status_filter)
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
        next_state = _with_last_schedule_query(
            state,
            rows=filtered_schedules,
            label=label,
            scope="command",
            status_filter=schedule_status_filter,
        )
        return {**next_state, "responses": [response]}

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
