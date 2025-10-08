from flask import Flask, request
from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import asyncio
import os

# Récupère le token depuis Render
TOKEN = os.getenv("BOT_TOKEN")

app = Flask(__name__)
bot = Bot(token=TOKEN)

# === Commande /start ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"Bienvenue {user.first_name} 👋\n"
        "Ceci est le bot de parrainage *MoneyToFlows* 💸\n\n"
        "Tape /dashboard pour voir ton tableau de bord."
    )

# === Point d'entrée Webhook ===
@app.route(f'/{TOKEN}', methods=['POST'])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    asyncio.run(handle_update(update))
    return "ok", 200

async def handle_update(update):
    if update.message and update.message.text == "/start":
        await start(update, None)

@app.route('/')
def home():
    return "Bot MoneyToFlows is running ✅"

# === Démarre le serveur Flask ===
if __name__ == '__main__':
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
