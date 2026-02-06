from __future__ import annotations

from pydantic import BaseModel, Field


class LedgerItemResponse(BaseModel):
    id: int
    amount: float
    currency: str
    category: str
    item: str
    transaction_date: str
    created_at: str


class LedgerUpdateRequest(BaseModel):
    amount: float | None = Field(default=None, ge=0)
    category: str | None = None
    item: str | None = None


class LedgerDeleteResponse(BaseModel):
    ok: bool
    id: int
