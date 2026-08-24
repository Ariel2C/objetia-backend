from typing import Optional, List
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from sqlmodel import select, or_, desc
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from src.config.database import get_db
from src.modules.users.models import User, UserRole, UserSession, UserLog
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
    role: UserRole

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

    return {"message": f"Sesión #{session_id} revocada exitosamente."}

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
