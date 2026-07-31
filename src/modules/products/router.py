from fastapi import APIRouter, Depends, UploadFile, File, Form, BackgroundTasks, status, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional

from src.config.database import get_db
from src.modules.products.models import Product, ProductCondition, ModerationStatus
from src.modules.ai_processor.pipeline import ejecutar_pipeline_ia_multi_imagenes
from src.modules.ai_processor.services import AIService
from src.common.contact_filter import (
    detectar_contacto_externo,
    MENSAJE_RECHAZO_PUBLICACION,
)

# Middleware de seguridad JWT para proteger el endpoint
from src.common.dependencies import get_current_user, get_optional_current_user
from src.modules.users.models import User

router = APIRouter(prefix="/products", tags=["Catálogo de Productos"])

@router.post("/create/", status_code=status.HTTP_202_ACCEPTED)
async def publicar_producto_con_multi_imagenes(
    background_tasks: BackgroundTasks,
    title: str = Form(...),
    price: float = Form(...),
    category: str = Form(...),
    condition: ProductCondition = Form(...),
    description: str = Form(None),
    stock: int = Form(1),
    weight_kg: float = Form(10.0),
    height_cm: float = Form(50.0),
    width_cm: float = Form(50.0),
    length_cm: float = Form(50.0),
    files: List[UploadFile] = File(...), # 🌟 CARGA MÚLTIPLE DE ARCHIVOS MULTIMEDIA
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Endpoint de grado empresarial. Recibe un lote de imágenes para el producto,
    valida restricciones de tamaño corporativas, responde de inmediato y
    delega la optimización multimedia y moderación por IA al hilo secundario.
    """
    # Si viene con strings vacías o nulos en las dimensiones, proveer valores por defecto
    stock = stock if stock is not None else 1
    weight_kg = weight_kg if weight_kg is not None else 10.0
    height_cm = height_cm if height_cm is not None else 50.0
    width_cm = width_cm if width_cm is not None else 50.0
    length_cm = length_cm if length_cm is not None else 50.0

    # Anti-evasión: teléfonos / redes / “comprá por fuera” en título y descripción
    for campo, valor in (("título", title), ("descripción", description or "")):
        motivo = detectar_contacto_externo(valor)
        if motivo:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{MENSAJE_RECHAZO_PUBLICACION} ({motivo} en el {campo}.)",
            )

    # Validación Senior: Evitar ataques de denegación de servicio (DoS) por archivos masivos
    if len(files) > 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="La plataforma permite un máximo de 8 imágenes por artículo de decoración."
        )

    # 1. Guardar la entidad del producto en estado PENDING
    nuevo_producto = Product(
        title=title,
        price=price,
        category=category,
        condition=condition,
        description=description,
        seller_id=current_user.id,
        moderation_status=ModerationStatus.PENDING,
        stock=stock,
        weight_kg=weight_kg,
        height_cm=height_cm,
        width_cm=width_cm,
        length_cm=length_cm
    )
    db.add(nuevo_producto)
    await db.flush() # Sincroniza para extraer el ID único generado por PostgreSQL local

    # 2. Leer el lote de imágenes en bloques de memoria temporal
    MAX_IMG_BYTES = 8 * 1024 * 1024  # 8 MB por imagen
    imagenes_en_memoria = []
    for file in files:
        if not file.content_type or not file.content_type.startswith("image/"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, 
                detail=f"El archivo '{file.filename}' no es una imagen válida."
            )
        bytes_contenido = await file.read()
        if len(bytes_contenido) > MAX_IMG_BYTES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"La imagen '{file.filename}' supera el máximo de 8 MB permitido."
            )
        imagenes_en_memoria.append((file.filename, bytes_contenido))

    # 3. Encolar el pipeline de procesamiento asíncrono (abre su propia sesión de DB)
    background_tasks.add_task(
        ejecutar_pipeline_ia_multi_imagenes,
        nuevo_producto.id,
        imagenes_en_memoria
    )

    return {
        "mensaje": f"Publicación creada con éxito. Se han recibido {len(files)} imágenes. "
                   f"Nuestros motores de Inteligencia Artificial están procesando las fotografías, "
                   f"removiendo fondos y analizando el contenido en segundo plano.",
        "product_id": nuevo_producto.id,
        "status": "processing_multimedia"
    }


@router.post("/copilot", status_code=status.HTTP_200_OK)
async def generar_descripcion_copiloto(
    title: str = Form(...),
    category: str = Form(...),
    condition: str = Form("used"),
    files: List[UploadFile] = File(default=[]),
    current_user: Optional[User] = Depends(get_optional_current_user)
):
    """
    Redacta una descripción comercial para el producto.
    Si se adjuntan fotos y hay GEMINI_API_KEY configurada, la IA analiza las
    imágenes reales (materiales, colores, estilo, estado). Si no, cae al
    generador de texto básico por título y categoría.
    """
    try:
        MAX_IMG_BYTES = 8 * 1024 * 1024
        imagenes: List[tuple[bytes, str]] = []
        for file in (files or [])[:3]:
            if not file.content_type or not file.content_type.startswith("image/"):
                continue
            contenido = await file.read()
            if 0 < len(contenido) <= MAX_IMG_BYTES:
                imagenes.append((contenido, file.content_type))

        descripcion = None
        try:
            descripcion = await AIService.generar_descripcion_con_vision(
                titulo=title, categoria=category, condicion=condition, imagenes=imagenes
            )
        except Exception as vision_err:
            print(f"⚠️ Error al invocar visión Gemini: {vision_err}")
            descripcion = None

        generada_con_fotos = descripcion is not None

        if not descripcion:
            try:
                descripcion = await AIService.generar_copiloto_descripcion(title, category)
            except Exception as text_err:
                print(f"⚠️ Error al invocar generador de texto: {text_err}")
                descripcion = f"Hermoso artículo de {category} titulado '{title}', ideal para renovar tu hogar con elegancia y calidez."

        return {"description": descripcion, "vision": generada_con_fotos}
    except Exception as general_err:
        print(f"⚠️ Excepción general en copiloto de IA: {str(general_err)}")
        return {
            "description": f"Hermoso artículo de {category} titulado '{title}', perfecto para personalizar y destacar tus ambientes.",
            "vision": False
        }


@router.post("/analyze-primary-photo", status_code=status.HTTP_200_OK)
async def analizar_foto_principal_endpoint(
    file: UploadFile = File(...),
    current_user: Optional[User] = Depends(get_optional_current_user)
):
    """
    Escanea la foto principal del producto e infiere título, categoría,
    descripción, tags, peso y dimensiones.
    """
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="El archivo debe ser una imagen válida.")
    
    contenido = await file.read()
    if len(contenido) > 8 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="La foto no debe superar los 8 MB.")

    # 1. OCR / moderación inicial rápida
    ocr_ok, notas_ocr = await AIService.detectar_contacto_en_imagen(contenido)
    if not ocr_ok:
        raise HTTPException(
            status_code=400,
            detail=f"La foto principal contiene datos de contacto no permitidos. Detalle: {notas_ocr}"
        )

    # 2. Análisis con OpenAI Vision
    datos = await AIService.analizar_foto_principal_ia(contenido, file.content_type)
    if not datos:
        raise HTTPException(status_code=500, detail="No se pudo completar el análisis visual de la foto.")

    return datos


@router.post("/check-photo-ocr", status_code=status.HTTP_200_OK)
async def verificar_ocr_foto_individual(
    file: UploadFile = File(...),
    current_user: Optional[User] = Depends(get_optional_current_user)
):
    """
    Verifica una foto secundaria en tiempo real para detectar números de teléfono,
    redes sociales o datos de contacto externos.
    """
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Archivo de imagen no válido.")

    contenido = await file.read()
    es_segura, notas_ia = await AIService.moderar_imagen_aws(contenido)
    if not es_segura:
        return {"ok": False, "reason": f"Foto no permitida: {notas_ia}"}

    ocr_ok, notas_ocr = await AIService.detectar_contacto_en_imagen(contenido)
    if not ocr_ok:
        return {"ok": False, "reason": f"Contacto externo detectado: {notas_ocr}"}

    return {"ok": True, "reason": "Foto aprobada y limpia."}


# ==============================================================================
# ENDPOINTS ADICIONALES PARA COMPATIBILIDAD CON FRONTEND
# ==============================================================================
from sqlmodel import select
from sqlalchemy.orm import selectinload
from src.modules.products.models import ProductImage, Favorite
from src.config.redis import get_redis
from redis.asyncio import Redis

async def get_optional_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> Optional[User]:
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return None
    token = auth_header.split(" ")[1]
    try:
        from src.modules.auth.services import AuthService
        payload = AuthService.verificar_token(token)
        user_id = int(payload.get("sub"))
        query = select(User).where(User.id == user_id)
        res = await db.execute(query)
        return res.scalar_one_or_none()
    except Exception:
        return None

@router.get("/", status_code=status.HTTP_200_OK)
async def listar_productos(
    category: Optional[str] = None,
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    current_user: Optional[User] = Depends(get_optional_current_user)
):
    query = select(Product, User).join(User, Product.seller_id == User.id).where(
        Product.moderation_status == ModerationStatus.APPROVED
    ).where(
        Product.stock > 0
    ).options(selectinload(Product.images))
    
    if current_user and isinstance(current_user, User):
        query = query.where(Product.seller_id != current_user.id)
    
    if category and category != "Todos":
        query = query.where(Product.category == category)
        
    if search:
        from sqlalchemy import or_
        search_term = f"%{search.strip()}%"
        query = query.where(
            or_(
                Product.title.ilike(search_term),
                Product.description.ilike(search_term),
                Product.category.ilike(search_term)
            )
        )
        
    result = await db.execute(query)
    rows = result.all()
    
    from datetime import datetime
    serialized = []
    for p, u in rows:
        img_url = ""
        for img in p.images:
            if img.is_primary:
                img_url = img.cloudfront_url
                break
        if not img_url and p.images:
            img_url = p.images[0].cloudfront_url
            
        # Comprobar si está reservado en Redis
        lock_owner = await redis.get(f"product_lock:{p.id}")
        
        status_stock = "AVAILABLE"
        if lock_owner:
            status_stock = "RESERVED"
        elif p.stock < 1:
            status_stock = "SOLD"
            
        es_reciente = (datetime.utcnow() - p.created_at).days < 7
            
        serialized.append({
            "id": p.id,
            "title": p.title,
            "category": p.category,
            "price": p.price,
            "condition": p.condition,
            "image_url": img_url,
            "status": status_stock,
            "seller_name": u.full_name,
            "is_new": es_reciente
        })
    return serialized

@router.get("/featured", status_code=status.HTTP_200_OK)
async def listar_productos_destacados(
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    current_user: Optional[User] = Depends(get_optional_current_user)
):
    # Se obtienen los productos aprobados
    return await listar_productos(db=db, redis=redis, current_user=current_user)

@router.get("/favorites/ids", status_code=status.HTTP_200_OK)
async def listar_ids_favoritos(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Devuelve solo los IDs de productos favoritos del usuario (para marcar corazones en el frontend)."""
    query = select(Favorite.product_id).where(Favorite.user_id == current_user.id)
    result = await db.execute(query)
    return [row[0] for row in result.all()]

@router.get("/favorites", status_code=status.HTTP_200_OK)
async def listar_favoritos_usuario(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    query = select(Product).join(Favorite).where(Favorite.user_id == current_user.id).options(selectinload(Product.images))
    result = await db.execute(query)
    productos = result.scalars().all()
    
    serialized = []
    for p in productos:
        img_url = ""
        for img in p.images:
            if img.is_primary:
                img_url = img.cloudfront_url
                break
        if not img_url and p.images:
            img_url = p.images[0].cloudfront_url
            
        serialized.append({
            "id": p.id,
            "title": p.title,
            "category": p.category,
            "price": p.price,
            "condition": p.condition,
            "image_url": img_url,
            "status": "AVAILABLE" if p.stock > 0 else "SOLD"
        })
    return serialized

@router.get("/{product_id}", status_code=status.HTTP_200_OK)
async def obtener_detalle_producto(
    product_id: int,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis)
):
    query = select(Product, User).join(User, Product.seller_id == User.id).where(Product.id == product_id).options(selectinload(Product.images))
    result = await db.execute(query)
    row = result.first()
    
    if not row:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
        
    p, u = row
    
    # Comprobar si está reservado
    lock_owner = await redis.get(f"product_lock:{p.id}")
    status_stock = "AVAILABLE"
    if lock_owner:
        status_stock = "RESERVED"
    elif p.stock < 1:
        status_stock = "SOLD"
        
    img_urls = [img.cloudfront_url for img in p.images]
    main_image = ""
    for img in p.images:
        if img.is_primary:
            main_image = img.cloudfront_url
            break
    if not main_image and p.images:
        main_image = p.images[0].cloudfront_url
        
    from datetime import datetime
    es_reciente = (datetime.utcnow() - p.created_at).days < 7
        
    return {
        "id": p.id,
        "title": p.title,
        "description": p.description,
        "price": p.price,
        "condition": p.condition,
        "category": p.category,
        "seller_id": p.seller_id,
        "seller_name": u.full_name,
        "image_url": main_image,
        "images": img_urls,
        "status": status_stock,
        "created_at": p.created_at,
        "is_new": es_reciente
    }

@router.post("/{product_id}/favorite", status_code=status.HTTP_200_OK)
async def toggle_favorito(
    product_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Validar que no sea su propio producto
    prod_query = select(Product).where(Product.id == product_id)
    prod_res = await db.execute(prod_query)
    product = prod_res.scalar_one_or_none()
    if product and product.seller_id == current_user.id:
        raise HTTPException(status_code=400, detail="No podés agregar tu propia publicación a tus favoritos.")

    query = select(Favorite).where(
        Favorite.user_id == current_user.id,
        Favorite.product_id == product_id
    )
    result = await db.execute(query)
    fav = result.scalar_one_or_none()
    
    if fav:
        await db.delete(fav)
        await db.commit()
        return {"favorito": False, "mensaje": "Eliminado de favoritos"}
    else:
        new_fav = Favorite(user_id=current_user.id, product_id=product_id)
        db.add(new_fav)
        await db.commit()
        return {"favorito": True, "mensaje": "Agregado a favoritos"}

@router.get("/my-publications/", status_code=status.HTTP_200_OK)
async def obtener_publicaciones_propias(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Retorna la lista de productos publicados por el usuario actual.
    """
    try:
        query = select(Product).where(Product.seller_id == current_user.id).options(selectinload(Product.images)).order_by(Product.created_at.desc())
        result = await db.execute(query)
        productos = result.scalars().all()
        
        serialized = []
        for p in productos:
            img_url = ""
            for img in p.images:
                if img.is_primary:
                    img_url = img.cloudfront_url
                    break
            if not img_url and p.images:
                img_url = p.images[0].cloudfront_url
                
            serialized.append({
                "id": p.id,
                "title": p.title,
                "price": p.price,
                "category": p.category,
                "condition": p.condition,
                "moderation_status": p.moderation_status.value,
                "ai_moderation_notes": p.ai_moderation_notes if p.moderation_status == ModerationStatus.REJECTED else None,
                "stock": p.stock,
                "image_url": img_url,
                "updated_at": p.updated_at.isoformat() if p.updated_at else p.created_at.isoformat()
            })
        return serialized
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al obtener publicaciones: {str(e)}")

@router.delete("/{product_id}/", status_code=status.HTTP_200_OK)
async def eliminar_producto(
    product_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Elimina un producto propio de forma permanente.
    """
    try:
        query = select(Product).where(Product.id == product_id, Product.seller_id == current_user.id)
        result = await db.execute(query)
        p = result.scalar_one_or_none()
        if not p:
            raise HTTPException(status_code=404, detail="Producto no encontrado o no tienes permisos para eliminarlo.")
        await db.delete(p)
        await db.commit()
        return {"mensaje": "Producto eliminado con éxito"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al eliminar producto: {str(e)}")

class ProductUpdateRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = None
    category: Optional[str] = None
    condition: Optional[str] = None
    stock: Optional[int] = None
    weight_kg: Optional[float] = None
    height_cm: Optional[float] = None
    width_cm: Optional[float] = None
    length_cm: Optional[float] = None

@router.put("/{product_id}/", status_code=status.HTTP_200_OK)
async def actualizar_producto(
    product_id: int,
    payload: ProductUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Actualiza la información de un producto propio.
    """
    try:
        query = select(Product).where(Product.id == product_id, Product.seller_id == current_user.id)
        result = await db.execute(query)
        p = result.scalar_one_or_none()
        if not p:
            raise HTTPException(status_code=404, detail="Producto no encontrado o no tienes permisos para editarlo.")
            
        if payload.title is not None:
            motivo = detectar_contacto_externo(payload.title)
            if motivo:
                raise HTTPException(
                    status_code=400,
                    detail=f"{MENSAJE_RECHAZO_PUBLICACION} ({motivo} en el título.)",
                )
            p.title = payload.title
        if payload.description is not None:
            motivo = detectar_contacto_externo(payload.description)
            if motivo:
                raise HTTPException(
                    status_code=400,
                    detail=f"{MENSAJE_RECHAZO_PUBLICACION} ({motivo} en la descripción.)",
                )
            p.description = payload.description
        if payload.price is not None:
            p.price = payload.price
        if payload.category is not None:
            p.category = payload.category
        if payload.condition is not None:
            try:
                p.condition = ProductCondition(payload.condition.lower().strip())
            except ValueError:
                raise HTTPException(status_code=400, detail="Condición inválida. Debe ser 'new' o 'used'.")
        if payload.stock is not None:
            if payload.stock < 0:
                raise HTTPException(status_code=400, detail="El stock no puede ser negativo.")
            p.stock = payload.stock
        if payload.weight_kg is not None:
            p.weight_kg = payload.weight_kg
        if payload.height_cm is not None:
            p.height_cm = payload.height_cm
        if payload.width_cm is not None:
            p.width_cm = payload.width_cm
        if payload.length_cm is not None:
            p.length_cm = payload.length_cm
            
        from datetime import datetime
        p.updated_at = datetime.utcnow()
        db.add(p)
        await db.commit()
        await db.refresh(p)
        
        return {"mensaje": "Producto actualizado con éxito", "product_id": p.id}
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Error al actualizar producto: {str(e)}")


# ==============================================================================
# ENDPOINTS DE MODERACIÓN ADMINISTRATIVA (ADMIN ROLE ONLY)
# ==============================================================================
from src.modules.users.models import UserRole

class ModerationActionRequest(BaseModel):
    action: str  # "approve" | "reject"

@router.get("/admin/moderation/", status_code=status.HTTP_200_OK)
async def listar_productos_moderacion_admin(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Requiere permisos de Administrador.")
    
    query = select(Product, User).join(User, Product.seller_id == User.id).options(selectinload(Product.images)).order_by(Product.created_at.desc())
    result = await db.execute(query)
    filas = result.all()
    
    serialized = []
    for p, u in filas:
        img_url = ""
        for img in p.images:
            if img.is_primary:
                img_url = img.cloudfront_url
                break
        if not img_url and p.images:
            img_url = p.images[0].cloudfront_url
            
        serialized.append({
            "id": p.id,
            "title": p.title,
            "price": p.price,
            "category": p.category,
            "condition": p.condition.value if hasattr(p.condition, 'value') else str(p.condition),
            "moderation_status": p.moderation_status.value if hasattr(p.moderation_status, 'value') else str(p.moderation_status),
            "ai_moderation_notes": p.ai_moderation_notes,
            "seller_id": u.id,
            "seller_name": u.full_name,
            "seller_email": u.email,
            "image_url": img_url,
            "created_at": p.created_at.isoformat() if hasattr(p, 'created_at') and p.created_at else None
        })
    return serialized


@router.post("/admin/moderation/{product_id}/action", status_code=status.HTTP_200_OK)
async def accion_moderacion_admin(
    product_id: int,
    payload: ModerationActionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Requiere permisos de Administrador.")
        
    query = select(Product).where(Product.id == product_id)
    result = await db.execute(query)
    p = result.scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Producto no encontrado.")
        
    if payload.action == "approve":
        p.moderation_status = ModerationStatus.APPROVED
        p.ai_moderation_notes = "Aprobado manualmente por Administrador."
    elif payload.action == "reject":
        p.moderation_status = ModerationStatus.REJECTED
    else:
        raise HTTPException(status_code=400, detail="Acción no válida. Usar 'approve' o 'reject'.")
        
    db.add(p)
    await db.commit()
    return {"mensaje": f"Producto actualizado a estado {p.moderation_status.value} exitosamente."}

