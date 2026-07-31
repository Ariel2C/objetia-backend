import os
import boto3
import json
import urllib.request
import urllib.error
from fastapi import HTTPException
from dotenv import load_dotenv

load_dotenv()

class AIService:
    # Inicializamos el cliente de AWS Rekognition para moderación visual
    rekognition_client = boto3.client(
        "rekognition",
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        region_name=os.getenv("AWS_REKOGNITION_REGION", "us-east-1")
    )

    @classmethod
    async def moderar_imagen_aws(cls, archivo_bytes: bytes) -> tuple[bool, str]:
        """
        Envía la imagen a moderación visual (Amazon Rekognition con fallback a OpenAI Vision).
        """
        try:
            respuesta = cls.rekognition_client.detect_moderation_labels(
                Image={'Bytes': archivo_bytes},
                MinConfidence=75.0
            )
            
            etiquetas_detectadas = respuesta.get("ModerationLabels", [])
            if etiquetas_detectadas:
                detalles = [f"{lbl['Name']} ({lbl['Confidence']:.1f}%)" for lbl in etiquetas_detectadas]
                notas_bloqueo = f"Rechazado automáticamente por IA. Detectado: {', '.join(detalles)}"
                return False, notas_bloqueo
                
            return True, "Aprobado automáticamente por Amazon Rekognition."
            
        except Exception as e:
            print(f"[AWS Rekognition] Alerta: {str(e)}")

        # Fallback a OpenAI Vision si Rekognition falla o no tiene permisos IAM
        openai_key = os.getenv("OPENAI_API_KEY")
        if openai_key:
            try:
                import base64
                import json
                import urllib.request
                from PIL import Image
                import io

                try:
                    img = Image.open(io.BytesIO(archivo_bytes))
                    img.thumbnail((512, 512), Image.Resampling.LANCZOS)
                    if img.mode != "RGB":
                        img = img.convert("RGB")
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=75)
                    bytes_opt = buf.getvalue()
                except Exception:
                    bytes_opt = archivo_bytes

                b64_img = base64.b64encode(bytes_opt).decode("ascii")
                payload = {
                    "model": "gpt-4o-mini",
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Revisión de seguridad visual: ¿Esta foto contiene contenido inapropiado, pornografía o violencia? Responde únicamente INAPROPIADO si detectas algo grave, o SEGURO si es un mueble/producto normal."},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}", "detail": "low"}}
                        ]
                    }],
                    "max_tokens": 50
                }
                req = urllib.request.Request(
                    "https://api.openai.com/v1/chat/completions",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {openai_key.strip()}"
                    }
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    res_json = json.loads(resp.read().decode("utf-8"))
                    res_text = res_json["choices"][0]["message"]["content"].strip().upper()
                    if "INAPROPIADO" in res_text:
                        return False, "Rechazado automáticamente por OpenAI Vision: Contenido inapropiado detectado."
                    return True, "Aprobado por OpenAI Vision."
            except Exception as oai_err:
                print(f"[OpenAI Moderation] Fallo: {oai_err}")

        fail_open = os.getenv("DEBUG") == "True" or os.getenv("AI_MODERATION_FAIL_OPEN") == "True"
        if fail_open:
            return True, "Aprobado por omisión (moderación IA no disponible - modo desarrollo)."
        return False, "Pendiente de revisión manual: servicio de moderación IA no disponible."

    @classmethod
    async def detectar_contacto_en_imagen(cls, archivo_bytes: bytes) -> tuple[bool, str]:
        """
        OCR anti-evasión (Amazon Rekognition + OpenAI Vision OCR).
        """
        from src.common.contact_filter import detectar_contacto_externo
        texto_ocr = ""

        try:
            respuesta = cls.rekognition_client.detect_text(
                Image={"Bytes": archivo_bytes}
            )
            detecciones = respuesta.get("TextDetections", []) or []
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
        except Exception as e:
            print(f"[OCR Rekognition] Fallo: {str(e)}")

        # Fallback de escaneo con OpenAI Vision OCR si Rekognition no obtuvo texto o falló
        openai_key = os.getenv("OPENAI_API_KEY")
        if not texto_ocr and openai_key:
            try:
                import base64
                import json
                import urllib.request

                # Optimizar imagen para el escaneo OCR
                from PIL import Image
                import io
                try:
                    img = Image.open(io.BytesIO(archivo_bytes))
                    img.thumbnail((512, 512), Image.Resampling.LANCZOS)
                    if img.mode != "RGB":
                        img = img.convert("RGB")
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=75)
                    bytes_opt = buf.getvalue()
                except Exception:
                    bytes_opt = archivo_bytes

                b64_img = base64.b64encode(bytes_opt).decode("ascii")
                payload = {
                    "model": "gpt-4o-mini",
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Transcripción OCR estricta: Transcribí ÚNICAMENTE el texto o números visibles dentro de la imagen. Si la imagen NO tiene texto grabado o escrito, respondé exactamente: SIN TEXTO."},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}", "detail": "low"}}
                        ]
                    }],
                    "max_tokens": 150
                }
                req = urllib.request.Request(
                    "https://api.openai.com/v1/chat/completions",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {openai_key.strip()}"
                    }
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    res_json = json.loads(resp.read().decode("utf-8"))
                    res_text = res_json["choices"][0]["message"]["content"].strip()
                    if res_text and "SIN TEXTO" not in res_text.upper():
                        texto_ocr = res_text
            except Exception as ocr_err:
                print(f"[OCR OpenAI] Fallo: {ocr_err}")

        if not texto_ocr:
            return True, "OCR: sin texto legible en la imagen."

        # Prevenir falsos positivos si la IA responde negativamente que no se ve texto
        frases_negativas = ["no hay texto", "sin texto", "no se aprecia texto", "no contiene texto", "no se ven números", "no hay datos", "no hay información"]
        if any(f_neg in texto_ocr.lower() for f_neg in frases_negativas):
            return True, "OCR OK (sin texto en la imagen)."

        motivo = detectar_contacto_externo(texto_ocr)
        if motivo:
            return False, (
                f"Rechazado por contacto externo en la foto. {motivo}. "
                f"Texto leído: «{texto_ocr[:160]}»"
            )

        return True, "OCR OK (texto revisado, sin contacto externo)."

    @staticmethod
    async def remover_fondo_imagen(archivo_bytes: bytes) -> bytes:
        """
        Simulación de remoción de fondo (Fondo Blanco).
        """
        return archivo_bytes

    @staticmethod
    async def generar_descripcion_con_vision(
        titulo: str,
        categoria: str,
        condicion: str,
        imagenes: list[tuple[bytes, str]],
    ) -> str | None:
        """
        Genera una descripción comercial redactada por IA (OpenAI / Groq).
        """
        import base64

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
            "- IMPORTANTE: la descripción debe ser un párrafo completo que termine con punto final."
        )

        # 1. OPCIÓN LÍDER: OPENAI (GPT-4o-mini Vision)
        openai_key = os.getenv("OPENAI_API_KEY")
        if openai_key:
            try:
                content_parts: list[dict] = [{"type": "text", "text": prompt}]
                if imagenes:
                    from PIL import Image
                    import io
                    for contenido, mime in imagenes[:1]:
                        try:
                            img = Image.open(io.BytesIO(contenido))
                            img.thumbnail((512, 512), Image.Resampling.LANCZOS)
                            if img.mode != "RGB":
                                img = img.convert("RGB")
                            buf = io.BytesIO()
                            img.save(buf, format="JPEG", quality=75, optimize=True)
                            contenido_optim = buf.getvalue()
                            mime_type = "image/jpeg"
                        except Exception:
                            contenido_optim = contenido
                            mime_type = mime or "image/jpeg"

                        b64_img = base64.b64encode(contenido_optim).decode("ascii")
                        content_parts.append({
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime_type};base64,{b64_img}",
                                "detail": "low"
                            }
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
                        print("[OpenAI] Descripción generada exitosamente.")
                        return text
            except Exception as openai_err:
                print(f"[OpenAI] Error: {openai_err}")

        # 2. OPCIÓN GRATUITA Y ULTRARRÁPIDA: GROQ (Llama 3.3 70B)
        groq_key = os.getenv("GROQ_API_KEY")
        if groq_key:
            try:
                payload = {
                    "model": os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
                    "messages": [{"role": "user", "content": prompt}],
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
                        print("[Groq] Descripción generada exitosamente.")
                        return text
            except Exception as groq_err:
                print(f"[Groq] Error: {groq_err}")

        return None

    @staticmethod
    async def generar_copiloto_descripcion(titulo: str, categoria: str) -> str:
        """
        Genera una descripción comercial redactada por IA basada en título y categoría.
        """
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
                print(f"[Groq] Error generando texto: {e}")

        return (
            f"Hermoso artículo de {categoria} ideal para renovar tus espacios. "
            f"Este producto titulado '{titulo}' destaca por su diseño exclusivo, "
            f"aportando elegancia, calidez y un toque sofisticado único a cualquier ambiente de tu hogar."
        )

    @staticmethod
    async def analizar_foto_principal_ia(
        archivo_bytes: bytes,
        mime_type: str = "image/jpeg"
    ) -> dict | None:
        """
        Escanea la foto de portada del producto con OpenAI GPT-4o-mini Vision y extrae
        un objeto JSON estructurado con título, categoría, descripción, tags, peso y medidas.
        """
        openai_key = os.getenv("OPENAI_API_KEY")
        if not openai_key:
            return None

        import base64
        import json
        import urllib.request
        from PIL import Image
        import io

        try:
            img = Image.open(io.BytesIO(archivo_bytes))
            img.thumbnail((512, 512), Image.Resampling.LANCZOS)
            if img.mode != "RGB":
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=75, optimize=True)
            bytes_opt = buf.getvalue()
        except Exception:
            bytes_opt = archivo_bytes

        b64_img = base64.b64encode(bytes_opt).decode("ascii")

        prompt = (
            "Sos el motor experto en e-commerce de un marketplace argentino de muebles y decoración.\n"
            "Analizá la foto de este producto y respondé ÚNICAMENTE en formato JSON estricto sin markdown ni bloques ```json.\n"
            "Ejemplo de formato requerido:\n"
            "{\n"
            '  "title": "Juego de 2 Veladores de Noche en Madera Maciza",\n'
            '  "category": "Iluminación",\n'
            '  "description": "Hermoso par de veladores para mesa de noche fabricados en madera maciza de primera calidad...",\n'
            '  "tags": "velador, veladores, par, madera, noche, dormitorio, iluminación, luz",\n'
            '  "weight_kg": 3.5,\n'
            '  "height_cm": 45,\n'
            '  "width_cm": 25,\n'
            '  "length_cm": 25\n'
            "}\n\n"
            "Reglas:\n"
            "- Observá con atención el objeto real: materiales, colores, cantidad de piezas (si se ven 2 veladores, poné en el título 'Juego de 2 Veladores...'), estilo y terminaciones.\n"
            "- La categoría DEBE ser exactamente una de estas: Iluminación, Sillones, Mesas, Sillas, Placards y Armarios, Camas y Respaldos, Estanterías, Espejos, Vajilleros y Racks, Jardín y Exterior, Adornos y Cuadros.\n"
            "- Estimá peso y dimensiones aproximadas de embalaje lógicas para este tipo de objeto.\n"
            "- No agregues comillas extras ni explicaciones fuera del objeto JSON."
        )

        try:
            payload = {
                "model": "gpt-4o-mini",
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}", "detail": "low"}}
                    ]
                }],
                "max_tokens": 600,
                "temperature": 0.5
            }
            req = urllib.request.Request(
                "https://api.openai.com/v1/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {openai_key.strip()}"
                }
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                res_json = json.loads(resp.read().decode("utf-8"))
                raw_text = res_json["choices"][0]["message"]["content"].strip()
                raw_text = raw_text.replace("```json", "").replace("```", "").strip()
                parsed = json.loads(raw_text)
                return parsed
        except Exception as err:
            print(f"[Analisis Foto Principal OpenAI] Error: {err}")
            return None
