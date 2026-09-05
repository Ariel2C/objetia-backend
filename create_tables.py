import asyncio
import os
from dotenv import load_dotenv

# Importar el motor y la clase Base unificada
from src.config.database import engine, Base

# Importaciones obligatorias para registrar las tablas en los metadatos de Base
from src.modules.users.models import User, UserAddress
from src.modules.products.models import Product, ProductImage, Favorite
from src.modules.chat.models import ChatRoom, ChatMessage
from src.modules.wallet.models import Wallet, WalletTransaction
from src.modules.orders.models import Order, Shipment
from src.modules.cms.models import CarouselBanner, StoreBranding
from src.modules.analytics.models import ProductAnalyticsEvent, SearchAnalyticsEvent
load_dotenv()

async def init_db():
    print(f"🔄 Conectando a PostgreSQL local en: {os.getenv('DATABASE_URL')}")
    
    try:
        async with engine.begin() as conn:
            print("🛠️  Inyectando tablas de forma física...")
            
            # Leemos los metadatos acumulados a través de la herencia de Base
            await conn.run_sync(Base.metadata.create_all)
            
        print("✅ ¡Tablas creadas y confirmadas exitosamente en vamaar_db!")
        
    except Exception as e:
        print(f"❌ Error crítico durante la creación: {str(e)}")
    finally:
        await engine.dispose()

if __name__ == "__main__":
    asyncio.run(init_db())
