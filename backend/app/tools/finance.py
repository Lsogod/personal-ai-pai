from datetime import datetime
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ledger import Ledger


async def insert_ledger(
    session: AsyncSession,
    user_id: int,
    amount: float,
    category: str,
    item: str,
    transaction_date: Optional[datetime] = None,
    image_url: Optional[str] = None,
    platform: str = "",
) -> Ledger:
    ledger = Ledger(
        user_id=user_id,
        amount=amount,
        category=category,
        item=item,
        transaction_date=transaction_date or datetime.utcnow(),
        image_url=image_url,
    )
    session.add(ledger)
    await session.commit()
    await session.refresh(ledger)
    try:
        from app.services.audit import log_event

        await log_event(
            session,
            action="ledger_inserted",
            platform=platform,
            user_id=user_id,
            detail={"amount": amount, "category": category, "item": item},
        )
    except Exception:
        pass
    return ledger


async def get_latest_ledger(session: AsyncSession, user_id: int) -> Ledger | None:
    stmt = (
        select(Ledger)
        .where(Ledger.user_id == user_id)
        .order_by(Ledger.id.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def update_ledger(
    session: AsyncSession,
    user_id: int,
    ledger_id: int,
    *,
    amount: float | None = None,
    category: str | None = None,
    item: str | None = None,
    transaction_date: datetime | None = None,
    platform: str = "",
) -> Ledger | None:
    ledger = await session.get(Ledger, ledger_id)
    if not ledger or ledger.user_id != user_id:
        return None

    if amount is not None:
        ledger.amount = amount
    if category:
        ledger.category = category
    if item:
        ledger.item = item
    if transaction_date:
        ledger.transaction_date = transaction_date

    session.add(ledger)
    await session.commit()
    await session.refresh(ledger)
    try:
        from app.services.audit import log_event

        await log_event(
            session,
            action="ledger_updated",
            platform=platform,
            user_id=user_id,
            detail={
                "ledger_id": ledger_id,
                "amount": ledger.amount,
                "category": ledger.category,
                "item": ledger.item,
            },
        )
    except Exception:
        pass
    return ledger


async def list_recent_ledgers(
    session: AsyncSession,
    user_id: int,
    limit: int = 10,
) -> list[Ledger]:
    stmt = (
        select(Ledger)
        .where(Ledger.user_id == user_id)
        .order_by(Ledger.id.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def delete_ledger(
    session: AsyncSession,
    user_id: int,
    ledger_id: int,
    *,
    platform: str = "",
) -> Ledger | None:
    ledger = await session.get(Ledger, ledger_id)
    if not ledger or ledger.user_id != user_id:
        return None

    snapshot = Ledger.model_validate(ledger.model_dump())
    await session.delete(ledger)
    await session.commit()
    try:
        from app.services.audit import log_event

        await log_event(
            session,
            action="ledger_deleted",
            platform=platform,
            user_id=user_id,
            detail={
                "ledger_id": snapshot.id,
                "amount": snapshot.amount,
                "category": snapshot.category,
                "item": snapshot.item,
            },
        )
    except Exception:
        pass
    return snapshot


async def summarize_ledgers_in_range(
    session: AsyncSession,
    user_id: int,
    start_at: datetime,
    end_at: datetime,
) -> dict:
    stmt = (
        select(func.sum(Ledger.amount), func.count(Ledger.id))
        .where(Ledger.user_id == user_id)
        .where(Ledger.transaction_date >= start_at)
        .where(Ledger.transaction_date < end_at)
    )
    result = await session.execute(stmt)
    total, count = result.one_or_none() or (0, 0)
    return {"total": float(total or 0), "count": int(count or 0)}


async def delete_ledgers_in_range(
    session: AsyncSession,
    user_id: int,
    start_at: datetime,
    end_at: datetime,
    *,
    platform: str = "",
) -> int:
    result = await session.execute(
        select(Ledger.id)
        .where(Ledger.user_id == user_id)
        .where(Ledger.transaction_date >= start_at)
        .where(Ledger.transaction_date < end_at)
    )
    ids = [row[0] for row in result.all()]
    if not ids:
        return 0

    deleted = 0
    for ledger_id in ids:
        row = await delete_ledger(
            session,
            user_id=user_id,
            ledger_id=ledger_id,
            platform=platform,
        )
        if row:
            deleted += 1
    return deleted


async def query_stats(session: AsyncSession, user_id: int, days: int = 30) -> dict:
    cutoff = datetime.utcnow().timestamp() - days * 86400
    stmt = (
        select(func.sum(Ledger.amount), func.count(Ledger.id))
        .where(Ledger.user_id == user_id)
        .where(Ledger.transaction_date >= datetime.utcfromtimestamp(cutoff))
    )
    result = await session.execute(stmt)
    total, count = result.one_or_none() or (0, 0)
    return {"total": float(total or 0), "count": int(count or 0)}
