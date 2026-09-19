import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.enums import ChatMemberStatus
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
)

logger = logging.getLogger(__name__)


class UserTrackMiddleware(BaseMiddleware):
    """Регистрирует/обновляет пользователя в БД на каждом событии (учёт посещений)."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is not None and not user.is_bot:
            repo = data["repo"]
            db_user = await repo.upsert_user(user.id, user.username, user.first_name)
            data["db_user"] = db_user
        return await handler(event, data)


class AccessMiddleware(BaseMiddleware):
    """Проверки монетизации перед скачиванием.

    Вешается обычным middleware на роутер ссылок: срабатывает только когда
    фильтр уже распознал поддерживаемую ссылку (в data есть "platform").
    Белый список / Premium / админ проходят без ограничений.
    """

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        if "platform" not in data:
            return await handler(event, data)

        repo = data["repo"]
        config = data["config"]
        db_user = data.get("db_user")
        user_id = event.from_user.id

        privileged = (
            user_id in config.admin_id_list
            or (db_user and (db_user["is_admin"] or db_user["is_whitelisted"]))
            or repo.is_premium(db_user)
        )
        data["is_privileged"] = bool(privileged)
        if privileged:
            return await handler(event, data)

        # Обязательная подписка на канал
        if await repo.get_setting_bool("channel_sub_enabled"):
            channel_id = await repo.get_setting("channel_id")
            if channel_id and not await self._is_subscribed(data["bot"], channel_id, user_id):
                await self._ask_to_subscribe(event, repo, channel_id)
                return None

        # Дневной лимит
        if await repo.get_setting_bool("limit_enabled"):
            daily_limit = await repo.get_setting_int("daily_limit")
            used = await repo.count_downloads_today(user_id)
            if used >= daily_limit:
                await self._offer_premium(event, repo, daily_limit)
                return None

        return await handler(event, data)

    @staticmethod
    async def _is_subscribed(bot, channel_id: str, user_id: int) -> bool:
        try:
            member = await bot.get_chat_member(channel_id, user_id)
        except Exception as e:  # noqa: BLE001 - aiogram raises many error types here
            # Канал недоступен боту (бот не админ канала и т.п.) — не блокируем людей
            logger.warning("Не удалось проверить подписку на %s: %s", channel_id, e)
            return True
        return member.status not in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED)

    @staticmethod
    async def _ask_to_subscribe(message: Message, repo, channel_id: str) -> None:
        channel_url = await repo.get_setting("channel_url")
        if not channel_url and channel_id.startswith("@"):
            channel_url = f"https://t.me/{channel_id.lstrip('@')}"
        buttons = []
        if channel_url:
            buttons.append([InlineKeyboardButton(text="📢 Подписаться", url=channel_url)])
        buttons.append(
            [InlineKeyboardButton(text="✅ Я подписался", callback_data="check_sub")]
        )
        await message.reply(
            "Чтобы пользоваться ботом, подпишись на наш канал 🙌\n"
            "После подписки нажми «Я подписался» и отправь ссылку ещё раз.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )

    @staticmethod
    async def _offer_premium(message: Message, repo, daily_limit: int) -> None:
        price = await repo.get_setting_int("premium_price_stars")
        days = await repo.get_setting_int("premium_days")
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"⭐ Premium за {price} Stars", callback_data="buy_premium"
                    )
                ]
            ]
        )
        await message.reply(
            f"Лимит на сегодня исчерпан ({daily_limit} скачиваний в день) 😔\n\n"
            f"⭐ <b>Premium</b> на {days} дней снимает лимит и убирает рекламу.\n"
            "Или возвращайся завтра — лимит обновится!",
            reply_markup=keyboard,
        )
