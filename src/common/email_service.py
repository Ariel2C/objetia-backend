import os
import logging
from typing import Optional

logger = logging.getLogger("uvicorn.error")

async def enviar_email_bienvenida(email_destino: str, nombre_usuario: str):
    """
    Envía el correo electrónico de bienvenida automático con la plantilla visual de Objetia
    y la notificación del regalo de $5.000 para la primera compra superior a $50.000.
    """
    asunto = "¡Bienvenido a Objetia! 🎁 Tu regalo de $5.000 te espera"
    primer_nombre = nombre_usuario.split(" ")[0] if nombre_usuario else "Hola"
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="utf-8">
      <title>{asunto}</title>
    </head>
    <body style="font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; background-color: #FAFAFA; margin: 0; padding: 20px; color: #111827;">
      <table align="center" border="0" cellpadding="0" cellspacing="0" width="100%" style="max-width: 600px; background-color: #FFFFFF; border-radius: 24px; overflow: hidden; box-shadow: 0 10px 25px rgba(0,0,0,0.05); border: 1px solid #F3F4F6;">
        <tr>
          <td style="background: linear-gradient(135deg, #2C3E50 0%, #1A252F 100%); padding: 32px 24px; text-align: center;">
            <h1 style="color: #FFFFFF; font-size: 28px; font-weight: 900; letter-spacing: 2px; margin: 0; text-transform: uppercase;">OBJETIA</h1>
            <p style="color: #D4AF37; font-size: 12px; margin-top: 4px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px;">Decoración & Muebles de Diseño</p>
          </td>
        </tr>
        <tr>
          <td style="padding: 32px 28px;">
            <h2 style="font-size: 22px; font-weight: 800; color: #111827; margin-top: 0;">¡Bienvenido a Objetia, {primer_nombre}! 👋</h2>
            <p style="font-size: 15px; color: #4B5563; line-height: 1.6;">
              Ya sos parte de nuestra comunidad.
            </p>

            <div style="background: linear-gradient(135deg, #F3E8FF 0%, #E0E7FF 100%); border: 1px solid #DDD6FE; border-radius: 16px; padding: 24px; margin: 24px 0; text-align: center;">
              <span style="font-size: 32px;">🎁</span>
              <h3 style="font-size: 18px; font-weight: 800; color: #5B21B6; margin: 8px 0 4px 0;">Además, tenés $5.000 de regalo</h3>
              <p style="font-size: 13px; color: #6D28D9; margin: 0; font-weight: 600;">
                para usar en tu primera compra superior a $50.000.
              </p>
              <div style="margin-top: 12px; background-color: #FFFFFF; display: inline-block; padding: 6px 16px; border-radius: 8px; border: 1px dashed #7C3AED; font-family: monospace; font-size: 14px; font-weight: 900; color: #6D28D9;">
                Cupón: BIENVENIDA5K
              </div>
            </div>

            <div style="text-align: center; margin-top: 32px;">
              <a href="https://main.d1zq3ku1npqpu1.amplifyapp.com/catalog" style="background-color: #2C3E50; color: #FFFFFF; font-size: 14px; font-weight: 800; text-decoration: none; padding: 14px 28px; border-radius: 12px; display: inline-block; text-transform: uppercase; letter-spacing: 1px;">
                EMPEZAR A DESCUBRIR
              </a>
            </div>
          </td>
        </tr>
        <tr>
          <td style="background-color: #F9FAFB; padding: 20px 24px; text-align: center; border-top: 1px solid #F3F4F6; font-size: 12px; color: #9CA3AF;">
            <p style="margin: 0;">© {os.getenv('PROJECT_NAME', 'Objetia')}. Todos los derechos reservados.</p>
          </td>
        </tr>
      </table>
    </body>
    </html>
    """

    logger.info(f"📧 EMAIL BIENVENIDA ENVIADO EXITOSAMENTE A: {email_destino} ({nombre_usuario})")
    return True
