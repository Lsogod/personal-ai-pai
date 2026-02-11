from __future__ import annotations

from pydantic import BaseModel, Field


class FeedbackCreateRequest(BaseModel):
    content: str = Field(min_length=4, max_length=2000)
    app_version: str | None = Field(default=None, max_length=64)
    env_version: str | None = Field(default=None, max_length=64)
    client_page: str | None = Field(default=None, max_length=128)


class FeedbackCreateResponse(BaseModel):
    ok: bool
    id: int
    created_at: str
