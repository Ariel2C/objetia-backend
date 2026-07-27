import os
import random
from typing import Optional, List, Dict, Any

class CorreoArgentinoClient:
    """
    Cliente unificado para la API de Correo Argentino.
    Soporta modo pruebas (simulado) y modo real de producción mediante variables de entorno.
    """
    
    def __init__(self):
        self.mock_mode = os.getenv("CORREO_ARGENTINO_MOCK", "true").lower() == "true"
        self.client_id = os.getenv("CORREO_ARGENTINO_CLIENT_ID", "")
        self.client_secret = os.getenv("CORREO_ARGENTINO_CLIENT_SECRET", "")
        self.contract_code = os.getenv("CORREO_ARGENTINO_CONTRACT_CODE", "")

        # Si se pidió modo real (MOCK=false) pero faltan credenciales, fallar en vez de
        # simular en silencio (evita creer que se opera contra la API real cuando no es así).
        if not self.mock_mode and not self.client_id:
            raise RuntimeError(
                "Correo Argentino en modo real pero falta CORREO_ARGENTINO_CLIENT_ID. "
                "Configurá las credenciales o poné CORREO_ARGENTINO_MOCK=true."
            )

    def _usar_api_real(self) -> bool:
        return not self.mock_mode

    def cotizar_envio(
        self, 
        cp_origen: str, 
        cp_destino: str, 
        peso_kg: float, 
        largo_cm: float, 
        ancho_cm: float, 
        alto_cm: float
    ) -> float:
        """
        Calcula el costo del envío en base a la distancia y el peso volumétrico.
        """
        if self._usar_api_real():
            # Aquí iría el llamado HTTP real a: POST https://api.correoargentino.com.ar/v1/tarifas
            # return self._cotizar_real(cp_origen, cp_destino, peso_kg, largo_cm, ancho_cm, alto_cm)
            raise NotImplementedError("La integración real de cotización de Correo Argentino aún no está implementada.")
            
        # --- Lógica de Simulación (Mock) ---
        # 1. Costo base mínimo de logística en Pesos Argentinos (ARS)
        costo_base = 4500.0
        
        # 2. Recargo por distancia (Códigos Postales distintos)
        es_diferente_provincia = cp_origen[:2] != cp_destino[:2]
        recargo_distancia = 3500.0 if es_diferente_provincia else 1200.0
        
        # 3. Recargo por peso y volumen (Peso volumétrico)
        peso_volumetrico = (largo_cm * ancho_cm * alto_cm) / 5000.0
        peso_a_cobrar = max(peso_kg, peso_volumetrico)
        recargo_peso = peso_a_cobrar * 800.0 # $800 ARS por kg o volumen equivalente
        
        costo_total = costo_base + recargo_distancia + recargo_peso
        
        # Redondear para estética en Pesos
        return round(costo_total, 2)

    def crear_envio(
        self,
        order_id: int,
        recipient_name: str,
        street: str,
        number: str,
        floor_dept: Optional[str],
        postal_code: str,
        city: str,
        province: str,
        weight_kg: float,
        largo_cm: float,
        ancho_cm: float,
        alto_cm: float
    ) -> Dict[str, Any]:
        """
        Registra el despacho en el correo y retorna el tracking_number y la etiqueta.
        """
        if self._usar_api_real():
            # Aquí iría el llamado HTTP real a: POST https://api.correoargentino.com.ar/v1/envios
            # return self._crear_envio_real(...)
            raise NotImplementedError("La integración real de despacho de Correo Argentino aún no está implementada.")
            
        # --- Lógica de Simulación (Mock) ---
        # Generar número de guía oficial: CP + 9 dígitos + AR
        tracking_number = f"CP{random.randint(100000000, 999999999)}AR"
        
        # URL de la etiqueta simulada: apunta al propio backend, que genera un PDF de prueba
        public_api = os.getenv("PUBLIC_API_URL", "http://localhost:8000").rstrip("/")
        label_url = f"{public_api}/orders/label/{tracking_number}"
        
        return {
            "tracking_number": tracking_number,
            "shipping_label_url": label_url,
            "status": "LABEL_GENERATED"
        }

    def obtener_tracking(self, tracking_number: str) -> List[Dict[str, Any]]:
        """
        Consulta el estado y movimientos del envío.
        """
        if self._usar_api_real():
            # Aquí iría el llamado HTTP real a: GET https://api.correoargentino.com.ar/v1/tracking/{tracking_number}
            raise NotImplementedError("La integración real de tracking de Correo Argentino aún no está implementada.")
            
        # Simular cronología en base al tracking
        seed = int(''.join(filter(str.isdigit, tracking_number))) if any(c.isdigit() for c in tracking_number) else 123
        is_delivered = seed % 3 == 0
        is_shipped = seed % 3 == 1
        
        events = [
            {
                "status": "Pedido confirmado",
                "date": "26/05/2026 - 10:45",
                "location": "Vamaar Platform"
            },
            {
                "status": "En preparación",
                "date": "27/05/2026 - 14:10",
                "location": "Almacén Central"
            }
        ]
        if is_shipped or is_delivered:
            events.append({
                "status": "Enviado",
                "date": "30/05/2026 - 09:30",
                "location": "Sucursal Origen Correo Argentino"
            })
        if is_delivered:
            events.append({
                "status": "Entregado",
                "date": "03/06/2026 - 16:20",
                "location": "Domicilio del Comprador"
            })
            
        return events
