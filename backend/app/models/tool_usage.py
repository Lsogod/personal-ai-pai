from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, Column, DateTime, Text
from sqlmodel import Field, SQLModel


class ToolUsageLog(SQLModel, table=True):
    __tablename__ = "tool_usage_logs"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: Optional[int] = Field(default=None, foreign_key="users.id", index=True)
    platform: str = Field(default="", index=True)
    conversation_id: Optional[int] = Field(default=None, foreign_key="conversations.id", index=True)
    tool_source: str = Field(default="builtin", index=True, max_length=40)
    tool_name: str = Field(index=True, max_length=120)
    success: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, default=True),
    )
    latency_ms: int = Field(default=0)
    error: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
