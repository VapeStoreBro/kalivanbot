import asyncio
import json
import math
import time
from copy import deepcopy
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
ADMIN_COMMANDS = ("admin", "addcountry", "customcountries", "cancel")
CUSTOM_COUNTRIES_FILE = Path(__file__).with_name("custom_countries.json")
NEW_COUNTRY_SECONDS = 60 * 60
LIST_PAGE_SIZE = 8
SEARCH_PAGE_SIZE = 8

BUILTIN_COUNTRIES = deepcopy(COUNTRIES)
STORAGE_LOCK = asyncio.Lock()


class AddCountry(StatesGroup):
    country = State()
    capital = State()
    aliases = State()
    confirm = State()


class SearchCountry(StatesGroup):
    query = State()


class EditCountry(StatesGroup):
    name = State()
    capital = State()
    aliases = State()


def normalize(text: str) -> str:
    return " ".join(text.lower().replace("ё", "е").strip().split())


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def is_private_admin_message(message: Message) -> bool:
    return bool(
        message.from_user
        and is_admin(message.from_user.id)
        and message.chat.type == ChatType.PRIVATE
    )


def is_private_admin_callback(callback: CallbackQuery) -> bool:
    return bool(
        callback.message
        and is_admin(callback.from_user.id)
        and callback.message.chat.type == ChatType.PRIVATE
    )


def is_valid_name(value: str) -> bool:
    return 2 <= len(value) <= 80 and all(
        char.isalpha() or char in " -'’." for char in value
    )


def clean_aliases(raw_aliases, country: str) -> list[str] | None:
    aliases = []
    for raw_alias in raw_aliases:
        alias = normalize(str(raw_alias))
        if not alias or alias == country or alias in aliases:
            continue
        if not is_valid_name(alias):
            return None
        aliases.append(alias)
    return aliases


def empty_storage() -> dict:
    return {"version": 2, "records": {}, "deleted_builtins": []}


def clean_record(country: str, raw_info: dict) -> dict | None:
    if not is_valid_name(country) or not isinstance(raw_info, dict):
        return None

    capital = normalize(str(raw_info.get("capital", "")))
    if not is_valid_name(capital):
        return None

    aliases = clean_aliases(raw_info.get("aliases", []), country)
    if aliases is None:
        return None

    source = raw_info.get("source", "custom")
    if source not in {"builtin", "custom"}:
        source = "custom"

    original_name = raw_info.get("original_name")
    if source == "builtin":
        original_name = normalize(str(original_name or country))
        if original_name not in BUILTIN_COUNTRIES:
            return None
    else:
        original_name = None

    try:
        created_at = float(raw_info.get("created_at", 0) or 0)
    except (TypeError, ValueError):
        created_at = 0

    return {
        "capital": capital,
        "aliases": aliases,
        "source": source,
        "original_name": original_name,
        "created_at": created_at,
    }


def load_storage() -> dict:
    if not CUSTOM_COUNTRIES_FILE.exists():
        return empty_storage()

    try:
        with CUSTOM_COUNTRIES_FILE.open("r", encoding="utf-8") as file:
            raw = json.load(file)
    except (OSError, json.JSONDecodeError):
        return empty_storage()

    result = empty_storage()
    if not isinstance(raw, dict):
        return result

    # Формат v2 хранит добавленные страны, изменения встроенных и удаления.
    if raw.get("version") == 2 and isinstance(raw.get("records"), dict):
        raw_records = raw["records"]
        raw_deleted = raw.get("deleted_builtins", [])
    else:
        # Автоматическая миграция старого формата {страна: {capital, aliases}}.
        raw_records = {
            country: {**info, "source": "custom", "created_at": 0}
            for country, info in raw.items()
            if isinstance(info, dict)
        }
        raw_deleted = []

    for raw_country, raw_info in raw_records.items():
        country = normalize(str(raw_country))
        record = clean_record(country, raw_info)
        if record:
            result["records"][country] = record

    deleted = []
    for raw_country in raw_deleted:
        country = normalize(str(raw_country))
        if country in BUILTIN_COUNTRIES and country not in deleted:
            deleted.append(country)
    result["deleted_builtins"] = deleted
    return result


def save_storage() -> None:
    temp_file = CUSTOM_COUNTRIES_FILE.with_suffix(".json.tmp")
    with temp_file.open("w", encoding="utf-8") as file:
        json.dump(STORAGE, file, ensure_ascii=False, indent=2, sort_keys=True)
        file.flush()
    temp_file.replace(CUSTOM_COUNTRIES_FILE)


def rebuild_countries() -> None:
    deleted = set(STORAGE["deleted_builtins"])
    overridden = {
        info["original_name"]
        for info in STORAGE["records"].values()
        if info["source"] == "builtin"
    }

    active = {
        country: deepcopy(info)
        for country, info in BUILTIN_COUNTRIES.items()
        if country not in deleted and country not in overridden
    }
    for country, info in STORAGE["records"].items():
        active[country] = {
            "capital": info["capital"],
            "aliases": list(info["aliases"]),
        }

    COUNTRIES.clear()
    COUNTRIES.update(active)


STORAGE = load_storage()
rebuild_countries()


def value_is_used(value: str, ignore_country: str | None = None) -> bool:
    for country, info in COUNTRIES.items():
        if country == ignore_country:
            continue
        if value == country or value in info.get("aliases", []):
            return True
    return False


def country_metadata(country: str) -> dict:
    stored = STORAGE["records"].get(country)
    if stored:
        return deepcopy(stored)
    return {
        "capital": COUNTRIES[country]["capital"],
        "aliases": list(COUNTRIES[country].get("aliases", [])),
        "source": "builtin",
        "original_name": country,
        "created_at": 0,
    }


def is_new_country(country: str) -> bool:
    record = STORAGE["records"].get(country)
    if not record or record["source"] != "custom":
        return False
    created_at = record.get("created_at", 0)
    return bool(created_at and time.time() - created_at < NEW_COUNTRY_SECONDS)


def sorted_countries(scope: str = "all") -> list[str]:
    countries = sorted(COUNTRIES)
    if scope == "new":
        return [country for country in countries if is_new_country(country)]
    return countries


def parse_aliases(text: str, country: str) -> tuple[list[str] | None, str | None]:
    if normalize(text) in {"-", "нет", "без алиасов"}:
        return [], None

    aliases = clean_aliases(text.split(","), country)
    if aliases is None:
        return None, "Используй только буквы, пробелы и дефисы."
    if len(aliases) > 15:
        return None, "Можно сохранить не больше 15 алиасов."
    for alias in aliases:
        if value_is_used(alias, ignore_country=country):
            return None, f"Название «{alias}» уже используется в базе."
    return aliases, None


def panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="➕ Добавить", callback_data="ca:add"),
                InlineKeyboardButton(text="🔎 Поиск", callback_data="ca:search"),
            ],
            [
                InlineKeyboardButton(text="🌍 Все страны", callback_data="ca:all:0"),
                InlineKeyboardButton(text="🆕 НОВЫЕ", callback_data="ca:new:0"),
            ],
        ]
    )


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="ca:cancel")]
        ]
    )


def aliases_keyboard(callback_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➡️ Без алиасов",
                    callback_data=callback_data,
                )
            ],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="ca:cancel")],
        ]
    )


def confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Сохранить", callback_data="ca:add_save"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="ca:cancel"),
            ]
        ]
    )


def country_card_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✏️ Название", callback_data="ca:edit_name"),
                InlineKeyboardButton(text="🏛 Столица", callback_data="ca:edit_capital"),
            ],
            [
                InlineKeyboardButton(text="🔎 Алиасы", callback_data="ca:edit_aliases"),
                InlineKeyboardButton(text="🗑 Удалить", callback_data="ca:delete"),
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="ca:card_back")],
        ]
    )


async def safe_edit(callback: CallbackQuery, text: str, keyboard: InlineKeyboardMarkup) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=keyboard)
    except Exception:
        await callback.message.answer(text, reply_markup=keyboard)


async def show_panel_message(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "🔐 <b>Скрытая админка стран</b>\n\nВыбери действие:",
        reply_markup=panel_keyboard(),
    )


async def show_panel_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await safe_edit(
        callback,
        "🔐 <b>Скрытая админка стран</b>\n\nВыбери действие:",
        panel_keyboard(),
    )


def page_keyboard(items: list[str], scope: str, page: int) -> InlineKeyboardMarkup:
    page_count = max(1, math.ceil(len(items) / LIST_PAGE_SIZE))
    page = max(0, min(page, page_count - 1))
    start = page * LIST_PAGE_SIZE
    page_items = items[start : start + LIST_PAGE_SIZE]
    rows = [
        [
            InlineKeyboardButton(
                text=("🆕 " if is_new_country(country) else "") + country.title(),
                callback_data=f"ca:item:{scope}:{page}:{index}",
            )
        ]
        for index, country in enumerate(page_items)
    ]

    navigation = []
    if page > 0:
        navigation.append(
            InlineKeyboardButton(text="⬅️", callback_data=f"ca:{scope}:{page - 1}")
        )
    navigation.append(
        InlineKeyboardButton(text=f"{page + 1}/{page_count}", callback_data="ca:noop")
    )
    if page + 1 < page_count:
        navigation.append(
            InlineKeyboardButton(text="➡️", callback_data=f"ca:{scope}:{page + 1}")
        )
    rows.append(navigation)
    rows.append([InlineKeyboardButton(text="🏠 Админка", callback_data="ca:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def render_country_list(
    callback: CallbackQuery,
    state: FSMContext,
    scope: str,
    page: int,
) -> None:
    items = sorted_countries(scope)
    page_count = max(1, math.ceil(len(items) / LIST_PAGE_SIZE))
    page = max(0, min(page, page_count - 1))
    await state.set_state(None)
    await state.set_data({"return_kind": scope, "return_page": page})

    if scope == "new":
        title = "🆕 <b>НОВЫЕ</b>"
        empty = "\n\nЗа последний час новых стран нет."
    else:
        title = "🌍 <b>Все страны</b>"
        empty = "\n\nСписок пуст."
    body = f"{title}\n\nВсего: <b>{len(items)}</b>"
    if not items:
        body += empty
    await safe_edit(callback, body, page_keyboard(items, scope, page))


def search_results_keyboard(results: list[str], page: int) -> InlineKeyboardMarkup:
    page_count = max(1, math.ceil(len(results) / SEARCH_PAGE_SIZE))
    page = max(0, min(page, page_count - 1))
    start = page * SEARCH_PAGE_SIZE
    rows = [
        [
            InlineKeyboardButton(
                text=country.title(),
                callback_data=f"ca:search_item:{index}",
            )
        ]
        for index, country in enumerate(results[start : start + SEARCH_PAGE_SIZE], start)
    ]
    navigation = []
    if page > 0:
        navigation.append(
            InlineKeyboardButton(text="⬅️", callback_data=f"ca:search_results:{page - 1}")
        )
    navigation.append(
        InlineKeyboardButton(text=f"{page + 1}/{page_count}", callback_data="ca:noop")
    )
    if page + 1 < page_count:
        navigation.append(
            InlineKeyboardButton(text="➡️", callback_data=f"ca:search_results:{page + 1}")
        )
    rows.append(navigation)
    rows.append(
        [
            InlineKeyboardButton(text="🔎 Новый поиск", callback_data="ca:search"),
            InlineKeyboardButton(text="🏠 Админка", callback_data="ca:home"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def render_search_results(
    callback: CallbackQuery,
    state: FSMContext,
    page: int,
) -> None:
    values = await state.get_data()
    results = [country for country in values.get("search_results", []) if country in COUNTRIES]
    query = values.get("search_query", "")
    await state.set_state(None)
    await safe_edit(
        callback,
        f"🔎 Результаты по запросу <b>{escape(query)}</b>\n\nНайдено: <b>{len(results)}</b>",
        search_results_keyboard(results, page),
    )


async def render_country_card(
    callback: CallbackQuery,
    state: FSMContext,
    country: str,
) -> None:
    if country not in COUNTRIES:
        await show_panel_callback(callback, state)
        return

    values = await state.get_data()
    values["selected_country"] = country
    await state.set_state(None)
    await state.set_data(values)

    info = COUNTRIES[country]
    aliases = ", ".join(escape(alias) for alias in info.get("aliases", [])) or "нет"
    new_label = "\n🆕 Добавлена меньше часа назад" if is_new_country(country) else ""
    text = (
        f"🌍 <b>{escape(country.title())}</b>\n"
        f"🏛 Столица: <b>{escape(info['capital'].title())}</b>\n"
        f"🔎 Алиасы: <b>{aliases}</b>"
        f"{new_label}"
    )
    await safe_edit(callback, text, country_card_keyboard())


async def show_add_confirmation(message: Message, state: FSMContext) -> None:
    values = await state.get_data()
    aliases = values.get("aliases", [])
    aliases_text = ", ".join(escape(alias) for alias in aliases) or "нет"
    await state.set_state(AddCountry.confirm)
    await message.answer(
        "Проверь данные:\n\n"
        f"🌍 Страна: <b>{escape(values['country'].title())}</b>\n"
        f"🏛 Столица: <b>{escape(values['capital'].title())}</b>\n"
        f"🔎 Алиасы: <b>{aliases_text}</b>",
        reply_markup=confirmation_keyboard(),
    )


async def handle_admin_command(message: Message, state: FSMContext) -> None:
    # Обработчик намеренно поглощает команды от всех остальных без ответа.
    if not is_private_admin_message(message):
        return

    command = message.text.split(maxsplit=1)[0].split("@", 1)[0].lower()
    if command == "/addcountry":
        await state.clear()
        await state.set_state(AddCountry.country)
        await message.answer(
            "🌍 Напиши название новой страны.",
            reply_markup=cancel_keyboard(),
        )
        return
    if command == "/customcountries":
        await show_panel_message(message, state)
        return
    if command == "/cancel":
        await state.clear()
        await message.answer("Отменено.", reply_markup=panel_keyboard())
        return
    await show_panel_message(message, state)


async def cancel_active_action(message: Message, state: FSMContext) -> None:
    if not is_private_admin_message(message):
        return
    await state.clear()
    await message.answer("Отменено.", reply_markup=panel_keyboard())


async def add_country_name(message: Message, state: FSMContext) -> None:
    if not is_private_admin_message(message):
        return
    country = normalize(message.text)
    if not is_valid_name(country):
        await message.answer("Некорректное название. Нужно от 2 до 80 букв.")
        return
    if value_is_used(country):
        await message.answer("Такая страна или алиас уже есть в базе.")
        return

    await state.update_data(country=country)
    await state.set_state(AddCountry.capital)
    await message.answer(f"🏛 Напиши столицу страны <b>{escape(country.title())}</b>.")


async def add_country_capital(message: Message, state: FSMContext) -> None:
    if not is_private_admin_message(message):
        return
    capital = normalize(message.text)
    if not is_valid_name(capital):
        await message.answer("Некорректная столица. Нужно от 2 до 80 букв.")
        return

    await state.update_data(capital=capital)
    await state.set_state(AddCountry.aliases)
    await message.answer(
        "🔎 Напиши алиасы страны через запятую.",
        reply_markup=aliases_keyboard("ca:add_no_aliases"),
    )


async def add_country_aliases(message: Message, state: FSMContext) -> None:
    if not is_private_admin_message(message):
        return
    values = await state.get_data()
    aliases, error = parse_aliases(message.text, values["country"])
    if error:
        await message.answer(f"❌ {escape(error)}")
        return
    await state.update_data(aliases=aliases)
    await show_add_confirmation(message, state)


async def search_country_text(message: Message, state: FSMContext) -> None:
    if not is_private_admin_message(message):
        return
    query = normalize(message.text)
    if len(query) < 2:
        await message.answer("Введи хотя бы две буквы.")
        return

    results = [
        country
        for country, info in sorted(COUNTRIES.items())
        if query in country or any(query in alias for alias in info.get("aliases", []))
    ]
    await state.set_state(None)
    await state.set_data({"search_query": query, "search_results": results, "return_kind": "search"})
    text = f"🔎 Результаты по запросу <b>{escape(query)}</b>\n\nНайдено: <b>{len(results)}</b>"
    await message.answer(text, reply_markup=search_results_keyboard(results, 0))


async def edit_country_name(message: Message, state: FSMContext) -> None:
    if not is_private_admin_message(message):
        return
    values = await state.get_data()
    old_country = values.get("selected_country")
    if old_country not in COUNTRIES:
        await state.clear()
        await message.answer("Страна больше не найдена.", reply_markup=panel_keyboard())
        return

    new_country = normalize(message.text)
    if not is_valid_name(new_country):
        await message.answer("Некорректное название. Нужно от 2 до 80 букв.")
        return
    if value_is_used(new_country, ignore_country=old_country):
        await message.answer("Такое название или алиас уже используется.")
        return

    async with STORAGE_LOCK:
        snapshot = deepcopy(STORAGE)
        metadata = country_metadata(old_country)
        info = COUNTRIES[old_country]
        aliases = [alias for alias in info.get("aliases", []) if alias != new_country]
        STORAGE["records"].pop(old_country, None)
        STORAGE["records"][new_country] = {
            **metadata,
            "capital": info["capital"],
            "aliases": aliases,
        }
        try:
            save_storage()
            rebuild_countries()
        except OSError:
            STORAGE.clear()
            STORAGE.update(snapshot)
            rebuild_countries()
            await message.answer("Не удалось сохранить файл. Попробуй ещё раз.")
            return

    await state.clear()
    await message.answer(
        f"✅ Название изменено на <b>{escape(new_country.title())}</b>.",
        reply_markup=panel_keyboard(),
    )


async def edit_country_capital(message: Message, state: FSMContext) -> None:
    if not is_private_admin_message(message):
        return
    values = await state.get_data()
    country = values.get("selected_country")
    if country not in COUNTRIES:
        await state.clear()
        await message.answer("Страна больше не найдена.", reply_markup=panel_keyboard())
        return

    capital = normalize(message.text)
    if not is_valid_name(capital):
        await message.answer("Некорректная столица. Нужно от 2 до 80 букв.")
        return

    async with STORAGE_LOCK:
        snapshot = deepcopy(STORAGE)
        metadata = country_metadata(country)
        STORAGE["records"][country] = {
            **metadata,
            "capital": capital,
            "aliases": list(COUNTRIES[country].get("aliases", [])),
        }
        try:
            save_storage()
            rebuild_countries()
        except OSError:
            STORAGE.clear()
            STORAGE.update(snapshot)
            rebuild_countries()
            await message.answer("Не удалось сохранить файл. Попробуй ещё раз.")
            return

    await state.clear()
    await message.answer("✅ Столица изменена.", reply_markup=panel_keyboard())


async def edit_country_aliases(message: Message, state: FSMContext) -> None:
    if not is_private_admin_message(message):
        return
    values = await state.get_data()
    country = values.get("selected_country")
    if country not in COUNTRIES:
        await state.clear()
        await message.answer("Страна больше не найдена.", reply_markup=panel_keyboard())
        return

    aliases, error = parse_aliases(message.text, country)
    if error:
        await message.answer(f"❌ {escape(error)}")
        return
    await persist_aliases(message, state, country, aliases)


async def persist_aliases(
    message: Message,
    state: FSMContext,
    country: str,
    aliases: list[str],
) -> None:
    async with STORAGE_LOCK:
        snapshot = deepcopy(STORAGE)
        metadata = country_metadata(country)
        STORAGE["records"][country] = {
            **metadata,
            "capital": COUNTRIES[country]["capital"],
            "aliases": aliases,
        }
        try:
            save_storage()
            rebuild_countries()
        except OSError:
            STORAGE.clear()
            STORAGE.update(snapshot)
            rebuild_countries()
            await message.answer("Не удалось сохранить файл. Попробуй ещё раз.")
            return

    await state.clear()
    await message.answer("✅ Алиасы изменены.", reply_markup=panel_keyboard())


async def country_admin_non_text(message: Message) -> None:
    if is_private_admin_message(message):
        await message.answer("Пришли обычный текст или используй /cancel.")


async def country_admin_callback(callback: CallbackQuery, state: FSMContext) -> None:
    # Поддельные/чужие callback-запросы закрываются без текста и без утечки админки.
    if not is_private_admin_callback(callback):
        await callback.answer()
        return
    await callback.answer()

    data = callback.data or ""
    if data == "ca:noop":
        return
    if data == "ca:home":
        await show_panel_callback(callback, state)
        return
    if data == "ca:cancel":
        await show_panel_callback(callback, state)
        return
    if data == "ca:add":
        await state.clear()
        await state.set_state(AddCountry.country)
        await safe_edit(
            callback,
            "🌍 Напиши название новой страны.",
            cancel_keyboard(),
        )
        return
    if data == "ca:search":
        await state.clear()
        await state.set_state(SearchCountry.query)
        await safe_edit(
            callback,
            "🔎 Напиши хотя бы две буквы из названия страны или алиаса.",
            cancel_keyboard(),
        )
        return
    if data.startswith("ca:all:") or data.startswith("ca:new:"):
        _, scope, raw_page = data.split(":", 2)
        await render_country_list(callback, state, scope, int(raw_page))
        return
    if data.startswith("ca:item:"):
        _, _, scope, raw_page, raw_index = data.split(":", 4)
        page = int(raw_page)
        index = int(raw_index)
        items = sorted_countries(scope)
        absolute_index = page * LIST_PAGE_SIZE + index
        if absolute_index >= len(items):
            await render_country_list(callback, state, scope, page)
            return
        await state.set_data({
            "return_kind": scope,
            "return_page": page,
            "selected_country": items[absolute_index],
        })
        await render_country_card(callback, state, items[absolute_index])
        return
    if data.startswith("ca:search_results:"):
        await render_search_results(callback, state, int(data.rsplit(":", 1)[1]))
        return
    if data.startswith("ca:search_item:"):
        index = int(data.rsplit(":", 1)[1])
        values = await state.get_data()
        results = values.get("search_results", [])
        if index >= len(results) or results[index] not in COUNTRIES:
            await show_panel_callback(callback, state)
            return
        values["return_kind"] = "search"
        values["selected_country"] = results[index]
        await state.set_data(values)
        await render_country_card(callback, state, results[index])
        return
    if data == "ca:card_back":
        values = await state.get_data()
        if values.get("return_kind") == "search":
            await render_search_results(callback, state, 0)
        else:
            await render_country_list(
                callback,
                state,
                values.get("return_kind", "all"),
                int(values.get("return_page", 0)),
            )
        return
    if data == "ca:add_no_aliases":
        if await state.get_state() == AddCountry.aliases.state:
            await state.update_data(aliases=[])
            await show_add_confirmation(callback.message, state)
        return
    if data == "ca:add_save":
        if await state.get_state() != AddCountry.confirm.state:
            return
        values = await state.get_data()
        country = values.get("country")
        capital = values.get("capital")
        aliases = values.get("aliases", [])
        if not country or not capital or value_is_used(country):
            await show_panel_callback(callback, state)
            return
        if any(value_is_used(alias) for alias in aliases):
            await show_panel_callback(callback, state)
            return

        async with STORAGE_LOCK:
            snapshot = deepcopy(STORAGE)
            STORAGE["records"][country] = {
                "capital": capital,
                "aliases": aliases,
                "source": "custom",
                "original_name": None,
                "created_at": time.time(),
            }
            try:
                save_storage()
                rebuild_countries()
            except OSError:
                STORAGE.clear()
                STORAGE.update(snapshot)
                rebuild_countries()
                await callback.message.answer("Не удалось сохранить файл. Попробуй ещё раз.")
                return
        await state.clear()
        await safe_edit(
            callback,
            f"✅ Страна <b>{escape(country.title())}</b> добавлена.",
            panel_keyboard(),
        )
        return

    values = await state.get_data()
    country = values.get("selected_country")
    if country not in COUNTRIES:
        await show_panel_callback(callback, state)
        return
    if data == "ca:edit_name":
        await state.set_state(EditCountry.name)
        await callback.message.answer(
            f"✏️ Новое название для <b>{escape(country.title())}</b>:",
            reply_markup=cancel_keyboard(),
        )
        return
    if data == "ca:edit_capital":
        await state.set_state(EditCountry.capital)
        await callback.message.answer(
            f"🏛 Новая столица для <b>{escape(country.title())}</b>:",
            reply_markup=cancel_keyboard(),
        )
        return
    if data == "ca:edit_aliases":
        await state.set_state(EditCountry.aliases)
        await callback.message.answer(
            "🔎 Напиши новый полный список алиасов через запятую.",
            reply_markup=aliases_keyboard("ca:edit_no_aliases"),
        )
        return
    if data == "ca:edit_no_aliases":
        if await state.get_state() == EditCountry.aliases.state:
            await persist_aliases(callback.message, state, country, [])
        return
    if data == "ca:delete":
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="🗑 Да, удалить", callback_data="ca:delete_yes"),
                    InlineKeyboardButton(text="Отмена", callback_data="ca:delete_no"),
                ]
            ]
        )
        await safe_edit(
            callback,
            f"Удалить страну <b>{escape(country.title())}</b>?",
            keyboard,
        )
        return
    if data == "ca:delete_no":
        await render_country_card(callback, state, country)
        return
    if data == "ca:delete_yes":
        async with STORAGE_LOCK:
            snapshot = deepcopy(STORAGE)
            metadata = country_metadata(country)
            STORAGE["records"].pop(country, None)
            if metadata["source"] == "builtin":
                original_name = metadata["original_name"]
                if original_name not in STORAGE["deleted_builtins"]:
                    STORAGE["deleted_builtins"].append(original_name)
            try:
                save_storage()
                rebuild_countries()
            except OSError:
                STORAGE.clear()
                STORAGE.update(snapshot)
                rebuild_countries()
                await callback.message.answer("Не удалось сохранить файл. Попробуй ещё раз.")
                return
        await state.clear()
        await safe_edit(
            callback,
            f"🗑 Страна <b>{escape(country.title())}</b> удалена.",
            panel_keyboard(),
        )


def register_country_admin(dispatcher: Dispatcher) -> None:
    # Сначала поглощаем скрытые команды: чужие пользователи не получают ответа.
    dispatcher.message.register(
        cancel_active_action,
        Command("cancel"),
        StateFilter(
            AddCountry.country,
            AddCountry.capital,
            AddCountry.aliases,
            AddCountry.confirm,
            SearchCountry.query,
            EditCountry.name,
            EditCountry.capital,
            EditCountry.aliases,
        ),
    )
    dispatcher.message.register(
        handle_admin_command,
        Command(commands=ADMIN_COMMANDS),
    )
    dispatcher.message.register(add_country_name, AddCountry.country, F.text)
    dispatcher.message.register(add_country_capital, AddCountry.capital, F.text)
    dispatcher.message.register(add_country_aliases, AddCountry.aliases, F.text)
    dispatcher.message.register(search_country_text, SearchCountry.query, F.text)
    dispatcher.message.register(edit_country_name, EditCountry.name, F.text)
    dispatcher.message.register(edit_country_capital, EditCountry.capital, F.text)
    dispatcher.message.register(edit_country_aliases, EditCountry.aliases, F.text)
    dispatcher.callback_query.register(
        country_admin_callback,
        F.data.startswith("ca:"),
    )
    dispatcher.message.register(
        country_admin_non_text,
        StateFilter(
            AddCountry.country,
            AddCountry.capital,
            AddCountry.aliases,
            SearchCountry.query,
            EditCountry.name,
            EditCountry.capital,
            EditCountry.aliases,
        ),
    )
