from typing import Optional
from datetime import datetime
from sqlmodel import Field
from pydantic import BaseModel
from src.config.database import Base

# ==============================================================================
# TABLA: BANNERS DEL CARRUSEL (Carrusel dinámico de la página de inicio)
# ==============================================================================
class CarouselBanner(Base, table=True):
    __tablename__ = "carousel_banners"

    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    title: Optional[str] = Field(default=None)         # Ej: "Colección Sillones de Invierno"
    subtitle: Optional[str] = Field(default=None)      # Subtítulo descriptivo opcional
    link_url: Optional[str] = Field(default=None)      # Redirección al hacer clic (ej: /category/sillones)
    cloudfront_url: str = Field(nullable=False)         # URL optimizada desde AWS CDN (PC)
    mobile_cloudfront_url: Optional[str] = Field(default=None) # URL optimizada para celular
    orden: int = Field(default=0, index=True)          # Controla el orden visual (0 es primero)
    is_active: bool = Field(default=True, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ==============================================================================
# TABLA: PERSONALIZACIÓN DE MARCA (Colores Hexadecimales y Logo)
# ==============================================================================
class StoreBranding(Base, table=True):
    __tablename__ = "store_branding"

    id: Optional[int] = Field(default=None, primary_key=True)
    brand_name: str = Field(default="Mi Marketplace", nullable=False)
    logo_cloudfront_url: Optional[str] = Field(default=None) # Logo cargado en S3
    
    # Sistema de Inyección de Colores para el Diseñador del Front-End (Tailwind CSS o CSS puro)
    primary_color_hex: str = Field(default="#1E3A8A", nullable=False)   # Azul corporativo por defecto
    secondary_color_hex: str = Field(default="#F59E0B", nullable=False) # Dorado/Amarillo de acento
    background_color_hex: str = Field(default="#FFFFFF", nullable=False)
    input_text_color_hex: str = Field(default="#111827", nullable=False) # Color de texto de textbox
    navbar_color_hex: str = Field(default="#FFFFFF", nullable=False) # Color de fondo del navbar
    section_title_color_hex: str = Field(default="#111827", nullable=False) # Color del título de secciones
    catalog_link_color_hex: str = Field(default="#3B82F6", nullable=False) # Color del enlace de catálogo
    
    # Propiedades de fuentes
    brand_font_family: str = Field(default="Outfit", nullable=False)
    brand_font_size: str = Field(default="1.5rem", nullable=False)
    
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class LogoHistory(Base, table=True):
    __tablename__ = "logo_history"
    id: Optional[int] = Field(default=None, primary_key=True)
    logo_url: str = Field(nullable=False)
    label: Optional[str] = Field(default="Logo sin nombre")
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ==============================================================================
# SCHEMAS DE PYDANTIC (Validación para actualizar la marca)
# ==============================================================================
class BrandingUpdate(BaseModel):
    brand_name: str
    primary_color_hex: str
    secondary_color_hex: str
    background_color_hex: str
    input_text_color_hex: str
    navbar_color_hex: str
    section_title_color_hex: str
    catalog_link_color_hex: str
    brand_font_family: str
    brand_font_size: str


# ==============================================================================
# TABLA: SECCIONES DE LA PÁGINA DE INICIO (Carruseles configurables)
# ==============================================================================
class HomepageSection(Base, table=True):
    __tablename__ = "homepage_sections"

    id: Optional[int] = Field(default=None, primary_key=True)
    title: str = Field(default="Destacados", nullable=False)
    category_filter: Optional[str] = Field(default=None)  # None = "Todos", o una categoría específica (ej: "Sillones")
    orden: int = Field(default=0, index=True)
    is_active: bool = Field(default=True, index=True)

