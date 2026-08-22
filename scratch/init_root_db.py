import asyncio
from src.config.database import AsyncSessionLocal, engine, Base
from sqlalchemy import text
from src.modules.users.models import User, UserRole, UserSession, UserLog
from src.modules.auth.services import AuthService

async def main():
    print("--- Inicializando tablas de auditoria y usuario ROOT ---")
    
    # Ejecutar ALTER TYPE con AUTOCOMMIT para permitir agregar valores al Enum
    async with engine.connect() as conn:
        autocommit_conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        try:
            await autocommit_conn.execute(text("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'root';"))
            print("✅ Valor 'root' añadido al Enum userrole en PostgreSQL")
        except Exception as e:
            print("Nota enum:", e)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    async with AsyncSessionLocal() as db:
        hashed_pwd = AuthService.hash_password("Goldgate1982")

        query = text("SELECT id, email FROM users WHERE email = 'root@objetia.com'")
        res = await db.execute(query)
        root_user = res.fetchone()

        if not root_user:
            nuevo_root = User(
                email="root@objetia.com",
                full_name="Programador Root",
                hashed_password=hashed_pwd,
                role=UserRole.ROOT,
                is_active=True
            )
            db.add(nuevo_root)
            await db.commit()
            print("ROOT USER CREATED SUCCESSFULLY: root@objetia.com")
        else:
            await db.execute(
                text("UPDATE users SET role = 'root', hashed_password = :hp WHERE email = 'root@objetia.com'"),
                {"hp": hashed_pwd}
            )
            await db.commit()
            print("ROOT USER UPDATED SUCCESSFULLY: root@objetia.com")

if __name__ == "__main__":
    asyncio.run(main())
