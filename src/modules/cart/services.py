import json
from typing import List, Dict, Optional
from fastapi import HTTPException, status
from redis.asyncio import Redis
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.products.models import Product

class CartService:
    @staticmethod
    def _get_cart_key(user_id: int) -> str:
        """Genera una clave única en la caché para el carrito de cada usuario."""
        return f"cart:{user_id}"

    @staticmethod
    def _get_lock_key(product_id: int) -> str:
        """Genera una clave única para controlar el bloqueo de stock de un producto único."""
        return f"product_lock:{product_id}"

    @classmethod
    async def agregar_item(
        cls, user_id: int, product_id: int, redis: Redis, db: AsyncSession
    ) -> Dict:
        """
        Añade un producto al carrito local y bloquea su stock exclusivo por 10 minutos
        utilizando un temporizador TTL en memoria.
        """
        # 1. Verificar si el producto existe y tiene stock disponible en PostgreSQL
        query = select(Product).where(Product.id == product_id)
        result = await db.execute(query)
        product = result.scalar_one_or_none()

        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, 
                detail="El producto de decoración no existe."
            )
        
        if product.stock < 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, 
                detail="Este artículo ya ha sido vendido o está agotado."
            )

        # 2. Reservar de forma ATÓMICA con SET NX (evita la condición de carrera de dos
        # compradores tomando el mismo artículo único a la vez).
        lock_key = cls._get_lock_key(product_id)
        adquirido = await redis.set(lock_key, user_id, ex=600, nx=True)

        if not adquirido:
            # Ya existe un lock: solo se permite si es del mismo usuario (renovamos su reserva)
            lock_owner = await redis.get(lock_key)
            owner_id = int(lock_owner) if lock_owner is not None else None
            if owner_id != user_id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Este artículo único ya está reservado temporalmente en el carrito de otro comprador."
                )
            # Renovar el TTL de la reserva propia
            await redis.set(lock_key, user_id, ex=600)

        # 4. Añadir el producto al carrito del usuario en la caché
        cart_key = cls._get_cart_key(user_id)
        product_data = {
            "id": product.id,
            "title": product.title,
            "price": product.price,
            "category": product.category
        }
        
        # Guardamos el producto dentro de un diccionario en caché (HSET)
        await redis.hset(cart_key, str(product_id), json.dumps(product_data))
        
        # Retornamos el tiempo de reserva restante para que el Front-End muestre un contador
        ttl_restante = await redis.ttl(lock_key)

        return {
            "mensaje": "Producto reservado y agregado al carrito con éxito.",
            "expires_in_seconds": ttl_restante
        }

    @classmethod
    async def obtener_carrito(cls, user_id: int, redis: Redis) -> List[Dict]:
        """Recupera todos los artículos guardados en el carrito del usuario."""
        cart_key = cls._get_cart_key(user_id)
        # Extrae todos los elementos mapeados en el hash de la caché
        items_raw = await redis.hgetall(cart_key)
        
        carrito = []
        for prod_id, data_str in items_raw.items():
            item = json.loads(data_str)
            # Consultamos el tiempo de vida (TTL) dinámico de cada reserva
            lock_key = cls._get_lock_key(int(prod_id))
            ttl = await redis.ttl(lock_key)
            
            # Si el bloqueo ya expiró en segundo plano, limpiamos el artículo del carrito
            if ttl <= 0:
                await redis.hdel(cart_key, prod_id)
                continue
                
            item["reservation_ttl"] = ttl
            carrito.append(item)
            
        return carrito

    @classmethod
    async def remover_item(cls, user_id: int, product_id: int, redis: Redis):
        """Elimina un artículo del carrito de forma manual y libera el stock inmediatamente."""
        cart_key = cls._get_cart_key(user_id)
        lock_key = cls._get_lock_key(product_id)

        # Verificar si el usuario es el dueño legítimo de la reserva antes de borrar
        lock_owner = await redis.get(lock_key)
        if lock_owner and int(lock_owner) == user_id:
            await redis.delete(lock_key) # Libera el sillón/decoración para otros usuarios

        await redis.hdel(cart_key, str(product_id))
        return {"mensaje": "Artículo removido de tu carrito y disponible nuevamente."}
