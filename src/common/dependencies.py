import os
from typing import Optional
from datetime import datetime
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession
from dotenv import load_dotenv

from src.config.database import get_db
from src.modules.users.models import User, UserRole

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = os.getenv("ALGORITHM", "HS256")

# 🌟 Configura FastAPI para buscar automáticamente el token en la cabecera "Authorization"
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/google")
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="/auth/google", auto_error=False)

async def get_optional_current_user(
    token: Optional[str] = Depends(oauth2_scheme_optional),
    db: AsyncSession = Depends(get_db)
) -> Optional[User]:
    """
    Middleware opcional de autenticación.
    Si viene un token válido, resuelve el usuario. Si no viene token o es inválido, devuelve None.
    """
    if not token:
        return None
    return await get_user_from_token(token, db)

async def get_current_user(
    token: str = Depends(oauth2_scheme), 
    db: AsyncSession = Depends(get_db)
) -> User:
    """
    Middleware Central de Autenticación.
    Valida el JWT, verifica que no haya expirado y retorna el objeto User de PostgreSQL.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Credenciales de acceso inválidas o expiradas.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        # 1. Decodificar y firmar el token JWT asíncronamente en memoria local
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")

        if user_id is None:
            raise credentials_exception

        user_id_int = int(user_id)
    except (JWTError, ValueError, TypeError):
        raise credentials_exception

    # 2. Consultar de forma asíncrona la existencia del usuario en tu Postgres local
    query = select(User).where(User.id == user_id_int)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if user is None:
        raise credentials_exception
        
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Tu cuenta de usuario se encuentra suspendida."
        )
        
    return user


async def get_user_from_token(token: str, db: AsyncSession) -> Optional[User]:
    """
    Resuelve un usuario a partir de un token JWT crudo (sin pasar por el header).
    Útil para WebSockets, donde el token no viaja en la cabecera Authorization.
    Devuelve None si el token es inválido o el usuario no existe / está inactivo.
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if user_id is None:
            return None
        user_id_int = int(user_id)
    except (JWTError, ValueError, TypeError):
        return None

    query = select(User).where(User.id == user_id_int)
    result = await db.execute(query)
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        return None
    return user


class RoleChecker:
    """
    Filtro de Seguridad Empresarial (RBAC).
    Permite restringir endpoints específicos solo a ciertos roles jerárquicos.
    """
    def __init__(self, allowed_roles: list[UserRole]):
        self.allowed_roles = allowed_roles

    def __call__(self, current_user: User = Depends(get_current_user)) -> User:
        # Comprobar si el rol inyectado en el JWT cumple con los privilegios de la ruta
        if current_user.role not in self.allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes los permisos requeridos para ejecutar esta acción administrativa."
            )
        return current_user
