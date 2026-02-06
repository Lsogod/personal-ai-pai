from __future__ import annotations

from datetime import datetime
from enum import IntEnum
from typing import Optional
from uuid import uuid4

from sqlalchemy import Column, DateTime, UniqueConstraint
from sqlmodel import Field, SQLModel


class SetupStage(IntEnum):
    NEW = 0
    USER_NAMED = 1
    AI_NAMED = 2
    COMPLETED = 3


class User(SQLModel, table=True):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("platform", "platform_id", name="uq_platform_user"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    uuid: str = Field(default_factory=lambda: str(uuid4()), index=True, unique=True)

    platform: str = Field(index=True)
    platform_id: str = Field(index=True)

    email: Optional[str] = Field(default=None, index=True)
    hashed_password: Optional[str] = Field(default=None)
    active_conversation_id: Optional[int] = Field(default=None, index=True)

    nickname: str = Field(default="主人")
    ai_name: str = Field(default="PAI")
    ai_emoji: str = Field(default="🤖")

    setup_stage: int = Field(default=SetupStage.NEW)

    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False, onupdate=datetime.utcnow),
    )
