from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any

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

logger = logging.getLogger(__name__)


class MemoryIdSelection(BaseModel):
    ids: list[int] = Field(default_factory=list)


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
    return inter / max(1, union)


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


def _serialize_existing_memories(rows: list[LongTermMemory]) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for row in rows:
        serialized.append(
            {
                "id": row.id,
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
) -> LongTermMemory | None:
    best_row: LongTermMemory | None = None
    best_score = 0.0
    for row in rows:
        if str(row.memory_type or "").strip().lower() != memory_type:
            continue
        score = _semantic_similarity(content, str(row.content or ""))
        if score > best_score:
            best_score = score
            best_row = row
    if best_row is None or best_score < threshold:
        return None
    return best_row


def _memory_score(query: str, row: LongTermMemory) -> float:
    q_tokens = _tokenize(query)
    if not q_tokens:
        recency = row.updated_at.timestamp() if row.updated_at else 0
        return float(row.importance) * 10 + recency / 1_000_000_000

    content_tokens = _tokenize(row.content)
    overlap = len(q_tokens & content_tokens)
    if overlap <= 0:
        return 0.0
    overlap_score = overlap / max(1, len(q_tokens))
    importance_score = max(1, min(5, int(row.importance))) / 5
    recency_days = 365.0
    if row.updated_at:
        recency_days = max(0.0, (datetime.utcnow() - row.updated_at.replace(tzinfo=None)).days)
    recency_score = 1.0 / (1.0 + recency_days / 30.0)
    return overlap_score * 0.7 + importance_score * 0.2 + recency_score * 0.1


async def _llm_select_relevant_memory_ids(
    *,
    query: str,
    rows: list[LongTermMemory],
    top_k: int,
) -> list[int]:
    if not rows or not (query or "").strip():
        return []

    runnable_schema = MemoryIdSelection
    system = SystemMessage(
        content=(
            "你是记忆检索器。请仅返回 schema 定义的结构化字段（ids 数组）。"
            "给定用户查询和候选记忆，选择最相关的记忆 id。"
            "应基于语义匹配（可跨语言），而非仅关键词重叠。"
            "优先稳定的用户偏好/事实/目标，弱化短期噪声。"
            "最多返回 top_k 个 id，并按相关性排序。"
        )
    )
    candidates: list[dict[str, Any]] = []
    for row in rows[:120]:
        if row.id is None:
            continue
        candidates.append(
            {
                "id": int(row.id),
                "memory_type": str(row.memory_type or ""),
                "content": str(row.content or ""),
                "importance": int(row.importance or 3),
                "confidence": float(row.confidence or 0.0),
                "updated_at": row.updated_at.isoformat() if row.updated_at else "",
            }
        )
    if not candidates:
        return []

    human = HumanMessage(
        content=json.dumps(
            {
                "query": (query or "").strip(),
                "top_k": int(max(1, top_k)),
                "candidates": candidates,
            },
            ensure_ascii=False,
        )
    )
    try:
        parsed = await _invoke_structured(
            schema=runnable_schema,
            messages=[system, human],
        )
        raw_ids = list(getattr(parsed, "ids", []) or [])
        valid_ids = {int(item["id"]) for item in candidates}
        selected: list[int] = []
        for rid in raw_ids:
            try:
                memory_id = int(rid)
            except Exception:
                continue
            if memory_id not in valid_ids or memory_id in selected:
                continue
            selected.append(memory_id)
            if len(selected) >= max(1, top_k):
                break
        return selected
    except Exception:
        return []


async def extract_memory_candidates(
    *,
    user_text: str,
    assistant_text: str,
    conversation_summary: str = "",
) -> list[dict[str, Any]]:
    content = (user_text or "").strip()
    reply = (assistant_text or "").strip()
    if not content:
        return []

    system = SystemMessage(
        content=(
            "你是长期记忆提取器。请仅返回 schema 定义的结构化字段（memories 数组）。"
            "只从用户输入提取候选记忆；助手回复仅作上下文，不是事实来源。"
            "仅保留在 30 天后仍可能有价值的候选。"
            "尽量保持用户原始措辞与语言，不要随意翻译记忆内容。"
            "不要保存短期状态、一次性任务进度、天气快照、日/周汇总、运维/系统/工具内部信息。"
            "不要保存原始数据库日志；应抽象为可复用的用户语义信息。"
            "不要输出身份档案记忆（昵称/姓名/助手名/emoji）。"
            "每条字段：op(save|delete), memory_type(preference|fact|goal|project|constraint), "
            "key(可选), content, importance(1-5), confidence(0-1), ttl_days(可选整数)。"
        )
    )
    human = HumanMessage(
        content=(
            f"会话摘要:\n{conversation_summary}\n\n"
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
            "内容尽量保留用户原始语言，避免不必要翻译。"
            "仅基于语义判断。"
            "需与已有记忆去重：若语义等价，优先更新旧记忆而非新建。"
            "对语义相同但表述不同的候选，应归并为一个规范抽象表述。"
            "请仅返回 schema 定义的结构化字段（decisions 数组）。"
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
        return []

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
    return refined


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
) -> int:
    settings = get_settings()
    if not settings.long_term_memory_enabled:
        return 0

    if not candidates:
        return 0

    now = datetime.utcnow()
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

    vetted = await _llm_refine_memory_candidates(
        user_text=user_text,
        candidates=candidates,
        existing_rows=existing_rows,
    )
    if not vetted:
        return 0

    processed = 0
    for raw in vetted[: max(1, settings.long_term_memory_max_write_items)]:
        op = str(raw.get("op") or "save").strip().lower()
        memory_type = _normalize_memory_type(str(raw.get("memory_type") or "fact"))
        content = str(raw.get("content") or "").strip()
        if _is_reserved_identity_memory_type(memory_type):
            continue
        try:
            confidence = float(raw.get("confidence") or 0.0)
        except Exception:
            confidence = 0.0
        try:
            importance = int(raw.get("importance") or 3)
        except Exception:
            importance = 3
        importance = max(1, min(5, importance))
        raw_key = str(raw.get("key") or "").strip()
        memory_key = _normalize_key(raw_key) or _build_memory_key(memory_type, content)
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
            continue
        content = content[:1000]
        if confidence < settings.long_term_memory_min_confidence:
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
    return processed


async def consolidate_user_long_term_memories(
    session: AsyncSession,
    *,
    user_id: int,
    max_scan: int = 200,
) -> dict[str, int]:
    now = datetime.utcnow()
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
    now = datetime.utcnow()
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
    limit: int = 120,
) -> list[dict[str, Any]]:
    settings = get_settings()
    if not settings.long_term_memory_enabled:
        return []

    now = datetime.utcnow()
    stmt = (
        select(LongTermMemory)
        .where(
            LongTermMemory.user_id == user_id,
            or_(LongTermMemory.expires_at.is_(None), LongTermMemory.expires_at > now),
        )
        .order_by(LongTermMemory.updated_at.desc(), LongTermMemory.id.desc())
        .limit(max(1, int(limit)))
    )
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
                "memory_type": row.memory_type,
                "content": row.content,
                "importance": int(row.importance or 3),
                "confidence": round(float(row.confidence or 0.0), 3),
                "updated_at": row.updated_at.isoformat() if row.updated_at else "",
            }
        )

    await session.commit()
    return result


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
    now = datetime.utcnow()
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
    query_tokens = _tokenize(query)
    ranked: list[LongTermMemory] = []
    if query_tokens:
        lexical = [item for item in scored if item[1] >= 0.12]
        if lexical:
            ranked = [item[0] for item in sorted(lexical, key=lambda pair: pair[1], reverse=True)[:top_k]]
        else:
            # Fallback: if user has only a few memories, prefer returning them over empty recall.
            # This avoids "memory exists but cannot recall" cases when wording/language differs.
            if len(rows) <= 3:
                ranked = rows[:top_k]
    else:
        ranked = [item[0] for item in sorted(scored, key=lambda pair: pair[1], reverse=True)[:top_k]]
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
