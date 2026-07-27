import os
import mimetypes
import boto3
from sqlmodel import select

from src.config.database import AsyncSessionLocal
from src.modules.cms.models import CarouselBanner, StoreBranding


async def ejecutar_pipeline_subida_cms(
    id_entidad: int, 
    archivo_bytes: bytes, 
    nombre_archivo: str, 
    tipo_cms: str  # "banner", "mobile_banner" o "logo"
):
    """
    Sube de forma asíncrona los recursos gráficos del CMS a Amazon S3
    y actualiza la URL de CloudFront en PostgreSQL local.

    Abre su PROPIA sesión de base de datos (las background tasks corren después
    de que la sesión de la petición ya fue cerrada). Si S3 falla, deja el registro
    con una URL vacía en vez de dejarlo permanentemente en "procesando...".
    """
    s3_client = boto3.client(
        "s3",
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        region_name=os.getenv("AWS_REGION")
    )
    
    bucket_name = os.getenv("AWS_BUCKET_NAME")
    cloudfront_base = os.getenv("CLOUDFRONT_URL") or ""
    ruta_s3 = f"cms/{tipo_cms}s/{id_entidad}_{nombre_archivo}"

    url_final = ""
    subida_ok = False
    try:
        content_type, _ = mimetypes.guess_type(nombre_archivo)
        if not content_type or not content_type.startswith("image/"):
            content_type = "image/jpeg"
        s3_client.put_object(
            Bucket=bucket_name,
            Key=ruta_s3,
            Body=archivo_bytes,
            ContentType=content_type
        )
        base_url = cloudfront_base
        if not base_url.startswith("http://") and not base_url.startswith("https://"):
            base_url = f"https://{base_url}"
        url_final = f"{base_url}/{ruta_s3}"
        subida_ok = True
    except Exception as e:
        print(f"❌ Fallo de infraestructura al subir elemento CMS: {str(e)}")

    # Persistir el resultado (URL final si subió; cadena vacía si falló, nunca "procesando..." permanente)
    async with AsyncSessionLocal() as db:
        try:
            if tipo_cms == "banner":
                res = await db.execute(select(CarouselBanner).where(CarouselBanner.id == id_entidad))
                banner = res.scalar_one_or_none()
                if banner:
                    banner.cloudfront_url = url_final
                    banner.is_active = subida_ok  # No mostrar un banner sin imagen válida
                    db.add(banner)

            elif tipo_cms == "mobile_banner":
                res = await db.execute(select(CarouselBanner).where(CarouselBanner.id == id_entidad))
                banner = res.scalar_one_or_none()
                if banner:
                    banner.mobile_cloudfront_url = url_final or None
                    db.add(banner)

            elif tipo_cms == "logo":
                res = await db.execute(select(StoreBranding).where(StoreBranding.id == id_entidad))
                branding = res.scalar_one_or_none()
                if branding and url_final:
                    branding.logo_cloudfront_url = url_final
                    db.add(branding)

            await db.commit()
            if subida_ok:
                print(f"✅ Recurso CMS '{tipo_cms}' subido y guardado de forma exitosa.")
        except Exception as e:
            print(f"❌ Error al persistir recurso CMS: {str(e)}")
            await db.rollback()
