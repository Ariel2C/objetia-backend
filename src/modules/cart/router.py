from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from redis.asyncio import Redis
from typing import List, Dict

from src.config.database import get_db
from src.config.redis import get_redis
from src.modules.cart.services import CartService

# 🌟 IMPORTAMOS EL MIDDLEWARE
from src.common.dependencies import get_current_user
from src.modules.users.models import User

router = APIRouter(prefix="/cart", tags=["Carrito de Compras (Reservas RAM)"])

@router.post("/add/{product_id}", status_code=status.HTTP_200_OK)
async def agregar_al_carrito(
    product_id: int,
    redis: Redis = Depends(get_redis),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user) # 🌟 RUTA BLINDADA
):
    """Agrega un artículo bloqueando su stock usando el ID del JWT."""
    resultado = await CartService.agregar_item(current_user.id, product_id, redis, db)
    return resultado


@router.get("/", response_model=List[Dict])
async def ver_mi_carrito(
    redis: Redis = Depends(get_redis),
    current_user: User = Depends(get_current_user) # 🌟 RUTA BLINDADA
):
    """Retorna el carrito del usuario logueado legítimamente."""
    carrito = await CartService.obtener_carrito(current_user.id, redis)
    return carrito


@router.delete("/remove/{product_id}")
async def remover_del_carrito(
    product_id: int,
    redis: Redis = Depends(get_redis),
    current_user: User = Depends(get_current_user) # 🌟 RUTA BLINDADA
):
    """Elimina un artículo liberando el stock usando el ID del JWT."""
    resultado = await CartService.remover_item(current_user.id, product_id, redis)
    return resultado
