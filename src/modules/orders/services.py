import os
from typing import Optional
from urllib.parse import urlparse
from sqlmodel import select
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from redis.asyncio import Redis

from src.modules.orders.models import Order, OrderStatus, Shipment, ShipmentStatus
from src.modules.products.models import Product
from src.modules.wallet.services import WalletService


def _get_mp_sdk():
    """Devuelve un SDK de Mercado Pago o lanza error si el token no está configurado.
    No se usa ningún token hardcodeado como fallback."""
    from mercadopago import SDK
    token = os.getenv("MERCADOPAGO_ACCESS_TOKEN")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="La pasarela de pago no está configurada en el servidor."
        )
    return SDK(token)


class OrderService:

    @classmethod
    async def calcular_costo_envio(cls, product: Product, postal_code: str) -> float:
        """Calcula el costo de envío en el servidor (nunca se confía en el valor del cliente)."""
        from src.modules.shipping.correo_argentino import CorreoArgentinoClient
        client = CorreoArgentinoClient()
        return client.cotizar_envio(
            cp_origen="X5000",
            cp_destino=postal_code,
            peso_kg=product.weight_kg,
            largo_cm=product.length_cm,
            ancho_cm=product.width_cm,
            alto_cm=product.height_cm
        )

    @classmethod
    async def crear_orden_pendiente(
        cls, 
        db: AsyncSession, 
        buyer_id: int, 
        product_id: int, 
        recipient_name: str,
        street: str,
        number: str,
        floor_dept: Optional[str],
        postal_code: str,
        city: str,
        province: str,
        redis: Optional[Redis] = None
    ) -> Order:
        """
        Crea una orden en estado PENDING_PAYMENT. El costo de envío se calcula
        en el servidor y se valida la reserva del carrito.
        """
        # 1. Buscar el producto en PostgreSQL local
        query = select(Product).where(Product.id == product_id)
        result = await db.execute(query)
        product = result.scalar_one_or_none()

        if not product or product.stock < 1:
            raise HTTPException(status_code=400, detail="El artículo ya no está disponible.")

        # 2. El comprador no puede comprar su propia publicación
        if product.seller_id == buyer_id:
            raise HTTPException(status_code=400, detail="No podés comprar tu propia publicación.")

        # 3. Validar la reserva del carrito: si el artículo está reservado, debe ser por este comprador
        if redis is not None:
            lock_owner = await redis.get(f"product_lock:{product_id}")
            if lock_owner is not None and int(lock_owner) != buyer_id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Este artículo está reservado por otro comprador."
                )

        # 4. Calcular el costo de envío en el servidor
        shipping_cost = await cls.calcular_costo_envio(product, postal_code)

        # 5. Registrar la Orden con estado PENDING_PAYMENT
        precio_total = product.price + shipping_cost
        nueva_orden = Order(
            buyer_id=buyer_id,
            product_id=product_id,
            total_price=precio_total,
            shipping_cost=shipping_cost,
            status=OrderStatus.PENDING_PAYMENT,
            recipient_name=recipient_name,
            street=street,
            number=number,
            floor_dept=floor_dept,
            postal_code=postal_code,
            city=city,
            province=province
        )
        db.add(nueva_orden)
        await db.flush()  # Sincroniza para obtener el ID de la orden
        return nueva_orden

    @classmethod
    async def expirar_ordenes_pendientes(cls, db: AsyncSession, minutos: int = 30) -> int:
        """
        Cancela las órdenes que quedaron en PENDING_PAYMENT por checkouts abandonados
        (el usuario cerró Mercado Pago sin pagar). Se invoca de forma perezosa desde
        los listados de compras/ventas. Si el pago llega tarde igual se recupera:
        confirmar_pago_orden verifica contra MP y marca la orden como pagada.
        """
        from datetime import datetime, timedelta
        limite = datetime.utcnow() - timedelta(minutes=minutos)
        query = select(Order).where(
            Order.status == OrderStatus.PENDING_PAYMENT,
            Order.created_at < limite
        )
        result = await db.execute(query)
        ordenes = result.scalars().all()
        for orden in ordenes:
            orden.status = OrderStatus.CANCELLED
            db.add(orden)
        return len(ordenes)

    @classmethod
    async def verificar_pago_aprobado_en_mp(cls, order_id: int) -> bool:
        """
        Consulta a Mercado Pago si existe un pago APROBADO para esta orden,
        buscando por external_reference. Es la verificación server-side que impide
        que un usuario marque como pagada una orden que no abonó.
        """
        sdk = _get_mp_sdk()
        filtros = {"external_reference": f"order_{order_id}"}
        try:
            resultado = sdk.payment().search(filtros)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"No se pudo verificar el pago con Mercado Pago: {str(e)}")

        respuesta = resultado.get("response", {}) if isinstance(resultado, dict) else {}
        pagos = respuesta.get("results", []) or []
        for pago in pagos:
            if pago.get("status") == "approved":
                return True
        return False

    @classmethod
    async def confirmar_pago_orden(
        cls,
        db: AsyncSession,
        redis: Redis,
        order_id: int,
        buyer_id: Optional[int] = None,
        verificar_en_mp: bool = True
    ) -> Order:
        """
        Confirma el pago de la orden: descuenta stock, genera etiqueta de envío,
        acredita comisiones en la billetera del vendedor y limpia el carrito.

        - Si se pasa buyer_id, valida que la orden pertenezca a ese comprador.
        - Si verificar_en_mp es True, consulta el estado real del pago en Mercado Pago
          antes de dar la orden por pagada (evita fraude por confirmación falsa).
        """
        # 1. Buscar la orden
        query = select(Order).where(Order.id == order_id)
        result = await db.execute(query)
        order = result.scalar_one_or_none()

        if not order:
            raise HTTPException(status_code=404, detail="Orden no encontrada.")

        # Validar propiedad de la orden
        if buyer_id is not None and order.buyer_id != buyer_id:
            raise HTTPException(status_code=403, detail="Esta orden no te pertenece.")

        # Si ya está paga, la retornamos directamente (idempotencia)
        if order.status == OrderStatus.PAID:
            return order

        # 2. Verificar contra Mercado Pago que el pago realmente exista y esté aprobado
        if verificar_en_mp:
            aprobado = await cls.verificar_pago_aprobado_en_mp(order_id)
            if not aprobado:
                raise HTTPException(
                    status_code=status.HTTP_402_PAYMENT_REQUIRED,
                    detail="El pago aún no fue aprobado por Mercado Pago."
                )

        # 3. Buscar producto con bloqueo de fila para evitar doble venta (condición de carrera)
        query_prod = select(Product).where(Product.id == order.product_id).with_for_update()
        result_prod = await db.execute(query_prod)
        product = result_prod.scalar_one_or_none()

        if not product:
            raise HTTPException(status_code=404, detail="El producto asociado a la orden no existe.")

        # Revalidar stock ahora que tenemos el lock: si otro pago lo agotó, cancelamos
        if product.stock < 1:
            order.status = OrderStatus.CANCELLED
            db.add(order)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="El artículo se agotó antes de confirmar el pago. La orden fue cancelada y se reembolsará."
            )

        # Descontar la unidad vendida (respeta el multi-stock)
        product.stock = product.stock - 1
        db.add(product)

        # 4. Actualizar estado de la orden a PAID
        order.status = OrderStatus.PAID
        db.add(order)

        # 5. Integración Logística (Correo Argentino Mock/Real)
        from src.modules.shipping.correo_argentino import CorreoArgentinoClient
        correo_client = CorreoArgentinoClient()
        
        envio_data = correo_client.crear_envio(
            order_id=order.id,
            recipient_name=order.recipient_name or "Comprador Vamaar",
            street=order.street or "Sin calle",
            number=order.number or "0",
            floor_dept=order.floor_dept,
            postal_code=order.postal_code or "X5000",
            city=order.city or "Córdoba",
            province=order.province or "Córdoba",
            weight_kg=product.weight_kg,
            largo_cm=product.length_cm,
            ancho_cm=product.width_cm,
            alto_cm=product.height_cm
        )

        nuevo_envio = Shipment(
            order_id=order.id,
            tracking_number=envio_data["tracking_number"],
            shipping_label_url=envio_data["shipping_label_url"],
            status=ShipmentStatus.LABEL_GENERATED,
            recipient_name=order.recipient_name,
            street=order.street,
            number=order.number,
            floor_dept=order.floor_dept,
            postal_code=order.postal_code,
            city=order.city,
            province=order.province
        )
        db.add(nuevo_envio)
        await db.flush()  # Obtener el ID del envío para el evento inicial del timeline

        from src.modules.orders.models import ShipmentEvent
        db.add(ShipmentEvent(
            shipment_id=nuevo_envio.id,
            status=ShipmentStatus.LABEL_GENERATED.value,
            description="El vendedor generó la etiqueta de envío",
            location="Vamaar",
        ))

        # 6. Acreditar saldo congelado al vendedor (comisión 10% sobre el precio del producto)
        await WalletService.procesar_ingreso_venta(
            db=db, 
            seller_id=product.seller_id, 
            total_pago_comprador=product.price
        )

        # 7. Limpieza de caché: eliminar el carrito y el candado temporal
        cart_key = f"cart:{order.buyer_id}"
        lock_key = f"product_lock:{order.product_id}"
        await redis.hdel(cart_key, str(order.product_id))
        await redis.delete(lock_key)

        # 8. Registrar evento de compra en analítica y recalcular relevancia
        try:
            from src.modules.analytics.services import AnalyticsService
            await AnalyticsService.record_product_event(
                db=db,
                product_id=order.product_id,
                event_type="purchase",
                user_id=order.buyer_id
            )
        except Exception as e_analytics:
            print(f"⚠️ Error al registrar analítica de compra: {e_analytics}")

        return order

    @classmethod
    def crear_preferencia_mercadopago(
        cls, order: Order, product_title: str, product_price: float
    ) -> dict:
        """
        Crea una preferencia de pago en Mercado Pago y retorna el objeto de respuesta.
        Las URLs de retorno y de webhook se leen de variables de entorno.
        """
        sdk = _get_mp_sdk()
        frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000").rstrip("/")
        public_api_url = os.getenv("PUBLIC_API_URL", "http://localhost:8000").rstrip("/")

        def _es_url_local(url: str) -> bool:
            host = urlparse(url).hostname or ""
            return (
                host in ("localhost", "127.0.0.1", "0.0.0.0")
                or host.startswith("192.168.")
                or host.startswith("10.")
            )

        preference_data = {
            "items": [
                {
                    "title": product_title,
                    "quantity": 1,
                    "unit_price": float(product_price + order.shipping_cost),
                    "currency_id": "ARS"
                }
            ],
            "back_urls": {
                "success": f"{frontend_url}/cart?status=success&order_id={order.id}",
                "failure": f"{frontend_url}/cart?status=failure&order_id={order.id}",
                "pending": f"{frontend_url}/cart?status=pending&order_id={order.id}"
            },
            "external_reference": f"order_{order.id}"
        }

        # Mercado Pago rechaza auto_return si back_urls apunta a localhost (exige URL pública).
        # En desarrollo local se omite: el usuario vuelve con el botón "Volver al sitio" y
        # el pago se confirma igual vía polling de /orders/confirm-payment/ en el frontend.
        if not _es_url_local(frontend_url):
            preference_data["auto_return"] = "approved"

        # El webhook solo sirve si la API es alcanzable públicamente por Mercado Pago
        if not _es_url_local(public_api_url):
            preference_data["notification_url"] = f"{public_api_url}/orders/webhook/"

        
        res = sdk.preference().create(preference_data)
        status_code = res.get("status")
        if status_code in [200, 201] and "response" in res:
            return res["response"]
        else:
            raise Exception(f"Mercado Pago error (status {status_code}): {res.get('response') or res}")
