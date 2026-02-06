from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Optional

from sqlalchemy import Column, DateTime, UniqueConstraint
from sqlmodel import Field, SQLModel


class SkillStatus(StrEnum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    DISABLED = "DISABLED"


class Skill(SQLModel, table=True):
    __tablename__ = "skills"
    __table_args__ = (
        UniqueConstraint("user_id", "slug", name="uq_skill_user_slug"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    slug: str = Field(index=True)
    name: str = Field()
    description: str = Field(default="")
    status: str = Field(default=SkillStatus.DRAFT, index=True)
    active_version: int = Field(default=1)

    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False, onupdate=datetime.utcnow),
    )


class SkillVersion(SQLModel, table=True):
    __tablename__ = "skill_versions"
    __table_args__ = (
        UniqueConstraint("skill_id", "version", name="uq_skill_version"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    skill_id: int = Field(foreign_key="skills.id", index=True)
    version: int = Field(index=True)
    content_md: str = Field()

    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
