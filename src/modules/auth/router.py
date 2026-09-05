from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from fastapi.security import OAuth2PasswordRequestForm
from sqlmodel import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from src.config.database import get_db
from src.modules.users.models import User, UserAddress, UserRole, UserRegisterForm, UserResponse, Role, Permission, RolePermission
from src.modules.auth.services import AuthService
from src.modules.audit.services import AuditService
from src.common.timezone import ahora_argentina
from src.common.email_service import enviar_email_bienvenida

router = APIRouter(prefix="/auth", tags=["Autenticación Híbrida"])

async def obtener_permisos_usuario(user: User, db: AsyncSession) -> List[str]:
    user_role_code = user.role.value.lower() if hasattr(user.role, 'value') else str(user.role).lower()
    if user_role_code == "root":
        return ["full_access"]

    role_res = await db.execute(select(Role).where(func.lower(Role.code) == user_role_code))
    role_obj = role_res.scalar_one_or_none()
    permission_codes = []
    if role_obj:
        rp_res = await db.execute(
            select(Permission)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == role_obj.id)
        )
        perms_db = rp_res.scalars().all()
        for p in perms_db:
            if getattr(p, "target_section", None):
                for ts in p.target_section.split(","):
                    ts_clean = ts.strip().lower()
                    if ts_clean:
                        permission_codes.append(ts_clean)
            elif p.code:
                permission_codes.append(p.code.lower())

    if not permission_codes and user_role_code in ["cliente", "client"]:
        permission_codes = ["billetera", "publications", "purchases", "sales", "perfil"]

    return list(dict.fromkeys(permission_codes))


async def armar_user_dict(user: User, db: AsyncSession) -> dict:
    perms = await obtener_permisos_usuario(user, db)
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "avatar_url": user.avatar_url,
        "role": user.role.value if hasattr(user.role, 'value') else str(user.role),
        "reputation_score": getattr(user, 'reputation_score', 5.0),
        "total_sales_count": getattr(user, 'total_sales_count', 0),
        "created_at": user.created_at,
        "permissions": perms,
        "street": getattr(user, 'street', None),
        "number": getattr(user, 'number', None),
        "floor_dept": getattr(user, 'floor_dept', None),
        "postal_code": getattr(user, 'postal_code', None),
        "city": getattr(user, 'city', None),
        "province": getattr(user, 'province', None),
    }

class GoogleLoginRequest(BaseModel):
    id_token: str
    wants_newsletter: Optional[bool] = False
    accepted_terms: Optional[bool] = True

class CheckEmailRequest(BaseModel):
    email: str

# ==============================================================================
# VÍA A: VERIFICACIÓN DE EXISTENCIA DE EMAIL
# ==============================================================================
@router.post("/check-email")
async def verificar_existencia_email(payload: CheckEmailRequest, db: AsyncSession = Depends(get_db)):
    """Verifica si un correo electrónico ya se encuentra registrado en la base de datos."""
    email_clean = payload.email.strip().lower()
    query = select(User).where(User.email == email_clean)
    result = await db.execute(query)
    user = result.scalar_one_or_none()
    return {
        "exists": user is not None,
        "email": email_clean,
        "full_name": user.full_name if user else None
    }

# ==============================================================================
# VÍA A.2: AUTENTICACIÓN POR GOOGLE (OAuth2)
# ==============================================================================
@router.post("/google")
async def google_auth(
    payload: GoogleLoginRequest, 
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    google_data = AuthService.verificar_google_token(payload.id_token)
    email = google_data.get("email")
    google_id = google_data.get("sub")
    full_name = google_data.get("name")
    avatar_url = google_data.get("picture")

    query = select(User).where(User.email == email)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        # Registro nuevo por Google con auditoría legal de Términos
        user = User(
            email=email,
            google_id=google_id,
            full_name=full_name,
            avatar_url=avatar_url,
            role=UserRole.CLIENT,
            accepted_terms_at=ahora_argentina(),
            accepted_terms_version="v1.0",
            wants_newsletter=payload.wants_newsletter or False
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

        # Enviar email de bienvenida al registrarse por primera vez
        background_tasks.add_task(enviar_email_bienvenida, user.email, user.full_name)
    elif not user.google_id:
        # Vinculación de cuenta existente
        user.google_id = google_id
        if not user.avatar_url:
            user.avatar_url = avatar_url
        db.add(user)
        await db.commit()

    token_data = {"sub": str(user.id), "email": user.email, "role": user.role.value if hasattr(user.role, 'value') else str(user.role)}
    return {
        "access_token": AuthService.crear_access_token(data=token_data),
        "token_type": "bearer",
        "user": await armar_user_dict(user, db)
    }


from datetime import datetime
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
    # Validar aceptación estricta de Términos y Condiciones
    if not form_data.accepted_terms:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Debés aceptar los Términos y Condiciones para poder registrarte."
        )

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
        accepted_terms_at=ahora_argentina(),
        accepted_terms_version="v1.0",
        wants_newsletter=getattr(form_data, 'wants_newsletter', False)
    )
    
    db.add(nuevo_usuario)
    await db.commit()
    await db.refresh(nuevo_usuario)

    # Disparar envío del correo de bienvenida en segundo plano
    background_tasks.add_task(enviar_email_bienvenida, nuevo_usuario.email, nuevo_usuario.full_name)

    token_data = {"sub": str(nuevo_usuario.id), "email": nuevo_usuario.email, "role": nuevo_usuario.role.value if hasattr(nuevo_usuario.role, 'value') else str(nuevo_usuario.role)}
    access_token = AuthService.crear_access_token(data=token_data)
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": await armar_user_dict(nuevo_usuario, db)
    }


# ==============================================================================
# VÍA C: INICIO DE SESIÓN TRADICIONAL (Correo y Contraseña)
# ==============================================================================
@router.post("/login/classic")
async def login_clasico(
    form_data: OAuth2PasswordRequestForm = Depends(), # Utiliza los campos username y password estándar
    background_tasks: BackgroundTasks = None,
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

    # 4. Auditoría de inicio de sesión
    if background_tasks:
        background_tasks.add_task(AuditService.registrar_sesion, db, user.id)
        background_tasks.add_task(AuditService.registrar_log, db, "LOGIN", user.id, user.email, "Inicio de sesión clásico exitoso.")

    # 5. Emitir el mismo token JWT unificado para toda la aplicación web
    token_data = {"sub": str(user.id), "email": user.email, "role": user.role.value if hasattr(user.role, 'value') else str(user.role)}
    return {
        "access_token": AuthService.crear_access_token(data=token_data),
        "token_type": "bearer",
        "user": await armar_user_dict(user, db)
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
        raise HTTPException(status_code=400, detail="Rol invalido. Debe ser: admin, cliente o root.")

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

from src.modules.users.models import Role, Permission, RolePermission

@router.get("/me", response_model=UserResponse)
async def get_current_user_profile(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Retorna los datos completos del perfil del usuario, incluyendo dirección de envío y permisos."""
    user_data = await armar_user_dict(current_user, db)
    return UserResponse(**user_data)

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


# ==============================================================================
# GESTIÓN DE MÚLTIPLES DIRECCIONES DEL USUARIO (UserAddress CRUD)
# ==============================================================================
class AddressCreateRequest(BaseModel):
    title: Optional[str] = "Mi casa"
    street: str
    number: str
    floor_dept: Optional[str] = None
    postal_code: Optional[str] = None
    city: str
    province: str
    is_default: Optional[bool] = False
    lat: Optional[float] = None
    lng: Optional[float] = None

class AddressUpdateRequest(BaseModel):
    title: Optional[str] = None
    street: Optional[str] = None
    number: Optional[str] = None
    floor_dept: Optional[str] = None
    postal_code: Optional[str] = None
    city: Optional[str] = None
    province: Optional[str] = None
    is_default: Optional[bool] = None
    lat: Optional[float] = None
    lng: Optional[float] = None

def _sincronizar_direccion_principal_en_usuario(user: User, addr: Optional[UserAddress]):
    if addr:
        user.street = addr.street
        user.number = addr.number
        user.floor_dept = addr.floor_dept
        user.postal_code = addr.postal_code
        user.city = addr.city
        user.province = addr.province
    else:
        user.street = None
        user.number = None
        user.floor_dept = None
        user.postal_code = None
        user.city = None
        user.province = None

@router.get("/addresses")
async def list_user_addresses(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Retorna la lista de todas las direcciones guardadas del usuario."""
    query = select(UserAddress).where(UserAddress.user_id == current_user.id).order_by(UserAddress.is_default.desc(), UserAddress.id.desc())
    result = await db.execute(query)
    addresses = result.scalars().all()

    # Si no tiene direcciones en la tabla pero tiene una cargada previamente en el usuario, migrarla automáticamente
    if not addresses and (current_user.street or current_user.city):
        migrated = UserAddress(
            user_id=current_user.id,
            title="Principal",
            street=current_user.street or "",
            number=current_user.number or "",
            floor_dept=current_user.floor_dept,
            postal_code=current_user.postal_code,
            city=current_user.city or "",
            province=current_user.province or "",
            is_default=True
        )
        db.add(migrated)
        await db.commit()
        await db.refresh(migrated)
        return [migrated]

    return addresses

@router.post("/addresses", status_code=status.HTTP_201_CREATED)
async def create_user_address(
    payload: AddressCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Crea una nueva dirección para el usuario."""
    existing_query = select(UserAddress).where(UserAddress.user_id == current_user.id)
    existing_res = await db.execute(existing_query)
    existing_addresses = existing_res.scalars().all()

    should_be_default = bool(payload.is_default or len(existing_addresses) == 0)

    if should_be_default:
        for addr in existing_addresses:
            addr.is_default = False
            db.add(addr)

    new_address = UserAddress(
        user_id=current_user.id,
        title=payload.title or "Mi dirección",
        street=payload.street.strip(),
        number=payload.number.strip(),
        floor_dept=payload.floor_dept.strip() if payload.floor_dept else None,
        postal_code=payload.postal_code.strip() if payload.postal_code else None,
        city=payload.city.strip(),
        province=payload.province.strip(),
        is_default=should_be_default,
        lat=payload.lat,
        lng=payload.lng
    )
    db.add(new_address)

    if should_be_default:
        _sincronizar_direccion_principal_en_usuario(current_user, new_address)
        db.add(current_user)

    await db.commit()
    await db.refresh(new_address)
    return new_address

@router.put("/addresses/{address_id}")
async def update_user_address(
    address_id: int,
    payload: AddressUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Actualiza una dirección existente perteneciente al usuario."""
    query = select(UserAddress).where(UserAddress.id == address_id, UserAddress.user_id == current_user.id)
    result = await db.execute(query)
    address = result.scalar_one_or_none()

    if not address:
        raise HTTPException(status_code=404, detail="Dirección no encontrada.")

    if payload.title is not None:
        address.title = payload.title
    if payload.street is not None:
        address.street = payload.street.strip()
    if payload.number is not None:
        address.number = payload.number.strip()
    if payload.floor_dept is not None:
        address.floor_dept = payload.floor_dept.strip() if payload.floor_dept else None
    if payload.postal_code is not None:
        address.postal_code = payload.postal_code.strip() if payload.postal_code else None
    if payload.city is not None:
        address.city = payload.city.strip()
    if payload.province is not None:
        address.province = payload.province.strip()
    if payload.lat is not None:
        address.lat = payload.lat
    if payload.lng is not None:
        address.lng = payload.lng

    if payload.is_default is True:
        all_query = select(UserAddress).where(UserAddress.user_id == current_user.id, UserAddress.id != address_id)
        all_res = await db.execute(all_query)
        for other in all_res.scalars().all():
            other.is_default = False
            db.add(other)
        address.is_default = True
        _sincronizar_direccion_principal_en_usuario(current_user, address)
        db.add(current_user)
    elif payload.is_default is False and address.is_default:
        address.is_default = False

    address.updated_at = ahora_argentina()
    db.add(address)
    await db.commit()
    await db.refresh(address)
    return address

@router.delete("/addresses/{address_id}")
async def delete_user_address(
    address_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Elimina una dirección del usuario. Si era la principal, promueve otra."""
    query = select(UserAddress).where(UserAddress.id == address_id, UserAddress.user_id == current_user.id)
    result = await db.execute(query)
    address = result.scalar_one_or_none()

    if not address:
        raise HTTPException(status_code=404, detail="Dirección no encontrada.")

    was_default = address.is_default
    await db.delete(address)
    await db.flush()

    if was_default:
        remaining_query = select(UserAddress).where(UserAddress.user_id == current_user.id).order_by(UserAddress.id.desc())
        remaining_res = await db.execute(remaining_query)
        next_default = remaining_res.scalars().first()
        if next_default:
            next_default.is_default = True
            db.add(next_default)
            _sincronizar_direccion_principal_en_usuario(current_user, next_default)
        else:
            _sincronizar_direccion_principal_en_usuario(current_user, None)
        db.add(current_user)

    await db.commit()
    return {"mensaje": "Dirección eliminada correctamente."}

@router.patch("/addresses/{address_id}/default")
async def set_default_user_address(
    address_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Establece una dirección como la activa / principal del usuario."""
    all_query = select(UserAddress).where(UserAddress.user_id == current_user.id)
    all_res = await db.execute(all_query)
    all_addresses = all_res.scalars().all()

    target_address = None
    for addr in all_addresses:
        if addr.id == address_id:
            addr.is_default = True
            target_address = addr
        else:
            addr.is_default = False
        db.add(addr)

    if not target_address:
        raise HTTPException(status_code=404, detail="Dirección no encontrada.")

    _sincronizar_direccion_principal_en_usuario(current_user, target_address)
    db.add(current_user)

    await db.commit()
    await db.refresh(target_address)
    return target_address


