import os
import boto3
from fastapi import HTTPException
from dotenv import load_dotenv

load_dotenv()

class AIService:
    # Inicializamos el cliente de AWS Rekognition para moderación visual
    rekognition_client = boto3.client(
        "rekognition",
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        region_name=os.getenv("AWS_REGION", "us-east-1")
    )

    @classmethod
    async def moderar_imagen_aws(cls, archivo_bytes: bytes) -> tuple[bool, str]:
        """
        Envía la imagen a Amazon Rekognition para detectar desnudez o contenido inapropiado.
        Retorna (True, notas) si la imagen es segura, o (False, motivo) si debe ser rechazada.
        """
        try:
            # Llamada nativa al motor de moderación de imágenes de AWS
            respuesta = cls.rekognition_client.detect_moderation_labels(
                Image={'Bytes': archivo_bytes},
                MinConfidence=75.0  # Nivel de certeza mínimo para activar una alerta
            )
            
            etiquetas_detectadas = respuesta.get("ModerationLabels", [])
            
            if etiquetas_detectadas:
                detalles = [f"{lbl['Name']} ({lbl['Confidence']:.1f}%)" for lbl in etiquetas_detectadas]
                notas_bloqueo = f"Rechazado automáticamente por IA. Detectado: {', '.join(detalles)}"
                return False, notas_bloqueo
                
            return True, "Aprobado automáticamente por Amazon Rekognition."
            
        except Exception as e:
            # Si Rekognition falla, NO aprobar automáticamente en producción (fail-closed).
            # En desarrollo (DEBUG=True) o si se habilita AI_MODERATION_FAIL_OPEN, se permite
            # para no bloquear las pruebas locales sin credenciales de AWS.
            print(f"⚠️ Alerta de infraestructura de IA: {str(e)}")
            fail_open = os.getenv("DEBUG") == "True" or os.getenv("AI_MODERATION_FAIL_OPEN") == "True"
            if fail_open:
                return True, "Aprobado por omisión (moderación IA no disponible - modo desarrollo)."
            return False, "Pendiente de revisión manual: el servicio de moderación por IA no está disponible."

    @classmethod
    async def detectar_contacto_en_imagen(cls, archivo_bytes: bytes) -> tuple[bool, str]:
        """
        OCR con Amazon Rekognition (DetectText) + filtro anti-evasión.
        Retorna (True, notas) si la imagen está limpia, o (False, motivo) si
        encuentra teléfonos, redes u otras señales de compra por fuera.
        """
        from src.common.contact_filter import detectar_contacto_externo

        try:
            respuesta = cls.rekognition_client.detect_text(
                Image={"Bytes": archivo_bytes}
            )
            detecciones = respuesta.get("TextDetections", []) or []
            # Preferir líneas completas; si no hay, usar palabras
            lineas = [
                d["DetectedText"]
                for d in detecciones
                if d.get("Type") == "LINE" and d.get("DetectedText")
            ]
            if not lineas:
                lineas = [
                    d["DetectedText"]
                    for d in detecciones
                    if d.get("DetectedText")
                ]
            texto_ocr = " ".join(lineas).strip()
            if not texto_ocr:
                return True, "OCR: sin texto legible en la imagen."

            motivo = detectar_contacto_externo(texto_ocr)
            if motivo:
                return False, (
                    f"Rechazado por contacto externo en la foto. {motivo}. "
                    f"Texto leído: «{texto_ocr[:160]}»"
                )
            return True, f"OCR OK (texto revisado, sin contacto externo)."

        except Exception as e:
            print(f"⚠️ Fallo OCR anti-evasión (DetectText): {str(e)}")
            # Fail-open en OCR: no bloquear publicaciones si AWS falla;
            # el filtro de título/descripción y el chat siguen activos.
            return True, "OCR omitido (servicio no disponible)."

    @staticmethod
    async def remover_fondo_imagen(archivo_bytes: bytes) -> bytes:
        """
        Simulación empresarial de remoción de fondos (Fondo Blanco).
        En producción local, aquí consumirías la API de servicios como Remove.bg,
        AWS Bedrock o un modelo local de HuggingFace.
        """
        # Por ahora, al estar en local, devolvemos los bytes intactos simulando el pipeline de IA
        # para que la arquitectura de archivos no se detenga.
        return archivo_bytes

    @staticmethod
    async def generar_descripcion_con_vision(
        titulo: str,
        categoria: str,
        condicion: str,
        imagenes: list[tuple[bytes, str]],
    ) -> str | None:
        """
        Genera una descripción comercial ANALIZANDO LAS FOTOS del producto con
        Google Gemini (multimodal). Devuelve None si no hay API key configurada,
        no hay imágenes o el servicio falla (el caller usa el fallback de texto).

        Configuración: GEMINI_API_KEY (y opcionalmente GEMINI_MODEL) en el .env.
        """
        import base64
        import httpx

        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key or not imagenes:
            return None

        # Modelo configurado + fallback: la disponibilidad de modelos Gemini cambia
        # (p. ej. gemini-2.0-flash dejó de estar disponible en el tier gratuito).
        modelo_config = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
        modelos = [modelo_config]
        if modelo_config != "gemini-flash-latest":
            modelos.append("gemini-flash-latest")
        condicion_texto = "usado" if condicion.upper() == "USED" else "nuevo"

        prompt = (
            "Sos redactor profesional de un marketplace argentino de muebles y decoración. "
            f"Mirá las fotos de este producto {condicion_texto} de la categoría '{categoria}', "
            f"titulado '{titulo}', y escribí UNA descripción comercial atractiva.\n"
            "Reglas:\n"
            "- Entre 60 y 100 palabras, en español rioplatense neutro.\n"
            "- Describí lo que realmente se ve: materiales, colores, estilo, terminaciones y estado aparente.\n"
            "- No inventes medidas, marcas ni datos que no se vean en las fotos.\n"
            "- Sin títulos, sin listas, sin emojis, sin comillas: solo el párrafo de la descripción.\n"
            "- IMPORTANTE: la descripción debe ser un párrafo completo que termine con punto final. "
            "No dejes frases a medias."
        )

        parts: list[dict] = [{"text": prompt}]
        for contenido, mime in imagenes[:3]:  # Máximo 3 fotos para mantener la request liviana
            parts.append({
                "inline_data": {
                    "mime_type": mime or "image/jpeg",
                    "data": base64.b64encode(contenido).decode("ascii"),
                }
            })

        # maxOutputTokens alto: modelos Gemini recientes restan tokens de "thinking"
        # del presupuesto de salida y con 400 la respuesta queda truncada a mitad de frase.
        body = {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "temperature": 0.7,
                "maxOutputTokens": 2048,
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }

        for modelo in modelos:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    res = await client.post(url, json=body, headers={"x-goog-api-key": api_key})
                if res.status_code == 404:
                    # Modelo no disponible para esta cuenta: probar el siguiente
                    print(f"⚠️ Copiloto de visión: modelo '{modelo}' no disponible (404), probando fallback...")
                    continue
                # Si thinkingConfig no es soportado por el modelo, reintentar sin él
                if res.status_code == 400 and "thinking" in res.text.lower():
                    body_sin_thinking = {
                        "contents": body["contents"],
                        "generationConfig": {
                            "temperature": 0.7,
                            "maxOutputTokens": 2048,
                        },
                    }
                    async with httpx.AsyncClient(timeout=60.0) as client:
                        res = await client.post(
                            url, json=body_sin_thinking, headers={"x-goog-api-key": api_key}
                        )
                res.raise_for_status()
                data = res.json()
                candidate = data["candidates"][0]
                finish = candidate.get("finishReason", "")
                partes = candidate.get("content", {}).get("parts", [])
                texto = "".join(p.get("text", "") for p in partes).strip()
                if not texto:
                    continue
                # Si Gemini cortó por límite de tokens, descartar (evita frases a medias)
                if finish == "MAX_TOKENS" and not texto.endswith((".", "!", "?")):
                    print(f"⚠️ Copiloto de visión: respuesta truncada (MAX_TOKENS) con '{modelo}'")
                    continue
                # Limitar al máximo del campo de descripción del frontend,
                # cortando en el último punto para no dejar frases a medias.
                if len(texto) <= 500:
                    return texto
                corte = texto[:500]
                ultimo_punto = max(corte.rfind("."), corte.rfind("!"), corte.rfind("?"))
                if ultimo_punto >= 80:
                    return corte[: ultimo_punto + 1].strip()
                return corte.rstrip()
            except Exception as e:
                print(f"⚠️ Falla del copiloto de visión (Gemini, modelo '{modelo}'): {str(e)}")
        return None

    @staticmethod
    async def generar_copiloto_descripcion(titulo: str, categoria: str) -> str:
        """
        Fallback de texto: descripción genérica basada en título y categoría.
        Se usa cuando no hay fotos, no hay GEMINI_API_KEY o el servicio de visión falla.
        """
        descripcion_automatica = (
            f"Hermoso artículo de {categoria} ideal para renovar tus espacios. "
            f"Este producto de título '{titulo}' destaca por su diseño exclusivo, "
            f"aportando elegancia, calidez y un toque sofisticado único a cualquier ambiente de tu hogar."
        )
        return descripcion_automatica
