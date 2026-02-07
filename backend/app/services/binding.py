from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Iterable

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bind_code import BindCode
from app.models.identity import UserIdentity
from app.models.user import User
from app.models.ledger import Ledger
from app.models.schedule import Schedule
from app.models.message import Message
from app.models.conversation import Conversation
from app.models.skill import Skill
from app.models.audit import AuditLog


def _generate_code() -> str:
    return f"{random.randint(0, 999999):06d}"


async def ensure_identity(
    session: AsyncSession,
    user_id: int,
    platform: str,
    platform_id: str,
) -> None:
    if not platform or not platform_id:
        return
    result = await session.execute(
        select(UserIdentity).where(
            UserIdentity.platform == platform,
            UserIdentity.platform_id == platform_id,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        if existing.user_id != user_id:
            existing.user_id = user_id
            session.add(existing)
            await session.commit()
        return
    session.add(
        UserIdentity(
            user_id=user_id,
            platform=platform,
            platform_id=platform_id,
        )
    )
    await session.commit()


async def list_identities(session: AsyncSession, user_id: int) -> list[dict]:
    result = await session.execute(
        select(UserIdentity)
        .where(UserIdentity.user_id == user_id)
        .order_by(UserIdentity.created_at.asc())
    )
    rows = result.scalars().all()
    return [
        {"platform": row.platform, "platform_id": row.platform_id}
        for row in rows
    ]


async def create_bind_code_record(
    session: AsyncSession,
    owner_user_id: int,
    ttl_minutes: int = 10,
) -> BindCode:
    for _ in range(6):
        code = _generate_code()
        exists = await session.execute(select(BindCode).where(BindCode.code == code))
        if exists.scalar_one_or_none():
            continue
        bind = BindCode(
            code=code,
            owner_user_id=owner_user_id,
            expires_at=datetime.utcnow() + timedelta(minutes=ttl_minutes),
        )
        session.add(bind)
        await session.commit()
        await session.refresh(bind)
        return bind
    bind = BindCode(
        code=_generate_code(),
        owner_user_id=owner_user_id,
        expires_at=datetime.utcnow() + timedelta(minutes=ttl_minutes),
    )
    session.add(bind)
    await session.commit()
    await session.refresh(bind)
    return bind


async def create_bind_code(
    session: AsyncSession,
    owner_user_id: int,
    ttl_minutes: int = 10,
) -> str:
    bind = await create_bind_code_record(
        session=session,
        owner_user_id=owner_user_id,
        ttl_minutes=ttl_minutes,
    )
    return bind.code


async def _rename_conflicting_skills(
    session: AsyncSession,
    source_user_id: int,
    target_user_id: int,
) -> None:
    target_result = await session.execute(
        select(Skill.slug).where(Skill.user_id == target_user_id)
    )
    target_slugs = {row[0] for row in target_result.all()}

    source_result = await session.execute(
        select(Skill).where(Skill.user_id == source_user_id)
    )
    source_skills = list(source_result.scalars().all())
    for skill in source_skills:
        if skill.slug in target_slugs:
            skill.slug = f"{skill.slug}-merged-{skill.id}"
            session.add(skill)
    await session.commit()


async def merge_users(
    session: AsyncSession,
    source_user_id: int,
    target_user_id: int,
) -> None:
    if source_user_id == target_user_id:
        return

    source = await session.get(User, source_user_id)
    target = await session.get(User, target_user_id)
    if not source or not target:
        return

    target.setup_stage = max(int(target.setup_stage or 0), int(source.setup_stage or 0))
    target.binding_stage = max(int(target.binding_stage or 0), int(source.binding_stage or 0))
    if target.nickname == "主人" and source.nickname and source.nickname != "主人":
        target.nickname = source.nickname
    if target.ai_name == "PAI" and source.ai_name and source.ai_name != "PAI":
        target.ai_name = source.ai_name
    if target.ai_emoji == "🤖" and source.ai_emoji and source.ai_emoji != "🤖":
        target.ai_emoji = source.ai_emoji
    if source.email and not target.email:
        target.email = source.email
        if source.hashed_password and not target.hashed_password:
            target.hashed_password = source.hashed_password
        source.email = None
        source.hashed_password = None
    session.add(target)
    session.add(source)

    await _rename_conflicting_skills(session, source_user_id, target_user_id)

    await session.execute(
        update(Ledger).where(Ledger.user_id == source_user_id).values(user_id=target_user_id)
    )
    await session.execute(
        update(Schedule).where(Schedule.user_id == source_user_id).values(user_id=target_user_id)
    )
    await session.execute(
        update(Message).where(Message.user_id == source_user_id).values(user_id=target_user_id)
    )
    await session.execute(
        update(Conversation).where(Conversation.user_id == source_user_id).values(user_id=target_user_id)
    )
    await session.execute(
        update(Skill).where(Skill.user_id == source_user_id).values(user_id=target_user_id)
    )
    await session.execute(
        update(AuditLog).where(AuditLog.user_id == source_user_id).values(user_id=target_user_id)
    )
    await session.execute(
        update(UserIdentity)
        .where(UserIdentity.user_id == source_user_id)
        .values(user_id=target_user_id)
    )

    if not target.active_conversation_id and source.active_conversation_id:
        target.active_conversation_id = source.active_conversation_id
        session.add(target)

    await session.commit()


async def consume_bind_code(
    session: AsyncSession,
    code: str,
    current_user_id: int,
) -> tuple[bool, str, int | None]:
    result = await session.execute(
        select(BindCode).where(BindCode.code == code)
    )
    bind = result.scalar_one_or_none()
    if not bind:
        return False, "绑定码不存在。", None
    if bind.used_at:
        return False, "绑定码已被使用。", None
    if bind.expires_at < datetime.utcnow():
        return False, "绑定码已过期，请重新生成。", None

    if bind.owner_user_id == current_user_id:
        return True, "该绑定码属于当前账号，无需绑定。", bind.owner_user_id

    await merge_users(session, current_user_id, bind.owner_user_id)
    bind.used_by_user_id = current_user_id
    bind.used_at = datetime.utcnow()
    session.add(bind)
    await session.commit()
    return True, "绑定成功，数据已合并。", bind.owner_user_id
