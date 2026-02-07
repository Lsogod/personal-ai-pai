import json
import re
from datetime import datetime

from langchain_core.messages import HumanMessage, SystemMessage

from app.graph.context import render_conversation_context
from app.graph.state import GraphState
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


AMOUNT_PATTERN = re.compile(r"(\d+(?:\.\d{1,2})?)")
LEDGER_ID_PATTERN = re.compile(r"(?:账单\s*#?\s*|#)(\d+)")
PENDING_INDEX_PATTERN = re.compile(r"(?:第\s*([1-9]\d*)\s*(?:个|笔|项)|选\s*([1-9]\d*)|#([1-9]\d*))")
PENDING_CN_INDEX_PATTERN = re.compile(r"第\s*([一二三四五六七八九十两])\s*(?:个|笔|项)")
PENDING_BATCH_SELECT_PATTERN = re.compile(r"选\s*([1-9]\d*(?:\s*[,，、和及]\s*[1-9]\d*)+)")
CANCEL_PENDING_TOKENS = ("取消", "算了", "不要了", "放弃", "不记了")
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


def _fmt_dt(dt: datetime | None) -> str:
    if not dt:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M")


def _extract_amount(text: str) -> float | None:
    source = text or ""
    candidates: list[float] = []
    for match in AMOUNT_PATTERN.finditer(source):
        token = match.group(1)
        start = match.start(1)
        end = match.end(1)
        prev_char = source[start - 1] if start > 0 else ""
        next_char = source[end] if end < len(source) else ""
        # Avoid treating ordinal selectors like "第2个/第1项" as money amounts.
        if prev_char == "第" and next_char in {"个", "笔", "项"}:
            continue
        try:
            value = float(token)
        except Exception:
            continue
        candidates.append(value)
    if not candidates:
        return None
    return candidates[-1]


def _extract_correction_amount(text: str) -> float | None:
    patterns = [
        r"(?:改成|改为|更正为|应该是|不是)\s*(\d+(?:\.\d{1,2})?)",
        r"(\d+(?:\.\d{1,2})?)\s*(?:元|块)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text or "")
        if match:
            try:
                return float(match.group(1))
            except Exception:
                continue
    return _extract_amount(text)


def _extract_ledger_id(text: str) -> int | None:
    match = LEDGER_ID_PATTERN.search(text or "")
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None


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


def _resolve_pending_amount(content: str, candidates: list[float]) -> float | None:
    indices = _extract_pending_indices(content, len(candidates))
    if indices:
        total = sum(candidates[idx - 1] for idx in indices)
        return round(total, 2)
    amount = _extract_amount(content)
    if amount is not None and amount > 0:
        return amount
    return None


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


def _extract_pending_category(content: str) -> str | None:
    source = (content or "").strip()
    if not source:
        return None
    for key, value in CATEGORY_MAP.items():
        if key in source:
            return value
    explicit = re.search(r"(?:分类|类别)\s*[:：]?\s*([^\s，,。.!！?？]{1,10})", source)
    if explicit:
        return _normalize_category(explicit.group(1))
    return None


def _render_pending_candidates(candidates: list[float]) -> str:
    lines = ["我识别到多个支付金额："]
    for idx, value in enumerate(candidates, start=1):
        lines.append(f"{idx}. {value:.2f} 元")
    lines.append("请回复要记账的金额（可回复“第1个”或直接“28.50”）。")
    lines.append("支持多选合并记账，例如：`第二个和第三个`、`选2和3`。")
    lines.append("可附带分类和摘要，例如：`第1个 餐饮 午饭`。")
    lines.append("不记这张图可回复：`取消`。")
    return "\n".join(lines)


async def _understand_finance_message(content: str, conversation_context: str) -> dict:
    llm = get_llm()
    system = SystemMessage(
        content=(
            "你是记账意图解析器。只输出 JSON。"
            "字段: intent, ledger_id, amount, item, category, confidence。"
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

            # Rule fallback when LLM is uncertain or fails.
            if amount is None and any(token in content for token in CANCEL_PENDING_TOKENS):
                await clear_pending_ledger(user_id=user_id, conversation_id=conversation_id)
                return {**state, "responses": ["已取消这张图片的记账。"]}
            if amount is None:
                amount = _resolve_pending_amount(content, candidates)

            if amount is None:
                if candidates:
                    return {**state, "responses": [_render_pending_candidates(candidates)]}
                return {
                    **state,
                    "responses": ["请直接回复要记账的金额和分类，例如：`28 餐饮 午饭`。"],
                }

            parsed_category = _extract_pending_category(content)
            category = llm_category or parsed_category or _normalize_category(str(pending.get("category") or "其他"))
            item = llm_item or _clean_item(content)
            if item and parsed_category and item.startswith(parsed_category):
                item = item[len(parsed_category) :].strip(" ，,。")
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
        result = await analyze_receipt(message.image_urls[0])
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
        if len(candidate_values) > 1 and conversation_id > 0:
            await set_pending_ledger(
                user_id=user_id,
                conversation_id=conversation_id,
                payload={
                    "image_url": message.image_urls[0],
                    "image_type": image_type,
                    "amount_candidates": candidate_values,
                    "category": _normalize_category(str(result.get("category") or "其他")),
                    "item": str(result.get("item") or result.get("merchant") or "消费"),
                    "merchant": str(result.get("merchant") or ""),
                    "confidence": confidence,
                },
            )
            return {
                **state,
                "responses": [_render_pending_candidates(candidate_values)],
            }

        if amount is not None and amount > 0 and confidence >= threshold:
            if conversation_id > 0:
                await clear_pending_ledger(user_id=user_id, conversation_id=conversation_id)
            ledger = await insert_ledger(
                session,
                user_id=user_id,
                amount=amount,
                category=_normalize_category(str(result.get("category", "其他"))),
                item=str(result.get("item", result.get("merchant", "消费"))),
                transaction_date=datetime.utcnow(),
                image_url=message.image_urls[0],
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
                        "image_url": message.image_urls[0],
                        "image_type": image_type,
                        "amount_candidates": candidate_values,
                        "category": _normalize_category(str(result.get("category") or "其他")),
                        "item": str(result.get("item") or result.get("merchant") or "消费"),
                        "merchant": str(result.get("merchant") or ""),
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

    ledger_id = parsed.get("ledger_id")
    try:
        ledger_id = int(ledger_id) if ledger_id is not None else None
    except Exception:
        ledger_id = None
    if ledger_id is None:
        ledger_id = _extract_ledger_id(content)

    amount = parsed.get("amount")
    try:
        amount = float(amount) if amount is not None else None
    except Exception:
        amount = None
    if amount is None:
        amount = _extract_correction_amount(content) if ledger_id is not None else _extract_amount(content)

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
        rows = await list_recent_ledgers(session, user_id, limit=10)
        if not rows:
            return {**state, "responses": ["暂无账单记录。"]}
        lines = ["最近账单："]
        for row in rows:
            lines.append(
                f"#{row.id} | {_fmt_dt(row.transaction_date)} | {row.amount:.2f} {row.currency} | {row.category} | {row.item}"
            )
        lines.append("要修改指定账单可说：`把账单#12改成28元 分类餐饮`。删除可说：`删除账单#12`。")
        return {**state, "responses": ["\n".join(lines)]}

    if intent == "query":
        return {**state, "responses": ["你可以在右侧账单概览查看统计，或说“本月花了多少”。"]}

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
