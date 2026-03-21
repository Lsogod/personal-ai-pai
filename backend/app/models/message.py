from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, Column, DateTime
from sqlmodel import Field, SQLModel


class Message(SQLModel, table=True):
    __tablename__ = "messages"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    conversation_id: Optional[int] = Field(default=None, foreign_key="conversations.id", index=True)
    role: str = Field(description="user/assistant/system")
    content: str = Field(default="")
    image_urls: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    platform: str = Field(default="")
    memory_status: Optional[str] = Field(default=None, index=True)
    memory_processed_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    memory_error: Optional[str] = Field(default=None)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
