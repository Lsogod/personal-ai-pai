import json
import re
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.config import get_settings
from app.graph.context import render_conversation_context
from app.graph.prompts.ledger_manager_prompts import (
    build_ledger_intent_messages,
    build_ledger_pending_selection_messages,
)
from app.graph.state import GraphState
from app.models.ledger import Ledger
from app.models.user import User
from app.services.langchain_tools import ToolInvocationContext
from app.services.llm import get_llm
from app.services.runtime_context import get_session
from app.services.toolsets import invoke_node_tool
from app.services.ledger_pending import (
    clear_pending_ledger,
    get_pending_ledger,
    set_pending_ledger,
)


async def _tracked_analyze_receipt(
    image_ref: str,
    *,
    user_id: int | None = None,
    platform: str = "",
    conversation_id: int | None = None,
) -> dict[str, Any]:
    output = await invoke_node_tool(
        context=ToolInvocationContext(
            user_id=user_id,
            platform=platform or "unknown",
            conversation_id=conversation_id,
        ),
        node_name="ledger_manager",
        tool_name="analyze_receipt",
        args={"image_ref": image_ref},
    )
    if _looks_like_tool_error(output):
        raise RuntimeError(output)
    payload = _parse_json_object(str(output or ""))
    if payload:
        return payload
    return {
        "image_type": "other",
        "confidence": 0.0,
        "amount": None,
        "amount_candidates": [],
        "category": "其他",
        "item": "消费",
        "merchant": "",
        "reason": str(output or ""),
    }


async def _tracked_text2sql(
    *,
    user_id: int,
    message: str,
    conversation_context: str = "",
    platform: str = "",
    conversation_id: int | None = None,
) -> str | None:
    output = await invoke_node_tool(
        context=ToolInvocationContext(
            user_id=user_id,
            platform=platform or "unknown",
            conversation_id=conversation_id,
        ),
        node_name="ledger_manager",
        tool_name="ledger_text2sql",
        args={
            "message": message,
            "conversation_context": conversation_context,
        },
    )
    if _looks_like_tool_error(output):
        raise RuntimeError(output)
    text = str(output or "").strip()
    return text or None


DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")
TECHNICAL_LEAK_PATTERN = re.compile(r"(json|payload|字段|数组|schema|schedules|ledgers)", re.IGNORECASE)
PENDING_INDEX_PATTERN = re.compile(r"(?:第\s*([1-9]\d*)\s*(?:个|条)?|选\s*([1-9]\d*)|#([1-9]\d*))")
PENDING_CN_INDEX_PATTERN = re.compile(r"第\s*([一二三四五六七八九十两])\s*(?:个|条)?")
PENDING_BATCH_SELECT_PATTERN = re.compile(r"选\s*([1-9]\d*(?:\s*[,，、和及]\s*[1-9]\d*)+)")
CN_NUM_MAP = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}

CATEGORY_MAP = {
    "餐饮": "餐饮",
    "吃饭": "餐饮",
    "午饭": "餐饮",
    "晚饭": "餐饮",
    "早餐": "餐饮",
    "交通": "交通",
    "打车": "交通",
    "地铁": "交通",
    "公交": "交通",
    "购物": "购物",
    "买": "购物",
    "超市": "购物",
    "居家": "居家",
    "房租": "居家",
    "水电": "居家",
    "娱乐": "娱乐",
    "电影": "娱乐",
    "医疗": "医疗",
    "看病": "医疗",
}
MAX_IMAGES = 6


def _fmt_dt(dt: datetime | None) -> str:
    if not dt:
        return ""
    tz = ZoneInfo(get_settings().timezone)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    else:
        dt = dt.astimezone(ZoneInfo("UTC"))
    return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M")


def _normalize_category(text: str | None) -> str:
    raw = (text or "").strip()
    if not raw:
        return "其他"
    for key, value in CATEGORY_MAP.items():
        if key in raw:
            return value
    return raw if len(raw) <= 10 else "其他"


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

def _parse_json_list(content: str) -> list[dict[str, Any]]:
    text = (content or "").strip()
    if not text:
        return []
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()
    try:
        data = json.loads(text)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _looks_like_tool_error(text: str) -> bool:
    raw = (text or "").strip().lower()
    if not raw:
        return False
    tokens = (
        "tool `",
        "failed",
        "not available",
        "missing required arg",
        "invalid ",
        "unsupported ",
        "is disabled",
        "blocked by allowlist",
    )
    return any(token in raw for token in tokens)


def _ledger_from_payload(payload: dict[str, Any]) -> Ledger | None:
    if not isinstance(payload, dict):
        return None
    try:
        ledger_id = int(payload.get("id") or 0)
        user_id = int(payload.get("user_id") or 0)
        amount = float(payload.get("amount") or 0.0)
    except Exception:
        return None
    if ledger_id <= 0 or user_id <= 0:
        return None
    transaction_raw = str(payload.get("transaction_date") or "").strip().replace("T", " ")
    transaction_date = None
    if transaction_raw:
        try:
            transaction_date = datetime.fromisoformat(transaction_raw)
        except Exception:
            transaction_date = None
    if transaction_date is None:
        transaction_date = datetime.utcnow()
    return Ledger(
        id=ledger_id,
        user_id=user_id,
        amount=amount,
        currency=str(payload.get("currency") or "CNY"),
        category=str(payload.get("category") or ""),
        item=str(payload.get("item") or ""),
        image_url=str(payload.get("image_url") or "") or None,
        transaction_date=transaction_date,
    )


async def _invoke_ledger_tool(
    *,
    user_id: int | None,
    platform: str,
    conversation_id: int | None,
    tool_name: str,
    args: dict[str, Any] | None = None,
) -> str:
    return await invoke_node_tool(
        context=ToolInvocationContext(
            user_id=user_id,
            platform=platform or "unknown",
            conversation_id=conversation_id,
        ),
        node_name="ledger_manager",
        tool_name=tool_name,
        args=dict(args or {}),
    )


async def _insert_ledger_via_tool(
    *,
    user_id: int,
    platform: str,
    conversation_id: int | None,
    amount: float,
    category: str,
    item: str,
    transaction_date: datetime | None = None,
    image_url: str = "",
) -> Ledger | None:
    payload = {
        "user_id": user_id,
        "amount": amount,
        "category": category,
        "item": item,
        "image_url": image_url,
    }
    if transaction_date is not None:
        payload["transaction_date"] = transaction_date.isoformat(sep=" ", timespec="seconds")
    output = await _invoke_ledger_tool(
        user_id=user_id,
        platform=platform,
        conversation_id=conversation_id,
        tool_name="ledger_insert",
        args=payload,
    )
    return _ledger_from_payload(_parse_json_object(output))



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
        if "当前数据为准" in norm and "此前对话" in norm:
            continue
        lines.append(line)
        prev = norm
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines).strip()


def _clean_item(item: str) -> str:
    value = (item or "").strip()
    if not value:
        return ""
    value = re.sub(r"(?:第\s*\d+\s*(?:个|条)?|#\s*\d+)", "", value)
    value = re.sub(
        r"^(错了|更正|修正|记错|不对|不是|把|改成|改为|应该是|应为|我说的是)\s*[:：]?\s*",
        "",
        value,
    )
    value = re.sub(r"^(今天|刚才|刚刚)\s*", "", value)
    value = re.sub(r"\d+(?:\.\d{1,2})?", "", value)
    value = re.sub(r"(应该是|应为|改成|改为)$", "", value)
    value = value.replace("元", "").replace("块", "").replace("记账", "").strip()
    value = value.replace("和", " ").replace("及", " ").replace("并且", " ")
    value = re.sub(r"\s+", " ", value).strip()
    value = value.strip("：:，,。.!！？?;；")
    if value in {"和", "及", "跟"}:
        return ""
    return value


def _unique_positive_amounts(values: list[float]) -> list[float]:
    result: list[float] = []
    for value in values:
        if value <= 0:
            continue
        if value not in result:
            result.append(value)
    return result


def _pending_amount_candidates(payload: dict) -> list[float]:
    raw = payload.get("amount_candidates")
    if not isinstance(raw, list):
        return []
    values: list[float] = []
    for item in raw:
        try:
            values.append(float(item))
        except Exception:
            continue
    return _unique_positive_amounts(values)


def _extract_pending_indices(content: str, max_len: int) -> list[int]:
    source = content or ""
    indices: list[int] = []
    seen: set[int] = set()

    for matched in PENDING_INDEX_PATTERN.finditer(source):
        for group in matched.groups():
            if not group:
                continue
            try:
                idx = int(group)
            except Exception:
                continue
            if 1 <= idx <= max_len and idx not in seen:
                seen.add(idx)
                indices.append(idx)

    for matched in PENDING_CN_INDEX_PATTERN.finditer(source):
        idx = CN_NUM_MAP.get(matched.group(1), 0)
        if 1 <= idx <= max_len and idx not in seen:
            seen.add(idx)
            indices.append(idx)

    for matched in PENDING_BATCH_SELECT_PATTERN.finditer(source):
        parts = re.split(r"\s*[,，、和及]\s*", matched.group(1))
        for token in parts:
            token = (token or "").strip()
            if not token.isdigit():
                continue
            idx = int(token)
            if 1 <= idx <= max_len and idx not in seen:
                seen.add(idx)
                indices.append(idx)

    # Loose fallback for "1 2" / "1和2" / "1,2" style selection.
    if not indices:
        has_decimal = bool(re.search(r"\d+\.\d+", source))
        has_currency = bool(re.search(r"(?:元|块|rmb|RMB|人民币)", source))
        if not has_decimal and not has_currency:
            tokens = re.findall(r"(?<!\d)(\d+)(?!\d)", source)
            parsed = [int(token) for token in tokens]
            if parsed and all(1 <= idx <= max_len for idx in parsed):
                for idx in parsed:
                    if idx not in seen:
                        seen.add(idx)
                        indices.append(idx)

    if not indices:
        if "前两个" in source and max_len >= 2:
            indices = [1, 2]
        elif "后两个" in source and max_len >= 2:
            indices = [max_len - 1, max_len]

    return indices


def _pick_amount_from_indexes(raw_indexes: object, candidates: list[float]) -> float | None:
    if not isinstance(raw_indexes, list):
        return None
    seen: set[int] = set()
    picked: list[int] = []
    for value in raw_indexes:
        try:
            idx = int(value)
        except Exception:
            continue
        if 1 <= idx <= len(candidates) and idx not in seen:
            seen.add(idx)
            picked.append(idx)
    if not picked:
        return None
    return round(sum(candidates[idx - 1] for idx in picked), 2)


def _extract_plain_amount(text: str) -> float | None:
    raw = (text or "").strip()
    if not raw:
        return None
    match = re.fullmatch(r"(\d+(?:\.\d{1,2})?)", raw)
    if not match:
        return None
    try:
        value = float(match.group(1))
    except Exception:
        return None
    return value if value > 0 else None


async def _understand_pending_selection(
    content: str,
    candidates: list[float],
    pending: dict,
    conversation_context: str,
) -> dict:
    llm = get_llm(node_name="ledger_manager")
    messages = build_ledger_pending_selection_messages(
        content=content,
        candidates=candidates,
        detected_item=str(pending.get("item") or pending.get("merchant") or "消费"),
        default_category=str(pending.get("category") or "其他"),
        conversation_context=conversation_context,
    )
    response = await llm.ainvoke(messages)
    return _parse_json_object(str(response.content))


def _render_pending_candidates(candidates: list[float]) -> str:
    lines = ["我识别到多个支付金额："]
    for idx, value in enumerate(candidates, start=1):
        lines.append(f"{idx}. {value:.2f} 元")
    lines.append("请回复要记账的金额（可回复“第1个”或直接“28.50”）。")
    lines.append("支持多选合并记账，例如：`第二个和第三个`、`选2和3`。")
    lines.append("可附带分类和摘要，例如：`第1个 餐饮 午饭`。")
    lines.append("不记这张图可回复：`取消`。")
    return "\n".join(lines)


def _parse_vision_result(result: dict) -> dict:
    image_type = str(result.get("image_type") or "other").strip().lower()
    try:
        confidence = float(result.get("confidence") or 0.0)
    except Exception:
        confidence = 0.0
    amount_raw = result.get("amount")
    try:
        amount = float(amount_raw) if amount_raw is not None else None
    except Exception:
        amount = None
    raw_candidates = result.get("amount_candidates")
    candidate_values: list[float] = []
    if isinstance(raw_candidates, list):
        for item in raw_candidates:
            try:
                candidate_values.append(float(item))
            except Exception:
                continue
    if amount is not None:
        candidate_values = [amount] + candidate_values
    candidate_values = _unique_positive_amounts(candidate_values)[:5]
    threshold = 0.8 if image_type == "receipt" else 0.65
    category = _normalize_category(str(result.get("category") or "其他"))
    item = str(result.get("item") or result.get("merchant") or "消费")
    return {
        "image_type": image_type,
        "confidence": confidence,
        "amount": amount,
        "candidate_values": candidate_values,
        "threshold": threshold,
        "category": category,
        "item": item,
        "merchant": str(result.get("merchant") or ""),
    }


async def _understand_ledger_message(content: str, conversation_context: str) -> dict:
    llm = get_llm(node_name="ledger_manager")
    messages = build_ledger_intent_messages(
        content=content,
        conversation_context=conversation_context,
    )
    response = await llm.ainvoke(messages)
    return _parse_json_object(str(response.content))


def _build_ledger_target_payload(rows: list[Ledger]) -> dict[str, Any]:
    return {
        "candidates": [
            {
                "id": row.id,
                "item": row.item,
                "category": row.category,
                "amount": round(float(row.amount), 2),
                "currency": row.currency,
                "datetime": _fmt_dt(row.transaction_date),
            }
            for row in rows[:120]
        ]
    }


async def _select_ledger_id_by_llm(
    *,
    content: str,
    conversation_context: str,
    target_item: str,
    operation: str,
    candidates: list[Ledger],
) -> int | None:
    if not candidates:
        return None
    llm = get_llm(node_name="ledger_manager")
    payload = _build_ledger_target_payload(candidates)
    system = SystemMessage(
        content=(
            "你是账单目标选择器，只输出 JSON。"
            "字段: ledger_id, confidence。"
            "根据用户输入，从候选账单中选择唯一目标账单ID。"
            "如果无法确定，ledger_id=null。"
            "operation 仅用于语义参考（correct 或 delete）。"
        )
    )
    human = HumanMessage(
        content=(
            f"operation: {operation}\n"
            f"target_item_hint: {target_item or ''}\n\n"
            f"会话上下文:\n{conversation_context}\n\n"
            f"用户输入:\n{content}\n\n"
            f"候选账单(JSON):\n{json.dumps(payload, ensure_ascii=False)}"
        )
    )
    response = await llm.ainvoke([system, human])
    parsed = _parse_json_object(str(response.content))
    selected = parsed.get("ledger_id")
    try:
        ledger_id = int(selected) if selected is not None else None
    except Exception:
        return None
    valid_ids = {int(row.id) for row in candidates if int(row.id or 0) > 0}
    return ledger_id if ledger_id in valid_ids else None


async def _select_ledger_ids_by_llm(
    *,
    content: str,
    conversation_context: str,
    target_item: str,
    operation: str,
    selection_mode: str,
    candidates: list[Ledger],
) -> list[int]:
    if not candidates:
        return []
    llm = get_llm(node_name="ledger_manager")
    payload = _build_ledger_target_payload(candidates)
    system = SystemMessage(
        content=(
            "你是账单目标批量选择器，只输出 JSON。"
            "字段: ledger_ids, confidence。"
            "根据用户输入，从候选账单中选择应操作的账单ID列表。"
            "operation 仅为 correct 或 delete。"
            "selection_mode 为 all/single/subset/auto。"
            "若用户明确说‘全部/都/这几笔’，优先返回多个ID。"
            "若用户表达单笔操作，返回一个ID。"
            "无法确定时返回空数组。"
        )
    )
    human = HumanMessage(
        content=(
            f"operation: {operation}\n"
            f"selection_mode: {selection_mode}\n"
            f"target_item_hint: {target_item or ''}\n\n"
            f"会话上下文:\n{conversation_context}\n\n"
            f"用户输入:\n{content}\n\n"
            f"候选账单(JSON):\n{json.dumps(payload, ensure_ascii=False)}"
        )
    )
    response = await llm.ainvoke([system, human])
    parsed = _parse_json_object(str(response.content))
    raw_ids = parsed.get("ledger_ids")
    if not isinstance(raw_ids, list):
        picked = _parse_int_list(parsed.get("ledger_id"))
        raw_ids = picked
    valid_ids = {int(row.id) for row in candidates if int(row.id or 0) > 0}
    result: list[int] = []
    seen: set[int] = set()
    for value in raw_ids:
        try:
            ledger_id = int(value)
        except Exception:
            continue
        if ledger_id in valid_ids and ledger_id not in seen:
            seen.add(ledger_id)
            result.append(ledger_id)
    if selection_mode == "single" and len(result) > 1:
        return result[:1]
    return result


async def _select_ledger_ids_from_context(
    *,
    content: str,
    conversation_context: str,
    candidates: list[Ledger],
) -> list[int]:
    if not candidates:
        return []
    llm = get_llm(node_name="ledger_manager")
    payload = _build_ledger_target_payload(candidates)
    system = SystemMessage(
        content=(
            "你是账单上下文引用解析器，只输出 JSON。"
            "字段: ledger_ids, confidence。"
            "当用户使用代词（如‘这几笔/这些/刚才那些’）时，需结合会话上下文和候选列表选择目标ID。"
            "若仍不确定，返回空数组。"
        )
    )
    human = HumanMessage(
        content=(
            f"会话上下文:\n{conversation_context}\n\n"
            f"用户输入:\n{content}\n\n"
            f"候选账单(JSON):\n{json.dumps(payload, ensure_ascii=False)}"
        )
    )
    response = await llm.ainvoke([system, human])
    parsed = _parse_json_object(str(response.content))
    return _parse_int_list(parsed.get("ledger_ids"))


def _to_utc_naive(local_dt: datetime, tz_name: str) -> datetime:
    local = local_dt.replace(tzinfo=ZoneInfo(tz_name))
    return local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def _format_window_label(name: str, start_local_date: date, end_local_date_exclusive: date) -> str:
    if end_local_date_exclusive == start_local_date + timedelta(days=1):
        return f"{name}（{start_local_date.isoformat()}）"
    end_local_date = end_local_date_exclusive - timedelta(days=1)
    return f"{name}（{start_local_date.isoformat()} ~ {end_local_date.isoformat()}）"


def _resolve_ledger_window_from_fields(scope: str, query_date: str | None) -> tuple[datetime, datetime, str] | None:
    tz_name = get_settings().timezone
    today_local = datetime.now(ZoneInfo(tz_name)).date()
    key = (scope or "").strip().lower()

    if key == "yesterday":
        target = today_local - timedelta(days=1)
        start_local = datetime.combine(target, datetime.min.time())
        end_local = start_local + timedelta(days=1)
        label = _format_window_label("昨天", target, target + timedelta(days=1))
        return _to_utc_naive(start_local, tz_name), _to_utc_naive(end_local, tz_name), label
    if key == "day_before_yesterday":
        target = today_local - timedelta(days=2)
        start_local = datetime.combine(target, datetime.min.time())
        end_local = start_local + timedelta(days=1)
        label = _format_window_label("前天", target, target + timedelta(days=1))
        return _to_utc_naive(start_local, tz_name), _to_utc_naive(end_local, tz_name), label
    if key == "today":
        start_local = datetime.combine(today_local, datetime.min.time())
        end_local = start_local + timedelta(days=1)
        label = _format_window_label("今天", today_local, today_local + timedelta(days=1))
        return _to_utc_naive(start_local, tz_name), _to_utc_naive(end_local, tz_name), label
    if key == "last_week":
        week_start = today_local - timedelta(days=today_local.weekday() + 7)
        week_end = week_start + timedelta(days=7)
        start_local = datetime.combine(week_start, datetime.min.time())
        end_local = datetime.combine(week_end, datetime.min.time())
        label = _format_window_label("上周", week_start, week_end)
        return _to_utc_naive(start_local, tz_name), _to_utc_naive(end_local, tz_name), label
    if key == "week":
        week_start = today_local - timedelta(days=today_local.weekday())
        start_local = datetime.combine(week_start, datetime.min.time())
        end_local = start_local + timedelta(days=7)
        label = _format_window_label("本周", week_start, week_start + timedelta(days=7))
        return _to_utc_naive(start_local, tz_name), _to_utc_naive(end_local, tz_name), label
    if key == "month":
        month_start = today_local.replace(day=1)
        if month_start.month == 12:
            next_month = date(month_start.year + 1, 1, 1)
        else:
            next_month = date(month_start.year, month_start.month + 1, 1)
        start_local = datetime.combine(month_start, datetime.min.time())
        end_local = datetime.combine(next_month, datetime.min.time())
        label = _format_window_label("本月", month_start, next_month)
        return _to_utc_naive(start_local, tz_name), _to_utc_naive(end_local, tz_name), label
    if key == "date" and query_date:
        try:
            target = date.fromisoformat(query_date.strip())
        except Exception:
            return None
        start_local = datetime.combine(target, datetime.min.time())
        end_local = start_local + timedelta(days=1)
        label = _format_window_label(target.isoformat(), target, target + timedelta(days=1))
        return _to_utc_naive(start_local, tz_name), _to_utc_naive(end_local, tz_name), label
    return None


async def _query_ledger_rows(
    user_id: int,
    *,
    platform: str,
    conversation_id: int | None,
    start_at: datetime | None,
    end_at: datetime | None,
    category: str | None,
    item_like: str | None = None,
    limit: int,
) -> list[Ledger]:
    args: dict[str, Any] = {
        "user_id": user_id,
        "limit": limit,
        "order": "desc",
    }
    if start_at is not None:
        args["start_at"] = start_at.isoformat(sep=" ", timespec="seconds")
    if end_at is not None:
        args["end_at"] = end_at.isoformat(sep=" ", timespec="seconds")
    if category:
        args["category"] = category
    if item_like:
        args["item_like"] = item_like

    output = await _invoke_ledger_tool(
        user_id=user_id,
        platform=platform,
        conversation_id=conversation_id,
        tool_name="ledger_list",
        args=args,
    )
    return [
        row
        for row in (
            _ledger_from_payload(item)
            for item in _parse_json_list(output)
        )
        if row is not None
    ]


def _build_ledger_payload(rows: list[Ledger]) -> dict[str, Any]:
    return {
        "ledgers": [
            {
                "id": row.id,
                "datetime": _fmt_dt(row.transaction_date),
                "amount": round(float(row.amount), 2),
                "currency": row.currency,
                "category": row.category,
                "item": row.item,
            }
            for row in rows[:100]
        ]
    }


def _parse_int_list(value: Any) -> list[int]:
    if isinstance(value, list):
        raw = value
    elif value is None:
        raw = []
    else:
        raw = [value]
    picked: list[int] = []
    seen: set[int] = set()
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


def _read_last_ledger_ids_from_state(state: GraphState) -> list[int]:
    extra = dict(state.get("extra") or {})
    payload = extra.get("ledger_last_query")
    if not isinstance(payload, dict):
        return []
    return _parse_int_list(payload.get("ids"))


async def _query_ledgers_by_ids(
    user_id: int,
    ledger_ids: list[int],
    *,
    platform: str,
    conversation_id: int | None,
) -> list[Ledger]:
    valid_ids = _parse_int_list(ledger_ids)
    if not valid_ids:
        return []
    output = await _invoke_ledger_tool(
        user_id=user_id,
        platform=platform,
        conversation_id=conversation_id,
        tool_name="ledger_list",
        args={
            "user_id": user_id,
            "ledger_ids": valid_ids,
            "limit": max(100, len(valid_ids) * 3),
            "order": "desc",
        },
    )
    rows = [
        row
        for row in (
            _ledger_from_payload(item)
            for item in _parse_json_list(output)
        )
        if row is not None
    ]
    index_map = {int(row.id): row for row in rows if int(row.id or 0) > 0}
    ordered: list[Ledger] = []
    for lid in valid_ids:
        row = index_map.get(lid)
        if row is not None:
            ordered.append(row)
    return ordered


async def _delete_ledgers_by_ids(
    *,
    user_id: int,
    user_platform: str,
    conversation_id: int | None,
    ledger_ids: list[int],
) -> tuple[int, list[Ledger]]:
    rows = await _query_ledgers_by_ids(
        user_id,
        ledger_ids,
        platform=user_platform,
        conversation_id=conversation_id,
    )
    deleted: list[Ledger] = []
    for row in rows:
        output = await _invoke_ledger_tool(
            user_id=user_id,
            platform=user_platform,
            conversation_id=conversation_id,
            tool_name="ledger_delete",
            args={"user_id": user_id, "ledger_id": int(row.id or 0)},
        )
        removed = _ledger_from_payload(_parse_json_object(output))
        if removed:
            deleted.append(removed)
    return len(rows), deleted


async def _update_ledgers_by_ids(
    *,
    user_id: int,
    user_platform: str,
    conversation_id: int | None,
    ledger_ids: list[int],
    amount: float,
    category: str | None,
    item: str | None,
) -> tuple[int, list[Ledger]]:
    rows = await _query_ledgers_by_ids(
        user_id,
        ledger_ids,
        platform=user_platform,
        conversation_id=conversation_id,
    )
    updated: list[Ledger] = []
    for row in rows:
        output = await _invoke_ledger_tool(
            user_id=user_id,
            platform=user_platform,
            conversation_id=conversation_id,
            tool_name="ledger_update",
            args={
                "user_id": user_id,
                "ledger_id": int(row.id or 0),
                "amount": amount,
                "category": category or "",
                "item": item or "",
            },
        )
        changed = _ledger_from_payload(_parse_json_object(output))
        if changed:
            updated.append(changed)
    return len(rows), updated


def _with_last_ledger_query(
    state: GraphState,
    *,
    rows: list[Ledger],
    label: str,
    scope: str,
    category: str | None,
) -> GraphState:
    extra = dict(state.get("extra") or {})
    extra["ledger_last_query"] = {
        "ids": [int(row.id) for row in rows if int(row.id or 0) > 0][:200],
        "label": label,
        "scope": scope or "",
        "category": category or "",
        "updated_at": datetime.utcnow().isoformat(),
    }
    return {**state, "extra": extra}


async def _answer_ledger_with_llm(
    *,
    content: str,
    conversation_context: str,
    label: str,
    category: str | None,
    rows: list[Ledger],
) -> str:
    llm = get_llm(node_name="ledger_manager")
    payload = _build_ledger_payload(rows)
    system = SystemMessage(
        content=(
            "你是账单查询答复助手。你只能根据提供的账单数据回答，禁止编造。"
            "按用户问题有针对性回答，不要固定模板。"
            "若用户问今天/本周/本月或某个日期，按对应范围说明。"
            "若用户问分类，请按分类回答。"
            "若没有记录，明确说明没有。"
            "涉及明细时要包含时间。"
            "默认给出简洁自然的中文回答，不要输出技术说明。"
            "不要提到 JSON、数组、字段、数据源、会话上下文，也不要做“当前数据为准”的提示。"
            "不要重复句子或段落。"
        )
    )
    human = HumanMessage(
        content=(
            f"查询范围: {label}\n"
            f"分类筛选: {category or '无'}\n\n"
            f"会话上下文:\n{conversation_context}\n\n"
            f"用户问题:\n{content}\n\n"
            f"数据(JSON):\n{json.dumps(payload, ensure_ascii=False)}"
        )
    )
    response = await llm.ainvoke([system, human])
    return _sanitize_llm_text(str(response.content or ""))


def _render_ledger_fallback(rows: list[Ledger], label: str, category: str | None) -> str:
    title = f"{label}账单" if label else "账单"
    if category:
        title += f"（{category}）"
    lines = [f"{title}："]
    if not rows:
        lines.append("暂无匹配账单记录。")
        return "\n".join(lines)
    for row in rows[:12]:
        lines.append(
            f"#{row.id} | {_fmt_dt(row.transaction_date)} | {row.amount:.2f} {row.currency} | {row.category} | {row.item}"
        )
    if len(rows) > 12:
        lines.append(f"... 其余 {len(rows) - 12} 条")
    return "\n".join(lines)


async def _handle_ledger_command(
    *,
    content: str,
    user_id: int,
    user_platform: str,
    conversation_id: int | None,
) -> list[str] | None:
    if not content.startswith("/ledger"):
        return None

    if content.startswith("/ledger list"):
        list_output = await _invoke_ledger_tool(
            user_id=user_id,
            platform=user_platform,
            conversation_id=conversation_id,
            tool_name="ledger_list_recent",
            args={"user_id": user_id, "limit": 10},
        )
        rows = [_ledger_from_payload(item) for item in _parse_json_list(list_output)]
        rows = [row for row in rows if row is not None]
        if not rows:
            return ["暂无账单记录。"]
        lines = ["最近账单："]
        for row in rows:
            lines.append(
                f"#{row.id} | {_fmt_dt(row.transaction_date)} | {row.amount:.2f} {row.currency} | {row.category} | {row.item}"
            )
        lines.append("可用：`/ledger update <id> <金额> [分类] [摘要]`、`/ledger delete <id|latest>`")
        return ["\n".join(lines)]

    if content.startswith("/ledger update"):
        m = re.match(
            r"^/ledger\s+update\s+(\d+)\s+(\d+(?:\.\d{1,2})?)\s*(.*)$",
            content,
        )
        if not m:
            return [
                "用法：`/ledger update <id> <金额> [分类] [摘要]`，例如 `/ledger update 12 28 餐饮 晚饭`。"
            ]
        ledger_id = int(m.group(1))
        amount = float(m.group(2))
        tail = (m.group(3) or "").strip()
        category = "其他"
        item = ""
        if tail:
            parts = tail.split(maxsplit=1)
            category = _normalize_category(parts[0])
            item = parts[1] if len(parts) > 1 else ""

        updated_output = await _invoke_ledger_tool(
            user_id=user_id,
            platform=user_platform,
            conversation_id=conversation_id,
            tool_name="ledger_update",
            args={
                "user_id": user_id,
                "ledger_id": ledger_id,
                "amount": amount,
                "category": category,
                "item": item or "",
            },
        )
        updated = _ledger_from_payload(_parse_json_object(updated_output))
        if not updated:
            return [f"未找到账单 #{ledger_id}，或它不属于你。"]
        return [
            f"已更新账单 #{updated.id}：{updated.item} {updated.amount} {updated.currency}，分类 {updated.category}，时间 {_fmt_dt(updated.transaction_date)}。"
        ]

    if content.startswith("/ledger delete"):
        m = re.match(r"^/ledger\s+delete\s+(.+)$", content)
        if not m:
            return ["用法：`/ledger delete <id|latest>`，例如 `/ledger delete 12`。"]
        target = m.group(1).strip().lower().lstrip("#")
        if target == "latest":
            latest_output = await _invoke_ledger_tool(
                user_id=user_id,
                platform=user_platform,
                conversation_id=conversation_id,
                tool_name="ledger_get_latest",
                args={"user_id": user_id},
            )
            latest = _ledger_from_payload(_parse_json_object(latest_output))
            if not latest:
                return ["暂无可删除的账单。"]
            deleted_output = await _invoke_ledger_tool(
                user_id=user_id,
                platform=user_platform,
                conversation_id=conversation_id,
                tool_name="ledger_delete",
                args={"user_id": user_id, "ledger_id": int(latest.id or 0)},
            )
            deleted = _ledger_from_payload(_parse_json_object(deleted_output))
            if not deleted:
                return ["删除失败，请稍后重试。"]
            return [
                f"已删除最近一笔：#{deleted.id} {deleted.item} {deleted.amount} {deleted.currency}，时间 {_fmt_dt(deleted.transaction_date)}。"
            ]
        if not target.isdigit():
            return ["用法：`/ledger delete <id|latest>`，例如 `/ledger delete 12`。"]
        ledger_id = int(target)
        deleted_output = await _invoke_ledger_tool(
            user_id=user_id,
            platform=user_platform,
            conversation_id=conversation_id,
            tool_name="ledger_delete",
            args={"user_id": user_id, "ledger_id": ledger_id},
        )
        deleted = _ledger_from_payload(_parse_json_object(deleted_output))
        if not deleted:
            return [f"未找到账单 #{ledger_id}，或它不属于你。"]
        return [
            f"已删除账单 #{deleted.id}：{deleted.item} {deleted.amount} {deleted.currency}，时间 {_fmt_dt(deleted.transaction_date)}。"
        ]

    return ["可用命令：`/ledger list`、`/ledger update <id> <金额> [分类] [摘要]`、`/ledger delete <id|latest>`。"]


async def ledger_manager_node(state: GraphState) -> GraphState:
    message = state["message"]
    session = get_session()
    user = await session.get(User, state["user_id"])
    if not user:
        return {**state, "responses": ["未找到用户信息。"]}
    user_id = user.id
    user_platform = user.platform
    conversation_id = int(state.get("conversation_id") or 0)

    content = (message.content or "").strip()
    context_text = render_conversation_context(state)

    if not message.image_urls and conversation_id > 0:
        pending = await get_pending_ledger(user_id=user_id, conversation_id=conversation_id)
        if pending:
            candidates = _pending_amount_candidates(pending)
            amount: float | None = None
            llm_category: str | None = None
            llm_item: str | None = None

            # LLM-first: use semantic understanding for pending confirmation.
            try:
                parsed = await _understand_pending_selection(
                    content,
                    candidates,
                    pending,
                    context_text,
                )
            except Exception:
                parsed = {}

            mode = str(parsed.get("mode") or "").strip().lower()
            if mode == "cancel":
                await clear_pending_ledger(user_id=user_id, conversation_id=conversation_id)
                return {**state, "responses": ["已取消这张图片的记账。"]}
            if mode == "indexes":
                amount = _pick_amount_from_indexes(parsed.get("indexes"), candidates)
            elif mode == "amount":
                try:
                    parsed_amount = float(parsed.get("amount"))
                    if parsed_amount > 0:
                        amount = parsed_amount
                except Exception:
                    amount = None

            llm_category_raw = str(parsed.get("category") or "").strip()
            if llm_category_raw:
                llm_category = _normalize_category(llm_category_raw)
            llm_item = _clean_item(str(parsed.get("item") or ""))

            if amount is None:
                # Minimal deterministic fallback: only accept a plain numeric reply.
                amount = _extract_plain_amount(content)

            if amount is None:
                if candidates:
                    return {**state, "responses": [_render_pending_candidates(candidates)]}
                return {
                    **state,
                    "responses": ["请直接回复要记账的金额和分类，例如：`28 餐饮 午饭`。"],
                }

            category = llm_category or _normalize_category(str(pending.get("category") or "其他"))
            item = llm_item
            if not item:
                item = str(pending.get("item") or pending.get("merchant") or "消费")
            if len(item) > 40:
                item = item[:40]

            ledger = await _insert_ledger_via_tool(
                user_id=user_id,
                platform=user_platform,
                conversation_id=conversation_id,
                amount=float(amount),
                category=category,
                item=item,
                transaction_date=datetime.utcnow(),
                image_url=str(pending.get("image_url") or ""),
            )
            if ledger is None:
                return {**state, "responses": ["记账失败，请稍后重试。"]}
            await clear_pending_ledger(user_id=user_id, conversation_id=conversation_id)
            return {
                **state,
                "responses": [
                    f"已记账：{ledger.item} {ledger.amount:.2f} {ledger.currency}，分类 {ledger.category}，时间 {_fmt_dt(ledger.transaction_date)}。"
                ],
            }

    if message.image_urls:
        image_inputs = [item for item in message.image_urls if isinstance(item, str) and item.strip()][:MAX_IMAGES]
        if len(image_inputs) > 1:
            parsed_images: list[dict] = []
            for idx, image_ref in enumerate(image_inputs, start=1):
                result = await _tracked_analyze_receipt(
                    image_ref,
                    user_id=user_id,
                    platform=user_platform,
                    conversation_id=conversation_id,
                )
                parsed = _parse_vision_result(result)
                parsed_images.append({**parsed, "index": idx, "image_url": image_ref})

            created_rows: list[tuple[dict, object]] = []
            unresolved_rows: list[dict] = []
            for parsed in parsed_images:
                amount = parsed.get("amount")
                confidence = float(parsed.get("confidence") or 0.0)
                threshold = float(parsed.get("threshold") or 1.0)
                if amount is not None and float(amount) > 0 and confidence >= threshold:
                    ledger = await _insert_ledger_via_tool(
                        user_id=user_id,
                        platform=user_platform,
                        conversation_id=conversation_id,
                        amount=float(amount),
                        category=str(parsed.get("category") or "其他"),
                        item=str(parsed.get("item") or "消费"),
                        transaction_date=datetime.utcnow(),
                        image_url=str(parsed.get("image_url") or ""),
                    )
                    if ledger is not None:
                        created_rows.append((parsed, ledger))
                else:
                    unresolved_rows.append(parsed)

            lines: list[str] = []
            if created_rows:
                lines.append(f"已解析 {len(image_inputs)} 张图片，并自动记账 {len(created_rows)} 笔：")
                for parsed, ledger in created_rows[:6]:
                    source_hint = "（支付截图识别）" if parsed.get("image_type") == "payment_screenshot" else ""
                    lines.append(
                        f"- 图{parsed.get('index')} {source_hint}：#{ledger.id} {ledger.item} {ledger.amount:.2f} {ledger.currency}，分类 {ledger.category}，时间 {_fmt_dt(ledger.transaction_date)}"
                    )
                if len(created_rows) > 6:
                    lines.append(f"- ... 其余 {len(created_rows) - 6} 笔")

            if unresolved_rows:
                lines.append(f"{len(unresolved_rows)} 张图片仍需确认金额：")
                for parsed in unresolved_rows[:4]:
                    candidates = list(parsed.get("candidate_values") or [])[:3]
                    if candidates:
                        values_text = "、".join([f"{value:.2f}" for value in candidates])
                        lines.append(f"- 图{parsed.get('index')} 候选金额：{values_text} 元")
                    else:
                        lines.append(f"- 图{parsed.get('index')} 未识别到有效金额")
                lines.append("可逐张重发更清晰图片，或直接告诉我金额与分类。")

            if lines:
                return {**state, "responses": ["\n".join(lines)]}

        result = await _tracked_analyze_receipt(
            image_inputs[0] if image_inputs else message.image_urls[0],
            user_id=user_id,
            platform=user_platform,
            conversation_id=conversation_id,
        )
        parsed_single = _parse_vision_result(result)
        image_type = str(parsed_single.get("image_type") or "other")
        confidence = float(parsed_single.get("confidence") or 0.0)
        amount = parsed_single.get("amount")
        candidate_values = list(parsed_single.get("candidate_values") or [])
        threshold = float(parsed_single.get("threshold") or 1.0)

        if len(candidate_values) > 1 and conversation_id > 0:
            await set_pending_ledger(
                user_id=user_id,
                conversation_id=conversation_id,
                payload={
                    "image_url": image_inputs[0] if image_inputs else message.image_urls[0],
                    "image_type": image_type,
                    "amount_candidates": candidate_values,
                    "category": str(parsed_single.get("category") or "其他"),
                    "item": str(parsed_single.get("item") or "消费"),
                    "merchant": str(parsed_single.get("merchant") or ""),
                    "confidence": confidence,
                },
            )
            return {
                **state,
                "responses": [_render_pending_candidates(candidate_values)],
            }

        if amount is not None and float(amount) > 0 and confidence >= threshold:
            if conversation_id > 0:
                await clear_pending_ledger(user_id=user_id, conversation_id=conversation_id)
            ledger = await _insert_ledger_via_tool(
                user_id=user_id,
                platform=user_platform,
                conversation_id=conversation_id,
                amount=float(amount),
                category=str(parsed_single.get("category") or "其他"),
                item=str(parsed_single.get("item") or "消费"),
                transaction_date=datetime.utcnow(),
                image_url=image_inputs[0] if image_inputs else message.image_urls[0],
            )
            if ledger is None:
                return {**state, "responses": ["记账失败，请稍后重试。"]}
            source_hint = "（支付截图识别）" if image_type == "payment_screenshot" else ""
            return {
                **state,
                "responses": [
                    f"已记账{source_hint}：{ledger.item} {ledger.amount} {ledger.currency}，分类 {ledger.category}，时间 {_fmt_dt(ledger.transaction_date)}。"
                ],
            }

        candidate_values = candidate_values[:3]
        if candidate_values:
            if conversation_id > 0:
                await set_pending_ledger(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    payload={
                        "image_url": image_inputs[0] if image_inputs else message.image_urls[0],
                        "image_type": image_type,
                        "amount_candidates": candidate_values,
                        "category": str(parsed_single.get("category") or "其他"),
                        "item": str(parsed_single.get("item") or "消费"),
                        "merchant": str(parsed_single.get("merchant") or ""),
                        "confidence": confidence,
                    },
                )
            values_text = "、".join([f"{v:.2f}" for v in candidate_values])
            return {
                **state,
                "responses": [
                    f"我识别到这张图可能的金额有：{values_text} 元。请直接回复准确金额和分类，例如：`{candidate_values[0]:.2f} 餐饮 晚饭`。"
                ],
            }
        return {
            **state,
            "responses": ["我没能准确识别这张图片（可能是支付截图或小票信息不完整），请告诉我金额和分类。"],
        }

    # LLM-first: understand natural-language ledger intent first.
    parsed: dict = {}
    try:
        parsed = await _understand_ledger_message(content, context_text)
    except Exception:
        parsed = {}

    intent = str(parsed.get("intent") or "").strip().lower()
    allowed_intents = {
        "insert",
        "correct_latest",
        "correct_by_id",
        "correct_by_name",
        "correct_by_scope",
        "delete_latest",
        "delete_by_id",
        "delete_by_name",
        "delete_by_scope",
        "query",
        "list",
        "unknown",
    }
    if intent not in allowed_intents:
        intent = "unknown"

    async def _resolve_query_rows() -> tuple[list[Ledger], str, str, str | None]:
        scope = str(parsed.get("query_scope") or "").strip().lower()
        query_date = str(parsed.get("query_date") or "").strip() or None
        llm_window = _resolve_ledger_window_from_fields(scope, query_date)
        category_hint = _normalize_category(str(parsed.get("category") or ""))
        if category_hint == "其他":
            category_hint = None
        if llm_window:
            start_at, end_at, label = llm_window
            rows = await _query_ledger_rows(
                user_id,
                platform=user_platform,
                conversation_id=conversation_id,
                start_at=start_at,
                end_at=end_at,
                category=category_hint,
                limit=120,
            )
            return rows, label, (scope or "date"), category_hint
        rows = await _query_ledger_rows(
            user_id,
            platform=user_platform,
            conversation_id=conversation_id,
            start_at=None,
            end_at=None,
            category=category_hint,
            limit=40,
        )
        return rows, "最近", (scope or "recent"), category_hint

    async def answer_ledger_query(rows: list[Ledger], label: str, category_hint: str | None) -> str:
        try:
            llm_text = await _answer_ledger_with_llm(
                content=content,
                conversation_context=context_text,
                label=label,
                category=category_hint,
                rows=rows,
            )
            if llm_text:
                return llm_text
        except Exception:
            pass
        return _render_ledger_fallback(rows, label, category_hint)

    ledger_id = parsed.get("ledger_id")
    try:
        ledger_id = int(ledger_id) if ledger_id is not None else None
    except Exception:
        ledger_id = None
    target_ids = _parse_int_list(parsed.get("target_ids"))
    if ledger_id is not None and ledger_id not in target_ids:
        target_ids = [ledger_id] + target_ids

    amount = parsed.get("amount")
    try:
        amount = float(amount) if amount is not None else None
    except Exception:
        amount = None

    parsed_item = _clean_item(str(parsed.get("item") or ""))
    item = parsed_item

    category = _normalize_category(str(parsed.get("category") or ""))
    if intent == "insert":
        if category == "其他":
            category = _normalize_category(content)
    elif category == "其他":
        # For correction/delete/query flows, never derive category from full sentence.
        # This avoids accidental category overwrite such as "不对是45" -> category.
        category = ""

    target_item = _clean_item(str(parsed.get("target_item") or ""))
    if intent == "insert":
        if not item:
            item = _clean_item(content)
        if not target_item:
            target_item = item
    else:
        # For correction/delete flows, do not derive replacement item from whole sentence.
        # Keep item empty unless the model explicitly extracted a new item field.
        if not target_item:
            target_item = parsed_item or _clean_item(content)
    if intent.startswith("correct_") and item and target_item and item == target_item:
        item = ""

    reference_mode = str(parsed.get("reference_mode") or "auto").strip().lower()
    if reference_mode not in {"by_id", "by_name", "by_scope", "latest", "last_result_set", "auto"}:
        reference_mode = "auto"
    selection_mode = str(parsed.get("selection_mode") or "auto").strip().lower()
    if selection_mode not in {"all", "single", "subset", "auto"}:
        selection_mode = "auto"

    async def _resolve_operation_ledgers(operation: str) -> tuple[list[Ledger], str]:
        # 1) explicit id(s)
        if target_ids and (reference_mode in {"by_id", "auto"} or intent in {"delete_by_id", "correct_by_id"}):
            rows = await _query_ledgers_by_ids(
                user_id,
                target_ids,
                platform=user_platform,
                conversation_id=conversation_id,
            )
            return rows, "by_id"

        # 2) previous result set
        if reference_mode == "last_result_set":
            last_ids = _read_last_ledger_ids_from_state(state)
            rows = await _query_ledgers_by_ids(
                user_id,
                last_ids,
                platform=user_platform,
                conversation_id=conversation_id,
            )
            return rows, "last_result_set"

        # 3) scope based
        if intent in {"delete_by_scope", "correct_by_scope"} or reference_mode == "by_scope":
            scope = str(parsed.get("query_scope") or "").strip().lower()
            query_date = str(parsed.get("query_date") or "").strip() or None
            category_hint = _normalize_category(str(parsed.get("category") or ""))
            if category_hint == "其他":
                category_hint = None
            llm_window = _resolve_ledger_window_from_fields(scope, query_date)
            if llm_window:
                start_at, end_at, _ = llm_window
                rows = await _query_ledger_rows(
                    user_id,
                    platform=user_platform,
                    conversation_id=conversation_id,
                    start_at=start_at,
                    end_at=end_at,
                    category=category_hint,
                    limit=300,
                )
                return rows, "by_scope"
            # Fallback to last query if scope parse failed.
            last_ids = _read_last_ledger_ids_from_state(state)
            rows = await _query_ledgers_by_ids(
                user_id,
                last_ids,
                platform=user_platform,
                conversation_id=conversation_id,
            )
            return rows, "last_result_set"

        # 4) latest
        if intent in {"delete_latest", "correct_latest"} or reference_mode == "latest":
            latest_output = await _invoke_ledger_tool(
                user_id=user_id,
                platform=user_platform,
                conversation_id=conversation_id,
                tool_name="ledger_get_latest",
                args={"user_id": user_id},
            )
            latest = _ledger_from_payload(_parse_json_object(latest_output))
            return ([latest] if latest else []), "latest"

        # 5) by name or semantic selection from candidates
        candidates = await _query_ledger_rows(
            user_id,
            platform=user_platform,
            conversation_id=conversation_id,
            start_at=None,
            end_at=None,
            category=None,
            limit=160,
        )
        if not candidates:
            return [], "by_name"
        picked_ids = await _select_ledger_ids_by_llm(
            content=content,
            conversation_context=context_text,
            target_item=target_item,
            operation=operation,
            selection_mode=selection_mode,
            candidates=candidates,
        )
        if not picked_ids:
            picked_ids = await _select_ledger_ids_from_context(
                content=content,
                conversation_context=context_text,
                candidates=candidates,
            )
        if not picked_ids and selection_mode != "single":
            # one-shot fallback for single selection
            maybe_one = await _select_ledger_id_by_llm(
                content=content,
                conversation_context=context_text,
                target_item=target_item,
                operation=operation,
                candidates=candidates,
            )
            if maybe_one:
                picked_ids = [maybe_one]
        rows = await _query_ledgers_by_ids(
            user_id,
            picked_ids,
            platform=user_platform,
            conversation_id=conversation_id,
        )
        return rows, "by_name"

    if intent == "unknown":
        # LLM SQL fallback for long-tail ledger CRUD queries.
        try:
            sql_response = await _tracked_text2sql(
                user_id=user_id,
                message=content,
                conversation_context=context_text,
                platform=user_platform,
                conversation_id=conversation_id,
            )
        except Exception:
            sql_response = None
        if sql_response:
            return {**state, "responses": [sql_response]}

        # Deterministic command fallback.
        command_responses = await _handle_ledger_command(
            content=content,
            user_id=user_id,
            user_platform=user_platform,
            conversation_id=conversation_id,
        )
        if command_responses:
            return {**state, "responses": command_responses}

        return {
            **state,
            "responses": [
                "我没完全理解你的账单意图。可直接说：`记一笔 午饭30`、`删除账单#12`、`把账单#12改成28元`、`列出最近账单`。"
            ],
        }

    if intent == "list":
        rows, label, scope_key, category_hint = await _resolve_query_rows()
        reply = await answer_ledger_query(rows, label, category_hint)
        next_state = _with_last_ledger_query(
            state,
            rows=rows,
            label=label,
            scope=scope_key,
            category=category_hint,
        )
        return {**next_state, "responses": [reply]}

    if intent == "query":
        rows, label, scope_key, category_hint = await _resolve_query_rows()
        reply = await answer_ledger_query(rows, label, category_hint)
        next_state = _with_last_ledger_query(
            state,
            rows=rows,
            label=label,
            scope=scope_key,
            category=category_hint,
        )
        return {**next_state, "responses": [reply]}

    if intent in {"delete_by_name", "delete_by_id", "delete_latest", "delete_by_scope"}:
        target_rows, source = await _resolve_operation_ledgers("delete")
        matched_count, deleted_rows = await _delete_ledgers_by_ids(
            user_id=user_id,
            user_platform=user_platform,
            conversation_id=conversation_id,
            ledger_ids=[int(row.id or 0) for row in target_rows if int(row.id or 0) > 0],
        )
        deleted_count = len(deleted_rows)
        if matched_count == 0:
            return {
                **state,
                "responses": ["我还不能定位要删除的账单。请补充账单名称、ID、范围，或先查询后说“删这几笔”。"],
            }
        preview = ""
        if deleted_rows:
            head = deleted_rows[0]
            preview = f" 示例：#{head.id} {head.item} {head.amount:.2f} {head.currency}（{_fmt_dt(head.transaction_date)}）。"
        return {
            **state,
            "responses": [f"已定位 {matched_count} 条（来源：{source}），成功删除 {deleted_count} 条。{preview}".strip()],
        }

    if amount is None:
        return {**state, "responses": ["请告诉我金额，例如：今天晚饭 28 元。"]}

    if intent in {"correct_by_name", "correct_by_id", "correct_latest", "correct_by_scope"}:
        target_rows, source = await _resolve_operation_ledgers("correct")
        if not target_rows:
            return {
                **state,
                "responses": ["我还不能定位要更正的账单。请补充账单名称、ID、范围，或先查询后说“改这几笔”。"],
            }
        matched_count, updated_rows = await _update_ledgers_by_ids(
            user_id=user_id,
            user_platform=user_platform,
            conversation_id=conversation_id,
            ledger_ids=[int(row.id or 0) for row in target_rows if int(row.id or 0) > 0],
            amount=amount,
            category=(category if category and category != "其他" else None),
            item=(item or None),
        )
        updated_count = len(updated_rows)
        preview = ""
        if updated_rows:
            head = updated_rows[0]
            preview = (
                f" 示例：#{head.id} {head.item} {head.amount:.2f} {head.currency}，"
                f"分类 {head.category}（{_fmt_dt(head.transaction_date)}）。"
            )
        return {
            **state,
            "responses": [f"已定位 {matched_count} 条（来源：{source}），成功更正 {updated_count} 条。{preview}".strip()],
        }

    ledger = await _insert_ledger_via_tool(
        user_id=user_id,
        platform=user_platform,
        conversation_id=conversation_id,
        amount=float(amount),
        category=category,
        item=item or "消费",
        transaction_date=datetime.utcnow(),
    )
    if ledger is None:
        return {**state, "responses": ["记账失败，请稍后重试。"]}
    return {
        **state,
        "responses": [
            f"已记账：{ledger.item} {ledger.amount} {ledger.currency}，分类 {ledger.category}，时间 {_fmt_dt(ledger.transaction_date)}。"
        ],
    }
