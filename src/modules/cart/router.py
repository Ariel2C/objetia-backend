from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from redis.asyncio import Redis
from typing import List, Dict

from src.config.database import get_db
from src.config.redis import get_redis
from src.modules.cart.services import CartService

# IMPORTAMOS EL MIDDLEWARE
from src.common.dependencies import get_current_user
from src.modules.users.models import User

router = APIRouter(prefix="/cart", tags=["Carrito de Compras"])

@router.post("/add/{product_id}", status_code=status.HTTP_200_OK)
async def agregar_al_carrito(
    product_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Agrega un artículo al carrito persistente (sin bloquear stock)."""
    resultado = await CartService.agregar_item(current_user.id, product_id, db)
    return resultado


@router.get("/", response_model=List[Dict])
async def ver_mi_carrito(
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    current_user: User = Depends(get_current_user)
):
    """Retorna el carrito persistente del usuario con estado de disponibilidad actualizado."""
    carrito = await CartService.obtener_carrito(current_user.id, db, redis)
    return carrito


@router.delete("/remove/{product_id}")
async def remover_del_carrito(
    product_id: int,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    current_user: User = Depends(get_current_user)
):
    """Elimina un artículo del carrito persistente del usuario."""
    resultado = await CartService.remover_item(current_user.id, product_id, db, redis)
    return resultado
