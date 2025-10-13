#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MoneyToFlowsBot - version complète (MLM, parrainage, retraits Mobile Money, admin)
Conçu pour Render Web Service (gunicorn bot:app_flask) + polling en thread.
Compatible python-telegram-bot==21.4
"""

import os
import re
import threading
import logging
import sqlite3
from datetime import datetime
from typing import Optional

from flask import Flask
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ---------------- CONFIG ----------------
TOKEN = os.getenv("TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")          # optionnel : ton ID Telegram (numérique)
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "@RUBENHRM777")
ACHAT_LINK = os.getenv("ACHAT_LINK", "https://sgzxfbtn.mychariow.shop/prd_8ind83")
PRODUCT_NAME = os.getenv("PRODUCT_NAME", "Pack Formations Business 2026")
SEUIL_RECOMPENSE = int(os.getenv("SEUIL_RECOMPENSE", "5"))
DB_FILE = os.getenv("DB_FILE", "referral_bot.db")
PHONE_REGEX = re.compile(r"^\+?\d{6,15}$")  # accepte +2426..., 06xxxxxx, etc.
# ----------------------------------------

if not TOKEN:
    raise RuntimeError("La variable d'environnement TOKEN n'est pas définie. Ajoute-la dans Render.")

# normalize ADMIN_ID
if ADMIN_ID:
    try:
        ADMIN_ID = int(ADMIN_ID)
    except:
        ADMIN_ID = None

# Logging
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------- DATABASE ----------------
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        telegram_id INTEGER PRIMARY KEY,
        username TEXT,
        ref_code TEXT UNIQUE,
        referrer_code TEXT,
        purchases INTEGER DEFAULT 0,
        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS referrals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        referrer_code TEXT,
        referred_telegram_id INTEGER,
        referred_username TEXT,
        joined_at TEXT
    );

    CREATE TABLE IF NOT EXISTS withdrawals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER,
        mobile_number TEXT,
        status TEXT DEFAULT 'pending', -- pending | validated | refused | waiting_number
        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS rewards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER,
        amount REAL,
        status TEXT DEFAULT 'pending',
        created_at TEXT
    );
    """)
    conn.commit()
    conn.close()

def db_execute(query: str, params: tuple = (), fetch: bool = False):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute(query, params)
    if fetch:
        rows = cur.fetchall()
        conn.commit()
        conn.close()
        return rows
    conn.commit()
    conn.close()
    return None

# ---------------- HELPERS DB ----------------
def get_user_by_telegram(tid: int) -> Optional[tuple]:
    rows = db_execute("SELECT telegram_id, username, ref_code, referrer_code, purchases, created_at FROM users WHERE telegram_id = ?", (tid,), True)
    return rows[0] if rows else None

def get_user_by_code(code: str) -> Optional[tuple]:
    rows = db_execute("SELECT telegram_id, username, ref_code FROM users WHERE ref_code = ?", (code,), True)
    return rows[0] if rows else None

def create_user(tid: int, username: str, referrer_code: Optional[str] = None) -> tuple:
    # simple ref_code generator: hex + timestamp fragment to reduce collision risk
    base = f"{tid:x}"
    code = (base + datetime.utcnow().strftime("%m%d%H%M%S"))[-12:]
    created_at = datetime.utcnow().isoformat()
    db_execute("INSERT OR IGNORE INTO users (telegram_id, username, ref_code, referrer_code, purchases, created_at) VALUES (?, ?, ?, ?, 0, ?)",
               (tid, username, code, referrer_code, created_at))
    return get_user_by_telegram(tid)

def add_referral(referrer_code: str, referred_telegram_id: int, referred_username: str):
    db_execute("INSERT INTO referrals (referrer_code, referred_telegram_id, referred_username, joined_at) VALUES (?, ?, ?, ?)",
               (referrer_code, referred_telegram_id, referred_username, datetime.utcnow().isoformat()))

def count_referred(referrer_code: str) -> int:
    rows = db_execute("SELECT COUNT(*) FROM referrals WHERE referrer_code = ?", (referrer_code,), True)
    return rows[0][0] if rows else 0

def count_referred_with_purchase(referrer_code: str) -> int:
    rows = db_execute(
        "SELECT COUNT(*) FROM referrals r JOIN users u ON r.referred_telegram_id = u.telegram_id WHERE r.referrer_code = ? AND u.purchases > 0",
        (referrer_code,), True)
    return rows[0][0] if rows else 0

def increment_purchase(tid: int):
    db_execute("UPDATE users SET purchases = purchases + 1 WHERE telegram_id = ?", (tid,))

def create_withdrawal_request(tid: int, mobile: str, status: str = "pending"):
    db_execute("INSERT INTO withdrawals (telegram_id, mobile_number, status, created_at) VALUES (?, ?, ?, ?)",
               (tid, mobile, status, datetime.utcnow().isoformat()))

def list_withdrawals(status: Optional[str] = None):
    if status:
        return db_execute("SELECT id, telegram_id, mobile_number, status, created_at FROM withdrawals WHERE status = ? ORDER BY id DESC", (status,), True) or []
    return db_execute("SELECT id, telegram_id, mobile_number, status, created_at FROM withdrawals ORDER BY id DESC", (), True) or []

def set_withdrawal_status(wid: int, status: str):
    db_execute("UPDATE withdrawals SET status = ? WHERE id = ?", (status, wid))

# ---------------- ADMIN CHECK ----------------
def is_admin_user(user) -> bool:
    try:
        if ADMIN_ID and hasattr(user, "id") and user.id == ADMIN_ID:
            return True
        if hasattr(user, "username") and user.username:
            admin_name = ADMIN_USERNAME.lstrip("@").lower()
            if user.username.lower() == admin_name:
                return True
    except Exception:
        pass
    return False

# ---------------- TELEGRAM HANDLERS ----------------
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args
    payload = args[0] if args else None
    referrer_code = None
    if payload and payload.startswith("ref_"):
        referrer_code = payload.split("ref_", 1)[1]

    if not get_user_by_telegram(user.id):
        create_user(user.id, user.username or user.full_name or str(user.id), referrer_code)
        if referrer_code:
            # record referral
            add_referral(referrer_code, user.id, user.username or user.first_name or "")
            # notify parrain if possible
            ref = get_user_by_code(referrer_code)
            if ref:
                try:
                    await context.bot.send_message(chat_id=ref[0], text=f"🎉 Nouveau filleul ! @{user.username or user.first_name} vient de s'inscrire via ton lien.")
                except Exception:
                    pass

    txt = (
        f"👋 Bonjour {user.first_name} !\n\n"
        f"Bienvenue sur le bot officiel *{PRODUCT_NAME}*.\n\n"
        "Commandes utiles :\n"
        "/achat → Lien d'achat\n"
        "/parrainage → Obtenir ton lien unique\n"
        "/dashboard → Voir tes statistiques\n"
        "/retrait → Demander un retrait (après 5 filleuls acheteurs)\n"
        "/confachat <REF> → Envoyer référence d'achat\n"
        "/aide → Aide\n"
    )
    await update.message.reply_text(txt)

async def achat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"🛍️ Lien officiel pour acheter *{PRODUCT_NAME}* :\n{ACHAT_LINK}\n\n"
        "Après ton achat, envoie la référence avec : /confachat <REFERENCE>"
    )

async def parrainage_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = get_user_by_telegram(user.id) or create_user(user.id, user.username or user.full_name or str(user.id))
    ref_code = u[2]
    deep_link = f"https://t.me/{context.bot.username}?start=ref_{ref_code}"
    await update.message.reply_text(
        f"🔗 Ton lien de parrainage :\n{deep_link}\n\n"
        f"⚠️ Rappel : le retrait est disponible uniquement après {SEUIL_RECOMPENSE} filleuls acheteurs."
    )

async def dashboard_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = get_user_by_telegram(user.id)
    if not u:
        await update.message.reply_text("Tu n'es pas encore enregistré. Fais /start.")
        return
    ref_code = u[2]
    total = count_referred(ref_code)
    acheteurs = count_referred_with_purchase(ref_code)
    purchases = u[4]
    eligible_text = "✅ OUI" if acheteurs >= SEUIL_RECOMPENSE else f"❌ NON (encore {SEUIL_RECOMPENSE - acheteurs})"
    txt = (
        "📊 TON TABLEAU DE BORD\n\n"
        f"👤 @{user.username}\n"
        f"🔗 Ton code : {ref_code}\n"
        f"👥 Filleuls inscrits : {total}\n"
        f"🛒 Filleuls acheteurs : {acheteurs}\n"
        f"🎯 Tes achats personnels : {purchases}\n"
        f"🏆 Éligible au retrait : {eligible_text}\n\n"
        f"⚠️ Le retrait est disponible UNIQUEMENT après {SEUIL_RECOMPENSE} filleuls acheteurs."
    )
    await update.message.reply_text(txt)

async def confachat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not context.args:
        await update.message.reply_text("Usage : /confachat <REFERENCE>")
        return
    reference = context.args[0]
    admin_msg = f"🔔 Nouvelle demande de validation d'achat : @{user.username or user.first_name} (ID:{user.id})\nRéf: {reference}\nUtilise /addpurchase <telegram_id> pour valider."
    try:
        if ADMIN_ID:
            await context.bot.send_message(chat_id=ADMIN_ID, text=admin_msg)
        else:
            await context.bot.send_message(chat_id=ADMIN_USERNAME, text=admin_msg)
    except Exception:
        pass
    await update.message.reply_text("✅ Ta référence a été envoyée à l'admin pour validation.")

# retrait flow: ask for number and create a withdrawal row with status waiting_number
async def retrait_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = get_user_by_telegram(user.id)
    if not u:
        await update.message.reply_text("Tu n'es pas enregistré. Envoie /start pour t'inscrire.")
        return
    ref_code = u[2]
    acheteurs = count_referred_with_purchase(ref_code)
    if acheteurs < SEUIL_RECOMPENSE:
        await update.message.reply_text(f"🚫 Tu as {acheteurs} filleuls acheteurs. Il en faut {SEUIL_RECOMPENSE} pour débloquer le retrait.")
        return
    # create waiting entry
    db_execute("INSERT INTO withdrawals (telegram_id, mobile_number, status, created_at) VALUES (?, ?, 'waiting_number', ?)",
               (user.id, '', datetime.utcnow().isoformat()))
    await update.message.reply_text(
        "✅ Tu es éligible au retrait.\n"
        "Envoie maintenant TON NUMÉRO Mobile Money (ex: +2426xxxxxxx ou 06xxxxxx) dans ce chat pour créer ta demande."
    )

# capture plain text messages (phone numbers) for withdrawals in waiting_number state
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    user = update.effective_user
    # check if this user has an entry waiting for number
    rows = db_execute("SELECT id FROM withdrawals WHERE telegram_id = ? AND status = 'waiting_number'", (user.id,), True)
    if rows:
        if PHONE_REGEX.match(text):
            wid = rows[0][0]
            db_execute("UPDATE withdrawals SET mobile_number = ?, status = 'pending', created_at = ? WHERE id = ?",
                       (text, datetime.utcnow().isoformat(), wid))
            await update.message.reply_text("✅ Numéro reçu. Ta demande de retrait a été envoyée à l'admin pour validation.")
            # notify admin with withdrawal id
            admin_text = f"🔔 Nouvelle demande de retrait (id:{wid}) : @{user.username or user.first_name} (ID:{user.id})\nNuméro: {text}\nValide: /valider_retrait {wid}\nRefuser: /refuser_retrait {wid}"
            try:
                if ADMIN_ID:
                    await context.bot.send_message(chat_id=ADMIN_ID, text=admin_text)
                else:
                    await context.bot.send_message(chat_id=ADMIN_USERNAME, text=admin_text)
            except Exception:
                pass
        else:
            await update.message.reply_text("❌ Numéro non reconnu. Envoie ton numéro Mobile Money (ex: +2426xxxxxxx ou 06xxxxxx).")
    else:
        # not a withdrawal flow message — ignore or keep
        return

# ---------------- ADMIN ACTIONS ----------------
async def admin_stats_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update.effective_user):
        await update.message.reply_text("Commande réservée à l'admin.")
        return
    total_users = (db_execute("SELECT COUNT(*) FROM users", (), True) or [[0]])[0][0]
    total_buyers = (db_execute("SELECT COUNT(*) FROM users WHERE purchases>0", (), True) or [[0]])[0][0]
    total_referrals = (db_execute("SELECT COUNT(*) FROM referrals", (), True) or [[0]])[0][0]
    pending_withdrawals = (db_execute("SELECT COUNT(*) FROM withdrawals WHERE status='pending'", (), True) or [[0]])[0][0]
    await update.message.reply_text(
        f"📈 STATISTIQUES\nMembres: {total_users}\nAcheteurs: {total_buyers}\nReferrals: {total_referrals}\nRetraits en attente: {pending_withdrawals}"
    )

async def addpurchase_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # usage: /addpurchase <telegram_id>
    if not is_admin_user(update.effective_user):
        await update.message.reply_text("Commande admin seulement.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /addpurchase <telegram_id>")
        return
    try:
        tid = int(context.args[0])
    except:
        await update.message.reply_text("ID Telegram invalide.")
        return
    increment_purchase(tid)
    # notify referrer if exists
    user = get_user_by_telegram(tid)
    if user and user[3]:
        ref_code = user[3]
        ref_user = get_user_by_code(ref_code)
        if ref_user:
            try:
                await context.bot.send_message(chat_id=ref_user[0], text=f"✅ Ton filleul @{user[1]} a acheté le produit.")
            except Exception:
                pass
    await update.message.reply_text("✅ Achat enregistré.")

async def list_retraits_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update.effective_user):
        await update.message.reply_text("Commande admin seulement.")
        return
    rows = list_withdrawals("pending")
    if not rows:
        await update.message.reply_text("Aucune demande de retrait en attente.")
        return
    txt = "🔔 Retraits en attente :\n"
    for r in rows:
        wid, tid, mobile, status, created = r
        user = get_user_by_telegram(tid)
        uname = user[1] if user else "unknown"
        txt += f"id:{wid} - @{uname} (ID:{tid}) - {mobile} - {created}\n"
    await update.message.reply_text(txt)

async def valider_retrait_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /valider_retrait <withdrawal_id>
    if not is_admin_user(update.effective_user):
        await update.message.reply_text("Commande admin seulement.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /valider_retrait <withdrawal_id>")
        return
    try:
        wid = int(context.args[0])
    except:
        await update.message.reply_text("ID retrait invalide.")
        return
    set_withdrawal_status(wid, "validated")
    row = db_execute("SELECT telegram_id, mobile_number FROM withdrawals WHERE id = ?", (wid,), True)
    if row:
        tid, mobile = row[0]
        try:
            await context.bot.send_message(chat_id=tid, text=f"🎉 Ton retrait (id:{wid}) a été validé. Paiement via Mobile Money ({mobile}) sera effectué par l'admin.")
        except Exception:
            pass
    await update.message.reply_text("✅ Retrait validé et utilisateur notifié.")

async def refuser_retrait_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /refuser_retrait <withdrawal_id> <raison_opt>
    if not is_admin_user(update.effective_user):
        await update.message.reply_text("Commande admin seulement.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /refuser_retrait <withdrawal_id> <raison_opt>")
        return
    try:
        wid = int(context.args[0])
    except:
        await update.message.reply_text("ID retrait invalide.")
        return
    reason = " ".join(context.args[1:]) if len(context.args) > 1 else "Aucune raison fournie."
    set_withdrawal_status(wid, "refused")
    row = db_execute("SELECT telegram_id FROM withdrawals WHERE id = ?", (wid,), True)
    if row:
        tid = row[0][0]
        try:
            await context.bot.send_message(chat_id=tid, text=f"❌ Ton retrait (id:{wid}) a été refusé. Raison: {reason}")
        except Exception:
            pass
    await update.message.reply_text("✅ Retrait refusé et utilisateur notifié.")

async def list_eligibles_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update.effective_user):
        await update.message.reply_text("Commande admin seulement.")
        return
    rows = db_execute("SELECT telegram_id, username, ref_code FROM users", (), True) or []
    res = []
    for r in rows:
        tid, uname, code = r
        cnt = count_referred_with_purchase(code)
        if cnt >= SEUIL_RECOMPENSE:
            res.append((tid, uname, code, cnt))
    if not res:
        await update.message.reply_text("Aucun eligible pour le moment.")
        return
    txt = "💰 Eligibles:\n"
    for t in res:
        txt += f"ID:{t[0]} @{t[1]} code:{t[2]} acheteurs:{t[3]}\n"
    await update.message.reply_text(txt)

# ---------------- REGISTER & BOT THREAD ----------------
def register_handlers(app):
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("achat", achat_handler))
    app.add_handler(CommandHandler("parrainage", parrainage_handler))
    app.add_handler(CommandHandler("dashboard", dashboard_handler))
    app.add_handler(CommandHandler("confachat", confachat_handler))
    app.add_handler(CommandHandler("retrait", retrait_handler))
    # admin
    app.add_handler(CommandHandler("admin_stats", admin_stats_handler))
    app.add_handler(CommandHandler("addpurchase", addpurchase_handler))
    app.add_handler(CommandHandler("list_retraits", list_retraits_handler))
    app.add_handler(CommandHandler("valider_retrait", valider_retrait_handler))
    app.add_handler(CommandHandler("refuser_retrait", refuser_retrait_handler))
    app.add_handler(CommandHandler("list_eligibles", list_eligibles_handler))
    # capture phone numbers / generic text
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), text_handler))

def run_telegram_bot():
    app = ApplicationBuilder().token(TOKEN).build()
    register_handlers(app)
    logger.info("Démarrage du bot Telegram (polling) en thread...")
    print("🤖 Bot Telegram démarré (polling).")
    app.run_polling()

# ---------------- FLASK (pour Render) ----------------
app_flask = Flask(__name__)

@app_flask.route("/")
def home():
    return "✅ MoneyToFlowsBot en ligne et opérationnel !"

# ---------------- MAIN ----------------
def main():
    init_db()
    # start Telegram bot in background thread
    t = threading.Thread(target=run_telegram_bot, daemon=True)
    t.start()
    # start Flask app (gunicorn will call app_flask)
    # when running locally 'python bot.py' we can run flask directly:
    port = int(os.getenv("PORT", "10000"))
    try:
        app_flask.run(host="0.0.0.0", port=port)
    except Exception as e:
        logger.exception("Erreur Flask: %s", e)

if __name__ == "__main__":
    main()
