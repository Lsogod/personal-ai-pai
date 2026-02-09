from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.memory import LongTermMemory
from app.services.llm import get_llm

VALID_MEMORY_TYPES = {"profile", "preference", "fact", "goal", "project", "constraint"}
TOKEN_PATTERN = re.compile(r"[\u4e00-\u9fff]+|[a-zA-Z0-9_]{2,}")
IDENTITY_KEYWORDS = {
    "nickname",
    "name",
    "ai_name",
    "ai_emoji",
    "assistant_name",
    "称呼",
    "昵称",
    "名字",
    "助手名字",
    "ai名称",
    "ai表情",
    "emoji",
}
IDENTITY_MEMORY_KEYS = {
    "preferred_name",
    "nickname",
    "ai_name",
    "ai_emoji",
    "assistant_name",
}


def _parse_json_object(content: str) -> dict[str, Any]:
    text = (content or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()
    try:
        data = json.loads(text)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


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


def _looks_like_identity_text(value: str) -> bool:
    text = (value or "").strip().lower()
    if not text:
        return False
    for kw in IDENTITY_KEYWORDS:
        if kw in text:
            return True
    return False


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
    if _looks_like_identity_text(memory_key) or _looks_like_identity_text(content):
        return True
    if memory_type == "profile":
        nick = (user_nickname or "").strip()
        ai_name = (user_ai_name or "").strip()
        ai_emoji = (user_ai_emoji or "").strip()
        c = (content or "").strip()
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
    return {token.lower() for token in TOKEN_PATTERN.findall((text or "").strip())}


def _memory_score(query: str, row: LongTermMemory) -> float:
    q_tokens = _tokenize(query)
    if not q_tokens:
        recency = row.updated_at.timestamp() if row.updated_at else 0
        return float(row.importance) * 10 + recency / 1_000_000_000

    content_tokens = _tokenize(row.content)
    overlap = len(q_tokens & content_tokens)
    overlap_score = overlap / max(1, len(q_tokens))
    importance_score = max(1, min(5, int(row.importance))) / 5
    recency_days = 365.0
    if row.updated_at:
        recency_days = max(0.0, (datetime.utcnow() - row.updated_at.replace(tzinfo=None)).days)
    recency_score = 1.0 / (1.0 + recency_days / 30.0)
    return overlap_score * 0.7 + importance_score * 0.2 + recency_score * 0.1


async def extract_memory_candidates(
    *,
    user_text: str,
    assistant_text: str,
    conversation_summary: str = "",
) -> list[dict[str, Any]]:
    content = (user_text or "").strip()
    reply = (assistant_text or "").strip()
    if not content or not reply:
        return []

    llm = get_llm(node_name="memory")
    system = SystemMessage(
        content=(
            "你是长期记忆提取器。"
            "请从用户信息中提取对未来对话长期有价值、稳定且可复用的记忆。"
            "只输出 JSON：{\"memories\":[...]}。"
            "每项字段：op(memory op: save|delete), memory_type(profile|preference|fact|goal|project|constraint),"
            " key(可空), content, importance(1-5), confidence(0-1), ttl_days(可空整数)。"
            "当用户明确表达“请记住/记一下/别忘了”时，应优先提取该长期记忆。"
            "不要提取昵称、AI 名称、AI 表情、称呼等身份设定，这些属于用户主档案。"
            "不要保存一次性瞬时信息（例如‘今天中午12点开会一次’）。"
            "如果没有可保存记忆，返回空数组。"
        )
    )
    human = HumanMessage(
        content=(
            f"会话摘要:\n{conversation_summary}\n\n"
            f"用户消息:\n{content}\n\n"
            f"助手回复:\n{reply}"
        )
    )
    try:
        response = await llm.ainvoke([system, human])
        payload = _parse_json_object(str(response.content))
        rows = payload.get("memories")
        return rows if isinstance(rows, list) else []
    except Exception:
        return []


async def upsert_long_term_memories(
    session: AsyncSession,
    *,
    user_id: int,
    conversation_id: int | None,
    source_message_id: int | None,
    candidates: list[dict[str, Any]],
    user_nickname: str = "",
    user_ai_name: str = "",
    user_ai_emoji: str = "",
) -> int:
    settings = get_settings()
    if not settings.long_term_memory_enabled:
        return 0

    if not candidates:
        return 0

    processed = 0
    now = datetime.utcnow()
    for raw in candidates[: max(1, settings.long_term_memory_max_write_items)]:
        if not isinstance(raw, dict):
            continue
        op = str(raw.get("op") or "save").strip().lower()
        memory_type = _normalize_memory_type(str(raw.get("memory_type") or "fact"))
        content = str(raw.get("content") or "").strip()
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
            stmt = select(LongTermMemory).where(
                LongTermMemory.user_id == user_id,
                LongTermMemory.memory_key == memory_key,
                LongTermMemory.is_active.is_(True),
            )
            existing = (await session.execute(stmt)).scalar_one_or_none()
            if existing:
                existing.is_active = False
                existing.updated_at = now
                session.add(existing)
                processed += 1
            continue

        if not content:
            continue
        content = content[:1000]
        if confidence < settings.long_term_memory_min_confidence:
            continue

        ttl_days = raw.get("ttl_days")
        expires_at = now + timedelta(days=settings.long_term_memory_default_ttl_days)
        if isinstance(ttl_days, int) and ttl_days > 0:
            expires_at = now + timedelta(days=ttl_days)

        stmt = select(LongTermMemory).where(
            LongTermMemory.user_id == user_id,
            LongTermMemory.memory_key == memory_key,
        )
        existing = (await session.execute(stmt)).scalar_one_or_none()
        if existing:
            existing.memory_type = memory_type
            existing.content = content
            existing.importance = importance
            existing.confidence = confidence
            existing.is_active = True
            existing.expires_at = expires_at
            existing.conversation_id = conversation_id
            existing.source_message_id = source_message_id
            existing.updated_at = now
            session.add(existing)
            processed += 1
            continue

        session.add(
            LongTermMemory(
                user_id=user_id,
                conversation_id=conversation_id,
                source_message_id=source_message_id,
                memory_key=memory_key,
                memory_type=memory_type,
                content=content,
                importance=importance,
                confidence=confidence,
                is_active=True,
                expires_at=expires_at,
            )
        )
        processed += 1

    if processed > 0:
        await session.commit()
    return processed


async def deactivate_identity_memories_for_user(
    session: AsyncSession,
    *,
    user_id: int,
) -> int:
    stmt = select(LongTermMemory).where(
        LongTermMemory.user_id == user_id,
        LongTermMemory.is_active.is_(True),
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
            row.is_active = False
            row.updated_at = now
            session.add(row)
            changed += 1
    return changed


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
            LongTermMemory.is_active.is_(True),
            or_(LongTermMemory.expires_at.is_(None), LongTermMemory.expires_at > now),
        )
        .order_by(LongTermMemory.importance.desc(), LongTermMemory.updated_at.desc())
        .limit(scan_limit)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    if not rows:
        return []

    ranked = sorted(rows, key=lambda item: _memory_score(query, item), reverse=True)[:top_k]
    result: list[dict[str, Any]] = []
    for row in ranked:
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
