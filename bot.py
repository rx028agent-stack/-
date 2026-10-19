
import os
import sqlite3
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes
)

TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

REWARD = 50
MIN_REFERRALS = 20
DB_NAME = "bot.db"

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)


def connect():
    return sqlite3.connect(DB_NAME)


def setup_db():
    with connect() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                referrals INTEGER DEFAULT 0,
                balance INTEGER DEFAULT 0,
                referred_by INTEGER,
                paid_referrals INTEGER DEFAULT 0
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS withdrawals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount INTEGER,
                wallet TEXT,
                status TEXT DEFAULT 'pending'
            )
        """)


def get_user(user_id):
    with connect() as db:
        return db.execute(
            "SELECT referrals, balance, referred_by FROM users WHERE user_id=?",
            (user_id,)
        ).fetchone()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    with connect() as db:
        existing = db.execute(
            "SELECT user_id FROM users WHERE user_id=?", (user_id,)
        ).fetchone()

        if not existing:
            referrer = None
            if context.args:
                try:
                    candidate = int(context.args[0])
                    if candidate != user_id:
                        found = db.execute(
                            "SELECT user_id FROM users WHERE user_id=?",
                            (candidate,)
                        ).fetchone()
                        if found:
                            referrer = candidate
                except ValueError:
                    pass

            db.execute(
                "INSERT INTO users(user_id, referred_by) VALUES (?, ?)",
                (user_id, referrer)
            )

            if referrer:
                db.execute(
                    """UPDATE users
                       SET referrals = referrals + 1,
                           balance = balance + ?
                       WHERE user_id = ?""",
                    (REWARD, referrer)
                )

    keyboard = [
        [InlineKeyboardButton("👤 My Account", callback_data="account")],
        [InlineKeyboardButton("🔗 My Referral Link", callback_data="link")],
        [InlineKeyboardButton("💸 Withdraw", callback_data="withdraw")]
    ]

    await update.message.reply_text(
        f"🎉 Welcome to Bangla Telegram!\n\n"
        f"💰 Per referral reward: ৳{REWARD}\n"
        f"👥 Minimum referrals: {MIN_REFERRALS}\n\n"
        "Invite friends using your personal referral link.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data == "account":
        row = get_user(user_id)
        if row:
            refs, balance, _ = row
            await query.message.reply_text(
                f"👤 Your Account\n\n"
                f"👥 Referrals: {refs}\n"
                f"💰 Balance: ৳{balance}\n"
                f"💸 Minimum withdrawal: 20 referrals"
            )

    elif data == "link":
        bot = await context.bot.get_me()
        await query.message.reply_text(
            f"🔗 Your referral link:\n"
            f"https://t.me/{bot.username}?start={user_id}"
        )

    elif data == "withdraw":
        row = get_user(user_id)
        if not row:
            return

        refs, balance, _ = row

        if refs < MIN_REFERRALS:
            await query.message.reply_text(
                f"❌ You need {MIN_REFERRALS} referrals to request withdrawal.\n"
                f"Your referrals: {refs}"
            )
            return

        if balance <= 0:
            await query.message.reply_text("Your balance is empty.")
            return

        context.user_data["awaiting_wallet"] = True
        await query.message.reply_text(
            "Send your payment wallet details now.\n"
            "Example: bKash/Nagad/Rocket number."
        )


async def wallet_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_wallet"):
        return

    user_id = update.effective_user.id
    wallet = update.message.text.strip()
    context.user_data["awaiting_wallet"] = False

    row = get_user(user_id)
    if not row:
        return

    refs, balance, _ = row
    if refs < MIN_REFERRALS or balance <= 0:
        await update.message.reply_text("Withdrawal requirements not met.")
        return

    with connect() as db:
        cur = db.execute(
            "INSERT INTO withdrawals(user_id, amount, wallet) VALUES (?, ?, ?)",
            (user_id, balance, wallet)
        )
        request_id = cur.lastrowid

    keyboard = [[
        InlineKeyboardButton(
            "✅ Approve",
            callback_data=f"approve:{request_id}"
        ),
        InlineKeyboardButton(
            "❌ Reject",
            callback_data=f"reject:{request_id}"
        )
    ]]

    if ADMIN_ID:
        await context.bot.send_message(
            ADMIN_ID,
            f"💸 Withdrawal Request #{request_id}\n"
            f"User ID: {user_id}\n"
            f"Referrals: {refs}\n"
            f"Amount: ৳{balance}\n"
            f"Wallet: {wallet}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    await update.message.reply_text(
        "✅ Withdrawal request submitted for admin verification."
    )


async def admin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        await query.message.reply_text("Not authorized.")
        return

    action, request_id = query.data.split(":")
    request_id = int(request_id)

    with connect() as db:
        request = db.execute(
            """SELECT user_id, amount, status
               FROM withdrawals WHERE id=?""",
            (request_id,)
        ).fetchone()

        if not request or request[2] != "pending":
            await query.message.reply_text("Request already processed.")
            return

        user_id, amount, _ = request

        if action == "approve":
            user = db.execute(
                "SELECT balance FROM users WHERE user_id=?", (user_id,)
            ).fetchone()

            if not user or user[0] < amount:
                await query.message.reply_text("Insufficient balance.")
                return

            db.execute(
                "UPDATE users SET balance=balance-? WHERE user_id=?",
                (amount, user_id)
            )
            db.execute(
                "UPDATE withdrawals SET status='approved' WHERE id=?",
                (request_id,)
            )
            message = "Your withdrawal was approved. Please wait for payment."

        else:
            db.execute(
                "UPDATE withdrawals SET status='rejected' WHERE id=?",
                (request_id,)
            )
            message = "Your withdrawal request was rejected by the admin."

    await context.bot.send_message(user_id, message)
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(f"Request #{request_id} processed.")


async def main():
    setup_db()
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(admin_handler, pattern=r"^(approve|reject):"))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(
        __import__("telegram.ext", fromlist=["MessageHandler"]).MessageHandler(
            __import__("telegram.ext", fromlist=["filters"]).filters.TEXT
            & ~__import__("telegram.ext", fromlist=["filters"]).filters.COMMAND,
            wallet_message
        )
    )

    print("Bot started.")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    await __import__("asyncio").Event().wait()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
