from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customization import UserSkillPolicy, UserToolPolicy


ALLOWED_TOOL_SOURCES = {"builtin", "mcp"}
ALLOWED_SKILL_SOURCES = {"builtin", "user"}


def normalize_tool_source(value: str) -> str:
    source = (value or "").strip().lower()
    return source if source in ALLOWED_TOOL_SOURCES else "builtin"


def normalize_skill_source(value: str) -> str:
    source = (value or "").strip().lower()
    return source if source in ALLOWED_SKILL_SOURCES else "builtin"


def make_tool_key(source: str, tool_name: str) -> str:
    return f"{normalize_tool_source(source)}:{(tool_name or '').strip()}"


def make_skill_key(source: str, skill_slug: str) -> str:
    return f"{normalize_skill_source(source)}:{(skill_slug or '').strip()}"


def merge_tool_catalog_with_policy(
    *,
    catalog: list[dict],
    policy_map: dict[str, bool],
) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    for item in catalog:
        source = normalize_tool_source(str(item.get("source") or "builtin"))
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        key = make_tool_key(source, name)
        if key in seen:
            continue
        seen.add(key)
        enabled = bool(policy_map.get(key, item.get("enabled", True)))
        rows.append(
            {
                "source": source,
                "name": name,
                "description": str(item.get("description") or "").strip(),
                "enabled": enabled,
            }
        )
    for key, enabled in policy_map.items():
        try:
            source, name = key.split(":", 1)
        except ValueError:
            continue
        source = normalize_tool_source(source)
        name = (name or "").strip()
        if not name:
            continue
        merge_key = make_tool_key(source, name)
        if merge_key in seen:
            continue
        seen.add(merge_key)
        rows.append(
            {
                "source": source,
                "name": name,
                "description": "策略自定义项",
                "enabled": bool(enabled),
            }
        )
    return rows


def merge_skill_catalog_with_policy(
    *,
    catalog: list[dict],
    policy_map: dict[str, bool],
) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    for item in catalog:
        source = normalize_skill_source(str(item.get("source") or "builtin"))
        slug = str(item.get("slug") or "").strip()
        if not slug:
            continue
        key = make_skill_key(source, slug)
        if key in seen:
            continue
        seen.add(key)
        enabled = bool(policy_map.get(key, True))
        rows.append(
            {
                "source": source,
                "slug": slug,
                "name": str(item.get("name") or slug).strip(),
                "description": str(item.get("description") or "").strip(),
                "enabled": enabled,
            }
        )
    for key, enabled in policy_map.items():
        try:
            source, slug = key.split(":", 1)
        except ValueError:
            continue
        source = normalize_skill_source(source)
        slug = (slug or "").strip()
        if not slug:
            continue
        merge_key = make_skill_key(source, slug)
        if merge_key in seen:
            continue
        seen.add(merge_key)
        rows.append(
            {
                "source": source,
                "slug": slug,
                "name": slug,
                "description": "策略自定义项",
                "enabled": bool(enabled),
            }
        )
    return rows


async def list_user_tool_policies(session: AsyncSession, user_id: int) -> list[UserToolPolicy]:
    result = await session.execute(
        select(UserToolPolicy)
        .where(UserToolPolicy.user_id == user_id)
        .order_by(UserToolPolicy.source.asc(), UserToolPolicy.tool_name.asc())
    )
    return list(result.scalars().all())


async def list_user_skill_policies(session: AsyncSession, user_id: int) -> list[UserSkillPolicy]:
    result = await session.execute(
        select(UserSkillPolicy)
        .where(UserSkillPolicy.user_id == user_id)
        .order_by(UserSkillPolicy.source.asc(), UserSkillPolicy.skill_slug.asc())
    )
    return list(result.scalars().all())


async def get_user_tool_policy_map(session: AsyncSession, user_id: int) -> dict[str, bool]:
    rows = await list_user_tool_policies(session, user_id)
    return {make_tool_key(row.source, row.tool_name): bool(row.enabled) for row in rows}


async def get_user_skill_policy_map(session: AsyncSession, user_id: int) -> dict[str, bool]:
    rows = await list_user_skill_policies(session, user_id)
    return {make_skill_key(row.source, row.skill_slug): bool(row.enabled) for row in rows}


async def upsert_user_tool_policy(
    session: AsyncSession,
    *,
    user_id: int,
    source: str,
    tool_name: str,
    enabled: bool,
) -> UserToolPolicy:
    source_norm = normalize_tool_source(source)
    target_name = (tool_name or "").strip()
    result = await session.execute(
        select(UserToolPolicy).where(
            UserToolPolicy.user_id == user_id,
            UserToolPolicy.source == source_norm,
            UserToolPolicy.tool_name == target_name,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = UserToolPolicy(
            user_id=user_id,
            source=source_norm,
            tool_name=target_name,
            enabled=bool(enabled),
        )
        session.add(row)
    else:
        row.enabled = bool(enabled)
        row.updated_at = datetime.utcnow()
        session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def upsert_user_skill_policy(
    session: AsyncSession,
    *,
    user_id: int,
    source: str,
    skill_slug: str,
    enabled: bool,
) -> UserSkillPolicy:
    source_norm = normalize_skill_source(source)
    target_slug = (skill_slug or "").strip()
    result = await session.execute(
        select(UserSkillPolicy).where(
            UserSkillPolicy.user_id == user_id,
            UserSkillPolicy.source == source_norm,
            UserSkillPolicy.skill_slug == target_slug,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = UserSkillPolicy(
            user_id=user_id,
            source=source_norm,
            skill_slug=target_slug,
            enabled=bool(enabled),
        )
        session.add(row)
    else:
        row.enabled = bool(enabled)
        row.updated_at = datetime.utcnow()
        session.add(row)
    await session.commit()
    await session.refresh(row)
    return row
