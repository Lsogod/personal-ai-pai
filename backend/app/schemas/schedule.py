from __future__ import annotations

from pydantic import BaseModel, Field


class ScheduleItemResponse(BaseModel):
    id: int
    content: str
    trigger_time: str
    status: str
    created_at: str


class ScheduleCreateRequest(BaseModel):
    content: str = Field(min_length=1)
    trigger_time: str  # ISO datetime


class ScheduleUpdateRequest(BaseModel):
    content: str | None = None
    trigger_time: str | None = None
    status: str | None = None  # PENDING / CANCELLED


class ScheduleDeleteResponse(BaseModel):
    ok: bool
    id: int
