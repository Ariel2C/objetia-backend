from enum import Enum
from typing import Optional, List
from datetime import datetime
from sqlmodel import Field, Relationship
from sqlalchemy import Column, Integer, ForeignKey

# 🌟 Importamos la clase Base unificada para que el registro de tablas funcione
from src.config.database import Base

class ProductCondition(str, Enum):
    NEW = "new"
    USED = "used"

class ModerationStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


# ==============================================================================
# MODELO: PRODUCTO
# ==============================================================================
class Product(Base, table=True):  # 🌟 Cambiado a Base
    __tablename__ = "products"

    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    title: str = Field(index=True, nullable=False)
    description: Optional[str] = Field(default=None)
    price: float = Field(nullable=False)
    stock: int = Field(default=1, nullable=False)
    condition: ProductCondition = Field(default=ProductCondition.USED, nullable=False)
    category: str = Field(index=True, nullable=False)
    
    moderation_status: ModerationStatus = Field(default=ModerationStatus.PENDING, index=True)
    ai_moderation_notes: Optional[str] = Field(default=None)
    
    # Datos de Envío (Correo Argentino)
    weight_kg: float = Field(default=10.0, nullable=False)
    height_cm: float = Field(default=50.0, nullable=False)
    width_cm: float = Field(default=50.0, nullable=False)
    length_cm: float = Field(default=50.0, nullable=False)
    
    # Recuerda que para pruebas iniciales puedes comentar esta FK si aún no creas el modelo de usuarios
    seller_id: int = Field(foreign_key="users.id", nullable=False, index=True)
    
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)

    images: List["ProductImage"] = Relationship(
        back_populates="product",
        sa_relationship_kwargs={"cascade": "all, delete-orphan", "passive_deletes": True}
    )
    favorited_by: List["Favorite"] = Relationship(
        back_populates="product",
        sa_relationship_kwargs={"cascade": "all, delete-orphan", "passive_deletes": True}
    )


# ==============================================================================
# MODELO: IMÁGENES DEL PRODUCTO
# ==============================================================================
class ProductImage(Base, table=True):  # 🌟 Cambiado a Base
    __tablename__ = "product_images"

    id: Optional[int] = Field(default=None, primary_key=True)
    cloudfront_url: str = Field(nullable=False)
    is_primary: bool = Field(default=False)
    
    product_id: int = Field(
        sa_column=Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    product: Product = Relationship(back_populates="images")


# ==============================================================================
# MODELO: MOTOR DE FAVORITOS
# ==============================================================================
class Favorite(Base, table=True):  # 🌟 Cambiado a Base
    __tablename__ = "favorites"

    user_id: int = Field(foreign_key="users.id", primary_key=True, index=True)
    
    product_id: int = Field(
        sa_column=Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), primary_key=True, index=True)
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)

    product: Product = Relationship(back_populates="favorited_by")
