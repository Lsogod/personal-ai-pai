from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, Column, DateTime, UniqueConstraint
from sqlmodel import Field, SQLModel


class AdminToolSwitch(SQLModel, table=True):
    __tablename__ = "admin_tool_switches"
    __table_args__ = (
        UniqueConstraint("tool_source", "tool_name", name="uq_admin_tool_switch"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    tool_source: str = Field(index=True, max_length=40)
    tool_name: str = Field(index=True, max_length=120)
    enabled: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, default=True),
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False, onupdate=datetime.utcnow),
    )
