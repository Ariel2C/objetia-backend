from datetime import datetime, timezone, timedelta

# Definición de la zona horaria oficial de Argentina: ART (UTC-3)
ARG_TZ = timezone(timedelta(hours=-3))

def ahora_argentina() -> datetime:
    """
    Devuelve la fecha y hora actual en la zona horaria de Argentina (America/Argentina/Buenos_Aires, UTC-3)
    como un objeto datetime ingenuo (naive) para guardar de forma limpia en PostgreSQL.
    """
    return datetime.now(ARG_TZ).replace(tzinfo=None)
