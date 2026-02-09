from typing import List, Optional
from pydantic import BaseModel, Field


class ChatSendRequest(BaseModel):
    content: str
    image_urls: List[str] = Field(default_factory=list)
    source_platform: Optional[str] = None


class ChatMessage(BaseModel):
    role: str
    content: str
    created_at: str


class ChatSendResponse(BaseModel):
    responses: List[str]


class ProfileResponse(BaseModel):
    uuid: str
    nickname: str
    ai_name: str
    ai_emoji: str
    platform: str
    email: Optional[str] = None
    setup_stage: int
