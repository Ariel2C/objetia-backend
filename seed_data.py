import asyncio
import os
from sqlalchemy import text
from dotenv import load_dotenv

# Importamos únicamente el motor asíncrono nativo para conectarnos a tu PostgreSQL
from src.config.database import engine

load_dotenv()

async def database_seed():
    print("Iniciando inyeccion masiva limpia mediante SQL Nativo en vamaar_db...")
    
    # Hash pre-calculado real de Bcrypt para la contraseña 'password123'.
    hash_password_fijo = "$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36Xf0b5BwJ/4B87e9v/2V9."
    
    # 🌟 CORRECCIÓN MAESTRA: Cambiamos 'client' y 'admin' a 'CLIENT' y 'ADMIN' en mayúsculas.
    # También ajustamos 'USED' y 'NEW' junto a 'APPROVED' para acoplarse al estándar de Postgres.
    sql_insert_usuarios = text("""
        INSERT INTO users (id, email, full_name, hashed_password, role, reputation_score, total_sales_count, is_active, created_at, updated_at)
        VALUES 
        (1, 'comprador@vamaar.com', 'Juan Pérez (Cliente C2C)', :hp, 'CLIENT'::userrole, 5.0, 0, true, now(), now()),
        (2, 'vendedor@vamaar.com', 'Muebles Vintage SRL (Vendedor)', :hp, 'CLIENT'::userrole, 4.8, 15, true, now(), now()),
        (3, 'admin@vamaar.com', 'Sofía Contadores (Admin General)', :hp, 'ADMIN'::userrole, 5.0, 0, true, now(), now())
        ON CONFLICT (email) DO NOTHING;
    """)

    sql_insert_branding = text("""
        INSERT INTO store_branding (
            id, brand_name, primary_color_hex, secondary_color_hex, 
            background_color_hex, input_text_color_hex, navbar_color_hex, 
            section_title_color_hex, catalog_link_color_hex, brand_font_family, brand_font_size, updated_at
        )
        VALUES (
            1, 'Objetia Decoración Premium', '#2C3E50', '#D4AF37', 
            '#FAFAFA', '#111827', '#FFFFFF', '#111827', '#3B82F6', 'Outfit', 'text-base', now()
        )
        ON CONFLICT (id) DO NOTHING;
    """)

    sql_insert_productos = text("""
        INSERT INTO products (
            id, title, description, price, stock, condition, category, 
            moderation_status, ai_moderation_notes, weight_kg, height_cm, width_cm, length_cm, seller_id, created_at, updated_at
        )
        VALUES 
        (1, 'Sillón Chesterfield de Cuero Usado', 'Espectacular sillón clásico tapizado en cuero legítimo. Posee un desgaste natural elegante.', 450.0, 1, 'USED'::productcondition, 'Sillones', 'APPROVED'::moderationstatus, 'Aprobación simulada por Seed', 15.0, 85.0, 90.0, 120.0, 2, now(), now()),
        (2, 'Lámpara de Pie Industrial de Cobre', 'Lámpara de iluminación interior hecha a mano. Diseño minimalista moderno.', 120.0, 5, 'NEW'::productcondition, 'Iluminación', 'APPROVED'::moderationstatus, 'Aprobación simulada por Seed', 3.5, 150.0, 40.0, 40.0, 2, now(), now())
        ON CONFLICT (id) DO NOTHING;
    """)

    # Abrimos una transacción limpia al motor de Postgres local
    async with engine.begin() as conn:
        try:
            print("Insertando cuentas con casteo de ENUM en mayusculas...")
            await conn.execute(sql_insert_usuarios, {"hp": hash_password_fijo})
            
            print("Inicializando marca del CMS...")
            await conn.execute(sql_insert_branding)
            
            print("Inyectando articulos de decoracion en el catalogo...")
            await conn.execute(sql_insert_productos)
            
            print("Sincronizando secuencias seriales (ID)...")
            tables = ["products", "users", "chat_rooms", "chat_messages", "carousel_banners", "store_branding"]
            for table in tables:
                try:
                    await conn.execute(text(f"""
                        SELECT setval(
                            pg_get_serial_sequence('{table}', 'id'), 
                            COALESCE((SELECT MAX(id) FROM {table}), 1), 
                            true
                        );
                    """))
                except Exception as e:
                    pass # Omitimos si la tabla no usa secuencias seriales en Postgres
            
            print("Transaccion confirmada y escrita con exito en vamaar_db!")
            print("\nDatos de prueba listos para usar:")
            print("   - admin@vamaar.com  | password123")
            print("   - vendedor@vamaar.com | password123")
            print("   - comprador@vamaar.com | password123")
            
        except Exception as e:
            print(f"Error critico en el motor de PostgreSQL: {str(e)}")
        finally:
            await engine.dispose()

if __name__ == "__main__":
    asyncio.run(database_seed())
