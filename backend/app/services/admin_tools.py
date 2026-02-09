from __future__ import annotations

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.admin_tool import AdminToolSwitch


def make_tool_key(source: str, name: str) -> str:
    return f"{(source or '').strip().lower()}:{(name or '').strip().lower()}"


async def load_tool_enabled_map() -> dict[str, bool]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(AdminToolSwitch))
        rows = result.scalars().all()
    return {make_tool_key(row.tool_source, row.tool_name): bool(row.enabled) for row in rows}


async def is_tool_enabled(source: str, name: str) -> bool:
    key = make_tool_key(source, name)
    mapping = await load_tool_enabled_map()
    return mapping.get(key, True)


async def set_tool_enabled(source: str, name: str, enabled: bool) -> AdminToolSwitch:
    tool_source = (source or "").strip().lower()
    tool_name = (name or "").strip()
    if not tool_source or not tool_name:
        raise ValueError("tool source/name required")

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AdminToolSwitch).where(
                AdminToolSwitch.tool_source == tool_source,
                AdminToolSwitch.tool_name == tool_name,
            )
        )
        row = result.scalar_one_or_none()
        if not row:
            row = AdminToolSwitch(tool_source=tool_source, tool_name=tool_name, enabled=enabled)
        else:
            row.enabled = enabled
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row
