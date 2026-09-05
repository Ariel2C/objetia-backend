from typing import Optional
from datetime import datetime
from sqlmodel import Field
from sqlalchemy import Column, Integer, ForeignKey, String, DateTime, Index
from src.config.database import Base

class ProductAnalyticsEvent(Base, table=True):
    __tablename__ = "product_analytics_events"

    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    product_id: int = Field(
        sa_column=Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    user_id: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    )
    event_type: str = Field(
        sa_column=Column(String(30), nullable=False, index=True) # view, favorite_add, favorite_remove, cart_add, purchase
    )
    visitor_hash: Optional[str] = Field(
        default=None,
        sa_column=Column(String(64), nullable=True, index=True)
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    )

class SearchAnalyticsEvent(Base, table=True):
    __tablename__ = "search_analytics_events"

    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    query: str = Field(
        sa_column=Column(String(255), nullable=False, index=True)
    )
    user_id: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    )
    results_count: int = Field(default=0)
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    )
