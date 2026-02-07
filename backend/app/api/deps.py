from typing import Tuple

from sqlalchemy import select

from app.db.session import AsyncSession
from app.models.identity import UserIdentity
from app.models.user import User
from app.services.binding import ensure_identity


async def get_or_create_user(
    session: AsyncSession,
    platform: str,
    platform_id: str,
) -> Tuple[User, bool]:
    identity_result = await session.execute(
        select(UserIdentity).where(
            UserIdentity.platform == platform,
            UserIdentity.platform_id == platform_id,
        )
    )
    identity = identity_result.scalar_one_or_none()
    if identity:
        user = await session.get(User, identity.user_id)
        if user:
            return user, False

    result = await session.execute(
        select(User).where(User.platform == platform, User.platform_id == platform_id)
    )
    user = result.scalar_one_or_none()
    if user:
        await ensure_identity(session, user.id, platform, platform_id)
        return user, False

    user = User(platform=platform, platform_id=platform_id)
    session.add(user)
    await session.flush()
    await ensure_identity(session, user.id, platform, platform_id)
    await session.commit()
    await session.refresh(user)
    return user, True
