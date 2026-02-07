from __future__ import annotations

from pydantic import BaseModel


class CalendarLedgerItem(BaseModel):
    id: int
    amount: float
    currency: str
    category: str
    item: str
    transaction_date: str


class CalendarScheduleItem(BaseModel):
    id: int
    content: str
    trigger_time: str
    status: str


class CalendarDayResponse(BaseModel):
    date: str
    ledger_total: float
    ledger_count: int
    schedule_count: int
    ledgers: list[CalendarLedgerItem]
    schedules: list[CalendarScheduleItem]


class CalendarResponse(BaseModel):
    start_date: str
    end_date: str
    days: list[CalendarDayResponse]
