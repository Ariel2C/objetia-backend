import os
import bcrypt  # 🌟 Cambiamos passlib por el módulo nativo puro
from datetime import datetime, timedelta
from typing import Optional
from jose import jwt, JWTError
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from fastapi import HTTPException, status
from dotenv import load_dotenv

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")

class AuthService:
    
    # ==========================================================================
    # 🌟 NUEVO MOTOR CRIPTOGRÁFICO NATIVO (Sin Passlib)
    # ==========================================================================
    @staticmethod
    def hash_password(password: str) -> str:
        """Transforma una clave plana en un hash Bcrypt usando salt nativo."""
        # Bcrypt requiere trabajar con bytes, así que codificamos la clave
        password_bytes = password.encode('utf-8')
        salt = bcrypt.gensalt(rounds=12)
        hashed = bcrypt.hashpw(password_bytes, salt)
        return hashed.decode('utf-8')

    @staticmethod
    def verificar_password(plain_password: str, hashed_password: str) -> bool:
        """Compara de forma segura el texto plano contra el hash de PostgreSQL."""
        try:
            return bcrypt.checkpw(
                plain_password.encode('utf-8'), 
                hashed_password.encode('utf-8')
            )
        except Exception:
            return False

    # ==========================================================================
    # COMPONENTES DE GOOGLE Y TOKENS JWT
    # ==========================================================================
       # ==========================================================================
    # COMPONENTES DE GOOGLE Y TOKENS JWT (Versión Actualizada 2026)
    # ==========================================================================
    @staticmethod
    def verificar_google_token(token: str) -> dict:
        try:
            # Verificar la firma del token Y que fue emitido para NUESTRA app (audience).
            # Sin el audience, se aceptaría cualquier id_token válido de cualquier app de Google.
            if not GOOGLE_CLIENT_ID:
                raise RuntimeError("GOOGLE_CLIENT_ID no está configurado en el servidor.")

            idinfo = id_token.verify_oauth2_token(
                token,
                google_requests.Request(),
                GOOGLE_CLIENT_ID
            )
            return idinfo
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Token de Google inválido: {str(e)}"
            )

    @staticmethod
    def crear_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
        to_encode = data.copy()
        expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
        to_encode.update({"exp": expire})
        return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

    @staticmethod
    def verificar_token(token: str) -> dict:
        """Decodifica y valida un JWT propio. Lanza JWTError si es inválido/expirado."""
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
