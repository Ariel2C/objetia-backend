from enum import Enum
from typing import Optional
from datetime import datetime
from sqlmodel import Field
from pydantic import EmailStr
from src.common.timezone import ahora_argentina

# Importamos la clase Base unificada de tu configuración
from src.config.database import Base

from sqlalchemy import Column, Enum as SQLEnum

class UserRole(str, Enum):
    ROOT = "root"                  # SuperAdministrador Programador (Acceso Total)
    ADMIN = "admin"                # Administrador de Contenido, Marcas y Banners (CMS)
    FINANCIAL = "financial"        # Administrador Financiero (Métricas y aprobaciones)
    CLIENT = "client"              # Comprador y Vendedor común (C2C)

# ==============================================================================
# MODELO PRINCIPAL: USUARIO (PostgreSQL Entity)
# ==============================================================================
class User(Base, table=True):
    __tablename__ = "users"

    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    email: str = Field(index=True, unique=True, nullable=False)
    full_name: str = Field(nullable=False)
    avatar_url: Optional[str] = Field(default=None)
    
    # 🌟 CONTROL HÍBRIDO DE CREDENCIALES
    google_id: Optional[str] = Field(default=None, index=True, unique=True) # Opcional si entra por correo
    hashed_password: Optional[str] = Field(default=None)                  # Opcional si entra por Google
    
    role: UserRole = Field(
        default=UserRole.CLIENT,
        sa_column=Column(SQLEnum(UserRole, values_callable=lambda x: [e.value for e in x]), nullable=False, default=UserRole.CLIENT)
    )
    reputation_score: float = Field(default=5.0)
    total_sales_count: int = Field(default=0)
    is_active: bool = Field(default=True, nullable=False)
    
    # Datos de Dirección
    street: Optional[str] = Field(default=None, nullable=True)
    number: Optional[str] = Field(default=None, nullable=True)
    floor_dept: Optional[str] = Field(default=None, nullable=True)
    postal_code: Optional[str] = Field(default=None, nullable=True)
    city: Optional[str] = Field(default=None, nullable=True)
    province: Optional[str] = Field(default=None, nullable=True)
    
    # 🌟 AUDITORÍA LEGAL Y PREFERENCIAS
    accepted_terms_at: Optional[datetime] = Field(default=None, nullable=True)
    accepted_terms_version: Optional[str] = Field(default=None, nullable=True)
    wants_newsletter: bool = Field(default=False, nullable=False)

    created_at: datetime = Field(default_factory=ahora_argentina, nullable=False)
    updated_at: datetime = Field(default_factory=ahora_argentina, nullable=False)


# ==============================================================================
# MODELO: SESIONES DE USUARIOS (UserSession)
# ==============================================================================
class UserSession(Base, table=True):
    __tablename__ = "user_sessions"

    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    user_id: int = Field(foreign_key="users.id", index=True, nullable=False)
    ip_address: Optional[str] = Field(default="127.0.0.1", nullable=True)
    user_agent: Optional[str] = Field(default=None, nullable=True)
    is_active: bool = Field(default=True, nullable=False)
    created_at: datetime = Field(default_factory=ahora_argentina, nullable=False)
    last_activity: datetime = Field(default_factory=ahora_argentina, nullable=False)


# ==============================================================================
# MODELO: LOGS DE AUDITORÍA Y SEGURIDAD (UserLog)
# ==============================================================================
class UserLog(Base, table=True):
    __tablename__ = "user_logs"

    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    user_id: Optional[int] = Field(default=None, foreign_key="users.id", index=True, nullable=True)
    user_email: Optional[str] = Field(default=None, nullable=True)
    action: str = Field(nullable=False, index=True) # ej: LOGIN, REGISTER, ROLE_CHANGE, DELETE_USER, REVOKE_SESSION
    details: Optional[str] = Field(default=None, nullable=True)
    ip_address: Optional[str] = Field(default="127.0.0.1", nullable=True)
    created_at: datetime = Field(default_factory=ahora_argentina, nullable=False)


# ==============================================================================
# SCHEMAS DE PYDANTIC (Validación de Datos para la API)
# ==============================================================================
class UserRegisterForm(Base):
    """Esquema de validación para el formulario de registro TRADICIONAL"""
    email: EmailStr
    password: str
    full_name: str
    accepted_terms: bool = False
    wants_newsletter: bool = False

class UserResponse(Base):
    """Esquema de salida seguro para enviar datos al Front-End (Corta el hashed_password)"""
    id: int
    email: str
    full_name: str
    avatar_url: Optional[str]
    role: UserRole
    reputation_score: float
    total_sales_count: int
    created_at: datetime
    
    # Address details
    street: Optional[str] = None
    number: Optional[str] = None
    floor_dept: Optional[str] = None
    postal_code: Optional[str] = None
    city: Optional[str] = None
    province: Optional[str] = None
