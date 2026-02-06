from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel


class Ledger(SQLModel, table=True):
    __tablename__ = "ledgers"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)

    amount: float = Field(description="金额")
    currency: str = Field(default="CNY")

    category: str = Field(description="分类: 餐饮/交通/购物/居家...")
    item: str = Field(description="摘要: 麦当劳午餐")

    image_url: Optional[str] = Field(default=None, description="原始小票图片URL")

    transaction_date: datetime = Field(description="实际交易时间")
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
