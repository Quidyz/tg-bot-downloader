import html
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import BaseFilter, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    MessageOriginUser,
)

from bot.config import Config
from bot.db.repo import Repo

router = Router(name="admin")

PLATFORM_TITLES = {
    "tiktok": "TikTok",
    "instagram": "Instagram",
    "youtube": "YouTube",
    "pinterest": "Pinterest",
    "threads": "Threads",
}

# Редактируемые значения: key -> (заголовок кнопки, подсказка при вводе)
EDITABLE_SETTINGS = {
    "daily_limit": ("📥 Лимит в день", "Введи число скачиваний в день для бесплатных пользователей:"),
    "channel_id": ("📢 Канал", "Введи @username канала или его ID (вида -100...).\nБот должен быть админом канала, чтобы проверять подписку.\nОтправь «-», чтобы очистить."),
    "channel_url": ("🔗 Ссылка на канал", "Введи ссылку-приглашение на канал (для кнопки «Подписаться»).\nОтправь «-», чтобы очистить."),
    "ad_text": ("📝 Текст рекламы", "Введи рекламный текст (добавляется к скачанным файлам).\nОтправь «-», чтобы очистить."),
    "ad_url": ("🔗 Ссылка рекламы", "Введи URL для рекламной кнопки.\nОтправь «-», чтобы очистить."),
    "premium_price_stars": ("⭐ Цена Premium", "Введи цену Premium в Telegram Stars (целое число):"),
    "premium_days": ("📅 Дней Premium", "Введи, на сколько дней выдаётся Premium:"),
}

TOGGLES = {
    "limit_enabled": "Дневной лимит",
    "channel_sub_enabled": "Подписка на канал",
    "ads_enabled": "Реклама",
}

INT_SETTINGS = {"daily_limit", "premium_price_stars", "premium_days"}


class AdminStates(StatesGroup):
    waiting_user = State()
    waiting_value = State()


class IsAdmin(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery, config: Config, db_user=None) -> bool:
        user = event.from_user
        if user is None:
            return False
        if user.id in config.admin_id_list:
            return True
        return bool(db_user and db_user["is_admin"])


router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


def _main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Статистика", callback_data="adm:stats")],
            [InlineKeyboardButton(text="👤 Выдать доступ", callback_data="adm:access")],
            [InlineKeyboardButton(text="⚙️ Механики и настройки", callback_data="adm:settings")],
        ]
    )


def _back_button() -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:main")]


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("🛠 <b>Админ-панель</b>", reply_markup=_main_menu())


@router.callback_query(F.data == "adm:main")
async def cb_main(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.edit_text("🛠 <b>Админ-панель</b>", reply_markup=_main_menu())
    await callback.answer()


# ---------- статистика ----------

@router.callback_query(F.data == "adm:stats")
async def cb_stats(callback: CallbackQuery, repo: Repo) -> None:
    s = await repo.stats()
    platforms = "\n".join(
        f"  • {PLATFORM_TITLES.get(name, name)}: {count}"
        for name, count in s["by_platform"].items()
    ) or "  • пока пусто"
    text = (
        "📊 <b>Статистика</b>\n\n"
        f"👥 Пользователи: <b>{s['users_total']}</b>\n"
        f"  • новых сегодня: {s['users_new_today']}\n"
        f"  • активных сегодня: {s['users_active_today']}\n\n"
        f"📥 Скачивания: <b>{s['downloads_total']}</b>\n"
        f"  • сегодня: {s['downloads_today']}\n"
        f"  • за неделю: {s['downloads_week']}\n\n"
        f"📱 По платформам:\n{platforms}\n\n"
        f"⭐ Активных Premium: {s['premium_active']}\n"
        f"✅ В белом списке: {s['whitelisted']}"
    )
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[_back_button()])
        )
    await callback.answer()


# ---------- выдача доступа ----------

@router.callback_query(F.data == "adm:access")
async def cb_access(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminStates.waiting_user)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "👤 Пришли <b>ID</b> или <b>@username</b> пользователя,\n"
            "либо перешли сюда любое его сообщение.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[_back_button()]),
        )
    await callback.answer()


@router.message(AdminStates.waiting_user)
async def receive_user(message: Message, state: FSMContext, repo: Repo) -> None:
    target = None
    if isinstance(message.forward_origin, MessageOriginUser):
        sender = message.forward_origin.sender_user
        target = await repo.upsert_user(sender.id, sender.username, sender.first_name)
    elif message.text:
        text = message.text.strip()
        if text.lstrip("-").isdigit():
            target = await repo.get_user(int(text))
            if target is None:
                # Пользователь ещё не писал боту — создаём запись по ID
                target = await repo.upsert_user(int(text), None, None)
        else:
            target = await repo.find_user_by_username(text)

    if target is None:
        await message.answer(
            "Не нашёл такого пользователя. Если у него скрыт профиль при пересылке — "
            "пришли его числовой ID (можно узнать у @userinfobot)."
        )
        return

    await state.clear()
    await _show_user_card(message, repo, target["id"])


async def _show_user_card(message_or_cb_message: Message, repo: Repo, user_id: int) -> None:
    user = await repo.get_user(user_id)
    if user is None:
        await message_or_cb_message.answer("Пользователь не найден.")
        return
    name = html.escape(user["first_name"] or "")
    username = f"@{user['username']}" if user["username"] else "—"
    premium = "нет"
    if repo.is_premium(user):
        premium = "до " + datetime.fromisoformat(user["premium_until"]).strftime("%d.%m.%Y")
    downloads_today = await repo.count_downloads_today(user_id)

    wl = "✅" if user["is_whitelisted"] else "❌"
    text = (
        f"👤 <b>{name}</b> ({username})\n"
        f"ID: <code>{user['id']}</code>\n\n"
        f"Белый список: {wl}\n"
        f"Premium: {premium}\n"
        f"Скачиваний сегодня: {downloads_today}"
    )
    days = await repo.get_setting_int("premium_days")
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=("❌ Убрать из белого списка" if user["is_whitelisted"] else "✅ В белый список"),
                    callback_data=f"adm:wl:{user_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"⭐ Выдать Premium ({days} дн.)", callback_data=f"adm:prem:{user_id}"
                )
            ],
            [InlineKeyboardButton(text="🚫 Забрать Premium", callback_data=f"adm:unprem:{user_id}")],
            _back_button(),
        ]
    )
    await message_or_cb_message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("adm:wl:"))
async def cb_toggle_whitelist(callback: CallbackQuery, repo: Repo) -> None:
    if not callback.data:
        return
    user_id = int(callback.data.split(":")[2])
    user = await repo.get_user(user_id)
    if user is None:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    new_value = not bool(user["is_whitelisted"])
    await repo.set_whitelist(user_id, new_value)
    await callback.answer("Добавлен в белый список ✅" if new_value else "Убран из белого списка")
    await _refresh_user_card(callback, repo, user_id)


@router.callback_query(F.data.startswith("adm:prem:"))
async def cb_grant_premium(callback: CallbackQuery, repo: Repo) -> None:
    if not callback.data:
        return
    user_id = int(callback.data.split(":")[2])
    days = await repo.get_setting_int("premium_days")
    until = await repo.grant_premium(user_id, days)
    await callback.answer(f"Premium выдан до {until.strftime('%d.%m.%Y')} ⭐")
    await _refresh_user_card(callback, repo, user_id)


@router.callback_query(F.data.startswith("adm:unprem:"))
async def cb_revoke_premium(callback: CallbackQuery, repo: Repo) -> None:
    if not callback.data:
        return
    user_id = int(callback.data.split(":")[2])
    await repo.revoke_premium(user_id)
    await callback.answer("Premium снят")
    await _refresh_user_card(callback, repo, user_id)


async def _refresh_user_card(callback: CallbackQuery, repo: Repo, user_id: int) -> None:
    if not isinstance(callback.message, Message):
        return
    try:
        await callback.message.delete()
    except Exception:
        pass
    await _show_user_card(callback.message, repo, user_id)


# ---------- настройки ----------

async def _settings_view(repo: Repo) -> tuple[str, InlineKeyboardMarkup]:
    rows = []
    lines = ["⚙️ <b>Механики и настройки</b>\n"]
    for key, title in TOGGLES.items():
        enabled = await repo.get_setting_bool(key)
        icon = "🟢" if enabled else "🔴"
        rows.append(
            [InlineKeyboardButton(text=f"{icon} {title}", callback_data=f"adm:toggle:{key}")]
        )

    values = {}
    for key in EDITABLE_SETTINGS:
        values[key] = await repo.get_setting(key)

    lines.append(
        f"Лимит: <b>{values['daily_limit']}</b>/день · "
        f"Premium: <b>{values['premium_price_stars']}</b> ⭐ на <b>{values['premium_days']}</b> дн."
    )
    lines.append(f"Канал: <b>{html.escape(values['channel_id'] or '—')}</b>")
    ad_preview = html.escape(values["ad_text"][:50]) or "—"
    lines.append(f"Реклама: {ad_preview}")

    for key, (title, _) in EDITABLE_SETTINGS.items():
        rows.append([InlineKeyboardButton(text=title, callback_data=f"adm:edit:{key}")])
    rows.append(_back_button())
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "adm:settings")
async def cb_settings(callback: CallbackQuery, repo: Repo, state: FSMContext) -> None:
    await state.clear()
    text, keyboard = await _settings_view(repo)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("adm:toggle:"))
async def cb_toggle(callback: CallbackQuery, repo: Repo) -> None:
    if not callback.data:
        await callback.answer()
        return
    key = callback.data.split(":")[2]
    if key not in TOGGLES:
        await callback.answer()
        return
    new_value = await repo.toggle_setting(key)
    await callback.answer(f"{TOGGLES[key]}: {'включено 🟢' if new_value else 'выключено 🔴'}")
    text, keyboard = await _settings_view(repo)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("adm:edit:"))
async def cb_edit(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.data:
        await callback.answer()
        return
    key = callback.data.split(":")[2]
    if key not in EDITABLE_SETTINGS:
        await callback.answer()
        return
    await state.set_state(AdminStates.waiting_value)
    await state.update_data(setting_key=key)
    _, prompt = EDITABLE_SETTINGS[key]
    if isinstance(callback.message, Message):
        await callback.message.answer(
            prompt,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data="adm:settings")]]
            ),
        )
    await callback.answer()


@router.message(AdminStates.waiting_value, F.text)
async def receive_value(message: Message, state: FSMContext, repo: Repo) -> None:
    data = await state.get_data()
    key = data.get("setting_key")
    if key not in EDITABLE_SETTINGS:
        await state.clear()
        return
    if not message.text:
        return

    value = message.text.strip()
    if value == "-":
        value = ""
    elif key in INT_SETTINGS:
        if not value.isdigit() or int(value) <= 0:
            await message.answer("Нужно положительное целое число. Попробуй ещё раз:")
            return
    await repo.set_setting(key, value)
    await state.clear()
    text, keyboard = await _settings_view(repo)
    await message.answer("Сохранено ✅")
    await message.answer(text, reply_markup=keyboard)
