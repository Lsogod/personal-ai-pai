from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.memory import LongTermMemory
from app.services.llm import get_llm

VALID_MEMORY_TYPES = {"profile", "preference", "fact", "goal", "project", "constraint"}
CJK_RUN_PATTERN = re.compile(r"[\u4e00-\u9fff]+")
ASCII_TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9_]{2,}")
IDENTITY_MEMORY_KEYS = {
    "preferred_name",
    "nickname",
    "ai_name",
    "ai_emoji",
    "assistant_name",
}
MEMORY_CONSOLIDATE_SCAN_LIMIT = 160
SEMANTIC_DUPLICATE_THRESHOLD = 0.82
SEMANTIC_MERGE_STRICT_THRESHOLD = 0.9

logger = logging.getLogger(__name__)


def _to_client_tz_iso(value: datetime | None) -> str:
    if value is None:
        return ""
    tz = ZoneInfo(get_settings().timezone)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(tz).isoformat(timespec="seconds")


class MemoryCandidateExtraction(BaseModel):
    op: str = Field(default="save")
    memory_type: str = Field(default="fact")
    key: str = Field(default="")
    content: str = Field(default="")
    importance: int | None = Field(default=None)
    confidence: float | None = Field(default=None)
    ttl_days: int | None = Field(default=None)


class MemoryExtractionResult(BaseModel):
    memories: list[MemoryCandidateExtraction] = Field(default_factory=list)


class MemoryRefineDecision(BaseModel):
    index: int = Field(default=-1)
    keep: bool = Field(default=False)
    op: str = Field(default="save")
    merge_target_id: int | None = Field(default=None)
    memory_type: str = Field(default="fact")
    key: str = Field(default="")
    content: str = Field(default="")
    importance: int | None = Field(default=None)
    confidence: float | None = Field(default=None)
    ttl_days: int | None = Field(default=None)


class MemoryRefineResult(BaseModel):
    decisions: list[MemoryRefineDecision] = Field(default_factory=list)


class MemoryConsolidationDecision(BaseModel):
    id: int = Field(default=-1)
    keep: bool = Field(default=False)
    merge_into_id: int | None = Field(default=None)
    memory_type: str = Field(default="fact")
    content: str = Field(default="")
    importance: int | None = Field(default=None)
    confidence: float | None = Field(default=None)
    ttl_days: int | None = Field(default=None)


class MemoryConsolidationResult(BaseModel):
    decisions: list[MemoryConsolidationDecision] = Field(default_factory=list)


class MemoryKeyNormalizationResult(BaseModel):
    key: str = Field(default="")


async def _invoke_structured(
    *,
    schema: type[BaseModel],
    messages: list[SystemMessage | HumanMessage],
) -> BaseModel:
    llm = get_llm(node_name="memory")
    runnable = llm.with_structured_output(schema)
    result = await runnable.ainvoke(messages)
    if isinstance(result, BaseModel):
        return result
    if isinstance(result, dict):
        return schema.model_validate(result)
    return schema()


def _fallback_refined_memory_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    settings = get_settings()
    refined: list[dict[str, Any]] = []
    min_confidence = float(settings.long_term_memory_min_confidence)
    for raw in candidates:
        if not isinstance(raw, dict):
            continue
        op = str(raw.get("op") or "save").strip().lower()
        if op != "save":
            continue
        content = str(raw.get("content") or "").strip()
        if not content:
            continue
        confidence_raw = raw.get("confidence")
        if confidence_raw is None or str(confidence_raw).strip() == "":
            confidence = max(min_confidence, 0.8)
        else:
            try:
                confidence = float(confidence_raw)
            except Exception:
                confidence = max(min_confidence, 0.8)
        confidence = max(0.0, min(1.0, confidence))
        if confidence < min_confidence:
            continue
        refined.append(
            {
                "op": "save",
                "merge_target_id": raw.get("merge_target_id"),
                "memory_type": _normalize_memory_type(str(raw.get("memory_type") or "fact")),
                "key": str(raw.get("key") or "").strip(),
                "content": content,
                "importance": raw.get("importance"),
                "confidence": confidence,
                "ttl_days": raw.get("ttl_days"),
            }
        )
    return refined


async def _infer_memory_key_via_llm(
    *,
    memory_type: str,
    content: str,
    existing_rows: list[LongTermMemory],
) -> str:
    text = (content or "").strip()
    if not text:
        return ""
    existing_keys: list[str] = []
    for row in existing_rows:
        key = str(getattr(row, "memory_key", "") or "").strip()
        if key and key not in existing_keys:
            existing_keys.append(key)
    system = SystemMessage(
        content=(
            "你是记忆槽位命名器。请仅返回 schema 定义字段 key。"
            "输出稳定、可复用的属性键，不要包含用户私有文本。"
            "格式要求：小写、点分路径或下划线，例如 profile.birthday、profile.residence_city、preference.sport。"
            "如果与已有键语义相同，必须复用已有键。"
            "不要输出解释文本。"
        )
    )
    human = HumanMessage(
        content=json.dumps(
            {
                "memory_type": str(memory_type or "fact"),
                "content": text,
                "existing_keys": existing_keys[:120],
            },
            ensure_ascii=False,
        )
    )
    try:
        parsed = await _invoke_structured(
            schema=MemoryKeyNormalizationResult,
            messages=[system, human],
        )
        return _normalize_key(str(getattr(parsed, "key", "") or ""))
    except Exception:
        return ""


def _is_reserved_identity_memory_type(memory_type: str) -> bool:
    # Identity/profile fields must stay in User profile as the single source of truth.
    return (memory_type or "").strip().lower() == "profile"


def _normalize_memory_type(value: str) -> str:
    raw = (value or "").strip().lower()
    if raw in VALID_MEMORY_TYPES:
        return raw
    return "fact"


def _normalize_key(value: str) -> str:
    key = re.sub(r"\s+", "-", (value or "").strip().lower())
    key = re.sub(r"[^a-z0-9_\-\u4e00-\u9fff:]+", "-", key)
    key = re.sub(r"-{2,}", "-", key).strip("-")
    return key[:160]


def _is_identity_memory_candidate(
    *,
    memory_type: str,
    memory_key: str,
    content: str,
    user_nickname: str = "",
    user_ai_name: str = "",
    user_ai_emoji: str = "",
) -> bool:
    key_tail = (memory_key.split(":", 1)[-1] if memory_key else "").strip().lower()
    if key_tail in IDENTITY_MEMORY_KEYS:
        return True
    if memory_type == "profile":
        return True
    c = (content or "").strip()
    if not c:
        return False
    nick = (user_nickname or "").strip()
    ai_name = (user_ai_name or "").strip()
    ai_emoji = (user_ai_emoji or "").strip()
    if nick and nick in c:
        return True
    if ai_name and ai_name in c:
        return True
    if ai_emoji and ai_emoji in c:
        return True
    return False


def _build_memory_key(memory_type: str, content: str) -> str:
    digest = hashlib.sha1(f"{memory_type}:{content}".encode("utf-8")).hexdigest()[:16]
    return f"{memory_type}:{digest}"


def _tokenize(text: str) -> set[str]:
    raw = (text or "").strip().lower()
    if not raw:
        return set()

    tokens: set[str] = {token for token in ASCII_TOKEN_PATTERN.findall(raw)}
    for run in CJK_RUN_PATTERN.findall(raw):
        run = run.strip()
        if not run:
            continue
        if len(run) <= 2:
            tokens.add(run)
            continue
        for i in range(len(run) - 1):
            tokens.add(run[i : i + 2])
    return tokens


def _semantic_similarity(a: str, b: str) -> float:
    ta = _tokenize(a)
    tb = _tokenize(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    if inter <= 0:
        return 0.0
    union = len(ta | tb)
    jaccard = inter / max(1, union)
    # Containment helps merge near-duplicate paraphrases like:
    # "住在武汉" vs "我住在武汉" where one token set is a superset of the other.
    containment = max(
        inter / max(1, len(ta)),
        inter / max(1, len(tb)),
    )
    return max(jaccard, containment * 0.92)


def _parse_ttl_days(value: Any, *, fallback_days: int) -> int:
    try:
        ttl = int(value)
    except Exception:
        return fallback_days
    if ttl <= 0:
        return fallback_days
    return ttl


def _prepare_memory_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for index, raw in enumerate(candidates):
        if not isinstance(raw, dict):
            continue
        prepared.append(
            {
                "index": index,
                "op": str(raw.get("op") or "save").strip().lower(),
                "memory_type": str(raw.get("memory_type") or "fact").strip().lower(),
                "key": str(raw.get("key") or "").strip(),
                "content": str(raw.get("content") or "").strip(),
                "importance": raw.get("importance"),
                "confidence": raw.get("confidence"),
                "ttl_days": raw.get("ttl_days"),
            }
        )
    return prepared


def _build_compact_context_text(rows: list[dict[str, str]]) -> str:
    settings = get_settings()
    max_chars = max(2000, int(settings.long_term_memory_extract_context_max_chars or 24000))
    per_message_max = max(60, int(settings.long_term_memory_extract_message_max_chars or 280))
    if not rows:
        return "（无）"

    lines: list[str] = []
    for row in rows:
        role = str(row.get("role") or "user").strip().lower() or "user"
        content = str(row.get("content") or "").strip()
        if not content:
            continue
        if len(content) > per_message_max:
            content = content[:per_message_max].rstrip() + "…"
        lines.append(f"- {role}: {content}")

    if not lines:
        return "（无）"
    full_text = "\n".join(lines)
    if len(full_text) <= max_chars:
        return full_text

    # Keep both early-session and recent-session evidence when transcript is too long.
    head: list[str] = []
    tail: list[str] = []
    head_budget = max_chars // 3
    tail_budget = max_chars - head_budget - 64
    head_used = 0
    tail_used = 0
    for line in lines:
        cost = len(line) + 1
        if head_used + cost > head_budget:
            break
        head.append(line)
        head_used += cost
    for line in reversed(lines):
        cost = len(line) + 1
        if tail_used + cost > tail_budget:
            break
        tail.append(line)
        tail_used += cost
    tail.reverse()
    compact = head + ["- system: （会话中段已压缩省略）"] + tail
    return "\n".join(compact)[:max_chars]


def _serialize_existing_memories(rows: list[LongTermMemory]) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for row in rows:
        serialized.append(
            {
                "id": row.id,
                "memory_key": row.memory_key,
                "memory_type": row.memory_type,
                "content": row.content,
                "importance": int(row.importance or 3),
                "confidence": float(row.confidence or 0.0),
                "updated_at": row.updated_at.isoformat() if row.updated_at else "",
                "expires_at": row.expires_at.isoformat() if row.expires_at else "",
            }
        )
    return serialized


def _find_semantic_duplicate(
    *,
    memory_type: str,
    content: str,
    rows: list[LongTermMemory],
    threshold: float = SEMANTIC_DUPLICATE_THRESHOLD,
    exclude_id: int | None = None,
) -> LongTermMemory | None:
    best_row: LongTermMemory | None = None
    best_score = 0.0
    for row in rows:
        if exclude_id is not None and row.id is not None and int(row.id) == int(exclude_id):
            continue
        if str(row.memory_type or "").strip().lower() != memory_type:
            continue
        score = _semantic_similarity(content, str(row.content or ""))
        if score > best_score:
            best_score = score
            best_row = row
    if best_row is None or best_score < threshold:
        return None
    return best_row


def _collect_semantic_duplicates(
    *,
    memory_type: str,
    content: str,
    rows: list[LongTermMemory],
    keep_id: int | None,
    threshold: float = SEMANTIC_MERGE_STRICT_THRESHOLD,
) -> list[LongTermMemory]:
    duplicates: list[LongTermMemory] = []
    for row in rows:
        if keep_id is not None and row.id is not None and int(row.id) == int(keep_id):
            continue
        if str(row.memory_type or "").strip().lower() != memory_type:
            continue
        score = _semantic_similarity(content, str(row.content or ""))
        if score >= threshold:
            duplicates.append(row)
    return duplicates


def _memory_score(query: str, row: LongTermMemory) -> float:
    q_tokens = _tokenize(query)
    if not q_tokens:
        recency = row.updated_at.timestamp() if row.updated_at else 0
        return float(row.importance) * 10 + recency / 1_000_000_000

    key_text = str(getattr(row, "memory_key", "") or "")
    memory_type = str(getattr(row, "memory_type", "") or "")
    key_text = key_text.replace(".", " ").replace("-", " ").replace("_", " ").replace(":", " ")
    content_tokens = _tokenize(f"{str(row.content or '')} {key_text} {memory_type}")
    overlap = len(q_tokens & content_tokens)
    if overlap <= 0:
        return 0.0
    overlap_score = overlap / max(1, len(q_tokens))
    importance_score = max(1, min(5, int(row.importance))) / 5
    recency_days = 365.0
    if row.updated_at:
        updated_at = row.updated_at
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        recency_days = max(0.0, (datetime.now(timezone.utc) - updated_at).days)
    recency_score = 1.0 / (1.0 + recency_days / 30.0)
    return overlap_score * 0.7 + importance_score * 0.2 + recency_score * 0.1


async def extract_memory_candidates(
    *,
    user_text: str,
    assistant_text: str,
    conversation_summary: str = "",
    conversation_messages: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    content = (user_text or "").strip()
    reply = (assistant_text or "").strip()
    if not content:
        return []

    context_rows: list[dict[str, str]] = []
    if isinstance(conversation_messages, list):
        for item in conversation_messages:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip().lower()
            msg = str(item.get("content") or "").strip()
            if not msg:
                continue
            if role not in {"user", "assistant", "system"}:
                role = "user"
            context_rows.append({"role": role, "content": msg})
    context_text = _build_compact_context_text(context_rows)
    summary_text = (conversation_summary or "").strip()
    if len(summary_text) > 1200:
        summary_text = summary_text[:1200].rstrip() + "…"
    if len(reply) > 1600:
        reply = reply[:1600].rstrip() + "…"
    if len(content) > 600:
        content = content[:600].rstrip() + "…"

    system = SystemMessage(
        content=(
            "你是长期记忆提取器。请仅返回 schema 定义的结构化字段（memories 数组）。"
            "只输出一个 JSON 对象，不要输出解释文本。"
            "从用户输入 + 完整会话上下文综合提取候选记忆，不要只看当前单句。"
            "助手回复仅作上下文，不是事实来源。"
            "仅保留在 30 天后仍可能有价值的候选。"
            "尽量保持用户原始措辞与语言，不要随意翻译记忆内容。"
            "不要保存短期状态、一次性任务进度、天气快照、日/周汇总、运维/系统/工具内部信息。"
            "不要保存原始数据库日志；应抽象为可复用的用户语义信息。"
            "不要输出身份档案记忆（昵称/姓名/助手名/emoji）。"
            "若用户明确表达“希望你记住/以后按此执行”，应优先提取对应长期规则或偏好，"
            "并提高 importance/confidence。"
            "若用户明确表达“忘记/不再记住某事项”，应输出 op=delete 的候选并直接执行，不需要确认。"
            "若用户明确表达“更改/改成/以后按新规则”，应将其视为覆盖旧记忆，优先输出可覆盖旧值的 save 决策。"
            "每条字段：op(save|delete), memory_type(preference|fact|goal|project|constraint), "
            "key(必须是稳定槽位键，例如 profile.birthday / profile.residence_city / preference.sport), "
            "content(仅填写值，不要整句复述，例如 1999-02-07 / 武汉), "
            "importance(1-5), confidence(0-1), ttl_days(可选整数)。"
        )
    )
    human = HumanMessage(
        content=(
            f"会话摘要:\n{summary_text}\n\n"
            f"完整会话:\n{context_text}\n\n"
            f"用户输入:\n{content}\n\n"
            f"助手回复（仅作参考，不是事实来源）:\n{reply}"
        )
    )
    try:
        parsed = await _invoke_structured(
            schema=MemoryExtractionResult,
            messages=[system, human],
        )
        rows = list(getattr(parsed, "memories", []) or [])
        return [row.model_dump() if isinstance(row, BaseModel) else dict(row) for row in rows if row]
    except Exception:
        return []


async def _llm_refine_memory_candidates(
    *,
    user_text: str,
    candidates: list[dict[str, Any]],
    existing_rows: list[LongTermMemory],
) -> list[dict[str, Any]]:
    prepared = _prepare_memory_candidates(candidates)
    if not prepared:
        return []

    system = SystemMessage(
        content=(
            "你是记忆整合器，负责决定长期记忆的最终写入动作。"
            "准入规则：仅保留 30 天后仍可能有用的记忆。"
            "拒绝短期状态、单轮上下文、短时报告、工具输出和系统内部信息。"
            "若用户明确表达“请记住某规则/偏好”，在不与现有记忆冲突时应优先保留。"
            "若用户明确表达“忘记某事项”，应优先产生 delete 决策并直接生效，不做确认。"
            "若用户明确表达“更改某信息”，应优先产出覆盖旧值的 save（必要时设置 merge_target_id）。"
            "内容尽量保留用户原始语言，避免不必要翻译。"
            "仅基于语义判断。"
            "需与已有记忆去重：若语义等价，优先更新旧记忆而非新建。"
            "对语义相同但表述不同的候选，应归并为一个规范抽象表述。"
            "key 必须稳定且可复用；若已有键语义可复用，优先复用已有键。"
            "content 仅保留值，不要带多余主语或礼貌语。"
            "请仅返回 schema 定义的结构化字段（decisions 数组）。"
            "只输出一个 JSON 对象，不要输出解释文本。"
            "每条 decision 字段：index(int), keep(bool), op(save|delete), merge_target_id(可选 int), "
            "memory_type(preference|fact|goal|project|constraint), key(可选), content, "
            "importance(1-5), confidence(0-1), ttl_days(可选 int)。"
        )
    )
    human = HumanMessage(
        content=(
            f"用户输入:\n{(user_text or '').strip()}\n\n"
            f"候选记忆JSON:\n{json.dumps(prepared, ensure_ascii=False)}\n\n"
            f"已有记忆JSON:\n{json.dumps(_serialize_existing_memories(existing_rows), ensure_ascii=False)}"
        )
    )
    try:
        parsed = await _invoke_structured(
            schema=MemoryRefineResult,
            messages=[system, human],
        )
        decisions = list(getattr(parsed, "decisions", []) or [])
    except Exception:
        logger.exception("memory refine failed; falling back to extracted candidates")
        return _fallback_refined_memory_candidates(prepared)

    by_index: dict[int, dict[str, Any]] = {}
    for item in decisions:
        item_dict = item.model_dump() if isinstance(item, BaseModel) else dict(item or {})
        try:
            idx = int(item_dict.get("index"))
        except Exception:
            continue
        by_index[idx] = item_dict

    refined: list[dict[str, Any]] = []
    for index, raw in enumerate(candidates):
        decision = by_index.get(index)
        if not decision or not bool(decision.get("keep") is True):
            continue
        if not isinstance(raw, dict):
            continue
        merged = {
            "op": str(decision.get("op") or raw.get("op") or "save").strip().lower(),
            "merge_target_id": decision.get("merge_target_id"),
            "memory_type": _normalize_memory_type(str(decision.get("memory_type") or raw.get("memory_type") or "fact")),
            "key": str(decision.get("key") or raw.get("key") or "").strip(),
            "content": str(decision.get("content") or raw.get("content") or "").strip(),
            "importance": decision.get("importance", raw.get("importance")),
            "confidence": decision.get("confidence", raw.get("confidence")),
            "ttl_days": decision.get("ttl_days", raw.get("ttl_days")),
        }
        refined.append(merged)
    if refined:
        return refined
    logger.warning("memory refine returned 0 kept candidates; falling back to extracted candidates")
    return _fallback_refined_memory_candidates(prepared)


async def upsert_long_term_memories(
    session: AsyncSession,
    *,
    user_id: int,
    conversation_id: int | None,
    source_message_id: int | None,
    candidates: list[dict[str, Any]],
    user_text: str = "",
    user_nickname: str = "",
    user_ai_name: str = "",
    user_ai_emoji: str = "",
    bypass_refine: bool = False,
) -> int:
    settings = get_settings()
    if not settings.long_term_memory_enabled:
        return 0

    if not candidates:
        return 0

    now = datetime.now(timezone.utc)
    scan_limit = max(settings.long_term_memory_retrieve_scan_limit, MEMORY_CONSOLIDATE_SCAN_LIMIT)
    existing_stmt = (
        select(LongTermMemory)
        .where(
            LongTermMemory.user_id == user_id,
            or_(LongTermMemory.expires_at.is_(None), LongTermMemory.expires_at > now),
        )
        .order_by(LongTermMemory.updated_at.desc(), LongTermMemory.id.desc())
        .limit(scan_limit)
    )
    existing_rows = list((await session.execute(existing_stmt)).scalars().all())
    existing_by_id = {int(row.id): row for row in existing_rows if row.id is not None}
    working_rows = list(existing_rows)

    prepared_candidates = _prepare_memory_candidates(candidates)
    if bypass_refine:
        vetted = prepared_candidates
    else:
        vetted = await _llm_refine_memory_candidates(
            user_text=user_text,
            candidates=candidates,
            existing_rows=existing_rows,
        )
    if not vetted:
        return 0

    processed = 0
    dropped_empty = 0
    dropped_identity = 0
    dropped_low_confidence = 0
    key_cache: dict[tuple[str, str], str] = {}
    for raw in vetted[: max(1, settings.long_term_memory_max_write_items)]:
        op = str(raw.get("op") or "save").strip().lower()
        memory_type = _normalize_memory_type(str(raw.get("memory_type") or "fact"))
        content = str(raw.get("content") or "").strip()
        if _is_reserved_identity_memory_type(memory_type):
            dropped_identity += 1
            continue
        confidence_raw = raw.get("confidence")
        if confidence_raw is None or str(confidence_raw).strip() == "":
            confidence = max(float(settings.long_term_memory_min_confidence), 0.8)
        else:
            try:
                confidence = float(confidence_raw)
            except Exception:
                confidence = max(float(settings.long_term_memory_min_confidence), 0.8)
        confidence = max(0.0, min(1.0, confidence))
        try:
            importance = int(raw.get("importance") or 3)
        except Exception:
            importance = 3
        importance = max(1, min(5, importance))
        raw_key = str(raw.get("key") or "").strip()
        memory_key = _normalize_key(raw_key)
        if not memory_key:
            cache_key = (memory_type, content)
            inferred = key_cache.get(cache_key, "")
            if not inferred:
                inferred = await _infer_memory_key_via_llm(
                    memory_type=memory_type,
                    content=content,
                    existing_rows=existing_rows,
                )
                key_cache[cache_key] = inferred
            memory_key = _normalize_key(inferred)
        if not memory_key:
            memory_key = _build_memory_key(memory_type, content)
        merge_target_id: int | None = None
        try:
            if raw.get("merge_target_id") is not None:
                merge_target_id = int(raw.get("merge_target_id"))
        except Exception:
            merge_target_id = None
        if _is_identity_memory_candidate(
            memory_type=memory_type,
            memory_key=memory_key,
            content=content,
            user_nickname=user_nickname,
            user_ai_name=user_ai_name,
            user_ai_emoji=user_ai_emoji,
        ):
            dropped_identity += 1
            continue

        if op == "delete":
            existing: LongTermMemory | None = None
            if merge_target_id and merge_target_id in existing_by_id:
                existing = existing_by_id[merge_target_id]
            if existing is None:
                stmt = select(LongTermMemory).where(
                    LongTermMemory.user_id == user_id,
                    LongTermMemory.memory_key == memory_key,
                )
                existing = (await session.execute(stmt)).scalar_one_or_none()
            if existing:
                await session.delete(existing)
                if existing in working_rows:
                    working_rows.remove(existing)
                if existing.id is not None:
                    existing_by_id.pop(int(existing.id), None)
                processed += 1
            continue

        if not content:
            dropped_empty += 1
            continue
        content = content[:1000]
        if confidence < settings.long_term_memory_min_confidence:
            dropped_low_confidence += 1
            continue

        ttl_days = _parse_ttl_days(raw.get("ttl_days"), fallback_days=settings.long_term_memory_default_ttl_days)
        expires_at = now + timedelta(days=ttl_days)

        existing: LongTermMemory | None = None
        if merge_target_id and merge_target_id in existing_by_id:
            existing = existing_by_id[merge_target_id]
        if existing is None:
            stmt = select(LongTermMemory).where(
                LongTermMemory.user_id == user_id,
                LongTermMemory.memory_key == memory_key,
            )
            existing = (await session.execute(stmt)).scalar_one_or_none()
        if existing is None:
            existing = _find_semantic_duplicate(memory_type=memory_type, content=content, rows=working_rows)

        if existing:
            existing.memory_type = memory_type
            existing.content = content
            existing.importance = importance
            existing.confidence = confidence
            existing.expires_at = expires_at
            existing.conversation_id = conversation_id
            existing.source_message_id = source_message_id
            existing.updated_at = now
            session.add(existing)
            if existing.id is not None:
                existing_by_id[int(existing.id)] = existing
            if existing not in working_rows:
                working_rows.append(existing)

            # Strong merge after update: clear semantic-equivalent duplicates even if keys differ.
            duplicate_rows = _collect_semantic_duplicates(
                memory_type=memory_type,
                content=content,
                rows=working_rows,
                keep_id=(int(existing.id) if existing.id is not None else None),
            )
            for dup in duplicate_rows:
                if dup in working_rows:
                    working_rows.remove(dup)
                if dup.id is not None:
                    existing_by_id.pop(int(dup.id), None)
                    await session.delete(dup)
                else:
                    # Pending in-session row (no PK yet): drop it before flush.
                    try:
                        session.expunge(dup)
                    except Exception:
                        pass
            processed += 1
            continue

        new_row = LongTermMemory(
            user_id=user_id,
            conversation_id=conversation_id,
            source_message_id=source_message_id,
            memory_key=memory_key,
            memory_type=memory_type,
            content=content,
            importance=importance,
            confidence=confidence,
            expires_at=expires_at,
        )
        session.add(new_row)
        working_rows.append(new_row)
        processed += 1

    if processed > 0:
        await session.commit()
    logger.info(
        "long-term memory upsert summary: user_id=%s conversation_id=%s input=%s processed=%s dropped_identity=%s dropped_empty=%s dropped_low_confidence=%s",
        user_id,
        conversation_id,
        len(vetted),
        processed,
        dropped_identity,
        dropped_empty,
        dropped_low_confidence,
    )
    return processed


async def consolidate_user_long_term_memories(
    session: AsyncSession,
    *,
    user_id: int,
    max_scan: int = 200,
) -> dict[str, int]:
    now = datetime.now(timezone.utc)
    stmt = (
        select(LongTermMemory)
        .where(
            LongTermMemory.user_id == user_id,
            or_(LongTermMemory.expires_at.is_(None), LongTermMemory.expires_at > now),
        )
        .order_by(LongTermMemory.updated_at.desc(), LongTermMemory.id.desc())
        .limit(max(1, max_scan))
    )
    rows_all = list((await session.execute(stmt)).scalars().all())
    deleted = 0
    # Legacy cleanup: physically delete rows previously soft-deactivated.
    for row in rows_all:
        if bool(getattr(row, "is_active", True)) is False:
            await session.delete(row)
            deleted += 1
    rows = [row for row in rows_all if bool(getattr(row, "is_active", True))]
    if len(rows) <= 1:
        if deleted > 0:
            await session.commit()
        return {"reviewed": len(rows), "updated": 0, "deleted": deleted, "merged": 0}

    system = SystemMessage(
        content=(
            "你是长期记忆清理器。请仅返回 schema 定义的结构化字段（decisions 数组）。"
            "只输出一个 JSON 对象，不要输出解释文本。"
            "你需要对每个记忆 id 判断是否保留。准入规则：仅保留 30 天后仍有价值的信息。"
            "清理短期状态、过时一次性事实、系统内部描述和重复项。"
            "如果存在重复记忆，保留一个规范记忆，其它项通过 merge_into_id 合并。"
            "每条 decision 字段：id(int), keep(bool), merge_into_id(可选 int), "
            "memory_type(preference|fact|goal|project|constraint), content, importance(1-5), confidence(0-1), "
            "ttl_days(可选 int)。"
        )
    )
    human = HumanMessage(content=json.dumps({"memories": _serialize_existing_memories(rows)}, ensure_ascii=False))
    try:
        parsed = await _invoke_structured(
            schema=MemoryConsolidationResult,
            messages=[system, human],
        )
        decisions = list(getattr(parsed, "decisions", []) or [])
    except Exception:
        logger.exception("memory consolidation llm call failed: user_id=%s", user_id)
        return {"reviewed": len(rows), "updated": 0, "deleted": deleted, "merged": 0}

    by_id = {int(row.id): row for row in rows if row.id is not None}
    updated = 0
    merged = 0

    # First pass: explicit deletions
    parsed: list[dict[str, Any]] = []
    for raw in decisions:
        raw_dict = raw.model_dump() if isinstance(raw, BaseModel) else dict(raw or {})
        try:
            rid = int(raw_dict.get("id"))
        except Exception:
            continue
        if rid not in by_id:
            continue
        parsed.append(raw_dict)
        keep = bool(raw_dict.get("keep") is True)
        if not keep:
            row = by_id[rid]
            await session.delete(row)
            deleted += 1

    # Second pass: merges / updates
    for raw in parsed:
        rid = int(raw.get("id"))
        source = by_id.get(rid)
        if source is None:
            continue
        if not bool(raw.get("keep") is True):
            continue
        merge_into_id = raw.get("merge_into_id")
        target: LongTermMemory | None = None
        try:
            if merge_into_id is not None:
                target = by_id.get(int(merge_into_id))
        except Exception:
            target = None

        try:
            importance = int(raw.get("importance") or source.importance or 3)
        except Exception:
            importance = int(source.importance or 3)
        importance = max(1, min(5, importance))
        try:
            confidence = float(raw.get("confidence") or source.confidence or 0.0)
        except Exception:
            confidence = float(source.confidence or 0.0)
        ttl_days = _parse_ttl_days(raw.get("ttl_days"), fallback_days=get_settings().long_term_memory_default_ttl_days)
        expires_at = now + timedelta(days=ttl_days)
        memory_type = _normalize_memory_type(str(raw.get("memory_type") or source.memory_type or "fact"))
        content = str(raw.get("content") or source.content or "").strip()[:1000]
        if not content:
            continue

        if target is not None and target.id != source.id:
            target.memory_type = memory_type
            target.content = content
            target.importance = importance
            target.confidence = confidence
            target.expires_at = expires_at
            target.updated_at = now
            session.add(target)
            updated += 1
            await session.delete(source)
            deleted += 1
            merged += 1
            continue

        source.memory_type = memory_type
        source.content = content
        source.importance = importance
        source.confidence = confidence
        source.expires_at = expires_at
        source.updated_at = now
        session.add(source)
        updated += 1

    if updated > 0 or deleted > 0:
        await session.commit()
    return {
        "reviewed": len(rows),
        "updated": updated,
        "deleted": deleted,
        "merged": merged,
    }


async def deactivate_identity_memories_for_user(
    session: AsyncSession,
    *,
    user_id: int,
) -> int:
    stmt = select(LongTermMemory).where(
        LongTermMemory.user_id == user_id,
    )
    rows = list((await session.execute(stmt)).scalars().all())
    if not rows:
        return 0

    changed = 0
    now = datetime.now(timezone.utc)
    for row in rows:
        if _is_identity_memory_candidate(
            memory_type=str(row.memory_type or ""),
            memory_key=str(row.memory_key or ""),
            content=str(row.content or ""),
        ):
            await session.delete(row)
            changed += 1
    return changed


async def list_long_term_memories(
    session: AsyncSession,
    *,
    user_id: int,
    limit: int | None = 120,
) -> list[dict[str, Any]]:
    settings = get_settings()
    if not settings.long_term_memory_enabled:
        return []

    now = datetime.now(timezone.utc)
    stmt = (
        select(LongTermMemory)
        .where(
            LongTermMemory.user_id == user_id,
            or_(LongTermMemory.expires_at.is_(None), LongTermMemory.expires_at > now),
        )
        .order_by(LongTermMemory.updated_at.desc(), LongTermMemory.id.desc())
    )
    if limit is not None:
        stmt = stmt.limit(max(1, int(limit)))
    rows = list((await session.execute(stmt)).scalars().all())
    if not rows:
        return []

    result: list[dict[str, Any]] = []
    for row in rows:
        if _is_identity_memory_candidate(
            memory_type=str(row.memory_type or ""),
            memory_key=str(row.memory_key or ""),
            content=str(row.content or ""),
        ):
            continue
        row.last_accessed_at = now
        session.add(row)
        result.append(
            {
                "id": row.id,
                "memory_key": str(row.memory_key or ""),
                "memory_type": row.memory_type,
                "content": row.content,
                "importance": int(row.importance or 3),
                "confidence": round(float(row.confidence or 0.0), 3),
                "updated_at": _to_client_tz_iso(row.updated_at),
            }
        )

    await session.commit()
    return result


async def find_active_long_term_memory(
    session: AsyncSession,
    *,
    user_id: int,
    memory_id: int | None = None,
    memory_key: str = "",
    content_hint: str = "",
    memory_type: str = "",
) -> LongTermMemory | None:
    settings = get_settings()
    if not settings.long_term_memory_enabled:
        return None

    now = datetime.now(timezone.utc)
    normalized_type = _normalize_memory_type(memory_type) if str(memory_type or "").strip() else ""
    if memory_id is not None:
        try:
            target_id = int(memory_id)
        except Exception:
            target_id = 0
        if target_id > 0:
            row = await session.get(LongTermMemory, target_id)
            if (
                row is not None
                and int(row.user_id or 0) == int(user_id)
                and (row.expires_at is None or row.expires_at > now)
                and not _is_identity_memory_candidate(
                    memory_type=str(row.memory_type or ""),
                    memory_key=str(row.memory_key or ""),
                    content=str(row.content or ""),
                )
            ):
                return row

    normalized_key = _normalize_key(memory_key)
    if normalized_key:
        stmt = select(LongTermMemory).where(
            LongTermMemory.user_id == user_id,
            LongTermMemory.memory_key == normalized_key,
            or_(LongTermMemory.expires_at.is_(None), LongTermMemory.expires_at > now),
        )
        row = (await session.execute(stmt)).scalar_one_or_none()
        if row is not None and not _is_identity_memory_candidate(
            memory_type=str(row.memory_type or ""),
            memory_key=str(row.memory_key or ""),
            content=str(row.content or ""),
        ):
            return row

    hint = str(content_hint or "").strip()
    if not hint:
        return None

    scan_limit = max(20, int(settings.long_term_memory_retrieve_scan_limit or 80))
    stmt = (
        select(LongTermMemory)
        .where(
            LongTermMemory.user_id == user_id,
            or_(LongTermMemory.expires_at.is_(None), LongTermMemory.expires_at > now),
        )
        .order_by(LongTermMemory.updated_at.desc(), LongTermMemory.id.desc())
        .limit(scan_limit)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    filtered_rows: list[LongTermMemory] = []
    for row in rows:
        if _is_identity_memory_candidate(
            memory_type=str(row.memory_type or ""),
            memory_key=str(row.memory_key or ""),
            content=str(row.content or ""),
        ):
            continue
        if normalized_type and str(row.memory_type or "").strip().lower() != normalized_type:
            continue
        filtered_rows.append(row)

    if not filtered_rows:
        return None

    hint_lower = hint.lower()
    for row in filtered_rows:
        if hint == str(row.content or "").strip():
            return row
        if hint_lower == str(row.memory_key or "").strip().lower():
            return row

    best_row: LongTermMemory | None = None
    best_score = 0.0
    for row in filtered_rows:
        score = _semantic_similarity(hint, str(row.content or ""))
        if score > best_score:
            best_score = score
            best_row = row
    if best_row is None or best_score < SEMANTIC_DUPLICATE_THRESHOLD:
        return None
    return best_row


async def retrieve_relevant_long_term_memories(
    session: AsyncSession,
    *,
    user_id: int,
    query: str,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    settings = get_settings()
    if not settings.long_term_memory_enabled:
        return []

    top_k = limit or settings.long_term_memory_retrieve_limit
    scan_limit = max(top_k, settings.long_term_memory_retrieve_scan_limit)
    now = datetime.now(timezone.utc)
    stmt = (
        select(LongTermMemory)
        .where(
            LongTermMemory.user_id == user_id,
            or_(LongTermMemory.expires_at.is_(None), LongTermMemory.expires_at > now),
        )
        .order_by(LongTermMemory.importance.desc(), LongTermMemory.updated_at.desc())
        .limit(scan_limit)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    if not rows:
        return []

    scored: list[tuple[LongTermMemory, float]] = [(row, _memory_score(query, row)) for row in rows]
    scored_sorted = sorted(scored, key=lambda pair: pair[1], reverse=True)
    query_tokens = _tokenize(query)
    ranked: list[LongTermMemory] = []
    if query_tokens:
        lexical = [item for item in scored_sorted if item[1] >= 0.12]
        ranked = [item[0] for item in lexical[:top_k]]
        if len(ranked) < top_k:
            for row, _score in scored_sorted:
                if row in ranked:
                    continue
                ranked.append(row)
                if len(ranked) >= top_k:
                    break
    else:
        ranked = [item[0] for item in scored_sorted[:top_k]]
    result: list[dict[str, Any]] = []
    for row in ranked:
        if _is_identity_memory_candidate(
            memory_type=str(row.memory_type or ""),
            memory_key=str(row.memory_key or ""),
            content=str(row.content or ""),
        ):
            continue
        row.last_accessed_at = now
        session.add(row)
        result.append(
            {
                "id": row.id,
                "memory_key": str(row.memory_key or ""),
                "memory_type": row.memory_type,
                "content": row.content,
                "importance": row.importance,
                "confidence": round(float(row.confidence), 3),
            }
        )
    await session.commit()
    return result


async def deactivate_all_identity_memories(session: AsyncSession) -> int:
    stmt = select(LongTermMemory)
    rows = list((await session.execute(stmt)).scalars().all())
    if not rows:
        return 0

    changed = 0
    for row in rows:
        if _is_identity_memory_candidate(
            memory_type=str(row.memory_type or ""),
            memory_key=str(row.memory_key or ""),
            content=str(row.content or ""),
        ):
            await session.delete(row)
            changed += 1
    if changed > 0:
        await session.commit()
    return changed
