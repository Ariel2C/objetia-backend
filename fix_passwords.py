import asyncio
from sqlmodel import select
from src.config.database import AsyncSessionLocal, engine
from src.modules.users.models import User
from src.modules.auth.services import AuthService

async def reparar_contrasenas():
    print("🔄 Iniciando reparación criptográfica de contraseñas en vamaar_db...")
    
    async with AsyncSessionLocal() as session:
        try:
            # 1. Buscar a los 3 usuarios del Seed en tu PostgreSQL
            query = select(User).where(User.email.in_([
                'admin@vamaar.com', 
                'vendedor@vamaar.com', 
                'comprador@vamaar.com'
            ]))
            result = await session.execute(query)
            usuarios = result.scalars().all()

            if not usuarios:
                print("❌ No se encontraron los usuarios del Seed en la base de datos.")
                print("💡 Asegurate de haber corrido 'seed_data.py' primero.")
                return

            # 2. Generar el hash nativo puro desde Python
            print("🔐 Generando hash Bcrypt limpio para 'password123'...")
            hash_perfecto = AuthService.hash_password("password123")

            # 3. Aplicar el hash a cada usuario de forma segura
            for usuario in usuarios:
                usuario.hashed_password = hash_perfecto
                session.add(usuario)
                print(f"   -> Contraseña actualizada para: {usuario.email}")

            # 4. Commit Atómico en tu Postgres local
            await session.commit()
            print("✅ ¡Contraseñas sincronizadas con éxito mediante Python y Bcrypt!")

        except Exception as e:
            print(f"❌ Error durante la reparación: {str(e)}")
            await session.rollback()
        finally:
            await session.close()
            await engine.dispose()

if __name__ == "__main__":
    asyncio.run(reparar_contrasenas())
