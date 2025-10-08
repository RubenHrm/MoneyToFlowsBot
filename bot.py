from flask import Flask, request
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes
import asyncio
import os

TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = "https://moneytoflowsbot-4.onrender.com"  # <-- ton URL Render

app = Flask(__name__)
bot = Bot(token=TOKEN)

# --- Commande /start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"Bienvenue {user.first_name} 👋\n"
        "Ceci est le bot de parrainage *MoneyToFlows* 💸\n\n"
        "Tape /dashboard pour voir ton tableau de bord."
    )

# --- Réception des messages Telegram ---
@app.route(f"/{TOKEN}", methods=["POST"])
def receive_update():
    data = request.get_json(force=True)
    update = Update.de_json(data, bot)
    asyncio.run(application.process_update(update))
    return "ok", 200

# --- Page d'accueil ---
@app.route("/")
def home():
    return "✅ MoneyToFlows bot is running"

# --- Application Telegram ---
application = Application.builder().token(TOKEN).build()
application.add_handler(CommandHandler("start", start))

# --- Démarrage Flask ---
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
