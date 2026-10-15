from typing import List, Dict, Optional
from fastapi import HTTPException, status
from redis.asyncio import Redis
from sqlmodel import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.modules.products.models import Product, ProductImage, ModerationStatus
from src.modules.cart.models import CartItem

class CartService:
    @staticmethod
    def _get_checkout_lock_key(product_id: int) -> str:
        """Clave única para el bloqueo temporal durante el pago (15 minutos)."""
        return f"checkout_lock:{product_id}"

    @classmethod
    async def agregar_item(
        cls, user_id: int, product_id: int, db: AsyncSession
    ) -> Dict:
        """
        Añade un producto al carrito persistente en PostgreSQL sin bloquear stock.
        El producto sigue disponible para todos hasta el momento de iniciar el pago.
        """
        # 1. Verificar si el producto existe en PostgreSQL
        query = select(Product).where(Product.id == product_id)
        result = await db.execute(query)
        product = result.scalar_one_or_none()

        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, 
                detail="El producto no existe."
            )

        # 2. El vendedor no puede agregar su propia publicación
        if product.seller_id == user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No podés agregar tu propia publicación al carrito."
            )
        
        # 3. Validar disponibilidad básica
        if product.stock < 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, 
                detail="Este artículo ya ha sido vendido o está agotado."
            )

        if product.moderation_status == ModerationStatus.PAUSED.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Esta publicación está pausada temporalmente por el vendedor."
            )

        # 4. Guardar o actualizar en la tabla cart_items
        q_item = select(CartItem).where(
            CartItem.user_id == user_id, 
            CartItem.product_id == product_id
        )
        res_item = await db.execute(q_item)
        existing_item = res_item.scalar_one_or_none()

        if not existing_item:
            nuevo_item = CartItem(
                user_id=user_id,
                product_id=product_id,
                price_at_add=product.price
            )
            db.add(nuevo_item)
            await db.commit()

        return {
            "mensaje": "Producto agregado a tu carrito con éxito.",
            "expires_in_seconds": None
        }

    @classmethod
    async def obtener_carrito(
        cls, user_id: int, db: AsyncSession, redis: Redis
    ) -> List[Dict]:
        """
        Recupera todos los artículos guardados en el carrito persistente del usuario.
        Determina dinámicamente si el producto sigue disponible, si alguien lo está pagando,
        o si ya fue vendido / pausado.
        """
        query = (
            select(CartItem, Product)
            .join(Product, CartItem.product_id == Product.id)
            .where(CartItem.user_id == user_id)
            .options(selectinload(Product.images))
            .order_by(CartItem.added_at.desc())
        )
        result = await db.execute(query)
        rows = result.all()

        carrito = []
        for cart_item, product in rows:
            # Obtener imagen principal
            img_url = ""
            for img in product.images:
                if img.is_primary:
                    img_url = img.cloudfront_url
                    break
            if not img_url and product.images:
                img_url = product.images[0].cloudfront_url

            # Determinar disponibilidad
            lock_key = cls._get_checkout_lock_key(product.id)
            lock_owner = await redis.get(lock_key)
            owner_id = int(lock_owner) if lock_owner is not None else None

            if product.stock < 1:
                availability = "SOLD"
            elif product.moderation_status == ModerationStatus.PAUSED.value or product.moderation_status == "PAUSED":
                availability = "PAUSED"
            elif owner_id is not None and owner_id != user_id:
                availability = "IN_CHECKOUT"
            else:
                availability = "AVAILABLE"

            price_changed = abs(product.price - cart_item.price_at_add) > 0.01

            carrito.append({
                "id": product.id,
                "title": product.title,
                "price": product.price,
                "category": product.category,
                "image_url": img_url,
                "availability": availability,
                "price_at_add": cart_item.price_at_add,
                "price_changed": price_changed,
                "added_at": cart_item.added_at.isoformat(),
                "reservation_ttl": 0, # Compatibilidad hacia atrás
            })

        return carrito

    @classmethod
    async def remover_item(
        cls, user_id: int, product_id: int, db: AsyncSession, redis: Optional[Redis] = None
    ) -> Dict:
        """Elimina un artículo del carrito persistente del usuario."""
        # Si el usuario tenía el checkout lock de este producto, liberarlo
        if redis is not None:
            lock_key = cls._get_checkout_lock_key(product_id)
            lock_owner = await redis.get(lock_key)
            if lock_owner and int(lock_owner) == user_id:
                await redis.delete(lock_key)

        query = delete(CartItem).where(
            CartItem.user_id == user_id,
            CartItem.product_id == product_id
        )
        await db.execute(query)
        await db.commit()

        return {"mensaje": "Artículo removido de tu carrito."}

    @classmethod
    async def remover_de_todos_los_carritos(
        cls, product_id: int, db: AsyncSession
    ) -> None:
        """
        Elimina el producto de los carritos de todos los usuarios cuando se concreta una venta.
        """
        query = delete(CartItem).where(CartItem.product_id == product_id)
        await db.execute(query)
