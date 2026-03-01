from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from typing import List
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session, AsyncSessionLocal
from app.models.user import User, SetupStage
from app.models.message import Message
from app.models.ledger import Ledger
from app.models.schedule import Schedule
from app.models.identity import UserIdentity
from app.models.feedback import UserFeedback
from app.models.reminder_delivery import ReminderDelivery
from app.models.app_setting import AppSetting
from app.schemas.auth import RegisterRequest, LoginRequest, TokenResponse
from app.schemas.auth import MiniappLoginRequest, MiniappTokenResponse
from app.schemas.binding import (
    BindCodeConsumeRequest,
    BindCodeConsumeResponse,
    BindCodeCreateRequest,
    BindCodeCreateResponse,
)
from app.schemas.calendar import (
    CalendarDayResponse,
    CalendarLedgerItem,
    CalendarResponse,
    CalendarScheduleItem,
)
from app.schemas.chat import ChatSendRequest, ChatSendResponse, ProfileResponse
from app.schemas.feedback import FeedbackCreateRequest, FeedbackCreateResponse
from app.schemas.conversation import ConversationCreateRequest, ConversationResponse
from app.schemas.conversation import ConversationDeleteResponse, ConversationUpdateRequest
from app.schemas.ledger import LedgerCreateRequest, LedgerDeleteResponse, LedgerItemResponse, LedgerUpdateRequest
from app.schemas.mcp import MCPFetchRequest, MCPFetchResponse, MCPToolItem
from app.schemas.schedule import (
    ScheduleCreateRequest,
    ScheduleDeleteResponse,
    ScheduleItemResponse,
    ScheduleUpdateRequest,
)
from app.schemas.skill import (
    SkillDetailResponse,
    SkillDraftRequest,
    SkillDraftResponse,
    SkillItemResponse,
)
from app.core.config import get_settings
from app.core.security import (
    create_access_token,
    decode_token,
    get_current_user,
    hash_password,
    verify_password,
)
from app.services.audit import log_event
from app.services.binding import (
    consume_bind_code,
    create_bind_code_record,
    ensure_identity,
    list_identities,
)
from app.services.platforms import miniapp as miniapp_platform
from app.services.message_handler import handle_message
from app.api.deps import get_or_create_user
from app.services.mcp_fetch import MCPFetchError, get_mcp_fetch_client
from app.services.conversations import (
    create_new_conversation,
    delete_conversation,
    ensure_active_conversation,
    list_conversations,
    rename_conversation,
    switch_conversation,
)
from app.services.skills import (
    create_or_update_skill_draft,
    disable_skill,
    get_builtin_skill,
    get_skill,
    get_skill_version_content,
    list_skills_with_source,
    publish_skill,
    render_skill_from_request,
)
from app.tools.finance import (
    delete_ledger,
    insert_ledger,
    query_stats,
    summarize_ledgers_in_range,
    update_ledger,
)
from app.services.realtime import get_notification_hub
from app.services.scheduler import get_scheduler
from app.services.scheduler_tasks import send_reminder_job
from app.services.runtime_context import (
    set_llm_streamer,
    reset_llm_streamer,
    set_llm_stream_nodes,
    reset_llm_stream_nodes,
)


router = APIRouter(prefix="/api")
MINIAPP_HOME_POPUP_KEY = "miniapp_home_popup"


def _default_miniapp_home_popup() -> dict:
    return {
        "enabled": False,
        "title": "系统公告",
        "content": "",
        "show_mode": "once_per_day",
        "start_at": "",
        "end_at": "",
        "version": 1,
        "primary_button_text": "我知道了",
    }


def _normalize_popup_datetime(value: str | None) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None
    tz = ZoneInfo(get_settings().timezone)
    if dt.tzinfo is not None:
        return dt.astimezone(tz).replace(tzinfo=None)
    return dt


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except Exception:
        return None


def _to_client_tz_iso(value: datetime | None) -> str:
    if value is None:
        return ""
    settings = get_settings()
    tz = ZoneInfo(settings.timezone)
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo("UTC"))
    return value.astimezone(tz).isoformat(timespec="seconds")


def _utc_naive_to_client_tz(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    tz = ZoneInfo(get_settings().timezone)
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo("UTC"))
    return value.astimezone(tz)


def _local_naive_to_utc_naive(value: datetime) -> datetime:
    tz = ZoneInfo(get_settings().timezone)
    return value.replace(tzinfo=tz).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def _parse_schedule_trigger_local(value: str) -> datetime:
    raw = (value or "").strip()
    if not raw:
        raise ValueError("empty trigger_time")
    normalized = raw.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    tz = ZoneInfo(get_settings().timezone)
    if dt.tzinfo is not None:
        return dt.astimezone(tz).replace(tzinfo=None)
    return dt


def _schedule_local_to_client_tz_iso(value: datetime | None) -> str:
    if value is None:
        return ""
    tz = ZoneInfo(get_settings().timezone)
    if value.tzinfo is None:
        value = value.replace(tzinfo=tz)
    else:
        value = value.astimezone(tz)
    return value.isoformat(timespec="seconds")


def _now_local_naive() -> datetime:
    return datetime.now(ZoneInfo(get_settings().timezone)).replace(tzinfo=None)


def _parse_ledger_transaction_utc_naive(value: str) -> datetime:
    raw = (value or "").strip()
    if not raw:
        raise ValueError("empty transaction_date")
    normalized = raw.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is not None:
        return dt.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    # Naive client input is treated as user local time and stored as UTC-naive.
    return _local_naive_to_utc_naive(dt)


@router.post("/auth/register", response_model=TokenResponse)
async def register(payload: RegisterRequest, session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(User).where(User.email == payload.email).limit(1)
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="email already exists")

    user = User(
        platform="web",
        platform_id=payload.email,
        email=payload.email,
        hashed_password=hash_password(payload.password),
    )
    session.add(user)
    await session.flush()
    await ensure_identity(session, user.id, "web", payload.email)
    await session.commit()
    await session.refresh(user)

    token = create_access_token(user.id)
    return TokenResponse(access_token=token)


@router.post("/auth/login", response_model=TokenResponse)
async def login(payload: LoginRequest, session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(User).where(User.email == payload.email).order_by(User.id.desc()).limit(1)
    )
    user = result.scalar_one_or_none()
    if not user or not user.hashed_password:
        raise HTTPException(status_code=401, detail="invalid credentials")
    if not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="invalid credentials")
    token = create_access_token(user.id)
    return TokenResponse(access_token=token)


@router.post("/miniapp/auth/login", response_model=MiniappTokenResponse)
async def miniapp_login(payload: MiniappLoginRequest, session: AsyncSession = Depends(get_session)):
    code = (payload.code or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="miniapp code is required")

    openid = await miniapp_platform.exchange_code_for_openid(code)
    if not openid:
        raise HTTPException(status_code=401, detail="miniapp login failed")

    user, _ = await get_or_create_user(session, "miniapp", openid)
    await ensure_identity(session, user.id, "miniapp", openid)
    nickname = (payload.nickname or "").strip()
    if nickname and (not user.nickname or user.nickname == "主人"):
        user.nickname = nickname
        session.add(user)
        await session.commit()
    token = create_access_token(user.id)
    return MiniappTokenResponse(access_token=token, openid=openid)


@router.get("/mcp/tools", response_model=List[MCPToolItem])
async def mcp_tools(
    user: User = Depends(get_current_user),
):
    _ = user
    settings = get_settings()
    if not settings.mcp_fetch_enabled:
        raise HTTPException(status_code=400, detail="mcp fetch disabled")
    try:
        tools = await get_mcp_fetch_client().list_tools()
    except MCPFetchError as exc:
        raise HTTPException(status_code=502, detail=f"mcp tools failed: {exc}") from exc

    rows: list[MCPToolItem] = []
    for item in tools:
        if not isinstance(item, dict):
            continue
        rows.append(
            MCPToolItem(
                name=str(item.get("name") or "").strip() or "unknown",
                description=str(item.get("description") or "").strip() or "no description",
            )
        )
    return rows


@router.post("/mcp/fetch", response_model=MCPFetchResponse)
async def mcp_fetch(
    payload: MCPFetchRequest,
    user: User = Depends(get_current_user),
):
    _ = user
    settings = get_settings()
    if not settings.mcp_fetch_enabled:
        raise HTTPException(status_code=400, detail="mcp fetch disabled")

    try:
        content = await get_mcp_fetch_client().fetch(
            url=payload.url,
            max_length=payload.max_length,
            start_index=payload.start_index,
            raw=payload.raw,
        )
    except MCPFetchError as exc:
        raise HTTPException(status_code=502, detail=f"mcp fetch failed: {exc}") from exc
    return MCPFetchResponse(content=content)


@router.get("/user/profile", response_model=ProfileResponse)
async def profile(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    return ProfileResponse(
        uuid=user.uuid,
        nickname=user.nickname,
        ai_name=user.ai_name,
        ai_emoji=user.ai_emoji,
        platform=user.platform,
        email=user.email,
        setup_stage=user.setup_stage,
        binding_stage=int(user.binding_stage or 0),
    )


@router.get("/config/miniapp/home-popup")
async def miniapp_home_popup_config(
    session: AsyncSession = Depends(get_session),
):
    data = _default_miniapp_home_popup()
    row = (
        (
            await session.execute(
                select(AppSetting).where(AppSetting.key == MINIAPP_HOME_POPUP_KEY).limit(1)
            )
        )
        .scalars()
        .first()
    )
    if row:
        try:
            payload = json.loads(row.value or "{}")
            if isinstance(payload, dict):
                data.update(payload)
        except Exception:
            pass

    now_local = _now_local_naive()
    start_at = _normalize_popup_datetime(str(data.get("start_at") or ""))
    end_at = _normalize_popup_datetime(str(data.get("end_at") or ""))
    enabled = bool(data.get("enabled"))
    active = enabled
    if start_at and now_local < start_at:
        active = False
    if end_at and now_local > end_at:
        active = False

    return {
        **data,
        "show_mode": str(data.get("show_mode") or "once_per_day"),
        "version": int(data.get("version") or 1),
        "active": active,
        "server_time": datetime.now(ZoneInfo(get_settings().timezone)).isoformat(timespec="seconds"),
    }


@router.get("/user/identities")
async def user_identities(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    rows = await list_identities(session, user.id)
    return rows


@router.post("/user/feedback", response_model=FeedbackCreateResponse)
async def user_feedback_create(
    payload: FeedbackCreateRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    content = (payload.content or "").strip()
    if len(content) < 4:
        raise HTTPException(status_code=400, detail="feedback content too short")

    feedback = UserFeedback(
        user_id=user.id,
        platform=user.platform,
        content=content,
        app_version=(payload.app_version or "").strip(),
        env_version=(payload.env_version or "").strip(),
        client_page=(payload.client_page or "").strip(),
    )
    session.add(feedback)
    await session.flush()

    await log_event(
        session,
        action="feedback_created",
        platform=user.platform,
        user_id=user.id,
        detail={
            "feedback_id": feedback.id,
            "app_version": feedback.app_version,
            "env_version": feedback.env_version,
            "client_page": feedback.client_page,
        },
    )
    await session.commit()
    await session.refresh(feedback)

    return FeedbackCreateResponse(
        ok=True,
        id=feedback.id,
        created_at=_to_client_tz_iso(feedback.created_at),
    )


@router.post("/user/bind-code", response_model=BindCodeCreateResponse)
async def user_bind_code_create(
    payload: BindCodeCreateRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    bind = await create_bind_code_record(
        session=session,
        owner_user_id=user.id,
        ttl_minutes=payload.ttl_minutes,
    )
    return BindCodeCreateResponse(
        code=bind.code,
        expires_at=bind.expires_at,
        ttl_minutes=payload.ttl_minutes,
    )


@router.post("/user/bind-consume", response_model=BindCodeConsumeResponse)
async def user_bind_code_consume(
    payload: BindCodeConsumeRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    code = (payload.code or "").strip()
    if not code.isdigit() or len(code) != 6:
        raise HTTPException(status_code=400, detail="invalid bind code format")
    ok, message, canonical_user_id = await consume_bind_code(
        session=session,
        code=code,
        current_user_id=user.id,
    )
    if not ok:
        return BindCodeConsumeResponse(ok=False, message=message, canonical_user_id=canonical_user_id)
    new_token = None
    if canonical_user_id and canonical_user_id != user.id:
        new_token = create_access_token(canonical_user_id)
    return BindCodeConsumeResponse(
        ok=True,
        message=message,
        canonical_user_id=canonical_user_id,
        access_token=new_token,
    )


@router.get("/chat/history")
async def chat_history(
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    conversation = await ensure_active_conversation(session, user)
    result = await session.execute(
        select(Message)
        .where(
            Message.user_id == user.id,
            Message.conversation_id == conversation.id,
        )
        .order_by(Message.id.desc())
        .limit(limit)
    )
    messages = list(reversed(result.scalars().all()))
    if not messages and user.setup_stage == SetupStage.NEW:
        if int(user.binding_stage or 0) < 2:
            greeting = "在其他客户端有账号吗？回复“有”或“没有”。有的话可稍后用 `/bind new` 与 `/bind <6位码>` 绑定数据。"
            user.binding_stage = 1
        else:
            greeting = "你好！我是您的私人助理 PAI。初次见面，请问我该怎么称呼您？"
            user.setup_stage = SetupStage.USER_NAMED
        session.add(user)
        session.add(
            Message(
                user_id=user.id,
                conversation_id=conversation.id,
                role="assistant",
                content=greeting,
                platform="web",
            )
        )
        await session.commit()
        messages = [
            Message(
                user_id=user.id,
                conversation_id=conversation.id,
                role="assistant",
                content=greeting,
                platform="web",
                created_at=datetime.now(timezone.utc),
            )
        ]
    return [
        {
            "role": msg.role,
            "content": msg.content,
            "created_at": _to_client_tz_iso(msg.created_at),
        }
        for msg in messages
    ]


def _to_conversation_response(item, active_id: int | None) -> ConversationResponse:
    return ConversationResponse(
        id=item.id,
        title=item.title,
        summary=item.summary,
        last_message_at=_to_client_tz_iso(item.last_message_at),
        active=(active_id == item.id),
    )


@router.get("/conversations", response_model=List[ConversationResponse])
async def conversations_list(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    await ensure_active_conversation(session, user)
    rows = await list_conversations(session, user, limit=50)
    return [_to_conversation_response(row, user.active_conversation_id) for row in rows]


@router.get("/conversations/current", response_model=ConversationResponse)
async def conversations_current(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    current = await ensure_active_conversation(session, user)
    return _to_conversation_response(current, current.id)


@router.post("/conversations", response_model=ConversationResponse)
async def conversations_create(
    payload: ConversationCreateRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    created = await create_new_conversation(session, user, title=payload.title)
    await log_event(
        session,
        action="conversation_created",
        platform=user.platform,
        user_id=user.id,
        detail={"conversation_id": created.id, "title": created.title, "via": "api"},
    )
    return _to_conversation_response(created, created.id)


@router.post("/conversations/{conversation_id}/switch", response_model=ConversationResponse)
async def conversations_switch(
    conversation_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    switched = await switch_conversation(session, user, conversation_id)
    if not switched:
        raise HTTPException(status_code=404, detail="conversation not found")
    await log_event(
        session,
        action="conversation_switched",
        platform=user.platform,
        user_id=user.id,
        detail={"conversation_id": switched.id, "title": switched.title, "via": "api"},
    )
    return _to_conversation_response(switched, switched.id)


@router.patch("/conversations/{conversation_id}", response_model=ConversationResponse)
async def conversations_rename(
    conversation_id: int,
    payload: ConversationUpdateRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    renamed = await rename_conversation(session, user, conversation_id, payload.title)
    if not renamed:
        raise HTTPException(status_code=404, detail="conversation not found")
    await log_event(
        session,
        action="conversation_renamed",
        platform=user.platform,
        user_id=user.id,
        detail={"conversation_id": renamed.id, "title": renamed.title, "via": "api"},
    )
    return _to_conversation_response(renamed, user.active_conversation_id)


@router.delete("/conversations/{conversation_id}", response_model=ConversationDeleteResponse)
async def conversations_delete(
    conversation_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    replacement, deleted_title = await delete_conversation(session, user, conversation_id)
    if not replacement or not deleted_title:
        raise HTTPException(status_code=404, detail="conversation not found")
    await log_event(
        session,
        action="conversation_deleted",
        platform=user.platform,
        user_id=user.id,
        detail={
            "conversation_id": conversation_id,
            "deleted_title": deleted_title,
            "active_conversation_id": replacement.id,
            "via": "api",
        },
    )
    return ConversationDeleteResponse(
        ok=True,
        deleted_id=conversation_id,
        deleted_title=deleted_title,
        active_conversation=_to_conversation_response(replacement, replacement.id),
    )


async def _sse_stream(text: str):
    chunk_size = 32
    for i in range(0, len(text), chunk_size):
        chunk = text[i : i + chunk_size]
        payload = json.dumps({"chunk": chunk}, ensure_ascii=False)
        yield f"data: {payload}\n\n"
        await asyncio.sleep(0.01)
    yield f"data: {json.dumps({'done': True}, ensure_ascii=False)}\n\n"


def _iter_text_chunks(text: str, *, chunk_size: int = 32):
    size = max(1, int(chunk_size))
    for i in range(0, len(text), size):
        yield text[i : i + size]


async def _sse_stream_live(
    *,
    source_platform: str,
    normalized: dict,
    session: AsyncSession,
):
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    streamed_chunks: list[str] = []
    result_holder: dict[str, dict] = {}
    error_holder: dict[str, str] = {}

    async def _on_stream_chunk(chunk: str) -> None:
        if not chunk:
            return
        streamed_chunks.append(chunk)
        await queue.put(chunk)

    async def _runner() -> None:
        streamer_token = set_llm_streamer(_on_stream_chunk)
        # Stream only final natural-language generation nodes; avoid leaking
        # structured-classifier/planner JSON fragments.
        stream_nodes_token = set_llm_stream_nodes({"chat_manager", "help_center"})
        try:
            result_holder["result"] = await handle_message(source_platform, normalized, session)
        except Exception as exc:
            error_holder["error"] = str(exc)
        finally:
            reset_llm_stream_nodes(stream_nodes_token)
            reset_llm_streamer(streamer_token)
            await queue.put(None)

    task = asyncio.create_task(_runner())
    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            payload = json.dumps({"chunk": item}, ensure_ascii=False)
            yield f"data: {payload}\n\n"

        await task
        if error_holder.get("error"):
            payload = json.dumps({"error": error_holder["error"], "done": True}, ensure_ascii=False)
            yield f"data: {payload}\n\n"
            return

        result = result_holder.get("result") or {}
        if not result.get("ok"):
            err_text = str(result.get("error") or "failed")
            payload = json.dumps({"error": err_text, "done": True}, ensure_ascii=False)
            yield f"data: {payload}\n\n"
            return

        responses = result.get("responses") or []
        joined = "\n".join(responses)
        streamed_text = "".join(streamed_chunks)
        if joined and not streamed_text:
            for chunk in _iter_text_chunks(joined, chunk_size=32):
                payload = json.dumps({"chunk": chunk}, ensure_ascii=False)
                yield f"data: {payload}\n\n"
        elif joined and streamed_text and joined.startswith(streamed_text):
            suffix = joined[len(streamed_text) :]
            for chunk in _iter_text_chunks(suffix, chunk_size=32):
                payload = json.dumps({"chunk": chunk}, ensure_ascii=False)
                yield f"data: {payload}\n\n"

        yield f"data: {json.dumps({'done': True}, ensure_ascii=False)}\n\n"
    finally:
        if not task.done():
            task.cancel()


@router.post("/chat/send")
async def chat_send(
    payload: ChatSendRequest,
    stream: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    source_platform = (payload.source_platform or "web").strip().lower()
    if source_platform not in {"web", "miniapp"}:
        source_platform = "web"

    platform_id = ""
    if source_platform == "miniapp":
        identity_result = await session.execute(
            select(UserIdentity).where(
                UserIdentity.user_id == user.id,
                UserIdentity.platform == "miniapp",
            )
            .order_by(UserIdentity.id.desc())
            .limit(1)
        )
        identity = identity_result.scalar_one_or_none()
        platform_id = (identity.platform_id if identity else "").strip()
        if not platform_id:
            source_platform = "web"

    if source_platform == "web":
        platform_id = f"web:{user.id}"
        await ensure_identity(session, user.id, "web", platform_id)

    normalized = {
        "platform_id": platform_id,
        "content": payload.content,
        "image_urls": payload.image_urls,
        "message_id": f"{source_platform}-{datetime.utcnow().timestamp()}",
        "event_ts": int(datetime.utcnow().timestamp()),
        "raw_data": {"source_platform": source_platform},
    }

    if stream:
        return StreamingResponse(
            _sse_stream_live(
                source_platform=source_platform,
                normalized=normalized,
                session=session,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    result = await handle_message(source_platform, normalized, session)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "failed"))
    responses = result.get("responses") or []

    return ChatSendResponse(responses=responses)


@router.websocket("/chat/ws")
async def chat_ws(websocket: WebSocket):
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4401)
        return
    try:
        user_id = decode_token(token)
    except Exception:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    try:
        async with AsyncSessionLocal() as session:
            user = await session.get(User, user_id)
            if not user:
                await websocket.close(code=4401)
                return
            while True:
                web_platform_id = f"web:{user.id}"
                await ensure_identity(session, user.id, "web", web_platform_id)
                payload = await websocket.receive_json()
                content = str(payload.get("content") or "")
                image_urls = payload.get("image_urls") or []
                normalized = {
                    "platform_id": web_platform_id,
                    "content": content,
                    "image_urls": image_urls,
                    "message_id": f"web-ws-{datetime.utcnow().timestamp()}",
                    "event_ts": int(datetime.utcnow().timestamp()),
                    "raw_data": {"web": True, "transport": "ws"},
                }
                result = await handle_message("web", normalized, session)
                if not result.get("ok"):
                    await websocket.send_json({"ok": False, "error": result.get("error", "failed")})
                else:
                    await websocket.send_json({"ok": True, "responses": result.get("responses", [])})
    except WebSocketDisconnect:
        return


@router.websocket("/notifications/ws")
async def notifications_ws(websocket: WebSocket):
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4401)
        return
    try:
        user_id = decode_token(token)
    except Exception:
        await websocket.close(code=4401)
        return

    hub = get_notification_hub()
    await hub.connect(user_id, websocket)
    try:
        while True:
            # Keep connection open; incoming content is optional (client ping/no-op).
            await websocket.receive_text()
    except WebSocketDisconnect:
        await hub.disconnect(user_id, websocket)
    except Exception:
        await hub.disconnect(user_id, websocket)


@router.get("/stats/ledger")
async def ledger_stats(
    scope: str | None = Query(default=None, pattern="^(day|week|month)$"),
    days: int = Query(default=30, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    if scope:
        now_local = _now_local_naive()
        start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        if scope == "week":
            start_local = start_local - timedelta(days=start_local.weekday())
        elif scope == "month":
            start_local = start_local.replace(day=1)

        start_at = _local_naive_to_utc_naive(start_local)
        end_at = _local_naive_to_utc_naive(now_local)
        if end_at <= start_at:
            end_at = start_at + timedelta(seconds=1)
        return await summarize_ledgers_in_range(session, user_id=user.id, start_at=start_at, end_at=end_at)

    return await query_stats(session, user_id=user.id, days=days)


@router.get("/calendar", response_model=CalendarResponse)
async def calendar_events(
    start_date: str | None = Query(default=None, description="YYYY-MM-DD"),
    end_date: str | None = Query(default=None, description="YYYY-MM-DD, exclusive"),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    today = _now_local_naive().date()
    default_start = today.replace(day=1)
    if default_start.month == 12:
        default_end = date(default_start.year + 1, 1, 1)
    else:
        default_end = date(default_start.year, default_start.month + 1, 1)

    start = _parse_date(start_date) or default_start
    end = _parse_date(end_date) or default_end
    if end <= start:
        raise HTTPException(status_code=400, detail="end_date must be greater than start_date")
    if (end - start).days > 120:
        raise HTTPException(status_code=400, detail="date range too large, max 120 days")

    start_at = datetime.combine(start, datetime.min.time())
    end_at = datetime.combine(end, datetime.min.time())
    ledger_start_at = _local_naive_to_utc_naive(start_at)
    ledger_end_at = _local_naive_to_utc_naive(end_at)

    ledger_result = await session.execute(
        select(Ledger)
        .where(
            Ledger.user_id == user.id,
            Ledger.transaction_date >= ledger_start_at,
            Ledger.transaction_date < ledger_end_at,
        )
        .order_by(Ledger.transaction_date.asc(), Ledger.id.asc())
    )
    schedule_result = await session.execute(
        select(Schedule)
        .where(
            Schedule.user_id == user.id,
            Schedule.trigger_time >= start_at,
            Schedule.trigger_time < end_at,
        )
        .order_by(Schedule.trigger_time.asc(), Schedule.id.asc())
    )

    day_map: dict[str, dict] = {}
    cursor = start
    while cursor < end:
        key = cursor.isoformat()
        day_map[key] = {
            "date": key,
            "ledger_total": 0.0,
            "ledger_count": 0,
            "schedule_count": 0,
            "ledgers": [],
            "schedules": [],
        }
        cursor += timedelta(days=1)

    for row in ledger_result.scalars().all():
        local_dt = _utc_naive_to_client_tz(row.transaction_date)
        if not local_dt:
            continue
        key = local_dt.date().isoformat()
        if key not in day_map:
            continue
        day = day_map[key]
        day["ledger_total"] += float(row.amount)
        day["ledger_count"] += 1
        day["ledgers"].append(
            CalendarLedgerItem(
                id=row.id,
                amount=float(row.amount),
                currency=row.currency,
                category=row.category,
                item=row.item,
                transaction_date=_to_client_tz_iso(row.transaction_date),
            )
        )

    for row in schedule_result.scalars().all():
        key = row.trigger_time.date().isoformat()
        if key not in day_map:
            continue
        day = day_map[key]
        day["schedule_count"] += 1
        day["schedules"].append(
            CalendarScheduleItem(
                id=row.id,
                content=row.content,
                trigger_time=_schedule_local_to_client_tz_iso(row.trigger_time),
                status=row.status,
            )
        )

    days = [CalendarDayResponse(**day_map[key]) for key in sorted(day_map.keys())]
    return CalendarResponse(
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        days=days,
    )


@router.get("/ledgers", response_model=List[LedgerItemResponse])
async def ledger_list(
    limit: int = Query(default=30, ge=1, le=200),
    before_id: int | None = Query(default=None, ge=1),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    stmt = (
        select(Ledger)
        .where(Ledger.user_id == user.id)
        .order_by(Ledger.id.desc())
        .limit(limit)
    )
    if before_id is not None:
        stmt = stmt.where(Ledger.id < before_id)

    result = await session.execute(stmt)
    rows = result.scalars().all()
    return [
        LedgerItemResponse(
            id=row.id,
            amount=row.amount,
            currency=row.currency,
            category=row.category,
            item=row.item,
            transaction_date=_to_client_tz_iso(row.transaction_date),
            created_at=_to_client_tz_iso(row.created_at),
        )
        for row in rows
    ]


@router.patch("/ledgers/{ledger_id}", response_model=LedgerItemResponse)
async def ledger_patch(
    ledger_id: int,
    payload: LedgerUpdateRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    updated = await update_ledger(
        session,
        user_id=user.id,
        ledger_id=ledger_id,
        amount=payload.amount,
        category=payload.category,
        item=payload.item,
        platform=user.platform,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="ledger not found")
    return LedgerItemResponse(
        id=updated.id,
        amount=updated.amount,
        currency=updated.currency,
        category=updated.category,
        item=updated.item,
        transaction_date=_to_client_tz_iso(updated.transaction_date),
        created_at=_to_client_tz_iso(updated.created_at),
    )


@router.delete("/ledgers/{ledger_id}", response_model=LedgerDeleteResponse)
async def ledger_delete(
    ledger_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    deleted = await delete_ledger(
        session,
        user_id=user.id,
        ledger_id=ledger_id,
        platform=user.platform,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="ledger not found")
    return LedgerDeleteResponse(ok=True, id=deleted.id)


@router.post("/ledgers", response_model=LedgerItemResponse)
async def ledger_create(
    payload: LedgerCreateRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    txn_date = None
    if payload.transaction_date:
        try:
            txn_date = _parse_ledger_transaction_utc_naive(payload.transaction_date)
        except Exception:
            pass

    ledger = await insert_ledger(
        session,
        user_id=user.id,
        amount=payload.amount,
        category=payload.category or "其他",
        item=payload.item or "手动记录",
        transaction_date=txn_date,
        platform=user.platform,
    )
    return LedgerItemResponse(
        id=ledger.id,
        amount=ledger.amount,
        currency=ledger.currency,
        category=ledger.category,
        item=ledger.item,
        transaction_date=_to_client_tz_iso(ledger.transaction_date),
        created_at=_to_client_tz_iso(ledger.created_at),
    )


# ── Schedule CRUD ──

@router.get("/schedules", response_model=List[ScheduleItemResponse])
async def schedule_list(
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    result = await session.execute(
        select(Schedule)
        .where(Schedule.user_id == user.id)
        .order_by(Schedule.trigger_time.desc())
        .limit(limit)
    )
    return [
        ScheduleItemResponse(
            id=row.id,
            content=row.content,
            trigger_time=_schedule_local_to_client_tz_iso(row.trigger_time),
            status=row.status,
            created_at=_to_client_tz_iso(row.created_at),
        )
        for row in result.scalars().all()
    ]


@router.post("/schedules", response_model=ScheduleItemResponse)
async def schedule_create(
    payload: ScheduleCreateRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    try:
        trigger = _parse_schedule_trigger_local(payload.trigger_time)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid trigger_time format")

    from uuid import uuid4

    job_id = str(uuid4())
    schedule = Schedule(
        user_id=user.id,
        job_id=job_id,
        content=payload.content,
        trigger_time=trigger,
    )
    session.add(schedule)
    await session.commit()
    await session.refresh(schedule)

    # Register APScheduler job if in future
    if trigger > _now_local_naive():
        try:
            scheduler = get_scheduler()
            scheduler.add_job(job_id, trigger, send_reminder_job, schedule.id)
        except Exception:
            pass

    await log_event(
        session,
        action="schedule_created",
        platform=user.platform,
        user_id=user.id,
        detail={"content": payload.content, "trigger_time": trigger.isoformat(), "via": "manual"},
    )
    return ScheduleItemResponse(
        id=schedule.id,
        content=schedule.content,
        trigger_time=_schedule_local_to_client_tz_iso(schedule.trigger_time),
        status=schedule.status,
        created_at=_to_client_tz_iso(schedule.created_at),
    )


@router.patch("/schedules/{schedule_id}", response_model=ScheduleItemResponse)
async def schedule_patch(
    schedule_id: int,
    payload: ScheduleUpdateRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    schedule = await session.get(Schedule, schedule_id)
    if not schedule or schedule.user_id != user.id:
        raise HTTPException(status_code=404, detail="schedule not found")

    if payload.content is not None:
        schedule.content = payload.content
    if payload.status is not None:
        schedule.status = payload.status.upper()
    if payload.trigger_time is not None:
        try:
            new_trigger = _parse_schedule_trigger_local(payload.trigger_time)
            schedule.trigger_time = new_trigger
            # Reschedule APScheduler job
            try:
                scheduler = get_scheduler()
                scheduler.remove_job(schedule.job_id)
                if new_trigger > _now_local_naive():
                    scheduler.add_job(schedule.job_id, new_trigger, send_reminder_job, schedule.id)
            except Exception:
                pass
        except Exception:
            raise HTTPException(status_code=400, detail="invalid trigger_time format")

    session.add(schedule)
    await session.commit()
    await session.refresh(schedule)
    return ScheduleItemResponse(
        id=schedule.id,
        content=schedule.content,
        trigger_time=_schedule_local_to_client_tz_iso(schedule.trigger_time),
        status=schedule.status,
        created_at=_to_client_tz_iso(schedule.created_at),
    )


@router.delete("/schedules/{schedule_id}", response_model=ScheduleDeleteResponse)
async def schedule_delete(
    schedule_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    schedule = await session.get(Schedule, schedule_id)
    if not schedule or schedule.user_id != user.id:
        raise HTTPException(status_code=404, detail="schedule not found")

    # Remove APScheduler job
    try:
        scheduler = get_scheduler()
        scheduler.remove_job(schedule.job_id)
    except Exception:
        pass

    snapshot_id = schedule.id
    snapshot_content = schedule.content
    # Clean up delivery rows first to satisfy FK constraint:
    # reminder_deliveries.schedule_id -> schedules.id
    await session.execute(
        delete(ReminderDelivery).where(ReminderDelivery.schedule_id == snapshot_id)
    )
    await session.delete(schedule)
    await session.commit()
    await log_event(
        session,
        action="schedule_deleted",
        platform=user.platform,
        user_id=user.id,
        detail={"schedule_id": snapshot_id, "content": snapshot_content, "via": "manual"},
    )
    return ScheduleDeleteResponse(ok=True, id=snapshot_id)


@router.get("/skills", response_model=List[SkillItemResponse])
async def skills_list(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    rows = await list_skills_with_source(session, user.id)
    return [SkillItemResponse(**item) for item in rows]


@router.get("/skills/{slug}", response_model=SkillDetailResponse)
async def skills_detail(
    slug: str,
    source: str = Query(default="user"),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    source_key = (source or "user").strip().lower()
    if source_key == "builtin":
        doc = get_builtin_skill(slug)
        if not doc:
            raise HTTPException(status_code=404, detail="skill not found")
        return SkillDetailResponse(
            slug=doc.slug,
            name=doc.name,
            description=doc.description,
            status="BUILTIN",
            active_version=1,
            source="builtin",
            read_only=True,
            content_md=doc.content,
        )

    skill = await get_skill(session, user.id, slug)
    if not skill:
        raise HTTPException(status_code=404, detail="skill not found")
    content_md = await get_skill_version_content(session, skill)
    return SkillDetailResponse(
        slug=skill.slug,
        name=skill.name,
        description=skill.description,
        status=skill.status,
        active_version=skill.active_version,
        source="user",
        read_only=False,
        content_md=content_md,
    )


@router.post("/skills/draft", response_model=SkillDraftResponse)
async def skills_draft(
    payload: SkillDraftRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    existing_content: str | None = None
    preferred_name = payload.skill_name
    if payload.skill_slug:
        current = await get_skill(session, user.id, payload.skill_slug)
        if not current:
            raise HTTPException(status_code=404, detail="skill not found")
        preferred_name = current.name
        existing_content = await get_skill_version_content(session, current)

    content_md = await render_skill_from_request(
        user_request=payload.request,
        preferred_name=preferred_name,
        existing_content=existing_content,
    )
    skill, version = await create_or_update_skill_draft(
        session,
        user_id=user.id,
        content_md=content_md,
        preferred_name=preferred_name or payload.skill_slug or "custom-skill",
    )
    await log_event(
        session,
        action="skill_draft_saved",
        platform=user.platform,
        user_id=user.id,
        detail={"slug": skill.slug, "version": version, "via": "api"},
    )
    return SkillDraftResponse(
        slug=skill.slug,
        version=version,
        status=skill.status,
        content_md=content_md,
    )


@router.post("/skills/{slug}/publish", response_model=SkillItemResponse)
async def skills_publish(
    slug: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    skill = await publish_skill(session, user.id, slug)
    if not skill:
        raise HTTPException(status_code=404, detail="skill not found or no version")
    await log_event(
        session,
        action="skill_published",
        platform=user.platform,
        user_id=user.id,
        detail={"slug": skill.slug, "version": skill.active_version, "via": "api"},
    )
    return SkillItemResponse(
        slug=skill.slug,
        name=skill.name,
        description=skill.description,
        status=skill.status,
        active_version=skill.active_version,
        source="user",
        read_only=False,
    )


@router.post("/skills/{slug}/disable", response_model=SkillItemResponse)
async def skills_disable(
    slug: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    skill = await disable_skill(session, user.id, slug)
    if not skill:
        raise HTTPException(status_code=404, detail="skill not found")
    await log_event(
        session,
        action="skill_disabled",
        platform=user.platform,
        user_id=user.id,
        detail={"slug": skill.slug, "via": "api"},
    )
    return SkillItemResponse(
        slug=skill.slug,
        name=skill.name,
        description=skill.description,
        status=skill.status,
        active_version=skill.active_version,
        source="user",
        read_only=False,
    )
