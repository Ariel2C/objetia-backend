import os
from dotenv import load_dotenv

load_dotenv()

# Si estás en desarrollo local sin Docker, usamos un simulador de Redis en memoria
if os.getenv("DEBUG") == "True":
    import fakeredis.aioredis as fakeredis
    print("--- INFO: Utilizando emulador de Redis en memoria para desarrollo local ---")
    redis_client = fakeredis.FakeRedis()
else:
    # Este código se activará automáticamente en el servidor de producción de AWS
    import aioredis
    redis_client = aioredis.from_url(
        f"redis://{os.getenv('REDIS_HOST')}:{os.getenv('REDIS_PORT')}", 
        encoding="utf-8", 
        decode_responses=True
    )

# Dependencia para usar la caché en tus módulos de FastAPI
async def get_redis():
    return redis_client
