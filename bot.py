# bot.py (FINAL - prêt pour Render)
import os
import sqlite3
import asyncio
import logging
from datetime import datetime
from flask import Flask, request
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ----------------- CONFIG -----------------
TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = "https://moneytoflowsbot-10.onrender.com"  # <-- Mets ici ton URL Render si besoin
ADMIN_USERNAME = "RUBENHRM777"  # sans @
PRODUCT_PRICE = 5000  # en FCFA ; 20% -> 1000 FCFA par filleul
DB_FILE = "data.db"

if not TOKEN:
    raise RuntimeError("La variable d'environnement BOT_TOKEN n'est pas définie.")

# ----------------- INIT -----------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
app = Flask(__name__)
bot = Bot(token=TOKEN)
application = Application.builder().token(TOKEN).build()

# ----------------- DATABASE -----------------
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            parrain_id INTEGER,
            registered_at TEXT,
            mm_number TEXT,
            is_admin INTEGER DEFAULT 0
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            reference TEXT,
            validated INTEGER DEFAULT 0,
            validated_at TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS earnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            source_user_id INTEGER,
            created_at TEXT,
            paid INTEGER DEFAULT 0
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            mm_number TEXT,
            status TEXT,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()

def db_execute(query, params=(), fetch=False, many=False):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    if many:
        c.executemany(query, params)
    else:
        c.execute(query, params)
    result = None
    if fetch:
        result = c.fetchall()
    conn.commit()
    conn.close()
    return result

# ----------------- HELPERS -----------------
def get_user_row(user_id):
    rows = db_execute("SELECT user_id, username, first_name, parrain_id, registered_at, mm_number, is_admin FROM users WHERE user_id = ?", (user_id,), fetch=True)
    return rows[0] if rows else None

def ensure_user(user):
    if not get_user_row(user.id):
        db_execute("INSERT INTO users (user_id, username, first_name, parrain_id, registered_at) VALUES (?, ?, ?, ?, ?)",
                   (user.id, user.username or "", user.first_name or "", None, datetime.utcnow().isoformat()))

def link_parrain(user_id, parrain_id):
    db_execute("UPDATE users SET parrain_id = ? WHERE user_id = ?", (parrain_id, user_id))

def add_purchase(user_id, reference):
    db_execute("INSERT INTO purchases (user_id, reference) VALUES (?, ?)", (user_id, reference))

def set_mm_number_db(user_id, mm):
    db_execute("UPDATE users SET mm_number = ? WHERE user_id = ?", (mm, user_id))

def create_withdrawal(user_id, amount, mm_number):
    db_execute("INSERT INTO withdrawals (user_id, amount, mm_number, status, created_at) VALUES (?, ?, ?, ?, ?)",
               (user_id, amount, mm_number, "pending", datetime.utcnow().isoformat()))

def mark_earnings_paid(user_id):
    db_execute("UPDATE earnings SET paid = 1 WHERE user_id = ?", (user_id,))

def compute_parrain_percent(nb_acheteurs):
    if nb_acheteurs >= 100:
        return 0.40
    elif nb_acheteurs >= 50:
        return 0.30
    else:
        return 0.20

# ----------------- CORE LOGIC -----------------
def get_parrain_stats(user_id):
    # total filleuls (registered)
    total_filleuls = db_execute("SELECT COUNT(*) FROM users WHERE parrain_id = ?", (user_id,), fetch=True)[0][0]
    # validated buyers (acheteurs)
    acheteurs = db_execute("""
        SELECT COUNT(DISTINCT p.user_id)
        FROM purchases p
        JOIN users u ON p.user_id = u.user_id
        WHERE u.parrain_id = ? AND p.validated = 1
    """, (user_id,), fetch=True)[0][0] or 0
    pending = db_execute("SELECT COALESCE(SUM(amount),0) FROM earnings WHERE user_id = ? AND paid = 0", (user_id,), fetch=True)[0][0] or 0.0
    total_earn = db_execute("SELECT COALESCE(SUM(amount),0) FROM earnings WHERE user_id = ?", (user_id,), fetch=True)[0][0] or 0.0
    pct = int(compute_parrain_percent(acheteurs) * 100)
    return {"total_filleuls": total_filleuls, "acheteurs": acheteurs, "pending": pending, "total_earn": total_earn, "pct": pct}

def validate_purchase_and_credit(purchase_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT user_id, validated FROM purchases WHERE id = ?", (purchase_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return False, "Référence introuvable."
    user_id, validated = row
    if validated:
        conn.close()
        return False, "Cette référence est déjà validée."

    now = datetime.utcnow().isoformat()
    c.execute("UPDATE purchases SET validated = 1, validated_at = ? WHERE id = ?", (now, purchase_id))

    # get parrain
    c.execute("SELECT parrain_id FROM users WHERE user_id = ?", (user_id,))
    r = c.fetchone()
    parrain_id = r[0] if r else None

    if parrain_id:
        # count validated buyers AFTER update
        c.execute("""
            SELECT COUNT(DISTINCT p.user_id)
            FROM purchases p
            JOIN users u ON p.user_id = u.user_id
            WHERE u.parrain_id = ? AND p.validated = 1
        """, (parrain_id,))
        total_validated = c.fetchone()[0] or 0

        pct = compute_parrain_percent(total_validated)
        amount = PRODUCT_PRICE * pct
        c.execute("INSERT INTO earnings (user_id, amount, source_user_id, created_at) VALUES (?, ?, ?, ?)",
                  (parrain_id, amount, user_id, now))
    conn.commit()
    conn.close()
    return True, "Achat validé et parrain crédité (si présent)."

# ----------------- HANDLERS -----------------
# /start with optional "ref_123"
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user)

    # parse deep-link argument (Telegram passes e.g. "ref_123")
    ref = None
    if context.args:
        arg = context.args[0]
        if isinstance(arg, str) and arg.startswith("ref_"):
            try:
                ref = int(arg.split("ref_")[1])
            except Exception:
                ref = None

    if ref and ref != user.id:
        # if user hasn't a parrain yet, set it
        row = get_user_row(user.id)
        if row and row[3] is None:
            link_parrain(user.id, ref)
            # notify parrain
            try:
                await context.bot.send_message(ref, f"🎉 Nouveau filleul inscrit : @{user.username or user.first_name}")
            except Exception:
                pass

    bot_username = (context.bot.username or "MoneyToFlowsBot")
    refer_link = f"https://t.me/{bot_username}?start=ref_{user.id}"
    text = (
        f"👋 Salut {user.first_name} !\n\n"
        f"Bienvenue dans le programme MoneyToFlows 💸\n\n"
        f"🔗 Ton lien de parrainage : {refer_link}\n\n"
        "Commandes : /achat /confirm_purchase <ref> /parrainage /dashboard /retrait /setmm /help"
    )
    await update.message.reply_text(text)

async def achat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"🛒 Lien officiel : https://sgzxfbtn.mychariow.shop/prd_8ind83\n\nAprès achat envoie /confirm_purchase <ta_reference>")

async def parrainage_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user)
    bot_username = (context.bot.username or "MoneyToFlowsBot")
    refer_link = f"https://t.me/{bot_username}?start=ref_{user.id}"
    await update.message.reply_text(f"💸 Ton lien de parrainage :\n{refer_link}")

async def confirm_purchase_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not context.args:
        await update.message.reply_text("Usage: /confirm_purchase <REFERENCE>")
        return
    reference = context.args[0]
    ensure_user(user)
    add_purchase(user.id, reference)
    await update.message.reply_text("✅ Référence reçue. L'admin la validera sous peu.")

    # notify admins
    admins = db_execute("SELECT user_id FROM users WHERE is_admin = 1", fetch=True)
    if admins:
        for a in admins:
            try:
                await context.bot.send_message(a[0], f"Nouvelle référence à valider : user {user.id} / @{user.username} / ref: {reference}")
            except Exception:
                pass

async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💡 Aide MoneyToFlows\n"
        "/start - Démarrer\n"
        "/parrainage - Récupérer ton lien\n"
        "/achat - Lien d'achat\n"
        "/confirm_purchase <ref> - Envoyer référence\n"
        "/dashboard - Voir tes stats\n"
        "/setmm <numero> - Enregistrer Mobile Money\n"
        "/retrait - Demander un retrait si >=5 filleuls acheteurs\n"
    )

async def dashboard_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user)
    stats = get_parrain_stats(user.id)
    text = (
        f"📊 Tableau de bord\n\n"
        f"👥 Filleuls inscrits : {stats['total_filleuls']}\n"
        f"🛒 Filleuls acheteurs validés : {stats['acheteurs']}\n"
        f"💰 Gains totaux : {int(stats['total_earn'])} FCFA\n"
        f"💵 Solde disponible : {int(stats['pending'])} FCFA\n"
        f"🔖 Taux actuel : {stats['pct']}%\n\n"
        f"🔔 Seuil retrait : 5 filleuls acheteurs"
    )
    await update.message.reply_text(text)

async def setmm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not context.args:
        await update.message.reply_text("Usage: /setmm <numero_mobile>")
        return
    mm = context.args[0]
    ensure_user(user)
    set_mm_number_db(user.id, mm)
    await update.message.reply_text(f"✅ Numéro Mobile Money enregistré : {mm}")

async def retrait_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user)
    stats = get_parrain_stats(user.id)
    if stats['acheteurs'] < 5:
        await update.message.reply_text(f"🚫 Il te faut au moins 5 filleuls acheteurs. Actuellement : {stats['acheteurs']}/5")
        return
    # check mm
    row = get_user_row(user.id)
    mm = row[5] if row else None
    if not mm:
        await update.message.reply_text("📲 Enregistre ton numéro Mobile Money avec /setmm <numero> avant de demander le retrait.")
        return
    amount = int(stats['pending'])
    if amount <= 0:
        await update.message.reply_text("Tu n'as pas de solde disponible pour retrait.")
        return
    create_withdrawal(user.id, amount, mm)
    # mark earnings paid to prevent duplicates
    mark_earnings_paid(user.id)
    await update.message.reply_text(f"✅ Demande de retrait enregistrée pour {amount} FCFA. L'admin te contactera pour confirmation.")

# Admin commands
async def admin_register_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if (user.username or "").lower() != ADMIN_USERNAME.lower():
        await update.message.reply_text("❌ Commande réservée à l'administrateur.")
        return
    ensure_user(user)
    db_execute("UPDATE users SET is_admin = 1 WHERE user_id = ?", (user.id,))
    await update.message.reply_text("✅ Vous êtes enregistré comme administrateur.")

async def validate_purchase_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    urow = get_user_row(user.id)
    if not (urow and urow[6] == 1):
        await update.message.reply_text("❌ Commande réservée à l'admin.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /validate_purchase <purchase_id>")
        return
    try:
        pid = int(context.args[0])
    except:
        await update.message.reply_text("L'ID doit être un nombre.")
        return
    ok, msg = validate_purchase_and_credit(pid)
    await update.message.reply_text(msg)
    if ok:
        # optionally notify parrain or buyer (handled in validate function via earnings)
        pass

async def stats_admin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    urow = get_user_row(user.id)
    if not (urow and urow[6] == 1):
        await update.message.reply_text("❌ Commande réservée à l'admin.")
        return
    total_users = db_execute("SELECT COUNT(*) FROM users", fetch=True)[0][0]
    total_purchases = db_execute("SELECT COUNT(*) FROM purchases WHERE validated = 1", fetch=True)[0][0]
    total_earnings = db_execute("SELECT COALESCE(SUM(amount),0) FROM earnings", fetch=True)[0][0] or 0
    pending_withdrawals = db_execute("SELECT COUNT(*) FROM withdrawals WHERE status = 'pending'", fetch=True)[0][0]
    await update.message.reply_text(
        f"📈 Stats Admin\n\nUtilisateurs: {total_users}\nAchats validés: {total_purchases}\nGains totaux: {int(total_earnings)} FCFA\nRetraits en attente: {pending_withdrawals}"
    )

async def pay_withdrawal_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    urow = get_user_row(user.id)
    if not (urow and urow[6] == 1):
        await update.message.reply_text("❌ Commande réservée à l'admin.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /pay_withdrawal <withdrawal_id>")
        return
    try:
        wid = int(context.args[0])
    except:
        await update.message.reply_text("L'ID doit être un nombre.")
        return
    # mark withdrawal as paid
    db_execute("UPDATE withdrawals SET status = 'paid' WHERE id = ?", (wid,))
    await update.message.reply_text(f"✅ Retrait {wid} marqué comme payé.")

# Generic message handler (for mobile money if user typed it without /setmm)
async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # simple heuristic: if message is digits and user hasn't mm -> save
    text = update.message.text.strip()
    user = update.effective_user
    ensure_user(user)
    row = get_user_row(user.id)
    if row and (not row[5]) and text.replace("+","").replace(" ","").isdigit() and 6 <= len(text) <= 15:
        set_mm_number_db(user.id, text)
        await update.message.reply_text(f"✅ Numéro Mobile Money enregistré : {text}")
        return
    await update.message.reply_text("Commande non reconnue. Utilise /help pour la liste des commandes.")

# ----------------- REGISTER HANDLERS -----------------
application.add_handler(CommandHandler("start", start_handler))
application.add_handler(CommandHandler("achat", achat_handler))
application.add_handler(CommandHandler("parrainage", parrainage_handler))
application.add_handler(CommandHandler("confirm_purchase", confirm_purchase_handler))
application.add_handler(CommandHandler("help", help_handler))
application.add_handler(CommandHandler("dashboard", dashboard_handler))
application.add_handler(CommandHandler("setmm", setmm_handler))
application.add_handler(CommandHandler("retrait", retrait_handler))

application.add_handler(CommandHandler("admin_register", admin_register_handler))
application.add_handler(CommandHandler("validate_purchase", validate_purchase_handler))
application.add_handler(CommandHandler("stats_admin", stats_admin_handler))
application.add_handler(CommandHandler("pay_withdrawal", pay_withdrawal_handler))

application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))

# ----------------- WEBHOOK ENDPOINT -----------------
@app.route(f"/{TOKEN}", methods=["POST"])
def webhook_endpoint():
    try:
        data = request.get_json(force=True)
        logging.info("Webhook received")
        update = Update.de_json(data, bot)
        asyncio.run(application.process_update(update))
    except Exception as e:
        logging.exception("Error processing update")
        return "error", 500
    return "ok", 200

# ----------------- START APP -----------------
if __name__ == "__main__":
    init_db()
    # attempt to set webhook (works when running directly)
    try:
        url = f"{WEBHOOK_URL}/{TOKEN}"
        resp = bot.set_webhook(url=url)
        logging.info("Webhook set (on startup): %s", resp)
    except Exception as e:
        logging.exception("Could not set webhook at startup")
    port = int(os.environ.get("PORT", 5000))
    logging.info("Starting Flask server on port %s", port)
    app.run(host="0.0.0.0", port=port)
