from flask import Flask, request
from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

import os

TOKEN = os.getenv("BOT_TOKEN")  # Ton token sera mis dans Render

app = Flask(__name__)
bot = Bot(token=TOKEN)

@app.route('/')
def home():
    return "Bot MoneyToFlows is running ✅"

# Commande /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"Bienvenue {user.first_name} 👋\n"
        "Ceci est le bot de parrainage *MoneyToFlows* 💸\n\n"
        "Tape /dashboard pour voir ton tableau de bord."
    )

# Fonction principale
if __name__ == '__main__':
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
