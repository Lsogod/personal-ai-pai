from typing import List

from pydantic import BaseModel, Field


class UnifiedMessage(BaseModel):
    platform: str
    user_uuid: str
    content: str
    image_urls: List[str] = Field(default_factory=list)
    raw_data: dict
    message_id: str | None = None
    event_ts: int | None = None
