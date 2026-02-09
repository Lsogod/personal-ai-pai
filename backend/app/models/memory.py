from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, Column, DateTime, UniqueConstraint
from sqlmodel import Field, SQLModel


class LongTermMemory(SQLModel, table=True):
    __tablename__ = "long_term_memories"
    __table_args__ = (
        UniqueConstraint("user_id", "memory_key", name="uq_long_term_memory_user_key"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    conversation_id: Optional[int] = Field(default=None, foreign_key="conversations.id", index=True)
    source_message_id: Optional[int] = Field(default=None, foreign_key="messages.id", index=True)

    memory_key: str = Field(index=True, max_length=160)
    memory_type: str = Field(default="fact", index=True, max_length=40)
    content: str = Field(default="", max_length=1000)
    importance: int = Field(default=3, index=True)
    confidence: float = Field(default=0.8)

    is_active: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, default=True),
    )
    last_accessed_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    expires_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False, onupdate=datetime.utcnow),
    )
