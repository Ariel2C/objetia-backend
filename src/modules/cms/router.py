from fastapi import APIRouter, Depends, UploadFile, File, Form, BackgroundTasks, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
from typing import List, Optional
from datetime import datetime

from src.config.database import get_db
from src.modules.cms.models import CarouselBanner, StoreBranding, BrandingUpdate
from src.modules.cms.pipeline import ejecutar_pipeline_subida_cms

# Middleware de control de accesos Senior
from src.common.dependencies import RoleChecker
from src.modules.users.models import UserRole, User

router = APIRouter(prefix="/cms", tags=["Configuración y Personalización Visual (CMS)"])

# Regla estricta: Solo el rol ADMIN puede alterar los colores y banners corporativos
solo_administradores = RoleChecker([UserRole.ADMIN])


# ==============================================================================
# ENDPOINTS PÚBLICOS (Cualquier visitante de la web los consume al entrar)
# ==============================================================================
@router.get("/layout/", status_code=status.HTTP_200_OK)
async def obtener_identidad_visual_tienda(db: AsyncSession = Depends(get_db)):
    """Retorna los colores de la marca, el logo actual y la lista de banners activos del carrusel."""
    query_brand = select(StoreBranding).where(StoreBranding.id == 1)
    res_brand = await db.execute(query_brand)
    branding = res_brand.scalar_one_or_none()

    if not branding:
        # Inicialización por defecto en tu PostgreSQL local si la tabla está vacía
        branding = StoreBranding(id=1)
        db.add(branding)
        await db.commit()
        await db.refresh(branding)

    query_banners = select(CarouselBanner).where(CarouselBanner.is_active == True).order_by(CarouselBanner.orden)
    res_banners = await db.execute(query_banners)
    banners = res_banners.scalars().all()

    return {
        "marca": branding,
        "carrusel_banners": banners
    }


# ==============================================================================
# ENDPOINTS PRIVADOS (Protegidos con JWT y Rol de Administrador)
# ==============================================================================
@router.post("/banner/upload/", status_code=status.HTTP_202_ACCEPTED)
async def agregar_banner_carrusel(
    background_tasks: BackgroundTasks,
    title: str = Form(None),
    subtitle: str = Form(None),
    link_url: str = Form(None),
    orden: int = Form(0),
    file: UploadFile = File(...),
    mobile_file: Optional[UploadFile] = File(None),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(solo_administradores) # 🌟 RUTA BLINDADA
):
    """Crea el registro del nuevo banner y procesa su subida a S3 en segundo plano."""
    mobile_url = "procesando..." if mobile_file else None
    
    nuevo_banner = CarouselBanner(
        title=title,
        subtitle=subtitle,
        link_url=link_url,
        cloudfront_url="procesando...", # Temporal mientras la tarea asíncrona finaliza
        mobile_cloudfront_url=mobile_url,
        orden=orden
    )
    db.add(nuevo_banner)
    await db.flush()

    # Confirmar el banner antes de que corran las background tasks (que usan su propia sesión)
    await db.commit()

    archivo_bytes = await file.read()
    background_tasks.add_task(
        ejecutar_pipeline_subida_cms,
        nuevo_banner.id, archivo_bytes, file.filename, "banner"
    )

    if mobile_file:
        mobile_bytes = await mobile_file.read()
        background_tasks.add_task(
            ejecutar_pipeline_subida_cms,
            nuevo_banner.id, mobile_bytes, mobile_file.filename, "mobile_banner"
        )

    return {
        "mensaje": "Banner registrado. Procesando imagen en segundo plano.",
        "banner_id": nuevo_banner.id
    }


@router.put("/branding/update/", status_code=status.HTTP_200_OK)
async def actualizar_colores_y_nombre(
    payload: BrandingUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(solo_administradores)
):
    """Modifica instantáneamente los códigos hexadecimales de color y nombre del sitio."""
    query = select(StoreBranding).where(StoreBranding.id == 1)
    res = await db.execute(query)
    branding = res.scalar_one_or_none()

    if not branding:
        branding = StoreBranding(id=1)

    branding.brand_name = payload.brand_name
    branding.primary_color_hex = payload.primary_color_hex
    branding.secondary_color_hex = payload.secondary_color_hex
    branding.background_color_hex = payload.background_color_hex
    branding.input_text_color_hex = payload.input_text_color_hex
    branding.navbar_color_hex = payload.navbar_color_hex
    branding.section_title_color_hex = payload.section_title_color_hex
    branding.catalog_link_color_hex = payload.catalog_link_color_hex
    branding.brand_font_family = payload.brand_font_family
    branding.brand_font_size = payload.brand_font_size
    branding.updated_at = datetime.utcnow()

    db.add(branding)
    await db.commit()
    return {"mensaje": "Identidad visual de la plataforma actualizada con éxito.", "config": branding}


# ==============================================================================
# ENDPOINTS DE COMPATIBILIDAD CON FRONTEND
# ==============================================================================
from typing import Optional
from pydantic import BaseModel

class FrontendBrandingUpdate(BaseModel):
    brand_name: Optional[str] = None
    primary_color: str
    secondary_color: str
    background_color: str
    input_text_color: Optional[str] = "#111827"
    navbar_color: Optional[str] = "#FFFFFF"
    section_title_color: Optional[str] = "#111827"
    catalog_link_color: Optional[str] = "#3B82F6"
    logo_url: Optional[str] = ""
    brand_font_family: Optional[str] = "Outfit"
    brand_font_size: Optional[str] = "1.5rem"

@router.get("/branding", status_code=status.HTTP_200_OK)
async def obtener_branding_tienda(db: AsyncSession = Depends(get_db)):
    query = select(StoreBranding).where(StoreBranding.id == 1)
    res = await db.execute(query)
    branding = res.scalar_one_or_none()
    if not branding:
        branding = StoreBranding(id=1)
        db.add(branding)
        await db.commit()
        await db.refresh(branding)
    return {
        "brand_name": branding.brand_name,
        "primary_color": branding.primary_color_hex,
        "secondary_color": branding.secondary_color_hex,
        "background_color": branding.background_color_hex,
        "input_text_color": branding.input_text_color_hex,
        "navbar_color": branding.navbar_color_hex,
        "section_title_color": branding.section_title_color_hex,
        "catalog_link_color": branding.catalog_link_color_hex,
        "logo_url": branding.logo_cloudfront_url or "",
        "brand_font_family": branding.brand_font_family,
        "brand_font_size": branding.brand_font_size
    }

@router.put("/branding", status_code=status.HTTP_200_OK)
async def actualizar_branding_tienda(
    payload: FrontendBrandingUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(solo_administradores)
):
    query = select(StoreBranding).where(StoreBranding.id == 1)
    res = await db.execute(query)
    branding = res.scalar_one_or_none()
    if not branding:
        branding = StoreBranding(id=1)
    
    if payload.brand_name is not None:
        branding.brand_name = payload.brand_name
    branding.primary_color_hex = payload.primary_color
    branding.secondary_color_hex = payload.secondary_color
    branding.background_color_hex = payload.background_color
    branding.input_text_color_hex = payload.input_text_color or "#111827"
    branding.navbar_color_hex = payload.navbar_color or "#FFFFFF"
    branding.section_title_color_hex = payload.section_title_color or "#111827"
    branding.catalog_link_color_hex = payload.catalog_link_color or "#3B82F6"
    branding.logo_cloudfront_url = payload.logo_url
    branding.brand_font_family = payload.brand_font_family or "Outfit"
    branding.brand_font_size = payload.brand_font_size or "1.5rem"
    branding.updated_at = datetime.utcnow()
    
    db.add(branding)
    await db.commit()
    return {"mensaje": "Branding actualizado", "config": branding}

@router.post("/branding/logo/", status_code=status.HTTP_200_OK)
async def subir_logo_tienda(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(solo_administradores)
):
    """
    Sube de forma síncrona el logo de la tienda, lo guarda en store_branding, lo archiva en el historial y retorna la URL.
    """
    import os
    import boto3
    from src.modules.cms.models import LogoHistory
    
    query = select(StoreBranding).where(StoreBranding.id == 1)
    res = await db.execute(query)
    branding = res.scalar_one_or_none()
    if not branding:
        branding = StoreBranding(id=1)
        db.add(branding)
        await db.flush()
        
    archivo_bytes = await file.read()
    
    s3_client = boto3.client(
        "s3",
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        region_name=os.getenv("AWS_REGION")
    )
    
    bucket_name = os.getenv("AWS_BUCKET_NAME")
    cloudfront_base = os.getenv("CLOUDFRONT_URL")
    ruta_s3 = f"cms/logos/1_{file.filename}"
    
    s3_client.put_object(
        Bucket=bucket_name,
        Key=ruta_s3,
        Body=archivo_bytes,
        ContentType=file.content_type or "image/png"
    )
    
    base_url = cloudfront_base
    if not base_url.startswith("http://") and not base_url.startswith("https://"):
        base_url = f"https://{base_url}"
    url_final = f"{base_url}/{ruta_s3}"
    
    branding.logo_cloudfront_url = url_final
    db.add(branding)
    
    # Registrar en el historial de logos
    historia = LogoHistory(logo_url=url_final, label=file.filename)
    db.add(historia)
    
    await db.commit()
    
    return {"mensaje": "Logo subido con éxito.", "logo_url": url_final}

@router.get("/logo/history", status_code=status.HTTP_200_OK)
async def obtener_historial_logos(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(solo_administradores)
):
    """Obtiene la lista de todos los logos subidos anteriormente."""
    from src.modules.cms.models import LogoHistory
    query = select(LogoHistory).order_by(LogoHistory.created_at.desc())
    res = await db.execute(query)
    return res.scalars().all()

@router.post("/logo/select/{logo_id}", status_code=status.HTTP_200_OK)
async def seleccionar_logo_historial(
    logo_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(solo_administradores)
):
    """Establece un logo del historial como el logotipo activo del marketplace."""
    from src.modules.cms.models import LogoHistory
    query_hist = select(LogoHistory).where(LogoHistory.id == logo_id)
    res_hist = await db.execute(query_hist)
    logo_reg = res_hist.scalar_one_or_none()
    if not logo_reg:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Registro de logo no encontrado.")
        
    query_brand = select(StoreBranding).where(StoreBranding.id == 1)
    res_brand = await db.execute(query_brand)
    branding = res_brand.scalar_one_or_none()
    if not branding:
        branding = StoreBranding(id=1)
        
    branding.logo_cloudfront_url = logo_reg.logo_url
    db.add(branding)
    await db.commit()
    return {"mensaje": "Logo del historial seleccionado con éxito.", "logo_url": logo_reg.logo_url}

@router.delete("/logo/{logo_id}", status_code=status.HTTP_200_OK)
async def eliminar_logo_historial(
    logo_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(solo_administradores)
):
    """Elimina un logo del historial."""
    from src.modules.cms.models import LogoHistory
    query = select(LogoHistory).where(LogoHistory.id == logo_id)
    res = await db.execute(query)
    logo_reg = res.scalar_one_or_none()
    if not logo_reg:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Registro de logo no encontrado.")
        
    await db.delete(logo_reg)
    await db.commit()
    return {"mensaje": "Logo eliminado del historial con éxito."}

def _es_host_privado(hostname: str) -> bool:
    """Resuelve el host y determina si apunta a una IP privada/reservada (anti-SSRF)."""
    import socket
    import ipaddress
    try:
        infos = socket.getaddrinfo(hostname, None)
    except Exception:
        return True  # Si no se puede resolver, lo tratamos como no confiable
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return True
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return True
    return False


@router.get("/proxy-image")
def proxy_image(url: str, admin: User = Depends(solo_administradores)):
    """
    Proxy de imágenes para el editor del CMS (evita problemas de CORS en el canvas).
    Protegido: solo admins, solo http/https y bloquea destinos internos (anti-SSRF).
    """
    import urllib.request
    from urllib.parse import urlparse
    from fastapi.responses import Response
    from fastapi import HTTPException

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="Esquema de URL no permitido.")
    if not parsed.hostname:
        raise HTTPException(status_code=400, detail="URL inválida.")
    if _es_host_privado(parsed.hostname):
        raise HTTPException(status_code=400, detail="Destino no permitido.")

    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            mime = response.info().get_content_type() or "image/png"
            # Solo devolvemos imágenes y limitamos el tamaño a 10 MB
            if not mime.startswith("image/"):
                raise HTTPException(status_code=400, detail="El recurso no es una imagen.")
            content = response.read(10 * 1024 * 1024)
            return Response(content=content, media_type=mime)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error proxying image: {str(e)}")

@router.get("/banner", status_code=status.HTTP_200_OK)
async def obtener_banner_destacado(db: AsyncSession = Depends(get_db)):
    query = select(CarouselBanner).where(CarouselBanner.is_active == True).order_by(CarouselBanner.orden)
    result = await db.execute(query)
    banner = result.scalars().first()
    
    if not banner:
        return {
            "title": "Renová tus Espacios con Piezas Únicas",
            "subtitle": "Artículos de decoración premium, nuevos y usados seleccionados por curadores.",
            "image_url": "https://images.unsplash.com/photo-1586023492125-27b2c045efd7?q=80&w=1200"
        }
        
    return {
        "title": banner.title,
        "subtitle": banner.subtitle,
        "image_url": banner.cloudfront_url
    }


class BannerOrderAndStatus(BaseModel):
    id: int
    is_active: bool

class ReorderBannersRequest(BaseModel):
    banners: List[BannerOrderAndStatus]

@router.put("/banner/reorder", status_code=status.HTTP_200_OK)
async def reordenar_banners(
    payload: ReorderBannersRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(solo_administradores)
):
    """
    Ruta protegida para actualizar el orden visual y estado de activacion de los banners del carrusel.
    """
    for index, item in enumerate(payload.banners):
        query = select(CarouselBanner).where(CarouselBanner.id == item.id)
        result = await db.execute(query)
        banner = result.scalar_one_or_none()
        if banner:
            banner.orden = index
            banner.is_active = item.is_active
            db.add(banner)
    await db.commit()
    return {"mensaje": "Orden y estado de banners actualizado con exito."}

@router.delete("/banner/{banner_id}", status_code=status.HTTP_200_OK)
async def eliminar_banner(
    banner_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(solo_administradores)
):
    """
    Ruta protegida para eliminar un banner del carrusel.
    """
    query = select(CarouselBanner).where(CarouselBanner.id == banner_id)
    result = await db.execute(query)
    banner = result.scalar_one_or_none()
    if not banner:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Banner no encontrado.")
    
    await db.delete(banner)
    await db.commit()
    return {"mensaje": "Banner eliminado con exito."}

@router.get("/admin/banners", status_code=status.HTTP_200_OK)
async def obtener_todos_los_banners(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(solo_administradores)
):
    """
    Ruta protegida para que el administrador obtenga la lista completa de banners (activos e inactivos).
    """
    query = select(CarouselBanner).order_by(CarouselBanner.orden)
    result = await db.execute(query)
    banners = result.scalars().all()
    return banners


# ==============================================================================
# SECCIONES DE LA PÁGINA DE INICIO (GESTIÓN DE CONTENIDOS DINÁMICOS)
# ==============================================================================
from src.modules.cms.models import HomepageSection
from src.modules.products.router import listar_productos
from src.config.redis import get_redis
from redis.asyncio import Redis

class HomepageSectionCreate(BaseModel):
    title: str
    category_filter: Optional[str] = None

class SectionOrderAndStatus(BaseModel):
    id: int
    is_active: bool

class ReorderSectionsRequest(BaseModel):
    sections: List[SectionOrderAndStatus]

@router.get("/sections/", status_code=status.HTTP_200_OK)
async def obtener_secciones_inicio(
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis)
):
    """Retorna las secciones activas con sus correspondientes productos asignados."""
    query = select(HomepageSection).where(HomepageSection.is_active == True).order_by(HomepageSection.orden)
    res = await db.execute(query)
    secciones = res.scalars().all()
    
    response_data = []
    for sec in secciones:
        productos = await listar_productos(category=sec.category_filter, db=db, redis=redis)
        response_data.append({
            "id": sec.id,
            "title": sec.title,
            "category_filter": sec.category_filter,
            "orden": sec.orden,
            "productos": productos
        })
    return response_data

@router.get("/admin/sections", status_code=status.HTTP_200_OK)
async def obtener_secciones_admin(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(solo_administradores)
):
    """Obtiene el listado completo de secciones de la landing page para el administrador."""
    query = select(HomepageSection).order_by(HomepageSection.orden)
    res = await db.execute(query)
    return res.scalars().all()

@router.post("/sections", status_code=status.HTTP_201_CREATED)
async def crear_seccion_inicio(
    payload: HomepageSectionCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(solo_administradores)
):
    """Crea una nueva sección para la página de inicio."""
    nueva_seccion = HomepageSection(
        title=payload.title,
        category_filter=payload.category_filter if payload.category_filter != "Todos" else None,
        orden=999
    )
    db.add(nueva_seccion)
    await db.commit()
    await db.refresh(nueva_seccion)
    return nueva_seccion

@router.put("/sections/reorder", status_code=status.HTTP_200_OK)
async def reordenar_secciones_inicio(
    payload: ReorderSectionsRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(solo_administradores)
):
    """Actualiza el orden visual y estado de visibilidad de las secciones de inicio."""
    for index, item in enumerate(payload.sections):
        query = select(HomepageSection).where(HomepageSection.id == item.id)
        result = await db.execute(query)
        sec = result.scalar_one_or_none()
        if sec:
            sec.orden = index
            sec.is_active = item.is_active
            db.add(sec)
    await db.commit()
    return {"mensaje": "Orden y estado de secciones actualizado con éxito."}

@router.delete("/sections/{section_id}", status_code=status.HTTP_200_OK)
async def eliminar_seccion_inicio(
    section_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(solo_administradores)
):
    """Elimina una sección de la landing page."""
    query = select(HomepageSection).where(HomepageSection.id == section_id)
    result = await db.execute(query)
    sec = result.scalar_one_or_none()
    if not sec:
        raise HTTPException(status_code=404, detail="Sección no encontrada.")
    await db.delete(sec)
    await db.commit()
    return {"mensaje": "Sección eliminada con éxito."}



