import json
from html import escape
from pathlib import Path

from aiogram import Dispatcher, F
from aiogram.enums import ChatType
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from countries import COUNTRIES

ADMIN_IDS = {6577441312}
CUSTOM_COUNTRIES_FILE = Path("custom_countries.json")


class AddCountry(StatesGroup):
    country = State()
    capital = State()
    aliases = State()
    confirm = State()


def normalize(text: str) -> str:
    return " ".join(text.lower().replace("ё", "е").strip().split())


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def is_valid_name(value: str) -> bool:
    return 2 <= len(value) <= 80 and all(
        char.isalpha() or char in " -'’." for char in value
    )


def load_custom_countries() -> dict:
    if not CUSTOM_COUNTRIES_FILE.exists():
        return {}

    try:
        with CUSTOM_COUNTRIES_FILE.open("r", encoding="utf-8") as file:
            raw = json.load(file)
    except (OSError, json.JSONDecodeError):
        return {}

    if not isinstance(raw, dict):
        return {}

    result = {}
    for raw_country, raw_info in raw.items():
        country = normalize(str(raw_country))
        if not is_valid_name(country) or not isinstance(raw_info, dict):
            continue

        capital = normalize(str(raw_info.get("capital", "")))
        if not is_valid_name(capital):
            continue

        aliases = []
        for raw_alias in raw_info.get("aliases", []):
            alias = normalize(str(raw_alias))
            if (
                is_valid_name(alias)
                and alias != country
                and alias not in aliases
            ):
                aliases.append(alias)

        result[country] = {
            "capital": capital,
            "aliases": aliases,
        }

    return result


def save_custom_countries(countries: dict) -> None:
    temp_file = CUSTOM_COUNTRIES_FILE.with_suffix(".json.tmp")
    with temp_file.open("w", encoding="utf-8") as file:
        json.dump(countries, file, ensure_ascii=False, indent=2, sort_keys=True)
        file.flush()
    temp_file.replace(CUSTOM_COUNTRIES_FILE)


def country_exists_as_name_or_alias(value: str) -> bool:
    if value in COUNTRIES:
        return True
    return any(value in info.get("aliases", []) for info in COUNTRIES.values())


def skip_aliases_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➡️ Без других названий",
                    callback_data="country_admin:skip_aliases",
                )
            ]
        ]
    )


def confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Сохранить",
                    callback_data="country_admin:save",
                ),
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data="country_admin:cancel",
                ),
            ]
        ]
    )


async def show_confirmation(message: Message, state: FSMContext) -> None:
    values = await state.get_data()
    aliases = values.get("aliases", [])
    aliases_text = ", ".join(escape(alias) for alias in aliases) or "нет"

    await state.set_state(AddCountry.confirm)
    await message.answer(
        "Проверь данные:\n\n"
        f"🌍 Страна: <b>{escape(values['country'])}</b>\n"
        f"🏛 Столица: <b>{escape(values['capital'])}</b>\n"
        f"🔎 Другие названия: <b>{aliases_text}</b>\n\n"
        "Сохранить?",
        reply_markup=confirmation_keyboard(),
    )


async def add_country_start(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        await message.reply("❌ Эта команда доступна только администратору.")
        return

    if message.chat.type != ChatType.PRIVATE:
        await message.reply("Напиши мне /addcountry в личные сообщения.")
        return

    await state.clear()
    await state.set_state(AddCountry.country)
    await message.answer(
        "🌍 Напиши название страны.\n\n"
        "Пример: <b>Германия</b>\n"
        "Для отмены: /cancel"
    )


async def cancel_country_add(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return

    await state.clear()
    await message.answer("❌ Добавление страны отменено.")


async def list_custom_countries(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.reply("❌ Эта команда доступна только администратору.")
        return

    custom = load_custom_countries()
    if not custom:
        await message.answer("Ты пока не добавил ни одной своей страны.")
        return

    lines = ["🌍 <b>Добавленные страны</b>\n"]
    for country, info in sorted(custom.items()):
        lines.append(f"• {escape(country)} — {escape(info['capital'])}")

    chunks = []
    current = ""
    for line in lines:
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > 3500:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)

    for chunk in chunks:
        await message.answer(chunk)


async def add_country_name(message: Message, state: FSMContext) -> None:
    country = normalize(message.text)

    if not is_valid_name(country):
        await message.answer(
            "❌ Некорректное название. Используй только буквы, пробелы и дефисы "
            "(от 2 до 80 символов). Попробуй ещё раз."
        )
        return

    if country_exists_as_name_or_alias(country):
        await message.answer(
            f"❌ <b>{escape(country)}</b> уже есть в базе. Введи другую страну."
        )
        return

    await state.update_data(country=country)
    await state.set_state(AddCountry.capital)
    await message.answer(
        f"🏛 Теперь напиши столицу страны <b>{escape(country)}</b>."
    )


async def add_country_capital(message: Message, state: FSMContext) -> None:
    capital = normalize(message.text)

    if not is_valid_name(capital):
        await message.answer(
            "❌ Некорректное название столицы. Используй только буквы, "
            "пробелы и дефисы (от 2 до 80 символов). Попробуй ещё раз."
        )
        return

    await state.update_data(capital=capital)
    await state.set_state(AddCountry.aliases)
    await message.answer(
        "🔎 Напиши другие названия страны через запятую.\n\n"
        "Например: <b>ФРГ, Германия Федеративная</b>\n"
        "Или нажми кнопку ниже, если они не нужны.",
        reply_markup=skip_aliases_keyboard(),
    )


async def add_country_aliases(message: Message, state: FSMContext) -> None:
    raw_aliases = [normalize(item) for item in message.text.split(",")]
    values = await state.get_data()
    country = values["country"]
    aliases = []

    for alias in raw_aliases:
        if not alias:
            continue
        if not is_valid_name(alias):
            await message.answer(
                f"❌ Некорректный вариант: <b>{escape(alias)}</b>. "
                "Используй только буквы, пробелы и дефисы."
            )
            return
        if alias == country or alias in aliases:
            continue
        if country_exists_as_name_or_alias(alias):
            await message.answer(
                f"❌ Название <b>{escape(alias)}</b> уже используется в базе. "
                "Введи другие варианты или нажми «Без других названий»."
            )
            return
        aliases.append(alias)

    if len(aliases) > 15:
        await message.answer("❌ Можно добавить не больше 15 других названий.")
        return

    await state.update_data(aliases=aliases)
    await show_confirmation(message, state)


async def skip_country_aliases(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    await state.update_data(aliases=[])
    await callback.answer()
    await show_confirmation(callback.message, state)


async def cancel_country_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    await state.clear()
    await callback.answer("Отменено")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer("❌ Добавление страны отменено.")


async def save_country_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    values = await state.get_data()
    country = values.get("country")
    capital = values.get("capital")
    aliases = values.get("aliases", [])

    if not country or not capital:
        await state.clear()
        await callback.answer("Данные потерялись. Начни заново.", show_alert=True)
        return

    if country_exists_as_name_or_alias(country):
        await state.clear()
        await callback.answer("Такая страна уже появилась в базе.", show_alert=True)
        return

    custom = load_custom_countries()
    custom[country] = {
        "capital": capital,
        "aliases": aliases,
    }

    try:
        save_custom_countries(custom)
    except OSError:
        await callback.answer(
            "Не удалось записать файл. Попробуй ещё раз.",
            show_alert=True,
        )
        return

    COUNTRIES[country] = {
        "capital": capital,
        "aliases": aliases,
    }

    await state.clear()
    await callback.answer("Сохранено")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    await callback.message.answer(
        "✅ Страна добавлена и уже участвует в квизе:\n\n"
        f"🌍 <b>{escape(country)}</b>\n"
        f"🏛 <b>{escape(capital)}</b>"
    )


async def country_admin_non_text(message: Message) -> None:
    await message.answer("Пришли обычный текст или используй /cancel.")


def register_country_admin(dispatcher: Dispatcher) -> None:
    # Эти обработчики регистрируются перед общим F.text-обработчиком бота.
    dispatcher.message.register(add_country_start, Command("addcountry"))
    dispatcher.message.register(
        cancel_country_add,
        Command("cancel"),
        StateFilter(
            AddCountry.country,
            AddCountry.capital,
            AddCountry.aliases,
            AddCountry.confirm,
        ),
    )
    dispatcher.message.register(
        list_custom_countries,
        Command("customcountries"),
    )
    dispatcher.message.register(
        add_country_name,
        AddCountry.country,
        F.text,
    )
    dispatcher.message.register(
        add_country_capital,
        AddCountry.capital,
        F.text,
    )
    dispatcher.message.register(
        add_country_aliases,
        AddCountry.aliases,
        F.text,
    )
    dispatcher.callback_query.register(
        skip_country_aliases,
        AddCountry.aliases,
        F.data == "country_admin:skip_aliases",
    )
    dispatcher.callback_query.register(
        cancel_country_callback,
        F.data == "country_admin:cancel",
    )
    dispatcher.callback_query.register(
        save_country_callback,
        AddCountry.confirm,
        F.data == "country_admin:save",
    )
    dispatcher.message.register(
        country_admin_non_text,
        StateFilter(
            AddCountry.country,
            AddCountry.capital,
            AddCountry.aliases,
        ),
    )


# Подмешиваем пользовательские страны при каждом запуске бота.
COUNTRIES.update(load_custom_countries())
