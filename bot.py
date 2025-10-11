# bot.py — MoneyToFlows v16 (complet: webhook trace JSON + MLM + admin + data.json)
import os
import json
import threading
import asyncio
import logging
import sqlite3  # kept import if later needed; but storage uses data.json per request
from datetime import datetime
from flask import Flask, request, jsonify
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ---------------- CONFIG ----------------
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN not set. Put it in Render > Environment variables.")

# public URL for webhook (change if you rename service)
WEBHOOK_HOSTNAME = "https://moneytoflowsbot-16.onrender.com"

# Admin identity (you)
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "RUBENHRM777")  # without @
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))  # optional numeric id; 0 means not set

PRODUCT_PRICE = int(os.getenv("PRODUCT_PRICE", "5000"))  # FCFA
MIN_FILLEULS_FOR_WITHDRAW = int(os.getenv("MIN_FILLEULS_FOR_WITHDRAW", "5"))

DATA_FILE = "data.json"
DATA_LOCK = threading.Lock()

# ---------------- LOGGING ----------------
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    level=logging.DEBUG,  # debug to see webhook payloads
)
logger = logging.getLogger("moneytoflows-v16")

# ---------------- FLASK & TELEGRAM APP ----------------
app = Flask(__name__)
bot = Bot(token=TOKEN)
application = Application.builder().token(TOKEN).build()

# ---------------- Data file layout helpers ----------------
def default_data():
    return {
        "users": {},         # user_id -> {username, first_name, parrain_id, registered_at, mm_number, is_admin}
        "purchases": {},     # purchase_id -> {user_id, reference, validated, validated_at}
        "earnings": {},      # earning_id -> {user_id, amount, source_user_id, created_at, paid}
        "withdrawals": {},   # withdrawal_id -> {user_id, amount, mm_number, status, created_at}
        "counters": {        # simple counters for auto-increment ids
            "purchase_id": 0,
            "earning_id": 0,
            "withdrawal_id": 0
        }
    }

def load_data():
    with DATA_LOCK:
        if not os.path.exists(DATA_FILE):
            d = default_data()
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
            return d
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)

def save_data(data):
    with DATA_LOCK:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

# Initialize file if missing
data = load_data()
logger.info("✅ data.json initialized (v16)")

# ---------------- Utility helpers ----------------
def ensure_user_record_from_obj(user_obj):
    """user_obj: telegram.User"""
    data = load_data()
    uid = str(user_obj.id)
    if uid not in data["users"]:
        data["users"][uid] = {
            "user_id": user_obj.id,
            "username": user_obj.username or "",
            "first_name": user_obj.first_name or "",
            "parrain_id": None,
            "registered_at": datetime.utcnow().isoformat(),
            "mm_number": None,
            "is_admin": False
        }
        save_data(data)
    return data["users"][uid]

def set_parrain(child_id, parrain_id):
    data = load_data()
    uid = str(child_id)
    if uid in data["users"]:
        data["users"][uid]["parrain_id"] = parrain_id
        save_data(data)

def add_purchase_record(user_id, reference):
    data = load_data()
    pid = data["counters"]["purchase_id"] + 1
    data["counters"]["purchase_id"] = pid
    data["purchases"][str(pid)] = {
        "user_id": user_id,
        "reference": reference,
        "validated": False,
        "validated_at": None,
    }
    save_data(data)
    return pid

def validate_purchase(pid):
    data = load_data()
    p = data["purchases"].get(str(pid))
    if not p:
        return False, "purchase not found"
    if p["validated"]:
        return False, "already validated"
    p["validated"] = True
    p["validated_at"] = datetime.utcnow().isoformat()
    save_data(data)
    # credit parrain if exists
    buyer_id = p["user_id"]
    amt = credit_parrain_for_buyer(buyer_id)
    return True, amt

def credit_parrain_for_buyer(buyer_id):
    data = load_data()
    buyer = data["users"].get(str(buyer_id))
    if not buyer:
        return None
    parrain_id = buyer.get("parrain_id")
    if not parrain_id:
        return None
    # count validated acheteurs for parrain
    acheteurs = 0
    for pid, p in data["purchases"].items():
        if p["validated"]:
            b = str(p["user_id"])
            u = data["users"].get(b)
            if u and u.get("parrain_id") == parrain_id:
                acheteurs += 1
    pct = compute_pct(acheteurs)
    amount = int(PRODUCT_PRICE * pct)
    eid = data["counters"]["earning_id"] + 1
    data["counters"]["earning_id"] = eid
    data["earnings"][str(eid)] = {
        "user_id": parrain_id,
        "amount": amount,
        "source_user_id": buyer_id,
        "created_at": datetime.utcnow().isoformat(),
        "paid": False
    }
    save_data(data)

    # If parrain reached threshold (>= MIN_FILLEULS_FOR_WITHDRAW) ask for MM if not set
    if acheteurs >= MIN_FILLEULS_FOR_WITHDRAW:
        u = data["users"].get(str(parrain_id))
        if u:
            mm = u.get("mm_number")
            # notify parrain to set MM number if missing
            if not mm:
                try:
                    asyncio.create_task(application.bot.send_message(
                        chat_id=parrain_id,
                        text=f"🎉 Tu as maintenant {acheteurs} filleuls acheteurs validés. Pour recevoir ton premier retrait, enregistre ton numéro Mobile Money avec /setmm <numero>."
                    ))
                except Exception:
                    logger.exception("Could not notify parrain to set mm")
            # also notify admin there is a pending earning/possible withdrawal
            notify_admins(f"Parrain {parrain_id} reached {acheteurs} acheteurs. New earning credited: {amount} FCFA. Parain mm: {mm or '(not set)'}")
    return amount

def compute_pct(n_acheteurs):
    if n_acheteurs >= 100:
        return 0.40
    if n_acheteurs >= 50:
        return 0.30
    return 0.20

def get_parrain_stats(user_id):
    data = load_data()
    total_filleuls = 0
    acheteurs = 0
    for uid, u in data["users"].items():
        if u.get("parrain_id") == user_id:
            total_filleuls += 1
    for pid, p in data["purchases"].items():
        if p["validated"]:
            buyer = data["users"].get(str(p["user_id"]))
            if buyer and buyer.get("parrain_id") == user_id:
                acheteurs += 1
    pending = 0
    total = 0
    for eid, e in data["earnings"].items():
        if e["user_id"] == user_id:
            total += e["amount"]
            if not e["paid"]:
                pending += e["amount"]
    pct = int(compute_pct(acheteurs) * 100)
    return {"total_filleuls": total_filleuls, "acheteurs": acheteurs, "pending": pending, "total": total, "pct": pct}

def set_mm_number(user_id, mm):
    data = load_data()
    u = data["users"].get(str(user_id))
    if u:
        u["mm_number"] = mm
        save_data(data)
        # If there are pending earnings, create a withdrawal automatically?
        # We'll leave admin to confirm payment; but create a withdrawal request
        pending = 0
        for eid, e in data["earnings"].items():
            if e["user_id"] == user_id and not e["paid"]:
                pending += e["amount"]
        if pending > 0:
            wid = data["counters"]["withdrawal_id"] + 1
            data["counters"]["withdrawal_id"] = wid
            data["withdrawals"][str(wid)] = {
                "user_id": user_id,
                "amount": pending,
                "mm_number": mm,
                "status": "pending",
                "created_at": datetime.utcnow().isoformat()
            }
            save_data(data)
            notify_admins(f"Nouvelle demande de retrait automatique pour user {user_id} : {pending} FCFA (mm {mm})")

def create_withdrawal(user_id, amount, mm):
    data = load_data()
    wid = data["counters"]["withdrawal_id"] + 1
    data["counters"]["withdrawal_id"] = wid
    data["withdrawals"][str(wid)] = {
        "user_id": user_id,
        "amount": amount,
        "mm_number": mm,
        "status": "pending",
        "created_at": datetime.utcnow().isoformat()
    }
    # mark earnings as paid placeholder (to avoid duplicate requests)
    for eid, e in data["earnings"].items():
        if e["user_id"] == user_id and not e["paid"]:
            e["paid"] = True
    save_data(data)
    notify_admins(f"User {user_id} requested withdrawal {amount} FCFA (mm {mm})")
    return wid

def notify_admins(message):
    # find admin users in data, plus ADMIN_USERNAME/ID
    data = load_data()
    admin_ids = set()
    if ADMIN_ID:
        admin_ids.add(int(ADMIN_ID))
    # look for users flagged admin
    for uid, u in data["users"].items():
        if u.get("is_admin"):
            admin_ids.add(int(u["user_id"]))
    # also find by username
    for uid, u in data["users"].items():
        if u.get("username", "").lower() == ADMIN_USERNAME.lower():
            admin_ids.add(int(u["user_id"]))
    # send messages asynchronously
    for aid in admin_ids:
        try:
            asyncio.create_task(application.bot.send_message(chat_id=aid, text=message))
        except Exception:
            logger.exception("notify_admins failed for %s", aid)

# ---------------- HANDLERS (USER) ----------------
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    logger.info("Received /start from %s (%s)", user.username, user.id)
    ensure_user_record_from_obj(user)

    # parse deep-link: ref_123
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
    # set parrain only if not set before
    if parrain_id and parrain_id != user.id and row and row[3] is None:
        set_parrain(user.id, parrain_id)
        try:
            await context.bot.send_message(parrain_id, f"🎉 Nouveau filleul inscrit : @{user.username or user.first_name}")
        except Exception:
            logger.exception("Could not notify parrain")

    # generate referral link
    try:
        bot_username = (await context.bot.get_me()).username
    except Exception:
        bot_username = "MoneyToFlowsBot"
    refer_link = f"https://t.me/{bot_username}?start=ref_{user.id}"

    await update.message.reply_text(
        f"👋 Salut {user.first_name} !\n\n"
        f"Bienvenue dans MoneyToFlows 💸\n\n"
        f"🔗 Ton lien de parrainage : {refer_link}\n\n"
        "Commandes : /achat /confirm_purchase <ref> /parrainage /dashboard /setmm /retrait /help"
    )

async def achat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"🛒 Lien d'achat officiel:\nhttps://sgzxfbtn.mychariow.shop/prd_8ind83\n\nAprès achat, envoie la référence avec /confirm_purchase <REFERENCE>."
    )

async def confirm_purchase_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not context.args:
        await update.message.reply_text("Usage : /confirm_purchase <REFERENCE>")
        return
    reference = context.args[0]
    ensure_user_record_from_obj(user)
    pid = add_purchase_record(user.id, reference)
    await update.message.reply_text(f"✅ Référence reçue (ID {pid}). L'admin la validera sous peu.")
    # notify admins
    notify_admins(f"Nouvelle référence à valider : user {user.id} / @{user.username} / ref: {reference} (purchase_id: {pid})")

async def parrainage_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user_record_from_obj(user)
    try:
        bot_username = (await context.bot.get_me()).username
    except:
        bot_username = "MoneyToFlowsBot"
    link = f"https://t.me/{bot_username}?start=ref_{user.id}"
    await update.message.reply_text(f"💸 Ton lien de parrainage :\n{link}")

async def dashboard_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user_record_from_obj(user)
    stats = get_parrain_stats(user.id)
    await update.message.reply_text(
        f"📊 Tableau de bord\n\n"
        f"👥 Filleuls inscrits : {stats['total_filleuls']}\n"
        f"🛒 Filleuls acheteurs validés : {stats['acheteurs']}\n"
        f"💰 Gains totaux : {int(stats['total'])} FCFA\n"
        f"💵 Solde disponible : {int(stats['pending'])} FCFA\n"
        f"🔖 Taux actuel : {stats['pct']}%\n\n"
        f"🔔 Seuil retrait : {MIN_FILLEULS_FOR_WITHDRAW} filleuls acheteurs"
    )

async def setmm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not context.args:
        await update.message.reply_text("Usage : /setmm <numero_mobile>")
        return
    mm = context.args[0]
    ensure_user_record_from_obj(user)
    set_mm_number(user.id, mm)
    await update.message.reply_text(f"✅ Numéro Mobile Money enregistré : {mm}\nSi tu avais des gains en attente, une demande de retrait a été créée et l'admin en sera informé.")

async def retrait_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user_record_from_obj(user)
    stats = get_parrain_stats(user.id)
    if stats["acheteurs"] < MIN_FILLEULS_FOR_WITHDRAW:
        await update.message.reply_text(f"🚫 Il te faut au moins {MIN_FILLEULS_FOR_WITHDRAW} filleuls acheteurs validés. Actuels : {stats['acheteurs']}/{MIN_FILLEULS_FOR_WITHDRAW}")
        return
    row = get_user_row(user.id)
    mm = row[5] if row else None
    if not mm:
        await update.message.reply_text("📲 Enregistre ton numéro Mobile Money avec /setmm <numero> avant de demander le retrait.")
        return
    amount = int(stats["pending"])
    if amount <= 0:
        await update.message.reply_text("Tu n'as pas de solde disponible pour retrait.")
        return
    wid = create_withdrawal(user.id, amount, mm)
    await update.message.reply_text(f"✅ Demande de retrait enregistrée (ID {wid}) pour {amount} FCFA. L'admin te contactera.")

# ---------------- HANDLERS (ADMIN) ----------------
async def admin_register_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if (user.username or "").lower() != ADMIN_USERNAME.lower() and (ADMIN_ID and user.id != ADMIN_ID):
        await update.message.reply_text("❌ Commande réservée à l'administrateur.")
        return
    ensure_user_record_from_obj(user)
    data = load_data()
    data["users"][str(user.id)]["is_admin"] = True
    save_data(data)
    await update.message.reply_text("✅ Vous êtes enregistré comme administrateur du bot.")

async def validate_purchase_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    # check admin
    if (user.username or "").lower() != ADMIN_USERNAME.lower() and (ADMIN_ID and user.id != ADMIN_ID):
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
    ok, res = validate_purchase(pid)
    if not ok:
        await update.message.reply_text(f"Erreur: {res}")
        return
    amt = res
    await update.message.reply_text(f"Achat validé. Parrain crédité: {int(amt)} FCFA (si un parrain existait).")

async def stats_admin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if (user.username or "").lower() != ADMIN_USERNAME.lower() and (ADMIN_ID and user.id != ADMIN_ID):
        await update.message.reply_text("❌ Commande réservée à l'admin.")
        return
    data = load_data()
    total_users = len(data["users"])
    total_valid_purchases = sum(1 for p in data["purchases"].values() if p["validated"])
    total_earnings = sum(e["amount"] for e in data["earnings"].values())
    pending_withdrawals = sum(1 for w in data["withdrawals"].values() if w["status"] == "pending")
    await update.message.reply_text(
        f"📈 Stats Admin\n\nUtilisateurs: {total_users}\nAchats validés: {total_valid_purchases}\nGains totaux: {int(total_earnings)} FCFA\nRetraits en attente: {pending_withdrawals}"
    )

async def pay_withdrawal_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if (user.username or "").lower() != ADMIN_USERNAME.lower() and (ADMIN_ID and user.id != ADMIN_ID):
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
    data = load_data()
    w = data["withdrawals"].get(str(wid))
    if not w:
        await update.message.reply_text("Retrait introuvable.")
        return
    w["status"] = "paid"
    save_data(data)
    await update.message.reply_text(f"✅ Retrait {wid} marqué comme payé.")

# ---------------- TEXT HANDLER ----------------
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user = update.effective_user
    ensure_user_record_from_obj(user)
    row = get_user_row(user.id)
    # accept phone numbers directly if user replies with MM number
    cleaned = text.replace("+", "").replace(" ", "")
    if row and (row[5] is None) and cleaned.isdigit() and 6 <= len(cleaned) <= 15:
        set_mm_number(user.id, text)
        await update.message.reply_text(f"✅ Numéro Mobile Money enregistré : {text}")
        return
    await update.message.reply_text("Commande non reconnue. Utilise /help pour la liste des commandes.")

# ---------------- REGISTER HANDLERS ----------------
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

# ---------------- START TELEGRAM APP IN BACKGROUND ----------------
def _start_telegram_app_in_background():
    def _runner():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(application.initialize())
        loop.run_until_complete(application.start())
        logger.info("✅ Telegram Application started in background loop (v16)")
        loop.run_forever()
    t = threading.Thread(target=_runner, daemon=True)
    t.start()

_start_telegram_app_in_background()

# ---------------- WEBHOOK (TRACE + QUEUE) ----------------
@app.route(f"/{TOKEN}", methods=["POST"])
def webhook_endpoint():
    try:
        data_payload = request.get_json(force=True)
        # TRACE: print full JSON payload into logs for debugging
        logger.debug("➡️ Webhook payload (raw): %s", json.dumps(data_payload, ensure_ascii=False))
        # Convert to Update and enqueue
        update = Update.de_json(data_payload, bot)
        application.update_queue.put_nowait(update)
        logger.info("✅ Update enqueued (type: %s)", "message" if "message" in data_payload else "update")
    except Exception:
        logger.exception("Error processing update")
        return "error", 500
    return "ok", 200

@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "ok", "service": "MoneyToFlows", "version": "v16"}), 200

# ---------------- MAIN (local run) ----------------
if __name__ == "__main__":
    # try to set webhook automatically when running locally
    try:
        url = f"{WEBHOOK_HOSTNAME}/{TOKEN}"
        bot.set_webhook(url=url)
        logger.info("Webhook set automatically: %s", url)
    except Exception:
        logger.exception("Could not set webhook automatically.")
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
