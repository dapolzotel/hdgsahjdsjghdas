import asyncio
import logging
import sqlite3
import os
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties

# ============================================================
#  НАСТРОЙКИ — заполни перед запуском
# ============================================================
BOT_TOKEN    = "YOUR_BOT_TOKEN_HERE"   # токен от @BotFather
BOT_USERNAME = "YOUR_BOT_USERNAME"     # username бота БЕЗ @

SPONSORS = [
    {"name": "doozmbot",    "channel_id": "@doozmbot"},
    {"name": "suetastarss", "channel_id": "@suetastarss"},
    {"name": "imasta4",     "channel_id": "@imasta4"},
    {"name": "mxdarka",     "channel_id": "@mxdarka"},
]

STARS_PER_REFERRAL = 8
DB_FILE = "database.db"
WITHDRAW_OPTIONS = [15, 25, 50, 100]

# ============================================================
#  БАЗА ДАННЫХ — SQLite
# ============================================================
def get_conn():
    return sqlite3.connect(DB_FILE)

def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id     INTEGER PRIMARY KEY,
                username    TEXT,
                first_name  TEXT,
                stars       INTEGER DEFAULT 0,
                referrals   INTEGER DEFAULT 0,
                invited_by  INTEGER DEFAULT NULL,
                joined_at   TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS referral_list (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id     INTEGER,
                referral_id     INTEGER,
                referral_name   TEXT,
                earned_stars    INTEGER,
                joined_at       TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS withdrawals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER,
                amount      INTEGER,
                status      TEXT DEFAULT 'pending',
                created_at  TEXT
            )
        """)
        conn.commit()

def is_new_user(user_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return row is None

def create_user(user_id: int, username: str, first_name: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, username, first_name, joined_at) VALUES (?, ?, ?, ?)",
            (user_id, username or "", first_name or "", datetime.now().isoformat())
        )
        conn.commit()

def get_user(user_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        return None
    cols = ["user_id", "username", "first_name", "stars", "referrals", "invited_by", "joined_at"]
    return dict(zip(cols, row))

def add_stars(user_id: int, amount: int):
    with get_conn() as conn:
        conn.execute("UPDATE users SET stars = stars + ? WHERE user_id = ?", (amount, user_id))
        conn.commit()

def deduct_stars(user_id: int, amount: int):
    with get_conn() as conn:
        conn.execute("UPDATE users SET stars = stars - ? WHERE user_id = ?", (amount, user_id))
        conn.commit()

def set_invited_by(user_id: int, referrer_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE users SET invited_by = ? WHERE user_id = ?", (referrer_id, user_id))
        conn.commit()

def increment_referrals(referrer_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE users SET referrals = referrals + 1 WHERE user_id = ?", (referrer_id,))
        conn.commit()

def add_referral_record(referrer_id: int, referral_id: int, referral_name: str, stars: int):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO referral_list (referrer_id, referral_id, referral_name, earned_stars, joined_at) VALUES (?, ?, ?, ?, ?)",
            (referrer_id, referral_id, referral_name, stars, datetime.now().isoformat())
        )
        conn.commit()

def get_referral_list(referrer_id: int) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT referral_name, earned_stars, joined_at FROM referral_list WHERE referrer_id = ? ORDER BY joined_at DESC LIMIT 10",
            (referrer_id,)
        ).fetchall()
    return rows

def add_withdrawal(user_id: int, amount: int):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO withdrawals (user_id, amount, status, created_at) VALUES (?, ?, 'pending', ?)",
            (user_id, amount, datetime.now().isoformat())
        )
        conn.commit()

def get_withdrawal_history(user_id: int) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT amount, status, created_at FROM withdrawals WHERE user_id = ? ORDER BY created_at DESC LIMIT 5",
            (user_id,)
        ).fetchall()
    return rows

# ============================================================
#  ПРОВЕРКА ПОДПИСОК
# ============================================================
async def get_unsubscribed(bot: Bot, user_id: int) -> list:
    result = []
    for sponsor in SPONSORS:
        try:
            member = await bot.get_chat_member(sponsor["channel_id"], user_id)
            if member.status in ("left", "kicked"):
                result.append(sponsor)
        except Exception:
            result.append(sponsor)
    return result

# ============================================================
#  КЛАВИАТУРЫ
# ============================================================
def sub_keyboard(unsubscribed: list) -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(text=f"📢 Подписаться на @{s['name']}", url=f"https://t.me/{s['name']}")] for s in unsubscribed]
    buttons.append([InlineKeyboardButton(text="✅ Я подписался — проверить", callback_data="check_sub")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Заработать звёзды", callback_data="earn")],
        [InlineKeyboardButton(text="👤 Профиль",           callback_data="profile")],
        [InlineKeyboardButton(text="💸 Вывод",             callback_data="withdraw")],
    ])

def back_btn() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu")]])

def profile_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Мои рефералы",      callback_data="my_refs")],
        [InlineKeyboardButton(text="📜 История выводов",   callback_data="withdraw_history")],
        [InlineKeyboardButton(text="🔙 Главное меню",      callback_data="menu")],
    ])

def withdraw_keyboard(stars: int) -> InlineKeyboardMarkup:
    buttons = []
    for amount in WITHDRAW_OPTIONS:
        if stars >= amount:
            buttons.append([InlineKeyboardButton(text=f"💸 Вывести {amount} ⭐", callback_data=f"do_withdraw_{amount}")])
        else:
            buttons.append([InlineKeyboardButton(text=f"🔒 {amount} ⭐  (не хватает)", callback_data="not_enough")])
    buttons.append([InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ============================================================
#  БОТ
# ============================================================
logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp  = Dispatcher(storage=MemoryStorage())

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user_id    = message.from_user.id
    username   = message.from_user.username or ""
    first_name = message.from_user.first_name or "Пользователь"
    args       = message.text.split()
    new_user   = is_new_user(user_id)

    create_user(user_id, username, first_name)

    # Реферал засчитывается ТОЛЬКО новому пользователю
    if new_user and len(args) > 1:
        try:
            referrer_id = int(args[1])
            if referrer_id != user_id and get_user(referrer_id):
                set_invited_by(user_id, referrer_id)
                add_stars(referrer_id, STARS_PER_REFERRAL)
                increment_referrals(referrer_id)
                add_referral_record(referrer_id, user_id, first_name, STARS_PER_REFERRAL)
                try:
                    await bot.send_message(
                        referrer_id,
                        f"🎉 По вашей ссылке зарегистрировался <b>{first_name}</b>!\n"
                        f"Вам начислено <b>+{STARS_PER_REFERRAL} ⭐</b>"
                    )
                except Exception:
                    pass
        except (ValueError, IndexError):
            pass

    unsubscribed = await get_unsubscribed(bot, user_id)
    if unsubscribed:
        await message.answer(
            "👋 Привет!\n\n🔒 Для доступа к боту подпишись на наших партнёров:",
            reply_markup=sub_keyboard(unsubscribed)
        )
    else:
        await message.answer(
            f"👋 Привет, <b>{first_name}</b>!\n\n✅ Все подписки активны.\nВыбери раздел:",
            reply_markup=main_menu()
        )

@dp.callback_query(F.data == "check_sub")
async def check_sub(call: types.CallbackQuery):
    unsubscribed = await get_unsubscribed(bot, call.from_user.id)
    if unsubscribed:
        await call.answer("❌ Ты ещё не подписался на все каналы!", show_alert=True)
        await call.message.edit_text("🔒 Подпишись на все каналы и нажми кнопку снова:", reply_markup=sub_keyboard(unsubscribed))
    else:
        await call.message.edit_text(
            f"✅ Отлично, <b>{call.from_user.first_name}</b>! Подписки подтверждены.\n\nВыбери раздел:",
            reply_markup=main_menu()
        )

@dp.callback_query(F.data == "menu")
async def go_menu(call: types.CallbackQuery):
    unsubscribed = await get_unsubscribed(bot, call.from_user.id)
    if unsubscribed:
        await call.message.edit_text("🔒 Подпишись на все каналы:", reply_markup=sub_keyboard(unsubscribed))
        return
    await call.message.edit_text("Выбери раздел:", reply_markup=main_menu())

@dp.callback_query(F.data == "earn")
async def earn_stars(call: types.CallbackQuery):
    unsubscribed = await get_unsubscribed(bot, call.from_user.id)
    if unsubscribed:
        await call.answer("Сначала подпишись на каналы!", show_alert=True)
        return
    ref_link = f"https://t.me/{BOT_USERNAME}?start={call.from_user.id}"
    await call.message.edit_text(
        "⭐ <b>Заработать звёзды</b>\n\n"
        f"Твоя реферальная ссылка:\n<code>{ref_link}</code>\n\n"
        f"За каждого нового пользователя, который впервые запустит бота по твоей ссылке, "
        f"тебе начислится <b>{STARS_PER_REFERRAL} ⭐</b> и +1 реферал в профиль.\n\n"
        "📌 Реферал засчитывается только если человек <b>впервые</b> открывает бота.",
        reply_markup=back_btn()
    )

@dp.callback_query(F.data == "profile")
async def profile(call: types.CallbackQuery):
    unsubscribed = await get_unsubscribed(bot, call.from_user.id)
    if unsubscribed:
        await call.answer("Сначала подпишись на каналы!", show_alert=True)
        return
    user = get_user(call.from_user.id)
    uname = f"@{user['username']}" if user['username'] else "—"
    joined = user['joined_at'][:10] if user['joined_at'] else "—"
    await call.message.edit_text(
        "👤 <b>Профиль</b>\n\n"
        f"🆔 ID: <code>{user['user_id']}</code>\n"
        f"👤 Username: {uname}\n"
        f"⭐ Звёзд: <b>{user['stars']}</b>\n"
        f"👥 Рефералов: <b>{user['referrals']}</b>\n"
        f"📅 Дата регистрации: {joined}",
        reply_markup=profile_keyboard()
    )

@dp.callback_query(F.data == "my_refs")
async def my_refs(call: types.CallbackQuery):
    refs = get_referral_list(call.from_user.id)
    if not refs:
        text = "👥 <b>Мои рефералы</b>\n\nУ тебя пока нет рефералов.\nПоделись ссылкой из раздела ⭐ Заработать звёзды!"
    else:
        lines = ["👥 <b>Мои рефералы (последние 10)</b>\n"]
        for i, (name, earned, joined) in enumerate(refs, 1):
            date = joined[:10] if joined else "—"
            lines.append(f"{i}. <b>{name}</b> — +{earned} ⭐ — {date}")
        text = "\n".join(lines)
    await call.message.edit_text(text, reply_markup=back_btn())

@dp.callback_query(F.data == "withdraw_history")
async def withdraw_history(call: types.CallbackQuery):
    history = get_withdrawal_history(call.from_user.id)
    if not history:
        text = "📜 <b>История выводов</b>\n\nВыводов пока не было."
    else:
        status_emoji = {"pending": "⏳", "paid": "✅", "rejected": "❌"}
        lines = ["📜 <b>История выводов (последние 5)</b>\n"]
        for amount, status, created_at in history:
            date = created_at[:10] if created_at else "—"
            emoji = status_emoji.get(status, "⏳")
            lines.append(f"{emoji} {amount} ⭐ — {date}")
        text = "\n".join(lines)
    await call.message.edit_text(text, reply_markup=back_btn())

@dp.callback_query(F.data == "withdraw")
async def withdraw(call: types.CallbackQuery):
    unsubscribed = await get_unsubscribed(bot, call.from_user.id)
    if unsubscribed:
        await call.answer("Сначала подпишись на каналы!", show_alert=True)
        return
    user = get_user(call.from_user.id)
    await call.message.edit_text(
        f"💸 <b>Вывод звёзд</b>\n\nУ тебя сейчас: <b>{user['stars']} ⭐</b>\n\nВыбери сумму для вывода:",
        reply_markup=withdraw_keyboard(user["stars"])
    )

@dp.callback_query(F.data.startswith("do_withdraw_"))
async def do_withdraw(call: types.CallbackQuery):
    amount = int(call.data.split("_")[-1])
    user   = get_user(call.from_user.id)
    if user["stars"] < amount:
        await call.answer("❌ Недостаточно звёзд!", show_alert=True)
        return
    deduct_stars(call.from_user.id, amount)
    add_withdrawal(call.from_user.id, amount)
    user = get_user(call.from_user.id)
    await call.message.edit_text(
        f"✅ <b>Заявка на вывод {amount} ⭐ принята!</b>\n\n"
        "Выплата будет произведена в течение <b>24 часов</b>.\n\n"
        f"Остаток на балансе: <b>{user['stars']} ⭐</b>",
        reply_markup=back_btn()
    )

@dp.callback_query(F.data == "not_enough")
async def not_enough(call: types.CallbackQuery):
    await call.answer("❌ Недостаточно звёзд для вывода!", show_alert=True)

# ============================================================
#  ЗАПУСК
# ============================================================
async def main():
    init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
