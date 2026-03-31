from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, Column, DateTime, Integer, String, UniqueConstraint
from sqlmodel import Field, SQLModel

MEMORY_VECTOR_STATUS_DIRTY = "DIRTY"
MEMORY_VECTOR_STATUS_SYNCED = "SYNCED"
MEMORY_VECTOR_STATUS_FAILED = "FAILED"


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
    vector_status: str = Field(
        default=MEMORY_VECTOR_STATUS_DIRTY,
        sa_column=Column(String(20), nullable=False, default=MEMORY_VECTOR_STATUS_DIRTY, index=True),
    )
    vector_synced_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    vector_error: Optional[str] = Field(
        default=None,
        sa_column=Column(String(500), nullable=True),
    )
    vector_model: Optional[str] = Field(
        default=None,
        sa_column=Column(String(160), nullable=True),
    )
    vector_version: int = Field(
        default=1,
        sa_column=Column(Integer, nullable=False, default=1),
    )
    vector_text_hash: Optional[str] = Field(
        default=None,
        sa_column=Column(String(64), nullable=True),
    )

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
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            onupdate=lambda: datetime.now(timezone.utc),
        ),
    )
