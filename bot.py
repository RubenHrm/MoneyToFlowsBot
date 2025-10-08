from flask import Flask, request
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler
import asyncio
import os

TOKEN = os.getenv("BOT_TOKEN")

app = Flask(__name__)
bot = Bot(token=TOKEN)

# --- Commande /start ---
async def start(update: Update, context):
    user = update.effective_user
    await update.message.reply_text(
        f"Bienvenue {user.first_name} 👋\n"
        "Ceci est le bot de parrainage *MoneyToFlows* 💸\n\n"
        "Tape /dashboard pour voir ton tableau de bord."
    )

# --- Route principale ---
@app.route('/')
def home():
    return "Bot MoneyToFlows is running ✅"

# --- Webhook ---
@app.route(f'/{TOKEN}', methods=['POST'])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    asyncio.run(app_telegram.process_update(update))
    return "OK", 200

# --- Application Telegram ---
app_telegram = Application.builder().token(TOKEN).build()
app_telegram.add_handler(CommandHandler("start", start))

# --- Démarrage ---
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
