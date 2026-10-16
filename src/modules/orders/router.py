import os
from typing import Optional
from fastapi import APIRouter, Depends, status, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from redis.asyncio import Redis
from sqlmodel import select

from src.config.database import get_db
from src.config.redis import get_redis
from src.modules.orders.services import OrderService
from src.modules.orders.models import Order, Shipment, OrderStatus, ShipmentStatus
from src.modules.products.models import Product

# IMPORTAMOS EL MIDDLEWARE
from src.common.dependencies import get_current_user
from src.modules.users.models import User

router = APIRouter(prefix="/orders", tags=["Módulo de Órdenes y Logística"])

@router.get("/shipping-cost/", status_code=status.HTTP_200_OK)
async def obtener_costo_envio(
    product_id: int,
    postal_code: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Calcula el costo del envío para un producto dado un código postal de destino.
    """
    try:
        query = select(Product).where(Product.id == product_id)
        result = await db.execute(query)
        product = result.scalar_one_or_none()
        if not product:
            raise HTTPException(status_code=404, detail="Producto no encontrado")
            
        from src.modules.shipping.correo_argentino import CorreoArgentinoClient
        client = CorreoArgentinoClient()
        
        # Córdoba centro (X5000) asumido como origen
        cost = client.cotizar_envio(
            cp_origen="X5000",
            cp_destino=postal_code,
            peso_kg=product.weight_kg,
            largo_cm=product.length_cm,
            ancho_cm=product.width_cm,
            alto_cm=product.height_cm
        )
        return {"shipping_cost": cost}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al cotizar envío: {str(e)}")

@router.post("/checkout/", status_code=status.HTTP_201_CREATED)
async def checkout_carrito(
    request: Request,
    product_id: int,
    recipient_name: str,
    street: str,
    number: str,
    postal_code: str,
    city: str,
    province: str,
    floor_dept: Optional[str] = None,
    apply_welcome_coupon: bool = False,
    redis: Redis = Depends(get_redis),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Inicia la transacción. Crea una orden PENDING_PAYMENT y genera
    una preferencia de pago en Mercado Pago para redirigir al comprador.
    El costo de envío se calcula en el servidor a partir del código postal.
    """
    try:
        # 1. Crear la orden pendiente (el envío se calcula server-side, valida hold de checkout y aplica cupón)
        orden = await OrderService.crear_orden_pendiente(
            db=db, 
            buyer_id=current_user.id, 
            product_id=product_id, 
            recipient_name=recipient_name,
            street=street,
            number=number,
            floor_dept=floor_dept,
            postal_code=postal_code,
            city=city,
            province=province,
            redis=redis,
            apply_welcome_coupon=apply_welcome_coupon
        )
        
        # 2. Buscar detalles del producto para los metadatos de Mercado Pago
        query = select(Product).where(Product.id == product_id)
        result = await db.execute(query)
        product = result.scalar_one_or_none()
        if not product:
            raise HTTPException(status_code=404, detail="Producto no encontrado")
            
        # Determinar URL de retorno dinámica basada en el origen del frontend que realiza la compra
        frontend_base_url = request.headers.get("origin")
        if not frontend_base_url:
            referer = request.headers.get("referer")
            if referer:
                from urllib.parse import urlparse
                parsed = urlparse(referer)
                frontend_base_url = f"{parsed.scheme}://{parsed.netloc}"

        # 3. Generar la preferencia de Mercado Pago
        preferencia = OrderService.crear_preferencia_mercadopago(
            order=orden, 
            product_title=product.title, 
            product_price=product.price,
            frontend_base_url=frontend_base_url
        )
        
        # Guardar la orden pendiente
        await db.commit()
        
        return {
            "mensaje": "Preferencia de Mercado Pago generada con éxito.",
            "order_id": orden.id,
            "preference_id": preferencia.get("id"),
            "public_key": os.getenv("MERCADOPAGO_PUBLIC_KEY", ""),
            "init_point": preferencia.get("init_point"),
            "sandbox_init_point": preferencia.get("sandbox_init_point")
        }
    except HTTPException as he:
        raise he
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Fallo en Checkout de Mercado Pago: {str(e)}")

@router.post("/checkout-with-balance/", status_code=status.HTTP_201_CREATED)
async def checkout_con_saldo(
    product_id: int,
    recipient_name: str,
    street: str,
    number: str,
    postal_code: str,
    city: str,
    province: str,
    floor_dept: Optional[str] = None,
    apply_welcome_coupon: bool = False,
    redis: Redis = Depends(get_redis),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Compra pagando con el saldo de la billetera Vamaar (sin pasar por Mercado Pago).
    El saldo de ventas se puede gastar en la app aunque todavía no sea retirable.
    Toda la operación es atómica: si algo falla, no se debita nada.
    """
    from src.modules.wallet.services import WalletService
    try:
        # 1. Crear la orden pendiente (valida stock, autocompra, hold y cupón; calcula envío server-side)
        orden = await OrderService.crear_orden_pendiente(
            db=db,
            buyer_id=current_user.id,
            product_id=product_id,
            recipient_name=recipient_name,
            street=street,
            number=number,
            floor_dept=floor_dept,
            postal_code=postal_code,
            city=city,
            province=province,
            redis=redis,
            apply_welcome_coupon=apply_welcome_coupon
        )

        # 2. Debitar la billetera del comprador por el total (producto + envío - descuento)
        await WalletService.procesar_pago_con_saldo(
            db=db, user_id=current_user.id, monto=orden.total_price
        )

        # 3. Confirmar la orden (descuenta stock, genera envío y acredita al vendedor)
        orden_confirmada = await OrderService.confirmar_pago_orden(
            db=db,
            redis=redis,
            order_id=orden.id,
            buyer_id=current_user.id,
            verificar_en_mp=False  # No hay pago externo: se pagó con saldo interno
        )

        await db.commit()

        return {
            "mensaje": "Compra pagada con tu saldo Vamaar con éxito.",
            "order_id": orden_confirmada.id,
            "total_pagado": orden_confirmada.total_price,
            "status": orden_confirmada.status.value
        }
    except HTTPException as he:
        await db.rollback()
        raise he
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Fallo al pagar con saldo: {str(e)}")


@router.post("/cancel-checkout/{product_id}", status_code=status.HTTP_200_OK)
async def cancelar_checkout(
    product_id: int,
    redis: Redis = Depends(get_redis),
    current_user: User = Depends(get_current_user)
):
    """Libera el lock temporal de checkout cuando el comprador cancela el proceso de pago."""
    liberado = await OrderService.liberar_checkout_lock(product_id, current_user.id, redis)
    return {"liberado": liberado, "mensaje": "Lock de checkout liberado exitosamente."}


@router.post("/confirm-payment/", status_code=status.HTTP_200_OK)
async def confirmar_pago(
    order_id: int,
    redis: Redis = Depends(get_redis),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Confirma de forma segura el pago de una orden tras el redirect exitoso de Mercado Pago.
    """
    try:
        # Valida que la orden sea del usuario y verifica el pago real contra Mercado Pago
        orden = await OrderService.confirmar_pago_orden(
            db=db, redis=redis, order_id=order_id,
            buyer_id=current_user.id, verificar_en_mp=True
        )
        await db.commit()
        return {
            "mensaje": "¡Pago verificado y orden procesada con éxito!",
            "order_id": orden.id,
            "status": orden.status
        }
    except HTTPException as he:
        await db.rollback()
        raise he
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Error al confirmar pago: {str(e)}")


@router.get("/status/", status_code=status.HTTP_200_OK)
async def obtener_estado_orden(
    order_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Permite al frontend consultar el estado de una orden propia (polling tras el pago)."""
    query = select(Order).where(Order.id == order_id)
    result = await db.execute(query)
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Orden no encontrada.")
    if order.buyer_id != current_user.id:
        raise HTTPException(status_code=403, detail="Esta orden no te pertenece.")
    return {
        "order_id": order.id,
        "status": order.status.value,
        "total_price": order.total_price,
        "shipping_cost": order.shipping_cost
    }

def _validar_firma_webhook(request: Request, data_id: Optional[str]) -> bool:
    """
    Valida la cabecera x-signature de Mercado Pago según su especificación.
    Si no hay secreto configurado (MERCADOPAGO_WEBHOOK_SECRET), en modo DEBUG se
    permite para no romper las pruebas locales; en producción se rechaza.
    """
    import os
    import hashlib
    import hmac

    secret = os.getenv("MERCADOPAGO_WEBHOOK_SECRET")
    if not secret:
        # Sin secreto configurado: permitir solo en desarrollo
        return os.getenv("DEBUG") == "True"

    signature = request.headers.get("x-signature", "")
    request_id = request.headers.get("x-request-id", "")
    if not signature:
        return False

    # x-signature viene como "ts=<timestamp>,v1=<hash>"
    partes = dict(
        p.strip().split("=", 1) for p in signature.split(",") if "=" in p
    )
    ts = partes.get("ts")
    hash_recibido = partes.get("v1")
    if not ts or not hash_recibido:
        return False

    manifest = f"id:{data_id};request-id:{request_id};ts:{ts};"
    hash_calculado = hmac.new(
        secret.encode(), manifest.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(hash_calculado, hash_recibido)


@router.post("/webhook/", status_code=status.HTTP_200_OK)
async def webhook_mercadopago(
    request: Request,
    redis: Redis = Depends(get_redis),
    db: AsyncSession = Depends(get_db)
):
    """
    Webhook para recibir las notificaciones asincrónicas de Mercado Pago (IPN).
    """
    try:
        # Extraer query params o body
        try:
            body = await request.json()
        except Exception:
            body = {}
            
        payment_id = request.query_params.get("data.id") or body.get("data", {}).get("id")
        topic = request.query_params.get("type") or body.get("type")

        # Validar la firma del webhook (x-signature) para asegurar que viene de Mercado Pago
        if not _validar_firma_webhook(request, payment_id):
            print("⚠️ Webhook con firma inválida rechazado.")
            return {"status": "invalid signature"}

        if topic == "payment" and payment_id:
            import os
            from mercadopago import SDK
            token = os.getenv("MERCADOPAGO_ACCESS_TOKEN")
            if not token:
                print("⚠️ MERCADOPAGO_ACCESS_TOKEN no configurado; se ignora el webhook.")
                return {"status": "not configured"}
            sdk = SDK(token)
            payment_info = sdk.payment().get(payment_id)
            
            if "response" in payment_info:
                payment_data = payment_info["response"]
                status_mp = payment_data.get("status")
                external_ref = payment_data.get("external_reference")
                
                if status_mp == "approved" and external_ref and external_ref.startswith("order_"):
                    order_id = int(external_ref.replace("order_", ""))
                    # El pago ya fue verificado como approved arriba; no re-consultamos MP
                    await OrderService.confirmar_pago_orden(
                        db=db, redis=redis, order_id=order_id, verificar_en_mp=False
                    )
                    await db.commit()
        return {"status": "ok"}
    except Exception as e:
        await db.rollback()
        print(f"⚠️ Error en webhook de Mercado Pago: {str(e)}")
        # Siempre responder 200 a Mercado Pago para evitar retries infinitos
        return {"status": "error"}

# ENDPOINTS DE HISTORIAL PARA EL COMPRADOR Y EL VENDEDOR
from src.modules.orders.models import Order, Shipment
from src.modules.products.models import ProductImage

@router.get("/purchases/")
async def obtener_compras_usuario(
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    current_user: User = Depends(get_current_user)
):
    """
    Retorna la lista de compras del usuario actual.
    """
    try:
        # Cancelar checkouts abandonados antes de listar (no se muestran las canceladas)
        expiradas = await OrderService.expirar_ordenes_pendientes(db, redis=redis)
        if expiradas:
            await db.commit()

        query = select(Order, Product).join(Product, Order.product_id == Product.id).where(
            Order.buyer_id == current_user.id,
            Order.status != OrderStatus.CANCELLED
        ).order_by(Order.created_at.desc())
        result = await db.execute(query)
        rows = result.fetchall()
        
        serialized = []
        for order, product in rows:
            shipment_query = select(Shipment).where(Shipment.order_id == order.id)
            shipment_result = await db.execute(shipment_query)
            shipment = shipment_result.scalar_one_or_none()
            
            img_query = select(ProductImage).where(ProductImage.product_id == product.id)
            img_result = await db.execute(img_query)
            images = img_result.scalars().all()
            img_url = ""
            for img in images:
                if img.is_primary:
                    img_url = img.cloudfront_url
                    break
            if not img_url and images:
                img_url = images[0].cloudfront_url
                
            serialized.append({
                "id": order.id,
                "product_id": product.id,
                "product_title": product.title,
                "product_price": product.price,
                "total_price": order.total_price,
                "shipping_cost": order.shipping_cost,
                "status": order.status.value,
                "image_url": img_url,
                "created_at": order.created_at.isoformat(),
                "tracking_number": shipment.tracking_number if shipment else None,
                "shipment_status": shipment.status.value if shipment else None,
                "shipping_label_url": shipment.shipping_label_url if shipment else None,
                "recipient_name": order.recipient_name,
                "street": order.street,
                "number": order.number,
                "floor_dept": order.floor_dept,
                "postal_code": order.postal_code,
                "city": order.city,
                "province": order.province
            })
        return serialized
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al obtener compras: {str(e)}")

@router.get("/sales/")
async def obtener_ventas_usuario(
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    current_user: User = Depends(get_current_user)
):
    """
    Retorna la lista de ventas del usuario actual.
    """
    try:
        # Cancelar checkouts abandonados antes de listar (no se muestran las canceladas)
        expiradas = await OrderService.expirar_ordenes_pendientes(db, redis=redis)
        if expiradas:
            await db.commit()

        query = select(Order, Product).join(Product, Order.product_id == Product.id).where(
            Product.seller_id == current_user.id,
            Order.status != OrderStatus.CANCELLED
        ).order_by(Order.created_at.desc())
        result = await db.execute(query)
        rows = result.fetchall()
        
        serialized = []
        for order, product in rows:
            shipment_query = select(Shipment).where(Shipment.order_id == order.id)
            shipment_result = await db.execute(shipment_query)
            shipment = shipment_result.scalar_one_or_none()
            
            img_query = select(ProductImage).where(ProductImage.product_id == product.id)
            img_result = await db.execute(img_query)
            images = img_result.scalars().all()
            img_url = ""
            for img in images:
                if img.is_primary:
                    img_url = img.cloudfront_url
                    break
            if not img_url and images:
                img_url = images[0].cloudfront_url
                
            serialized.append({
                "id": order.id,
                "product_id": product.id,
                "product_title": product.title,
                "product_price": product.price,
                "total_price": order.total_price,
                "shipping_cost": order.shipping_cost,
                "status": order.status.value,
                "image_url": img_url,
                "created_at": order.created_at.isoformat(),
                "tracking_number": shipment.tracking_number if shipment else None,
                "shipment_status": shipment.status.value if shipment else None,
                "shipping_label_url": shipment.shipping_label_url if shipment else None,
                "recipient_name": order.recipient_name,
                "street": order.street,
                "number": order.number,
                "floor_dept": order.floor_dept,
                "postal_code": order.postal_code,
                "city": order.city,
                "province": order.province
            })
        return serialized
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al obtener ventas: {str(e)}")

@router.post("/{order_id}/confirm-delivery/", status_code=status.HTTP_200_OK)
async def confirmar_entrega_orden(
    order_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Permite al comprador confirmar que recibió su paquete.
    Marca la orden y el envío como DELIVERED, y da inicio a los 7 días de garantía
    para la posterior liberación de fondos al vendedor.
    """
    query = select(Order).where(Order.id == order_id)
    result = await db.execute(query)
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Orden no encontrada.")
    if order.buyer_id != current_user.id:
        raise HTTPException(status_code=403, detail="Solo el comprador de la orden puede confirmar la recepción.")

    if order.status == OrderStatus.DELIVERED:
        return {"mensaje": "La orden ya se encuentra registrada como entregada."}

    order.status = OrderStatus.DELIVERED
    db.add(order)

    # Actualizar shipment si existe
    query_ship = select(Shipment).where(Shipment.order_id == order.id)
    res_ship = await db.execute(query_ship)
    shipment = res_ship.scalar_one_or_none()
    if shipment:
        shipment.status = ShipmentStatus.DELIVERED
        db.add(shipment)
        
        from src.modules.orders.models import ShipmentEvent
        db.add(ShipmentEvent(
            shipment_id=shipment.id,
            status=ShipmentStatus.DELIVERED.value,
            description="El comprador confirmó la recepción del paquete",
            location="Domicilio del comprador"
        ))

    # Iniciar los 7 días de garantía para el vendedor
    from src.modules.wallet.services import WalletService
    await WalletService.iniciar_periodo_garantia_por_entrega(db, order.id, dias_garantia=7)

    await db.commit()
    return {"mensaje": "¡Entrega confirmada con éxito! Se inició el período de garantía de 7 días para el vendedor."}

# ==============================================================================
# GESTIÓN DE REPORTES Y PANEL DE CONTROL ADMINISTRATIVO
# ==============================================================================
from src.modules.users.models import UserRole
from src.common.dependencies import RoleChecker
from src.modules.orders.models import Order, OrderStatus
from src.modules.products.models import ModerationStatus
from sqlalchemy import func

@router.get("/admin/dashboard/", status_code=status.HTTP_200_OK)
async def admin_dashboard_data(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(RoleChecker([UserRole.ADMIN, UserRole.ROOT]))
):
    """
    Retorna métricas reales de la plataforma y el registro completo de ventas de todos los usuarios.
    """
    try:
        # 1. Total Usuarios
        query_users = select(func.count()).select_from(User)
        res_users = await db.execute(query_users)
        total_users = res_users.scalar() or 0

        # Estados que cuentan como venta concretada (pagada, despachada o entregada)
        ESTADOS_VENTA = [OrderStatus.PAID, OrderStatus.SHIPPED, OrderStatus.DELIVERED]

        # 2. Volumen de Ventas (Total pagado en órdenes confirmadas)
        query_vol = select(func.sum(Order.total_price)).where(Order.status.in_(ESTADOS_VENTA))
        res_vol = await db.execute(query_vol)
        total_sales_volume = res_vol.scalar() or 0.0

        # 3. Volumen de Envíos (Total pagado por logística)
        query_ship = select(func.sum(Order.shipping_cost)).where(Order.status.in_(ESTADOS_VENTA))
        res_ship = await db.execute(query_ship)
        total_shipping_volume = res_ship.scalar() or 0.0

        # 4. Volumen de Productos (Sin envíos)
        total_products_volume = max(0.0, total_sales_volume - total_shipping_volume)
        # Ganancia Plataforma (10% de comisión)
        total_platform_commissions = total_products_volume * 0.10

        # 5. Saldo Congelado y Disponible de Billeteras
        from src.modules.wallet.models import Wallet
        query_wallets = select(func.sum(Wallet.balance_frozen), func.sum(Wallet.balance_available))
        res_wallets = await db.execute(query_wallets)
        wallets_row = res_wallets.first()
        
        total_frozen_funds = 0.0
        total_available_funds = 0.0
        if wallets_row:
            total_frozen_funds = wallets_row[0] or 0.0
            total_available_funds = wallets_row[1] or 0.0

        # 6. Moderaciones Pendientes
        query_mod = select(func.count()).select_from(Product).where(Product.moderation_status == ModerationStatus.PENDING)
        res_mod = await db.execute(query_mod)
        pending_moderations = res_mod.scalar() or 0

        # 7. Registro completo de todas las ventas/órdenes
        from sqlalchemy.orm import aliased
        BuyerAlias = aliased(User)
        SellerAlias = aliased(User)
        
        query_sales = (
            select(Order, BuyerAlias.full_name, Product.title, SellerAlias.full_name)
            .join(BuyerAlias, Order.buyer_id == BuyerAlias.id)
            .join(Product, Order.product_id == Product.id)
            .join(SellerAlias, Product.seller_id == SellerAlias.id)
            .order_by(Order.id.desc())
        )
        res_sales = await db.execute(query_sales)
        sales_rows = res_sales.all()
        
        all_sales = []
        for order, buyer_name, product_title, seller_name in sales_rows:
            all_sales.append({
                "id": order.id,
                "buyer_name": buyer_name,
                "seller_name": seller_name,
                "product_title": product_title,
                "total_price": order.total_price,
                "shipping_cost": order.shipping_cost,
                "status": order.status.value,
                "created_at": order.created_at.isoformat()
            })

        return {
            "total_users": total_users,
            "total_sales_volume": total_sales_volume,
            "total_shipping_volume": total_shipping_volume,
            "total_platform_commissions": total_platform_commissions,
            "total_frozen_funds": total_frozen_funds,
            "total_available_funds": total_available_funds,
            "pending_moderations": pending_moderations,
            "all_sales": all_sales
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al obtener métricas del panel: {str(e)}")

@router.get("/admin/stats/", status_code=status.HTTP_200_OK)
async def admin_stats(
    year: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(RoleChecker([UserRole.ADMIN, UserRole.ROOT]))
):
    """
    Estadísticas de rendimiento para el panel de administración.
    Serie mensual del año elegido + totales del año + totales históricos.
    El "volumen de productos" excluye el costo de envío (es la base de la comisión).
    """
    from datetime import datetime

    ESTADOS_VENTA = [OrderStatus.PAID, OrderStatus.SHIPPED, OrderStatus.DELIVERED]
    COMISION = 0.10

    def _resumen(total: float, envios: float, ordenes: int) -> dict:
        productos = max(0.0, total - envios)
        return {
            "orders_count": ordenes,
            "sales_total": round(total, 2),           # Con envío incluido
            "products_total": round(productos, 2),    # Solo productos (sin envíos)
            "shipping_total": round(envios, 2),
            "commissions": round(productos * COMISION, 2),
            "avg_ticket": round(productos / ordenes, 2) if ordenes else 0.0,
        }

    try:
        # Años con ventas registradas (para el selector del panel)
        anio_col = func.extract('year', Order.created_at)
        q_years = select(anio_col).where(Order.status.in_(ESTADOS_VENTA)).distinct()
        res_years = await db.execute(q_years)
        years = sorted({int(r[0]) for r in res_years.all() if r[0] is not None})
        anio_actual = datetime.utcnow().year
        if not years:
            years = [anio_actual]
        if year is None or year not in years:
            year = years[-1]

        # Serie mensual del año seleccionado
        mes_col = func.extract('month', Order.created_at)
        q_mensual = (
            select(
                mes_col.label("mes"),
                func.count(Order.id),
                func.coalesce(func.sum(Order.total_price), 0.0),
                func.coalesce(func.sum(Order.shipping_cost), 0.0),
            )
            .where(Order.status.in_(ESTADOS_VENTA), anio_col == year)
            .group_by(mes_col)
        )
        res_mensual = await db.execute(q_mensual)
        por_mes = {int(m): (int(c), float(t), float(e)) for m, c, t, e in res_mensual.all()}

        MESES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
        monthly = []
        for m in range(1, 13):
            ordenes, total, envios = por_mes.get(m, (0, 0.0, 0.0))
            monthly.append({"month": m, "label": MESES[m - 1], **_resumen(total, envios, ordenes)})

        # Totales del año seleccionado
        tot_ordenes = sum(x["orders_count"] for x in monthly)
        tot_total = sum(x["sales_total"] for x in monthly)
        tot_envios = sum(x["shipping_total"] for x in monthly)
        totals_year = _resumen(tot_total, tot_envios, tot_ordenes)

        # Totales históricos (todos los años)
        q_all = select(
            func.count(Order.id),
            func.coalesce(func.sum(Order.total_price), 0.0),
            func.coalesce(func.sum(Order.shipping_cost), 0.0),
        ).where(Order.status.in_(ESTADOS_VENTA))
        res_all = await db.execute(q_all)
        c_all, t_all, e_all = res_all.first()
        all_time = _resumen(float(t_all or 0), float(e_all or 0), int(c_all or 0))

        return {
            "years": years,
            "year": year,
            "monthly": monthly,
            "totals_year": totals_year,
            "all_time": all_time,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al obtener estadísticas: {str(e)}")


def _label_estado_envio(shipment_status_value: str) -> str:
    """Traduce el estado interno del envío (ShipmentStatus) a una etiqueta para el usuario."""
    mapa = {
        ShipmentStatus.LABEL_GENERATED.value: "En preparación",
        ShipmentStatus.DISPATCHED.value: "Despachado",
        ShipmentStatus.IN_TRANSIT.value: "En viaje",
        ShipmentStatus.OUT_FOR_DELIVERY.value: "En reparto",
        ShipmentStatus.DELIVERED.value: "Entregado",
        ShipmentStatus.ARRIVED.value: "Entregado",
    }
    return mapa.get(shipment_status_value, "En preparación")


@router.get("/label/{tracking_number}")
async def obtener_etiqueta_envio(
    tracking_number: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Genera la etiqueta de envío (HTML imprimible) con los datos REALES del envío.
    Solo el vendedor de la orden puede obtenerla.
    """
    from fastapi.responses import HTMLResponse

    query_ship = select(Shipment).where(Shipment.tracking_number == tracking_number.strip().upper())
    res_ship = await db.execute(query_ship)
    shipment = res_ship.scalar_one_or_none()
    if not shipment:
        raise HTTPException(status_code=404, detail="Guía de envío no encontrada.")

    query_order = (
        select(Order, Product, User.full_name)
        .join(Product, Order.product_id == Product.id)
        .join(User, Product.seller_id == User.id)
        .where(Order.id == shipment.order_id)
    )
    res_order = await db.execute(query_order)
    row = res_order.first()
    if not row:
        raise HTTPException(status_code=404, detail="Orden asociada no encontrada.")
    order, product, seller_name = row

    if current_user.id != product.seller_id:
        raise HTTPException(status_code=403, detail="Solo el vendedor puede imprimir esta etiqueta.")

    direccion = f"{shipment.street or ''} {shipment.number or ''}"
    if shipment.floor_dept:
        direccion += f", {shipment.floor_dept}"
    def _generar_barcode_svg(texto: str) -> str:
        patterns = {
            '0': '000110100', '1': '100100001', '2': '001100001', '3': '101100000',
            '4': '000110001', '5': '100110000', '6': '001110000', '7': '000100101',
            '8': '100100100', '9': '001100100', 'A': '100001001', 'B': '001001001',
            'C': '101001000', 'D': '000011001', 'E': '100011000', 'F': '001011000',
            'G': '000001101', 'H': '100001100', 'I': '001001100', 'J': '000011100',
            'K': '100000011', 'L': '001000011', 'M': '101000010', 'N': '000010011',
            'O': '100010010', 'P': '001010010', 'Q': '000000111', 'R': '100000110',
            'S': '001000110', 'T': '000010110', 'U': '110000001', 'V': '011000001',
            'W': '111000000', 'X': '010010001', 'Y': '110010000', 'Z': '011010000',
            '-': '010000101', '.': '110000100', ' ': '011000100', '*': '010010100'
        }
        raw = f"*{texto.upper().strip()}*"
        narrow = 2.4
        wide = 5.8
        height = 62.0
        rects = []
        x = 12.0
        for char in raw:
            pat = patterns.get(char, patterns[' '])
            for i, bit in enumerate(pat):
                w = wide if bit == '1' else narrow
                if i % 2 == 0:
                    rects.append(f'<rect x="{x:.1f}" y="0" width="{w:.1f}" height="{height}" fill="#000" />')
                x += w
            x += narrow
        total_w = x + 12.0
        return f'<svg viewBox="0 0 {total_w:.1f} {height}" style="width: 100%; max-width: 420px; height: 62px;" xmlns="http://www.w3.org/2000/svg">{"".join(rects)}</svg>'

    tracking_raw = tracking_number.strip().upper()
    if len(tracking_raw) == 13:
        tracking_formatted = f"{tracking_raw[:2]} {tracking_raw[2:5]} {tracking_raw[5:8]} {tracking_raw[8:11]} {tracking_raw[11:]}"
    else:
        tracking_formatted = tracking_raw

    barcode_svg = _generar_barcode_svg(tracking_raw)
    recipient_name = shipment.recipient_name or "Destinatario"
    postal_code = (shipment.postal_code or "").strip().upper()
    city = shipment.city or "Ciudad"
    province = shipment.province or "Provincia"
    peso_str = f"{product.weight_kg:.1f} kg" if getattr(product, "weight_kg", None) else "5.0 kg"
    medidas_str = f"{getattr(product, 'length_cm', 30):.0f}x{getattr(product, 'width_cm', 30):.0f}x{getattr(product, 'height_cm', 30):.0f} cm"

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <title>Etiqueta de Envío · {tracking_raw} · Objetia</title>
  <style>
    @page {{
      size: auto;
      margin: 10mm;
    }}
    * {{
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background-color: #f6f5f2;
      color: #2C2723;
      display: flex;
      flex-direction: column;
      align-items: center;
      padding: 24px;
    }}
    @media print {{
      body {{
        background: #fff;
        padding: 0;
      }}
      .no-print {{
        display: none !important;
      }}
      .label-card {{
        box-shadow: none !important;
        border: 2px solid #000 !important;
      }}
    }}
    .cut-guide {{
      width: 100%;
      max-width: 520px;
      display: flex;
      align-items: center;
      gap: 10px;
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 1px;
      color: #73675C;
      margin-bottom: 10px;
    }}
    .cut-guide::before, .cut-guide::after {{
      content: "";
      flex: 1;
      border-bottom: 1.5px dashed #A28764;
    }}
    .label-card {{
      width: 100%;
      max-width: 520px;
      background: #fff;
      border: 2.5px solid #2C2723;
      border-radius: 14px;
      padding: 24px 26px;
      box-shadow: 0 4px 20px rgba(44, 39, 35, 0.08);
    }}
    /* Encabezado */
    .header {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      border-bottom: 2px solid #2C2723;
      padding-bottom: 14px;
      gap: 16px;
    }}
    .brand-title {{
      font-size: 26px;
      font-weight: 900;
      letter-spacing: 3px;
      color: #2C2723;
      line-height: 1;
    }}
    .brand-title span {{
      color: #A28764;
      font-size: 20px;
      margin-left: 2px;
    }}
    .brand-subtitle {{
      font-size: 10px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 1.2px;
      color: #73675C;
      margin-top: 4px;
    }}
    .correo-pill {{
      background: #003366;
      color: #ffffff;
      padding: 6px 12px;
      border-radius: 8px;
      text-align: right;
    }}
    .correo-name {{
      font-size: 12px;
      font-weight: 900;
      letter-spacing: 1px;
    }}
    .correo-sub {{
      font-size: 9px;
      font-weight: 700;
      letter-spacing: 0.8px;
      opacity: 0.9;
      margin-top: 2px;
    }}

    /* Barcode y Tracking */
    .barcode-section {{
      text-align: center;
      padding: 18px 0 14px;
      border-bottom: 1.5px solid #EAE5DC;
    }}
    .tracking-label {{
      font-size: 10px;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: 1.5px;
      color: #73675C;
      margin-bottom: 4px;
    }}
    .tracking-code {{
      font-family: "Courier New", Courier, monospace;
      font-size: 22px;
      font-weight: 900;
      letter-spacing: 3px;
      color: #111;
      margin-bottom: 8px;
    }}
    .barcode-box {{
      display: flex;
      justify-content: center;
      align-items: center;
      margin: 8px 0;
    }}
    .service-badge {{
      display: inline-block;
      font-size: 10px;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: 1px;
      padding: 4px 12px;
      background: #FAF0E6;
      border: 1px solid #EAE5DC;
      border-radius: 20px;
      color: #2C2723;
    }}

    /* Destinatario y Routing */
    .dest-section {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      padding: 16px 0;
      border-bottom: 1.5px solid #EAE5DC;
    }}
    .dest-left {{
      flex: 1;
    }}
    .tag-label {{
      font-size: 10px;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: 1.2px;
      color: #73675C;
      margin-bottom: 4px;
    }}
    .dest-name {{
      font-size: 17px;
      font-weight: 800;
      color: #2C2723;
      line-height: 1.25;
      margin-bottom: 4px;
    }}
    .dest-address {{
      font-size: 14px;
      font-weight: 600;
      color: #333;
      line-height: 1.35;
    }}
    .dest-city {{
      font-size: 13px;
      color: #666;
      margin-top: 2px;
    }}
    .cp-routing-box {{
      width: 140px;
      border: 2px solid #2C2723;
      border-radius: 10px;
      background: #FAF8F5;
      padding: 10px;
      text-align: center;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
    }}
    .cp-header {{
      font-size: 9px;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: 1px;
      color: #73675C;
    }}
    .cp-val {{
      font-family: "Courier New", Courier, monospace;
      font-size: 24px;
      font-weight: 900;
      color: #2C2723;
      margin: 3px 0;
      letter-spacing: 1px;
    }}
    .cp-dest {{
      font-size: 9.5px;
      font-weight: 800;
      text-transform: uppercase;
      color: #003366;
    }}

    /* Remitente y Detalles */
    .grid-info {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 16px;
      padding: 14px 0;
      border-bottom: 1.5px solid #EAE5DC;
    }}
    .rem-name {{
      font-size: 13px;
      font-weight: 700;
      color: #2C2723;
    }}
    .rem-badge {{
      font-size: 10px;
      color: #73675C;
      margin-top: 2px;
    }}
    .pkg-info {{
      text-align: right;
    }}
    .pkg-order {{
      font-family: monospace;
      font-size: 12px;
      font-weight: 800;
      color: #2C2723;
    }}
    .pkg-title {{
      font-size: 11px;
      color: #555;
      margin-top: 2px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .pkg-specs {{
      font-size: 10px;
      font-weight: 600;
      color: #73675C;
      margin-top: 2px;
    }}

    /* Pie con Garantía e Instrucciones */
    .footer {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      padding-top: 14px;
    }}
    .instructions {{
      font-size: 10px;
      color: #73675C;
      line-height: 1.4;
      flex: 1;
    }}
    .escrow-shield {{
      display: inline-flex;
      align-items: center;
      gap: 5px;
      font-size: 10px;
      font-weight: 800;
      color: #00a650;
      background: #e8f8ef;
      border: 1px solid #ceead6;
      padding: 5px 10px;
      border-radius: 8px;
      white-space: nowrap;
    }}
  </style>
</head>
<body onload="window.print()">
  <div class="cut-guide no-print">✂ Doblar o cortar por aquí para adherir al paquete</div>
  <div class="label-card">
    <div class="header">
      <div>
        <div class="brand-title">OBJETIA<span>✦</span></div>
        <div class="brand-subtitle">Decoración & Muebles de Autor</div>
      </div>
      <div class="correo-pill">
        <div class="correo-name">CORREO ARGENTINO</div>
        <div class="correo-sub">PAQ.AR · ENCOMIENDA CLÁSICA</div>
      </div>
    </div>

    <div class="barcode-section">
      <div class="tracking-label">Código de Seguimiento Oficial</div>
      <div class="tracking-code">{tracking_formatted}</div>
      <div class="barcode-box">
        {barcode_svg}
      </div>
      <div class="service-badge">Servicio Puerta a Puerta · Entrega con Firma</div>
    </div>

    <div class="dest-section">
      <div class="dest-left">
        <div class="tag-label">Destinatario</div>
        <div class="dest-name">{recipient_name}</div>
        <div class="dest-address">{direccion.strip()}</div>
        <div class="dest-city">{city}, {province}</div>
      </div>
      <div class="cp-routing-box">
        <div class="cp-header">Código Postal</div>
        <div class="cp-val">{postal_code or '----'}</div>
        <div class="cp-dest">{province or 'ARGENTINA'}</div>
      </div>
    </div>

    <div class="grid-info">
      <div>
        <div class="tag-label">Remitente</div>
        <div class="rem-name">{seller_name}</div>
        <div class="rem-badge">Vendedor Verificado Objetia</div>
      </div>
      <div class="pkg-info">
        <div class="tag-label">Paquete</div>
        <div class="pkg-order">#OBJ-000{order.id}</div>
        <div class="pkg-title" title="{product.title}">{product.title}</div>
        <div class="pkg-specs">Peso: {peso_str} · Dimensiones: {medidas_str}</div>
      </div>
    </div>

    <div class="footer">
      <div class="instructions">
        <strong>Instrucciones:</strong> Pegar en la cara superior plana del paquete. No doblar ni cubrir el código de barras. Despachar en cualquier sucursal de Correo Argentino.
      </div>
      <div class="escrow-shield">
        ✓ Garantía Objetia 7 Días
      </div>
    </div>
  </div>
</body>
</html>"""
    return HTMLResponse(content=html)


@router.get("/tracking/", status_code=status.HTTP_200_OK)
async def obtener_detalles_tracking(
    number: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Obtiene el estado y los datos de entrega de un envío por su guía.
    Requiere autenticación: solo el comprador o el vendedor de la orden pueden verlo
    (evita la fuga de datos personales a cualquiera con el número de guía).
    """
    try:
        # Buscar Shipment
        query_ship = select(Shipment).where(Shipment.tracking_number == number.strip().upper())
        res_ship = await db.execute(query_ship)
        shipment = res_ship.scalar_one_or_none()
        
        if not shipment:
            raise HTTPException(status_code=404, detail="Guía de envío no encontrada.")
            
        # Buscar Orden y Producto
        query_order = (
            select(Order, Product, User.full_name)
            .join(Product, Order.product_id == Product.id)
            .join(User, Product.seller_id == User.id)
            .where(Order.id == shipment.order_id)
        )
        res_order = await db.execute(query_order)
        row = res_order.first()
        
        if not row:
            raise HTTPException(status_code=404, detail="Orden asociada al envío no encontrada.")
            
        order, product, seller_name = row

        # Autorización: solo comprador o vendedor de la orden
        if current_user.id != order.buyer_id and current_user.id != product.seller_id:
            raise HTTPException(status_code=403, detail="No tenés permiso para ver este envío.")

        # Timeline real: eventos registrados por el correo (simulador) para este envío
        from src.modules.orders.models import ShipmentEvent
        from src.modules.shipping.router import SECUENCIA_ESTADOS, DETALLE_EVENTO, _indice_estado

        query_events = select(ShipmentEvent).where(
            ShipmentEvent.shipment_id == shipment.id
        ).order_by(ShipmentEvent.created_at.asc())
        res_events = await db.execute(query_events)
        eventos = res_events.scalars().all()
        eventos_por_estado = {}
        for ev in eventos:
            eventos_por_estado.setdefault(ev.status, ev)  # Nos quedamos con el primero de cada estado

        idx_actual = _indice_estado(shipment.status)
        timeline_events = [{
            "code": "CONFIRMED",
            "title": "Pedido confirmado",
            "description": "Recibimos tu pago y le avisamos al vendedor",
            "location": "Vamaar",
            "date": order.created_at.isoformat() + "Z",
            "done": True,
            "current": False,
        }]
        for idx, estado in enumerate(SECUENCIA_ESTADOS):
            detalle = DETALLE_EVENTO[estado]
            evento = eventos_por_estado.get(estado.value)
            hecho = idx <= idx_actual
            timeline_events.append({
                "code": estado.value,
                "title": detalle["label"],
                "description": detalle["description"] if hecho else None,
                "location": (evento.location if evento else detalle["location"]) if hecho else None,
                "date": (evento.created_at.isoformat() + "Z") if evento and hecho else None,
                "done": hecho,
                "current": (idx == idx_actual) and (estado.value != "DELIVERED") and (shipment.status.value != "DELIVERED"),
            })

        return {
            "number": number,
            "order_id": f"LH-000{order.id}",
            "store_name": f"Casa de {seller_name}",
            "date": order.created_at.strftime("%d/%m/%Y"),
            "total": f"${order.total_price:.2f}",
            "status": _label_estado_envio(shipment.status.value),
            "recipient_name": shipment.recipient_name or "Destinatario",
            "street": shipment.street or "",
            "number_addr": shipment.number or "",
            "floor_dept": shipment.floor_dept or "",
            "postal_code": shipment.postal_code or "",
            "city": shipment.city or "",
            "province": shipment.province or "",
            "timeline": timeline_events
        }
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al obtener tracking: {str(e)}")
