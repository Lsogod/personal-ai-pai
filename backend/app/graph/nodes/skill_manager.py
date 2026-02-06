from __future__ import annotations

from app.graph.state import GraphState
from app.models.user import User
from app.services.audit import log_event
from app.services.runtime_context import get_session
from app.services.skills import (
    create_or_update_skill_draft,
    disable_skill,
    get_skill,
    get_skill_version_content,
    list_user_skills,
    parse_skill_intent,
    publish_skill,
    render_skill_from_request,
)


def _extract_slug(value: str) -> str:
    return (value or "").strip().lower().replace(" ", "-")


async def skill_manager_node(state: GraphState) -> GraphState:
    message = state["message"]
    session = get_session()
    user = await session.get(User, state["user_id"])
    if not user:
        return {**state, "responses": ["未找到用户信息。"]}

    content = (message.content or "").strip()
    intent = await parse_skill_intent(content)
    action = str(intent.get("action") or "help").lower()
    target = str(intent.get("target") or intent.get("skill_slug") or "").strip()
    skill_name = str(intent.get("skill_name") or "").strip()
    request = str(intent.get("request") or content).strip()

    if action in {"list"}:
        rows = await list_user_skills(session, user.id)
        if not rows:
            return {
                **state,
                "responses": ["你还没有动态技能。可发送：`/skill create 翻译专家` 来创建。"],
            }
        lines = ["你的技能列表："]
        for row in rows:
            lines.append(
                f"- `{row['slug']}` | {row['name']} | {row['status']} | v{row['active_version']}"
            )
        return {**state, "responses": ["\n".join(lines)]}

    if action in {"publish"}:
        slug = _extract_slug(target)
        if not slug:
            return {**state, "responses": ["请指定要发布的技能，例如：`/skill publish translator`"]}
        skill = await publish_skill(session, user.id, slug)
        if not skill:
            return {**state, "responses": [f"未找到技能 `{slug}`，或该技能没有可发布版本。"]}
        await log_event(
            session,
            action="skill_published",
            platform=user.platform,
            user_id=user.id,
            detail={"slug": slug, "version": skill.active_version},
        )
        return {**state, "responses": [f"已发布技能 `{slug}` (v{skill.active_version})。"]}

    if action in {"disable"}:
        slug = _extract_slug(target)
        if not slug:
            return {**state, "responses": ["请指定要停用的技能，例如：`/skill disable translator`"]}
        skill = await disable_skill(session, user.id, slug)
        if not skill:
            return {**state, "responses": [f"未找到技能 `{slug}`。"]}
        await log_event(
            session,
            action="skill_disabled",
            platform=user.platform,
            user_id=user.id,
            detail={"slug": slug},
        )
        return {**state, "responses": [f"已停用技能 `{slug}`。"]}

    if action in {"show"}:
        slug = _extract_slug(target)
        if not slug:
            return {**state, "responses": ["请指定要查看的技能，例如：`/skill show translator`"]}
        skill = await get_skill(session, user.id, slug)
        if not skill:
            return {**state, "responses": [f"未找到技能 `{slug}`。"]}
        content_md = await get_skill_version_content(session, skill)
        preview = (content_md or "").strip()
        if len(preview) > 1200:
            preview = preview[:1200] + "\n...\n(已截断)"
        return {
            **state,
            "responses": [
                f"技能 `{slug}` | {skill.status} | v{skill.active_version}\n\n{preview}"
            ],
        }

    if action in {"create", "update"}:
        existing_content = None
        if action == "update":
            slug = _extract_slug(target or skill_name)
            if not slug:
                return {
                    **state,
                    "responses": ["请指定要更新的技能，例如：`/skill update translator 新增术语保留规则`"],
                }
            existing = await get_skill(session, user.id, slug)
            if not existing:
                return {**state, "responses": [f"未找到技能 `{slug}`。"]}
            existing_content = await get_skill_version_content(session, existing)
            if not request:
                request = content

        markdown = await render_skill_from_request(
            user_request=request,
            preferred_name=skill_name or target or "custom-skill",
            existing_content=existing_content,
        )
        skill, version = await create_or_update_skill_draft(
            session,
            user_id=user.id,
            content_md=markdown,
            preferred_name=skill_name or target or "custom-skill",
        )
        await log_event(
            session,
            action="skill_draft_saved",
            platform=user.platform,
            user_id=user.id,
            detail={"slug": skill.slug, "version": version},
        )
        preview = markdown
        if len(preview) > 800:
            preview = preview[:800] + "\n...\n(已截断)"
        return {
            **state,
            "responses": [
                (
                    f"已生成技能草稿 `{skill.slug}` v{version}（状态：DRAFT）。\n"
                    f"发送 `/skill publish {skill.slug}` 后生效。\n\n"
                    f"草稿预览：\n{preview}"
                )
            ],
        }

    return {
        **state,
        "responses": [
            (
                "技能命令：\n"
                "- `/skill list`\n"
                "- `/skill create <技能名或需求>`\n"
                "- `/skill update <slug> <更新需求>`\n"
                "- `/skill show <slug>`\n"
                "- `/skill publish <slug>`\n"
                "- `/skill disable <slug>`"
            )
        ],
    }
