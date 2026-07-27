import os
import boto3
from sqlmodel import select
from typing import List, Tuple

from src.config.database import AsyncSessionLocal
from src.modules.products.models import Product, ProductImage, ModerationStatus
from src.modules.ai_processor.services import AIService


async def ejecutar_pipeline_ia_multi_imagenes(
    product_id: int,
    imagenes_data: List[Tuple[str, bytes]],  # Lista de tuples: [(nombre_archivo, bytes)]
):
    """
    Tubería de procesamiento asíncrono avanzado (Background Task).
    Modera, remueve fondos y sube todas las imágenes a S3 en segundo plano,
    asignando la primera foto como la imagen principal del catálogo.

    Abre su PROPIA sesión de base de datos: las background tasks se ejecutan
    después de que la sesión de la petición ya fue cerrada.
    """
    async with AsyncSessionLocal() as db:
        query = select(Product).where(Product.id == product_id)
        result = await db.execute(query)
        product = result.scalar_one_or_none()

        if not product:
            return

        s3_client = boto3.client(
            "s3",
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=os.getenv("AWS_REGION")
        )

        bucket_name = os.getenv("AWS_BUCKET_NAME")
        cloudfront_url = os.getenv("CLOUDFRONT_URL")

        # Marcador para definir la foto de portada
        es_primaria = True
        hubo_error_critico = False
        notas_totales = []

        for index, (nombre_archivo, archivo_bytes) in enumerate(imagenes_data):
            # 1. Moderación con Amazon Rekognition para cada foto independiente
            es_segura, notas_ia = await AIService.moderar_imagen_aws(archivo_bytes)
            notas_totales.append(f"Foto {index + 1}: {notas_ia}")

            if not es_segura:
                product.moderation_status = ModerationStatus.REJECTED
                product.ai_moderation_notes = f"Rechazado por fallo en Foto {index + 1}. Detalle: {notas_ia}"
                await db.commit()
                return  # Detiene el bucle por completo si se detecta contenido inapropiado

            # 1b. OCR anti-evasión: teléfonos / redes / “comprá por fuera” escritos en la foto
            ocr_ok, notas_ocr = await AIService.detectar_contacto_en_imagen(archivo_bytes)
            notas_totales.append(f"Foto {index + 1} OCR: {notas_ocr}")
            if not ocr_ok:
                product.moderation_status = ModerationStatus.REJECTED
                product.ai_moderation_notes = (
                    f"Rechazado por contacto externo en Foto {index + 1}. {notas_ocr}"
                )
                await db.commit()
                return

            try:
                # 2. Quitar fondo mediante nuestro procesador de IA
                imagen_limpia_bytes = await AIService.remover_fondo_imagen(archivo_bytes)

                # 3. Subir el binario procesado a AWS S3 respetando el tipo real del archivo
                content_type = _content_type_desde_nombre(nombre_archivo)
                ruta_s3 = f"productos/{product_id}/img_{index}_{nombre_archivo}"
                s3_client.put_object(
                    Bucket=bucket_name,
                    Key=ruta_s3,
                    Body=imagen_limpia_bytes,
                    ContentType=content_type
                )

                # 4. Registrar la URL de la CDN de CloudFront en PostgreSQL local
                base_url = cloudfront_url or ""
                if not base_url.startswith("http://") and not base_url.startswith("https://"):
                    base_url = f"https://{base_url}"

                nueva_imagen = ProductImage(
                    cloudfront_url=f"{base_url}/{ruta_s3}",
                    is_primary=es_primaria,
                    product_id=product_id
                )
                db.add(nueva_imagen)

                # Después de la primera iteración, las siguientes fotos ya no serán de portada
                es_primaria = False

            except Exception as e:
                print(f"❌ Error al procesar imagen {nombre_archivo}: {str(e)}")
                hubo_error_critico = True

        # 5. Copiloto de Redacción (si aplica) y Aprobación Final
        if not hubo_error_critico:
            if not product.description or product.description.strip() == "":
                product.description = await AIService.generar_copiloto_descripcion(product.title, product.category)
            product.moderation_status = ModerationStatus.APPROVED
        else:
            product.moderation_status = ModerationStatus.REJECTED

        product.ai_moderation_notes = " | ".join(notas_totales)
        await db.commit()


def _content_type_desde_nombre(nombre_archivo: str) -> str:
    """Deriva el Content-Type real a partir de la extensión (no forzar siempre JPEG)."""
    import mimetypes
    mime, _ = mimetypes.guess_type(nombre_archivo)
    if mime and mime.startswith("image/"):
        return mime
    return "image/jpeg"
