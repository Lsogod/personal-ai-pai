from __future__ import annotations

import json
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.skill import Skill, SkillStatus, SkillVersion
from app.services.llm import get_llm


SKILLS_DIR = Path(__file__).resolve().parents[2] / "skills"
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
_WORD_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9_-]+")


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


def _extract_json_object(text: str) -> dict:
    payload = (text or "").strip()
    if payload.startswith("```"):
        lines = payload.splitlines()
        if len(lines) >= 3:
            payload = "\n".join(lines[1:-1]).strip()
    try:
        obj = json.loads(payload)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


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
        }
        for skill in skills
    ]


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


async def render_skill_from_request(
    user_request: str,
    preferred_name: str | None = None,
    existing_content: str | None = None,
) -> str:
    llm = get_llm()
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


async def parse_skill_intent(message_content: str) -> dict:
    text = (message_content or "").strip()
    if text.startswith("/skill"):
        parts = text.split(maxsplit=2)
        action = parts[1].lower() if len(parts) > 1 else "help"
        remainder = parts[2].strip() if len(parts) > 2 else ""
        if action in {"update"}:
            update_parts = remainder.split(maxsplit=1)
            return {
                "action": action,
                "target": update_parts[0] if update_parts else "",
                "request": update_parts[1] if len(update_parts) > 1 else "",
            }
        return {"action": action, "target": remainder, "request": remainder}

    llm = get_llm()
    system = SystemMessage(
        content=(
            "你是意图解析器。将用户关于技能管理的消息解析为 JSON。"
            "action 仅可为: create, update, publish, disable, list, show, help。"
            "返回字段: action, skill_name, skill_slug, request。"
            "只输出 JSON。"
        )
    )
    human = HumanMessage(content=text)
    try:
        response = await llm.ainvoke([system, human])
        data = _extract_json_object(str(response.content))
        if data.get("action"):
            return data
    except Exception:
        pass

    return {"action": "help", "request": text}


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
