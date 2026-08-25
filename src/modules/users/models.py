from enum import Enum
from typing import Optional, List
from datetime import datetime
from sqlmodel import Field
from pydantic import EmailStr
from src.common.timezone import ahora_argentina

# Importamos la clase Base unificada de tu configuración
from src.config.database import Base

from sqlalchemy import Column, String, Enum as SQLEnum

class UserRole(str, Enum):
    ROOT = "root"                  # SuperAdministrador Programador (Acceso Total)
    ADMIN = "admin"                # Administrador de Contenido, Marcas y Banners (CMS)
    CLIENT = "client"              # Comprador y Vendedor común (legacy)
    CLIENTE = "cliente"            # Comprador y Vendedor común (C2C)
    FINANCIAL = "financial"        # Legacy (Compatibilidad)

# ==============================================================================
# MODELO: TABLA DE PERMISOS DEL SISTEMA
# ==============================================================================
class Permission(Base, table=True):
    __tablename__ = "permissions"

    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    code: str = Field(index=True, unique=True, nullable=False)
    name: str = Field(nullable=False)
    category: str = Field(default="Sistema", nullable=False)
    description: Optional[str] = Field(default=None)
    target_section: Optional[str] = Field(default=None) # Código de la sección de la app protegida por este permiso
    created_at: datetime = Field(default_factory=ahora_argentina)

# ==============================================================================
# MODELO: SECCIONES Y ESTRUCTURA DE LA APLICACIÓN (TREE / JERARQUÍA)
# ==============================================================================
class AppSection(Base, table=True):
    __tablename__ = "app_sections"

    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    code: str = Field(index=True, unique=True, nullable=False)
    name: str = Field(nullable=False)
    path: Optional[str] = Field(default=None, nullable=True) # Dirección de ruta URL, ej: /dashboard o /appearance
    category: str = Field(default="General", nullable=False)
    description: Optional[str] = Field(default=None)
    parent_code: Optional[str] = Field(default=None, index=True)
    icon_name: Optional[str] = Field(default="Folder")
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=ahora_argentina)

# ==============================================================================
# MODELO: ACCIONES ESPECÍFICAS DENTRO DE UNA SECCIÓN
# ==============================================================================
class SectionAction(Base, table=True):
    __tablename__ = "section_actions"

    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    section_code: str = Field(index=True, nullable=False)
    code: str = Field(index=True, unique=True, nullable=False)
    name: str = Field(nullable=False)
    description: Optional[str] = Field(default=None)
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=ahora_argentina)

# ==============================================================================
# MODELO ASOCIACIÓN: ROL Y PERMISO (Junction Table)
# ==============================================================================
class RolePermission(Base, table=True):
    __tablename__ = "role_permissions"

    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    role_id: int = Field(index=True, nullable=False)
    permission_id: int = Field(index=True, nullable=False)
    created_at: datetime = Field(default_factory=ahora_argentina)

# ==============================================================================
# MODELO: TABLA DE RANGOS / ROLES
# ==============================================================================
class Role(Base, table=True):
    __tablename__ = "roles"

    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    code: str = Field(index=True, unique=True, nullable=False)
    name: str = Field(nullable=False)
    label: str = Field(nullable=False)
    description: Optional[str] = Field(default=None)
    level: int = Field(default=10, nullable=False)
    badge_color: str = Field(default="bg-[#2a2a2a] text-[#8c8c8c] border-[#333333]")
    permissions: str = Field(default="Acceso General")
    created_at: datetime = Field(default_factory=ahora_argentina)

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
    
    role: str = Field(
        default="cliente",
        sa_column=Column(String, nullable=False, default="cliente")
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
    role: str
    reputation_score: float
    total_sales_count: int
    created_at: datetime
    permissions: Optional[List[str]] = []
    
    # Address details
    street: Optional[str] = None
    number: Optional[str] = None
    floor_dept: Optional[str] = None
    postal_code: Optional[str] = None
    city: Optional[str] = None
    province: Optional[str] = None
