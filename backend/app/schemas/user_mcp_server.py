from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class KV(BaseModel):
    key: str = ""
    value: str = ""


class UserMcpServerCreate(BaseModel):
    name: str
    transport: str = "http"
    url: str = ""
    api_key: Optional[str] = None
    headers: list[KV] = []
    env: list[KV] = []
    enabled: bool = True


class UserMcpServerUpdate(BaseModel):
    name: Optional[str] = None
    transport: Optional[str] = None
    url: Optional[str] = None
    api_key: Optional[str] = None
    headers: Optional[list[KV]] = None
    env: Optional[list[KV]] = None
    enabled: Optional[bool] = None


class UserMcpServerResponse(BaseModel):
    id: int
    name: str
    transport: str
    url: str
    headers: list[KV] = []
    env: list[KV] = []
    enabled: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
