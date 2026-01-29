import asyncio
import json
import random
import time
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    FSInputFile
)
from aiogram.client.default import DefaultBotProperties

from countries import COUNTRIES

# ================== CONFIG ==================
BOT_TOKEN = "8245111028:AAEX8C4Q7DYot-a4NHQtqxfJTlvoKrCFzXQ"
DONATE_URL = "https://finance.ozon.ru/apps/sbp/ozonbankpay/019b4f50-2700-7e4c-be12-2dc23039e5ac"

AUTO_DELETE_SECONDS = 3600

SLIV_LIMIT = 3
SLIV_WINDOW = 600

HINT_LIMIT = 2
HINT_WINDOW = 600

RECENT_QUESTIONS_LIMIT = 15
DATA_FILE = "data.json"

MEME_TRIGGERS = [
    "догони меня калыван",
    "меня калыван",
    "калыван догони",
    "калыван"
]

# ================== INIT ==================
bot = Bot(
    BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

# ================== DATA ==================
def load_data():
    if not Path(DATA_FILE).exists():
        return {}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_data():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

data = load_data()

# safe defaults (чтобы никогда не падало)
data.setdefault("scores", {})
data.setdefault("current_question", {})
data.setdefault("recent_questions", {})
data.setdefault("sliv_usage", {})
data.setdefault("hint_usage", {})
data.setdefault("catch_count", {})
data.setdefault("chat_users", {})
data.setdefault("legend_sent", {})

# ================== UTILS ==================
def normalize(text: str):
    return text.lower().replace("ё", "е").strip()

def now():
    return int(time.time())

def clean_usage(lst, window):
    t = now()
    return [x for x in lst if t - x <= window]

def mention(user):
    return f'<a href="tg://user?id={user.id}">{user.first_name}</a>'

async def auto_delete(message: Message):
    await asyncio.sleep(AUTO_DELETE_SECONDS)
    try:
        await message.delete()
    except:
        pass

def donate_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="💸 Бабки максимке", url=DONATE_URL)]]
    )

def hint_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="💡 Подсказка", callback_data="hint")]]
    )

# ================== START ==================
@dp.message(F.text.lower().in_(["/start", "start"]))
async def start(message: Message):
    msg = await message.answer_photo(
        FSInputFile("start.jpg"),
        caption=(
            "Приветствуем тебя, это бот <b>Калыван</b> создан повелителем всех миров "
            "@VapeStoreBro\n\n"
            "Бот выполняет функцию квиза — столицы разных стран.\n"
            "Если понравился бот — ниже кнопка доната.\n\n"
            "Остальную инфу можно узнать нажав /helpslang"
        ),
        reply_markup=donate_kb()
    )
    asyncio.create_task(auto_delete(msg))

# ================== HELP ==================
@dp.message(F.text.lower().contains("helpslang"))
async def helpslang(message: Message):
    msg = await message.answer(
        "Все полезные команды калывана:\n\n"
        "/rate — рейтинг в квиз\n"
        "/sliv — пропустить вопрос (3 раза за 10 минут)\n"
        "/stop — остановка квиза\n\n"
        "Вопросы/предложения: @gangstore44"
    )
    asyncio.create_task(auto_delete(msg))

# ================== MEME (ВСЕГДА ПЕРВЫЙ) ==================
@dp.message(F.text)
async def meme_handler(message: Message):
    text = normalize(message.text)
    chat_id = str(message.chat.id)
    user_id = str(message.from_user.id)

    data.setdefault("chat_users", {}).setdefault(chat_id, {})
    data["chat_users"][chat_id][user_id] = {
        "first_name": message.from_user.first_name
    }

    save_data()
    if any(t in text for t in MEME_TRIGGERS):
        await message.delete()

        chat = str(message.chat.id)

        users = list(data["chat_users"].get(chat, {}).keys())

        # fallback если вдруг пусто
        if not users:
            target_id = str(message.from_user.id)
            target_name = message.from_user.first_name
        else:
            target_id = random.choice(users)
            target_name = data["chat_users"][chat][target_id]["first_name"]

        data.setdefault("catch_count", {}).setdefault(chat, {})
        data["catch_count"][chat][target_id] = data["catch_count"][chat].get(target_id, 0) + 1
        count = data["catch_count"][chat][target_id]

        img = "image.jpg"
        caption = (
            f"🐓 петуха <a href='tg://user?id={target_id}'>"
            f"{target_name}</a> догнал Калыван"
        )

        if count == 5:
            img = "catch.jpg"
            caption = "Калыван догнал тебя 5 раз. Теперь ты калываноед."

        msg = await message.answer_photo(
            FSInputFile(img),
            caption=caption,
            reply_markup=donate_kb()
        )
        asyncio.create_task(auto_delete(msg))
        save_data()
        return

    await quiz_commands_and_answers(message)

# ================== QUIZ CORE ==================
async def quiz_commands_and_answers(message: Message):
    text = normalize(message.text)
    chat_id = str(message.chat.id)
    user_id = str(message.from_user.id)

    # ---------- STOP ----------
    if text in ["стоп", "стопэ", "/stop"]:
        data["current_question"].pop(chat_id, None)
        save_data()
        await message.reply("❌ Квиз остановлен")
        return

    # ---------- RATE ----------
    if text in ["рейт", "рейтинг", "/rate"]:
        scores = data["scores"].get(chat_id, {})
        if not scores:
            await message.reply("Пока нет результатов")
            return

        sorted_users = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        msg_text = "🏆 <b>Рейтинг</b>\n\n"
        for i, (uid, score) in enumerate(sorted_users[:10], 1):
            msg_text += f"{i}. <a href='tg://user?id={uid}'>Игрок</a> — {score}\n"

        msg = await message.reply(msg_text)
        asyncio.create_task(auto_delete(msg))
        return

    # ---------- SLIV ----------
    if text in ["слив", "скип", "/sliv"]:
        usage = clean_usage(data["sliv_usage"].get(user_id, []), SLIV_WINDOW)
        if len(usage) >= SLIV_LIMIT:
            await message.reply("❌ Лимит сливов")
            return

        usage.append(now())
        data["sliv_usage"][user_id] = usage

        country = data["current_question"].get(chat_id)
        if country:
            await message.reply(f"Правильный ответ: <b>{COUNTRIES[country]['capital']}</b>")
            await send_question(message.chat.id)

        save_data()
        return

    # ---------- START QUIZ ----------
    if text in ["страна", "страны", "/страна"]:
        await send_question(message.chat.id)
        return

    # ---------- CAPITAL INFO ----------
    if text.startswith("столица"):
        for country, info in COUNTRIES.items():
            if country in text or any(a in text for a in info["aliases"]):
                await message.reply(
                    f"Столица <b>{country}</b> — <b>{info['capital']}</b>"
                )
                return

    # ---------- ANSWER ----------
    if chat_id not in data["current_question"]:
        return

    country = data["current_question"][chat_id]
    capital = normalize(COUNTRIES[country]["capital"])

    if text == capital:
        data.setdefault("scores", {}).setdefault(chat_id, {})
        data["scores"][chat_id][user_id] = data["scores"][chat_id].get(user_id, 0) + 1
        wins = data["scores"][chat_id][user_id]

        await message.reply(f"✅ Верно! {mention(message.from_user)}")

        achievements = {
            2: ("IMG_20260129_092840_938.jpg", "хохо ныкитэ"),
            10: ("legend.jpg", "ахуеть че за легенда набил 10 побед"),
            20: ("legend2.jpg", "какафki"),
            30: ("legend3.jpg", "калыванчик в попе пальчик"),
            40: ("legend4.jpg", "о нiкiткi острый перчик"),
            50: ("legend5.jpg", "назначаю тебя на высшую расу варвар"),
        }

        if wins in achievements:
            img, txt = achievements[wins]
            await message.answer_photo(FSInputFile(img), caption=txt)

        await send_question(message.chat.id)
        save_data()
    else:
        await message.reply("❌ Хуй там.")

# ================== QUESTIONS ==================
def get_random_country(chat_id):
    recent = data["recent_questions"].get(chat_id, [])
    pool = [c for c in COUNTRIES if c not in recent]
    if not pool:
        recent.clear()
        pool = list(COUNTRIES.keys())

    country = random.choice(pool)
    recent.append(country)
    data["recent_questions"][chat_id] = recent[-RECENT_QUESTIONS_LIMIT:]
    return country

async def send_question(chat_id):
    chat_id = str(chat_id)
    country = get_random_country(chat_id)
    data["current_question"][chat_id] = country
    save_data()
    await bot.send_message(
        chat_id,
        f"🏳️ Столица какой страны: <b>{country.upper()}</b>?",
        reply_markup=hint_kb()
    )

# ================== HINT (ALERT WINDOW) ==================
@dp.callback_query(F.data == "hint")
async def hint(callback: CallbackQuery):
    user_id = str(callback.from_user.id)
    chat_id = str(callback.message.chat.id)

    usage = clean_usage(data["hint_usage"].get(user_id, []), HINT_WINDOW)
    if len(usage) >= HINT_LIMIT:
        await callback.answer(
            "Ты уже заебал 😈\nЛимит подсказок исчерпан",
            show_alert=True
        )
        return

    usage.append(now())
    data["hint_usage"][user_id] = usage

    country = data["current_question"].get(chat_id)
    if not country:
        await callback.answer("Квиз не запущен далбаеб", show_alert=True)
        return

    capital = COUNTRIES[country]["capital"][:2].upper()

    await callback.answer(

        f"Ебать ты тупой 😈\n"
        f"Первые две буквы столицы:\n\n"
        f"{capital}…",
        show_alert=True
    )
    save_data()

# ================== RUN ==================
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())



