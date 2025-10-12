#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bot Telegram - Parrainage / MLM (V21)
- Compatible python-telegram-bot==21.4
- Gère parrainage, dashboard, demande de retrait Mobile Money, et commandes admin.
"""

import os
import re
import logging
import sqlite3
from datetime import datetime
from typing import Optional, Tuple

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# ---------------- CONFIG ----------------
TOKEN = os.getenv("TOKEN")  # OBLIGATOIRE (Render Environment)
ADMIN_ID = os.getenv("ADMIN_ID")  # optionnel: met ton ID numérique si tu veux
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "@RUBENHRM777")  # fallback
ACHAT_LINK = os.getenv("ACHAT_LINK", "https://sgzxfbtn.mychariow.shop/prd_8ind83")
PRODUCT_NAME = os.getenv("PRODUCT_NAME", "Pack Formations Business 2026")
SEUIL_RECOMPENSE = int(os.getenv("SEUIL_RECOMPENSE", "5"))
DB_FILE = os.getenv("DB_FILE", "referral_bot.db")
PHONE_REGEX = re.compile(r"^\+?\d{6,15}$")  # accepté: +231..., 069xxxxxx, etc.
# ----------------------------------------

if not TOKEN:
    raise RuntimeError("La variable d'environnement TOKEN n'est pas définie. Ajoute-la sur Render.")

# Convert ADMIN_ID to int if present
if ADMIN_ID:
    try:
        ADMIN_ID = int(ADMIN_ID)
    except:
        ADMIN_ID = None

# Logging
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------- DATABASE HELPERS ----------------
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
        status TEXT DEFAULT 'pending', -- pending | validated | refused
        created_at TEXT
    );
    """)
    conn.commit()
    conn.close()

def db_execute(query: str, params: tuple = (), fetch: bool = False):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(query, params)
    if fetch:
        rows = c.fetchall()
        conn.commit()
        conn.close()
        return rows
    conn.commit()
    conn.close()
    return None

# --- user utilities ---
def get_user_by_telegram(telegram_id: int) -> Optional[tuple]:
    rows = db_execute("SELECT telegram_id, username, ref_code, referrer_code, purchases, created_at FROM users WHERE telegram_id = ?", (telegram_id,), True)
    return rows[0] if rows else None

def get_user_by_code(code: str) -> Optional[tuple]:
    rows = db_execute("SELECT telegram_id, username, ref_code FROM users WHERE ref_code = ?", (code,), True)
    return rows[0] if rows else None

def create_user(telegram_id: int, username: str, referrer_code: Optional[str] = None) -> tuple:
    # generate simple unique ref_code
    code = f"r{telegram_id:x}"[-8:]
    created_at = datetime.utcnow().isoformat()
    db_execute("INSERT OR IGNORE INTO users (telegram_id, username, ref_code, referrer_code, purchases, created_at) VALUES (?, ?, ?, ?, 0, ?)",
               (telegram_id, username, code, referrer_code, created_at))
    return get_user_by_telegram(telegram_id)

def add_referral(referrer_code: str, referred_telegram_id: int, referred_username: str):
    db_execute("INSERT INTO referrals (referrer_code, referred_telegram_id, referred_username, joined_at) VALUES (?, ?, ?, ?)",
               (referrer_code, referred_telegram_id, referred_username, datetime.utcnow().isoformat()))

def count_referred(referrer_code: str) -> int:
    rows = db_execute("SELECT COUNT(*) FROM referrals WHERE referrer_code = ?", (referrer_code,), True)
    return rows[0][0] if rows else 0

def count_referred_with_purchase(referrer_code: str) -> int:
    rows = db_execute(
        "SELECT COUNT(*) FROM referrals r JOIN users u ON r.referred_telegram_id = u.telegram_id WHERE r.referrer_code = ? AND u.purchases > 0",
        (referrer_code,), True
    )
    return rows[0][0] if rows else 0

def increment_purchase(telegram_id: int):
    db_execute("UPDATE users SET purchases = purchases + 1 WHERE telegram_id = ?", (telegram_id,))

def create_withdrawal_request(telegram_id: int, mobile_number: str):
    db_execute("INSERT INTO withdrawals (telegram_id, mobile_number, status, created_at) VALUES (?, ?, 'pending', ?)",
               (telegram_id, mobile_number, datetime.utcnow().isoformat()))

def list_withdrawals(status_filter: Optional[str] = None) -> list:
    if status_filter:
        return db_execute("SELECT id, telegram_id, mobile_number, status, created_at FROM withdrawals WHERE status = ? ORDER BY created_at DESC", (status_filter,), True) or []
    return db_execute("SELECT id, telegram_id, mobile_number, status, created_at FROM withdrawals ORDER BY created_at DESC", (), True) or []

def set_withdrawal_status(withdrawal_id: int, status: str):
    db_execute("UPDATE withdrawals SET status = ? WHERE id = ?", (status, withdrawal_id))

# ---------------- HELPERS ----------------
def is_admin_user(user) -> bool:
    try:
        if ADMIN_ID and hasattr(user, "id") and user.id == ADMIN_ID:
            return True
        # compare username (without @)
        if hasattr(user, "username") and user.username:
            u = user.username.lower()
            admin_name = ADMIN_USERNAME.lstrip("@").lower()
            if u == admin_name:
                return True
    except Exception:
        pass
    return False

# ---------------- HANDLERS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args
    payload = args[0] if args else None
    referrer_code = None
    if payload and payload.startswith("ref_"):
        referrer_code = payload.split("ref_", 1)[1]
    # create user if not exists
    if not get_user_by_telegram(user.id):
        create_user(user.id, user.username or (user.full_name if hasattr(user, "full_name") else str(user.id)), referrer_code)
        if referrer_code:
            add_referral(referrer_code, user.id, user.username or user.first_name or "")
            # notify parrain if possible
            ref = get_user_by_code(referrer_code)
            if ref:
                try:
                    await context.bot.send_message(chat_id=ref[0], text=f"🎉 Nouveau filleul ! @{user.username or user.first_name} s'est inscrit via ton lien.")
                except Exception:
                    pass
    # welcome text
    await update.message.reply_text(
        f"👋 Bonjour {user.first_name} !\n\n"
        f"Tu es sur le bot *{PRODUCT_NAME}*.\n\n"
        "Commandes utiles :\n"
        "/achat → Lien d'achat\n"
        "/parrainage → Obtenir ton lien unique\n"
        "/dashboard → Voir ton tableau de bord\n"
        "/retrait → Demander un retrait (après 5 filleuls acheteurs)\n"
        "/aide → Assistance\n"
    )

async def achat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"🛍️ Voici le lien officiel pour acheter *{PRODUCT_NAME}* :\n{ACHAT_LINK}\n\n"
        "Après ton achat, utilise /confachat <REFERENCE> pour envoyer ta preuve (admin validera)."
    )

async def parrainage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = get_user_by_telegram(user.id) or create_user(user.id, user.username or user.full_name or str(user.id))
    ref_code = u[2]
    deep_link = f"https://t.me/{context.bot.username}?start=ref_{ref_code}"
    await update.message.reply_text(
        f"🔗 Ton lien de parrainage :\n{deep_link}\n\n"
        f"⚠️ Rappel : le retrait est disponible uniquement après {SEUIL_RECOMPENSE} filleuls acheteurs."
    )

async def dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = get_user_by_telegram(user.id)
    if not u:
        await update.message.reply_text("Tu n'es pas encore enregistré. Fais /start.")
        return
    code = u[2]
    total = count_referred(code)
    acheteurs = count_referred_with_purchase(code)
    eligible = acheteurs >= SEUIL_RECOMPENSE
    await update.message.reply_text(
        f"📊 TABLEAU DE BORD\n\n"
        f"👤 @{user.username}\n"
        f"🔗 Code : {code}\n"
        f"👥 Filleuls inscrits : {total}\n"
        f"🛒 Filleuls acheteurs : {acheteurs}\n"
        f"🏆 Éligible au retrait : {'✅ OUI' if eligible else f'❌ NON (encore {SEUIL_RECOMPENSE - acheteurs})'}\n\n"
        f"⚠️ Le retrait se débloque à {SEUIL_RECOMPENSE} filleuls acheteurs.\n"
        "Pour demander un retrait : /retrait"
    )

async def confachat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Usage: /confachat <REFERENCE>
    user = update.effective_user
    if not context.args:
        await update.message.reply_text("Usage: /confachat <REFERENCE>")
        return
    reference = context.args[0]
    # Notify admin with the reference for manual validation
    admin_text = f"🔔 Demande de validation d'achat : @{user.username or user.first_name} (ID:{user.id})\nRéf: {reference}\nUtilise /addpurchase <telegram_id> pour valider."
    try:
        if ADMIN_ID:
            await context.bot.send_message(chat_id=ADMIN_ID, text=admin_text)
        else:
            await context.bot.send_message(chat_id=ADMIN_USERNAME, text=admin_text)
    except Exception:
        pass
    await update.message.reply_text("✅ Ta demande a été envoyée à l'admin pour validation.")

# ---------------- RETRAIT FLOW ----------------
async def retrait(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = get_user_by_telegram(user.id)
    if not u:
        await update.message.reply_text("Tu n'es pas enregistré. Fais /start d'abord.")
        return
    code = u[2]
    acheteurs = count_referred_with_purchase(code)
    if acheteurs < SEUIL_RECOMPENSE:
        await update.message.reply_text(f"🚫 Tu as {acheteurs} filleuls acheteurs. Il en faut {SEUIL_RECOMPENSE} pour débloquer le retrait.")
        return
    # ask for mobile number
    db_execute("DELETE FROM withdrawals WHERE telegram_id = ? AND status = 'waiting_number'", (user.id,))
    db_execute("INSERT INTO withdrawals (telegram_id, mobile_number, status, created_at) VALUES (?, ?, 'waiting_number', ?)",
               (user.id, '', datetime.utcnow().isoformat()))
    await update.message.reply_text(
        "✅ Tu es éligible au retrait.\n"
        "Envoie maintenant ton numéro Mobile Money (ex: +2426xxxxxxxx ou 06xxxxxxxx) pour que nous puissions créer ta demande."
    )

# capture messages that look like phone numbers and match waiting entries
async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user = update.effective_user
    # check if user has a withdrawal waiting for number
    rows = db_execute("SELECT id FROM withdrawals WHERE telegram_id = ? AND status = 'waiting_number'", (user.id,), True)
    if rows:
        # expects phone number
        if PHONE_REGEX.match(text):
            # update withdrawal
            db_execute("UPDATE withdrawals SET mobile_number = ?, status = 'pending' WHERE id = ?", (text, rows[0][0]))
            await update.message.reply_text("✅ Ton numéro a bien été reçu. Ta demande de retrait a été envoyée à l'admin pour validation.")
            # notify admin
            admin_text = f"🔔 Nouvelle demande de retrait : @{user.username or user.first_name} (ID:{user.id})\nNuméro: {text}\nUtilise /valider_retrait <withdrawal_id> pour valider."
            # send the admin the withdrawal id too
            w = db_execute("SELECT id FROM withdrawals WHERE telegram_id = ? AND status = 'pending' ORDER BY created_at DESC LIMIT 1", (user.id,), True)
            wid = w[0][0] if w else None
            if wid:
                admin_text = f"🔔 Nouvelle demande de retrait (id:{wid}) : @{user.username or user.first_name} (ID:{user.id})\nNuméro: {text}\nValide: /valider_retrait {wid}\nRefuser: /refuser_retrait {wid}"
            try:
                if ADMIN_ID:
                    await context.bot.send_message(chat_id=ADMIN_ID, text=admin_text)
                else:
                    await context.bot.send_message(chat_id=ADMIN_USERNAME, text=admin_text)
            except Exception:
                pass
        else:
            # not a phone number
            await update.message.reply_text("❌ Numéro non reconnu. Envoie ton numéro Mobile Money au format 06xxxxxxxx ou +2426xxxxxxxx.")
    else:
        # ignore or help
        # you can keep generic fallback or ignore
        return

# ---------------- ADMIN COMMANDS ----------------
async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update.effective_user):
        await update.message.reply_text("Commande réservée à l'admin.")
        return
    rows = db_execute("SELECT COUNT(*) FROM users", (), True) or [[0]]
    total_users = rows[0][0]
    rows2 = db_execute("SELECT COUNT(*) FROM users WHERE purchases>0", (), True) or [[0]]
    total_buyers = rows2[0][0]
    rows3 = db_execute("SELECT COUNT(*) FROM referrals", (), True) or [[0]]
    total_referrals = rows3[0][0]
    rows4 = db_execute("SELECT COUNT(*) FROM withdrawals WHERE status='pending'", (), True) or [[0]]
    pending_withdrawals = rows4[0][0]
    await update.message.reply_text(
        f"📈 STATS ADMIN\nMembres: {total_users}\nAcheteurs: {total_buyers}\nReferrals: {total_referrals}\nRetraits en attente: {pending_withdrawals}"
    )

async def addpurchase(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /addpurchase <telegram_id>
    if not is_admin_user(update.effective_user):
        await update.message.reply_text("Commande admin seulement.")
        return
    if not context.args or len(context.args) < 1:
        await update.message.reply_text("Usage : /addpurchase <telegram_id>")
        return
    try:
        tid = int(context.args[0])
    except:
        await update.message.reply_text("ID Telegram invalide.")
        return
    increment_purchase(tid)
    # notify referrer(s)
    user = get_user_by_telegram(tid)
    if user and user[3]:
        ref_code = user[3]
        ref_user = get_user_by_code(ref_code)
        if ref_user:
            try:
                await context.bot.send_message(chat_id=ref_user[0], text=f"✅ Ton filleul @{user[1]} a acheté le produit. Ta progression augmente.")
            except Exception:
                pass
    await update.message.reply_text("✅ Achat enregistré.")

async def list_retraits(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

async def valider_retrait(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /valider_retrait <withdrawal_id>
    if not is_admin_user(update.effective_user):
        await update.message.reply_text("Commande admin seulement.")
        return
    if not context.args or len(context.args) < 1:
        await update.message.reply_text("Usage : /valider_retrait <withdrawal_id>")
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
            await context.bot.send_message(chat_id=tid, text=f"🎉 Ton retrait (id:{wid}) a été validé par l'admin. Nous procéderons au paiement via Mobile Money ({mobile}).")
        except Exception:
            pass
    await update.message.reply_text("✅ Retrait validé et utilisateur notifié.")

async def refuser_retrait(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /refuser_retrait <withdrawal_id> <raison_opt>
    if not is_admin_user(update.effective_user):
        await update.message.reply_text("Commande admin seulement.")
        return
    if not context.args or len(context.args) < 1:
        await update.message.reply_text("Usage : /refuser_retrait <withdrawal_id> <raison_opt>")
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
            await context.bot.send_message(chat_id=tid, text=f"❌ Ton retrait (id:{wid}) a été refusé par l'admin.\nRaison: {reason}")
        except Exception:
            pass
    await update.message.reply_text("✅ Retrait refusé et utilisateur notifié.")

async def list_eligibles_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update.effective_user):
        await update.message.reply_text("Commande admin seulement.")
        return
    # list users with >= threshold acheteurs
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

# ---------------- REGISTER HANDLERS ----------------
def register_handlers(app):
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("achat", achat))
    app.add_handler(CommandHandler("parrainage", parrainage))
    app.add_handler(CommandHandler("dashboard", dashboard))
    app.add_handler(CommandHandler("confachat", confachat))
    app.add_handler(CommandHandler("retrait", retrait))

    # admin
    app.add_handler(CommandHandler("admin_stats", admin_stats))
    app.add_handler(CommandHandler("addpurchase", addpurchase))
    app.add_handler(CommandHandler("list_retraits", list_retraits))
    app.add_handler(CommandHandler("valider_retrait", valider_retrait))
    app.add_handler(CommandHandler("refuser_retrait", refuser_retrait))
    app.add_handler(CommandHandler("list_eligibles", list_eligibles_cmd))

    # text handler for capturing mobile numbers when user is expected to send one
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text_messages))

# ---------------- MAIN ----------------
def main():
    init_db()
    app = ApplicationBuilder().token(TOKEN).build()
    register_handlers(app)
    logger.info("Bot V21 démarrage (polling)...")
    print("🤖 Bot V21 démarré (polling).")
    app.run_polling()

if __name__ == "__main__":
    main()
