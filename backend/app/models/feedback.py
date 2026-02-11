from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, Text
from sqlmodel import Field, SQLModel


class UserFeedback(SQLModel, table=True):
    __tablename__ = "user_feedbacks"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    platform: str = Field(default="", index=True)
    content: str = Field(default="", sa_column=Column(Text, nullable=False))
    app_version: str = Field(default="")
    env_version: str = Field(default="")
    client_page: str = Field(default="")
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
