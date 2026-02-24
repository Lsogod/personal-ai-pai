from __future__ import annotations

import re

from sqlalchemy import select

from app.graph.context import render_conversation_context
from app.graph.state import GraphState
from app.models.skill import Skill
from app.models.user import User
from app.services.commands.skills import (
    builtin_delete_block_text,
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
    delete_all_user_skills,
    delete_skill,
    disable_skill,
    get_builtin_skill,
    get_skill,
    get_skill_version_content,
    list_skills_with_source,
    list_user_skills,
    parse_skill_intent,
    publish_skill,
    render_skill_from_request,
)

GENERIC_SKILL_SCOPE_PATTERN = re.compile(
    r"^(我的技能|我创建的技能|用户技能|全部技能|所有技能|全部|所有|全量)$",
    re.IGNORECASE,
)
SKILL_FOLLOWUP_UPDATE_PATTERN = re.compile(
    r"(改为|改成|修改|调整|限制|收紧|放宽|change|update|revise)",
    re.IGNORECASE,
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


async def _resolve_user_skill_slug(session, user_id: int, target: str) -> tuple[str | None, str | None]:
    raw = (target or "").strip()
    if not raw:
        return None, "missing"
    if GENERIC_SKILL_SCOPE_PATTERN.match(raw):
        return None, "missing"

    source, slug = _parse_source_and_slug(raw)
    if source == "builtin":
        return None, "builtin"

    if slug:
        existing = await get_skill(session, user_id, slug)
        if existing:
            return slug, None

    stmt = select(Skill).where(Skill.user_id == user_id)
    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    if not rows:
        return None, "not_found"

    raw_lower = raw.lower()
    exact = [row for row in rows if str(row.name or "").strip().lower() == raw_lower]
    if len(exact) == 1:
        return str(exact[0].slug), None

    fuzzy = [row for row in rows if raw_lower in str(row.name or "").strip().lower()]
    if len(fuzzy) == 1:
        return str(fuzzy[0].slug), None
    if len(exact) > 1 or len(fuzzy) > 1:
        candidates = exact if len(exact) > 1 else fuzzy
        hint = "、".join([f"`{item.slug}`" for item in candidates[:5]])
        return None, f"ambiguous:{hint}"

    return None, "not_found"


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
    delete_scope = str(intent.get("delete_scope") or "unknown").strip().lower()
    delete_confirmed = bool(intent.get("confirmed") is True)
    clarification_needed = bool(intent.get("clarification_needed") is True)

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

    if action in {"delete"}:
        user_skill_rows = await list_user_skills(session, user.id)
        if not user_skill_rows:
            return {**state, "responses": ["你当前没有可删除的用户技能。"]}

        if not target:
            if delete_scope == "all":
                if not delete_confirmed:
                    preview = "、".join(
                        [f"`{item.get('name') or item.get('slug')}({item.get('slug')})`" for item in user_skill_rows[:8]]
                    )
                    suffix = "..." if len(user_skill_rows) > 8 else ""
                    return {
                        **state,
                        "responses": [
                            (
                                "你是要删除全部用户技能吗？"
                                f"当前技能：{preview}{suffix}。"
                                "请回复“确认删除全部技能”后我再执行。"
                            )
                        ],
                    }
                deleted_count, deleted_versions, deleted_slugs = await delete_all_user_skills(session, user.id)
                if deleted_count <= 0:
                    return {**state, "responses": ["你当前没有可删除的用户技能。"]}
                await log_event(
                    session,
                    action="skill_deleted_all",
                    platform=user.platform,
                    user_id=user.id,
                    detail={
                        "deleted_count": deleted_count,
                        "deleted_versions": deleted_versions,
                        "deleted_slugs": deleted_slugs[:20],
                    },
                )
                preview = "、".join([f"`{item}`" for item in deleted_slugs[:6]])
                suffix = "..." if len(deleted_slugs) > 6 else ""
                return {
                    **state,
                    "responses": [
                        f"已删除你创建的 {deleted_count} 个技能（清理版本 {deleted_versions} 条）：{preview}{suffix}"
                    ],
                }

            candidates = "、".join(
                [f"`{str(item.get('name') or item.get('slug'))}({str(item.get('slug') or '')})`" for item in user_skill_rows[:8]]
            )
            clarify_suffix = "（请直接回复其中一个 slug 或技能名）"
            if clarification_needed:
                clarify_suffix = "（当前无法确认你要删单个还是全部，请先明确）"
            return {
                **state,
                "responses": [
                    f"我还不能确认你要删除哪个技能。当前可删：{candidates}。{clarify_suffix}"
                ],
            }

        slug, resolve_state = await _resolve_user_skill_slug(session, user.id, target)
        if resolve_state == "missing":
            candidates = "、".join(
                [f"`{str(item.get('name') or item.get('slug'))}({str(item.get('slug') or '')})`" for item in user_skill_rows[:8]]
            )
            return {
                **state,
                "responses": [f"请指定要删除的技能名或 slug。当前可删：{candidates}"],
            }
        if resolve_state == "builtin":
            return {**state, "responses": [builtin_delete_block_text()]}
        if resolve_state and resolve_state.startswith("ambiguous:"):
            slug_hint = resolve_state.split(":", 1)[1] if ":" in resolve_state else ""
            return {
                **state,
                "responses": [f"匹配到多个技能，请指定 slug 再删，例如：/skill delete {slug_hint}"],
            }
        if not slug:
            return {**state, "responses": [f"未找到技能 `{target}`。可先用 `/skill list` 查看可删技能。"]}

        skill_obj = await get_skill(session, user.id, slug)
        skill_display_name = str(skill_obj.name) if skill_obj and str(skill_obj.name or "").strip() else slug
        ok, deleted_versions = await delete_skill(session, user.id, slug)
        if not ok:
            return {**state, "responses": [f"未找到技能 `{slug}`。"]}
        await log_event(
            session,
            action="skill_deleted",
            platform=user.platform,
            user_id=user.id,
            detail={"slug": slug, "name": skill_display_name, "deleted_versions": deleted_versions},
        )
        return {
            **state,
            "responses": [f"已删除技能：`{skill_display_name}`（slug: `{slug}`，清理版本 {deleted_versions} 条）。"],
        }

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

    # Follow-up edit fallback: user continues refining a previously created skill
    # without explicitly writing '/skill update ...'.
    if action == "help" and SKILL_FOLLOWUP_UPDATE_PATTERN.search(content):
        user_skill_rows = await list_user_skills(session, user.id)
        if not user_skill_rows:
            return {**state, "responses": [skill_help_text()]}
        if len(user_skill_rows) > 1:
            candidates = "、".join(
                [f"`{str(item.get('name') or item.get('slug'))}({str(item.get('slug') or '')})`" for item in user_skill_rows[:8]]
            )
            return {
                **state,
                "responses": [
                    f"检测到你在修改技能，但我还不能确认目标。请指定要更新的技能 slug（例如：`/skill update <slug> ...`）。当前可选：{candidates}"
                ],
            }
        only_slug = str(user_skill_rows[0].get("slug") or "").strip()
        only_skill = await get_skill(session, user.id, only_slug)
        if not only_skill:
            return {**state, "responses": [skill_help_text()]}
        existing_content = await get_skill_version_content(session, only_skill)
        markdown = await render_skill_from_request(
            user_request=content,
            preferred_name=str(only_skill.name or only_slug),
            existing_content=existing_content,
        )
        skill, version = await create_or_update_skill_draft(
            session,
            user_id=user.id,
            content_md=markdown,
            preferred_name=str(only_skill.name or only_slug),
        )
        await log_event(
            session,
            action="skill_draft_saved",
            platform=user.platform,
            user_id=user.id,
            detail={"slug": skill.slug, "version": version, "via": "followup_update"},
        )
        preview = markdown
        if len(preview) > 800:
            preview = preview[:800] + "\n...\n(已截断)"
        return {
            **state,
            "responses": [
                (
                    f"已更新技能草稿：`{skill.name}`（slug: `{skill.slug}`，v{version}）。\n"
                    f"{publish_hint_text(skill.slug)}\n\n"
                    f"草稿预览：\n{preview}"
                )
            ],
        }

    return {
        **state,
        "responses": [skill_help_text()],
    }
