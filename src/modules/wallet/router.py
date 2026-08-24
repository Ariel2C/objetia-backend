from fastapi import APIRouter, Depends, status, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
from src.config.database import get_db
from src.modules.wallet.models import Wallet
from src.modules.wallet.services import WalletService

# 🌟 IMPORTAMOS EL MIDDLEWARE Y REGLAS DE ROLES (RBAC)
from src.common.dependencies import get_current_user, RoleChecker
from src.modules.users.models import User, UserRole

router = APIRouter(prefix="/wallet", tags=["Billetera Virtual y Comisiones"])


class WithdrawRequest(BaseModel):
    amount: float
    cbu_cvu: str

# Definimos el validador estricto para rutas de negocio/administración
solo_financieros_o_admins = RoleChecker([UserRole.ADMIN, UserRole.ROOT])

@router.get("/balance/")
async def ver_mi_saldo(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user) # 🌟 RUTA BLINDADA (Solo ve su propio saldo)
):
    """Muestra los balances financieros extraídos del JWT verificado."""
    # Liberar automáticamente cualquier saldo cuya garantía de 7 días ya venció
    liberadas = await WalletService.liberar_saldos_vencidos_usuario(db, current_user.id)
    if liberadas > 0:
        await db.commit()

    query = select(Wallet).where(Wallet.user_id == current_user.id)
    result = await db.execute(query)
    wallet = result.scalar_one_or_none()

    if not wallet:
        return {"balance_available": 0.0, "balance_frozen": 0.0, "balance_spendable": 0.0}

    return {
        "balance_available": wallet.balance_available,   # Retirable al banco ya mismo
        "balance_frozen": wallet.balance_frozen,         # Retirable recién a los 7 días de la venta
        # Para comprar DENTRO de la app se puede usar todo el saldo (congelado + disponible)
        "balance_spendable": wallet.balance_available + wallet.balance_frozen
    }

@router.post("/withdraw/", status_code=status.HTTP_200_OK)
async def solicitar_retiro(
    payload: WithdrawRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Retira saldo disponible hacia el CBU/CVU del vendedor autenticado."""
    # Antes de validar el saldo, liberar lo que ya cumplió los 7 días de garantía
    await WalletService.liberar_saldos_vencidos_usuario(db, current_user.id)

    transaccion = await WalletService.procesar_retiro(
        db=db, user_id=current_user.id, monto=payload.amount, cbu_cvu=payload.cbu_cvu
    )
    await db.commit()

    # Devolver el nuevo saldo actualizado
    result = await db.execute(select(Wallet).where(Wallet.user_id == current_user.id))
    wallet = result.scalar_one_or_none()
    return {
        "mensaje": "Retiro procesado con éxito.",
        "monto_retirado": abs(transaccion.amount),
        "balance_available": wallet.balance_available if wallet else 0.0,
        "balance_frozen": wallet.balance_frozen if wallet else 0.0
    }


@router.get("/transactions/", status_code=status.HTTP_200_OK)
async def listar_transacciones(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Historial de movimientos de la billetera del usuario autenticado."""
    from src.modules.wallet.models import WalletTransaction
    # Mantener el historial coherente: liberar garantías vencidas antes de listar
    liberadas = await WalletService.liberar_saldos_vencidos_usuario(db, current_user.id)
    if liberadas > 0:
        await db.commit()

    wallet_res = await db.execute(select(Wallet).where(Wallet.user_id == current_user.id))
    wallet = wallet_res.scalar_one_or_none()
    if not wallet:
        return []
    tx_res = await db.execute(
        select(WalletTransaction)
        .where(WalletTransaction.wallet_id == wallet.id)
        .order_by(WalletTransaction.created_at.desc())
    )
    transacciones = tx_res.scalars().all()
    return [
        {
            "id": t.id,
            "amount": t.amount,
            "marketplace_commission": t.marketplace_commission,
            "type": t.type.value,
            "status": t.status.value,
            "available_at": t.available_at.isoformat(),
            "created_at": t.created_at.isoformat()
        }
        for t in transacciones
    ]


@router.post("/admin/cron-release/", status_code=status.HTTP_200_OK)
async def simular_cron_liberacion(
    db: AsyncSession = Depends(get_db),
    admin_verificado: User = Depends(solo_financieros_o_admins) # 🌟 ACCESO RESTRINGIDO POR ROL
):
    """Ruta protegida. Solo un Administrador Financiero puede ejecutar este disparador."""
    items_procesados = await WalletService.ejecutar_cron_descongelar_saldos(db)
    return {
        "mensaje": "Proceso de liberación completado con éxito por el departamento financiero.",
        "transacciones_liberadas": items_procesados
    }
