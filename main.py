from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from config import BOT_TOKEN
from procesar_tabla import procesar_tabla
import os
import threading
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer

# Configurar logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Diccionario para recordar en qué modo está cada usuario
user_modes = {}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("¡Hola! en que te puedo ayudar?\n \nCCTV Monitoring:\n   - /tabla para procesar una foto de revicion de devoluciones.")


async def tabla(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_modes[user_id] = "tabla"
    await update.message.reply_text("Modo tabla activado ✅\nEnvíame la imagen de la tabla.")


async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    mode = user_modes.get(user_id)

    if not mode:
        await update.message.reply_text("Usa un comando para activar el modo de procesamiento antes de enviar una imagen 📋")
        return

    filepath = f"temp_{user_id}.jpg"
    
    try:
        # Usar versión más pequeña de la foto para reducir memoria y ancho de banda
        # Telegram genera múltiples tamaños: photo[0] es la más pequeña, photo[-1] es la más grande
        # Usamos una versión intermedia (photo[1] o photo[0] si solo hay una)
        if len(update.message.photo) > 1:
            photo = update.message.photo[1]  # Versión intermedia
        else:
            photo = update.message.photo[0]  # Única versión disponible
        
        logger.info(f"Descargando foto para usuario {user_id}, tamaño: {photo.width}x{photo.height}")
        
        file = await context.bot.get_file(photo.file_id)
        await file.download_to_drive(custom_path=filepath)

        if mode == "tabla":
            await update.message.reply_text("Procesando tabla... 🧾")
            try:
                resultado = procesar_tabla(filepath)
                if isinstance(resultado, str) and resultado.startswith("Error:"):
                    await update.message.reply_text(f"❌ {resultado}")
                else:
                    await update.message.reply_text(f"✅ Resultado: {resultado}")
            except Exception as e:
                logger.error(f"Error procesando tabla: {e}", exc_info=True)
                await update.message.reply_text(f"❌ Error al procesar la tabla: {str(e)}")
        
        user_modes[user_id] = None  # Resetea el modo
        
    except MemoryError:
        logger.error(f"Error de memoria al procesar imagen para usuario {user_id}")
        await update.message.reply_text("❌ Error: No hay suficiente memoria para procesar la imagen. Intenta con una imagen más pequeña.")
        user_modes[user_id] = None
        
    except OSError as e:
        logger.error(f"Error del sistema al procesar imagen para usuario {user_id}: {e}")
        await update.message.reply_text("❌ Error del sistema al procesar la imagen. Por favor, intenta de nuevo.")
        user_modes[user_id] = None
        
    except Exception as e:
        logger.error(f"Error inesperado al procesar imagen para usuario {user_id}: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Error inesperado: {str(e)}")
        user_modes[user_id] = None
        
    finally:
        # Limpiar archivo temporal siempre, incluso si hubo errores
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
                logger.info(f"Archivo temporal eliminado: {filepath}")
        except Exception as e:
            logger.warning(f"No se pudo eliminar archivo temporal {filepath}: {e}")


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Evita escribir en stderr por cada petición de health check
        return


def _start_health_server():
    """Arranca un servidor HTTP simple para healthchecks en un hilo separado."""
    port = int(os.getenv('PORT', '8080'))
    server = HTTPServer(('0.0.0.0', port), _HealthHandler)
    server.serve_forever()


def main():
    # Inicia servidor de health en segundo plano para que Fly pueda comprobar el contenedor
    t = threading.Thread(target=_start_health_server, daemon=True)
    t.start()

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("tabla", tabla))
    app.add_handler(MessageHandler(filters.PHOTO, handle_image))
    app.run_polling()


if __name__ == "__main__":
    main()