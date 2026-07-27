"""
Filtro anti-evasión de comisiones: detecta teléfonos, redes sociales y
frases que invitan a cerrar la compra fuera de Vamaar.
"""
from __future__ import annotations

import re
from typing import Optional


# --- Patrones de contacto ---

# Teléfonos AR / internacionales (con o sin +54, 15, 11, guiones, espacios, puntos)
_PATRON_TELEFONO = re.compile(
    r"""
    (?:
        (?:\+?54[\s.\-]*)?(?:9[\s.\-]*)?     # +54 9 opcional
        (?:15[\s.\-]*)?                      # 15 opcional (celular viejo)
        (?:11|2\d{1,3}|3\d{1,3})             # código de área (11 CABA / interior)
        [\s.\-]*
        \d{3,4}[\s.\-]*\d{3,4}               # número
      |
        \b\d{2,4}[\s.\-]?\d{3,4}[\s.\-]?\d{3,4}\b  # genérico 7–12 dígitos agrupados
      |
        \b\d{8,13}\b                         # bloque continuo de dígitos (celulares)
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

_PATRON_EMAIL = re.compile(
    r"\b[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}\b",
    re.IGNORECASE,
)

_PATRON_REDES = re.compile(
    r"""
    (?:
        \b(?:whats?\s*app|w(?:h)?atsapp|wpp|wsp|wa\.me|api\.whatsapp\.com)\b
      | \b(?:instagram|insta|ig)\b[\s:./@]*[a-z0-9._]{2,}
      | (?:instagram\.com|instagr\.am)/[a-z0-9._]+
      | \b(?:facebook|face\s*book|fb\.com|fb)\b[\s:./]*[a-z0-9.]+
      | (?:facebook\.com|fb\.com)/[a-z0-9.]+
      | \b(?:telegram|t\.me)\b[\s:/]*[a-z0-9_]+
      | t\.me/[a-z0-9_]+
      | \b(?:tiktok|twitter|x\.com|linkedin)\b[\s:./@]*[a-z0-9._]+
      | @[a-z0-9._]{3,}                       # handle tipo @usuario
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

_PATRON_FRASES_EVASION = re.compile(
    r"""
    (?:
        por\s+fuera
      | fuera\s+de\s+(?:la\s+)?(?:app|plataforma|p[aá]gina|web|vamaar|mercado)
      | sin\s+comisi[oó]n(?:es)?
      | evit(?:ar|á|a)\s+(?:la\s+)?comisi[oó]n
      | pago\s+(?:por\s+)?(?:afuera|fuera|transferencia\s+directa)
      | transfer(?:encia)?\s+directa
      | contact(?:ame|ame|anos|anos|o)\s+(?:por\s+)?(?:wpp|wsp|whats|ig|insta|mail|tel)
      | escrib(?:ime|ime|inos|inos|ime)\s+(?:al|por|a)
      | hablame\s+(?:al|por)
      | al\s+privado
      | por\s+privado
      | dm\s+(?:al|por|directo)?
      | mensaje\s+privado
      | n[uú]mero\s+(?:de\s+)?(?:tel[eé]fono|celular|cel|whats)
      | mi\s+(?:whats|wpp|wsp|cel|celular|tel[eé]fono)
      | te\s+paso\s+(?:mi\s+)?(?:n[uú]mero|wpp|wsp|whats|cel)
      | coordinamos\s+por\s+(?:fuera|wpp|whats|ig)
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Números que parecen medidas / códigos de producto (evitar falsos positivos leves)
_PATRON_MEDIDA = re.compile(
    r"^\d{1,4}([xX×]\d{1,4}){1,3}$|^\d{1,3}([.,]\d{1,2})?(cm|mm|m|kg|g|mts?)$",
    re.IGNORECASE,
)


def _es_falso_positivo_telefono(fragmento: str) -> bool:
    """Descarta medidas, precios simples o códigos cortos que no son teléfonos."""
    limpio = re.sub(r"[\s.\-]", "", fragmento)
    if _PATRON_MEDIDA.match(fragmento.strip()):
        return True
    # Solo dígitos: menos de 8 suele ser stock/código; más de 13 no es cel AR típico
    if limpio.isdigit() and (len(limpio) < 8 or len(limpio) > 13):
        return True
    # Años u otros números cortos con separadores
    if limpio.isdigit() and limpio.startswith("20") and len(limpio) == 4:
        return True
    return False


def detectar_contacto_externo(texto: str | None) -> Optional[str]:
    """
    Analiza un texto y, si encuentra indicios de contacto externo o evasión
    de comisiones, devuelve un motivo legible. Si está limpio, None.
    """
    if not texto or not texto.strip():
        return None

    # Emails
    if _PATRON_EMAIL.search(texto):
        return "Se detectó una dirección de email"

    # Redes / handles
    if _PATRON_REDES.search(texto):
        return "Se detectó una red social o handle de contacto externo"

    # Frases de evasión
    m_frase = _PATRON_FRASES_EVASION.search(texto)
    if m_frase:
        return f"Se detectó una invitación a comprar fuera de la plataforma («{m_frase.group(0).strip()}»)"

    # Teléfonos (con filtro de falsos positivos)
    for match in _PATRON_TELEFONO.finditer(texto):
        fragmento = match.group(0)
        if not _es_falso_positivo_telefono(fragmento):
            return "Se detectó un posible número de teléfono"

    return None


def texto_contiene_contacto_externo(texto: str | None) -> bool:
    return detectar_contacto_externo(texto) is not None


MENSAJE_BLOQUEO_CHAT = (
    "[MENSAJE BLOQUEADO: No se permiten datos de contacto externos. "
    "Completá la compra dentro de Vamaar.]"
)

MENSAJE_RECHAZO_PUBLICACION = (
    "Tu publicación no puede incluir teléfonos, redes sociales ni invitaciones "
    "a comprar por fuera de Vamaar. Sacá esos datos y volvé a intentar."
)
