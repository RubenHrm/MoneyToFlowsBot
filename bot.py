# bot.py
import os
import sqlite3
import asyncio
from datetime import datetime
from flask import Flask, request
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes
import logging

# ----------------- CONFIG -----------------
TOKEN = os.getenv("BOT_TOKEN")
# Mets ici ton URL Render (mise à jour à chaque nouveau déploiement si besoin)
WEBHOOK_URL = "https://moneytoflowsbot-9.onrender.com"

# Admin (nom d'utilisateur - pour l'enregistrement initial)
ADMIN_USERNAME = "RUBENHRM777"  # sans @

# Paramètres commercial
PRODUCT_PRICE = 10000  # montant du produit en FCFA (modifiable)
BASE_COMMISSION = 0.20  # 20% par défaut (sera ajusté selon palier)

# ----------------- INIT -----------------
if not TOKEN:
    raise RuntimeError("La variable d'environnement BOT_TOKEN n'est pas définie sur Render.")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
app = Flask(__name__)
bot = Bot(token=TOKEN)
application = Application.builder().token(TOKEN).build()

DB_FILE = "data.db"

# ----------------- DB UTIL -----------------
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # users: user_id, username, first_name, parrain_id, registered_at, mm_number
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
    # purchases: id, user_id, reference, validated (0/1), validated_at
    c.execute("""
        CREATE TABLE IF NOT EXISTS purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            reference TEXT,
            validated INTEGER DEFAULT 0,
            validated_at TEXT
        )
    """)
    # earnings: id, user_id (parrain), amount, source_user_id, created_at, paid (0/1)
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
    # withdrawals: id, user_id, amount, mm_number, status, created_at
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

# ----------------- LOGIC UTIL -----------------
def get_user(user_id):
    rows = db_execute("SELECT * FROM users WHERE user_id = ?", (user_id,), fetch=True)
    return rows[0] if rows else None

def ensure_user_record(user):
    if not get_user(user.id):
        db_execute(
            "INSERT INTO users (user_id, username, first_name, parrain_id, registered_at) VALUES (?, ?, ?, ?, ?)",
            (user.id, user.username or "", user.first_name or "", None, datetime.utcnow().isoformat())
        )

def link_user_with_parrain(user_id, parrain_id):
    db_execute("UPDATE users SET parrain_id = ? WHERE user_id = ?", (parrain_id, user_id))

def add_purchase(user_id, reference):
    db_execute("INSERT INTO purchases (user_id, reference) VALUES (?, ?)", (user_id, reference))

def validate_purchase(purchase_id, validator_id=None):
    # mark purchase validated and credit parrain
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT user_id, validated FROM purchases WHERE id = ?", (purchase_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return False, "ID de purchase introuvable"
    user_id, validated = row
    if validated:
        conn.close()
        return False, "Cette référence est déjà validée."
    # mark validated
    now = datetime.utcnow().isoformat()
    c.execute("UPDATE purchases SET validated = 1, validated_at = ? WHERE id = ?", (now, purchase_id))
    # find parrain of this buyer
    c.execute("SELECT parrain_id FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    parrain_id = row[0] if row else None
    # compute commission for parrain, if exists
    if parrain_id:
        # compute parrain's current number of validated filleuls
        c.execute("""
            SELECT COUNT(DISTINCT p.user_id)
            FROM purchases p
            JOIN users u ON p.user_id = u.user_id
            WHERE u.parrain_id = ? AND p.validated = 1
        """, (parrain_id,))
        cnt_row = c.fetchone()
        acheteurs_count = cnt_row[0] if cnt_row else 0
        # determine percentage based on thresholds AFTER this validation
        acheteurs_after = acheteurs_count  # since we counted inclusive newly validated? ensure.
        # We'll compute levels based on total validated filleuls for parrain (we'll recalc below)
        # compute final count
        c.execute("""
            SELECT COUNT(DISTINCT p.user_id)
            FROM purchases p
            JOIN users u ON p.user_id = u.user_id
            WHERE u.parrain_id = ? AND p.validated = 1
        """, (parrain_id,))
        total_validated = c.fetchone()[0] or 0
        # determine percentage
        if total_validated >= 100:
            pct = 0.40
        elif total_validated >= 50:
            pct = 0.30
        else:
            pct = 0.20
        amount = PRODUCT_PRICE * pct
        # insert earning
        c.execute("INSERT INTO earnings (user_id, amount, source_user_id, created_at) VALUES (?, ?, ?, ?)",
                  (parrain_id, amount, user_id, now))
    conn.commit()
    conn.close()
    return True, "Achat validé et parrain crédité (si présent)."

def get_parrain_stats(user_id):
    # total filleuls (registered), total filleuls acheteurs (validated purchases), total earnings, pending balance
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users WHERE parrain_id = ?", (user_id,))
    total_filleuls = c.fetchone()[0] or 0
    c.execute("""
        SELECT COUNT(DISTINCT p.user_id)
        FROM purchases p
        JOIN users u ON p.user_id = u.user_id
        WHERE u.parrain_id = ? AND p.validated = 1
    """, (user_id,))
    acheteurs = c.fetchone()[0] or 0
    c.execute("SELECT COALESCE(SUM(amount),0) FROM earnings WHERE user_id = ? AND paid = 0", (user_id,))
    pending = c.fetchone()[0] or 0.0
    c.execute("SELECT COALESCE(SUM(amount),0) FROM earnings WHERE user_id = ?", (user_id,))
    total_earn = c.fetchone()[0] or 0.0
    conn.close()
    # determine percent tier
    if acheteurs >= 100:
        pct = 40
    elif acheteurs >= 50:
        pct = 30
    else:
        pct = 20
    return {
        "total_filleuls": total_filleuls,
        "acheteurs": acheteurs,
        "pending": pending,
        "total_earn": total_earn,
        "pct": pct
    }

def set_mm_number(user_id, mm_number):
    db_execute("UPDATE users SET mm_number = ? WHERE user_id = ?", (mm_number, user_id))

def create_withdrawal(user_id, amount, mm_number):
    now = datetime.utcnow().isoformat()
    db_execute("INSERT INTO withdrawals (user_id, amount, mm_number, status, created_at) VALUES (?, ?, ?, ?, ?)",
               (user_id, amount, mm_number, "pending", now))

def mark_earnings_paid(user_id):
    db_execute("UPDATE earnings SET paid = 1 WHERE user_id = ?", (user_id,))

# ----------------- HANDLERS -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args
    ref = args[0] if args else None

    # ensure user in DB
    ensure_user_record(user)
    # process referral param if exists (format: ref_<parrain_id>)
    if ref and ref.startswith("ref_"):
        try:
            parrain_id = int(ref.split("ref_")[1])
            if parrain_id != user.id:
                # set parrain for this user if not already set
                row = get_user(user.id)
                if row and (row[3] is None):
                    link_user_with_parrain(user.id, parrain_id)
                    # notify parrain if exists
                    parrain = get_user(parrain_id)
                    if parrain:
                        try:
                            await context.bot.send_message(parrain_id, f"🎉 Nouveau filleul inscrit : @{user.username or user.first_name}")
                        except Exception:
                            pass
        except Exception:
            pass

    # send welcome and referral link
    bot_username = context.bot.username
    refer_link = f"https://t.me/{bot_username}?start=ref_{user.id}"
    text = (
        f"👋 Salut {user.first_name} !\n\n"
        "Bienvenue dans le programme MoneyToFlows 💸\n\n"
        f"🔗 Ton lien de parrainage : {refer_link}\n\n"
        "📌 Commandes utiles:\n"
        "/achat - Lien du produit\n"
        "/confirm_purchase <réf> - Envoyer ta référence d'achat\n"
        "/parrainage - Récupérer ton lien\n"
        "/dashboard - Voir ton tableau de bord\n"
        "/retrait - Demander un retrait (si éligible)\n"
        "/help - Aide\n"
    )
    await update.message.reply_text(text)

async def achat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"🛒 Lien d'achat officiel:\nhttps://sgzxfbtn.mychariow.shop/prd_8ind83\n\nAprès ton achat, envoie la référence avec /confirm_purchase <REFERENCE>.")

async def parrainage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user_record(user)
    refer_link = f"https://t.me/{context.bot.username}?start=ref_{user.id}"
    await update.message.reply_text(f"💸 Ton lien de parrainage :\n{refer_link}")

async def confirm_purchase(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args
    if not args:
        await update.message.reply_text("Usage : /confirm_purchase <REFERENCE>")
        return
    reference = args[0]
    ensure_user_record(user)
    add_purchase(user.id, reference)
    await update.message.reply_text("✅ Référence reçue. Elle sera vérifiée et validée par l'admin sous peu. Merci !")
    # notify admin(s) by username stored in DB
    # find admin user_id if registered
    admins = db_execute("SELECT user_id FROM users WHERE is_admin = 1", fetch=True)
    if admins:
        for a in admins:
            try:
                await context.bot.send_message(a[0], f"Nouvelle référence d'achat à valider : user {user.id} / @{user.username} / ref: {reference}")
            except Exception:
                pass

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💡 Help MoneyToFlows\n"
        "/start - Démarrer\n"
        "/achat - Lien d'achat\n"
        "/parrainage - Ton lien unique\n"
        "/confirm_purchase <ref> - Envoyer référence d'achat\n"
        "/dashboard - Voir stats\n"
        "/retrait - Demander retrait (si >=5 filleuls acheteurs)\n"
    )

async def dashboard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user_record(user)
    stats = get_parrain_stats(user.id)
    text = (
        f"📊 Tableau de bord\n\n"
        f"👥 Filleuls inscrits : {stats['total_filleuls']}\n"
        f"🛒 Filleuls acheteurs validés : {stats['acheteurs']}\n"
        f"💰 Gains totaux : {int(stats['total_earn'])} FCFA\n"
        f"💵 Solde disponible (non payé) : {int(stats['pending'])} FCFA\n"
        f"🔖 Taux actuel : {stats['pct']}%\n\n"
        f"🔔 Seuil retrait : 5 filleuls acheteurs"
    )
    await update.message.reply_text(text)

async def retrait_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user_record(user)
    stats = get_parrain_stats(user.id)
    if stats['acheteurs'] < 5:
        await update.message.reply_text(f"🚫 Tu dois avoir au moins 5 filleuls acheteurs pour demander un retrait. Actuellement : {stats['acheteurs']}/5")
        return
    # ensure mm number
    row = get_user(user.id)
    mm = row[5] if row else None
    if not mm:
        await update.message.reply_text("📲 Tu dois enregistrer ton numéro Mobile Money. Envoie : /setmm <numero>")
        return
    # create withdrawal for pending balance (all pending)
    amount = int(stats['pending'])
    if amount <= 0:
        await update.message.reply_text("Tu n'as pas de solde disponible pour retrait.")
        return
    create_withdrawal(user.id, amount, mm)
    # mark earnings as paid (for simplicity we mark them as paid here; in réel, admin validation needed)
    mark_earnings_paid(user.id)
    await update.message.reply_text(f"✅ Demande de retrait enregistrée pour {amount} FCFA. L'admin te contactera pour le paiement.")

async def setmm_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args
    if not args:
        await update.message.reply_text("Usage : /setmm <numero_mobile>")
        return
    mm = args[0]
    ensure_user_record(user)
    set_mm_number(user.id, mm)
    await update.message.reply_text(f"✅ Numéro Mobile Money enregistré : {mm}")

# Admin register command (owner types this once to register their Telegram id)
async def admin_register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if (user.username or "").lower() != ADMIN_USERNAME.lower():
        await update.message.reply_text("❌ Cette commande est réservée à l'administrateur.")
        return
    # mark as admin
    db_execute("UPDATE users SET is_admin = 1 WHERE user_id = ?", (user.id,))
    ensure_user_record(user)
    await update.message.reply_text("✅ Vous êtes enregistré comme administrateur du bot.")

async def validate_purchase_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    # check admin
    urow = get_user(user.id)
    if not (urow and urow[6] == 1):
        await update.message.reply_text("❌ Commande réservée à l'admin.")
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage : /validate_purchase <purchase_id>")
        return
    try:
        pid = int(args[0])
    except:
        await update.message.reply_text("L'ID doit être un nombre.")
        return
    ok, msg = validate_purchase(pid, user.id)
    await update.message.reply_text(msg)

async def stats_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    urow = get_user(user.id)
    if not (urow and urow[6] == 1):
        await update.message.reply_text("❌ Commande réservée à l'admin.")
        return
    # show summary
    rows = db_execute("SELECT user_id, username, first_name FROM users", fetch=True)
    total_users = len(rows)
    total_purchases = db_execute("SELECT COUNT(*) FROM purchases WHERE validated = 1", fetch=True)[0][0]
    total_earnings = db_execute("SELECT COALESCE(SUM(amount),0) FROM earnings", fetch=True)[0][0]
    await update.message.reply_text(
        f"📈 Stats Admin\n\n"
        f"Utilisateurs: {total_users}\n"
        f"Achats validés: {total_purchases}\n"
        f"Gains totaux: {int(total_earnings)} FCFA\n"
    )

# ----------------- Register handlers -----------------
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("achat", achat))
application.add_handler(CommandHandler("parrainage", parrainage))
application.add_handler(CommandHandler("confirm_purchase", confirm_purchase))
application.add_handler(CommandHandler("help", help_cmd))
application.add_handler(CommandHandler("dashboard", dashboard_cmd))
application.add_handler(CommandHandler("retrait", retrait_cmd))
application.add_handler(CommandHandler("setmm", setmm_cmd))
application.add_handler(CommandHandler("admin_register", admin_register))
application.add_handler(CommandHandler("validate_purchase", validate_purchase_cmd))
application.add_handler(CommandHandler("stats_admin", stats_admin))

# ----------------- Webhook endpoint -----------------
@app.route(f'/{TOKEN}', methods=['POST'])
def receive_update():
    try:
        data = request.get_json(force=True)
        logging.info(f"Webhook request received: {data}")
        update = Update.de_json(data, bot)
        asyncio.run(application.process_update(update))
    except Exception as e:
        logging.exception("Error processing update")
        return "error", 500
    return "OK", 200

# ----------------- App start -----------------
if __name__ == "__main__":
    init_db()
    # Optional: attempt to set webhook automatically
    try:
        url = f"{WEBHOOK_URL}/{TOKEN}"
        resp = bot.set_webhook(url=url)
        logging.info("Webhook set response: %s", resp)
    except Exception as e:
        logging.exception("Could not set webhook automatically")
    port = int(os.environ.get("PORT", 5000))
    logging.info("Starting Flask server on port %s", port)
    app.run(host="0.0.0.0", port=port)
