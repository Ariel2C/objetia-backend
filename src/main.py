import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

from src.config.database import engine, Base
from src.config.redis import redis_client

from fastapi.staticfiles import StaticFiles

load_dotenv()

IS_DEBUG = os.getenv("DEBUG", "False").lower() in ("true", "1")

from sqlalchemy import text

from src.modules.analytics.models import ProductAnalyticsEvent, SearchAnalyticsEvent

@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"--- Iniciando {os.getenv('PROJECT_NAME', 'Marketplace')} ---")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(text("ALTER TABLE permissions ADD COLUMN IF NOT EXISTS target_section VARCHAR;"))
            await conn.execute(text("ALTER TABLE users ALTER COLUMN role TYPE VARCHAR USING role::VARCHAR;"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS phone VARCHAR;"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS street VARCHAR;"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS number VARCHAR;"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS floor_dept VARCHAR;"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS postal_code VARCHAR;"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS city VARCHAR;"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS province VARCHAR;"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS accepted_terms_at TIMESTAMP;"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS accepted_terms_version VARCHAR;"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS wants_newsletter BOOLEAN DEFAULT FALSE;"))
            await conn.execute(text("ALTER TABLE app_sections ADD COLUMN IF NOT EXISTS path VARCHAR;"))
            await conn.execute(text("ALTER TABLE products ADD COLUMN IF NOT EXISTS views_count INTEGER DEFAULT 0;"))
            await conn.execute(text("ALTER TABLE products ADD COLUMN IF NOT EXISTS favorites_count INTEGER DEFAULT 0;"))
            await conn.execute(text("ALTER TABLE products ADD COLUMN IF NOT EXISTS sales_count INTEGER DEFAULT 0;"))
            await conn.execute(text("ALTER TABLE products ADD COLUMN IF NOT EXISTS relevance_score DOUBLE PRECISION DEFAULT 0.0;"))
    except Exception as e:
        print(f"⚠️ Aviso al verificar/crear tablas en BD: {e}")
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

# Servir archivos estáticos subidos (Banners, fotos de productos, logos)
os.makedirs("uploads", exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# ==============================================================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)



@app.middleware("http")
async def catching_errors_middleware(request: Request, call_next):
    try:
        response = await call_next(request)
        return response
    except Exception as e:
        # Registrar el detalle en el servidor, pero NO exponerlo al cliente en producción
        print(f"❌ ERROR CRÍTICO NO CONTROLADO: {str(e)}")
        message = str(e) if IS_DEBUG else "Ocurrió un error interno. Intentá nuevamente más tarde."
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "*",
            "Access-Control-Allow-Headers": "*",
        }
        return JSONResponse(
            status_code=500,
            content={"error": "InternalServerError", "message": message},
            headers=headers
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
from src.modules.root.router import router as root_router
from src.modules.analytics.router import router as analytics_router

app.include_router(auth_router)
app.include_router(products_router)
app.include_router(chat_router)
app.include_router(cart_router)
app.include_router(wallet_router)
app.include_router(cms_router)
app.include_router(orders_router)
app.include_router(shipping_router)
app.include_router(root_router)
app.include_router(analytics_router)