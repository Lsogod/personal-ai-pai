from __future__ import annotations

import asyncio
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session, AsyncSessionLocal
from app.models.user import User, SetupStage
from app.models.message import Message
from app.models.ledger import Ledger
from app.schemas.auth import RegisterRequest, LoginRequest, TokenResponse
from app.schemas.chat import ChatSendRequest, ChatSendResponse, ProfileResponse
from app.schemas.conversation import ConversationCreateRequest, ConversationResponse
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
from app.services.message_handler import handle_message
from app.services.conversations import (
    create_new_conversation,
    ensure_active_conversation,
    list_conversations,
    switch_conversation,
)
from app.services.skills import (
    create_or_update_skill_draft,
    disable_skill,
    get_skill,
    get_skill_version_content,
    list_user_skills,
    publish_skill,
    render_skill_from_request,
)
from app.tools.finance import delete_ledger, query_stats, update_ledger


router = APIRouter(prefix="/api")


@router.post("/auth/register", response_model=TokenResponse)
async def register(payload: RegisterRequest, session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(User).where(User.platform == "web", User.email == payload.email)
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
    await session.commit()
    await session.refresh(user)

    token = create_access_token(user.id)
    return TokenResponse(access_token=token)


@router.post("/auth/login", response_model=TokenResponse)
async def login(payload: LoginRequest, session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(User).where(User.platform == "web", User.email == payload.email)
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


async def _sse_stream(text: str):
    chunk_size = 12
    for i in range(0, len(text), chunk_size):
        chunk = text[i : i + chunk_size]
        yield f"data: {chunk}\n\n"
        await asyncio.sleep(0.02)
    yield "data: [DONE]\n\n"


@router.post("/chat/send")
async def chat_send(
    payload: ChatSendRequest,
    stream: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    normalized = {
        "platform_id": user.platform_id,
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
                payload = await websocket.receive_json()
                content = str(payload.get("content") or "")
                image_urls = payload.get("image_urls") or []
                normalized = {
                    "platform_id": user.platform_id,
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


@router.get("/stats/ledger")
async def ledger_stats(
    days: int = Query(default=30, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    return await query_stats(session, user_id=user.id, days=days)


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
    rows = await list_user_skills(session, user.id)
    return [SkillItemResponse(**item) for item in rows]


@router.get("/skills/{slug}", response_model=SkillDetailResponse)
async def skills_detail(
    slug: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
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
    )
