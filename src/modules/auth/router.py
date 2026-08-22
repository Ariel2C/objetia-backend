from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from src.config.database import get_db
from src.modules.users.models import User, UserRole, UserRegisterForm, UserResponse
from src.modules.auth.services import AuthService

router = APIRouter(prefix="/auth", tags=["Autenticación Híbrida"])

class GoogleLoginRequest(BaseModel):
    id_token: str

# ==============================================================================
# VÍA A: AUTENTICACIÓN POR GOOGLE (OAuth2)
# ==============================================================================
@router.post("/google")
async def google_auth(payload: GoogleLoginRequest, db: AsyncSession = Depends(get_db)):
    google_data = AuthService.verificar_google_token(payload.id_token)
    email = google_data.get("email")
    google_id = google_data.get("sub")
    full_name = google_data.get("name")
    avatar_url = google_data.get("picture")

    query = select(User).where(User.email == email)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        # Registro automático si viene de Google
        user = User(
            email=email,
            google_id=google_id,
            full_name=full_name,
            avatar_url=avatar_url,
            role=UserRole.CLIENT
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
    elif not user.google_id:
        # Vinculación de cuenta: si ya existía por correo clásico, le enlazamos su Google ID
        user.google_id = google_id
        if not user.avatar_url:
            user.avatar_url = avatar_url
        db.add(user)
        await db.commit()

    token_data = {"sub": str(user.id), "email": user.email, "role": user.role.value}
    return {
        "access_token": AuthService.crear_access_token(data=token_data),
        "token_type": "bearer",
        "user": user
    }


from datetime import datetime
from fastapi import BackgroundTasks
from src.common.email_service import enviar_email_bienvenida

# ==============================================================================
# VÍA B: REGISTRO TRADICIONAL (Correo y Contraseña)
# ==============================================================================
@router.post("/register", status_code=status.HTTP_201_CREATED)
async def registrar_usuario_clasico(
    form_data: UserRegisterForm, 
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """Crea un usuario nuevo aplicando el algoritmo de hashing Bcrypt a su clave y guardando versión legal de Términos."""
    # Verificar si el correo ya está registrado en la base de datos local
    query = select(User).where(User.email == form_data.email)
    result = await db.execute(query)
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="El correo electrónico ya se encuentra registrado.")

    # Hashear la contraseña antes de guardarla de forma inalterable
    hashed_pwd = AuthService.hash_password(form_data.password)

    nuevo_usuario = User(
        email=form_data.email,
        full_name=form_data.full_name,
        hashed_password=hashed_pwd,
        role=UserRole.CLIENT,
        accepted_terms_at=datetime.utcnow(),
        accepted_terms_version="v1.0",
        wants_newsletter=getattr(form_data, 'wants_newsletter', False)
    )
    
    db.add(nuevo_usuario)
    await db.commit()
    await db.refresh(nuevo_usuario)

    # Disparar envío del correo de bienvenida en segundo plano
    background_tasks.add_task(enviar_email_bienvenida, nuevo_usuario.email, nuevo_usuario.full_name)

    token_data = {"sub": str(nuevo_usuario.id), "email": nuevo_usuario.email, "role": nuevo_usuario.role.value}
    access_token = AuthService.crear_access_token(data=token_data)
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": nuevo_usuario
    }


# ==============================================================================
# VÍA C: INICIO DE SESIÓN TRADICIONAL (Correo y Contraseña)
# ==============================================================================
@router.post("/login/classic")
async def login_clasico(
    form_data: OAuth2PasswordRequestForm = Depends(), # Utiliza los campos username y password estándar
    db: AsyncSession = Depends(get_db)
):
    """Verifica las credenciales locales y emite el token JWT correspondiente."""
    # 1. Buscar al usuario por correo
    query = select(User).where(User.email == form_data.username)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    # 2. Validaciones estrictas de seguridad corporativa
    if not user or not user.hashed_password:
        raise HTTPException(status_code=401, detail="Correo electrónico o contraseña incorrectos.")

    # 3. Verificar criptográficamente la clave de entrada contra la DB local
    if not AuthService.verificar_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Correo electrónico o contraseña incorrectos.")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Esta cuenta se encuentra suspendida.")

    # 4. Emitir el mismo token JWT unificado para toda la aplicación web
    token_data = {"sub": str(user.id), "email": user.email, "role": user.role.value}
    return {
        "access_token": AuthService.crear_access_token(data=token_data),
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
            "street": user.street,
            "number": user.number,
            "floor_dept": user.floor_dept,
            "postal_code": user.postal_code,
            "city": user.city,
            "province": user.province
        }
    }


# 🌟 ENDPOINT DE ASIGNACIÓN DE ROL POR ADMINISTRADOR
from src.common.dependencies import RoleChecker

class RoleChangeRequest(BaseModel):
    email: str
    role: str

@router.put("/assign-role", status_code=status.HTTP_200_OK)
async def assign_user_role(
    payload: RoleChangeRequest,
    db: AsyncSession = Depends(get_db),
    admin_verificado: User = Depends(RoleChecker([UserRole.ADMIN]))
):
    """
    Ruta protegida para que un Administrador General pueda asignar roles
    (admin, financial, client) a otros usuarios por correo electrónico.
    """
    query = select(User).where(User.email == payload.email)
    result = await db.execute(query)
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado con ese correo electrónico.")
        
    try:
        nuevo_rol = UserRole(payload.role.lower().strip())
        user.role = nuevo_rol
        db.add(user)
        await db.commit()
        return {"mensaje": f"Rol de {payload.email} cambiado exitosamente a {payload.role.upper()}."}
    except ValueError:
        raise HTTPException(status_code=400, detail="Rol invalido. Debe ser: admin, client o financial.")

# ==============================================================================
# VÍA D: GESTIÓN DE PERFIL Y DIRECCIÓN DE ENVÍO
# ==============================================================================
from src.common.dependencies import get_current_user

class ProfileUpdateRequest(BaseModel):
    full_name: Optional[str] = None
    avatar_url: Optional[str] = None
    street: Optional[str] = None
    number: Optional[str] = None
    floor_dept: Optional[str] = None
    postal_code: Optional[str] = None
    city: Optional[str] = None
    province: Optional[str] = None

@router.get("/me", response_model=UserResponse)
async def get_current_user_profile(
    current_user: User = Depends(get_current_user)
):
    """Retorna los datos completos del perfil del usuario, incluyendo dirección de envío."""
    return current_user

@router.put("/profile", response_model=UserResponse)
async def update_user_profile(
    payload: ProfileUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Actualiza los datos del perfil y dirección de envío del usuario en la base de datos."""
    if payload.full_name is not None:
        current_user.full_name = payload.full_name
    if payload.avatar_url is not None:
        current_user.avatar_url = payload.avatar_url
    if payload.street is not None:
        current_user.street = payload.street
    if payload.number is not None:
        current_user.number = payload.number
    if payload.floor_dept is not None:
        current_user.floor_dept = payload.floor_dept
    if payload.postal_code is not None:
        current_user.postal_code = payload.postal_code
    if payload.city is not None:
        current_user.city = payload.city
    if payload.province is not None:
        current_user.province = payload.province
        
    db.add(current_user)
    await db.commit()
    await db.refresh(current_user)
    return current_user

