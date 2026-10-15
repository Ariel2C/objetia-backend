from typing import Optional
from datetime import datetime
from sqlmodel import Field
from src.config.database import Base

class CartItem(Base, table=True):
    __tablename__ = "cart_items"

    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    user_id: int = Field(foreign_key="users.id", nullable=False, index=True)
    product_id: int = Field(foreign_key="products.id", nullable=False, index=True)
    added_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    price_at_add: float = Field(nullable=False)
