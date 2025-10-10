# bot.py — MoneyToFlows (v12 stable, complet)
import os
import sqlite3
import asyncio
import logging
from datetime import datetime
from flask import Flask, request, jsonify
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ---------------- CONFIG ----------------
TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = "https://moneytoflowsbot-12.onrender.com"
ADMIN_USERNAME = "RUBENHRM777"
PRODUCT_PRICE = 5000
DB_FILE = "data.db"

if not TOKEN:
    raise RuntimeError("La variable d'environnement BOT_TOKEN n'est pas définie.")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

app = Flask(__name__)
bot = Bot(token=TOKEN)
application = Application.builder().token(TOKEN).build()

# -------------- DATABASE ----------------
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
    logging.info("✅ Base de données initialisée.")

def db(query, params=(), fetch=False):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(query, params)
    rows = c.fetchall() if fetch else None
    conn.commit()
    conn.close()
    return rows

# initialize DB on import
init_db()

# --------------- HELPERS ----------------
def ensure_user_record(user):
    if not db("SELECT 1 FROM users WHERE user_id = ?", (user.id,), fetch=True):
        db("INSERT INTO users (user_id, username, first_name, registered_at) VALUES (?, ?, ?, ?)",
           (user.id, user.username or "", user.first_name or "", datetime.utcnow().isoformat()))

def get_user_row(user_id):
    rows = db("SELECT user_id, username, first_name, parrain_id, registered_at, mm_number, is_admin FROM users WHERE user_id = ?",
              (user_id,), fetch=True)
    return rows[0] if rows else None

def set_parrain(child_id, parrain_id):
    db("UPDATE users SET parrain_id = ? WHERE user_id = ?", (parrain_id, child_id))

def add_purchase(user_id, reference):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO purchases (user_id, reference) VALUES (?, ?)", (user_id, reference))
    pid = c.lastrowid
    conn.commit()
    conn.close()
    return pid

def compute_pct(n_acheteurs):
    if n_acheteurs >= 100:
        return 0.40
    if n_acheteurs >= 50:
        return 0.30
    return 0.20

def count_validated_acheteurs(parrain_id):
    rows = db("""
        SELECT COUNT(DISTINCT p.user_id)
        FROM purchases p
        JOIN users u ON u.user_id = p.user_id
        WHERE u.parrain_id = ? AND p.validated = 1
    """, (parrain_id,), fetch=True)
    return rows[0][0] if rows else 0

def credit_parrain_for_buyer(buyer_id):
    row = db("SELECT parrain_id FROM users WHERE user_id = ?", (buyer_id,), fetch=True)
    if not row or not row[0][0]:
        return None
    parrain_id = row[0][0]
    acheteurs = count_validated_acheteurs(parrain_id)
    pct = compute_pct(acheteurs)
    amount = PRODUCT_PRICE * pct
    db("INSERT INTO earnings (user_id, amount, source_user_id, created_at) VALUES (?, ?, ?, ?)",
       (parrain_id, amount, buyer_id, datetime.utcnow().isoformat()))
    return amount

def get_parrain_stats(user_id):
    total_filleuls = db("SELECT COUNT(*) FROM users WHERE parrain_id = ?", (user_id,), fetch=True)[0][0]
    acheteurs = db("""
        SELECT COUNT(DISTINCT p.user_id)
        FROM purchases p
        JOIN users u ON u.user_id = p.user_id
        WHERE u.parrain_id = ? AND p.validated = 1
    """, (user_id,), fetch=True)[0][0] or 0
    pending = db("SELECT COALESCE(SUM(amount),0) FROM earnings WHERE user_id = ? AND paid = 0", (user_id,), fetch=True)[0][0] or 0.0
    total = db("SELECT COALESCE(SUM(amount),0) FROM earnings WHERE user_id = ?", (user_id,), fetch=True)[0][0] or 0.0
    pct = int(compute_pct(acheteurs) * 100)
    return {"total_filleuls": total_filleuls, "acheteurs": acheteurs, "pending": pending, "total": total, "pct": pct}

def set_mm_number(user_id, mm):
    db("UPDATE users SET mm_number = ? WHERE user_id = ?", (mm, user_id))

def create_withdrawal(user_id, amount, mm):
    db("INSERT INTO withdrawals (user_id, amount, mm_number, status, created_at) VALUES (?, ?, ?, ?, ?)",
       (user_id, amount, mm, "pending", datetime.utcnow().isoformat()))
    # mark earnings paid to avoid duplicate requests
    db("UPDATE earnings SET paid = 1 WHERE user_id = ?", (user_id,))

# -------------- HANDLERS --------------
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user_record(user)

    # parse deep-link: accepts "ref_123" or plain digits
    parrain_id = None
    if context.args:
        arg = context.args[0]
        if isinstance(arg, str):
            if arg.startswith("ref_"):
                try:
                    parrain_id = int(arg.split("ref_")[1])
                except:
                    parrain_id = None
            elif arg.isdigit():
                parrain_id = int(arg)

    row = get_user_row(user.id)
    if parrain_id and parrain_id != user.id and row and row[3] is None:
        set_parrain(user.id, parrain_id)
        try:
            await context.bot.send_message(parrain_id, f"🎉 Nouveau filleul inscrit : @{user.username or user.first_name}")
        except Exception:
            logging.exception("Erreur notification parrain")

    try:
        bot_username = (await context.bot.get_me()).username
    except Exception:
        bot_username = "MoneyToFlowsBot"
    link = f"https://t.me/{bot_username}?start=ref_{user.id}"

    await update.message.reply_text(
        f"👋 Salut {user.first_name} !\n\n"
        f"Bienvenue dans MoneyToFlows 💸\n\n"
        f"🔗 Ton lien de parrainage : {link}\n\n"
        "Commandes : /achat /confirm_purchase <ref> /parrainage /dashboard /setmm /retrait /help"
    )

async def achat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🛒 Lien d'achat officiel:\nhttps://sgzxfbtn.mychariow.shop/prd_8ind83\n\n"
        "Après achat, envoie la référence avec /confirm_purchase <REFERENCE>."
    )

async def confirm_purchase_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not context.args:
        await update.message.reply_text("Usage : /confirm_purchase <REFERENCE>")
        return
    reference = context.args[0]
    ensure_user_record(user)
    pid = add_purchase(user.id, reference)
    await update.message.reply_text(f"✅ Référence reçue (ID {pid}). L'admin la validera sous peu.")
    admins = db("SELECT user_id FROM users WHERE is_admin = 1", fetch=True)
    if admins:
        for a in admins:
            try:
                await context.bot.send_message(a[0], f"Nouvelle référence à valider : user {user.id} / @{user.username} / ref: {reference} (ID:{pid})")
            except Exception:
                logging.exception("notify admin failed")

async def parrainage_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user_record(user)
    try:
        bot_username = (await context.bot.get_me()).username
    except:
        bot_username = "MoneyToFlowsBot"
    link = f"https://t.me/{bot_username}?start=ref_{user.id}"
    await update.message.reply_text(f"💸 Ton lien de parrainage :\n{link}")

async def dashboard_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user_record(user)
    stats = get_parrain_stats(user.id)
    await update.message.reply_text(
        f"📊 Tableau de bord\n\n"
        f"👥 Filleuls inscrits : {stats['total_filleuls']}\n"
        f"🛒 Filleuls acheteurs validés : {stats['acheteurs']}\n"
        f"💰 Gains totaux : {int(stats['total'])} FCFA\n"
        f"💵 Solde disponible : {int(stats['pending'])} FCFA\n"
        f"🔖 Taux actuel : {stats['pct']}%\n\n"
        f"🔔 Seuil retrait : 5 filleuls acheteurs"
    )

async def setmm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not context.args:
        await update.message.reply_text("Usage : /setmm <numero_mobile>")
        return
    mm = context.args[0]
    ensure_user_record(user)
    set_mm_number(user.id, mm)
    await update.message.reply_text(f"✅ Numéro Mobile Money enregistré : {mm}")

async def retrait_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user_record(user)
    stats = get_parrain_stats(user.id)
    if stats['acheteurs'] < 5:
        await update.message.reply_text(f"🚫 Il te faut au moins 5 filleuls acheteurs pour demander un retrait. Actuels : {stats['acheteurs']}/5")
        return
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
    await update.message.reply_text(f"✅ Demande de retrait enregistrée pour {amount} FCFA. L'admin te contactera pour confirmation.")

# ------------- ADMIN HANDLERS -------------
async def admin_register_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if (user.username or "").lower() != ADMIN_USERNAME.lower():
        await update.message.reply_text("❌ Commande réservée à l'administrateur.")
        return
    ensure_user_record(user)
    db("UPDATE users SET is_admin = 1 WHERE user_id = ?", (user.id,))
    await update.message.reply_text("✅ Vous êtes enregistré comme administrateur du bot.")

async def validate_purchase_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = get_user_row(user.id)
    if not (u and u[6] == 1):
        await update.message.reply_text("❌ Commande réservée à l'admin.")
        return
    if not context.args:
        await update.message.reply_text("Usage : /validate_purchase <purchase_id>")
        return
    try:
        pid = int(context.args[0])
    except:
        await update.message.reply_text("L'ID doit être un nombre.")
        return
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT user_id, validated FROM purchases WHERE id = ?", (pid,))
    r = c.fetchone()
    if not r:
        await update.message.reply_text("Référence introuvable.")
        conn.close()
        return
    buyer_id, validated = r
    if validated == 1:
        await update.message.reply_text("Cette référence est déjà validée.")
        conn.close()
        return
    now = datetime.utcnow().isoformat()
    c.execute("UPDATE purchases SET validated = 1, validated_at = ? WHERE id = ?", (now, pid))
    conn.commit()
    conn.close()
    amt = credit_parrain_for_buyer(buyer_id) or 0
    await update.message.reply_text(f"Achat validé. Parrain crédité: {int(amt)} FCFA (si un parrain existait).")

async def stats_admin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = get_user_row(user.id)
    if not (u and u[6] == 1):
        await update.message.reply_text("❌ Commande réservée à l'admin.")
        return
    total_users = db("SELECT COUNT(*) FROM users", fetch=True)[0][0]
    total_valid_purchases = db("SELECT COUNT(*) FROM purchases WHERE validated = 1", fetch=True)[0][0]
    total_earnings = db("SELECT COALESCE(SUM(amount),0) FROM earnings", fetch=True)[0][0] or 0
    pending_withdrawals = db("SELECT COUNT(*) FROM withdrawals WHERE status = 'pending'", fetch=True)[0][0]
    await update.message.reply_text(
        f"📈 Stats Admin\n\nUtilisateurs: {total_users}\nAchats validés: {total_valid_purchases}\nGains totaux: {int(total_earnings)} FCFA\nRetraits en attente: {pending_withdrawals}"
    )

async def pay_withdrawal_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = get_user_row(user.id)
    if not (u and u[6] == 1):
        await update.message.reply_text("❌ Commande réservée à l'admin.")
        return
    if not context.args:
        await update.message.reply_text("Usage : /pay_withdrawal <withdrawal_id>")
        return
    try:
        wid = int(context.args[0])
    except:
        await update.message.reply_text("L'ID doit être un nombre.")
        return
    db("UPDATE withdrawals SET status = 'paid' WHERE id = ?", (wid,))
    await update.message.reply_text(f"✅ Retrait {wid} marqué comme payé.")

# ------------- TEXT HANDLER -------------
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user = update.effective_user
    ensure_user_record(user)
    row = get_user_row(user.id)
    if row and (row[5] is None) and text.replace("+","").replace(" ","").isdigit() and 6 <= len(text) <= 15:
        set_mm_number(user.id, text)
        await update.message.reply_text(f"✅ Numéro Mobile Money enregistré : {text}")
        return
    await update.message.reply_text("Commande non reconnue. Utilise /help pour la liste des commandes.")

# ------------- REGISTER HANDLERS -------------
application.add_handler(CommandHandler("start", start_handler))
application.add_handler(CommandHandler("achat", achat_handler))
application.add_handler(CommandHandler("confirm_purchase", confirm_purchase_handler))
application.add_handler(CommandHandler("parrainage", parrainage_handler))
application.add_handler(CommandHandler("dashboard", dashboard_handler))
application.add_handler(CommandHandler("setmm", setmm_handler))
application.add_handler(CommandHandler("retrait", retrait_handler))
application.add_handler(CommandHandler("help", start_handler))

# admin
application.add_handler(CommandHandler("admin_register", admin_register_handler))
application.add_handler(CommandHandler("validate_purchase", validate_purchase_handler))
application.add_handler(CommandHandler("stats_admin", stats_admin_handler))
application.add_handler(CommandHandler("pay_withdrawal", pay_withdrawal_handler))

application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

# === START TELEGRAM APP IN BACKGROUND (safe for Gunicorn) ===
def _start_telegram_app_in_background():
    import threading
    def _runner():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        # initialize and start the application on this loop
        loop.run_until_complete(application.initialize())
        loop.run_until_complete(application.start())
        loop.run_forever()

    t = threading.Thread(target=_runner, daemon=True)
    t.start()

# start app background worker so update_queue is processed
_start_telegram_app_in_background()

# ------------- WEBHOOK -------------
@app.route(f"/{TOKEN}", methods=["POST"])
def webhook_endpoint():
    try:
        data = request.get_json(force=True)
        logging.info("Webhook received")
        update = Update.de_json(data, bot)
        # put update into Application queue (non-blocking)
        application.update_queue.put_nowait(update)
    except Exception:
        logging.exception("Error processing update")
        return "error", 500
    return "ok", 200

@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "ok", "service": "MoneyToFlows", "version": "stable-12"})

# ------------- MAIN (local run) -------------
if __name__ == "__main__":
    try:
        bot.set_webhook(f"{WEBHOOK_URL}/{TOKEN}")
        logging.info("Webhook set successfully.")
    except Exception:
        logging.exception("Could not set webhook automatically.")
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
