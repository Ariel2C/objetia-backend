import hashlib
from typing import Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, status, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from redis.asyncio import Redis

from src.config.database import get_db
from src.config.redis import get_redis
from src.common.dependencies import get_current_user, get_optional_current_user, RoleChecker
from src.modules.users.models import User, UserRole
from src.modules.analytics.services import AnalyticsService

router = APIRouter(prefix="/analytics", tags=["Módulo de Analítica y Métricas"])

# ==============================================================================
# ESQUEMAS DE ENTRADA (PYDANTIC)
# ==============================================================================
class EventPayload(BaseModel):
    product_id: int
    event_type: str = "view" # view, click, favorite_add, favorite_remove, cart_add, purchase
    visitor_hash: Optional[str] = None

class SearchPayload(BaseModel):
    query: str
    results_count: int = 0


# ==============================================================================
# ENDPOINTS PÚBLICOS / SEMI-PÚBLICOS DE TRACKING
# ==============================================================================
@router.post("/event", status_code=status.HTTP_200_OK)
async def registrar_evento_interaccion(
    payload: EventPayload,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    current_user: Optional[User] = Depends(get_optional_current_user)
):
    """
    Registra un evento de interacción con un producto (vista, favorito, carrito, etc.).
    Deduplica visitas en ventana de 30 minutos según la huella digital del visitante.
    """
    # Generar huella digital de respaldo si no la envía el frontend
    visitor_hash = payload.visitor_hash
    if not visitor_hash:
        client_ip = request.client.host if request.client else "unknown"
        user_agent = request.headers.get("user-agent", "")
        raw_fingerprint = f"{client_ip}-{user_agent}"
        visitor_hash = hashlib.sha256(raw_fingerprint.encode("utf-8")).hexdigest()

    user_id = current_user.id if current_user else None

    registrado = await AnalyticsService.record_product_event(
        db=db,
        product_id=payload.product_id,
        event_type=payload.event_type,
        user_id=user_id,
        visitor_hash=visitor_hash,
        redis_conn=redis
    )

    return {"ok": True, "recorded": registrado}


@router.post("/search", status_code=status.HTTP_200_OK)
async def registrar_busqueda(
    payload: SearchPayload,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_current_user)
):
    """
    Registra un término buscado por un usuario en el catálogo o barra de navegación.
    Alimenta la analítica de tendencias y términos más buscados.
    """
    user_id = current_user.id if current_user else None
    await AnalyticsService.record_search_query(
        db=db,
        query=payload.query,
        user_id=user_id,
        results_count=payload.results_count
    )
    return {"ok": True}


# ==============================================================================
# ENDPOINTS DE MÉTRICAS (VENDEDORES Y ADMINISTRACIÓN)
# ==============================================================================
@router.get("/seller/metrics", status_code=status.HTTP_200_OK)
async def obtener_metricas_vendedor(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Devuelve las métricas de impacto y audiencia para el vendedor autenticado:
    - Visualizaciones totales
    - Favoritos acumulados
    - Ventas realizadas
    - Tasa de conversión (%)
    - Serie de tiempo de visitas últimos 30 días
    - Ranking de sus 5 publicaciones con mejor desempeño
    """
    metricas = await AnalyticsService.get_seller_metrics(db=db, seller_id=current_user.id)
    return metricas


@router.get("/admin/overview", status_code=status.HTTP_200_OK)
async def obtener_metricas_administrador(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Devuelve la analítica global de la plataforma para roles Root y Administradores:
    - Tráfico global y visitas (hoy, 7d, 30d, histórico y curva temporal)
    - Palabras clave más buscadas en la plataforma
    - Embudo de conversión (Vistas -> Favoritos -> Carrito -> Compras)
    - Distribución de rendimiento por categoría
    - Top productos más relevantes de la plataforma
    """
    # Verificación de permisos de administrador
    email = (current_user.email or "").lower().strip()
    role_str = str(current_user.role.value if hasattr(current_user.role, 'value') else current_user.role).lower().strip()
    
    es_admin = (
        role_str in ("admin", "administrador", "root") or 
        email in ("admin@vamaar.com", "root@objetia.com", "arielcaballero182@gmail.com")
    )
    
    if not es_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tienes los permisos requeridos para acceder a la analítica global de la plataforma."
        )

    analitica = await AnalyticsService.get_admin_analytics(db=db)
    return analitica
