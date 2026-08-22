import asyncio
from src.config.database import AsyncSessionLocal
from sqlalchemy import text

async def main():
    print("--- Normalizando roles de usuarios en PostgreSQL a minusculas ---")
    async with AsyncSessionLocal() as db:
        await db.execute(text("UPDATE users SET role = 'client' WHERE role::text IN ('CLIENT', 'client');"))
        await db.execute(text("UPDATE users SET role = 'admin' WHERE role::text IN ('ADMIN', 'admin');"))
        await db.execute(text("UPDATE users SET role = 'financial' WHERE role::text IN ('FINANCIAL', 'financial');"))
        await db.execute(text("UPDATE users SET role = 'root' WHERE role::text IN ('ROOT', 'root');"))
        await db.commit()
        print("ROLES EN BD NORMALIZADOS A MINUSCULAS CORRECTAMENTE!")

if __name__ == "__main__":
    asyncio.run(main())
