from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field
from sqlalchemy import delete as sa_delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.skill import Skill, SkillStatus, SkillVersion
from app.services.commands.skills import parse_skill_command_fallback
from app.services.llm import get_llm


SKILLS_DIR = Path(__file__).resolve().parents[2] / "skills"
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
_WORD_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9_-]+")
_SKILL_SLUG_RE = re.compile(r"\b([a-z0-9][a-z0-9-]{2,63})\b", re.IGNORECASE)
_FOLLOWUP_UPDATE_PATTERN = re.compile(
    r"(改为|改成|修改|调整|限制|收紧|放宽|改下|改一下|change|update|revise)",
    re.IGNORECASE,
)


@dataclass
class SkillDoc:
    source: str
    slug: str
    name: str
    description: str
    content: str


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", (value or "").lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    if slug:
        return slug[:64]
    digest = hashlib.md5((value or "skill").encode("utf-8")).hexdigest()[:10]
    return f"skill-{digest}"


def _parse_frontmatter(content: str) -> tuple[str, str]:
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return "", ""
    raw = match.group(1)
    name = ""
    description = ""
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("name:"):
            name = line.split(":", 1)[1].strip().strip('"').strip("'")
        elif line.startswith("description:"):
            description = line.split(":", 1)[1].strip().strip('"').strip("'")
    return name, description


def _validate_skill_content(content: str) -> tuple[bool, list[str]]:
    errors: list[str] = []
    name, description = _parse_frontmatter(content)
    if not name:
        errors.append("frontmatter 缺少 name")
    if not description:
        errors.append("frontmatter 缺少 description")
    for section in ("# Trigger", "# Workflow", "# Constraints", "# Output Contract"):
        if section not in content:
            errors.append(f"缺少章节: {section}")
    return (len(errors) == 0), errors


class SkillIntentExtraction(BaseModel):
    action: str = Field(default="help")
    skill_name: str = Field(default="")
    skill_slug: str = Field(default="")
    target: str = Field(default="")
    request: str = Field(default="")
    delete_scope: str = Field(default="unknown")
    confirmed: bool = Field(default=False)
    clarification_needed: bool = Field(default=False)


class SkillFollowupExtraction(BaseModel):
    action: str = Field(default="help")
    target: str = Field(default="")
    request: str = Field(default="")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


def _iter_static_skill_files() -> Iterable[Path]:
    if not SKILLS_DIR.exists():
        return []

    yielded: list[Path] = []
    # New convention: backend/skills/<skill-name>/SKILL.md
    for child in sorted(SKILLS_DIR.iterdir()):
        skill_md = child / "SKILL.md"
        if child.is_dir() and skill_md.exists():
            yielded.append(skill_md)
    # Backward compatible: backend/skills/*.md (exclude SKILL.md nested)
    for path in sorted(SKILLS_DIR.glob("*.md")):
        yielded.append(path)
    return yielded


def _load_static_skill_docs() -> list[SkillDoc]:
    docs: list[SkillDoc] = []
    for path in _iter_static_skill_files():
        try:
            content = path.read_text(encoding="utf-8")
        except Exception:
            continue
        name, description = _parse_frontmatter(content)
        slug = _slugify(name or path.stem)
        docs.append(
            SkillDoc(
                source="static",
                slug=slug,
                name=name or path.stem,
                description=description,
                content=content,
            )
        )
    return docs


def _score_skill(doc: SkillDoc, query: str) -> int:
    if not query:
        return 0
    score = 0
    q = query.lower()
    name = (doc.name or "").lower()
    slug = (doc.slug or "").lower()
    desc = (doc.description or "").lower()

    if name and name in q:
        score += 10
    if slug and slug in q:
        score += 8

    for token in _WORD_RE.findall(f"{name} {slug} {desc}"):
        t = token.lower()
        if len(t) < 2:
            continue
        if t in q:
            score += 2

    return score


async def list_user_skills(session: AsyncSession, user_id: int) -> list[dict]:
    stmt = (
        select(Skill)
        .where(Skill.user_id == user_id)
        .order_by(Skill.updated_at.desc())
    )
    result = await session.execute(stmt)
    skills = result.scalars().all()
    return [
        {
            "slug": skill.slug,
            "name": skill.name,
            "description": skill.description,
            "status": skill.status,
            "active_version": skill.active_version,
            "source": "user",
            "read_only": False,
        }
        for skill in skills
    ]


def list_builtin_skills() -> list[dict]:
    docs = _load_static_skill_docs()
    return [
        {
            "slug": doc.slug,
            "name": doc.name,
            "description": doc.description,
            "status": "BUILTIN",
            "active_version": 1,
            "source": "builtin",
            "read_only": True,
        }
        for doc in docs
    ]


def get_builtin_skill(slug: str) -> SkillDoc | None:
    target = _slugify(slug or "")
    for doc in _load_static_skill_docs():
        if _slugify(doc.slug) == target:
            return doc
    return None


async def list_skills_with_source(session: AsyncSession, user_id: int) -> list[dict]:
    builtin = list_builtin_skills()
    user = await list_user_skills(session, user_id)
    builtin_sorted = sorted(builtin, key=lambda item: item["name"].lower())
    return [*builtin_sorted, *user]


async def get_skill(session: AsyncSession, user_id: int, slug: str) -> Skill | None:
    stmt = select(Skill).where(Skill.user_id == user_id, Skill.slug == slug)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_latest_version(session: AsyncSession, skill_id: int) -> int:
    stmt = select(func.max(SkillVersion.version)).where(SkillVersion.skill_id == skill_id)
    result = await session.execute(stmt)
    latest = result.scalar_one_or_none()
    return int(latest or 0)


async def get_skill_version_content(
    session: AsyncSession,
    skill: Skill,
    version: int | None = None,
) -> str | None:
    target_version = version or skill.active_version
    stmt = select(SkillVersion).where(
        SkillVersion.skill_id == skill.id,
        SkillVersion.version == target_version,
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    return row.content_md if row else None


async def create_or_update_skill_draft(
    session: AsyncSession,
    user_id: int,
    content_md: str,
    preferred_name: str | None = None,
) -> tuple[Skill, int]:
    name, description = _parse_frontmatter(content_md)
    base_name = preferred_name or name or "custom-skill"
    slug = _slugify(base_name)

    skill = await get_skill(session, user_id, slug)
    if not skill:
        skill = Skill(
            user_id=user_id,
            slug=slug,
            name=name or preferred_name or slug,
            description=description,
            status=SkillStatus.DRAFT,
            active_version=1,
        )
        session.add(skill)
        await session.flush()
        version = 1
    else:
        version = await get_latest_version(session, skill.id) + 1
        skill.name = name or skill.name
        skill.description = description or skill.description
        skill.status = SkillStatus.DRAFT
        skill.active_version = version
        session.add(skill)

    session.add(
        SkillVersion(
            skill_id=skill.id,
            version=version,
            content_md=content_md,
        )
    )
    await session.commit()
    await session.refresh(skill)
    return skill, version


async def publish_skill(session: AsyncSession, user_id: int, slug: str) -> Skill | None:
    skill = await get_skill(session, user_id, slug)
    if not skill:
        return None

    latest = await get_latest_version(session, skill.id)
    if latest <= 0:
        return None

    skill.status = SkillStatus.PUBLISHED
    skill.active_version = latest
    session.add(skill)
    await session.commit()
    await session.refresh(skill)
    return skill


async def disable_skill(session: AsyncSession, user_id: int, slug: str) -> Skill | None:
    skill = await get_skill(session, user_id, slug)
    if not skill:
        return None
    skill.status = SkillStatus.DISABLED
    session.add(skill)
    await session.commit()
    await session.refresh(skill)
    return skill


async def delete_skill(session: AsyncSession, user_id: int, slug: str) -> tuple[bool, int]:
    skill = await get_skill(session, user_id, slug)
    if not skill:
        return False, 0

    versions_result = await session.execute(
        sa_delete(SkillVersion).where(SkillVersion.skill_id == skill.id)
    )
    deleted_versions = int(versions_result.rowcount or 0)

    await session.execute(
        sa_delete(Skill).where(Skill.id == skill.id, Skill.user_id == user_id)
    )
    await session.commit()
    return True, max(0, deleted_versions)


async def delete_all_user_skills(
    session: AsyncSession,
    user_id: int,
) -> tuple[int, int, list[str]]:
    result = await session.execute(
        select(Skill).where(Skill.user_id == user_id).order_by(Skill.id.asc())
    )
    skills = list(result.scalars().all())
    if not skills:
        return 0, 0, []

    skill_ids = [int(item.id) for item in skills if int(item.id or 0) > 0]
    slugs = [str(item.slug) for item in skills if str(item.slug or "").strip()]
    if not skill_ids:
        return 0, 0, []

    versions_result = await session.execute(
        sa_delete(SkillVersion).where(SkillVersion.skill_id.in_(skill_ids))
    )
    deleted_versions = int(versions_result.rowcount or 0)

    await session.execute(
        sa_delete(Skill).where(Skill.user_id == user_id, Skill.id.in_(skill_ids))
    )
    await session.commit()
    return len(skill_ids), max(0, deleted_versions), slugs


async def render_skill_from_request(
    user_request: str,
    preferred_name: str | None = None,
    existing_content: str | None = None,
) -> str:
    llm = get_llm(node_name="skills")
    system = SystemMessage(
        content=(
            "你是 Skill 规范生成器。输出必须是可直接保存的 SKILL.md。"
            "必须包含 frontmatter(name, description) 与四个一级章节："
            "# Trigger # Workflow # Constraints # Output Contract。"
            "内容要精炼，遵循渐进式披露：只写核心流程，细节用简短引用提示。"
            "只输出 markdown，不要解释。"
        )
    )

    prompt = [f"用户需求:\n{user_request.strip()}"]
    if preferred_name:
        prompt.append(f"建议技能名: {preferred_name}")
    if existing_content:
        prompt.append("请在现有技能基础上更新:\n" + existing_content)
    human = HumanMessage(content="\n\n".join(prompt))

    response = await llm.ainvoke([system, human])
    content = str(response.content).strip()

    ok, errors = _validate_skill_content(content)
    if ok:
        return content

    # One retry with explicit error feedback.
    retry = HumanMessage(
        content=(
            "上一次输出不符合格式，请修正:\n"
            + "\n".join(errors)
            + "\n仅输出合规的 SKILL.md markdown。"
        )
    )
    response2 = await llm.ainvoke([system, human, retry])
    return str(response2.content).strip()


async def parse_skill_intent(
    message_content: str,
    conversation_context: str = "",
) -> dict:
    text = (message_content or "").strip()
    llm = get_llm(node_name="skills")
    runnable = llm.with_structured_output(SkillIntentExtraction)
    system = SystemMessage(
        content=(
            "你是意图解析器。请将用户关于技能管理的消息解析为结构化字段。"
            "只输出一个 JSON 对象，不要输出解释文本。"
            "action 仅可为: create, update, publish, disable, delete, list, show, help。"
            "返回字段: action, skill_name, skill_slug, target, request, delete_scope, confirmed, clarification_needed。"
            "delete_scope 仅可为 single/all/unknown。"
            "当 action=delete 且用户明确说“全部/所有/我的技能都删掉”时 delete_scope=all。"
            "当 action=delete 且用户明确指定某个技能（slug/名称）时 delete_scope=single，并填 target。"
            "若你无法确认是删单个还是删全部，delete_scope=unknown 且 clarification_needed=true。"
            "confirmed 仅在用户明确确认执行高风险删除时为 true（例如“确认删除全部技能”）。"
            "必须同时支持自然语言和命令式输入（例如 `/skill publish demo`）。"
            "仅返回 schema 定义字段。"
        )
    )
    human = HumanMessage(
        content=(
            f"会话上下文:\n{conversation_context or '（无）'}\n\n"
            f"用户输入:\n{text}"
        )
    )
    allowed_actions = {"create", "update", "publish", "disable", "delete", "list", "show", "help"}
    fallback = parse_skill_command_fallback(text, allowed_actions)
    try:
        parsed = await runnable.ainvoke([system, human])
        data = (
            parsed.model_dump()
            if isinstance(parsed, BaseModel)
            else dict(parsed or {}) if isinstance(parsed, dict) else {}
        )
        action = str(data.get("action") or "").strip().lower()
        if action in allowed_actions:
            # LLM sometimes over-returns "help" for clear NL operations.
            # Keep deterministic fallback as a correction path.
            if action == "help":
                fallback_action = str(fallback.get("action") or "").strip().lower()
                if fallback_action in allowed_actions and fallback_action != "help":
                    return fallback
                followup = await _parse_skill_followup_intent(
                    text=text,
                    conversation_context=conversation_context,
                )
                if followup:
                    return followup
            return data
    except Exception:
        pass

    followup = await _parse_skill_followup_intent(
        text=text,
        conversation_context=conversation_context,
    )
    if followup:
        return followup
    return fallback


async def _parse_skill_followup_intent(*, text: str, conversation_context: str) -> dict:
    if not text:
        return {}
    llm = get_llm(node_name="skills")
    runnable = llm.with_structured_output(SkillFollowupExtraction)
    system = SystemMessage(
        content=(
            "你是技能跟进意图解析器，请仅返回结构化字段。"
            "只输出一个 JSON 对象，不要输出解释文本。"
            "当用户在上一轮创建/展示技能后，用自然语言继续修改约束（如“标题改为...”“把正文限制到80字”），"
            "应识别为 action=update。"
            "从会话上下文中提取最近一个用户技能 slug 填入 target。"
            "字段: action,target,request,confidence。"
            "action 仅可为 update/help。"
            "若无法确认是技能修改，返回 action=help。"
        )
    )
    human = HumanMessage(
        content=(
            f"会话上下文:\n{conversation_context or '（无）'}\n\n"
            f"用户输入:\n{text}"
        )
    )
    try:
        parsed = await runnable.ainvoke([system, human])
        data = (
            parsed.model_dump()
            if isinstance(parsed, BaseModel)
            else dict(parsed or {}) if isinstance(parsed, dict) else {}
        )
        action = str(data.get("action") or "").strip().lower()
        confidence = float(data.get("confidence") or 0.0)
        target = str(data.get("target") or "").strip()
        if not target:
            target = _extract_recent_skill_slug_from_context(conversation_context)
        if action == "update" and confidence >= 0.55 and target:
            return {
                "action": "update",
                "target": target,
                "request": text,
            }
        if confidence >= 0.55 and target and _FOLLOWUP_UPDATE_PATTERN.search(text):
            return {
                "action": "update",
                "target": target,
                "request": text,
            }
    except Exception:
        return {}
    return {}


def _extract_recent_skill_slug_from_context(conversation_context: str) -> str:
    text = str(conversation_context or "")
    if not text:
        return ""
    matches = list(_SKILL_SLUG_RE.finditer(text))
    if not matches:
        return ""
    for match in reversed(matches):
        token = str(match.group(1) or "").strip().lower()
        if token.startswith("skill-"):
            return token
    return ""


async def load_skills(
    session: AsyncSession,
    user_id: int,
    query: str | None = None,
    top_k: int = 4,
) -> str:
    docs: list[SkillDoc] = []
    docs.extend(_load_static_skill_docs())

    stmt = select(Skill).where(
        Skill.user_id == user_id,
        Skill.status == SkillStatus.PUBLISHED,
    )
    result = await session.execute(stmt)
    dynamic_skills = result.scalars().all()

    for item in dynamic_skills:
        content = await get_skill_version_content(session, item)
        if not content:
            continue
        docs.append(
            SkillDoc(
                source="user",
                slug=item.slug,
                name=item.name,
                description=item.description,
                content=content,
            )
        )

    if not docs:
        return ""

    q = (query or "").strip()
    if q:
        docs = sorted(docs, key=lambda d: _score_skill(d, q), reverse=True)

    selected = docs[: max(1, top_k)]
    parts = [
        f"<!-- source={doc.source} slug={doc.slug} -->\n{doc.content}" for doc in selected
    ]
    return "\n\n".join(parts)
