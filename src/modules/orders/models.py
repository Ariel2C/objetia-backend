from enum import Enum
from typing import Optional
from datetime import datetime
from sqlmodel import Field
from src.config.database import Base

class OrderStatus(str, Enum):
    PENDING_PAYMENT = "pending_payment"
    PAID = "paid"                      # Pago confirmado, dinero en Escrow (Garantía)
    SHIPPED = "shipped"                # Vendedor imprimió etiqueta y despachó el mueble
    DELIVERED = "delivered"            # Correo entregó el paquete al comprador
    CANCELLED = "cancelled"            # Compra cancelada o reembolso ejecutado

class ShipmentStatus(str, Enum):
    LABEL_GENERATED = "LABEL_GENERATED"    # Vendedor imprimió la etiqueta (en preparación)
    DISPATCHED = "DISPATCHED"              # Despachado en la sucursal de Correo Argentino
    IN_TRANSIT = "IN_TRANSIT"              # En viaje hacia el destino
    OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY"  # El repartidor salió a hacer la entrega
    DELIVERED = "DELIVERED"                # Entregado al comprador
    ARRIVED = "ARRIVED"                    # (Legado) equivalente a DELIVERED


# ==============================================================================
# TABLA: ÓRDENES DE COMPRA (Transaccional)
# ==============================================================================
class Order(Base, table=True):
    __tablename__ = "orders"

    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    buyer_id: int = Field(foreign_key="users.id", nullable=False, index=True)
    product_id: int = Field(foreign_key="products.id", nullable=False, index=True)
    
    # Valores Monetarios
    total_price: float = Field(nullable=False)         # Monto total cobrado al comprador
    shipping_cost: float = Field(default=0.0, nullable=False)
    
    status: OrderStatus = Field(default=OrderStatus.PENDING_PAYMENT, index=True)
    
    # Datos de Envío del Pedido
    recipient_name: Optional[str] = Field(default=None, nullable=True)
    street: Optional[str] = Field(default=None, nullable=True)
    number: Optional[str] = Field(default=None, nullable=True)
    floor_dept: Optional[str] = Field(default=None, nullable=True)
    postal_code: Optional[str] = Field(default=None, nullable=True)
    city: Optional[str] = Field(default=None, nullable=True)
    province: Optional[str] = Field(default=None, nullable=True)
    
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)


# ==============================================================================
# TABLA: EVENTOS DE ENVÍO (Timeline del tracking, alimentado por el correo)
# ==============================================================================
class ShipmentEvent(Base, table=True):
    __tablename__ = "shipment_events"

    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    shipment_id: int = Field(foreign_key="shipments.id", nullable=False, index=True)
    status: str = Field(nullable=False)  # Valor de ShipmentStatus al momento del evento
    description: Optional[str] = Field(default=None, nullable=True)
    location: Optional[str] = Field(default=None, nullable=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)


# ==============================================================================
# TABLA: LOGÍSTICA Y ENVÍOS (Seguimiento Seguro)
# ==============================================================================
class Shipment(Base, table=True):
    __tablename__ = "shipments"

    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    order_id: int = Field(foreign_key="orders.id", unique=True, nullable=False, index=True)
    
    tracking_number: str = Field(nullable=False, index=True)
    shipping_label_url: str = Field(nullable=False)
    status: ShipmentStatus = Field(default=ShipmentStatus.LABEL_GENERATED, nullable=False, index=True)
    
    # Detalles de Entrega Destinatario (Correo Argentino)
    recipient_name: Optional[str] = Field(default=None, nullable=True)
    street: Optional[str] = Field(default=None, nullable=True)
    number: Optional[str] = Field(default=None, nullable=True)
    floor_dept: Optional[str] = Field(default=None, nullable=True)
    postal_code: Optional[str] = Field(default=None, nullable=True, index=True)
    city: Optional[str] = Field(default=None, nullable=True)
    province: Optional[str] = Field(default=None, nullable=True)
    
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)