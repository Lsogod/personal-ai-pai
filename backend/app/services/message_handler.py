from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_or_create_user
from app.core.config import get_settings
from app.schemas.unified import UnifiedMessage
from app.services.commands.conversation import handle_conversation_command
from app.services.llm import get_llm
from app.services.memory import (
    extract_memory_candidates,
    list_long_term_memories,
    upsert_long_term_memories,
)
from app.services.sender import UnifiedSender
from app.services.scheduler import get_scheduler
from app.graph.workflow import get_graph
from app.services.dedup import is_duplicate
from app.services.audit import log_event
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user import User
from app.services.conversations import (
    apply_assistant_message_updates,
    apply_user_message_updates,
    ensure_active_conversation,
)
from app.services.realtime import get_notification_hub
from app.db.session import AsyncSessionLocal
from app.services.runtime_context import (
    set_session,
    reset_session,
    set_scheduler,
    reset_scheduler,
    set_tool_user_id,
    reset_tool_user_id,
    set_tool_platform,
    reset_tool_platform,
    set_tool_conversation_id,
    reset_tool_conversation_id,
    set_llm_streamer,
    reset_llm_streamer,
    set_llm_stream_nodes,
    reset_llm_stream_nodes,
)


_sender = UnifiedSender()
_scheduler = get_scheduler()
logger = logging.getLogger(__name__)
settings = get_settings()
UNSUPPORTED_REBIND_TEXT = "当前版本暂不支持换绑/解绑。你仍可使用 `/bind new` 与 `/bind <6位码>` 进行账号绑定合并。"
_memory_tasks: set[asyncio.Task[Any]] = set()
_memory_debounce_tasks: dict[tuple[int, int], asyncio.Task[Any]] = {}
_memory_debounce_payloads: dict[tuple[int, int], dict[str, Any]] = {}
REBIND_HINT_PATTERN = re.compile(r"(换绑|改绑|解绑|重新绑定|rebind|unbind|re-bind|un-bind)", re.IGNORECASE)
MEMORY_STATUS_PENDING = "PENDING"
MEMORY_STATUS_PROCESSED = "PROCESSED"
MEMORY_STATUS_FAILED = "FAILED"
MEMORY_STATUS_SKIPPED = "SKIPPED"
MEMORY_DONE_STATUSES = {MEMORY_STATUS_PROCESSED, MEMORY_STATUS_SKIPPED}


def _pending_memory_clause():
    return or_(
        Message.memory_status.is_(None),
        Message.memory_status.in_([MEMORY_STATUS_PENDING, MEMORY_STATUS_FAILED]),
    )


def _today_start_utc_naive() -> datetime:
    tz = ZoneInfo(settings.timezone)
    now_local = datetime.now(tz)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def _month_start_utc_naive() -> datetime:
    tz = ZoneInfo(settings.timezone)
    now_local = datetime.now(tz)
    start_local = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start_local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def _to_client_tz_iso(value: datetime | None) -> str:
    if value is None:
        return ""
    tz = ZoneInfo(settings.timezone)
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo("UTC"))
    return value.astimezone(tz).isoformat(timespec="seconds")


class RebindIntentExtraction(BaseModel):
    block: bool = Field(default=False)


async def _is_rebind_natural_intent(text: str) -> bool:
    content = (text or "").strip()
    if not content or content.startswith("/"):
        return False
    # Fast pre-filter: only call LLM when text is likely related to rebind.
    if not REBIND_HINT_PATTERN.search(content):
        return False
    llm = get_llm(node_name="message_handler")
    runnable = llm.with_structured_output(RebindIntentExtraction)
    system = SystemMessage(
        content=(
            "你是意图分类器。判断用户是否在表达“换绑/解绑/重新绑定账号”的诉求。"
            "请仅返回 JSON 结构化字段 block。"
            "只有在用户明确要求更换绑定关系、解除绑定、改绑账号时 block=true；"
            "普通咨询、记账、提醒等场景都应 block=false。"
        )
    )
    human = HumanMessage(content=content)
    try:
        parsed = await asyncio.wait_for(
            runnable.ainvoke([system, human]),
            timeout=max(1, int(get_settings().rebind_intent_timeout_sec or 4)),
        )
        return bool(getattr(parsed, "block", False) is True)
    except Exception:
        return False


async def _load_context_messages(
    session: AsyncSession,
    user_id: int,
    conversation_id: int,
    limit: int | None = 20,
    max_message_id: int | None = None,
) -> list[dict[str, str]]:
    stmt = (
        select(Message)
        .where(
            Message.user_id == user_id,
            Message.conversation_id == conversation_id,
        )
        .order_by(Message.id.desc())
    )
    if isinstance(max_message_id, int) and max_message_id > 0:
        stmt = stmt.where(Message.id <= max_message_id)
    if isinstance(limit, int) and limit > 0:
        stmt = stmt.limit(limit)
    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    rows.reverse()

    context_messages: list[dict[str, str]] = []
    for row in rows:
        content = (row.content or "").strip()
        if not content:
            continue
        context_messages.append(
            {
                "role": (row.role or "user").strip().lower(),
                "content": content,
                "created_at": _to_client_tz_iso(row.created_at),
            }
        )
    return context_messages


def _track_background_task(task: asyncio.Task[Any]) -> None:
    _memory_tasks.add(task)

    def _on_done(done: asyncio.Task[Any]) -> None:
        _memory_tasks.discard(done)
        try:
            done.result()
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("background task failed")

    task.add_done_callback(_on_done)


async def _mark_conversation_memory_processed(
    *,
    user_id: int,
    conversation_id: int,
    user_message_id: int | None,
) -> None:
    if user_message_id is None:
        return
    try:
        message_id = int(user_message_id)
    except Exception:
        return
    if message_id <= 0:
        return

    try:
        async with AsyncSessionLocal() as mark_session:
            message = await mark_session.get(Message, message_id)
            if (
                message is None
                or int(message.user_id or 0) != int(user_id)
                or int(message.conversation_id or 0) != int(conversation_id)
                or str(message.role or "").strip().lower() != "user"
            ):
                return
            message.memory_status = MEMORY_STATUS_PROCESSED
            message.memory_processed_at = datetime.now(timezone.utc)
            message.memory_error = None
            mark_session.add(message)
            conversation = await mark_session.get(Conversation, int(conversation_id))
            if conversation is None or int(conversation.user_id or 0) != int(user_id):
                return
            prev = int(conversation.memory_last_processed_message_id or 0)
            if prev >= message_id and conversation.memory_extracted_at is not None:
                await mark_session.commit()
                return
            if prev < message_id:
                conversation.memory_last_processed_message_id = message_id
            conversation.memory_extracted_at = datetime.now(timezone.utc)
            mark_session.add(conversation)
            await mark_session.commit()
    except Exception:
        logger.exception(
            "mark conversation memory processed failed: user_id=%s conversation_id=%s message_id=%s",
            user_id,
            conversation_id,
            user_message_id,
        )


async def _mark_message_memory_failed(
    *,
    user_id: int,
    conversation_id: int,
    user_message_id: int | None,
    error: str = "",
) -> None:
    if user_message_id is None:
        return
    try:
        message_id = int(user_message_id)
    except Exception:
        return
    if message_id <= 0:
        return
    try:
        async with AsyncSessionLocal() as mark_session:
            message = await mark_session.get(Message, message_id)
            if (
                message is None
                or int(message.user_id or 0) != int(user_id)
                or int(message.conversation_id or 0) != int(conversation_id)
                or str(message.role or "").strip().lower() != "user"
            ):
                return
            message.memory_status = MEMORY_STATUS_FAILED
            message.memory_error = (error or "").strip()[:500] or None
            mark_session.add(message)
            await mark_session.commit()
    except Exception:
        logger.exception(
            "mark message memory failed status failed: user_id=%s conversation_id=%s message_id=%s",
            user_id,
            conversation_id,
            user_message_id,
        )


async def _mark_message_memory_skipped(
    *,
    user_id: int,
    conversation_id: int,
    user_message_id: int | None,
) -> None:
    if user_message_id is None:
        return
    try:
        message_id = int(user_message_id)
    except Exception:
        return
    if message_id <= 0:
        return
    try:
        async with AsyncSessionLocal() as mark_session:
            message = await mark_session.get(Message, message_id)
            if (
                message is None
                or int(message.user_id or 0) != int(user_id)
                or int(message.conversation_id or 0) != int(conversation_id)
                or str(message.role or "").strip().lower() != "user"
            ):
                return
            message.memory_status = MEMORY_STATUS_SKIPPED
            message.memory_processed_at = datetime.now(timezone.utc)
            message.memory_error = None
            mark_session.add(message)
            conversation = await mark_session.get(Conversation, int(conversation_id))
            if conversation is not None and int(conversation.user_id or 0) == int(user_id):
                prev = int(conversation.memory_last_processed_message_id or 0)
                if prev < message_id:
                    conversation.memory_last_processed_message_id = message_id
                conversation.memory_extracted_at = datetime.now(timezone.utc)
                mark_session.add(conversation)
            await mark_session.commit()
    except Exception:
        logger.exception(
            "mark message memory skipped failed: user_id=%s conversation_id=%s message_id=%s",
            user_id,
            conversation_id,
            user_message_id,
        )


async def _has_pending_memory_messages(
    *,
    session: AsyncSession,
    user_id: int,
    conversation_id: int,
) -> bool:
    stmt = (
        select(func.count(Message.id))
        .where(
            Message.user_id == int(user_id),
            Message.conversation_id == int(conversation_id),
            Message.role == "user",
            _pending_memory_clause(),
        )
    )
    pending_count = int((await session.execute(stmt)).scalar_one() or 0)
    return pending_count > 0


def _build_chat_debug_payload(graph_result: Any) -> dict[str, Any] | None:
    if not isinstance(graph_result, dict):
        return None

    payload: dict[str, Any] = {}
    intent = str(graph_result.get("intent") or "").strip()
    if intent:
        payload["route_intent"] = intent

    extra = graph_result.get("extra")
    if isinstance(extra, dict):
        complex_task = extra.get("complex_task")
        if isinstance(complex_task, dict):
            block: dict[str, Any] = {}
            for key in [
                "reason",
                "completed",
                "outcome_reason",
                "fallback_mode",
                "fallback_node_action",
                "tool_calls_total",
            ]:
                if key in complex_task:
                    block[key] = complex_task.get(key)
            if block:
                payload["complex_task"] = block

        pending = extra.get("complex_task_pending")
        if isinstance(pending, dict):
            payload["complex_task_pending"] = pending

    return payload or None


def _friendly_graph_error_message(exc: Exception) -> tuple[str, str]:
    text = str(exc or "").strip()
    lowered = text.lower()
    if (
        "allocationquota.freetieronly" in lowered
        or "free tier of the model has been exhausted" in lowered
        or "insufficient_quota" in lowered
    ):
        return (
            "当前模型额度已用尽（free tier），请在模型管理端关闭“仅免费额度”或切换到可用付费模型后再试。",
            "llm_quota_exhausted",
        )
    if "rate limit" in lowered or "too many requests" in lowered:
        return ("当前模型请求过于频繁，请稍后重试。", "llm_rate_limited")
    if "permissiondeniederror" in lowered or "error code: 403" in lowered:
        return ("当前模型调用被拒绝（403），请检查模型权限或账号额度后重试。", "llm_permission_denied")
    return ("处理消息时发生错误，请稍后重试。", "graph_invoke_failed")


async def _run_long_term_memory_pipeline(
    *,
    user_id: int,
    conversation_id: int,
    user_message_id: int | None,
    user_text: str,
    assistant_outputs: list[str],
    conversation_summary: str,
    conversation_context_messages: list[dict[str, str]] | None,
    user_nickname: str,
    user_ai_name: str,
    user_ai_emoji: str,
) -> bool:
    if user_message_id is not None:
        try:
            async with AsyncSessionLocal() as check_session:
                message = await check_session.get(Message, int(user_message_id))
                if (
                    message is not None
                    and int(message.user_id or 0) == int(user_id)
                    and int(message.conversation_id or 0) == int(conversation_id)
                    and str(message.role or "").strip().lower() == "user"
                ):
                    status = str(message.memory_status or "").strip().upper()
                    if status in MEMORY_DONE_STATUSES:
                        logger.info(
                            "long-term memory pipeline skipped(already processed): user_id=%s conversation_id=%s message_id=%s status=%s",
                            user_id,
                            conversation_id,
                            user_message_id,
                            status,
                        )
                        return True
        except Exception:
            logger.exception(
                "long-term memory pre-check failed: user_id=%s conversation_id=%s message_id=%s",
                user_id,
                conversation_id,
                user_message_id,
            )

    extract_timeout_sec = max(3, int(get_settings().long_term_memory_extract_timeout_sec or 20))
    upsert_timeout_sec = max(3, int(get_settings().long_term_memory_upsert_timeout_sec or 12))
    logger.info(
        "long-term memory pipeline start: user_id=%s conversation_id=%s source_message_id=%s extract_timeout=%ss upsert_timeout=%ss",
        user_id,
        conversation_id,
        user_message_id,
        extract_timeout_sec,
        upsert_timeout_sec,
    )
    candidates: list[dict[str, Any]] = []
    try:
        candidates = await asyncio.wait_for(
            extract_memory_candidates(
                user_text=user_text,
                assistant_text="\n".join(assistant_outputs),
                conversation_summary=conversation_summary,
                conversation_messages=conversation_context_messages,
            ),
            timeout=extract_timeout_sec,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "long-term memory extract timed out: user_id=%s conversation_id=%s",
            user_id,
            conversation_id,
        )
        await _mark_message_memory_failed(
            user_id=user_id,
            conversation_id=conversation_id,
            user_message_id=user_message_id,
            error="extract_timeout",
        )
        return False
    except Exception:
        logger.exception("long-term memory extract failed: user_id=%s", user_id)
        await _mark_message_memory_failed(
            user_id=user_id,
            conversation_id=conversation_id,
            user_message_id=user_message_id,
            error="extract_failed",
        )
        return False

    if not candidates:
        logger.info(
            "long-term memory pipeline extracted 0 candidates: user_id=%s conversation_id=%s",
            user_id,
            conversation_id,
        )
        await _mark_conversation_memory_processed(
            user_id=user_id,
            conversation_id=conversation_id,
            user_message_id=user_message_id,
        )
        return True

    try:
        async with AsyncSessionLocal() as mem_session:
            processed = await asyncio.wait_for(
                upsert_long_term_memories(
                    session=mem_session,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    source_message_id=user_message_id,
                    candidates=candidates,
                    user_text=user_text,
                    user_nickname=user_nickname,
                    user_ai_name=user_ai_name,
                    user_ai_emoji=user_ai_emoji,
                ),
                timeout=upsert_timeout_sec,
            )
            logger.info(
                "long-term memory pipeline upsert done: user_id=%s conversation_id=%s candidates=%s processed=%s",
                user_id,
                conversation_id,
                len(candidates),
                int(processed or 0),
            )
            await _mark_conversation_memory_processed(
                user_id=user_id,
                conversation_id=conversation_id,
                user_message_id=user_message_id,
            )
    except asyncio.TimeoutError:
        logger.warning(
            "long-term memory upsert timed out: user_id=%s conversation_id=%s",
            user_id,
            conversation_id,
        )
        await _mark_message_memory_failed(
            user_id=user_id,
            conversation_id=conversation_id,
            user_message_id=user_message_id,
            error="upsert_timeout",
        )
        return False
    except Exception:
        logger.exception("long-term memory upsert failed: user_id=%s", user_id)
        await _mark_message_memory_failed(
            user_id=user_id,
            conversation_id=conversation_id,
            user_message_id=user_message_id,
            error="upsert_failed",
        )
        return False
    return True


def _schedule_long_term_memory_pipeline(
    *,
    user_id: int,
    conversation_id: int,
    user_message_id: int | None,
    user_text: str,
    assistant_outputs: list[str],
    conversation_summary: str,
    user_nickname: str,
    user_ai_name: str,
    user_ai_emoji: str,
) -> None:
    key = (int(user_id), int(conversation_id))
    _memory_debounce_payloads[key] = {
        "user_id": int(user_id),
        "conversation_id": int(conversation_id),
    }
    previous = _memory_debounce_tasks.get(key)
    if previous is not None and not previous.done():
        previous.cancel()
    task = asyncio.create_task(_run_debounced_long_term_memory_pipeline(key))
    _memory_debounce_tasks[key] = task
    _track_background_task(task)


async def _run_debounced_long_term_memory_pipeline(key: tuple[int, int]) -> None:
    try:
        delay_sec = max(0, int(get_settings().long_term_memory_debounce_sec or 0))
        if delay_sec > 0:
            await asyncio.sleep(delay_sec)
        payload = dict(_memory_debounce_payloads.get(key) or {})
        if not payload:
            return
        await _run_session_long_term_memory_pipeline(
            user_id=int(payload.get("user_id") or 0),
            conversation_id=int(payload.get("conversation_id") or 0),
        )
    except asyncio.CancelledError:
        return
    except Exception:
        logger.exception("debounced memory pipeline failed: key=%s", key)
    finally:
        current = _memory_debounce_tasks.get(key)
        if current is asyncio.current_task():
            _memory_debounce_tasks.pop(key, None)
            _memory_debounce_payloads.pop(key, None)


async def _run_session_long_term_memory_pipeline(*, user_id: int, conversation_id: int) -> None:
    try:
        if not get_settings().long_term_memory_enabled:
            return
        logger.info(
            "session memory backfill start: user_id=%s conversation_id=%s",
            user_id,
            conversation_id,
        )
        async with AsyncSessionLocal() as mem_session:
            conversation = await mem_session.get(Conversation, int(conversation_id))
            if conversation is None or int(conversation.user_id or 0) != int(user_id):
                return
            user = await mem_session.get(User, int(user_id))
            if user is None:
                return
            msg_limit = max(1, int(get_settings().long_term_memory_scan_max_messages_per_conversation or 30))
            pending_user_stmt = (
                select(Message)
                .where(
                    Message.user_id == int(user_id),
                    Message.conversation_id == int(conversation_id),
                    Message.role == "user",
                    _pending_memory_clause(),
                )
                .order_by(Message.id.asc())
                .limit(msg_limit)
            )
            pending_user_rows = list((await mem_session.execute(pending_user_stmt)).scalars().all())
            if not pending_user_rows:
                logger.info(
                    "session memory backfill skipped(no pending user msg): user_id=%s conversation_id=%s",
                    user_id,
                    conversation_id,
                )
                return
            completed_count = 0
            skipped_count = 0
            failed_count = 0
            for row in pending_user_rows:
                message_id = int(row.id or 0)
                seed_user_text = str(row.content or "").strip()
                if message_id <= 0:
                    continue
                if not seed_user_text or seed_user_text.startswith("/"):
                    await _mark_message_memory_skipped(
                        user_id=int(user_id),
                        conversation_id=int(conversation_id),
                        user_message_id=message_id,
                    )
                    skipped_count += 1
                    continue

                context_messages = await _load_context_messages(
                    session=mem_session,
                    user_id=int(user_id),
                    conversation_id=int(conversation_id),
                    limit=None,
                    max_message_id=message_id,
                )
                if len(context_messages) > msg_limit * 8:
                    context_messages = context_messages[-(msg_limit * 8) :]

                assistant_outputs: list[str] = []
                for item in reversed(context_messages):
                    if str(item.get("role") or "").strip().lower() != "assistant":
                        continue
                    text = str(item.get("content") or "").strip()
                    if text:
                        assistant_outputs.append(text)
                    if len(assistant_outputs) >= 6:
                        break

                completed = await _run_long_term_memory_pipeline(
                    user_id=int(user_id),
                    conversation_id=int(conversation_id),
                    user_message_id=message_id,
                    user_text=seed_user_text,
                    assistant_outputs=list(reversed(assistant_outputs)),
                    conversation_summary=str(conversation.summary or "").strip(),
                    conversation_context_messages=context_messages,
                    user_nickname=str(user.nickname or ""),
                    user_ai_name=str(user.ai_name or ""),
                    user_ai_emoji=str(user.ai_emoji or ""),
                )
                if not completed:
                    failed_count += 1
                    logger.warning(
                        "session memory backfill stopped on failure: user_id=%s conversation_id=%s message_id=%s",
                        user_id,
                        conversation_id,
                        message_id,
                    )
                    break
                completed_count += 1

            logger.info(
                "session memory backfill finished: user_id=%s conversation_id=%s completed=%s skipped=%s failed=%s",
                user_id,
                conversation_id,
                completed_count,
                skipped_count,
                failed_count,
            )
    except Exception:
        logger.exception(
            "session memory extract failed: user_id=%s conversation_id=%s",
            user_id,
            conversation_id,
        )


def schedule_conversation_memory_backfill(*, user_id: int, conversation_id: int) -> None:
    task = asyncio.create_task(
        _run_session_long_term_memory_pipeline(
            user_id=int(user_id),
            conversation_id=int(conversation_id),
        )
    )
    _track_background_task(task)


async def scan_unprocessed_memory_messages(
    *,
    max_conversations: int,
    max_messages_per_conversation: int,
) -> dict[str, int]:
    scanned_conversations = 0
    processed_messages = 0
    skipped_messages = 0
    failed_messages = 0

    conv_limit = max(1, int(max_conversations))
    msg_limit = max(1, int(max_messages_per_conversation))
    async with AsyncSessionLocal() as session:
        conv_stmt = (
            select(Conversation)
            .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
            .limit(conv_limit)
        )
        conversations = list((await session.execute(conv_stmt)).scalars().all())
        conv_payloads: list[dict[str, Any]] = []
        for conversation in conversations:
            user_id = int(conversation.user_id or 0)
            conversation_id = int(conversation.id or 0)
            if user_id <= 0 or conversation_id <= 0:
                continue
            msg_stmt = (
                select(Message)
                .where(
                    Message.user_id == user_id,
                    Message.conversation_id == conversation_id,
                    Message.role == "user",
                    _pending_memory_clause(),
                )
                .order_by(Message.id.asc())
                .limit(msg_limit)
            )
            user_rows = list((await session.execute(msg_stmt)).scalars().all())
            if not user_rows:
                continue
            user = await session.get(User, user_id)
            if user is None:
                continue
            scanned_conversations += 1
            conv_payloads.append(
                {
                    "conversation": conversation,
                    "user": user,
                    "rows": user_rows,
                    "index": 0,
                    "blocked": False,  # Stop this conversation for current scan after first failure.
                }
            )

        # Round-robin over conversations to keep fairness while still allowing >1 message per conversation.
        while True:
            made_progress = False
            for payload in conv_payloads:
                if bool(payload.get("blocked")):
                    continue
                rows = list(payload.get("rows") or [])
                idx = int(payload.get("index") or 0)
                if idx >= len(rows):
                    continue
                row = rows[idx]
                payload["index"] = idx + 1
                made_progress = True

                conversation = payload["conversation"]
                user = payload["user"]
                user_id = int(user.id or 0)
                conversation_id = int(conversation.id or 0)

                message_id = int(row.id or 0)
                text = str(row.content or "").strip()
                if message_id <= 0:
                    continue
                if not text or text.startswith("/"):
                    await _mark_message_memory_skipped(
                        user_id=user_id,
                        conversation_id=conversation_id,
                        user_message_id=message_id,
                    )
                    skipped_messages += 1
                    continue
                context_messages = await _load_context_messages(
                    session=session,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    limit=None,
                    max_message_id=message_id,
                )
                if msg_limit > 0 and len(context_messages) > msg_limit * 8:
                    context_messages = context_messages[-(msg_limit * 8) :]
                assistant_outputs: list[str] = []
                for item in reversed(context_messages):
                    if str(item.get("role") or "").strip().lower() != "assistant":
                        continue
                    content = str(item.get("content") or "").strip()
                    if content:
                        assistant_outputs.append(content)
                    if len(assistant_outputs) >= 6:
                        break

                completed = await _run_long_term_memory_pipeline(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    user_message_id=message_id,
                    user_text=text,
                    assistant_outputs=list(reversed(assistant_outputs)),
                    conversation_summary=str(conversation.summary or "").strip(),
                    conversation_context_messages=context_messages,
                    user_nickname=str(user.nickname or ""),
                    user_ai_name=str(user.ai_name or ""),
                    user_ai_emoji=str(user.ai_emoji or ""),
                )
                if completed:
                    processed_messages += 1
                else:
                    failed_messages += 1
                    payload["blocked"] = True

            if not made_progress:
                break

    logger.info(
        "memory scan finished: scanned_conversations=%s processed_messages=%s skipped_messages=%s failed_messages=%s",
        scanned_conversations,
        processed_messages,
        skipped_messages,
        failed_messages,
    )
    return {
        "scanned_conversations": scanned_conversations,
        "processed_messages": processed_messages,
        "skipped_messages": skipped_messages,
        "failed_messages": failed_messages,
    }


async def handle_message(
    platform: str,
    normalized: dict[str, Any],
    session: AsyncSession,
) -> dict[str, Any]:
    if not normalized.get("platform_id"):
        return {"ok": False, "error": "missing platform_id"}

    if await is_duplicate(platform, normalized.get("message_id")):
        return {"ok": True, "dedup": True}

    user, _ = await get_or_create_user(session, platform, normalized["platform_id"])
    conversation = await ensure_active_conversation(session, user)
    user_id = user.id
    user_uuid = user.uuid
    user_setup_stage = user.setup_stage
    reply_platform = platform
    reply_platform_id = normalized["platform_id"]

    message = UnifiedMessage(
        platform=platform,
        user_uuid=user_uuid,
        content=normalized.get("content") or "",
        image_urls=normalized.get("image_urls") or [],
        raw_data=normalized.get("raw_data", {}),
        message_id=normalized.get("message_id"),
        event_ts=normalized.get("event_ts"),
    )

    async def _emit_block_notice(text: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        await _sender.send_text(reply_platform, reply_platform_id, text)
        await log_event(
            session,
            action="message_sent",
            platform=platform,
            user_id=user_id,
            detail={"content": text, "conversation_id": conversation.id, **(extra or {})},
        )
        conversation.last_message_at = datetime.now(timezone.utc)
        session.add(conversation)
        session.add(
            Message(
                user_id=user_id,
                conversation_id=conversation.id,
                role="assistant",
                content=text,
                platform=platform,
            )
        )
        await session.commit()
        if platform != "web":
            await get_notification_hub().send_to_user(
                user_id,
                {
                    "type": "message",
                    "role": "assistant",
                    "content": text,
                    "platform": platform,
                    "conversation_id": conversation.id,
                    "created_at": _to_client_tz_iso(datetime.now(timezone.utc)),
                },
            )
        return {"ok": True, "responses": [text]}

    if bool(user.is_blocked):
        blocked_msg = "你的账号已被管理员禁用。"
        reason = (user.blocked_reason or "").strip()
        if reason:
            blocked_msg = f"{blocked_msg} 原因：{reason}"
        await log_event(
            session,
            action="message_blocked",
            platform=platform,
            user_id=user_id,
            detail={
                "reason": reason,
                "message_id": message.message_id,
                "conversation_id": conversation.id,
            },
        )
        result = await _emit_block_notice(blocked_msg, {"blocked": True})
        result["blocked"] = True
        return result

    if int(user.daily_message_limit or 0) > 0:
        today_start_utc = _today_start_utc_naive()
        count_result = await session.execute(
            select(func.count(Message.id)).where(
                Message.user_id == user_id,
                Message.role == "user",
                Message.created_at >= today_start_utc,
            )
        )
        sent_today = int(count_result.scalar_one() or 0)
        if sent_today >= int(user.daily_message_limit):
            limit_msg = f"你今日消息配额已用完（{int(user.daily_message_limit)} 条），请明天再试。"
            await log_event(
                session,
                action="message_quota_blocked",
                platform=platform,
                user_id=user_id,
                detail={
                    "daily_message_limit": int(user.daily_message_limit),
                    "sent_today": sent_today,
                    "message_id": message.message_id,
                    "conversation_id": conversation.id,
                },
            )
            result = await _emit_block_notice(limit_msg, {"quota_scope": "daily", "quota_blocked": True})
            result["quota_blocked"] = True
            return result

    if int(user.monthly_message_limit or 0) > 0:
        month_start_utc = _month_start_utc_naive()
        month_count_result = await session.execute(
            select(func.count(Message.id)).where(
                Message.user_id == user_id,
                Message.role == "user",
                Message.created_at >= month_start_utc,
            )
        )
        sent_this_month = int(month_count_result.scalar_one() or 0)
        if sent_this_month >= int(user.monthly_message_limit):
            limit_msg = f"你本月消息配额已用完（{int(user.monthly_message_limit)} 条），请下月再试。"
            await log_event(
                session,
                action="message_quota_blocked",
                platform=platform,
                user_id=user_id,
                detail={
                    "scope": "monthly",
                    "monthly_message_limit": int(user.monthly_message_limit),
                    "sent_this_month": sent_this_month,
                    "message_id": message.message_id,
                    "conversation_id": conversation.id,
                },
            )
            result = await _emit_block_notice(limit_msg, {"quota_scope": "monthly", "quota_blocked": True})
            result["quota_blocked"] = True
            return result

    state = {
        "user_id": user_id,
        "conversation_id": conversation.id,
        "user_setup_stage": user_setup_stage,
        "message": message,
        "responses": [],
    }

    await log_event(
        session,
        action="message_received",
        platform=platform,
        user_id=user_id,
        detail={
            "message_id": message.message_id,
            "content": message.content,
            "conversation_id": conversation.id,
        },
    )
    apply_user_message_updates(conversation, message.content)
    session.add(conversation)
    user_message_row = Message(
        user_id=user_id,
        conversation_id=conversation.id,
        role="user",
        content=message.content,
        platform=platform,
        memory_status=MEMORY_STATUS_PENDING,
    )
    session.add(user_message_row)
    await session.commit()
    if platform != "web":
        await get_notification_hub().send_to_user(
            user_id,
            {
                "type": "message",
                "role": "user",
                "content": message.content,
                "platform": platform,
                "conversation_id": conversation.id,
                "created_at": _to_client_tz_iso(datetime.now(timezone.utc)),
            },
        )

    debug_payload: dict[str, Any] | None = None
    miniapp_stream_enabled = platform == "miniapp"
    miniapp_stream_used = False
    miniapp_stream_chunks: list[str] = []
    miniapp_stream_id = f"miniapp:{conversation.id}:{int(user_message_row.id or 0)}"
    miniapp_stream_created_at = _to_client_tz_iso(datetime.now(timezone.utc))

    async def _emit_miniapp_stream_chunk(chunk: str) -> None:
        nonlocal miniapp_stream_used
        text = str(chunk or "")
        if not text:
            return

        # Tool events use a special prefix and are sent as a separate WS type.
        TOOL_EVENT_PREFIX = "\x00TOOL_EVENT:"
        if text.startswith(TOOL_EVENT_PREFIX):
            import json as _json

            try:
                tool_payload = _json.loads(text[len(TOOL_EVENT_PREFIX):])
            except Exception:
                return
            miniapp_stream_used = True
            await get_notification_hub().send_to_user(
                user_id,
                {
                    "type": "tool_event",
                    **tool_payload,
                    "stream_id": miniapp_stream_id,
                    "platform": platform,
                    "conversation_id": conversation.id,
                },
            )
            return

        miniapp_stream_chunks.append(text)
        miniapp_stream_used = True
        await get_notification_hub().send_to_user(
            user_id,
            {
                "type": "message_chunk",
                "stream_id": miniapp_stream_id,
                "chunk": text,
                "done": False,
                "platform": platform,
                "conversation_id": conversation.id,
                "created_at": miniapp_stream_created_at,
            },
        )

    if await _is_rebind_natural_intent(message.content):
        responses = [UNSUPPORTED_REBIND_TEXT]
        skip_summary_update = False
    else:
        command_result = await handle_conversation_command(
            session=session,
            user=user,
            conversation=conversation,
            text=message.content,
        )

        if command_result is not None:
            responses, response_conversation = command_result
            conversation = response_conversation
            skip_summary_update = True
        else:
            skip_summary_update = False
            # Inject all active long-term memories into graph context.
            # No relevance scoring/filtering is applied for this path.
            long_term_memories = await list_long_term_memories(
                session=session,
                user_id=user_id,
                limit=None,
            )
            state["extra"] = {
                "conversation_summary": (conversation.summary or "").strip(),
                "context_messages": await _load_context_messages(
                    session=session,
                    user_id=user_id,
                    conversation_id=conversation.id,
                    limit=20,
                ),
                "long_term_memories": long_term_memories,
            }
            graph = await get_graph()
            graph_config = {"configurable": {"thread_id": f"{user_uuid}:{conversation.id}"}}
            # Preserve node-produced conversation-scoped context (e.g. last query result ids)
            # across turns, while always refreshing runtime context fields for this turn.
            try:
                state_snapshot = await graph.aget_state(graph_config)
                prev_values = getattr(state_snapshot, "values", {}) or {}
                prev_extra = prev_values.get("extra")
                if isinstance(prev_extra, dict) and isinstance(state.get("extra"), dict):
                    merged_extra = dict(prev_extra)
                    merged_extra.update(dict(state.get("extra") or {}))
                    state["extra"] = merged_extra
            except Exception:
                pass
            session_token = set_session(session)
            scheduler_token = set_scheduler(_scheduler)
            tool_user_token = set_tool_user_id(user_id)
            tool_platform_token = set_tool_platform(platform)
            tool_conv_token = set_tool_conversation_id(conversation.id)
            stream_token = None
            stream_nodes_token = None
            if miniapp_stream_enabled:
                stream_token = set_llm_streamer(_emit_miniapp_stream_chunk)
                # Don't set stream_nodes — main_agent.astream_events handles
                # streaming itself. Setting stream_nodes would cause
                # TrackingChatOpenAI to ALSO stream, resulting in double output.
            try:
                try:
                    result = await graph.ainvoke(
                        state,
                        config=graph_config,
                    )
                    responses = result.get("responses") or []
                    debug_payload = _build_chat_debug_payload(result)
                    if debug_payload is None:
                        try:
                            state_snapshot = await graph.aget_state(graph_config)
                            final_values = getattr(state_snapshot, "values", {}) or {}
                            debug_payload = _build_chat_debug_payload(final_values)
                        except Exception:
                            debug_payload = None
                except Exception as exc:
                    logger.exception("graph invoke failed: platform=%s user_id=%s", platform, user_id)
                    error_text, error_code = _friendly_graph_error_message(exc)
                    responses = [error_text]
                    debug_payload = {"route_intent": "unknown", "error": error_code}
            finally:
                if stream_nodes_token is not None:
                    reset_llm_stream_nodes(stream_nodes_token)
                if stream_token is not None:
                    reset_llm_streamer(stream_token)
                reset_tool_conversation_id(tool_conv_token)
                reset_tool_platform(tool_platform_token)
                reset_tool_user_id(tool_user_token)
                reset_scheduler(scheduler_token)
                reset_session(session_token)

    if miniapp_stream_enabled and responses:
        joined = "\n".join(responses)
        streamed_text = "".join(miniapp_stream_chunks)
        if joined and not streamed_text:
            await _emit_miniapp_stream_chunk(joined)
        elif joined and streamed_text and joined.startswith(streamed_text):
            suffix = joined[len(streamed_text) :]
            if suffix:
                await _emit_miniapp_stream_chunk(suffix)
        if joined:
            miniapp_stream_used = True
        if miniapp_stream_used:
            await get_notification_hub().send_to_user(
                user_id,
                {
                    "type": "message_chunk",
                    "stream_id": miniapp_stream_id,
                    "chunk": "",
                    "done": True,
                    "platform": platform,
                    "conversation_id": conversation.id,
                    "created_at": miniapp_stream_created_at,
                },
            )

    assistant_outputs: list[str] = []
    for text in responses:
        await _sender.send_text(reply_platform, reply_platform_id, text)
        await log_event(
            session,
            action="message_sent",
            platform=platform,
            user_id=user_id,
            detail={"content": text, "conversation_id": conversation.id},
        )
        if skip_summary_update:
            conversation.last_message_at = datetime.now(timezone.utc)
        else:
            apply_assistant_message_updates(conversation, text)
        session.add(conversation)
        session.add(
            Message(
                user_id=user_id,
                conversation_id=conversation.id,
                role="assistant",
                content=text,
                platform=platform,
            )
        )
        await session.commit()
        assistant_outputs.append(text)
        if platform != "web" and not (platform == "miniapp" and miniapp_stream_used):
            await get_notification_hub().send_to_user(
                user_id,
                {
                    "type": "message",
                    "role": "assistant",
                    "content": text,
                    "platform": platform,
                    "conversation_id": conversation.id,
                    "created_at": _to_client_tz_iso(datetime.now(timezone.utc)),
                },
            )

    if (
        settings.long_term_memory_enabled
        and assistant_outputs
        and message.content
        and not str(message.content).strip().startswith("/")
    ):
        _schedule_long_term_memory_pipeline(
            user_id=user_id,
            conversation_id=conversation.id,
            user_message_id=user_message_row.id,
            user_text=message.content,
            assistant_outputs=assistant_outputs,
            conversation_summary=(conversation.summary or "").strip(),
            user_nickname=user.nickname or "",
            user_ai_name=user.ai_name or "",
            user_ai_emoji=user.ai_emoji or "",
        )

    result_payload: dict[str, Any] = {"ok": True, "responses": responses}
    if debug_payload is not None:
        result_payload["debug"] = debug_payload
    return result_payload
