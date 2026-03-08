import json
import re
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

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
from app.services.toolsets import invoke_node_tool_typed
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
    output = await invoke_node_tool_typed(
        context=ToolInvocationContext(
            user_id=user_id,
            platform=platform or "unknown",
            conversation_id=conversation_id,
        ),
        node_name="ledger_manager",
        tool_name="analyze_receipt",
        args={"image_ref": image_ref},
    )
    if isinstance(output, str) and _looks_like_tool_error(output):
        raise RuntimeError(output)
    payload = _parse_json_object(output)
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
    output = await invoke_node_tool_typed(
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
    if isinstance(output, str) and _looks_like_tool_error(output):
        raise RuntimeError(output)
    text = str(output or "").strip()
    return text or None


async def _tracked_text2sql_preview_write(
    *,
    user_id: int,
    message: str,
    operation: str,
    update_fields: dict[str, Any] | None = None,
    preview_hints: dict[str, Any] | None = None,
    conversation_context: str = "",
    platform: str = "",
    conversation_id: int | None = None,
) -> dict[str, Any] | None:
    output = await invoke_node_tool_typed(
        context=ToolInvocationContext(
            user_id=user_id,
            platform=platform or "unknown",
            conversation_id=conversation_id,
        ),
        node_name="ledger_manager",
        tool_name="ledger_text2sql",
        args={
            "mode": "preview_write",
            "operation": operation,
            "message": message,
            "update_fields": dict(update_fields or {}),
            "preview_hints": dict(preview_hints or {}),
            "conversation_context": conversation_context,
            "preview_limit": 50,
        },
    )
    if isinstance(output, str) and _looks_like_tool_error(output):
        raise RuntimeError(output)
    payload = _parse_json_object(output)
    if payload:
        return payload
    if isinstance(output, dict):
        return output
    return None


async def _tracked_text2sql_commit_write_by_ids(
    *,
    user_id: int,
    operation: str,
    target_ids: list[int],
    expected_count: int,
    update_fields: dict[str, Any] | None = None,
    platform: str = "",
    conversation_id: int | None = None,
) -> dict[str, Any]:
    output = await invoke_node_tool_typed(
        context=ToolInvocationContext(
            user_id=user_id,
            platform=platform or "unknown",
            conversation_id=conversation_id,
        ),
        node_name="ledger_manager",
        tool_name="ledger_text2sql",
        args={
            "mode": "commit_write_by_ids",
            "operation": operation,
            "message": "commit_write_by_ids",
            "target_ids": list(target_ids or []),
            "expected_count": int(expected_count or 0),
            "update_fields": dict(update_fields or {}),
        },
    )
    if isinstance(output, str) and _looks_like_tool_error(output):
        raise RuntimeError(output)
    payload = _parse_json_object(output)
    if payload:
        return payload
    if isinstance(output, dict):
        return output
    return {}


DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")
TECHNICAL_LEAK_PATTERN = re.compile(r"(json|payload|字段|数组|schema|schedules|ledgers)", re.IGNORECASE)

CATEGORY_MAP = {
    "餐饮": "餐饮",
    "吃饭": "餐饮",
    "午饭": "餐饮",
    "晚饭": "餐饮",
    "早饭": "餐饮",
    "交通": "交通",
    "打车": "交通",
    "地铁": "交通",
    "公交": "交通",
    "购物": "购物",
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


class LedgerPendingSelectionExtraction(BaseModel):
    mode: str = Field(default="unknown")
    indexes: list[int] = Field(default_factory=list)
    amount: float | None = Field(default=None)
    category: str = Field(default="")
    item: str = Field(default="")


class LedgerIntentExtraction(BaseModel):
    intent: Any = Field(default="unknown")
    ledger_id: Any = Field(default=None)
    target_ids: Any = Field(default_factory=list)
    target_item: Any = Field(default="")
    amount: Any = Field(default=None)
    item: Any = Field(default="")
    category: Any = Field(default="")
    query_scope: Any = Field(default="")
    query_date: Any = Field(default="")
    reference_mode: Any = Field(default="auto")
    selection_mode: Any = Field(default="auto")
    confidence: Any = Field(default=0.0)


class LedgerUpdateRewriteExtraction(BaseModel):
    target_item: str = Field(default="")
    item: str = Field(default="")
    category: str = Field(default="")
    amount: float | None = Field(default=None)
    amount_explicit: bool = Field(default=False)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class LedgerPreviewControlExtraction(BaseModel):
    action: str = Field(default="unknown")
    indexes: list[int] = Field(default_factory=list)
    comparator: str = Field(default="")
    threshold: float | None = Field(default=None)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class LedgerHandoffDecision(BaseModel):
    handoff: bool = Field(default=False)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class LedgerClarificationExtraction(BaseModel):
    question: str = Field(default="")


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


def _resolve_query_category_hint(content: str, parsed_category: str | None) -> str | None:
    normalized = _normalize_category(parsed_category)
    if not (normalized or "").strip():
        return None
    if normalized == _normalize_category(""):
        return None
    return normalized


def _parse_json_object(content: Any) -> dict:
    if isinstance(content, dict):
        return content
    if content is None:
        return {}
    if isinstance(content, list):
        return {}
    text = str(content).strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def _parse_json_list(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, list):
        return [item for item in content if isinstance(item, dict)]
    if content is None:
        return []
    if isinstance(content, dict):
        return []
    text = str(content).strip()
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
) -> Any:
    return await invoke_node_tool_typed(
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


async def _render_fixed_reply_with_llm(
    *,
    fallback: str,
    style: str,
    facts: dict[str, Any] | None = None,
    required_keywords: list[str] | None = None,
) -> str:
    base = (fallback or "").strip()
    facts_payload = dict(facts or {})
    facts_text = json.dumps(facts_payload, ensure_ascii=False)
    llm = get_llm(node_name="ledger_manager")
    system = SystemMessage(
        content=(
            "你是账单助手文案渲染器。"
            "请基于给定事实输出最终用户回复，使其自然、清晰、简洁。"
            "不得改变事实，不得新增事实，不得遗漏关键操作指令。"
            "仅输出最终文本，不输出解释。"
        )
    )
    attempts = 2
    last_error = "llm_render_failed"
    for attempt in range(attempts):
        retry_hint = "请确保输出更直接、简洁。" if attempt > 0 else ""
        try:
            human = HumanMessage(
                content=(
                    f"style: {style}\n\n"
                    f"facts_json:\n{json.dumps(facts_payload, ensure_ascii=False)}\n\n"
                    f"source_text:\n{base}\n\n"
                    f"retry_hint:\n{retry_hint}"
                )
            )
            response = await llm.ainvoke([system, human])
            candidate = _sanitize_llm_text(str(response.content or ""))
            if not candidate:
                last_error = "empty_llm_output"
                continue
            return candidate
        except Exception as exc:
            last_error = str(exc)
            continue
    try:
        rescue = await llm.ainvoke(
            [
                system,
                HumanMessage(
                    content=(
                        f"style: {style}\n\n"
                        f"facts_json:\n{facts_text}\n\n"
                        "请直接生成一条自然、简洁的最终回复。"
                    )
                ),
            ]
        )
        rescue_text = _sanitize_llm_text(str(rescue.content or ""))
        if rescue_text:
            return rescue_text
    except Exception as exc:
        last_error = str(exc)
    if base:
        return base
    raise RuntimeError(f"llm_render_failed:{last_error}")


def _clean_item(item: str) -> str:
    value = (item or "").strip()
    if not value:
        return ""
    value = re.sub(r"(?:第\s*\d+\s*(?:笔|条)|#\s*\d+)", "", value)
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
    value = value.strip("，。,.!?！？;；:：")
    if value in {"和", "及", "并"}:
        return ""
    return value


GENERIC_TARGET_ITEM_TOKENS = {
    "这笔",
    "那笔",
    "这条",
    "那条",
    "这个",
    "那个",
    "这个账单",
    "那个账单",
    "刚才那笔",
    "刚刚那笔",
    "上一笔",
    "上条",
    "最新",
    "latest",
    "它",
}


def _is_generic_target_item(value: str) -> bool:
    raw = (value or "").strip().lower()
    if not raw:
        return True
    if raw in GENERIC_TARGET_ITEM_TOKENS:
        return True
    if re.fullmatch(r"\d+", raw):
        return True
    return False


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
    runnable = llm.with_structured_output(LedgerPendingSelectionExtraction)
    messages = build_ledger_pending_selection_messages(
        content=content,
        candidates=candidates,
        detected_item=str(pending.get("item") or pending.get("merchant") or "消费"),
        default_category=str(pending.get("category") or "其他"),
        conversation_context=conversation_context,
    )
    parsed = await runnable.ainvoke(messages)
    if isinstance(parsed, LedgerPendingSelectionExtraction):
        return parsed.model_dump()
    if isinstance(parsed, dict):
        return parsed
    return {}


def _render_pending_candidates(candidates: list[float]) -> str:
    lines = ["我识别到多个可能金额："]
    for idx, value in enumerate(candidates, start=1):
        lines.append(f"{idx}. {value:.2f} 元")
    lines.append("请回复要记账的金额（可回复“第1个”或直接“28.50”）。")
    lines.append("支持多选合并记账，例如：`第2个和第3个` 或 `选2和3`。")
    lines.append("可附带分类和摘要，例如：`第1个 餐饮 午饭`。")
    lines.append("不记这张图可回复：`取消`。")
    return "\n".join(lines)


async def _render_pending_candidates_with_llm(candidates: list[float]) -> str:
    return await _render_fixed_reply_with_llm(
        fallback="请基于候选金额生成确认提示。",
        style="pending_amount_candidates",
        facts={"candidates": [round(float(v), 2) for v in candidates[:5]]},
        required_keywords=["取消"],
    )


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


async def _understand_ledger_message(
    content: str,
    conversation_context: str,
    *,
    pending_preview_hint: str = "",
) -> dict:
    llm = get_llm(node_name="ledger_manager")
    runnable = llm.with_structured_output(LedgerIntentExtraction)
    tz_name = get_settings().timezone
    now_local = datetime.now(ZoneInfo(tz_name)).strftime("%Y-%m-%d %H:%M:%S")
    enriched_context = (
        f"{conversation_context}\n\n"
        f"当前时间基准({tz_name}): {now_local}\n"
        "相对时间词（今天/昨天/本月/上月/上周）必须以上述时间基准计算。"
    )
    messages = build_ledger_intent_messages(
        content=content,
        conversation_context=enriched_context,
        pending_preview_hint=pending_preview_hint,
    )
    try:
        parsed = await runnable.ainvoke(messages)
    except Exception:
        return LedgerIntentExtraction().model_dump()
    if isinstance(parsed, LedgerIntentExtraction):
        return parsed.model_dump()
    if isinstance(parsed, dict):
        try:
            return LedgerIntentExtraction.model_validate(parsed).model_dump()
        except Exception:
            return LedgerIntentExtraction().model_dump()
    return LedgerIntentExtraction().model_dump()


async def _extract_update_rewrite_fields(content: str, conversation_context: str) -> dict[str, Any]:
    llm = get_llm(node_name="ledger_manager")
    runnable = llm.with_structured_output(LedgerUpdateRewriteExtraction)
    system = SystemMessage(
        content=(
            "请提取账单操作中的改写式更新意图。"
            "只返回一个 JSON 对象。"
            "只返回结构化字段：target_item, item, category, amount, amount_explicit, confidence。"
            "对于“把A改成B/将A修改为B”这类表达，target_item 必须是 A，item 必须是 B。"
            "对于“把A和B改成C”这类多源改写，target_item 必须保留完整源集合（A 和 B），"
            "item 必须是 C。不要把源集合错误折叠成目标值。"
            "只有当当前用户消息明确给出新的数值金额时，amount_explicit 才设为 true。"
            "如果用户只是修改 item/category 表述，且没有明确设置金额，则 amount_explicit=false 且 amount=null。"
            "如果不确定，保持字段为空，并将 confidence 设低。"
        )
    )
    human = HumanMessage(
        content=(
            f"会话上下文:\n{conversation_context}\n\n"
            f"用户消息:\n{content}"
        )
    )
    parsed = await runnable.ainvoke([system, human])
    if isinstance(parsed, LedgerUpdateRewriteExtraction):
        return parsed.model_dump()
    if isinstance(parsed, dict):
        return parsed
    return {}


async def _understand_preview_control_message(
    *,
    content: str,
    conversation_context: str,
    pending: dict[str, Any],
) -> dict[str, Any]:
    llm = get_llm(node_name="ledger_manager")
    runnable = llm.with_structured_output(LedgerPreviewControlExtraction)
    summary = dict(pending.get("summary") or {})
    candidate_rows = [dict(item) for item in list(pending.get("candidate_rows") or []) if isinstance(item, dict)][:10]
    system = SystemMessage(
        content=(
            "请对已有待提交预览任务下的用户消息进行分类。"
            "只返回一个 JSON 对象。"
            "只返回结构化字段：action, indexes, comparator, threshold, confidence。"
            "action 只能是：confirm, cancel, pick_indexes, refine_amount, new_request, unknown。"
            "pick_indexes 表示用户按展示序号（从 1 开始）选择了部分记录。"
            "refine_amount 表示用户希望按金额比较条件和阈值筛选当前候选项。"
            "只有当用户在当前消息里明确给出具体金额条件时，才使用 refine_amount "
            "（例如：金额>30、大于30、小于等于20、等于30）。"
            "不要从“你怎么把金额改了”或“金额是30不是60”这类抱怨或疑问中推断 refine_amount。"
            "new_request 表示用户发起了另一条完整新请求，不应按待处理控制语义理解。"
        )
    )
    human = HumanMessage(
        content=(
            f"会话上下文:\n{conversation_context}\n\n"
            f"待处理操作: {str(pending.get('operation') or '').strip().lower()}\n"
            f"待处理摘要 JSON:\n{json.dumps(summary, ensure_ascii=False)}\n\n"
            f"待处理候选行 JSON:\n{json.dumps(candidate_rows, ensure_ascii=False)}\n\n"
            f"用户消息:\n{content}"
        )
    )
    parsed = await runnable.ainvoke([system, human])
    if isinstance(parsed, LedgerPreviewControlExtraction):
        return parsed.model_dump()
    if isinstance(parsed, dict):
        return parsed
    return {}


async def _decide_handoff_to_chat_manager(
    *,
    content: str,
    conversation_context: str,
    pending_preview_hint: str = "",
) -> dict[str, Any]:
    llm = get_llm(node_name="ledger_manager")
    runnable = llm.with_structured_output(LedgerHandoffDecision)
    system = SystemMessage(
        content=(
            "你是 ledger_manager 的路由守卫。只返回一个 JSON 对象。"
            "只返回结构化字段：handoff, confidence。"
            "只有当用户消息主要是在进行元信息或泛闲聊对话 "
            "（例如助手身份、产品或项目能力、使用说明、寒暄），"
            "且不是可执行的账单任务时，才设置 handoff=true。"
            "对于可执行的账单意图：insert/query/list/update/delete/confirm/cancel/refine selection，"
            "都应设置 handoff=false。"
            "不要依赖关键词匹配，要基于完整上下文判断语义意图。"
        )
    )
    human = HumanMessage(
        content=(
            f"会话上下文:\n{conversation_context}\n\n"
            f"待处理预览提示:\n{pending_preview_hint or '无'}\n\n"
            f"用户消息:\n{content}"
        )
    )
    parsed = await runnable.ainvoke([system, human])
    if isinstance(parsed, LedgerHandoffDecision):
        return parsed.model_dump()
    if isinstance(parsed, dict):
        return parsed
    return {}


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
    key = (scope or "").strip().lower().replace("-", "_").replace(" ", "_")
    scope_alias = {
        "lastmonth": "last_month",
        "prev_month": "last_month",
        "previous_month": "last_month",
        "month_last": "last_month",
        "上月": "last_month",
        "上个月": "last_month",
        "上個月": "last_month",
        "本月": "month",
        "这个月": "month",
        "這個月": "month",
    }
    key = scope_alias.get(key, key)

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
        label = _format_window_label("浠婂ぉ", today_local, today_local + timedelta(days=1))
        return _to_utc_naive(start_local, tz_name), _to_utc_naive(end_local, tz_name), label
    if key == "last_week":
        week_start = today_local - timedelta(days=today_local.weekday() + 7)
        week_end = week_start + timedelta(days=7)
        start_local = datetime.combine(week_start, datetime.min.time())
        end_local = datetime.combine(week_end, datetime.min.time())
        label = _format_window_label("涓婂懆", week_start, week_end)
        return _to_utc_naive(start_local, tz_name), _to_utc_naive(end_local, tz_name), label
    if key == "week":
        week_start = today_local - timedelta(days=today_local.weekday())
        start_local = datetime.combine(week_start, datetime.min.time())
        end_local = start_local + timedelta(days=7)
        label = _format_window_label("鏈懆", week_start, week_start + timedelta(days=7))
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
    if key == "last_month":
        this_month_start = today_local.replace(day=1)
        if this_month_start.month == 1:
            last_month_start = date(this_month_start.year - 1, 12, 1)
        else:
            last_month_start = date(this_month_start.year, this_month_start.month - 1, 1)
        start_local = datetime.combine(last_month_start, datetime.min.time())
        end_local = datetime.combine(this_month_start, datetime.min.time())
        label = _format_window_label("上月", last_month_start, this_month_start)
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


def _scope_hint_from_content(content: str) -> str | None:
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
    if isinstance(output, str) and _looks_like_tool_error(output):
        raise RuntimeError(output.strip())
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


def _build_preview_pending_payload_from_text2sql(
    *,
    operation: str,
    source: str,
    preview_payload: dict[str, Any],
    update_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    candidate_rows = [
        dict(item)
        for item in list(preview_payload.get("candidate_rows") or [])
        if isinstance(item, dict)
    ][:50]
    target_ids = _parse_int_list(preview_payload.get("target_ids"))[:200]
    summary = dict(preview_payload.get("summary") or {})
    if not summary:
        summary = _build_preview_summary_from_row_dicts(candidate_rows)
    return {
        "pending_type": "preview_commit",
        "operation": operation,
        "source": source,
        "target_ids": target_ids,
        "candidate_rows": candidate_rows,
        "summary": summary,
        "preview_sql": str(preview_payload.get("preview_sql") or ""),
        "preview_params": dict(preview_payload.get("preview_params") or {}),
        "update_fields": dict(update_fields or {}),
        "requires_double_confirm": len(target_ids) > 20,
        "double_confirmed": False,
        "created_at": datetime.utcnow().isoformat(),
    }


def _build_update_preview_diffs(
    rows: list[dict[str, Any]],
    update_fields: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    fields = dict(update_fields or {})
    has_amount = fields.get("amount") is not None
    has_category = bool(str(fields.get("category") or "").strip())
    has_item = bool(str(fields.get("item") or "").strip())
    if not (has_amount or has_category or has_item):
        return []

    result: list[dict[str, Any]] = []
    for idx, row in enumerate(rows[:10], start=1):
        before_amount = float(row.get("amount") or 0)
        before_currency = str(row.get("currency") or "CNY")
        before_category = str(row.get("category") or "")
        before_item = str(row.get("item") or "")

        after_amount = before_amount
        if has_amount:
            try:
                after_amount = float(fields.get("amount"))
            except Exception:
                after_amount = before_amount
        after_category = str(fields.get("category") or "").strip() if has_category else before_category
        after_item = str(fields.get("item") or "").strip() if has_item else before_item

        changes: list[str] = []
        if abs(after_amount - before_amount) > 1e-9:
            changes.append(f"金额：{before_amount:.2f} -> {after_amount:.2f} {before_currency}")
        if after_category != before_category:
            changes.append(f"分类：{before_category or '（空）'} -> {after_category or '（空）'}")
        if after_item != before_item:
            changes.append(f"摘要：{before_item or '（空）'} -> {after_item or '（空）'}")
        if not changes:
            changes.append("无变化")

        result.append(
            {
                "index": idx,
                "datetime": str(row.get("datetime") or ""),
                "changes": changes,
            }
        )
    return result


def _render_preview_confirmation(payload: dict[str, Any]) -> str:
    operation = str(payload.get("operation") or "").strip().lower()
    summary = dict(payload.get("summary") or {})
    rows = [dict(item) for item in list(payload.get("candidate_rows") or []) if isinstance(item, dict)]
    update_fields = dict(payload.get("update_fields") or {})
    count = int(summary.get("count") or 0)
    total = float(summary.get("total") or 0)
    time_start = str(summary.get("time_start") or "").strip()
    time_end = str(summary.get("time_end") or "").strip()
    categories = list(summary.get("categories") or [])
    verb = "删除" if operation == "delete" else "修改"
    lines: list[str] = []
    lines.append(f"将{verb} {count} 条账单。")
    if time_start or time_end:
        lines.append(f"时间范围：{time_start or '?'} ~ {time_end or '?'}")
    lines.append(f"总金额：{total:.2f} CNY")
    if categories:
        cat_text = "，".join([f"{str(name)}({int(num)})" for name, num in categories[:3]])
        lines.append(f"类别分布：{cat_text}")
    lines.append("样例（前10条）：")
    if not rows:
        lines.append("- 无可展示样例")
    else:
        for idx, row in enumerate(rows[:10], start=1):
            lines.append(
                f"{idx}. {row.get('datetime') or ''} | {row.get('item') or ''} | "
                f"{float(row.get('amount') or 0):.2f} {row.get('currency') or 'CNY'} | "
                f"{row.get('category') or ''}"
            )
    if operation == "update":
        diffs = _build_update_preview_diffs(rows, update_fields)
        if diffs:
            lines.append("修改预览（前 -> 后）：")
            for diff in diffs:
                changes_text = "；".join([str(item) for item in list(diff.get("changes") or []) if str(item).strip()])
                lines.append(f"{int(diff.get('index') or 0)}. {diff.get('datetime') or ''} | {changes_text or '无变化'}")
    if operation == "delete":
        lines.append("回复“确认删除”执行；或回复“只删第1、3条”；或回复“金额>30”先缩小范围。")
    else:
        lines.append("回复“确认修改”执行；或回复“只改第1、3条”；或回复“金额>30”先缩小范围。")
    lines.append("回复“取消”可终止本次操作。")
    return "\n".join(lines)


async def _render_preview_confirmation_with_llm(payload: dict[str, Any]) -> str:
    operation = str(payload.get("operation") or "").strip().lower()
    required = ["取消", "确认删除"] if operation == "delete" else ["取消", "确认修改"]
    candidate_rows = [dict(item) for item in list(payload.get("candidate_rows") or []) if isinstance(item, dict)][:10]
    update_fields = dict(payload.get("update_fields") or {})
    diffs = _build_update_preview_diffs(candidate_rows, update_fields) if operation == "update" else []
    return await _render_fixed_reply_with_llm(
        fallback="请基于预览事实生成待确认文案。",
        style="preview_confirmation",
        facts={
            "operation": operation,
            "summary": dict(payload.get("summary") or {}),
            "candidate_rows": candidate_rows,
            "update_fields": update_fields,
            "update_diffs": diffs,
        },
        required_keywords=required,
    )


def _build_preview_summary_from_row_dicts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = round(sum(float(row.get("amount") or 0) for row in rows), 2)
    times = [str(row.get("datetime") or "").strip() for row in rows if str(row.get("datetime") or "").strip()]
    categories: dict[str, int] = {}
    for row in rows:
        key = str(row.get("category") or "其他").strip() or "其他"
        categories[key] = categories.get(key, 0) + 1
    top_categories = sorted(categories.items(), key=lambda x: x[1], reverse=True)[:5]
    return {
        "count": len(rows),
        "total": total,
        "time_start": times[-1] if times else "",
        "time_end": times[0] if times else "",
        "categories": top_categories,
    }


def _refine_preview_rows_by_rule(
    rows: list[dict[str, Any]],
    comparator: str,
    threshold: float | None,
) -> tuple[list[dict[str, Any]] | None, str]:
    op = str(comparator or "").strip()
    if op not in {">", ">=", "<", "<=", "="} or threshold is None:
        return None, ""
    try:
        bound = float(threshold)
    except Exception:
        return None, ""

    def _ok(value: float) -> bool:
        if op == ">":
            return value > bound
        if op == ">=":
            return value >= bound
        if op == "<":
            return value < bound
        if op == "<=":
            return value <= bound
        return abs(value - bound) < 1e-9

    filtered = [row for row in rows if _ok(float(row.get("amount") or 0))]
    return filtered, f"金额{op}{bound:.2f}"


async def _execute_preview_commit(
    *,
    operation: str,
    user_id: int,
    user_platform: str,
    conversation_id: int | None,
    ledger_ids: list[int],
    update_fields: dict[str, Any] | None = None,
) -> str:
    picked_ids = _parse_int_list(ledger_ids)[:200]
    expected = len(picked_ids)
    if expected <= 0:
        return "没有可执行的账单目标，请先重新筛选。"
    fields = dict(update_fields or {})
    if operation == "update":
        amount_raw = fields.get("amount")
        try:
            amount = float(amount_raw) if amount_raw is not None else None
        except Exception:
            amount = None
        if amount is not None:
            fields["amount"] = amount
        has_amount = fields.get("amount") is not None
        has_category = bool(str(fields.get("category") or "").strip())
        has_item = bool(str(fields.get("item") or "").strip())
        if not (has_amount or has_category or has_item):
            return "缺少可修改字段（金额/分类/摘要），无法执行。请重新发起更正请求。"

    result = await _tracked_text2sql_commit_write_by_ids(
        user_id=user_id,
        operation=operation,
        target_ids=picked_ids,
        expected_count=expected,
        update_fields=fields,
        platform=user_platform,
        conversation_id=conversation_id,
    )
    if not bool(result.get("ok")):
        error = str(result.get("error") or "").strip()
        if error == "count_mismatch":
            exp = int(result.get("expected") or expected)
            act = int(result.get("actual") or 0)
            return f"执行被拦截：目标集合已变化（确认 {exp} 条，当前 {act} 条）。请重新预览后再确认。"
        if error == "rowcount_guard_triggered":
            exp = int(result.get("expected") or expected)
            act = int(result.get("actual") or 0)
            return f"执行被拦截：实际影响条数异常（确认 {exp} 条，执行 {act} 条）。请重新预览。"
        if error in {"missing_update_fields", "missing_amount_for_update"}:
            return "缺少可修改字段（金额/分类/摘要），无法执行。请重新发起更正请求。"
        return "执行失败，请稍后重试。"

    matched = int(result.get("matched") or 0)
    affected = int(result.get("affected") or 0)
    if affected <= 0:
        return "没有实际变更，可能目标已被修改。请重新预览。"
    if operation == "delete":
        return f"已确认删除：目标 {expected} 条，实际删除 {affected} 条（匹配 {matched} 条）。"
    return f"已确认修改：目标 {expected} 条，实际修改 {affected} 条（匹配 {matched} 条）。"


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
            "你是账单查询助手。只能依据提供的账单行回答。"
            "回答要简洁，不要提及 JSON、schema 或内部实现细节。"
        )
    )
    human = HumanMessage(
        content=(
            f"查询标签: {label}\n"
            f"分类筛选: {category or '无'}\n\n"
            f"会话上下文:\n{conversation_context}\n\n"
            f"用户问题:\n{content}\n\n"
            f"账单 JSON:\n{json.dumps(payload, ensure_ascii=False)}"
        )
    )
    response = await llm.ainvoke([system, human])
    return _sanitize_llm_text(str(response.content or ""))


async def _generate_ledger_clarification(
    *,
    content: str,
    conversation_context: str,
    reason: str,
) -> str:
    llm = get_llm(node_name="ledger_manager")
    runnable = llm.with_structured_output(LedgerClarificationExtraction)
    system = SystemMessage(
        content=(
            "你是账单意图澄清助手。请仅返回结构化字段 question。\n"
            "只输出一个 JSON 对象，不要输出解释文本。\n"
            "要求：问题简洁、可执行、面向下一步操作。"
        )
    )
    human = HumanMessage(
        content=(
            f"用户消息:\n{content}\n\n"
            f"会话上下文:\n{conversation_context}\n\n"
            f"原因:\n{reason}"
        )
    )
    try:
        parsed = await runnable.ainvoke([system, human])
        question = _sanitize_llm_text(str(getattr(parsed, "question", "") or ""))
        if question:
            return question
    except Exception:
        pass
    return "请确认你要执行哪类账单操作：记一笔、查询、修改，还是删除？"


def _render_ledger_metric_answer(
    *,
    content: str,
    label: str,
    category: str | None,
    rows: list[Ledger],
) -> str | None:
    return None

def _build_pending_preview_hint(pending: dict[str, Any]) -> str:
    operation = str(pending.get("operation") or "").strip().lower()
    verb = "删除" if operation == "delete" else "修改"
    summary = dict(pending.get("summary") or {})
    count = int(summary.get("count") or 0)
    total = float(summary.get("total") or 0)
    time_start = str(summary.get("time_start") or "").strip()
    time_end = str(summary.get("time_end") or "").strip()
    rows = [dict(item) for item in list(pending.get("candidate_rows") or []) if isinstance(item, dict)]
    samples = ", ".join(
        f"{str(row.get('item') or '').strip()}({float(row.get('amount') or 0):.2f})"
        for row in rows[:3]
    )
    return (
        f"当前有待确认{verb}任务：{count}条，总额{total:.2f} CNY，"
        f"时间范围 {time_start or '?'} ~ {time_end or '?'}，"
        f"样例: {samples or '无'}。"
    )


def _is_actionable_new_intent(intent: str) -> bool:
    return intent in {
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
    }


async def _handle_pending_preview_commit(
    *,
    state: GraphState,
    content: str,
    pending: dict[str, Any],
    user_id: int,
    user_platform: str,
    conversation_id: int,
    conversation_context: str,
    parsed_control: dict[str, Any] | None = None,
) -> GraphState:
    async def _resp(
        text: str,
        *,
        style: str = "status",
        facts: dict[str, Any] | None = None,
        required_keywords: list[str] | None = None,
    ) -> GraphState:
        rendered = await _render_fixed_reply_with_llm(
            fallback=text,
            style=style,
            facts=facts or {},
            required_keywords=required_keywords or [],
        )
        return {**state, "responses": [rendered]}

    operation = str(pending.get("operation") or "").strip().lower()
    if operation not in {"delete", "update"}:
        await clear_pending_ledger(user_id=user_id, conversation_id=conversation_id)
        return await _resp("待确认任务已失效，请重新发起删除/修改请求。", style="error")

    target_ids = _parse_int_list(pending.get("target_ids"))
    candidate_rows = [dict(item) for item in list(pending.get("candidate_rows") or []) if isinstance(item, dict)]
    if not target_ids:
        await clear_pending_ledger(user_id=user_id, conversation_id=conversation_id)
        return await _resp("待确认任务中没有可执行目标，请重新发起请求。", style="error")

    control = dict(parsed_control or {})
    if not control:
        try:
            control = await _understand_preview_control_message(
                content=content,
                conversation_context=conversation_context,
                pending=pending,
            )
        except Exception:
            control = {}
    action = str(control.get("action") or "").strip().lower()

    if action == "cancel":
        await clear_pending_ledger(user_id=user_id, conversation_id=conversation_id)
        return await _resp("已取消本次批量操作。", style="status")

    if action == "refine_amount" and candidate_rows:
        refine_rows, refine_desc = _refine_preview_rows_by_rule(
            candidate_rows,
            str(control.get("comparator") or "").strip(),
            control.get("threshold"),
        )
        if refine_rows is None:
            return await _resp("未能理解缩小范围条件，请换一种说法。", style="hint")
        if not refine_rows:
            return await _resp("按该金额条件没有匹配账单，请调整条件后重试。", style="hint")
        refined_id_set = {int(item.get("id") or 0) for item in refine_rows if int(item.get("id") or 0) > 0}
        refined_target_ids = [lid for lid in target_ids if lid in refined_id_set][:200]
        if not refined_target_ids:
            return await _resp("缩小范围后没有可执行目标，请调整条件后重试。", style="hint")
        next_payload = dict(pending)
        next_payload["target_ids"] = refined_target_ids
        next_payload["candidate_rows"] = refine_rows[:50]
        next_payload["summary"] = _build_preview_summary_from_row_dicts(next_payload["candidate_rows"])
        next_payload["requires_double_confirm"] = len(refined_target_ids) > 20
        next_payload["double_confirmed"] = False
        await set_pending_ledger(
            user_id=user_id,
            conversation_id=conversation_id,
            payload=next_payload,
        )
        preview_text = await _render_preview_confirmation_with_llm(next_payload)
        merged = f"已按“{refine_desc}”缩小范围。\n{preview_text}"
        rendered = await _render_fixed_reply_with_llm(
            fallback=merged,
            style="preview_refine",
            facts={"refine": refine_desc},
            required_keywords=["缩小范围"],
        )
        return {**state, "responses": [rendered]}

    if action == "pick_indexes" and candidate_rows:
        raw_indexes = control.get("indexes")
        if not isinstance(raw_indexes, list):
            return await _resp("没有解析到可执行的样例序号，请重试。", style="hint")
        picked_ids: list[int] = []
        seen: set[int] = set()
        for value in raw_indexes:
            try:
                idx = int(value)
            except Exception:
                continue
            if 1 <= idx <= len(candidate_rows):
                lid = int(candidate_rows[idx - 1].get("id") or 0)
                if lid > 0 and lid in target_ids and lid not in seen:
                    seen.add(lid)
                    picked_ids.append(lid)
        if not picked_ids:
            return await _resp("没有解析到可执行的样例序号，请重试。", style="hint")
        response = await _execute_preview_commit(
            operation=operation,
            user_id=user_id,
            user_platform=user_platform,
            conversation_id=conversation_id,
            ledger_ids=picked_ids,
            update_fields=dict(pending.get("update_fields") or {}),
        )
        response = await _render_fixed_reply_with_llm(
            fallback=response,
            style="commit_result",
            facts={"operation": operation},
        )
        await clear_pending_ledger(user_id=user_id, conversation_id=conversation_id)
        return {**state, "responses": [response]}

    if action == "confirm":
        requires_double = bool(pending.get("requires_double_confirm"))
        double_confirmed = bool(pending.get("double_confirmed"))
        if requires_double and not double_confirmed:
            next_payload = dict(pending)
            next_payload["double_confirmed"] = True
            await set_pending_ledger(
                user_id=user_id,
                conversation_id=conversation_id,
                payload=next_payload,
            )
            count = int(dict(next_payload.get("summary") or {}).get("count") or len(target_ids))
            verb = "删除" if operation == "delete" else "修改"
            tip = f"该操作将影响 {count} 条账单。请再次回复“确认{verb}”以继续，或回复“取消”。"
            rendered_tip = await _render_fixed_reply_with_llm(
                fallback=tip,
                style="double_confirm",
                facts={"count": count, "operation": operation},
                required_keywords=[f"确认{verb}", "取消"],
            )
            return {
                **state,
                "responses": [rendered_tip],
            }
        response = await _execute_preview_commit(
            operation=operation,
            user_id=user_id,
            user_platform=user_platform,
            conversation_id=conversation_id,
            ledger_ids=target_ids,
            update_fields=dict(pending.get("update_fields") or {}),
        )
        response = await _render_fixed_reply_with_llm(
            fallback=response,
            style="commit_result",
            facts={"operation": operation},
        )
        await clear_pending_ledger(user_id=user_id, conversation_id=conversation_id)
        return {**state, "responses": [response]}

    return {
        **state,
        "responses": [await _render_preview_confirmation_with_llm(pending)],
    }

async def ledger_manager_node(state: GraphState) -> GraphState:
    message = state["message"]
    session = get_session()
    user = await session.get(User, state["user_id"])
    if not user:
        rendered = await _render_fixed_reply_with_llm(
            fallback="未找到用户信息。",
            style="error",
            facts={},
        )
        return {**state, "responses": [rendered]}

    user_id = user.id
    user_platform = user.platform
    conversation_id = int(state.get("conversation_id") or 0)
    content = (message.content or "").strip()
    context_text = render_conversation_context(state)
    prefetched_parsed_intent: dict[str, Any] | None = None

    async def _resp(
        text: str,
        *,
        style: str = "status",
        facts: dict[str, Any] | None = None,
        required_keywords: list[str] | None = None,
    ) -> GraphState:
        rendered = await _render_fixed_reply_with_llm(
            fallback=text,
            style=style,
            facts=facts or {},
            required_keywords=required_keywords or [],
        )
        return {**state, "responses": [rendered]}

    if not message.image_urls and conversation_id > 0:
        pending = await get_pending_ledger(user_id=user_id, conversation_id=conversation_id)
        if pending:
            pending_type = str(pending.get("pending_type") or "receipt_amount").strip().lower()
            if pending_type == "preview_commit":
                try:
                    control = await _understand_preview_control_message(
                        content=content,
                        conversation_context=context_text,
                        pending=pending,
                    )
                except Exception:
                    control = {}
                control_action = str(control.get("action") or "").strip().lower()
                pending_hint = _build_pending_preview_hint(pending)
                if control_action == "new_request":
                    try:
                        maybe_new = await _understand_ledger_message(
                            content,
                            context_text,
                            pending_preview_hint=pending_hint,
                        )
                    except Exception:
                        maybe_new = {}
                    maybe_intent = str(maybe_new.get("intent") or "").strip().lower()
                    if _is_actionable_new_intent(maybe_intent):
                        await clear_pending_ledger(user_id=user_id, conversation_id=conversation_id)
                        prefetched_parsed_intent = maybe_new
                    else:
                        return await _handle_pending_preview_commit(
                            state=state,
                            content=content,
                            pending=pending,
                            user_id=user_id,
                            user_platform=user_platform,
                            conversation_id=conversation_id,
                            conversation_context=context_text,
                            parsed_control=control,
                        )
                else:
                    return await _handle_pending_preview_commit(
                        state=state,
                        content=content,
                        pending=pending,
                        user_id=user_id,
                        user_platform=user_platform,
                        conversation_id=conversation_id,
                        conversation_context=context_text,
                        parsed_control=control,
                    )

            if pending_type == "preview_commit":
                # Interrupted by a new actionable request; continue with normal intent flow.
                continue_pending_amount = False
            else:
                continue_pending_amount = True

            if continue_pending_amount:
                candidates = _pending_amount_candidates(pending)
                amount: float | None = None
                llm_category: str | None = None
                llm_item: str | None = None
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
                    return await _resp("已取消本次图片记账。", style="status")
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
                    amount = _extract_plain_amount(content)

                if amount is None:
                    if candidates:
                        candidate_text = await _render_pending_candidates_with_llm(candidates)
                        return {**state, "responses": [candidate_text]}
                    return await _resp("请直接回复要记账的金额和分类，例如：`28 餐饮 午饭`。", style="hint")

                category = llm_category or _normalize_category(str(pending.get("category") or "其他"))
                item = llm_item or str(pending.get("item") or pending.get("merchant") or "消费")
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
                    return await _resp("记账失败，请稍后重试。", style="error")
                await clear_pending_ledger(user_id=user_id, conversation_id=conversation_id)
                done_text = await _render_fixed_reply_with_llm(
                    fallback=(
                        f"已记账：{ledger.item} {ledger.amount:.2f} {ledger.currency}，分类 {ledger.category}，时间 {_fmt_dt(ledger.transaction_date)}。"
                    ),
                    style="insert_result",
                    facts={
                        "item": ledger.item,
                        "amount": float(ledger.amount),
                        "currency": ledger.currency,
                        "category": ledger.category,
                        "time": _fmt_dt(ledger.transaction_date),
                    },
                )
                return {
                    **state,
                    "responses": [done_text],
                }

    if message.image_urls:
        image_inputs = [item for item in message.image_urls if isinstance(item, str) and item.strip()][:MAX_IMAGES]
        if image_inputs:
            result = await _tracked_analyze_receipt(
                image_inputs[0],
                user_id=user_id,
                platform=user_platform,
                conversation_id=conversation_id,
            )
            parsed_single = _parse_vision_result(result)
            image_type = str(parsed_single.get("image_type") or "other")
            confidence = float(parsed_single.get("confidence") or 0.0)
            amount = parsed_single.get("amount")
            candidate_values = list(parsed_single.get("candidate_values") or [])

            if amount is not None and float(amount) > 0:
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
                    image_url=image_inputs[0],
                )
                if ledger is None:
                    return await _resp("记账失败，请稍后重试。", style="error")
                source_hint = "（支付截图识别）" if image_type == "payment_screenshot" else ""
                done_text = await _render_fixed_reply_with_llm(
                    fallback=(
                        f"已记账{source_hint}：{ledger.item} {ledger.amount:.2f} {ledger.currency}，分类 {ledger.category}，时间 {_fmt_dt(ledger.transaction_date)}。"
                    ),
                    style="insert_result",
                    facts={
                        "source_hint": source_hint,
                        "item": ledger.item,
                        "amount": float(ledger.amount),
                        "currency": ledger.currency,
                        "category": ledger.category,
                        "time": _fmt_dt(ledger.transaction_date),
                    },
                )
                return {
                    **state,
                    "responses": [done_text],
                }

            if candidate_values and conversation_id > 0:
                await set_pending_ledger(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    payload={
                        "pending_type": "receipt_amount",
                        "image_url": image_inputs[0],
                        "image_type": image_type,
                        "amount_candidates": candidate_values[:5],
                        "category": str(parsed_single.get("category") or "其他"),
                        "item": str(parsed_single.get("item") or "消费"),
                        "merchant": str(parsed_single.get("merchant") or ""),
                        "confidence": confidence,
                    },
                )
                candidate_text = await _render_pending_candidates_with_llm(candidate_values[:5])
                return {**state, "responses": [candidate_text]}

            return {
                **state,
                "responses": [await _render_fixed_reply_with_llm(
                    fallback="我没能准确识别这张图片，请告诉我金额和分类。",
                    style="hint",
                    facts={},
                )],
            }

    parsed: dict = dict(prefetched_parsed_intent or {})
    if not parsed:
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
    if intent == "unknown":
        try:
            handoff = await _decide_handoff_to_chat_manager(
                content=content,
                conversation_context=context_text,
                pending_preview_hint="",
            )
        except Exception:
            handoff = {}
        handoff_conf = float(handoff.get("confidence") or 0.0)
        if bool(handoff.get("handoff")):
            extra = dict(state.get("extra") or {})
            extra["ledger_handoff"] = {
                "target": "chat_manager",
                "confidence": handoff_conf,
                "at": datetime.utcnow().isoformat(),
            }
            return {**state, "intent": "chat_manager", "extra": extra}

    async def _resolve_query_rows() -> tuple[list[Ledger], str, str, str | None]:
        scope = str(parsed.get("query_scope") or "").strip().lower()
        hint_scope = _scope_hint_from_content(content)
        if hint_scope:
            scope = hint_scope
        query_date = str(parsed.get("query_date") or "").strip() or None
        llm_window = _resolve_ledger_window_from_fields(scope, query_date)
        category_hint = _resolve_query_category_hint(content, str(parsed.get("category") or ""))
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
        metric_reply = _render_ledger_metric_answer(
            content=content,
            label=label,
            category=category_hint,
            rows=rows,
        )
        if metric_reply:
            return metric_reply
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
        return await _render_fixed_reply_with_llm(
            fallback="",
            style="query_answer",
            facts={
                "label": label,
                "category": category_hint or "",
                "count": len(rows),
                "rows": _build_ledger_payload(rows),
            },
        )

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
        category = ""

    target_item = _clean_item(str(parsed.get("target_item") or ""))
    if intent == "insert":
        if not item:
            item = _clean_item(content)
        if not target_item:
            target_item = item
    else:
        if not target_item:
            target_item = parsed_item or _clean_item(content)
    if intent.startswith("correct_"):
        try:
            rewrite = await _extract_update_rewrite_fields(content, context_text)
        except Exception:
            rewrite = {}
        rewrite_target = _clean_item(str(rewrite.get("target_item") or ""))
        rewrite_item = _clean_item(str(rewrite.get("item") or ""))
        rewrite_category = str(rewrite.get("category") or "").strip()
        rewrite_amount_explicit = bool(rewrite.get("amount_explicit"))
        has_rewrite_signal = bool(
            rewrite_target or rewrite_item or rewrite_category or rewrite_amount_explicit
        )
        if has_rewrite_signal:
            if rewrite_target:
                target_item = rewrite_target
            if rewrite_item:
                item = rewrite_item
            if rewrite_category:
                normalized_rewrite_category = _normalize_category(rewrite_category)
                if normalized_rewrite_category and normalized_rewrite_category != _normalize_category(""):
                    category = normalized_rewrite_category
            if rewrite_amount_explicit:
                rewrite_amount_raw = rewrite.get("amount")
                try:
                    rewrite_amount = float(rewrite_amount_raw) if rewrite_amount_raw is not None else None
                except Exception:
                    rewrite_amount = None
                amount = rewrite_amount
            else:
                amount = None
        if item and target_item and item == target_item:
            item = ""

    reference_mode = str(parsed.get("reference_mode") or "auto").strip().lower()
    selection_mode = str(parsed.get("selection_mode") or "auto").strip().lower()

    has_named_target = bool(target_item) and not _is_generic_target_item(target_item)
    if intent == "correct_latest" and has_named_target:
        intent = "correct_by_name"
    elif intent == "delete_latest" and has_named_target:
        intent = "delete_by_name"

    if intent in {"correct_by_name", "delete_by_name"}:
        if reference_mode in {"auto", "latest"}:
            reference_mode = "by_name"
        if selection_mode == "auto":
            selection_mode = "all"

    if intent == "unknown":
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
            rendered_sql = await _render_fixed_reply_with_llm(
                fallback=sql_response,
                style="sql_result",
                facts={"raw": sql_response},
            )
            return {**state, "responses": [rendered_sql]}

        clarify_question = await _generate_ledger_clarification(
            content=content,
            conversation_context=context_text,
            reason="intent_unknown",
        )
        return {
            **state,
            "responses": [
                await _render_fixed_reply_with_llm(
                    fallback=clarify_question,
                    style="hint",
                    facts={"reason": "intent_unknown"},
                )
            ],
        }

    if intent == "list":
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
            rendered_sql = await _render_fixed_reply_with_llm(
                fallback=sql_response,
                style="sql_result",
                facts={"raw": sql_response},
            )
            return {**state, "responses": [rendered_sql]}
        try:
            rows, label, scope_key, category_hint = await _resolve_query_rows()
        except RuntimeError as exc:
            return await _resp(f"账单查询失败：{str(exc)}。请稍后重试。", style="error")
        reply = await answer_ledger_query(rows, label, category_hint)
        rendered_reply = await _render_fixed_reply_with_llm(
            fallback=reply,
            style="query_answer",
            facts={"label": label, "category": category_hint or "", "count": len(rows)},
        )
        next_state = _with_last_ledger_query(
            state,
            rows=rows,
            label=label,
            scope=scope_key,
            category=category_hint,
        )
        return {**next_state, "responses": [rendered_reply]}

    if intent == "query":
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
            rendered_sql = await _render_fixed_reply_with_llm(
                fallback=sql_response,
                style="sql_result",
                facts={"raw": sql_response},
            )
            return {**state, "responses": [rendered_sql]}
        try:
            rows, label, scope_key, category_hint = await _resolve_query_rows()
        except RuntimeError as exc:
            return await _resp(f"账单查询失败：{str(exc)}。请稍后重试。", style="error")
        reply = await answer_ledger_query(rows, label, category_hint)
        rendered_reply = await _render_fixed_reply_with_llm(
            fallback=reply,
            style="query_answer",
            facts={"label": label, "category": category_hint or "", "count": len(rows)},
        )
        next_state = _with_last_ledger_query(
            state,
            rows=rows,
            label=label,
            scope=scope_key,
            category=category_hint,
        )
        return {**next_state, "responses": [rendered_reply]}

    if intent in {"delete_by_name", "delete_by_id", "delete_latest", "delete_by_scope"}:
        preview_data = await _tracked_text2sql_preview_write(
            user_id=user_id,
            message=content,
            operation="delete",
            update_fields={},
            preview_hints={
                "intent": intent,
                "target_ids": target_ids,
                "target_item": target_item,
                "query_scope": str(parsed.get("query_scope") or ""),
                "query_date": str(parsed.get("query_date") or ""),
                "category": str(parsed.get("category") or ""),
                "reference_mode": reference_mode,
                "selection_mode": selection_mode,
            },
            conversation_context=context_text,
            platform=user_platform,
            conversation_id=conversation_id,
        )
        if not preview_data:
            return await _resp("无法生成删除预览，请补充更明确的筛选条件。", style="hint")
        if not bool(preview_data.get("ok", True)):
            return await _resp(
                f"删除预览失败：{str(preview_data.get('error') or 'unknown')}。请调整条件后重试。",
                style="error",
            )
        target_ids = _parse_int_list(preview_data.get("target_ids"))
        if not target_ids:
            return await _resp("没有匹配到可删除的账单。", style="status")
        if len(target_ids) > 200:
            return await _resp(
                f"本次命中 {len(target_ids)} 条，超过单次可确认上限 200。请先缩小范围后重试。",
                style="hint",
            )
        preview_payload = _build_preview_pending_payload_from_text2sql(
            operation="delete",
            source="text2sql_preview",
            preview_payload=preview_data,
            update_fields={},
        )
        if conversation_id > 0:
            await set_pending_ledger(
                user_id=user_id,
                conversation_id=conversation_id,
                payload=preview_payload,
            )
            preview_text = await _render_preview_confirmation_with_llm(preview_payload)
            return {**state, "responses": [preview_text]}
        response = await _execute_preview_commit(
            operation="delete",
            user_id=user_id,
            user_platform=user_platform,
            conversation_id=conversation_id,
            ledger_ids=target_ids,
            update_fields={},
        )
        response = await _render_fixed_reply_with_llm(
            fallback=response,
            style="commit_result",
            facts={"operation": "delete"},
        )
        return {**state, "responses": [response]}

    if intent in {"correct_by_name", "correct_by_id", "correct_latest", "correct_by_scope"}:
        update_fields = {
            "amount": amount,
            "category": (category if category and category != "其他" else ""),
            "item": (item or ""),
        }
        has_amount = update_fields["amount"] is not None
        has_category = bool(update_fields["category"])
        has_item = bool(update_fields["item"])
        if not (has_amount or has_category or has_item):
            return await _resp("请告诉我要修改成什么（金额/分类/摘要）。例如：把晚饭改成早饭。", style="hint")
        preview_data = await _tracked_text2sql_preview_write(
            user_id=user_id,
            message=content,
            operation="update",
            update_fields=update_fields,
            preview_hints={
                "intent": intent,
                "target_ids": target_ids,
                "target_item": target_item,
                "query_scope": str(parsed.get("query_scope") or ""),
                "query_date": str(parsed.get("query_date") or ""),
                "category": str(parsed.get("category") or ""),
                "reference_mode": reference_mode,
                "selection_mode": selection_mode,
            },
            conversation_context=context_text,
            platform=user_platform,
            conversation_id=conversation_id,
        )
        if not preview_data:
            return await _resp("无法生成修改预览，请补充更明确的筛选条件。", style="hint")
        if not bool(preview_data.get("ok", True)):
            return await _resp(
                f"修改预览失败：{str(preview_data.get('error') or 'unknown')}。请调整条件后重试。",
                style="error",
            )
        target_ids = _parse_int_list(preview_data.get("target_ids"))
        if not target_ids:
            return await _resp("没有匹配到可修改的账单。", style="status")
        if len(target_ids) > 200:
            return await _resp(
                f"本次命中 {len(target_ids)} 条，超过单次可确认上限 200。请先缩小范围后重试。",
                style="hint",
            )
        preview_payload = _build_preview_pending_payload_from_text2sql(
            operation="update",
            source="text2sql_preview",
            preview_payload=preview_data,
            update_fields=update_fields,
        )
        if conversation_id > 0:
            await set_pending_ledger(
                user_id=user_id,
                conversation_id=conversation_id,
                payload=preview_payload,
            )
            preview_text = await _render_preview_confirmation_with_llm(preview_payload)
            return {**state, "responses": [preview_text]}
        response = await _execute_preview_commit(
            operation="update",
            user_id=user_id,
            user_platform=user_platform,
            conversation_id=conversation_id,
            ledger_ids=target_ids,
            update_fields=dict(preview_payload.get("update_fields") or {}),
        )
        response = await _render_fixed_reply_with_llm(
            fallback=response,
            style="commit_result",
            facts={"operation": "update"},
        )
        return {**state, "responses": [response]}

    if amount is None:
        return await _resp("请告诉我金额，例如：今天晚饭 28 元。", style="hint")

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
        return await _resp("记账失败，请稍后重试。", style="error")
    done_text = await _render_fixed_reply_with_llm(
        fallback=f"已记账：{ledger.item} {ledger.amount} {ledger.currency}，分类 {ledger.category}，时间 {_fmt_dt(ledger.transaction_date)}。",
        style="insert_result",
        facts={
            "item": ledger.item,
            "amount": float(ledger.amount),
            "currency": ledger.currency,
            "category": ledger.category,
            "time": _fmt_dt(ledger.transaction_date),
        },
    )
    return {
        **state,
        "responses": [done_text],
    }
