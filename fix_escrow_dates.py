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
        await db.commit()
        print("¡Listo! Todas las transacciones en garantía ahora esperan la entrega del paquete.")

if __name__ == "__main__":
    asyncio.run(fix())
