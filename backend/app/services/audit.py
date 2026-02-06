import json
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog


async def log_event(
    session: AsyncSession,
    action: str,
    platform: str,
    user_id: Optional[int] = None,
    detail: Any = None,
) -> None:
    payload = ""
    if detail is not None:
        try:
            payload = json.dumps(detail, ensure_ascii=False)
        except Exception:
            payload = str(detail)

    event = AuditLog(
        user_id=user_id,
        platform=platform,
        action=action,
        detail=payload,
    )
    session.add(event)
    await session.commit()
