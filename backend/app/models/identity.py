from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, UniqueConstraint
from sqlmodel import Field, SQLModel


class UserIdentity(SQLModel, table=True):
    __tablename__ = "user_identities"
    __table_args__ = (
        UniqueConstraint("platform", "platform_id", name="uq_identity_platform"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    platform: str = Field(index=True)
    platform_id: str = Field(index=True)

    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
