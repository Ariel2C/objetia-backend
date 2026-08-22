import asyncio
from src.config.database import engine, AsyncSessionLocal
from sqlalchemy import text

async def main():
    print("--- Normalizando Enum userrole y filas de la base de datos ---")
    async with engine.connect() as conn:
        autocommit_conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        for val in ['client', 'admin', 'financial', 'root']:
            try:
                await autocommit_conn.execute(text(f"ALTER TYPE userrole ADD VALUE IF NOT EXISTS '{val}';"))
                print(f"Enum value '{val}' added to userrole")
            except Exception as e:
                print(f"Note for '{val}':", e)

    async with AsyncSessionLocal() as db:
        await db.execute(text("UPDATE users SET role = 'client' WHERE role::text = 'CLIENT';"))
        await db.execute(text("UPDATE users SET role = 'admin' WHERE role::text = 'ADMIN';"))
        await db.execute(text("UPDATE users SET role = 'financial' WHERE role::text = 'FINANCIAL';"))
        await db.commit()
        print("MIGRATION COMPLETED SUCCESSFULLY!")

if __name__ == "__main__":
    asyncio.run(main())
