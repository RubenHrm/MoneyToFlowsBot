from flask import Flask, request
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes
import asyncio
import os
import logging

# --- Configuration ---
TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = "https://moneytoflowsbot-5.onrender.com"  # <-- Ton URL Render actuelle

# --- Initialisation ---
app = Flask(__name__)
bot = Bot(token=TOKEN)

# Activer les logs pour Render (important pour voir les erreurs)
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# --- Commande /start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user = update.effective_user
        await update.message.reply_text(
            f"Bienvenue {user.first_name} 👋\n"
            "Ceci est le bot de parrainage *MoneyToFlows* 💸\n\n"
            "Tape /dashboard pour voir ton tableau de bord."
        )
    except Exception as e:
        logging.error(f"Erreur dans /start : {e}")

# --- Commande /dashboard ---
async def dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.reply_text("Voici ton tableau de bord 📊 (bientôt disponible).")
    except Exception as e:
        logging.error(f"Erreur dans /dashboard : {e}")

# --- Application Telegram ---
application = Application.builder().token(TOKEN).build()
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("dashboard", dashboard))

# --- Route d’accueil ---
@app.route('/')
def home():
    return "Bot MoneyToFlows is running ✅"

# --- Réception des messages Telegram ---
@app.route(f'/{TOKEN}', methods=['POST'])
def receive_update():
    try:
        data = request.get_json(force=True)
        update = Update.de_json(data, bot)
        asyncio.run(application.process_update(update))
    except Exception as e:
        logging.error(f"Erreur lors du traitement de la mise à jour : {e}")
        return "error", 500
    return "ok", 200

# --- Lancement Flask ---
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
