import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

from src.config.database import engine
from src.config.redis import redis_client

load_dotenv()

@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"--- Iniciando {os.getenv('PROJECT_NAME', 'Marketplace')} ---")
    yield
    print("--- Apagando el servidor de forma segura ---")
    await engine.dispose()
    await redis_client.close()

app = FastAPI(
    title=os.getenv("PROJECT_NAME", "Marketplace Enterprise API"),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if os.getenv("DEBUG") == "True" else None,
    redoc_url="/redoc" if os.getenv("DEBUG") == "True" else None
)

# ==============================================================================
# CONFIGURACIÓN DE CORS
# En desarrollo (DEBUG=True) se permiten los orígenes locales por regex.
# En producción se usan únicamente los orígenes de CORS_ALLOWED_ORIGINS.
# ==============================================================================
IS_DEBUG = os.getenv("DEBUG") == "True"

origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://lvh.me:3000",
    "http://lvh.me:8000",
]

# Orígenes de producción declarados por variable de entorno (coma-separados)
extra_origins = [o.strip() for o in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()]
origins.extend(extra_origins)

cors_kwargs = dict(
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# El regex permisivo (cualquier localhost / IP de red local) solo en desarrollo
if IS_DEBUG:
    cors_kwargs["allow_origin_regex"] = r"https?://(localhost|127\.0\.0\.1|lvh\.me|192\.168\.\d+\.\d+)(:\d+)?"

app.add_middleware(CORSMiddleware, **cors_kwargs)



@app.middleware("http")
async def catching_errors_middleware(request: Request, call_next):
    try:
        response = await call_next(request)
        return response
    except Exception as e:
        # Registrar el detalle en el servidor, pero NO exponerlo al cliente en producción
        print(f"❌ ERROR CRÍTICO NO CONTROLADO: {str(e)}")
        message = str(e) if IS_DEBUG else "Ocurrió un error interno. Intentá nuevamente más tarde."
        return JSONResponse(
            status_code=500,
            content={"error": "InternalServerError", "message": message}
        )

@app.get("/health", tags=["Infraestructura"])
async def health_check():
    return {"status": "healthy", "database": "connected"}

# Inclusión de Enrutadores de tus Módulos
from src.modules.auth.router import router as auth_router
from src.modules.products.router import router as products_router
from src.modules.chat.router import router as chat_router
from src.modules.cart.router import router as cart_router
from src.modules.wallet.router import router as wallet_router
from src.modules.cms.router import router as cms_router
from src.modules.orders.router import router as orders_router
from src.modules.shipping.router import router as shipping_router

app.include_router(auth_router)
app.include_router(products_router)
app.include_router(chat_router)
app.include_router(cart_router)
app.include_router(wallet_router)
app.include_router(cms_router)
app.include_router(orders_router)
app.include_router(shipping_router)