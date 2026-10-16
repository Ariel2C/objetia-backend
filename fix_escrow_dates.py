import asyncio
from datetime import datetime, timedelta
from sqlmodel import select
from src.config.database import AsyncSessionLocal
from src.modules.wallet.models import WalletTransaction, TransactionStatus, TransactionType
from src.modules.orders.models import Order, OrderStatus
from src.modules.products.models import Product

async def fix():
    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(WalletTransaction).where(
                WalletTransaction.status == TransactionStatus.FROZEN,
                WalletTransaction.type == TransactionType.CREDIT_SALE
            )
        )
        txs = res.scalars().all()
        print(f"Encontradas {len(txs)} transacciones congeladas de venta.")
        for t in txs:
            t.available_at = datetime.utcnow() + timedelta(days=365)
            db.add(t)
            print(f"Transacción #{t.id} (monto: {t.amount}) actualizada a espera de entrega del paquete.")

        from sqlalchemy import text
        # 2. Limpiar residuos de texto Vamaar en historial de envíos
        await db.execute(text("UPDATE shipment_events SET location = 'Objetia' WHERE location ILIKE '%vamaar%'"))
        await db.execute(text("UPDATE shipment_events SET description = REPLACE(REPLACE(description, 'Vamaar', 'Objetia'), 'vamaar', 'objetia') WHERE description ILIKE '%vamaar%'"))
        await db.execute(text("UPDATE shipments SET recipient_name = REPLACE(REPLACE(recipient_name, 'Vamaar', 'Objetia'), 'vamaar', 'objetia') WHERE recipient_name ILIKE '%vamaar%'"))
        print("Historial de envíos y destinatarios purgados de menciones a Vamaar.")

        await db.commit()
        print("¡Listo! Todas las transacciones en garantía ahora esperan la entrega del paquete y los envíos quedaron limpios con Objetia.")

if __name__ == "__main__":
    asyncio.run(fix())
