from __future__ import annotations

from pydantic import BaseModel, Field


class ConversationCreateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=60)


class ConversationResponse(BaseModel):
    id: int
    title: str
    summary: str
    last_message_at: str
    active: bool

