from __future__ import annotations

from pydantic import BaseModel, Field


class ConversationCreateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=60)


class ConversationUpdateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=60)


class ConversationResponse(BaseModel):
    id: int
    title: str
    summary: str
    last_message_at: str
    active: bool


class ConversationDeleteResponse(BaseModel):
    ok: bool
    deleted_id: int
    deleted_title: str
    active_conversation: ConversationResponse
