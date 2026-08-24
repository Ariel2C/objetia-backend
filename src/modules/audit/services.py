from typing import Optional
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from src.modules.users.models import UserSession, UserLog
from src.common.timezone import ahora_argentina

class AuditService:
    @staticmethod
    async def registrar_log(
        db: AsyncSession,
        accion: str,
        usuario_id: Optional[int] = None,
        usuario_email: Optional[str] = None,
        detalles: Optional[str] = None,
        ip_address: Optional[str] = "127.0.0.1"
    ):
        """Registra un evento de auditoría en la tabla user_logs"""
        try:
            nuevo_log = UserLog(
                user_id=usuario_id,
                user_email=usuario_email,
                action=accion,
                details=detalles,
                ip_address=ip_address,
                created_at=ahora_argentina()
            )
            db.add(nuevo_log)
            await db.commit()
        except Exception as e:
            print(f"⚠️ Error al registrar log de auditoría: {e}")

    @staticmethod
    async def registrar_sesion(
        db: AsyncSession,
        usuario_id: int,
        ip_address: Optional[str] = "127.0.0.1",
        user_agent: Optional[str] = None
    ) -> UserSession:
        """Registra una nueva sesión activa en user_sessions desactivando las anteriores."""
        from src.config.database import AsyncSessionLocal
        from sqlmodel import select

        async with AsyncSessionLocal() as session:
            try:
                # Desactivar sesiones anteriores activas del mismo usuario
                stmt = select(UserSession).where(UserSession.user_id == usuario_id, UserSession.is_active == True)
                res = await session.execute(stmt)
                sesiones_anteriores = res.scalars().all()
                for s in sesiones_anteriores:
                    s.is_active = False
                    session.add(s)

                nueva_sesion = UserSession(
                    user_id=usuario_id,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    is_active=True,
                    created_at=ahora_argentina(),
                    last_activity=ahora_argentina()
                )
                session.add(nueva_sesion)
                await session.commit()
                await session.refresh(nueva_sesion)
                return nueva_sesion
            except Exception as e:
                print(f"⚠️ Error al registrar sesión de usuario: {e}")
                return None
