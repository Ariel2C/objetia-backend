from typing import Dict, List
from fastapi import WebSocket
from src.common.contact_filter import (
    detectar_contacto_externo,
    MENSAJE_BLOQUEO_CHAT,
)

class ConnectionManager:
    def __init__(self):
        # Estructura en memoria: { room_id: [lista_de_websockets_activos] }
        self.active_connections: Dict[int, List[WebSocket]] = {}

    async def connect(self, room_id: int, websocket: WebSocket):
        """Acepta la conexión y asigna al usuario a una sala de chat específica."""
        await websocket.accept()
        if room_id not in self.active_connections:
            self.active_connections[room_id] = []
        self.active_connections[room_id].append(websocket)

    def disconnect(self, room_id: int, websocket: WebSocket):
        """Remueve la conexión cuando el usuario cierra la pestaña o pierde internet."""
        if room_id in self.active_connections:
            self.active_connections[room_id].remove(websocket)
            if not self.active_connections[room_id]:
                del self.active_connections[room_id]

    async def send_personal_message(self, message: str, websocket: WebSocket):
        """Envía un mensaje privado a una sola conexión."""
        await websocket.send_text(message)

    async def broadcast(self, room_id: int, message_json: dict):
        """Envía el mensaje en tiempo real a todos los miembros dentro de la sala."""
        if room_id in self.active_connections:
            for connection in self.active_connections[room_id]:
                await connection.send_json(message_json)

    @staticmethod
    def filtrar_contenido_sensible(texto: str) -> tuple[str, bool]:
        """
        Filtro de Seguridad Corporativo. Detecta números telefónicos, redes
        sociales y frases de compra por fuera para proteger las comisiones.
        Retorna el texto procesado y un booleano indicando si fue bloqueado.
        """
        if detectar_contacto_externo(texto):
            return MENSAJE_BLOQUEO_CHAT, True
        return texto, False

# Instancia única global del gestor para toda la aplicación
manager = ConnectionManager()
