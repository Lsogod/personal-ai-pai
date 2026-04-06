from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, UniqueConstraint
from sqlmodel import Field, SQLModel


class UserMcpServer(SQLModel, table=True):
    __tablename__ = "user_mcp_servers"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_user_mcp_server_name"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    name: str = Field()
    transport: str = Field(default="http")
    url: str = Field(default="")
    api_key: str = Field(default="")
    headers_json: str = Field(default="[]")
    env_json: str = Field(default="[]")
    is_enabled: bool = Field(default=True)

    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False, onupdate=datetime.utcnow),
    )
