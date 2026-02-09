from __future__ import annotations

from pydantic import BaseModel, Field


class MCPFetchRequest(BaseModel):
    url: str
    max_length: int = Field(default=5000, ge=1, le=20000)
    start_index: int = Field(default=0, ge=0)
    raw: bool = False


class MCPFetchResponse(BaseModel):
    content: str


class MCPToolItem(BaseModel):
    name: str
    description: str

