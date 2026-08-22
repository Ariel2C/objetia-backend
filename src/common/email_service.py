import os
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger("uvicorn.error")

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
EMAILS_FROM_EMAIL = os.getenv("EMAILS_FROM_EMAIL", "novedades@objetia.com")
PROJECT_NAME = os.getenv("PROJECT_NAME", "Objetia")

async def enviar_email_bienvenida(email_destino: str, nombre_usuario: str):
    """
    Envía el correo electrónico de bienvenida automático con la plantilla visual de Objetia
    y la notificación del regalo de $5.000 para la primera compra superior a $50.000.
    """
    asunto = f"¡Bienvenido a {PROJECT_NAME}! 🎁 Tu regalo de $5.000 te espera"
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
            <h1 style="color: #FFFFFF; font-size: 28px; font-weight: 900; letter-spacing: 2px; margin: 0; text-transform: uppercase;">{PROJECT_NAME}</h1>
            <p style="color: #D4AF37; font-size: 12px; margin-top: 4px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px;">Decoración & Muebles de Diseño</p>
          </td>
        </tr>
        <tr>
          <td style="padding: 32px 28px;">
            <h2 style="font-size: 22px; font-weight: 800; color: #111827; margin-top: 0;">¡Bienvenido a {PROJECT_NAME}, {primer_nombre}! 👋</h2>
            <p style="font-size: 15px; color: #4B5563; line-height: 1.6;">
              Ya sos parte de nuestra comunidad. Podés comprar y vender productos de diseño de forma simple y segura.
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
            <p style="margin: 0;">© {PROJECT_NAME}. Todos los derechos reservados.</p>
          </td>
        </tr>
      </table>
    </body>
    </html>
    """

    # Enviar correo real por SMTP si las credenciales están presentes en .env
    if SMTP_USER and SMTP_PASSWORD:
      try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = asunto
        msg["From"] = f"{PROJECT_NAME} <{EMAILS_FROM_EMAIL or SMTP_USER}>"
        msg["To"] = email_destino
        msg.attach(MIMEText(html_content, "html"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
          server.starttls()
          server.login(SMTP_USER, SMTP_PASSWORD)
          server.sendmail(msg["From"], [email_destino], msg.as_string())

        logger.info(f"📧 EMAIL DE BIENVENIDA ENVIADO VÍA SMTP A: {email_destino}")
        return True
      except Exception as e:
        logger.error(f"❌ Error al enviar email SMTP de bienvenida: {e}")
        return False
    else:
      logger.info(f"📧 [MODO SIMULACIÓN] EMAIL DE BIENVENIDA GENERADO PARA: {email_destino} ({nombre_usuario})")
      return True
