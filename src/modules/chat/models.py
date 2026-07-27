from typing import Optional
from datetime import datetime
from sqlmodel import Field
from src.config.database import Base

class ChatRoom(Base, table=True):
    __tablename__ = "chat_rooms"

    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    product_id: int = Field(foreign_key="products.id", nullable=False, index=True)
    buyer_id: int = Field(foreign_key="users.id", nullable=False, index=True)
    seller_id: int = Field(foreign_key="users.id", nullable=False, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)


class ChatMessage(Base, table=True):
    __tablename__ = "chat_messages"

    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    room_id: int = Field(foreign_key="chat_rooms.id", nullable=False, index=True)
    sender_id: int = Field(foreign_key="users.id", nullable=False, index=True)
    message: str = Field(nullable=False)
    
    # Flags de moderación para reportes financieros y de administración
    was_moderated: bool = Field(default=False, nullable=False)
    is_read: bool = Field(default=False, nullable=False)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
