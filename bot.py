import os
import asyncio
from flask import Flask, request
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes

# --- Configuration ---
TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = "https://moneytoflowsbot-8.onrender.com"  # 🔗 Ton URL Render actuelle

# --- Initialisation ---
app = Flask(__name__)
bot = Bot(token=TOKEN)

# --- Application Telegram ---
application = Application.builder().token(TOKEN).build()

# --- Données temporaires (en mémoire, à relier à une vraie DB plus tard) ---
users_data = {}  # {user_id: {"filleuls": int, "retrait_dispo": bool}}

# --- Commande /start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    if user_id not in users_data:
        users_data[user_id] = {"filleuls": 0, "retrait_dispo": False}

    print(f"[LOG] Commande /start reçue de {user.first_name} ({user_id})")
    await update.message.reply_text(
        f"👋 Bienvenue {user.first_name} !\n"
        "Ceci est le bot officiel *MoneyToFlows* 💸\n\n"
        "📘 Tu fais partie du programme de parrainage du produit Chariow.\n"
        "👉 Tape /help pour savoir comment ça marche.\n\n"
        "Et surtout, partage ton lien d’affiliation pour gagner des récompenses 💰."
    )

# --- Commande /help ---
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(f"[LOG] Commande /help reçue de {update.effective_user.first_name}")
    await update.message.reply_text(
        "💡 *Aide MoneyToFlows*\n\n"
        "Voici comment fonctionne ton programme de parrainage :\n"
        "1️⃣ Achète le produit digital sur Chariow :\n"
        "👉 [Accéder au produit](https://sgzxfbtn.mychariow.shop/prd_8ind83)\n\n"
        "2️⃣ Partage ton lien d’affiliation Telegram à tes amis.\n"
        "3️⃣ À partir de 5 filleuls acheteurs, tu débloques ton retrait 💸.\n\n"
        "Commandes disponibles :\n"
        "/start - Démarre le bot\n"
        "/dashboard - Voir tes statistiques\n"
        "/retrait - Retirer tes gains (si éligible)"
    )

# --- Commande /dashboard ---
async def dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    data = users_data.get(user.id, {"filleuls": 0, "retrait_dispo": False})
    filleuls = data["filleuls"]
    retrait_dispo = "✅ Disponible" if data["retrait_dispo"] else "❌ Non disponible"

    print(f"[LOG] Commande /dashboard reçue de {user.first_name} ({user.id})")

    await update.message.reply_text(
        f"📊 *Tableau de bord - MoneyToFlows*\n\n"
        f"👥 Filleuls acheteurs : {filleuls}\n"
        f"💰 Statut du retrait : {retrait_dispo}\n\n"
        f"➡️ À partir de 5 filleuls acheteurs, ton retrait sera débloqué automatiquement !"
    )

# --- Commande /retrait ---
async def retrait(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    data = users_data.get(user.id, {"filleuls": 0, "retrait_dispo": False})

    print(f"[LOG] Commande /retrait reçue de {user.first_name} ({user.id})")

    if data["filleuls"] >= 5:
        data["retrait_dispo"] = True
        await update.message.reply_text(
            "🎉 Félicitations ! Tu as atteint 5 filleuls acheteurs ✅\n"
            "💸 Tu peux désormais demander ton retrait.\n"
            "Notre équipe te contactera sous peu pour le traitement 🔔."
        )
    else:
        await update.message.reply_text(
            f"🚫 Tu n’as pas encore atteint le seuil minimum.\n"
            f"👥 Filleuls acheteurs actuels : {data['filleuls']}/5\n"
            "Continue à inviter pour débloquer ton retrait 💪."
        )

# --- Ajout des commandes ---
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("help", help_command))
application.add_handler(CommandHandler("dashboard", dashboard))
application.add_handler(CommandHandler("retrait", retrait))

# --- Route d’accueil ---
@app.route('/')
def home():
    return "✅ Bot MoneyToFlows est en ligne et opérationnel !"

# --- Webhook (réception des messages Telegram) ---
@app.route(f'/{TOKEN}', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True)
        print(f"[LOG] Webhook reçu ✅ : {data}")
        update = Update.de_json(data, bot)
        asyncio.run(application.process_update(update))
    except Exception as e:
        print(f"[ERREUR] {e}")
        return "Erreur interne", 500
    return "OK", 200

# --- Démarrage Flask ---
if __name__ == '__main__':
    webhook_url = f"{WEBHOOK_URL}/{TOKEN}"
    try:
        bot.delete_webhook()
        success = bot.set_webhook(url=webhook_url)
        print(f"[LOG] Webhook configuré : {success}")
    except Exception as e:
        print(f"[ERREUR] Impossible de configurer le Webhook : {e}")

    port = int(os.environ.get('PORT', 5000))
    print(f"[LOG] Serveur démarré sur le port {port} 🚀")
    app.run(host='0.0.0.0', port=port)
