"""
Panel simulador de Correo Argentino (solo modo mock).

Permite avanzar manualmente el estado de los envíos para probar el flujo
logístico completo sin la API real: etiqueta -> despachado -> en viaje ->
en reparto -> entregado. Cada cambio genera un evento del timeline que ve
el comprador en la página de seguimiento.
"""
import os
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from src.config.database import get_db
from src.common.dependencies import get_current_user
from src.modules.users.models import User
from src.modules.orders.models import (
    Order, OrderStatus, Shipment, ShipmentStatus, ShipmentEvent
)
from src.modules.products.models import Product

router = APIRouter(prefix="/shipping", tags=["Logística Correo Argentino"])

# Orden cronológico de los estados del envío
SECUENCIA_ESTADOS = [
    ShipmentStatus.LABEL_GENERATED,
    ShipmentStatus.DISPATCHED,
    ShipmentStatus.IN_TRANSIT,
    ShipmentStatus.OUT_FOR_DELIVERY,
    ShipmentStatus.DELIVERED,
]

DETALLE_EVENTO = {
    ShipmentStatus.LABEL_GENERATED: {
        "label": "En preparación",
        "description": "El vendedor generó la etiqueta de envío",
        "location": "Vamaar",
    },
    ShipmentStatus.DISPATCHED: {
        "label": "Despachado",
        "description": "El vendedor entregó el paquete en la sucursal de Correo Argentino",
        "location": "Sucursal Correo Argentino (origen)",
    },
    ShipmentStatus.IN_TRANSIT: {
        "label": "En viaje",
        "description": "Tu paquete está viajando hacia el centro de distribución de destino",
        "location": "Centro de distribución Correo Argentino",
    },
    ShipmentStatus.OUT_FOR_DELIVERY: {
        "label": "En reparto",
        "description": "El repartidor salió a entregar tu paquete",
        "location": "Zona de entrega",
    },
    ShipmentStatus.DELIVERED: {
        "label": "Entregado",
        "description": "Tu paquete fue entregado",
        "location": "Domicilio del comprador",
    },
}


def _indice_estado(estado: ShipmentStatus) -> int:
    if estado == ShipmentStatus.ARRIVED:  # Alias legado de DELIVERED
        return SECUENCIA_ESTADOS.index(ShipmentStatus.DELIVERED)
    return SECUENCIA_ESTADOS.index(estado)


def _verificar_modo_mock():
    if os.getenv("CORREO_ARGENTINO_MOCK", "true").lower() != "true":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="El simulador solo está disponible con CORREO_ARGENTINO_MOCK=true."
        )


@router.get("/simulator/shipments", status_code=status.HTTP_200_OK)
async def listar_envios_simulador(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Lista todos los envíos con su estado actual, para el panel simulador."""
    _verificar_modo_mock()

    query = (
        select(Shipment, Order, Product, User.full_name)
        .join(Order, Shipment.order_id == Order.id)
        .join(Product, Order.product_id == Product.id)
        .join(User, Order.buyer_id == User.id)
        .order_by(Shipment.created_at.desc())
    )
    result = await db.execute(query)
    rows = result.all()

    serialized = []
    for shipment, order, product, buyer_name in rows:
        idx = _indice_estado(shipment.status)
        siguiente = SECUENCIA_ESTADOS[idx + 1] if idx + 1 < len(SECUENCIA_ESTADOS) else None
        serialized.append({
            "shipment_id": shipment.id,
            "order_id": order.id,
            "tracking_number": shipment.tracking_number,
            "product_title": product.title,
            "buyer_name": buyer_name,
            "city": shipment.city,
            "province": shipment.province,
            "status": shipment.status.value,
            "status_label": DETALLE_EVENTO[SECUENCIA_ESTADOS[idx]]["label"],
            "status_index": idx,
            "next_status": siguiente.value if siguiente else None,
            "next_status_label": DETALLE_EVENTO[siguiente]["label"] if siguiente else None,
            "created_at": shipment.created_at.isoformat() + "Z",
        })
    return serialized


@router.post("/simulator/{shipment_id}/status", status_code=status.HTTP_200_OK)
async def cambiar_estado_envio(
    shipment_id: int,
    new_status: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Cambia el estado de un envío (simula el avance en la red de Correo Argentino).
    Registra el evento del timeline y sincroniza el estado de la orden.
    """
    _verificar_modo_mock()

    try:
        estado_nuevo = ShipmentStatus(new_status)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Estado inválido: {new_status}")
    if estado_nuevo == ShipmentStatus.ARRIVED:
        estado_nuevo = ShipmentStatus.DELIVERED

    query = select(Shipment).where(Shipment.id == shipment_id)
    result = await db.execute(query)
    shipment = result.scalar_one_or_none()
    if not shipment:
        raise HTTPException(status_code=404, detail="Envío no encontrado.")

    idx_actual = _indice_estado(shipment.status)
    idx_nuevo = _indice_estado(estado_nuevo)
    if idx_nuevo <= idx_actual:
        raise HTTPException(
            status_code=400,
            detail="El envío ya pasó por ese estado: solo se puede avanzar hacia adelante."
        )

    # Registrar eventos intermedios si se saltearon estados (timeline consistente)
    for idx in range(idx_actual + 1, idx_nuevo + 1):
        estado_paso = SECUENCIA_ESTADOS[idx]
        detalle = DETALLE_EVENTO[estado_paso]
        db.add(ShipmentEvent(
            shipment_id=shipment.id,
            status=estado_paso.value,
            description=detalle["description"],
            location=detalle["location"],
        ))

    shipment.status = estado_nuevo
    db.add(shipment)

    # Sincronizar el estado de la orden para comprador y vendedor
    query_order = select(Order).where(Order.id == shipment.order_id)
    res_order = await db.execute(query_order)
    order = res_order.scalar_one_or_none()
    if order:
        if estado_nuevo == ShipmentStatus.DELIVERED:
            order.status = OrderStatus.DELIVERED
        elif order.status == OrderStatus.PAID:
            order.status = OrderStatus.SHIPPED
        db.add(order)

    await db.commit()

    return {
        "mensaje": f"Envío actualizado a '{DETALLE_EVENTO[estado_nuevo]['label']}'.",
        "shipment_id": shipment.id,
        "status": estado_nuevo.value,
        "status_label": DETALLE_EVENTO[estado_nuevo]["label"],
    }
