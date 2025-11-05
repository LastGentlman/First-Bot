from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from config import BOT_TOKEN
from procesar_tabla import procesar_tabla
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# Diccionario para recordar en qué modo está cada usuario
user_modes = {}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("¡Hola! Envíame /tabla para procesar una imagen de tabla.")


async def tabla(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_modes[user_id] = "tabla"
    await update.message.reply_text("Modo tabla activado ✅\nEnvíame la imagen de la tabla.")


async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    mode = user_modes.get(user_id)

    if not mode:
        await update.message.reply_text("Usa /tabla antes de enviar una imagen 📋")
        return

    file = await update.message.photo[-1].get_file()
    filepath = f"temp_{user_id}.jpg"
    await file.download_to_drive(filepath)

    if mode == "tabla":
        await update.message.reply_text("Procesando tabla... 🧾")
        resultado = procesar_tabla(filepath)
        await update.message.reply_text(f"✅ Resultado: {resultado}")

    user_modes[user_id] = None  # Resetea el modo


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