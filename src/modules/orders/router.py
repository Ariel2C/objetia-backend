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
    product_id: int,
    recipient_name: str,
    street: str,
    number: str,
    postal_code: str,
    city: str,
    province: str,
    floor_dept: Optional[str] = None,
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
        # 1. Crear la orden pendiente (el envío se calcula server-side y se valida la reserva)
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
            redis=redis
        )
        
        # 2. Buscar detalles del producto para los metadatos de Mercado Pago
        query = select(Product).where(Product.id == product_id)
        result = await db.execute(query)
        product = result.scalar_one_or_none()
        if not product:
            raise HTTPException(status_code=404, detail="Producto no encontrado")
            
        # 3. Generar la preferencia de Mercado Pago
        preferencia = OrderService.crear_preferencia_mercadopago(
            order=orden, product_title=product.title, product_price=product.price
        )
        
        # Guardar la orden pendiente
        await db.commit()
        
        return {
            "mensaje": "Preferencia de Mercado Pago generada con éxito.",
            "order_id": orden.id,
            "preference_id": preferencia.get("id"),
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
        # 1. Crear la orden pendiente (valida stock, autocompra y reserva; calcula envío server-side)
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
            redis=redis
        )

        # 2. Debitar la billetera del comprador por el total (producto + envío)
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
    current_user: User = Depends(get_current_user)
):
    """
    Retorna la lista de compras del usuario actual.
    """
    try:
        # Cancelar checkouts abandonados antes de listar (no se muestran las canceladas)
        expiradas = await OrderService.expirar_ordenes_pendientes(db)
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
    current_user: User = Depends(get_current_user)
):
    """
    Retorna la lista de ventas del usuario actual.
    """
    try:
        # Cancelar checkouts abandonados antes de listar (no se muestran las canceladas)
        expiradas = await OrderService.expirar_ordenes_pendientes(db)
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
    html = f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"><title>Etiqueta {tracking_number}</title>
<style>
  body {{ font-family: Arial, sans-serif; padding: 24px; }}
  .label {{ border: 2px solid #111; border-radius: 8px; max-width: 480px; padding: 20px; }}
  .brand {{ font-weight: 800; font-size: 22px; letter-spacing: 1px; }}
  .track {{ font-family: monospace; font-size: 20px; font-weight: bold; margin: 12px 0; }}
  .row {{ margin: 6px 0; font-size: 14px; }}
  .muted {{ color: #555; font-size: 12px; text-transform: uppercase; }}
  hr {{ border: none; border-top: 1px dashed #999; margin: 16px 0; }}
</style></head>
<body onload="window.print()">
  <div class="label">
    <div class="brand">VAMAAR · Correo Argentino</div>
    <div class="track">{tracking_number}</div>
    <hr>
    <div class="muted">Destinatario</div>
    <div class="row"><strong>{shipment.recipient_name or 'Destinatario'}</strong></div>
    <div class="row">{direccion.strip()}</div>
    <div class="row">{shipment.postal_code or ''} - {shipment.city or ''}, {shipment.province or ''}</div>
    <hr>
    <div class="muted">Remitente</div>
    <div class="row">{seller_name}</div>
    <hr>
    <div class="muted">Pedido</div>
    <div class="row">#LH-000{order.id} · {product.title}</div>
  </div>
</body></html>"""
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
                "current": idx == idx_actual,
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
