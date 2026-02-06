from fastapi import APIRouter, Depends, Query, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.user import User
from app.models.ledger import Ledger
from app.models.schedule import Schedule
from app.models.audit import AuditLog
from app.core.config import get_settings


def require_admin(x_admin_token: str = Header(default="")) -> None:
    settings = get_settings()
    if not settings.admin_token:
        raise HTTPException(status_code=403, detail="admin token not set")
    if x_admin_token != settings.admin_token:
        raise HTTPException(status_code=401, detail="invalid admin token")


router = APIRouter(prefix="/api", dependencies=[Depends(require_admin)])


@router.get("/users")
async def list_users(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(User))
    return result.scalars().all()


@router.get("/ledgers")
async def list_ledgers(
    user_id: int | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(Ledger)
    if user_id is not None:
        stmt = stmt.where(Ledger.user_id == user_id)
    result = await session.execute(stmt)
    return result.scalars().all()


@router.get("/schedules")
async def list_schedules(
    user_id: int | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(Schedule)
    if user_id is not None:
        stmt = stmt.where(Schedule.user_id == user_id)
    result = await session.execute(stmt)
    return result.scalars().all()


@router.get("/audit")
async def list_audit(
    user_id: int | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(AuditLog).order_by(AuditLog.id.desc())
    if user_id is not None:
        stmt = stmt.where(AuditLog.user_id == user_id)
    result = await session.execute(stmt)
    return result.scalars().all()
