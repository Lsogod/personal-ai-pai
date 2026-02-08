from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel
from sqlalchemy import UniqueConstraint


class UserToolPolicy(SQLModel, table=True):
    __tablename__ = "user_tool_policies"
    __table_args__ = (
        UniqueConstraint("user_id", "source", "tool_name", name="uq_user_tool_policy"),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    source: str = Field(index=True, max_length=32)  # builtin / mcp
    tool_name: str = Field(index=True, max_length=128)
    enabled: bool = Field(default=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class UserSkillPolicy(SQLModel, table=True):
    __tablename__ = "user_skill_policies"
    __table_args__ = (
        UniqueConstraint("user_id", "source", "skill_slug", name="uq_user_skill_policy"),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    source: str = Field(index=True, max_length=32)  # builtin / user
    skill_slug: str = Field(index=True, max_length=128)
    enabled: bool = Field(default=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

