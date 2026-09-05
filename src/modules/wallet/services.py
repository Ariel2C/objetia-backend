from datetime import datetime, timedelta
from typing import List, Optional
from fastapi import HTTPException, status
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.wallet.models import Wallet, WalletTransaction, WalletPayoutAccount, TransactionType, TransactionStatus

class WalletService:
    MARKETPLACE_COMMISSION_RATE = 0.10  # 10% de comisión por venta de decoración

    @classmethod
    async def procesar_ingreso_venta(
        cls, db: AsyncSession, seller_id: int, total_pago_comprador: float
    ) -> WalletTransaction:
        """
        Calcula la comisión de la página web, congela el saldo restante al vendedor
        y programa su fecha de liberación (Garantía de 7 días).
        """
        # 1. Obtener o inicializar la billetera del vendedor
        query_wallet = select(Wallet).where(Wallet.user_id == seller_id)
        result = await db.execute(query_wallet)
        wallet = result.scalar_one_or_none()

        if not wallet:
            wallet = Wallet(user_id=seller_id, balance_available=0.0, balance_frozen=0.0)
            db.add(wallet)
            await db.flush() # Obtiene el ID de la billetera antes de hacer commit

        # 2. Ingeniería Financiera: Calcular Comisiones e Ingreso Neto
        comision = total_pago_comprador * cls.MARKETPLACE_COMMISSION_RATE
        ingreso_neto_vendedor = total_pago_comprador - comision

        # 3. Actualizar balance en PostgreSQL (Sumar al saldo congelado)
        wallet.balance_frozen += ingreso_neto_vendedor
        wallet.updated_at = datetime.utcnow()
        db.add(wallet)

        # 4. Crear el registro en el libro mayor inmutable
        # Se programa la liberación exacta a los 7 días desde hoy
        fecha_liberacion = datetime.utcnow() + timedelta(days=7)
        
        nueva_transaccion = WalletTransaction(
            wallet_id=wallet.id,
            amount=ingreso_neto_vendedor,
            marketplace_commission=comision,
            type=TransactionType.CREDIT_SALE,
            status=TransactionStatus.FROZEN,
            available_at=fecha_liberacion
        )
        db.add(nueva_transaccion)
        return nueva_transaccion

    MONTO_MINIMO_RETIRO = 1000.0  # Retiro mínimo en ARS

    @classmethod
    async def procesar_pago_con_saldo(
        cls, db: AsyncSession, user_id: int, monto: float
    ) -> WalletTransaction:
        """
        Debita saldo de la billetera para pagar una compra DENTRO de la app.

        Regla de negocio: el dinero de una venta se puede usar para comprar en la web
        de inmediato (aunque esté "congelado" para retiro). Por eso acá se puede gastar
        balance_frozen + balance_available. El congelamiento de 7 días solo aplica
        a los retiros bancarios.

        Se consume primero el saldo congelado (el que todavía no se puede retirar)
        y recién después el disponible.
        """
        if monto <= 0:
            raise HTTPException(status_code=400, detail="El monto a pagar debe ser mayor a cero.")

        query_wallet = select(Wallet).where(Wallet.user_id == user_id)
        result = await db.execute(query_wallet)
        wallet = result.scalar_one_or_none()

        saldo_total = (wallet.balance_available + wallet.balance_frozen) if wallet else 0.0
        if not wallet or saldo_total < monto:
            raise HTTPException(
                status_code=400,
                detail=f"Saldo insuficiente para pagar con la billetera. Tenés ${saldo_total:,.2f} y la compra cuesta ${monto:,.2f}."
            )

        # Consumir primero el saldo congelado, después el disponible
        usar_congelado = min(wallet.balance_frozen, monto)
        wallet.balance_frozen -= usar_congelado
        wallet.balance_available -= (monto - usar_congelado)
        wallet.updated_at = datetime.utcnow()
        db.add(wallet)

        nueva_transaccion = WalletTransaction(
            wallet_id=wallet.id,
            amount=-monto,  # Negativo: salida de dinero
            marketplace_commission=0.0,
            type=TransactionType.INTERNAL_PURCHASE,
            status=TransactionStatus.COMPLETED,
            available_at=datetime.utcnow()
        )
        db.add(nueva_transaccion)
        return nueva_transaccion

    @classmethod
    async def procesar_retiro(
        cls, db: AsyncSession, user_id: int, monto: float, cbu_cvu: str
    ) -> WalletTransaction:
        """
        Debita el saldo DISPONIBLE del vendedor y registra un retiro hacia su CBU/CVU.
        Valida monto mínimo, saldo suficiente y formato del CBU/CVU.
        """
        # 1. Validaciones del monto
        if monto <= 0:
            raise HTTPException(status_code=400, detail="El monto a retirar debe ser mayor a cero.")
        if monto < cls.MONTO_MINIMO_RETIRO:
            raise HTTPException(
                status_code=400,
                detail=f"El monto mínimo de retiro es ${cls.MONTO_MINIMO_RETIRO:,.0f}."
            )

        # 2. Validación de CBU/CVU (22 dígitos) o alias (6-20 caracteres)
        cbu_limpio = (cbu_cvu or "").strip()
        es_cbu = cbu_limpio.isdigit() and len(cbu_limpio) == 22
        es_alias = 6 <= len(cbu_limpio) <= 20 and not cbu_limpio.isdigit()
        if not (es_cbu or es_alias):
            raise HTTPException(
                status_code=400,
                detail="Ingresá un CBU/CVU válido (22 dígitos) o un alias (6 a 20 caracteres)."
            )

        # 3. Obtener la billetera y validar saldo disponible
        query_wallet = select(Wallet).where(Wallet.user_id == user_id)
        result = await db.execute(query_wallet)
        wallet = result.scalar_one_or_none()

        if not wallet or wallet.balance_available < monto:
            disponible = wallet.balance_available if wallet else 0.0
            raise HTTPException(
                status_code=400,
                detail=f"Saldo disponible insuficiente. Disponible: ${disponible:,.2f}."
            )

        # 4. Debitar el saldo disponible
        wallet.balance_available -= monto
        wallet.updated_at = datetime.utcnow()
        db.add(wallet)

        # 5. Registrar la transacción de retiro en el libro mayor
        nueva_transaccion = WalletTransaction(
            wallet_id=wallet.id,
            amount=-monto,  # Negativo: es una salida de dinero
            marketplace_commission=0.0,
            type=TransactionType.DEBIT_WITHDRAW,
            status=TransactionStatus.COMPLETED,
            available_at=datetime.utcnow(),
            destination_account=cbu_limpio
        )
        db.add(nueva_transaccion)

        # 6. Guardar/actualizar CBU/CVU/Alias en base de datos para el usuario
        ahora = datetime.utcnow()
        query_cuenta = select(WalletPayoutAccount).where(
            WalletPayoutAccount.user_id == user_id,
            WalletPayoutAccount.cbu_cvu == cbu_limpio
        )
        res_cuenta = await db.execute(query_cuenta)
        cuenta_existente = res_cuenta.scalar_one_or_none()
        if cuenta_existente:
            cuenta_existente.last_used_at = ahora
            db.add(cuenta_existente)
        else:
            nueva_cuenta = WalletPayoutAccount(
                user_id=user_id,
                cbu_cvu=cbu_limpio,
                created_at=ahora,
                last_used_at=ahora
            )
            db.add(nueva_cuenta)

        return nueva_transaccion

    @classmethod
    async def obtener_cuentas_recientes(
        cls, db: AsyncSession, user_id: int, limit: int = 3
    ) -> List[str]:
        """Devuelve las últimas N cuentas (CBU/CVU/Alias) utilizadas por el usuario."""
        query = (
            select(WalletPayoutAccount.cbu_cvu)
            .where(WalletPayoutAccount.user_id == user_id)
            .order_by(WalletPayoutAccount.last_used_at.desc())
            .limit(limit)
        )
        res = await db.execute(query)
        return list(res.scalars().all())

    @classmethod
    async def liberar_saldos_vencidos_usuario(cls, db: AsyncSession, user_id: int) -> int:
        """
        Libera los saldos congelados VENCIDOS de un solo usuario.
        Se ejecuta automáticamente al consultar la billetera, así el usuario
        nunca ve dinero "en espera" cuya garantía de 7 días ya pasó,
        aunque el cron global no haya corrido.
        Devuelve la cantidad de transacciones liberadas (0 si no había nada vencido).
        """
        query_wallet = select(Wallet).where(Wallet.user_id == user_id)
        result = await db.execute(query_wallet)
        wallet = result.scalar_one_or_none()
        if not wallet:
            return 0

        ahora = datetime.utcnow()
        query_tx = select(WalletTransaction).where(
            WalletTransaction.wallet_id == wallet.id,
            WalletTransaction.status == TransactionStatus.FROZEN,
            WalletTransaction.available_at <= ahora
        )
        result_tx = await db.execute(query_tx)
        transacciones_vencidas = result_tx.scalars().all()

        contador = 0
        for tx in transacciones_vencidas:
            tx.status = TransactionStatus.AVAILABLE
            db.add(tx)

            # Puede haber gastado parte del congelado comprando en la app:
            # liberamos como máximo lo que quede congelado.
            monto_a_liberar = min(tx.amount, wallet.balance_frozen)
            if monto_a_liberar > 0:
                wallet.balance_frozen -= monto_a_liberar
                wallet.balance_available += monto_a_liberar
            contador += 1

        if contador > 0:
            wallet.updated_at = ahora
            db.add(wallet)
        return contador

    @classmethod
    async def ejecutar_cron_descongelar_saldos(cls, db: AsyncSession) -> int:
        """
        PROCESO EN SEGUNDO PLANO (Cron Job / Background Task).
        Busca todas las transacciones congeladas cuya fecha de garantía ya expiró,
        las marca como disponibles y mueve el dinero de caja congelada a disponible.
        """
        ahora = datetime.utcnow()
        
        # Buscar transacciones congeladas vencidas
        query = select(WalletTransaction).where(
            WalletTransaction.status == TransactionStatus.FROZEN,
            WalletTransaction.available_at <= ahora
        )
        result = await db.execute(query)
        transacciones_vencidas = result.scalars().all()

        contador = 0
        for tx in transacciones_vencidas:
            # Cambiar estado de la transacción
            tx.status = TransactionStatus.AVAILABLE
            db.add(tx)

            # Mover el dinero físicamente dentro de la billetera correspondiente
            query_wallet = select(Wallet).where(Wallet.id == tx.wallet_id)
            res_wallet = await db.execute(query_wallet)
            wallet = res_wallet.scalar_one()

            # El usuario puede haber gastado parte del saldo congelado comprando
            # dentro de la app: se libera como máximo lo que quede congelado
            # para no crear dinero de la nada.
            monto_a_liberar = min(tx.amount, wallet.balance_frozen)
            if monto_a_liberar > 0:
                wallet.balance_frozen -= monto_a_liberar
                wallet.balance_available += monto_a_liberar
                db.add(wallet)
            
            contador += 1
            
        return contador
