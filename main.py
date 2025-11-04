from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from config import BOT_TOKEN
from procesar_tabla import procesar_tabla

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

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("tabla", tabla))
    app.add_handler(MessageHandler(filters.PHOTO, handle_image))
    app.run_polling()

if _name_ == "_main_":
    main()