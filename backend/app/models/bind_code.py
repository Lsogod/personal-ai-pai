from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, UniqueConstraint
from sqlmodel import Field, SQLModel


class BindCode(SQLModel, table=True):
    __tablename__ = "bind_codes"
    __table_args__ = (
        UniqueConstraint("code", name="uq_bind_code"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(index=True)
    owner_user_id: int = Field(foreign_key="users.id", index=True)
    used_by_user_id: Optional[int] = Field(default=None, foreign_key="users.id")
    expires_at: datetime = Field(index=True)
    used_at: Optional[datetime] = Field(default=None)

    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
