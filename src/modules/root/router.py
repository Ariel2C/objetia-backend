from typing import Optional, List
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from sqlmodel import select, or_, desc
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, validator

from src.config.database import get_db, engine, Base
from src.modules.users.models import User, UserRole, UserSession, UserLog, Role
from src.modules.auth.services import AuthService
from src.modules.audit.services import AuditService

from fastapi.security import OAuth2PasswordBearer

router = APIRouter(prefix="/root", tags=["Panel Root - Control de Usuarios y Auditoría"])

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login/classic")

# Helper de dependencia: Exigir rol ROOT strictly
async def get_current_root_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db)
) -> User:
    try:
        payload = AuthService.verificar_token(token)
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Token no válido.")
        user = await db.get(User, int(user_id))
    except Exception:
        raise HTTPException(status_code=401, detail="Token de sesión no válido o expirado.")

    if not user or user.role != UserRole.ROOT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso denegado: Esta sección requiere privilegios de programador ROOT."
        )
    return user

class RoleUpdateRequest(BaseModel):
    role: str

    @validator("role")
    def validate_role(cls, v):
        v_str = str(v).lower().strip()
        if v_str in ["cliente", "client"]:
            return UserRole.CLIENT
        if v_str == "admin":
            return UserRole.ADMIN
        if v_str == "root":
            return UserRole.ROOT
        raise ValueError(f"Rol inválido: '{v}'. Debe ser: root, admin o cliente.")

class RoleCreateForm(BaseModel):
    code: str
    name: str
    label: str
    description: Optional[str] = None
    level: int = 10
    badge_color: Optional[str] = "bg-[#2a2a2a] text-[#8c8c8c] border-[#333333]"
    permissions: Optional[str] = "Acceso General"

# ==============================================================================
# 1. LISTAR USUARIOS Y ADMINISTRADORES
# ==============================================================================
@router.get("/users")
async def listar_usuarios_root(
    search: Optional[str] = None,
    role: Optional[UserRole] = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    current_root: User = Depends(get_current_root_user),
    db: AsyncSession = Depends(get_db)
):
    """Lista todos los usuarios y administradores registrados con búsqueda y filtros."""
    query = select(User)

    if search:
        s = f"%{search.strip()}%"
        query = query.where(or_(User.email.ilike(s), User.full_name.ilike(s)))
    
    if role:
        query = query.where(User.role == role)
    
    query = query.order_by(desc(User.created_at)).offset((page - 1) * limit).limit(limit)
    res = await db.execute(query)
    users = res.scalars().all()

    return {
        "page": page,
        "limit": limit,
        "users": users
    }

# ==============================================================================
# 2. CAMBIAR RANGO / ROL DE UN USUARIO O ADMIN
# ==============================================================================
@router.patch("/users/{user_id}/role")
async def cambiar_rol_usuario(
    user_id: int,
    payload: RoleUpdateRequest,
    request: Request,
    current_root: User = Depends(get_current_root_user),
    db: AsyncSession = Depends(get_db)
):
    """Permite al usuario Root otorgar o modificar el rango de cualquier cuenta."""
    target_user = await db.get(User, user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")

    if target_user.id == current_root.id and payload.role != UserRole.ROOT:
        raise HTTPException(status_code=400, detail="No podés quitarte tu propio rol ROOT.")

    rol_anterior = target_user.role.value if hasattr(target_user.role, 'value') else str(target_user.role)
    target_user.role = payload.role
    target_user.updated_at = datetime.utcnow()
    
    db.add(target_user)
    await db.commit()
    await db.refresh(target_user)

    # Registrar en Log de Auditoría
    ip = request.client.host if request.client else "127.0.0.1"
    await AuditService.registrar_log(
        db,
        accion="CHANGE_ROLE",
        usuario_id=current_root.id,
        usuario_email=current_root.email,
        detalles=f"Cambió el rol de {target_user.email} (ID: {target_user.id}) de '{rol_anterior}' a '{payload.role.value}'.",
        ip_address=ip
    )

    return {
        "message": f"Rol actualizado exitosamente a {payload.role.value}.",
        "user": target_user
    }

# ==============================================================================
# 3. ELIMINAR USUARIO O ADMINISTRADOR
# ==============================================================================
@router.delete("/users/{user_id}")
async def eliminar_usuario_root(
    user_id: int,
    request: Request,
    current_root: User = Depends(get_current_root_user),
    db: AsyncSession = Depends(get_db)
):
    """Elimina definitivamente una cuenta de usuario o administrador."""
    target_user = await db.get(User, user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")

    if target_user.id == current_root.id:
        raise HTTPException(status_code=400, detail="No podés eliminar tu propia cuenta ROOT.")

    email_borrado = target_user.email
    await db.delete(target_user)
    await db.commit()

    # Registrar en Log de Auditoría
    ip = request.client.host if request.client else "127.0.0.1"
    await AuditService.registrar_log(
        db,
        accion="DELETE_USER",
        usuario_id=current_root.id,
        usuario_email=current_root.email,
        detalles=f"Eliminó definitivamente la cuenta de {email_borrado} (ID: {user_id}).",
        ip_address=ip
    )

    return {"message": f"Usuario {email_borrado} eliminado correctamente."}

# ==============================================================================
# 4. MONITOR DE SESIONES ACTIVAS
# ==============================================================================
@router.get("/sessions")
async def listar_sesiones_root(
    limit: int = Query(default=50, ge=1, le=200),
    current_root: User = Depends(get_current_root_user),
    db: AsyncSession = Depends(get_db)
):
    """Lista las sesiones dejando únicamente activa la sesión más reciente de cada usuario."""
    from datetime import timedelta
    from src.common.timezone import ahora_argentina

    ahora = ahora_argentina()
    ahora_naive = ahora.replace(tzinfo=None)

    # 1. Obtener todas las sesiones activas en la BD ordenadas por fecha reciente
    stmt_activas = select(UserSession).where(UserSession.is_active == True).order_by(desc(UserSession.created_at))
    res_act = await db.execute(stmt_activas)
    sesiones_activas = res_act.scalars().all()

    usuarios_con_sesion_activa = set()
    modificado = False
    for s in sesiones_activas:
        creado_naive = s.created_at.replace(tzinfo=None) if s.created_at and s.created_at.tzinfo else s.created_at
        es_expirada_por_tiempo = (ahora_naive - creado_naive).total_seconds() > 86400 if creado_naive else True

        # Si el usuario ya tiene una sesión más reciente activa hoy O pasaron más de 24h, desactivar esta sesión
        if s.user_id in usuarios_con_sesion_activa or es_expirada_por_tiempo:
            s.is_active = False
            db.add(s)
            modificado = True
        else:
            usuarios_con_sesion_activa.add(s.user_id)

    if modificado:
        await db.commit()

    # 2. Consultar listado completo ordenado
    query = select(UserSession, User).join(User, UserSession.user_id == User.id).order_by(desc(UserSession.created_at)).limit(limit)
    res = await db.execute(query)
    rows = res.all()

    sesiones = []
    for s, u in rows:
        sesiones.append({
            "id": s.id,
            "user_id": u.id,
            "user_email": u.email,
            "user_name": u.full_name,
            "user_role": u.role,
            "ip_address": s.ip_address,
            "user_agent": s.user_agent,
            "is_active": s.is_active,
            "created_at": s.created_at,
            "last_activity": s.last_activity
        })

    return {"sessions": sesiones}

# ==============================================================================
# 5. REVOCAR / CERRAR SESIÓN REMOTAMENTE
# ==============================================================================
@router.post("/sessions/{session_id}/revoke")
async def revocar_sesion(
    session_id: int,
    request: Request,
    current_root: User = Depends(get_current_root_user),
    db: AsyncSession = Depends(get_db)
):
    """Cierra/invalida remotamente una sesión activa de usuario."""
    sesion = await db.get(UserSession, session_id)
    if not sesion:
        raise HTTPException(status_code=404, detail="Sesión no encontrada.")

    sesion.is_active = False
    db.add(sesion)
    await db.commit()

    ip = request.client.host if request.client else "127.0.0.1"
    await AuditService.registrar_log(
        db,
        accion="REVOKE_SESSION",
        usuario_id=current_root.id,
        usuario_email=current_root.email,
        detalles=f"Revocó la sesión ID #{session_id} del usuario ID #{sesion.user_id}.",
        ip_address=ip
    )

    return {"mensaje": f"Sesión #{session_id} revocada exitosamente."}

class RoleForm(BaseModel):
    code: str
    name: str
    label: str
    description: Optional[str] = None
    level: int = 10
    badge_color: Optional[str] = "bg-[#2a2a2a] text-[#8c8c8c] border-[#333333]"
    permission_ids: Optional[List[int]] = []

class PermissionForm(BaseModel):
    code: str
    name: str
    category: str = "Sistema"
    description: Optional[str] = None

# ==============================================================================
# 6. GESTIÓN DE PERMISOS EN BASE DE DATOS
# ==============================================================================
@router.get("/permissions")
async def listar_permisos_db(
    current_root: User = Depends(get_current_root_user),
    db: AsyncSession = Depends(get_db)
):
    """Lista todos los permisos registrados en la BD. Si está vacía, se auto-inicializan los por defecto."""
    try:
        res = await db.execute(select(Permission).order_by(Permission.category, Permission.name))
        perms = res.scalars().all()
    except Exception:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        res = await db.execute(select(Permission).order_by(Permission.category, Permission.name))
        perms = res.scalars().all()

    if not perms:
        default_perms = [
            # Sistema & Root
            Permission(code="full_access", name="Acceso Total Root", category="Sistema", description="Supervisión total y configuración avanzada"),
            Permission(code="manage_roles", name="Control de Rangos", category="Sistema", description="Crear, editar y eliminar rangos y permisos"),
            Permission(code="manage_users", name="Control de Usuarios", category="Sistema", description="Ver usuarios, cambiar rangos y eliminar cuentas"),
            Permission(code="view_audit_logs", name="Logs de Auditoría", category="Sistema", description="Consultar el historial de eventos del sistema"),
            Permission(code="manage_sessions", name="Monitor de Sesiones", category="Sistema", description="Ver sesiones activas e inactivar accesos remotamente"),
            # CMS & Moderación
            Permission(code="manage_products", name="Moderación de Productos", category="CMS", description="Aprobar, rechazar y revisar publicaciones"),
            Permission(code="manage_banners", name="Gestión de Banners", category="CMS", description="Crear y editar sliders y marcas"),
            Permission(code="manage_branding", name="Branding & Apariencia", category="CMS", description="Modificar la interfaz y presentación pública"),
            # Operaciones C2C & Billetera
            Permission(code="buy_products", name="Comprar Productos", category="Operaciones", description="Realizar compras en la plataforma"),
            Permission(code="sell_products", name="Publicar Venta C2C", category="Operaciones", description="Crear anuncios y vender artículos"),
            Permission(code="wallet_access", name="Mi Billetera", category="Operaciones", description="Gestionar ingresos y retirar saldos")
        ]
        for p in default_perms:
            db.add(p)
        await db.commit()

        res = await db.execute(select(Permission).order_by(Permission.category, Permission.name))
        perms = res.scalars().all()

    return {"permissions": perms}

@router.post("/permissions")
async def crear_permiso_db(
    payload: PermissionForm,
    current_root: User = Depends(get_current_root_user),
    db: AsyncSession = Depends(get_db)
):
    """Crea un nuevo permiso en la BD."""
    code_clean = payload.code.lower().strip().replace(" ", "_")
    existente = await db.execute(select(Permission).where(Permission.code == code_clean))
    if existente.scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"Ya existe un permiso con el código '{code_clean}'.")

    nuevo_permiso = Permission(
        code=code_clean,
        name=payload.name.strip(),
        category=payload.category.strip() or "General",
        description=payload.description
    )
    db.add(nuevo_permiso)
    await db.commit()
    await db.refresh(nuevo_permiso)
    return {"mensaje": "Permiso creado exitosamente.", "permission": nuevo_permiso}

# ==============================================================================
# 7. GESTIÓN DE TABLA DE RANGOS / ROLES EN BASE DE DATOS
# ==============================================================================
@router.get("/roles")
async def listar_roles_db(
    current_root: User = Depends(get_current_root_user),
    db: AsyncSession = Depends(get_db)
):
    """Lista todos los rangos registrados en la tabla roles con sus permisos asociados."""
    try:
        res = await db.execute(select(Role).order_by(Role.level.desc()))
        roles_db = res.scalars().all()
    except Exception:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        res = await db.execute(select(Role).order_by(Role.level.desc()))
        roles_db = res.scalars().all()

    # Asegurar que existan permisos en la BD
    res_perms = await db.execute(select(Permission))
    all_perms = res_perms.scalars().all()
    if not all_perms:
        await listar_permisos_db(current_root, db)
        res_perms = await db.execute(select(Permission))
        all_perms = res_perms.scalars().all()

    perms_dict = {p.id: p for p in all_perms}

    if not roles_db:
        roles_default = [
            Role(code="root", name="ROOT", label="SuperAdmin Programador", description="Acceso total sin restricciones al sistema, base de datos, sesiones, logs y variables de entorno.", level=100, badge_color="bg-amber-500/20 text-amber-300 border-amber-500/40", permissions="Acceso Total"),
            Role(code="admin", name="ADMIN", label="Administrador CMS", description="Administración de contenido, moderación de productos, catálogos, banners y branding.", level=50, badge_color="bg-purple-500/20 text-purple-300 border-purple-500/40", permissions="Moderación CMS"),
            Role(code="cliente", name="CLIENTE", label="Usuario Comprador / Vendedor", description="Perfil estándar de usuario para comprar, publicar productos C2C y gestionar billetera.", level=10, badge_color="bg-[#2a2a2a] text-[#8c8c8c] border-[#333333]", permissions="Comprador / Vendedor")
        ]
        for r in roles_default:
            db.add(r)
        await db.commit()

        res = await db.execute(select(Role).order_by(Role.level.desc()))
        roles_db = res.scalars().all()

        for r in roles_db:
            if r.code == "root":
                for p in all_perms:
                    db.add(RolePermission(role_id=r.id, permission_id=p.id))
            elif r.code == "admin":
                for p in all_perms:
                    if p.category in ["CMS", "Operaciones"] or p.code in ["manage_users"]:
                        db.add(RolePermission(role_id=r.id, permission_id=p.id))
            elif r.code == "cliente":
                for p in all_perms:
                    if p.category == "Operaciones":
                        db.add(RolePermission(role_id=r.id, permission_id=p.id))
        await db.commit()

    # Cargar asociaciones role_permissions
    res_rp = await db.execute(select(RolePermission))
    role_perms_all = res_rp.scalars().all()
    role_to_perms = {}
    for rp in role_perms_all:
        if rp.role_id not in role_to_perms:
            role_to_perms[rp.role_id] = []
        if rp.permission_id in perms_dict:
            role_to_perms[rp.role_id].append(perms_dict[rp.permission_id])

    resultado = []
    for r in roles_db:
        perms_assigned = role_to_perms.get(r.id, [])
        resultado.append({
            "id": r.id,
            "code": r.code,
            "name": r.name,
            "label": r.label,
            "description": r.description,
            "level": r.level,
            "badge_color": r.badge_color,
            "permission_ids": [p.id for p in perms_assigned],
            "permissions": [p.name for p in perms_assigned] or [r.permissions]
        })

    return {"roles": resultado}

@router.post("/roles")
async def crear_rol_db(
    payload: RoleForm,
    current_root: User = Depends(get_current_root_user),
    db: AsyncSession = Depends(get_db)
):
    """Crea un nuevo rango en la BD asociando sus permisos elegidos."""
    code_clean = payload.code.lower().strip().replace(" ", "_")
    existente = await db.execute(select(Role).where(Role.code == code_clean))
    if existente.scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"Ya existe un rango con el código '{code_clean}'.")

    nuevo_rol = Role(
        code=code_clean,
        name=payload.name.upper().strip(),
        label=payload.label.strip() or payload.name.strip(),
        description=payload.description,
        level=payload.level,
        badge_color=payload.badge_color or "bg-emerald-500/20 text-emerald-300 border-emerald-500/40",
        permissions="Personalizado"
    )
    db.add(nuevo_rol)
    await db.commit()
    await db.refresh(nuevo_rol)

    if payload.permission_ids:
        for p_id in payload.permission_ids:
            db.add(RolePermission(role_id=nuevo_rol.id, permission_id=p_id))
        await db.commit()

    return {"mensaje": "Rango creado exitosamente.", "role": nuevo_rol}

@router.put("/roles/{role_id}")
async def editar_rol_db(
    role_id: int,
    payload: RoleForm,
    current_root: User = Depends(get_current_root_user),
    db: AsyncSession = Depends(get_db)
):
    """Edita el nombre visible, etiqueta, descripción y permisos de un rango sin alterar su código interno."""
    target_role = await db.get(Role, role_id)
    if not target_role:
        raise HTTPException(status_code=404, detail="Rango no encontrado.")

    target_role.name = payload.name.upper().strip()
    target_role.label = payload.label.strip() or payload.name.strip()
    target_role.description = payload.description
    target_role.level = payload.level

    db.add(target_role)

    # Actualizar tabla de asociación role_permissions
    res_rp = await db.execute(select(RolePermission).where(RolePermission.role_id == target_role.id))
    current_rp = res_rp.scalars().all()
    for rp in current_rp:
        await db.delete(rp)

    if payload.permission_ids:
        for p_id in payload.permission_ids:
            db.add(RolePermission(role_id=target_role.id, permission_id=p_id))

    await db.commit()
    await db.refresh(target_role)
    return {"mensaje": f"Rango '{target_role.name}' actualizado exitosamente."}

@router.delete("/roles/{role_id}")
async def eliminar_rol_db(
    role_id: int,
    current_root: User = Depends(get_current_root_user),
    db: AsyncSession = Depends(get_db)
):
    """Elimina un rango personalizado únicamente si no hay usuarios asignados."""
    target_role = await db.get(Role, role_id)
    if not target_role:
        raise HTTPException(status_code=404, detail="Rango no encontrado.")

    if target_role.code in ["root", "admin", "cliente", "client"]:
        raise HTTPException(status_code=400, detail="No se pueden eliminar los rangos base del sistema (ROOT, ADMIN, CLIENTE).")

    # Verificar si hay usuarios con este rol asignado
    q_count = select(func.count()).select_from(User).where(
        or_(
            User.role == target_role.code,
            User.role == target_role.name.lower(),
            User.role == target_role.name
        )
    )
    res_count = await db.execute(q_count)
    assigned_count = res_count.scalar_one() or 0

    if assigned_count > 0:
        raise HTTPException(
            status_code=400,
            detail=f"No se puede eliminar el rango '{target_role.name}' porque tiene {assigned_count} usuario(s) asignado(s). Reasigna los usuarios antes de eliminarlo."
        )

    # Borrar permisos asociados
    res_rp = await db.execute(select(RolePermission).where(RolePermission.role_id == target_role.id))
    for rp in res_rp.scalars().all():
        await db.delete(rp)

    await db.delete(target_role)
    await db.commit()
    return {"mensaje": f"Rango '{target_role.name}' eliminado exitosamente de la base de datos."}

# ==============================================================================
# 6. VER HISTORIAL DE LOGS DE AUDITORÍA
# ==============================================================================
@router.get("/logs")
async def listar_logs_auditoria(
    limit: int = Query(default=100, ge=1, le=500),
    current_root: User = Depends(get_current_root_user),
    db: AsyncSession = Depends(get_db)
):
    """Devuelve el historial completo de eventos de auditoría y seguridad."""
    query = select(UserLog).order_by(desc(UserLog.created_at)).limit(limit)
    res = await db.execute(query)
    logs = res.scalars().all()
    return {"logs": logs}
