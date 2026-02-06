from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel


class Schedule(SQLModel, table=True):
    __tablename__ = "schedules"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)

    job_id: str = Field(index=True, description="APScheduler 任务ID")
    content: str = Field(description="提醒内容")
    trigger_time: datetime = Field(description="触发时间")

    status: str = Field(default="PENDING")
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
