from __future__ import annotations

from pydantic import BaseModel, Field


class SkillDraftRequest(BaseModel):
    request: str = Field(min_length=1, max_length=4000)
    skill_name: str | None = None
    skill_slug: str | None = None


class SkillItemResponse(BaseModel):
    slug: str
    name: str
    description: str
    status: str
    active_version: int
    source: str = "user"
    read_only: bool = False


class SkillDraftResponse(BaseModel):
    slug: str
    version: int
    status: str
    content_md: str


class SkillDetailResponse(BaseModel):
    slug: str
    name: str
    description: str
    status: str
    active_version: int
    source: str = "user"
    read_only: bool = False
    content_md: str | None = None
