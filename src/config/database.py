import os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlmodel import SQLModel
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("ERROR: La variable DATABASE_URL no está configurada en el archivo .env")

# Crear el motor asíncrono para PostgreSQL local
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    future=True,
    pool_size=20,
    max_overflow=10
)

# 🌟 CORRECCIÓN CRÍTICA: Se agrega expire_on_commit=False para evitar el error f405
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # 🧠 Conserva los IDs y campos legibles tras hacer commits
    autocommit=False,
    autoflush=False
)

class Base(SQLModel):
    pass

async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
