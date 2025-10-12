# bot.py — MoneyToFlows v18 (final)
import os
import json
import threading
import asyncio
import logging
from datetime import datetime
from flask import Flask, request, jsonify
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# ---------------- CONFIG ----------------
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN not set in environment (Render > Environment variables).")

# Change if your Render service uses another hostname
WEBHOOK_HOSTNAME = os.getenv("WEBHOOK_HOSTNAME", "https://moneytoflowsbot-18.onrender.com")

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "RUBENHRM777")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))  # put your numeric Telegram id in Render env if you want
PRODUCT_PRICE = int(os.getenv("PRODUCT_PRICE", "5000"))
MIN_FILLEULS_FOR_WITHDRAW = int(os.getenv("MIN_FILLEULS_FOR_WITHDRAW", "5"))
DATA_FILE = os.getenv("DATA_FILE", "data.json")
DATA_LOCK = threading.Lock()

# ---------------- LOGGING ----------------
logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s - %(message)s", level=logging.DEBUG)
logger = logging.getLogger("moneytoflows-v18")

# ---------------- FLASK + TELEGRAM app ----------------
app = Flask(__name__)

# Create application instance (python-telegram-bot)
application = Application.builder().token(TOKEN).build()

# ---------------- Data (simple JSON) ----------------
def default_data():
    return {
        "users": {},
        "purchases": {},
        "earnings": {},
        "withdrawals": {},
        "counters": {"purchase_id": 0, "earning_id": 0, "withdrawal_id": 0}
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

def save_data(d):
    with DATA_LOCK:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)

# initialize file if missing
data = load_data()
logger.info("✅ data.json initialized (v18)")

# ---------------- Helpers (same logic as v16/v17) ----------------
def ensure_user_record_from_obj(user_obj):
    d = load_data()
    uid = str(user_obj.id)
    if uid not in d["users"]:
        d["users"][uid] = {
            "user_id": user_obj.id,
            "username": user_obj.username or "",
            "first_name": user_obj.first_name or "",
            "parrain_id": None,
            "registered_at": datetime.utcnow().isoformat(),
            "mm_number": None,
            "is_admin": False
        }
        save_data(d)
    return d["users"][uid]

def get_user_row(user_id):
    d = load_data()
    return d["users"].get(str(user_id))

def set_parrain(child_id, parrain_id):
    d = load_data()
    u = d["users"].get(str(child_id))
    if u:
        u["parrain_id"] = parrain_id
        save_data(d)

def add_purchase_record(user_id, reference):
    d = load_data()
    pid = d["counters"]["purchase_id"] + 1
    d["counters"]["purchase_id"] = pid
    d["purchases"][str(pid)] = {
        "user_id": user_id,
        "reference": reference,
        "validated": False,
        "validated_at": None
    }
    save_data(d)
    return pid

def compute_pct(n_acheteurs):
    if n_acheteurs >= 100:
        return 0.40
    if n_acheteurs >= 50:
        return 0.30
    return 0.20

def count_validated_acheteurs(parrain_id):
    d = load_data()
    cnt = 0
    for p in d["purchases"].values():
        if p["validated"]:
            b = str(p["user_id"])
            u = d["users"].get(b)
            if u and u.get("parrain_id") == parrain_id:
                cnt += 1
    return cnt

def credit_parrain_for_buyer(buyer_id):
    d = load_data()
    buyer = d["users"].get(str(buyer_id))
    if not buyer:
        return None
    parrain = buyer.get("parrain_id")
    if not parrain:
        return None
    acheteurs = count_validated_acheteurs(parrain)
    pct = compute_pct(acheteurs)
    amount = int(PRODUCT_PRICE * pct)
    eid = d["counters"]["earning_id"] + 1
    d["counters"]["earning_id"] = eid
    d["earnings"][str(eid)] = {
        "user_id": parrain,
        "amount": amount,
        "source_user_id": buyer_id,
        "created_at": datetime.utcnow().isoformat(),
        "paid": False
    }
    save_data(d)
    # notify parrain if reached threshold and mm not set
    if acheteurs >= MIN_FILLEULS_FOR_WITHDRAW:
        u = d["users"].get(str(parrain))
        mm = u.get("mm_number") if u else None
        if not mm:
            try:
                asyncio.create_task(application.bot.send_message(parrain, text=(
                    f"🎉 Tu as {acheteurs} filleuls acheteurs validés. Enregistre ton numéro Mobile Money avec /setmm <numero> pour recevoir tes gains."
                )))
            except Exception:
                logger.exception("notify parrain failed")
        notify_admins(f"Parrain {parrain} reached {acheteurs} acheteurs. New earning: {amount} FCFA. MM: {mm or '(not set)'}")
    return amount

def get_parrain_stats(user_id):
    d = load_data()
    total_filleuls = sum(1 for u in d["users"].values() if u.get("parrain_id") == user_id)
    acheteurs = 0
    for p in d["purchases"].values():
        if p["validated"]:
            buyer = d["users"].get(str(p["user_id"]))
            if buyer and buyer.get("parrain_id") == user_id:
                acheteurs += 1
    pending = sum(e["amount"] for e in d["earnings"].values() if e["user_id"] == user_id and not e["paid"])
    total = sum(e["amount"] for e in d["earnings"].values() if e["user_id"] == user_id)
    pct = int(compute_pct(acheteurs) * 100)
    return {"total_filleuls": total_filleuls, "acheteurs": acheteurs, "pending": pending, "total": total, "pct": pct}

def set_mm_number(user_id, mm):
    d = load_data()
    u = d["users"].get(str(user_id))
    if u:
        u["mm_number"] = mm
        # create auto-withdrawal if pending
        pending = sum(e["amount"] for e in d["earnings"].values() if e["user_id"] == user_id and not e["paid"])
        if pending > 0:
            wid = d["counters"]["withdrawal_id"] + 1
            d["counters"]["withdrawal_id"] = wid
            d["withdrawals"][str(wid)] = {
                "user_id": user_id,
                "amount": pending,
                "mm_number": mm,
                "status": "pending",
                "created_at": datetime.utcnow().isoformat()
            }
            for e in d["earnings"].values():
                if e["user_id"] == user_id and not e["paid"]:
                    e["paid"] = True
            notify_admins(f"New withdrawal auto-created for {user_id}: {pending} FCFA (mm {mm})")
        save_data(d)

def create_withdrawal(user_id, amount, mm):
    d = load_data()
    wid = d["counters"]["withdrawal_id"] + 1
    d["counters"]["withdrawal_id"] = wid
    d["withdrawals"][str(wid)] = {"user_id": user_id, "amount": amount, "mm_number": mm, "status": "pending", "created_at": datetime.utcnow().isoformat()}
    # mark earnings as paid
    for e in d["earnings"].values():
        if e["user_id"] == user_id and not e["paid"]:
            e["paid"] = True
    save_data(d)
    notify_admins(f"User {user_id} requested withdrawal {amount} FCFA (mm {mm})")
    return wid

def notify_admins(message):
    d = load_data()
    admin_ids = set()
    if ADMIN_ID:
        admin_ids.add(ADMIN_ID)
    for uid, u in d["users"].items():
        if u.get("is_admin"):
            try:
                admin_ids.add(int(u["user_id"]))
            except:
                pass
        if (u.get("username") or "").lower() == ADMIN_USERNAME.lower():
            try:
                admin_ids.add(int(u["user_id"]))
            except:
                pass
    for aid in admin_ids:
        try:
            asyncio.create_task(application.bot.send_message(chat_id=aid, text=message))
        except Exception:
            logger.exception("notify_admins failed for %s", aid)

# ---------------- Handlers (users) ----------------
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    logger.info("Handler /start called for %s (%s)", user.username, user.id)
    ensure_user_record_from_obj(user)
    # parse deep link
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
    if parrain_id and parrain_id != user.id and row and row.get("parrain_id") is None:
        set_parrain(user.id, parrain_id)
        try:
            await context.bot.send_message(parrain_id, f"🎉 Nouveau filleul inscrit : @{user.username or user.first_name}")
        except Exception:
            logger.exception("notify parrain failed")
    try:
        bot_username = (await context.bot.get_me()).username
    except Exception:
        bot_username = "MoneyToFlowsBot"
    refer_link = f"https://t.me/{bot_username}?start=ref_{user.id}"
    await update.message.reply_text(
        f"👋 Salut {user.first_name} !\n\nBienvenue dans MoneyToFlows 🤑💸\n\n🔗 Ton lien de parrainage : {refer_link}\n\nCommandes : /achat /confirm_purchase <ref> /parrainage /dashboard /setmm /retrait /help"
    )

async def achat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"🛒 Lien d'achat officiel:\nhttps://sgzxfbtn.mychariow.shop/prd_8ind83\n\nAprès achat, envoie la référence avec /confirm_purchase <REFERENCE>.")

async def confirm_purchase_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not context.args:
        await update.message.reply_text("Usage : /confirm_purchase <REFERENCE>")
        return
    reference = context.args[0]
    ensure_user_record_from_obj(user)
    pid = add_purchase_record(user.id, reference)
    await update.message.reply_text(f"✅ Référence reçue (ID {pid}). L'admin la validera sous peu.")
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
        f"📊 Tableau de bord\n\n👥 Filleuls inscrits : {stats['total_filleuls']}\n🛒 Filleuls acheteurs validés : {stats['acheteurs']}\n💰 Gains totaux : {int(stats['total'])} FCFA\n💵 Solde disponible : {int(stats['pending'])} FCFA\n🔖 Taux actuel : {stats['pct']}%\n\n🔔 Seuil retrait : {MIN_FILLEULS_FOR_WITHDRAW} filleuls acheteurs"
    )

async def setmm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not context.args:
        await update.message.reply_text("Usage : /setmm <numero_mobile>")
        return
    mm = context.args[0]
    ensure_user_record_from_obj(user)
    set_mm_number(user.id, mm)
    await update.message.reply_text(f"✅ Numéro Mobile Money enregistré : {mm}\nSi tu avais des gains en attente, une demande de retrait a été créée et l'admin en a été informé.")

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

# ---------------- Handlers (admin) ----------------
async def admin_register_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if (user.username or "").lower() != ADMIN_USERNAME.lower() and (ADMIN_ID and user.id != ADMIN_ID):
        await update.message.reply_text("❌ Commande réservée à l'administrateur.")
        return
    ensure_user_record_from_obj(user)
    d = load_data()
    d["users"][str(user.id)]["is_admin"] = True
    save_data(d)
    await update.message.reply_text("✅ Vous êtes enregistré comme administrateur du bot.")

async def validate_purchase_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
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
    ok_res = validate_purchase(pid)
    if isinstance(ok_res, tuple):
        ok, res = ok_res
    else:
        ok, res = ok_res if isinstance(ok_res, tuple) else (True, ok_res)
    if not ok:
        await update.message.reply_text(f"Erreur : {res}")
        return
    amt = res
    await update.message.reply_text(f"Achat validé. Parrain crédité: {int(amt)} FCFA (si un parrain existait).")

async def stats_admin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if (user.username or "").lower() != ADMIN_USERNAME.lower() and (ADMIN_ID and user.id != ADMIN_ID):
        await update.message.reply_text("❌ Commande réservée à l'admin.")
        return
    d = load_data()
    total_users = len(d["users"])
    total_valid_purchases = sum(1 for p in d["purchases"].values() if p["validated"])
    total_earnings = sum(e["amount"] for e in d["earnings"].values())
    pending_withdrawals = sum(1 for w in d["withdrawals"].values() if w["status"] == "pending")
    await update.message.reply_text(f"📈 Stats Admin\n\nUtilisateurs: {total_users}\nAchats validés: {total_valid_purchases}\nGains totaux: {int(total_earnings)} FCFA\nRetraits en attente: {pending_withdrawals}")

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
    d = load_data()
    w = d["withdrawals"].get(str(wid))
    if not w:
        await update.message.reply_text("Retrait introuvable.")
        return
    w["status"] = "paid"
    save_data(d)
    await update.message.reply_text(f"✅ Retrait {wid} marqué comme payé.")

# ---------------- Text handler ----------------
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user = update.effective_user
    ensure_user_record_from_obj(user)
    row = get_user_row(user.id)
    cleaned = text.replace("+", "").replace(" ", "")
    if row and (row[5] is None) and cleaned.isdigit() and 6 <= len(cleaned) <= 15:
        set_mm_number(user.id, text)
        await update.message.reply_text(f"✅ Numéro Mobile Money enregistré : {text}")
        return
    await update.message.reply_text("Commande non reconnue. Utilise /help pour la liste des commandes.")

# ---------------- Register handlers ----------------
application.add_handler(CommandHandler("start", start_handler))
application.add_handler(CommandHandler("achat", achat_handler))
application.add_handler(CommandHandler("confirm_purchase", confirm_purchase_handler))
application.add_handler(CommandHandler("parrainage", parrainage_handler))
application.add_handler(CommandHandler("dashboard", dashboard_handler))
application.add_handler(CommandHandler("setmm", setmm_handler))
application.add_handler(CommandHandler("retrait", retrait_handler))
application.add_handler(CommandHandler("help", start_handler))

application.add_handler(CommandHandler("admin_register", admin_register_handler))
application.add_handler(CommandHandler("validate_purchase", validate_purchase_handler))
application.add_handler(CommandHandler("stats_admin", stats_admin_handler))
application.add_handler(CommandHandler("pay_withdrawal", pay_withdrawal_handler))

application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

# ---------------- Start telegram app in background (safe for Gunicorn) ----------------
def _start_telegram_app_in_background():
    def _runner():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(application.initialize())
        loop.run_until_complete(application.start())
        logger.info("✅ Telegram Application started in background loop (v18)")
        loop.run_forever()
    t = threading.Thread(target=_runner, daemon=True)
    t.start()

_start_telegram_app_in_background()

# ---------------- WEBHOOK endpoint (fast response to Telegram to avoid 502) ----------------
@app.route(f"/{TOKEN}", methods=["POST"])
def webhook_endpoint():
    try:
        payload = request.get_json(force=True)
        # trace full payload in logs for debugging
        logger.debug("➡️ Webhook payload (raw): %s", json.dumps(payload, ensure_ascii=False))
        # convert JSON to Update and enqueue (fast)
        update = Update.de_json(payload, application.bot)
        application.update_queue.put_nowait(update)
        # Return a quick valid JSON 200 response to Telegram to avoid timeouts/502
        return jsonify({"ok": True}), 200
    except Exception:
        logger.exception("Error processing update")
        # return 500 so Telegram logs it as error; but we tried our best
        return jsonify({"ok": False}), 500

@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "ok", "service": "MoneyToFlows", "version": "v18"}), 200

# ---------------- MAIN ----------------
if __name__ == "__main__":
    # try to set webhook automatically when running locally (harmless if already set)
    try:
        url = f"{WEBHOOK_HOSTNAME}/{TOKEN}"
        # set webhook using bot from application
        app.logger.info("Trying to set webhook automatically to %s", url)
        # note: application.bot is available after building; use a short async call
        async def _set_wh():
            try:
                await application.bot.set_webhook(url=url)
                logger.info("Webhook set automatically: %s", url)
            except Exception:
                logger.exception("Could not set webhook automatically.")
        # run it in a new loop only when running as script
        asyncio.get_event_loop().run_until_complete(_set_wh())
    except Exception:
        logger.exception("Webhook automatic set failed.")
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
