import asyncio
from sqlalchemy import text
from src.config.database import engine

async def add_column():
    async with engine.begin() as conn:
        print("Agregando columna is_read a la tabla chat_messages...")
        try:
            await conn.execute(text("ALTER TABLE chat_messages ADD COLUMN is_read BOOLEAN NOT NULL DEFAULT FALSE;"))
            print("¡Columna is_read agregada con exito!")
        except Exception as e:
            print(f"La columna probablemente ya existe o hubo un error: {e}")
            
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(add_column())
