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
        import json
        import urllib.request
        import urllib.error

        if not imagenes:
            return None

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

        # 1. OPCIÓN LÍDER: OPENAI (GPT-4o-mini / GPT-4o Vision)
        openai_key = os.getenv("OPENAI_API_KEY")
        if openai_key:
            try:
                content_parts = [{"type": "text", "text": prompt}]
                for contenido, mime in imagenes[:3]:
                    b64_img = base64.b64encode(contenido).decode("ascii")
                    mime_type = mime or "image/jpeg"
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{b64_img}"}
                    })

                payload = {
                    "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                    "messages": [{"role": "user", "content": content_parts}],
                    "max_tokens": 500,
                    "temperature": 0.7
                }
                data_bytes = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    "https://api.openai.com/v1/chat/completions",
                    data=data_bytes,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {openai_key.strip()}"
                    }
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    res_json = json.loads(resp.read().decode("utf-8"))
                    text = res_json["choices"][0]["message"]["content"].strip()
                    if text:
                        print("✅ Descripción generada exitosamente por OpenAI GPT-4o Vision.")
                        return text
            except Exception as openai_err:
                print(f"⚠️ Error OpenAI Vision: {openai_err}")

        # 2. OPCIÓN 100% GRATUITA Y ULTRARRÁPIDA: GROQ (Llama 3.3 70B Versatile)
        groq_key = os.getenv("GROQ_API_KEY")
        if groq_key:
            try:
                content_parts = [{"type": "text", "text": prompt}]
                for contenido, mime in imagenes[:3]:
                    b64_img = base64.b64encode(contenido).decode("ascii")
                    mime_type = mime or "image/jpeg"
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{b64_img}"}
                    })

                payload = {
                    "model": os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
                    "messages": [{"role": "user", "content": content_parts}],
                    "max_tokens": 500,
                    "temperature": 0.7
                }
                data_bytes = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    "https://api.groq.com/openai/v1/chat/completions",
                    data=data_bytes,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {groq_key.strip()}",
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Objetia/1.0"
                    }
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    res_json = json.loads(resp.read().decode("utf-8"))
                    text = res_json["choices"][0]["message"]["content"].strip()
                    if text:
                        print("✅ Descripción generada exitosamente por Groq Llama 3.3 70B.")
                        return text
            except Exception as groq_err:
                print(f"⚠️ Error Groq Vision: {groq_err}")

        # 3. OPCIÓN ALTERNATIVA: GOOGLE GEMINI
        import httpx
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return None

        modelos = ["gemini-1.5-flash", "gemini-1.5-pro"]
        body = {
            "contents": [{"parts": [{"text": prompt}] + [{"inline_data": {"mime_type": m, "data": base64.b64encode(c).decode("ascii")}} for c, m in imagenes[:3]]}],
            "generationConfig": {"temperature": 0.7, "maxOutputTokens": 1000},
        }

        for modelo in modelos:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    res = await client.post(url, json=body, headers={"x-goog-api-key": api_key})
                if res.status_code == 404:
                    print(f"⚠️ Copiloto de visión: modelo '{modelo}' no disponible (404), probando fallback...")
                    continue
                res.raise_for_status()
                data = res.json()
                candidate = data["candidates"][0]
                finish = candidate.get("finishReason", "")
                partes = candidate.get("content", {}).get("parts", [])
                texto = "".join(p.get("text", "") for p in partes).strip()
                if not texto:
                    continue
                if finish == "MAX_TOKENS" and not texto.endswith((".", "!", "?")):
                    print(f"⚠️ Copiloto de visión: respuesta truncada (MAX_TOKENS) con '{modelo}'")
                    continue
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
        Genera una descripción comercial redactada por IA basada en título y categoría.
        """
        import json
        import urllib.request
        groq_key = os.getenv("GROQ_API_KEY")
        if groq_key:
            try:
                prompt_text = (
                    "Sos redactor profesional de un marketplace argentino de muebles y decoración. "
                    f"Escribí una descripción comercial breve (60 a 90 palabras) para un producto titulado '{titulo}' "
                    f"de la categoría '{categoria}'. En español rioplatense neutro, sin emojis, sin comillas ni listas."
                )
                payload = {
                    "model": "llama-3.3-70b-versatile",
                    "messages": [{"role": "user", "content": prompt_text}],
                    "max_tokens": 300,
                    "temperature": 0.7
                }
                data_bytes = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    "https://api.groq.com/openai/v1/chat/completions",
                    data=data_bytes,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {groq_key.strip()}",
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Objetia/1.0"
                    }
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    res_json = json.loads(resp.read().decode("utf-8"))
                    text = res_json["choices"][0]["message"]["content"].strip()
                    if text:
                        return text
            except Exception as e:
                print(f"⚠️ Error generando texto con Groq: {e}")

        return (
            f"Hermoso artículo de {categoria} ideal para renovar tus espacios. "
            f"Este producto titulado '{titulo}' destaca por su diseño exclusivo, "
            f"aportando elegancia, calidez y un toque sofisticado único a cualquier ambiente de tu hogar."
        )
