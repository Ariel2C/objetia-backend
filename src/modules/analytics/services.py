import math
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select, func, col, desc
from sqlalchemy import text, and_, or_
from redis.asyncio import Redis

from src.modules.analytics.models import ProductAnalyticsEvent, SearchAnalyticsEvent
from src.modules.products.models import Product


class AnalyticsService:

    @staticmethod
    def calculate_relevance(views: int, favorites: int, sales: int, created_at: datetime) -> float:
        """
        Calcula el puntaje de relevancia ponderado:
        - Visualizaciones: peso 1.0
        - Favoritos: peso 5.0 (alto interés)
        - Ventas: peso 20.0 (conversión exitosa)
        - Bonus de frescura temporal: +15 puntos si tiene menos de 7 días, +8 si tiene menos de 14 días.
        """
        base_score = (views * 1.0) + (favorites * 5.0) + (sales * 20.0)
        
        dias_antiguedad = (datetime.utcnow() - created_at).total_seconds() / 86400.0
        recency_bonus = 0.0
        if dias_antiguedad <= 7.0:
            recency_bonus = 15.0 * max(0.0, (1.0 - (dias_antiguedad / 7.0)))
        elif dias_antiguedad <= 14.0:
            recency_bonus = 8.0 * max(0.0, (1.0 - ((dias_antiguedad - 7.0) / 7.0)))

        return round(base_score + recency_bonus, 2)

    @staticmethod
    async def record_product_event(
        db: AsyncSession,
        product_id: int,
        event_type: str,
        user_id: Optional[int] = None,
        visitor_hash: Optional[str] = None,
        redis_conn: Optional[Redis] = None
    ) -> bool:
        """
        Registra un evento de interacción y actualiza contadores y relevancia en Product.
        Deduplica 'view' en ventanas de 30 minutos por visitor_hash.
        """
        event_type = event_type.lower().strip()

        # Deduplicación para 'view'
        if event_type == "view" and visitor_hash:
            if redis_conn:
                cache_key = f"view_dedup:{product_id}:{visitor_hash}"
                is_new = await redis_conn.set(cache_key, "1", ex=1800, nx=True)
                if not is_new:
                    # Ya se contabilizó en los últimos 30 minutos
                    return False
            else:
                hace_30_min = datetime.utcnow() - timedelta(minutes=30)
                check_q = select(ProductAnalyticsEvent.id).where(
                    and_(
                        ProductAnalyticsEvent.product_id == product_id,
                        ProductAnalyticsEvent.visitor_hash == visitor_hash,
                        ProductAnalyticsEvent.event_type == "view",
                        ProductAnalyticsEvent.created_at >= hace_30_min
                    )
                ).limit(1)
                res = await db.execute(check_q)
                if res.scalar_one_or_none():
                    return False

        # Guardar evento en log analítico
        event = ProductAnalyticsEvent(
            product_id=product_id,
            user_id=user_id,
            event_type=event_type,
            visitor_hash=visitor_hash,
            created_at=datetime.utcnow()
        )
        db.add(event)

        # Actualizar producto
        prod_q = select(Product).where(Product.id == product_id)
        prod_res = await db.execute(prod_q)
        product = prod_res.scalar_one_or_none()
        if product:
            if event_type == "view":
                product.views_count = (product.views_count or 0) + 1
            elif event_type == "favorite_add":
                product.favorites_count = (product.favorites_count or 0) + 1
            elif event_type == "favorite_remove":
                product.favorites_count = max(0, (product.favorites_count or 0) - 1)
            elif event_type == "purchase":
                product.sales_count = (product.sales_count or 0) + 1

            product.relevance_score = AnalyticsService.calculate_relevance(
                views=product.views_count or 0,
                favorites=product.favorites_count or 0,
                sales=product.sales_count or 0,
                created_at=product.created_at or datetime.utcnow()
            )
            db.add(product)

        await db.commit()
        return True

    @staticmethod
    async def record_search_query(
        db: AsyncSession,
        query: str,
        user_id: Optional[int] = None,
        results_count: int = 0
    ):
        """Registra un término de búsqueda para analítica de tendencias."""
        cleaned_query = query.strip()
        if not cleaned_query or len(cleaned_query) < 2:
            return

        event = SearchAnalyticsEvent(
            query=cleaned_query[:255],
            user_id=user_id,
            results_count=results_count,
            created_at=datetime.utcnow()
        )
        db.add(event)
        await db.commit()

    @staticmethod
    async def get_seller_metrics(db: AsyncSession, seller_id: int) -> Dict[str, Any]:
        """Obtiene métricas completas para el vendedor logueado."""
        prods_q = select(Product).where(Product.seller_id == seller_id)
        prods_res = await db.execute(prods_q)
        products = prods_res.scalars().all()

        total_products = len(products)
        total_views = sum(p.views_count or 0 for p in products)
        total_favorites = sum(p.favorites_count or 0 for p in products)
        total_sales = sum(p.sales_count or 0 for p in products)

        conversion_rate = round((total_sales / total_views * 100), 2) if total_views > 0 else 0.0

        # Serie temporal de visualizaciones en los últimos 30 días
        hace_30_dias = datetime.utcnow() - timedelta(days=30)
        product_ids = [p.id for p in products]

        views_timeline: List[Dict[str, Any]] = []
        if product_ids:
            timeline_q = (
                select(
                    func.date_trunc('day', ProductAnalyticsEvent.created_at).label('dia'),
                    ProductAnalyticsEvent.event_type,
                    func.count(ProductAnalyticsEvent.id).label('total')
                )
                .where(
                    and_(
                        ProductAnalyticsEvent.product_id.in_(product_ids),
                        ProductAnalyticsEvent.event_type.in_(['view', 'favorite_add', 'purchase']),
                        ProductAnalyticsEvent.created_at >= hace_30_dias
                    )
                )
                .group_by('dia', ProductAnalyticsEvent.event_type)
                .order_by('dia')
            )
            timeline_res = await db.execute(timeline_q)
            rows = timeline_res.all()
            
            views_map = {}
            favs_map = {}
            sales_map = {}
            for row in rows:
                dia_str = row.dia.strftime('%Y-%m-%d') if hasattr(row.dia, 'strftime') else str(row.dia)[:10]
                if row.event_type == 'view':
                    views_map[dia_str] = row.total
                elif row.event_type == 'favorite_add':
                    favs_map[dia_str] = row.total
                elif row.event_type == 'purchase':
                    sales_map[dia_str] = row.total

            for i in range(30, -1, -1):
                d = (datetime.utcnow() - timedelta(days=i)).strftime('%Y-%m-%d')
                views_timeline.append({
                    "date": d,
                    "label": (datetime.utcnow() - timedelta(days=i)).strftime('%d/%m'),
                    "views": views_map.get(d, 0),
                    "favorites": favs_map.get(d, 0),
                    "sales": sales_map.get(d, 0)
                })

        # Top 5 publicaciones con mejor rendimiento
        top_products = sorted(products, key=lambda p: (p.relevance_score or 0.0), reverse=True)[:5]
        top_serialized = []
        for p in top_products:
            conv = round(((p.sales_count or 0) / (p.views_count or 1) * 100), 1) if (p.views_count or 0) > 0 else 0.0
            top_serialized.append({
                "id": p.id,
                "title": p.title,
                "category": p.category,
                "price": p.price,
                "views": p.views_count or 0,
                "favorites": p.favorites_count or 0,
                "sales": p.sales_count or 0,
                "conversion_rate": conv,
                "relevance_score": p.relevance_score or 0.0
            })

        return {
            "total_publications": total_products,
            "total_views": total_views,
            "total_favorites": total_favorites,
            "total_sales": total_sales,
            "conversion_rate": conversion_rate,
            "views_timeline": views_timeline,
            "top_products": top_serialized
        }

    @staticmethod
    async def get_admin_analytics(db: AsyncSession) -> Dict[str, Any]:
        """Obtiene métricas globales de audiencia, búsquedas y catálogo para administración."""
        now = datetime.utcnow()
        inicio_hoy = datetime(now.year, now.month, now.day)
        hace_7_dias = now - timedelta(days=7)
        hace_30_dias = now - timedelta(days=30)

        # 1. Visualizaciones totales
        total_views_all = (await db.execute(select(func.sum(Product.views_count)))).scalar() or 0
        total_favs_all = (await db.execute(select(func.sum(Product.favorites_count)))).scalar() or 0
        total_sales_all = (await db.execute(select(func.sum(Product.sales_count)))).scalar() or 0

        views_today_q = select(func.count(ProductAnalyticsEvent.id)).where(
            and_(ProductAnalyticsEvent.event_type == 'view', ProductAnalyticsEvent.created_at >= inicio_hoy)
        )
        views_today = (await db.execute(views_today_q)).scalar() or 0

        views_7d_q = select(func.count(ProductAnalyticsEvent.id)).where(
            and_(ProductAnalyticsEvent.event_type == 'view', ProductAnalyticsEvent.created_at >= hace_7_dias)
        )
        views_7d = (await db.execute(views_7d_q)).scalar() or 0

        views_30d_q = select(func.count(ProductAnalyticsEvent.id)).where(
            and_(ProductAnalyticsEvent.event_type == 'view', ProductAnalyticsEvent.created_at >= hace_30_dias)
        )
        views_30d = (await db.execute(views_30d_q)).scalar() or 0

        # 2. Búsquedas populares (Top 10 keywords)
        top_searches_q = (
            select(
                SearchAnalyticsEvent.query,
                func.count(SearchAnalyticsEvent.id).label('total_searches')
            )
            .group_by(SearchAnalyticsEvent.query)
            .order_by(desc('total_searches'))
            .limit(10)
        )
        top_searches_res = await db.execute(top_searches_q)
        top_searches = [{"query": row.query, "count": row.total_searches} for row in top_searches_res.all()]

        total_searches_q = select(func.count(SearchAnalyticsEvent.id))
        total_searches = (await db.execute(total_searches_q)).scalar() or 0

        # 3. Embudo de conversión (Funnel)
        carts_count_q = select(func.count(ProductAnalyticsEvent.id)).where(ProductAnalyticsEvent.event_type == 'cart_add')
        carts_count = (await db.execute(carts_count_q)).scalar() or 0

        funnel = {
            "views": total_views_all,
            "favorites": total_favs_all,
            "cart_additions": carts_count,
            "purchases": total_sales_all,
            "views_to_purchase_rate": round((total_sales_all / total_views_all * 100), 2) if total_views_all > 0 else 0.0
        }

        # 4. Distribución por Categoría
        cat_q = (
            select(
                Product.category,
                func.count(Product.id).label('product_count'),
                func.sum(Product.views_count).label('views'),
                func.sum(Product.favorites_count).label('favorites'),
                func.sum(Product.sales_count).label('sales')
            )
            .group_by(Product.category)
            .order_by(desc('views'))
        )
        cat_res = await db.execute(cat_q)
        categories = []
        for row in cat_res.all():
            categories.append({
                "category": row.category,
                "products": row.product_count,
                "views": row.views or 0,
                "favorites": row.favorites or 0,
                "sales": row.sales or 0
            })

        # 5. Top 10 Productos Más Relevantes en la Plataforma
        top_prods_q = (
            select(Product)
            .where(Product.stock > 0)
            .order_by(desc(Product.relevance_score), desc(Product.views_count))
            .limit(10)
        )
        top_prods_res = await db.execute(top_prods_q)
        top_products = [
            {
                "id": p.id,
                "title": p.title,
                "category": p.category,
                "price": p.price,
                "views": p.views_count or 0,
                "favorites": p.favorites_count or 0,
                "sales": p.sales_count or 0,
                "relevance_score": p.relevance_score or 0.0
            }
            for p in top_prods_res.scalars().all()
        ]

        # 6. Serie de tiempo global últimos 14 días
        hace_14_dias = now - timedelta(days=14)
        timeline_q = (
            select(
                func.date_trunc('day', ProductAnalyticsEvent.created_at).label('dia'),
                func.count(ProductAnalyticsEvent.id).label('views')
            )
            .where(
                and_(
                    ProductAnalyticsEvent.event_type == 'view',
                    ProductAnalyticsEvent.created_at >= hace_14_dias
                )
            )
            .group_by('dia')
            .order_by('dia')
        )
        timeline_res = await db.execute(timeline_q)
        date_map = {row.dia.strftime('%Y-%m-%d') if hasattr(row.dia, 'strftime') else str(row.dia)[:10]: row.views for row in timeline_res.all()}
        daily_traffic = []
        for i in range(14, -1, -1):
            d = (now - timedelta(days=i)).strftime('%Y-%m-%d')
            daily_traffic.append({
                "date": d,
                "label": (now - timedelta(days=i)).strftime('%d/%m'),
                "views": date_map.get(d, 0)
            })

        return {
            "traffic": {
                "views_today": views_today,
                "views_7d": views_7d,
                "views_30d": views_30d,
                "views_all_time": total_views_all,
                "daily_traffic": daily_traffic
            },
            "searches": {
                "total": total_searches,
                "top_keywords": top_searches
            },
            "funnel": funnel,
            "categories": categories,
            "top_products": top_products
        }
