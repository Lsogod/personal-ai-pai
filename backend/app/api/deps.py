from typing import Tuple

from sqlalchemy import select

from app.db.session import AsyncSession
from app.models.user import User


async def get_or_create_user(
    session: AsyncSession,
    platform: str,
    platform_id: str,
) -> Tuple[User, bool]:
    result = await session.execute(
        select(User).where(User.platform == platform, User.platform_id == platform_id)
    )
    user = result.scalar_one_or_none()
    if user:
        return user, False

    user = User(platform=platform, platform_id=platform_id)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user, True
