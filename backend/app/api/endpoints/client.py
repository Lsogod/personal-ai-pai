from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session, AsyncSessionLocal
from app.models.user import User, SetupStage
from app.models.message import Message
from app.models.ledger import Ledger
from app.models.schedule import Schedule
from app.schemas.auth import RegisterRequest, LoginRequest, TokenResponse
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
from app.schemas.conversation import ConversationCreateRequest, ConversationResponse
from app.schemas.conversation import ConversationDeleteResponse, ConversationUpdateRequest
from app.schemas.ledger import LedgerDeleteResponse, LedgerItemResponse, LedgerUpdateRequest
from app.schemas.skill import (
    SkillDetailResponse,
    SkillDraftRequest,
    SkillDraftResponse,
    SkillItemResponse,
)
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
from app.services.message_handler import handle_message
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
from app.tools.finance import delete_ledger, query_stats, update_ledger
from app.services.realtime import get_notification_hub


router = APIRouter(prefix="/api")


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
    )


@router.get("/user/identities")
async def user_identities(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    rows = await list_identities(session, user.id)
    return rows


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
                created_at=datetime.utcnow(),
            )
        ]
    return [
        {
            "role": msg.role,
            "content": msg.content,
            "created_at": msg.created_at.isoformat(),
        }
        for msg in messages
    ]


def _to_conversation_response(item, active_id: int | None) -> ConversationResponse:
    return ConversationResponse(
        id=item.id,
        title=item.title,
        summary=item.summary,
        last_message_at=item.last_message_at.isoformat(),
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
        yield f"data: {chunk}\n\n"
        await asyncio.sleep(0.03)
    yield "data: [DONE]\n\n"


@router.post("/chat/send")
async def chat_send(
    payload: ChatSendRequest,
    stream: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    web_platform_id = f"web:{user.id}"
    await ensure_identity(session, user.id, "web", web_platform_id)
    normalized = {
        "platform_id": web_platform_id,
        "content": payload.content,
        "image_urls": payload.image_urls,
        "message_id": f"web-{datetime.utcnow().timestamp()}",
        "event_ts": int(datetime.utcnow().timestamp()),
        "raw_data": {"web": True},
    }

    result = await handle_message("web", normalized, session)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "failed"))
    responses = result.get("responses") or []
    joined = "\n".join(responses)

    if stream:
        return StreamingResponse(
            _sse_stream(joined),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

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
    days: int = Query(default=30, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    return await query_stats(session, user_id=user.id, days=days)


@router.get("/calendar", response_model=CalendarResponse)
async def calendar_events(
    start_date: str | None = Query(default=None, description="YYYY-MM-DD"),
    end_date: str | None = Query(default=None, description="YYYY-MM-DD, exclusive"),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    today = datetime.utcnow().date()
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

    ledger_result = await session.execute(
        select(Ledger)
        .where(
            Ledger.user_id == user.id,
            Ledger.transaction_date >= start_at,
            Ledger.transaction_date < end_at,
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
        key = row.transaction_date.date().isoformat()
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
                transaction_date=row.transaction_date.isoformat(),
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
                trigger_time=row.trigger_time.isoformat(),
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
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    result = await session.execute(
        select(Ledger)
        .where(Ledger.user_id == user.id)
        .order_by(Ledger.id.desc())
        .limit(limit)
    )
    rows = result.scalars().all()
    return [
        LedgerItemResponse(
            id=row.id,
            amount=row.amount,
            currency=row.currency,
            category=row.category,
            item=row.item,
            transaction_date=row.transaction_date.isoformat(),
            created_at=row.created_at.isoformat(),
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
        transaction_date=updated.transaction_date.isoformat(),
        created_at=updated.created_at.isoformat(),
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
