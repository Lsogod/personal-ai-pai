from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, Text, UniqueConstraint
from sqlmodel import Field, SQLModel


class AppSetting(SQLModel, table=True):
    __tablename__ = "app_settings"
    __table_args__ = (UniqueConstraint("key", name="uq_app_setting_key"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    key: str = Field(index=True, max_length=120)
    value: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False, onupdate=datetime.utcnow),
    )
