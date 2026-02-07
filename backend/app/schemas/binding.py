from datetime import datetime

from pydantic import BaseModel, Field


class BindCodeCreateRequest(BaseModel):
    ttl_minutes: int = Field(default=10, ge=1, le=60)


class BindCodeCreateResponse(BaseModel):
    code: str
    expires_at: datetime
    ttl_minutes: int


class BindCodeConsumeRequest(BaseModel):
    code: str = Field(min_length=6, max_length=6)


class BindCodeConsumeResponse(BaseModel):
    ok: bool
    message: str
    canonical_user_id: int | None = None
    access_token: str | None = None
