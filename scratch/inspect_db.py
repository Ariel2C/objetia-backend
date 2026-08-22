import asyncio
from src.config.database import AsyncSessionLocal
from sqlalchemy import text

async def main():
    async with AsyncSessionLocal() as db:
        res = await db.execute(text("SELECT enumlabel FROM pg_enum JOIN pg_type ON pg_enum.enumtypid = pg_type.oid WHERE pg_type.typname = 'userrole';"))
        labels = [r[0] for r in res.fetchall()]
        print("PG ENUM USERROLE LABELS:", labels)

        res2 = await db.execute(text("SELECT DISTINCT role::text FROM users;"))
        roles_in_users = [r[0] for r in res2.fetchall()]
        print("ROLES CURRENTLY IN USERS TABLE:", roles_in_users)

if __name__ == "__main__":
    asyncio.run(main())
