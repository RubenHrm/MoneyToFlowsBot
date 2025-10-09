from flask import Flask, request
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes
import asyncio
import os
import requests

# --- Configuration ---
TOKEN = os.getenv("BOT_TOKEN")  # Ton token Telegram stocké sur Render
WEBHOOK_URL = "https://moneytoflowsbot-7.onrender.com"  # <-- Remplace ici à chaque nouveau déploiement

app = Flask(__name__)
bot = Bot(token=TOKEN)

# --- Commande /start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"👋 Bienvenue {user.first_name} !\n\n"
        "🔥 Ceci est le bot officiel *MoneyToFlows*.\n"
        "💸 Gagne des revenus grâce au parrainage automatisé !\n\n"
        "📊 Tape /dashboard pour voir ton tableau de bord."
    )

# --- Commande /dashboard ---
async def dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📊 *Ton tableau de bord MoneyToFlows* :\n"
        "- Parrains : 0\n"
        "- Gains : 0 FCFA\n"
        "- Statut : En cours 🚀",
        parse_mode="Markdown"
    )

# --- Réception des mises à jour Telegram ---
@app.route(f"/{TOKEN}", methods=["POST"])
def receive_update():
    data = request.get_json(force=True)
    update = Update.de_json(data, bot)
    asyncio.run(app_telegram.process_update(update))
    return "OK", 200

# --- Route de test ---
@app.route('/')
def home():
    return "✅ Bot MoneyToFlows en ligne et opérationnel !"

# --- Lancement du bot Telegram ---
app_telegram = Application.builder().token(TOKEN).build()
app_telegram.add_handler(CommandHandler("start", start))
app_telegram.add_handler(CommandHandler("dashboard", dashboard))

# --- Configuration automatique du Webhook ---
def set_webhook():
    webhook_url = f"{WEBHOOK_URL}/{TOKEN}"
    resp = requests.get(
        f"https://api.telegram.org/bot{TOKEN}/setWebhook?url={webhook_url}"
    )
    print("Webhook setup response:", resp.text)

set_webhook()

# --- Démarrage du serveur Flask ---
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
