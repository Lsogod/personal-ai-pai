from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, Text, UniqueConstraint
from sqlmodel import Field, SQLModel


class ReminderDelivery(SQLModel, table=True):
    __tablename__ = "reminder_deliveries"
    __table_args__ = (
        UniqueConstraint("schedule_id", "platform", "platform_id", name="uq_reminder_delivery_target"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    schedule_id: int = Field(foreign_key="schedules.id", index=True)
    user_id: int = Field(foreign_key="users.id", index=True)

    platform: str = Field(index=True)
    platform_id: str = Field(index=True)

    status: str = Field(default="PENDING", index=True)
    attempt_count: int = Field(default=0)
    last_error: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    delivered_at: Optional[datetime] = Field(
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
