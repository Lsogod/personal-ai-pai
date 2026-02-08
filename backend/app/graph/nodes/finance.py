import json
import re
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import select

from app.core.config import get_settings
from app.graph.context import render_conversation_context
from app.graph.state import GraphState
from app.models.ledger import Ledger
from app.models.user import User
from app.services.llm import get_llm
from app.services.runtime_context import get_session
from app.services.ledger_pending import (
    clear_pending_ledger,
    get_pending_ledger,
    set_pending_ledger,
)
from app.tools.finance import (
    delete_ledger,
    get_latest_ledger,
    insert_ledger,
    list_recent_ledgers,
    update_ledger,
)
from app.tools.ledger_text2sql import try_execute_ledger_text2sql
from app.tools.vision import analyze_receipt


DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")
TECHNICAL_LEAK_PATTERN = re.compile(r"(json|payload|字段|数组|schema|schedules|ledgers)", re.IGNORECASE)
PENDING_INDEX_PATTERN = re.compile(r"(?:第\s*([1-9]\d*)\s*(?:个|笔|项)|选\s*([1-9]\d*)|#([1-9]\d*))")
PENDING_CN_INDEX_PATTERN = re.compile(r"第\s*([一二三四五六七八九十两])\s*(?:个|笔|项)")
PENDING_BATCH_SELECT_PATTERN = re.compile(r"选\s*([1-9]\d*(?:\s*[,，、和及]\s*[1-9]\d*)+)")
CN_NUM_MAP = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

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
    return dt.strftime("%Y-%m-%d %H:%M")


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
    value = re.sub(
        r"(?:第\s*(?:[1-9]\d*|[一二三四五六七八九十两])\s*(?:个|笔|项)|选\s*[1-9]\d*|#[1-9]\d*)",
        "",
        value,
    )
    value = re.sub(r"^(错了|更正|改成|改为|修正|记错|不是|应该是)\s*", "", value)
    value = re.sub(r"^(今天|刚才|刚刚)\s*", "", value)
    value = re.sub(r"\d+(?:\.\d{1,2})?", "", value)
    value = value.replace("元", "").replace("块", "").replace("记账", "").strip()
    value = value.replace("和", " ").replace("及", " ").replace("并且", " ")
    value = re.sub(r"\s+", " ", value).strip()
    value = value.strip("，,。.!！?？")
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
        has_currency = bool(re.search(r"(?:元|块|¥|￥|rmb|RMB|人民币)", source))
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
    llm = get_llm()
    system = SystemMessage(
        content=(
            "你是待确认记账解析器。只输出 JSON。"
            "字段: mode, indexes, amount, category, item。"
            "mode 仅可为 indexes, amount, cancel, unknown。"
            "若用户选择候选项（如“1 2”“第二个和第三个”“选2和3”“前两个”），mode=indexes，indexes 输出数组。"
            "若用户给出直接金额（如“28.50”），mode=amount，amount 输出数字。"
            "若用户表示取消（如“取消/算了/不记了”），mode=cancel。"
            "若不确定，mode=unknown。"
            "category 仅在用户明确提到分类时填写（餐饮/交通/购物/居家/娱乐/医疗/其他），否则留空。"
            "item 仅在用户明确给出摘要时填写，否则留空。"
        )
    )
    human = HumanMessage(
        content=(
            f"会话上下文:\n{conversation_context}\n\n"
            f"候选金额列表（序号从1开始）: {candidates}\n"
            f"识别来源摘要: {pending.get('item') or pending.get('merchant') or '消费'}\n"
            f"默认分类: {pending.get('category') or '其他'}\n"
            f"用户回复: {content}"
        )
    )
    response = await llm.ainvoke([system, human])
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


async def _understand_finance_message(content: str, conversation_context: str) -> dict:
    llm = get_llm()
    system = SystemMessage(
        content=(
            "你是记账意图解析器。只输出 JSON。"
            "字段: intent, ledger_id, amount, item, category, query_scope, query_date, confidence。"
            "intent 仅可为 insert, correct_latest, correct_by_id, delete_latest, delete_by_id, query, list, unknown。"
            "若用户在纠正上一笔（例如“错了...”“改成...”），intent=correct_latest。"
            "若用户在纠正指定账单（例如“把账单#12改成28元”），intent=correct_by_id，并给出 ledger_id。"
            "若用户在删除最近一笔，intent=delete_latest。"
            "若用户在删除指定账单（例如“删除账单#12”），intent=delete_by_id，并给出 ledger_id。"
            "若用户在新增一笔支出，intent=insert。"
            "若用户在问统计数据，intent=query。"
            "若用户在要账单列表，intent=list。"
            "无法确定时 intent=unknown。"
            "amount 为数字；item 为简洁摘要，必须去掉“错了/改成”等词。"
            "category 不确定填“其他”。"
            "query_scope 仅可为 today/week/month/date/recent/all/yesterday/day_before_yesterday/last_week。"
            "当 query_scope=date 时，query_date 输出 YYYY-MM-DD。"
            "当用户说“我今天的账单/本周花了多少”等查询，必须输出 query_scope。"
            "当用户说昨天/前天时，优先用 query_scope=yesterday/day_before_yesterday；"
            "当用户说上周时，用 query_scope=last_week。"
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
    session,
    user_id: int,
    *,
    start_at: datetime | None,
    end_at: datetime | None,
    category: str | None,
    limit: int,
) -> list[Ledger]:
    stmt = select(Ledger).where(Ledger.user_id == user_id)
    if start_at is not None and end_at is not None:
        stmt = stmt.where(Ledger.transaction_date >= start_at, Ledger.transaction_date < end_at)
    if category:
        stmt = stmt.where(Ledger.category == category)
    stmt = stmt.order_by(Ledger.transaction_date.desc(), Ledger.id.desc()).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


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


async def _answer_ledger_with_llm(
    *,
    content: str,
    conversation_context: str,
    label: str,
    category: str | None,
    rows: list[Ledger],
) -> str:
    llm = get_llm()
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
    session,
    user_id: int,
    user_platform: str,
) -> list[str] | None:
    if not content.startswith("/ledger"):
        return None

    if content.startswith("/ledger list"):
        rows = await list_recent_ledgers(session, user_id, limit=10)
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

        updated = await update_ledger(
            session,
            user_id=user_id,
            ledger_id=ledger_id,
            amount=amount,
            category=category if category != "其他" else None,
            item=item or None,
            platform=user_platform,
        )
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
            latest = await get_latest_ledger(session, user_id)
            if not latest:
                return ["暂无可删除的账单。"]
            deleted = await delete_ledger(
                session,
                user_id=user_id,
                ledger_id=latest.id,
                platform=user_platform,
            )
            if not deleted:
                return ["删除失败，请稍后重试。"]
            return [
                f"已删除最近一笔：#{deleted.id} {deleted.item} {deleted.amount} {deleted.currency}，时间 {_fmt_dt(deleted.transaction_date)}。"
            ]
        if not target.isdigit():
            return ["用法：`/ledger delete <id|latest>`，例如 `/ledger delete 12`。"]
        ledger_id = int(target)
        deleted = await delete_ledger(
            session,
            user_id=user_id,
            ledger_id=ledger_id,
            platform=user_platform,
        )
        if not deleted:
            return [f"未找到账单 #{ledger_id}，或它不属于你。"]
        return [
            f"已删除账单 #{deleted.id}：{deleted.item} {deleted.amount} {deleted.currency}，时间 {_fmt_dt(deleted.transaction_date)}。"
        ]

    return ["可用命令：`/ledger list`、`/ledger update <id> <金额> [分类] [摘要]`、`/ledger delete <id|latest>`。"]


async def finance_node(state: GraphState) -> GraphState:
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

            ledger = await insert_ledger(
                session,
                user_id=user_id,
                amount=amount,
                category=category,
                item=item,
                transaction_date=datetime.utcnow(),
                image_url=str(pending.get("image_url") or ""),
                platform=user_platform,
            )
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
                result = await analyze_receipt(image_ref)
                parsed = _parse_vision_result(result)
                parsed_images.append({**parsed, "index": idx, "image_url": image_ref})

            created_rows: list[tuple[dict, object]] = []
            unresolved_rows: list[dict] = []
            for parsed in parsed_images:
                amount = parsed.get("amount")
                confidence = float(parsed.get("confidence") or 0.0)
                threshold = float(parsed.get("threshold") or 1.0)
                if amount is not None and float(amount) > 0 and confidence >= threshold:
                    ledger = await insert_ledger(
                        session,
                        user_id=user_id,
                        amount=float(amount),
                        category=str(parsed.get("category") or "其他"),
                        item=str(parsed.get("item") or "消费"),
                        transaction_date=datetime.utcnow(),
                        image_url=str(parsed.get("image_url") or ""),
                        platform=user_platform,
                    )
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

        result = await analyze_receipt(image_inputs[0] if image_inputs else message.image_urls[0])
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
            ledger = await insert_ledger(
                session,
                user_id=user_id,
                amount=float(amount),
                category=str(parsed_single.get("category") or "其他"),
                item=str(parsed_single.get("item") or "消费"),
                transaction_date=datetime.utcnow(),
                image_url=image_inputs[0] if image_inputs else message.image_urls[0],
                platform=user_platform,
            )
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
        parsed = await _understand_finance_message(content, context_text)
    except Exception:
        parsed = {}

    intent = str(parsed.get("intent") or "").strip().lower()
    allowed_intents = {
        "insert",
        "correct_latest",
        "correct_by_id",
        "delete_latest",
        "delete_by_id",
        "query",
        "list",
        "unknown",
    }
    if intent not in allowed_intents:
        intent = "unknown"

    async def answer_ledger_query() -> str:
        scope = str(parsed.get("query_scope") or "").strip().lower()
        query_date = str(parsed.get("query_date") or "").strip() or None
        llm_window = _resolve_ledger_window_from_fields(scope, query_date)
        category_hint = _normalize_category(str(parsed.get("category") or ""))
        if category_hint == "其他":
            category_hint = None
        if llm_window:
            start_at, end_at, label = llm_window
            rows = await _query_ledger_rows(
                session,
                user_id,
                start_at=start_at,
                end_at=end_at,
                category=category_hint,
                limit=80,
            )
        else:
            label = "最近"
            rows = await _query_ledger_rows(
                session,
                user_id,
                start_at=None,
                end_at=None,
                category=category_hint,
                limit=20,
            )
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

    amount = parsed.get("amount")
    try:
        amount = float(amount) if amount is not None else None
    except Exception:
        amount = None

    item = _clean_item(str(parsed.get("item") or ""))
    if not item:
        item = _clean_item(content)

    category = _normalize_category(str(parsed.get("category") or ""))
    if category == "其他":
        category = _normalize_category(content)

    if intent == "unknown":
        # LLM SQL fallback for long-tail ledger CRUD queries.
        try:
            sql_response = await try_execute_ledger_text2sql(
                user_id=user_id,
                message=content,
                conversation_context=context_text,
            )
        except Exception:
            sql_response = None
        if sql_response:
            return {**state, "responses": [sql_response]}

        # Deterministic command fallback.
        command_responses = await _handle_ledger_command(
            content=content,
            session=session,
            user_id=user_id,
            user_platform=user_platform,
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
        reply = await answer_ledger_query()
        return {**state, "responses": [reply]}

    if intent == "query":
        reply = await answer_ledger_query()
        return {**state, "responses": [reply]}

    if intent == "delete_by_id":
        if ledger_id is None:
            return {**state, "responses": ["请指定账单 ID，例如：`删除账单#12`。"]}
        deleted = await delete_ledger(
            session,
            user_id=user_id,
            ledger_id=ledger_id,
            platform=user_platform,
        )
        if not deleted:
            return {**state, "responses": [f"未找到账单 #{ledger_id}，或它不属于你。"]}
        return {
            **state,
            "responses": [
                f"已删除账单 #{deleted.id}：{deleted.item} {deleted.amount} {deleted.currency}，时间 {_fmt_dt(deleted.transaction_date)}。"
            ],
        }

    if intent == "delete_latest":
        latest = await get_latest_ledger(session, user_id)
        if not latest:
            return {**state, "responses": ["暂无可删除的账单。"]}
        deleted = await delete_ledger(
            session,
            user_id=user_id,
            ledger_id=latest.id,
            platform=user_platform,
        )
        if not deleted:
            return {**state, "responses": ["删除失败，请稍后重试。"]}
        return {
            **state,
            "responses": [
                f"已删除最近一笔：#{deleted.id} {deleted.item} {deleted.amount} {deleted.currency}，时间 {_fmt_dt(deleted.transaction_date)}。"
            ],
        }

    if amount is None:
        return {**state, "responses": ["请告诉我金额，例如：今天晚饭 28 元。"]}

    if intent == "correct_by_id":
        if ledger_id is None:
            return {**state, "responses": ["请指定账单 ID，例如：`把账单#12改成28元`。"]}
        updated = await update_ledger(
            session,
            user_id=user_id,
            ledger_id=ledger_id,
            amount=amount,
            category=category if category != "其他" else None,
            item=item or None,
            platform=user_platform,
        )
        if not updated:
            return {**state, "responses": [f"未找到账单 #{ledger_id}，或它不属于你。"]}
        return {
            **state,
            "responses": [
                f"已更正账单 #{updated.id}：{updated.item} {updated.amount} {updated.currency}，分类 {updated.category}，时间 {_fmt_dt(updated.transaction_date)}。"
            ],
        }

    if intent == "correct_latest":
        latest = await get_latest_ledger(session, user_id)
        if not latest:
            return {**state, "responses": ["你还没有可更正的账单，请先记一笔。"]}
        updated = await update_ledger(
            session,
            user_id=user_id,
            ledger_id=latest.id,
            amount=amount,
            category=category if category != "其他" else None,
            item=item or latest.item,
            platform=user_platform,
        )
        if not updated:
            return {**state, "responses": ["更正失败，请稍后重试。"]}
        return {
            **state,
            "responses": [
                f"已更正最近一笔：{updated.item} {updated.amount} {updated.currency}，分类 {updated.category}（账单 #{updated.id}，时间 {_fmt_dt(updated.transaction_date)}）。"
            ],
        }

    ledger = await insert_ledger(
        session,
        user_id=user_id,
        amount=amount,
        category=category,
        item=item or "消费",
        transaction_date=datetime.utcnow(),
        platform=user_platform,
    )
    return {
        **state,
        "responses": [
            f"已记账：{ledger.item} {ledger.amount} {ledger.currency}，分类 {ledger.category}，时间 {_fmt_dt(ledger.transaction_date)}。"
        ],
    }
