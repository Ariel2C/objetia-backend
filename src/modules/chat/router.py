from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, HTTPException, status, Query
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime

from src.config.database import get_db, AsyncSessionLocal
from src.modules.chat.manager import manager
from src.modules.chat.models import ChatRoom, ChatMessage
from src.common.dependencies import get_current_user, get_user_from_token
from src.modules.users.models import User

router = APIRouter(prefix="/chat", tags=["Mensajería y Soporte en Tiempo Real"])

@router.post("/rooms/get-or-create/", response_model=dict)
async def obtener_o_crear_sala(
    product_id: int,
    seller_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Crea o recupera una sala de chat. El comprador es SIEMPRE el usuario autenticado;
    el vendedor se valida contra el dueño real del producto.
    """
    # Validar que el vendedor informado sea realmente el dueño del producto
    from src.modules.products.models import Product
    prod_res = await db.execute(select(Product).where(Product.id == product_id))
    product = prod_res.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Producto no encontrado.")
    if product.seller_id != seller_id:
        raise HTTPException(status_code=400, detail="El vendedor no corresponde a este producto.")

    buyer_id = current_user.id
    if buyer_id == seller_id:
        raise HTTPException(status_code=400, detail="No puedes abrir un chat contigo mismo.")

    query = select(ChatRoom).where(
        ChatRoom.product_id == product_id,
        ChatRoom.buyer_id == buyer_id,
        ChatRoom.seller_id == seller_id
    )
    result = await db.execute(query)
    room = result.scalar_one_or_none()

    if not room:
        room = ChatRoom(product_id=product_id, buyer_id=buyer_id, seller_id=seller_id)
        db.add(room)
        await db.commit()
        await db.refresh(room)

    return {"room_id": room.id, "mensaje": "Sala lista para transmisión por WebSocket."}


# ==============================================================================
# CANAL WEBSOCKET EN TIEMPO REAL
# ==============================================================================
@router.websocket("/ws/{room_id}")
async def websocket_chat_endpoint(
    websocket: WebSocket,
    room_id: int,
    token: str = Query(...)
):
    """
    Túnel WebSocket bidireccional. La identidad del emisor se deriva del token JWT
    (query param), no de la URL, para impedir la suplantación de identidad.
    """
    # 1. Autenticar por token y resolver el usuario emisor
    async with AsyncSessionLocal() as db:
        user = await get_user_from_token(token, db)
        if user is None:
            await websocket.close(code=4001)
            return
        sender_id = user.id

        # 2. Verificar que el usuario pertenezca a la sala
        room_res = await db.execute(select(ChatRoom).where(ChatRoom.id == room_id))
        room = room_res.scalar_one_or_none()
        if not room or (room.buyer_id != sender_id and room.seller_id != sender_id):
            await websocket.close(code=4003)
            return

    await manager.connect(room_id, websocket)

    try:
        while True:
            texto_entrante = await websocket.receive_text()
            texto_filtrado, fue_moderado = manager.filtrar_contenido_sensible(texto_entrante)

            # Cada mensaje se persiste con su propia sesión (no reusar una sesión de larga vida)
            async with AsyncSessionLocal() as db:
                nuevo_mensaje = ChatMessage(
                    room_id=room_id,
                    sender_id=sender_id,
                    message=texto_filtrado,
                    was_moderated=fue_moderado
                )
                db.add(nuevo_mensaje)
                await db.commit()
                await db.refresh(nuevo_mensaje)

                payload_mensaje = {
                    "id": nuevo_mensaje.id,
                    "sender_id": sender_id,
                    "message": texto_filtrado,
                    "was_moderated": fue_moderado,
                    "is_deleted": False,
                    "timestamp": str(nuevo_mensaje.created_at)
                }

            await manager.broadcast(room_id, payload_mensaje)

    except WebSocketDisconnect:
        manager.disconnect(room_id, websocket)


# ==============================================================================
# ENDPOINTS ADICIONALES DE HISTORIAL Y SALAS
# ==============================================================================
from src.modules.products.models import Product
from sqlalchemy.orm import aliased
from sqlalchemy import text


async def _verificar_pertenencia_sala(db: AsyncSession, room_id: int, user_id: int) -> ChatRoom:
    """Devuelve la sala si el usuario participa en ella; si no, lanza 404/403."""
    room_res = await db.execute(select(ChatRoom).where(ChatRoom.id == room_id))
    room = room_res.scalar_one_or_none()
    if not room:
        raise HTTPException(status_code=404, detail="Sala de chat no encontrada.")
    if room.buyer_id != user_id and room.seller_id != user_id:
        raise HTTPException(status_code=403, detail="Acceso denegado: No participas en esta conversación.")
    return room


@router.get("/rooms/", response_model=list)
async def listar_salas_usuario(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Lista las salas del usuario autenticado (comprador o vendedor)."""
    user_id = current_user.id
    Buyer = aliased(User)
    Seller = aliased(User)
    query = (
        select(ChatRoom, Product.title, Buyer.full_name, Seller.full_name)
        .join(Product, ChatRoom.product_id == Product.id)
        .join(Buyer, ChatRoom.buyer_id == Buyer.id)
        .join(Seller, ChatRoom.seller_id == Seller.id)
        .where((ChatRoom.buyer_id == user_id) | (ChatRoom.seller_id == user_id))
    )
    result = await db.execute(query)
    rooms_data = []
    for room, product_title, buyer_name, seller_name in result.all():
        last_msg_query = (
            select(ChatMessage)
            .where(ChatMessage.room_id == room.id)
            .order_by(ChatMessage.created_at.desc())
            .limit(1)
        )
        last_msg_res = await db.execute(last_msg_query)
        last_msg = last_msg_res.scalar_one_or_none()
        last_message_time = str(last_msg.created_at) if last_msg else None

        unread_room_query = text("""
            SELECT COUNT(id) FROM chat_messages 
            WHERE room_id = :room_id 
              AND sender_id != :user_id 
              AND is_read = FALSE
        """)
        unread_room_res = await db.execute(unread_room_query, {"room_id": room.id, "user_id": user_id})
        unread_room_count = unread_room_res.scalar() or 0

        rooms_data.append({
            "id": room.id,
            "product_id": room.product_id,
            "product_title": product_title,
            "buyer_id": room.buyer_id,
            "seller_id": room.seller_id,
            "buyer_name": buyer_name,
            "seller_name": seller_name,
            "created_at": room.created_at,
            "last_message_time": last_message_time,
            "unread_count": unread_room_count
        })
    return rooms_data


@router.get("/rooms/{room_id}/", response_model=dict)
async def obtener_detalle_sala(
    room_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Detalle de una sala específica (solo si el usuario participa en ella)."""
    await _verificar_pertenencia_sala(db, room_id, current_user.id)

    Buyer = aliased(User)
    Seller = aliased(User)
    query = (
        select(ChatRoom, Product.title, Buyer.full_name, Seller.full_name)
        .join(Product, ChatRoom.product_id == Product.id)
        .join(Buyer, ChatRoom.buyer_id == Buyer.id)
        .join(Seller, ChatRoom.seller_id == Seller.id)
        .where(ChatRoom.id == room_id)
    )
    result = await db.execute(query)
    row = result.first()
    room, product_title, buyer_name, seller_name = row

    return {
        "id": room.id,
        "product_id": room.product_id,
        "product_title": product_title,
        "buyer_id": room.buyer_id,
        "seller_id": room.seller_id,
        "buyer_name": buyer_name,
        "seller_name": seller_name,
        "created_at": room.created_at
    }


@router.get("/rooms/{room_id}/messages/", response_model=list)
async def obtener_historial_mensajes(
    room_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Historial de mensajes de una sala (solo si el usuario participa en ella)."""
    await _verificar_pertenencia_sala(db, room_id, current_user.id)

    query = select(ChatMessage).where(ChatMessage.room_id == room_id).order_by(ChatMessage.created_at.asc())
    result = await db.execute(query)
    messages = result.scalars().all()

    payload = []
    for msg in messages:
        payload.append({
            "id": msg.id,
            "sender_id": msg.sender_id,
            "message": msg.message,
            "was_moderated": msg.was_moderated,
            "is_deleted": msg.message == "Este mensaje fue eliminado",
            "timestamp": str(msg.created_at)
        })
    return payload


@router.post("/rooms/{room_id}/read/")
async def marcar_mensajes_leidos(
    room_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Marca como leídos los mensajes ajenos de una sala del usuario autenticado."""
    await _verificar_pertenencia_sala(db, room_id, current_user.id)

    query = text(
        "UPDATE chat_messages SET is_read = TRUE WHERE room_id = :room_id AND sender_id != :user_id AND is_read = FALSE"
    )
    await db.execute(query, {"room_id": room_id, "user_id": current_user.id})
    await db.commit()
    return {"status": "success", "message": "Mensajes marcados como leídos."}


@router.get("/unread-count/", response_model=dict)
async def obtener_mensajes_no_leidos(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Cantidad total de mensajes no leídos del usuario autenticado."""
    query = text("""
        SELECT COUNT(m.id) 
        FROM chat_messages m
        JOIN chat_rooms r ON m.room_id = r.id
        WHERE (r.buyer_id = :user_id OR r.seller_id = :user_id)
          AND m.sender_id != :user_id
          AND m.is_read = FALSE
    """)
    result = await db.execute(query, {"user_id": current_user.id})
    count = result.scalar() or 0
    return {"unread_count": count}


@router.delete("/messages/{message_id}/")
async def eliminar_mensaje(
    message_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Marca un mensaje propio como eliminado y lo notifica por WebSocket."""
    query = select(ChatMessage).where(ChatMessage.id == message_id)
    result = await db.execute(query)
    msg = result.scalar_one_or_none()
    if not msg:
        raise HTTPException(status_code=404, detail="Mensaje no encontrado.")
    if msg.sender_id != current_user.id:
        raise HTTPException(status_code=403, detail="No tienes permiso para eliminar este mensaje.")

    msg.message = "Este mensaje fue eliminado"
    msg.was_moderated = False
    db.add(msg)
    await db.commit()
    await db.refresh(msg)

    payload_mensaje = {
        "id": msg.id,
        "sender_id": msg.sender_id,
        "message": msg.message,
        "was_moderated": msg.was_moderated,
        "is_deleted": True,
        "timestamp": str(msg.created_at)
    }
    await manager.broadcast(msg.room_id, payload_mensaje)

    return {"status": "success", "message": "Mensaje eliminado."}


@router.delete("/rooms/{room_id}/")
async def eliminar_conversacion(
    room_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Elimina una conversación completa (solo si el usuario participa en ella)."""
    await _verificar_pertenencia_sala(db, room_id, current_user.id)

    del_msg_query = text("DELETE FROM chat_messages WHERE room_id = :room_id")
    await db.execute(del_msg_query, {"room_id": room_id})

    room_res = await db.execute(select(ChatRoom).where(ChatRoom.id == room_id))
    room = room_res.scalar_one_or_none()
    if room:
        await db.delete(room)
    await db.commit()
    return {"status": "success", "message": "Conversación eliminada con éxito."}
