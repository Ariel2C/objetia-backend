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

def get_smtp_config():
    return {
        "host": os.getenv("SMTP_HOST", "smtp.gmail.com"),
        "port": int(os.getenv("SMTP_PORT", "587")),
        "user": os.getenv("SMTP_USER", ""),
        "password": os.getenv("SMTP_PASSWORD", ""),
        "from_email": os.getenv("EMAILS_FROM_EMAIL", "novedades@objetia.com"),
        "project_name": os.getenv("PROJECT_NAME", "Objetia")
    }

async def enviar_email_bienvenida(email_destino: str, nombre_usuario: str):
    """
    Envía el correo electrónico de bienvenida automático con la plantilla visual de Objetia
    y la notificación del regalo de $5.000 para la primera compra superior a $50.000 (descuento automático).
    """
    config = get_smtp_config()
    project_name = config["project_name"]
    asunto = f"¡Bienvenido a {project_name}! 🎁 Tenés $5.000 de regalo para tu primera compra"
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
          <td style="background: linear-gradient(135deg, #18181B 0%, #27272A 100%); padding: 32px 24px; text-align: center;">
            <h1 style="color: #FFFFFF; font-size: 28px; font-weight: 900; letter-spacing: 2px; margin: 0; text-transform: uppercase;">{project_name}</h1>
            <p style="color: #D4AF37; font-size: 12px; margin-top: 4px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px;">Muebles & Objetos de Diseño</p>
          </td>
        </tr>
        <tr>
          <td style="padding: 32px 28px;">
            <h2 style="font-size: 22px; font-weight: 800; color: #111827; margin-top: 0;">¡Bienvenido a {project_name}! 👋</h2>
            <p style="font-size: 15px; color: #4B5563; line-height: 1.6;">
              Ya sos parte de nuestra comunidad.
            </p>

            <div style="background: linear-gradient(135deg, #FAF5FF 0%, #EEF2FF 100%); border: 1px solid #DDD6FE; border-radius: 16px; padding: 24px; margin: 24px 0; text-align: center;">
              <span style="font-size: 32px;">🎁</span>
              <h3 style="font-size: 18px; font-weight: 800; color: #5B21B6; margin: 8px 0 4px 0;">Además, tenés $5.000 de regalo</h3>
              <p style="font-size: 14px; color: #6D28D9; margin: 0; font-weight: 600;">
                para usar en tu primera compra superior a $50.000.
              </p>
              <div style="margin-top: 14px; background-color: #FFFFFF; display: inline-block; padding: 6px 16px; border-radius: 8px; border: 1px solid #C4B5FD; font-size: 12px; font-weight: 800; color: #6D28D9;">
                ✓ Se descuenta automáticamente en tu checkout
              </div>
            </div>

            <div style="text-align: center; margin-top: 32px;">
              <a href="https://main.d1zq3ku1npqpu1.amplifyapp.com/catalog" style="background-color: #18181B; color: #FFFFFF; font-size: 14px; font-weight: 800; text-decoration: none; padding: 14px 28px; border-radius: 12px; display: inline-block; text-transform: uppercase; letter-spacing: 1px;">
                EMPEZAR A DESCUBRIR
              </a>
            </div>
          </td>
        </tr>
        <tr>
          <td style="background-color: #F9FAFB; padding: 20px 24px; text-align: center; border-top: 1px solid #F3F4F6; font-size: 12px; color: #9CA3AF;">
            <p style="margin: 0;">© {project_name}. Todos los derechos reservados.</p>
          </td>
        </tr>
      </table>
    </body>
    </html>
    """

    # Enviar correo real por SMTP si las credenciales están configuradas
    pwd_clean = config["password"].strip().replace(" ", "") if config["password"] else ""
    user_clean = config["user"].strip()
    if user_clean and pwd_clean:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = asunto
            from_display = config['from_email'] or user_clean
            msg["From"] = f"{project_name} <{from_display}>"
            msg["To"] = email_destino
            msg.attach(MIMEText(html_content, "html"))

            # Envoltorio SMTP: Gmail requiere que el remitente del sobre coincida con la cuenta autenticada
            envelope_sender = user_clean if "gmail" in config["host"].lower() else from_display
            envelope_sender = envelope_sender.split("<")[-1].replace(">", "").strip()

            with smtplib.SMTP(config["host"], config["port"], timeout=15) as server:
                server.starttls()
                server.login(user_clean, pwd_clean)
                server.sendmail(envelope_sender, [email_destino], msg.as_string())

            logger.info(f"📧 EMAIL DE BIENVENIDA ENVIADO VÍA SMTP A: {email_destino}")
            return True
        except Exception as e:
            logger.error(f"❌ Error al enviar email SMTP de bienvenida: {e}")
            return False
    else:
        logger.info(f"📧 [MODO SIMULACIÓN] EMAIL DE BIENVENIDA GENERADO PARA: {email_destino} ({nombre_usuario}) - Configure el servidor SMTP en el panel Programador.")
        return True

async def enviar_email_prueba(email_destino: str, custom_config: dict = None):
    """
    Envía un correo de prueba para verificar conectividad y credenciales SMTP.
    """
    cfg = custom_config if custom_config else get_smtp_config()
    project_name = cfg.get("project_name", "Objetia")
    user_clean = cfg.get("user", "").strip()
    pwd_clean = cfg.get("password", "").strip().replace(" ", "")
    host = cfg.get("host", "smtp.gmail.com").strip()
    port = int(cfg.get("port", 587))
    from_display = cfg.get("from_email") or user_clean

    asunto = f"[{project_name}] Prueba de conexión de correo SMTP exitosa ✓"

    html = f"""
    <div style="font-family: sans-serif; padding: 24px; color: #111; max-width: 600px; margin: 0 auto; border: 1px solid #E5E7EB; border-radius: 16px;">
      <h2 style="color: #10B981; margin-top: 0;">¡Conexión Exitosa con el Servidor de Emails! 🎉</h2>
      <p>Hola Ariel, este correo confirma que la configuración de correo de <strong>{project_name}</strong> está 100% operativa.</p>
      <div style="background-color: #F9FAFB; padding: 16px; border-radius: 12px; margin: 16px 0; font-size: 13px;">
        <p style="margin: 4px 0;"><strong>Servidor Host:</strong> {host}</p>
        <p style="margin: 4px 0;"><strong>Puerto:</strong> {port}</p>
        <p style="margin: 4px 0;"><strong>Cuenta de Envío:</strong> {user_clean}</p>
        <p style="margin: 4px 0;"><strong>Remitente Visible:</strong> {from_display}</p>
      </div>
      <p style="color: #059669; font-weight: bold;">Tus usuarios recibirán el email de bienvenida con los $5.000 de regalo inmediatamente al registrarse.</p>
    </div>
    """

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = asunto
        msg["From"] = f"{project_name} <{from_display}>"
        msg["To"] = email_destino
        msg.attach(MIMEText(html, "html"))

        envelope_sender = user_clean if "gmail" in host.lower() else from_display
        envelope_sender = envelope_sender.split("<")[-1].replace(">", "").strip()

        with smtplib.SMTP(host, port, timeout=15) as server:
            server.starttls()
            server.login(user_clean, pwd_clean)
            server.sendmail(envelope_sender, [email_destino], msg.as_string())

        return {"ok": True, "message": f"¡Email de prueba enviado exitosamente a {email_destino}!"}
    except Exception as e:
        return {"ok": False, "message": f"Error SMTP: {str(e)}"}
