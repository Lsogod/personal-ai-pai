from fastapi import APIRouter, Depends, Query, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.user import User
from app.models.ledger import Ledger
from app.models.schedule import Schedule
from app.models.audit import AuditLog
from app.core.config import get_settings
from app.schemas.customization import (
    SkillPolicyItem,
    SkillPolicyUpdateRequest,
    ToolPolicyItem,
    ToolPolicyUpdateRequest,
    UserCustomizationResponse,
)
from app.schemas.skill import (
    SkillDetailResponse,
    SkillDraftRequest,
    SkillDraftResponse,
    SkillItemResponse,
    SkillRawDraftRequest,
)
from app.services.audit import log_event
from app.services.customization import (
    get_user_skill_policy_map,
    merge_skill_catalog_with_policy,
    normalize_skill_source,
    upsert_user_skill_policy,
    upsert_user_tool_policy,
)
from app.services.skills import (
    create_or_update_skill_draft,
    disable_skill,
    get_skill,
    get_skill_version_content,
    get_builtin_skill,
    list_skills_with_source,
    publish_skill,
    render_skill_from_request,
)
from app.services.tool_registry import list_runtime_tool_metas


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


async def _build_user_customization(
    *,
    session: AsyncSession,
    user_id: int,
) -> UserCustomizationResponse:
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="user not found")

    tools = await list_runtime_tool_metas(user_id=user_id, include_disabled=True)
    tool_rows = [
        ToolPolicyItem(
            source=str(item.get("source") or ""),
            name=str(item.get("name") or ""),
            description=str(item.get("description") or ""),
            enabled=bool(item.get("enabled")),
        )
        for item in tools
    ]

    skill_catalog = await list_skills_with_source(session, user_id)
    skill_policy_map = await get_user_skill_policy_map(session, user_id)
    merged_skills = merge_skill_catalog_with_policy(
        catalog=[
            {
                "source": normalize_skill_source(str(item.get("source") or "")),
                "slug": str(item.get("slug") or ""),
                "name": str(item.get("name") or ""),
                "description": str(item.get("description") or ""),
            }
            for item in skill_catalog
        ],
        policy_map=skill_policy_map,
    )
    skill_rows = [
        SkillPolicyItem(
            source=str(item.get("source") or ""),
            slug=str(item.get("slug") or ""),
            name=str(item.get("name") or ""),
            description=str(item.get("description") or ""),
            enabled=bool(item.get("enabled")),
        )
        for item in merged_skills
    ]

    return UserCustomizationResponse(user_id=user_id, tools=tool_rows, skills=skill_rows)


@router.get("/admin/customization/{user_id}", response_model=UserCustomizationResponse)
async def admin_get_user_customization(
    user_id: int,
    session: AsyncSession = Depends(get_session),
):
    return await _build_user_customization(session=session, user_id=user_id)


@router.post("/admin/customization/{user_id}/tool-policy", response_model=UserCustomizationResponse)
async def admin_update_user_tool_policy(
    user_id: int,
    payload: ToolPolicyUpdateRequest,
    session: AsyncSession = Depends(get_session),
):
    await upsert_user_tool_policy(
        session,
        user_id=user_id,
        source=payload.source,
        tool_name=payload.name,
        enabled=payload.enabled,
    )
    return await _build_user_customization(session=session, user_id=user_id)


@router.post("/admin/customization/{user_id}/skill-policy", response_model=UserCustomizationResponse)
async def admin_update_user_skill_policy(
    user_id: int,
    payload: SkillPolicyUpdateRequest,
    session: AsyncSession = Depends(get_session),
):
    await upsert_user_skill_policy(
        session,
        user_id=user_id,
        source=payload.source,
        skill_slug=payload.slug,
        enabled=payload.enabled,
    )
    return await _build_user_customization(session=session, user_id=user_id)


@router.post("/admin/users/{user_id}/skills/draft", response_model=SkillDraftResponse)
async def admin_skill_draft_for_user(
    user_id: int,
    payload: SkillDraftRequest,
    session: AsyncSession = Depends(get_session),
):
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="user not found")

    existing_content: str | None = None
    preferred_name = payload.skill_name
    if payload.skill_slug:
        current = await get_skill(session, user_id, payload.skill_slug)
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
        user_id=user_id,
        content_md=content_md,
        preferred_name=preferred_name or payload.skill_slug or "custom-skill",
    )
    await log_event(
        session,
        action="admin_skill_draft_saved",
        platform=user.platform,
        user_id=user.id,
        detail={"slug": skill.slug, "version": version},
    )
    return SkillDraftResponse(
        slug=skill.slug,
        version=version,
        status=skill.status,
        content_md=content_md,
    )


@router.get("/admin/users/{user_id}/skills/{slug}", response_model=SkillDetailResponse)
async def admin_skill_detail_for_user(
    user_id: int,
    slug: str,
    source: str = Query(default="user"),
    session: AsyncSession = Depends(get_session),
):
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="user not found")

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

    skill = await get_skill(session, user_id, slug)
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


@router.post("/admin/users/{user_id}/skills/raw-draft", response_model=SkillDraftResponse)
async def admin_skill_raw_draft_for_user(
    user_id: int,
    payload: SkillRawDraftRequest,
    session: AsyncSession = Depends(get_session),
):
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="user not found")

    preferred_name = payload.skill_name
    if payload.skill_slug:
        current = await get_skill(session, user_id, payload.skill_slug)
        if not current:
            raise HTTPException(status_code=404, detail="skill not found")
        preferred_name = payload.skill_slug

    skill, version = await create_or_update_skill_draft(
        session,
        user_id=user_id,
        content_md=payload.content_md,
        preferred_name=preferred_name or payload.skill_slug or "custom-skill",
    )
    await log_event(
        session,
        action="admin_skill_raw_draft_saved",
        platform=user.platform,
        user_id=user.id,
        detail={"slug": skill.slug, "version": version},
    )
    return SkillDraftResponse(
        slug=skill.slug,
        version=version,
        status=skill.status,
        content_md=payload.content_md,
    )


@router.post("/admin/users/{user_id}/skills/{slug}/publish", response_model=SkillItemResponse)
async def admin_skill_publish_for_user(
    user_id: int,
    slug: str,
    session: AsyncSession = Depends(get_session),
):
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="user not found")
    skill = await publish_skill(session, user_id, slug)
    if not skill:
        raise HTTPException(status_code=404, detail="skill not found or no version")
    await log_event(
        session,
        action="admin_skill_published",
        platform=user.platform,
        user_id=user.id,
        detail={"slug": skill.slug, "version": skill.active_version},
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


@router.post("/admin/users/{user_id}/skills/{slug}/disable", response_model=SkillItemResponse)
async def admin_skill_disable_for_user(
    user_id: int,
    slug: str,
    session: AsyncSession = Depends(get_session),
):
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="user not found")
    skill = await disable_skill(session, user_id, slug)
    if not skill:
        raise HTTPException(status_code=404, detail="skill not found")
    await log_event(
        session,
        action="admin_skill_disabled",
        platform=user.platform,
        user_id=user.id,
        detail={"slug": skill.slug},
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
