from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, Column, DateTime, Text
from sqlmodel import Field, SQLModel


class LLMUsageLog(SQLModel, table=True):
    __tablename__ = "llm_usage_logs"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: Optional[int] = Field(default=None, foreign_key="users.id", index=True)
    platform: str = Field(default="", index=True)
    conversation_id: Optional[int] = Field(default=None, foreign_key="conversations.id", index=True)
    node: str = Field(default="unknown", index=True)
    model: str = Field(default="", index=True)
    prompt_tokens: int = Field(default=0)
    completion_tokens: int = Field(default=0)
    total_tokens: int = Field(default=0, index=True)
    latency_ms: int = Field(default=0)
    success: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, default=True),
    )
    error: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
