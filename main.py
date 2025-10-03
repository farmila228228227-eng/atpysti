import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ParseMode
from aiogram.utils.exceptions import BadRequest

from keep_alive import keep_alive

# === CONFIG ===
BOT_TOKEN = os.getenv("BOT_TOKEN", "ВСТАВЬ_СЮДА_СВОЙ_ТОКЕН")
OWNER_ID = 7322925570
DB_PATH = "moderation_bot.db"
DEFAULT_MUTE_SECONDS = 3600

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher(bot)

# === DATABASE ===
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS settings(
        chat_id INTEGER PRIMARY KEY, enabled INTEGER DEFAULT 1)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS banned(
        id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, word TEXT, is_link INTEGER DEFAULT 0)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS logs(
        id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, user_id INTEGER,
        username TEXT, action TEXT, reason TEXT, timestamp TEXT)""")
    conn.commit()
    conn.close()

def db_execute(query, params=(), fetch=False):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(query, params)
    data = cur.fetchall() if fetch else None
    conn.commit()
    conn.close()
    return data

def is_enabled(chat_id: int) -> bool:
    res = db_execute("SELECT enabled FROM settings WHERE chat_id=?", (chat_id,), fetch=True)
    if not res:
        return True
    return bool(res[0][0])

def set_enabled(chat_id: int, val: bool):
    db_execute("INSERT OR REPLACE INTO settings(chat_id, enabled) VALUES(?,?)", (chat_id, 1 if val else 0))

def add_banned(chat_id: int, word: str, is_link: bool = False):
    db_execute("INSERT INTO banned(chat_id, word, is_link) VALUES(?,?,?)",
               (chat_id, word.lower(), 1 if is_link else 0))

def remove_banned(chat_id: int, word: str):
    db_execute("DELETE FROM banned WHERE chat_id=? AND word=?", (chat_id, word.lower()))

def list_banned(chat_id: int):
    return db_execute("SELECT word, is_link FROM banned WHERE chat_id=?", (chat_id,), fetch=True) or []

def add_log(chat_id, user_id, username, action, reason):
    ts = datetime.utcnow().isoformat()
    db_execute("INSERT INTO logs(chat_id, user_id, username, action, reason, timestamp) VALUES(?,?,?,?,?,?)",
               (chat_id, user_id, username or "", action, reason, ts))

def get_logs(chat_id=None, limit=10):
    if chat_id:
        return db_execute("SELECT user_id, username, action, reason, timestamp FROM logs WHERE chat_id=? ORDER BY id DESC LIMIT ?",
                          (chat_id, limit), fetch=True)
    else:
        return db_execute("SELECT chat_id, user_id, username, action, reason, timestamp FROM logs ORDER BY id DESC LIMIT ?",
                          (limit,), fetch=True)

# === HELPERS ===
async def is_admin(chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.is_chat_admin() or member.status == "creator"
    except Exception:
        return False

def format_offender(user: types.User):
    return f"<b>{user.username}</b>" if user.username else f"<b>id:{user.id}</b>"

LINK_REGEX = re.compile(r"(https?://\\S+|t.me/\\S+|\\.com|\\.ru|\\.net)")

# === PUNISHMENT ===
async def apply_punishment(chat_id: int, offender: types.User, punishment: str, duration: int = None):
    try:
        if punishment == "ban":
            await bot.kick_chat_member(chat_id, offender.id)
        elif punishment == "kick":
            await bot.kick_chat_member(chat_id, offender.id)
            await bot.unban_chat_member(chat_id, offender.id)
        elif punishment == "mute":
            until = datetime.utcnow() + timedelta(seconds=DEFAULT_MUTE_SECONDS)
            await bot.restrict_chat_member(chat_id, offender.id, can_send_messages=False, until_date=until)
        elif punishment == "tempmute":
            until = datetime.utcnow() + timedelta(seconds=duration or DEFAULT_MUTE_SECONDS)
            await bot.restrict_chat_member(chat_id, offender.id, can_send_messages=False, until_date=until)
        elif punishment == "tempban":
            until = datetime.utcnow() + timedelta(seconds=duration or DEFAULT_MUTE_SECONDS)
            await bot.kick_chat_member(chat_id, offender.id, until_date=until)
        return punishment
    except Exception as e:
        logging.exception("Punishment failed: %s", e)
        return "не удалось применить наказание"

async def handle_offense(message: types.Message, reason_text: str, punishment: str = "mute", duration: int = None):
    try:
        await message.delete()
    except BadRequest:
        pass
    offender = message.from_user
    offender_display = format_offender(offender)
    chat_id = message.chat.id
    action_text = await apply_punishment(chat_id, offender, punishment, duration)
    msg = f"<b>Пользователь {offender_display} нарушил правила и {reason_text} и получил {action_text}.</b>"
    await bot.send_message(chat_id, msg)
    add_log(chat_id, offender.id, offender.username or "", punishment, reason_text)

# === ADMIN PANEL ===
def admin_keyboard():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🟢 Включить", callback_data="enable"))
    kb.add(InlineKeyboardButton("🔴 Выключить", callback_data="disable"))
    kb.add(InlineKeyboardButton("🚫 Blacklist", callback_data="blacklist"))
    kb.add(InlineKeyboardButton("📖 Логи", callback_data="logs"))
    kb.add(InlineKeyboardButton("🛡 Whitelist", callback_data="whitelist"))
    return kb

@dp.message_handler(commands=["admin"])
async def cmd_admin(message: types.Message):
    if not await is_admin(message.chat.id, message.from_user.id) and message.from_user.id != OWNER_ID:
        return await message.reply("<b>Недостаточно прав.</b>")
    await message.reply("<b>Админ-панель:</b>", reply_markup=admin_keyboard())

@dp.callback_query_handler(lambda c: c.data == "enable")
async def cb_enable(call: types.CallbackQuery):
    set_enabled(call.message.chat.id, True)
    await call.answer("✅ Бот включен")
    await call.message.edit_text("<b>Бот включен.</b>", reply_markup=admin_keyboard())

@dp.callback_query_handler(lambda c: c.data == "disable")
async def cb_disable(call: types.CallbackQuery):
    set_enabled(call.message.chat.id, False)
    await call.answer("⛔ Бот выключен")
    await call.message.edit_text("<b>Бот выключен.</b>", reply_markup=admin_keyboard())

@dp.callback_query_handler(lambda c: c.data == "whitelist")
async def cb_whitelist(call: types.CallbackQuery):
    await call.answer()
    await call.message.edit_text("<b>Все слова и ссылки разрешены, кроме тех, что находятся в blacklist.</b>",
                                 reply_markup=admin_keyboard())

@dp.callback_query_handler(lambda c: c.data == "logs")
async def cb_logs(call: types.CallbackQuery):
    logs = get_logs(call.message.chat.id, 5)
    if not logs:
        text = "<b>Логи пусты.</b>"
    else:
        text = "<b>Последние нарушения:</b>\n"
        for u_id, uname, act, reason, ts in logs:
            text += f"— {uname or u_id}: {reason} → {act} ({ts})\n"
    await call.answer()
    await call.message.edit_text(text, reply_markup=admin_keyboard())

# === MONITOR ===
@dp.message_handler(content_types=types.ContentTypes.ANY)
async def monitor_messages(message: types.Message):
    if message.chat.type == "private":
        return
    chat_id = message.chat.id
    if not is_enabled(chat_id):
        return
    if await is_admin(chat_id, message.from_user.id) or message.from_user.id == OWNER_ID:
        return
    text = (message.text or message.caption or "").lower()
    banned_list = list_banned(chat_id)
    for w, is_link in banned_list:
        if is_link:
            if w in text or LINK_REGEX.search(text):
                await handle_offense(message, "написал запрещенную ссылку", punishment="ban")
                return
        else:
            if w in text:
                await handle_offense(message, "написал запрещенное слово", punishment="mute")
                return
    if LINK_REGEX.search(text):
        if any(is_link for _, is_link in banned_list):
            await handle_offense(message, "написал запрещенную ссылку", punishment="ban")
            return

if __name__ == "__main__":
    keep_alive()
    init_db()
    executor.start_polling(dp, skip_updates=True)
