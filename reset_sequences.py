import asyncio
import os
from sqlalchemy import text
from dotenv import load_dotenv

load_dotenv()

# Importamos la sesión del proyecto
from src.config.database import AsyncSessionLocal

async def reset_sequences():
    print("Resincronizando secuencias de PostgreSQL local...")
    tables = [
        "products", 
        "users", 
        "chat_rooms", 
        "chat_messages", 
        "carousel_banners", 
        "store_branding"
    ]
    
    async with AsyncSessionLocal() as session:
        for table in tables:
            try:
                seq_name = f"{table}_id_seq"
                # Resetea la secuencia al valor de MAX(id).
                # COALESCE previene errores si la tabla está vacía.
                q = text(f"""
                    SELECT setval(
                        pg_get_serial_sequence('{table}', 'id'), 
                        COALESCE((SELECT MAX(id) FROM {table}), 1), 
                        true
                    );
                """)
                await session.execute(q)
                print(f" OK: Secuencia para la tabla '{table}' resincronizada con exito.")
            except Exception as e:
                # Si no usa autoincremento serial, lo ignoramos
                print(f" INFO: Omitiendo tabla '{table}': {str(e)}")
                
        await session.commit()
    print("Secuencias de base de datos listas y sincronizadas.")

if __name__ == "__main__":
    asyncio.run(reset_sequences())
