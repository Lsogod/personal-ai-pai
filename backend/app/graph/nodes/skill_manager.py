from __future__ import annotations

from app.graph.context import render_conversation_context
from app.graph.state import GraphState
from app.models.user import User
from app.services.commands.skills import (
    builtin_update_block_text,
    disable_usage_text,
    no_dynamic_skill_text,
    publish_usage_text,
    publish_hint_text,
    show_usage_text,
    skill_help_text,
    update_usage_text,
)
from app.services.audit import log_event
from app.services.runtime_context import get_session
from app.services.skills import (
    create_or_update_skill_draft,
    disable_skill,
    get_builtin_skill,
    get_skill,
    get_skill_version_content,
    list_skills_with_source,
    parse_skill_intent,
    publish_skill,
    render_skill_from_request,
)


def _skill_status_label(value: str | None) -> str:
    key = str(value or "").upper()
    return {
        "BUILTIN": "内置",
        "DRAFT": "草稿",
        "PUBLISHED": "已发布",
        "DISABLED": "已停用",
    }.get(key, key or "未知")


def _extract_slug(value: str) -> str:
    return (value or "").strip().lower().replace(" ", "-")


def _parse_source_and_slug(value: str) -> tuple[str, str]:
    raw = (value or "").strip()
    if ":" in raw:
        source, slug = raw.split(":", 1)
        source_key = source.strip().lower()
        if source_key in {"builtin", "user"}:
            return source_key, _extract_slug(slug)
    return "user", _extract_slug(raw)


async def skill_manager_node(state: GraphState) -> GraphState:
    message = state["message"]
    session = get_session()
    user = await session.get(User, state["user_id"])
    if not user:
        return {**state, "responses": ["未找到用户信息。"]}

    content = (message.content or "").strip()
    context_text = render_conversation_context(state)
    intent = await parse_skill_intent(content, conversation_context=context_text)
    action = str(intent.get("action") or "help").lower()
    target = str(intent.get("target") or intent.get("skill_slug") or "").strip()
    skill_name = str(intent.get("skill_name") or "").strip()
    request = str(intent.get("request") or content).strip()

    if action in {"list"}:
        rows = await list_skills_with_source(session, user.id)
        if not rows:
            return {
                **state,
                "responses": [no_dynamic_skill_text()],
            }
        lines = ["技能列表（含内置与用户）："]
        for row in rows:
            source = "内置" if row.get("source") == "builtin" else "用户"
            lines.append(
                f"- `[{source}] {row['source']}:{row['slug']}` | {row['name']} | {_skill_status_label(row['status'])} | v{row['active_version']}"
            )
        return {**state, "responses": ["\n".join(lines)]}

    if action in {"publish"}:
        slug = _extract_slug(target)
        if not slug:
            return {**state, "responses": [publish_usage_text()]}
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
            return {**state, "responses": [disable_usage_text()]}
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
        source, slug = _parse_source_and_slug(target)
        if not slug:
            return {
                **state,
                "responses": [show_usage_text()],
            }

        if source == "builtin":
            doc = get_builtin_skill(slug)
            if not doc:
                return {**state, "responses": [f"未找到内置技能 `{slug}`。"]}
            preview = (doc.content or "").strip()
            status = _skill_status_label("BUILTIN")
        else:
            skill = await get_skill(session, user.id, slug)
            if not skill:
                doc = get_builtin_skill(slug)
                if doc:
                    preview = (doc.content or "").strip()
                    status = _skill_status_label("BUILTIN")
                    source = "builtin"
                else:
                    return {**state, "responses": [f"未找到技能 `{slug}`。"]}
            else:
                content_md = await get_skill_version_content(session, skill)
                preview = (content_md or "").strip()
                status = _skill_status_label(str(skill.status))
        if len(preview) > 1200:
            preview = preview[:1200] + "\n...\n(已截断)"
        return {
            **state,
            "responses": [
                f"技能 `{source}:{slug}` | {status}\n\n{preview}"
            ],
        }

    if action in {"create", "update"}:
        existing_content = None
        if action == "update":
            source, slug = _parse_source_and_slug(target or skill_name)
            if not slug:
                return {
                    **state,
                    "responses": [update_usage_text()],
                }
            if source == "builtin":
                return {**state, "responses": [builtin_update_block_text()]}
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
                    f"已生成技能草稿 `{skill.slug}` v{version}（状态：{_skill_status_label('DRAFT')}）。\n"
                    f"{publish_hint_text(skill.slug)}\n\n"
                    f"草稿预览：\n{preview}"
                )
            ],
        }

    return {
        **state,
        "responses": [skill_help_text()],
    }
