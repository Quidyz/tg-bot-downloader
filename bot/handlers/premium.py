import logging
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery

from bot.db.repo import Repo

logger = logging.getLogger(__name__)

router = Router(name="premium")

PREMIUM_PAYLOAD = "premium_subscription"


async def _send_invoice(message: Message, repo: Repo) -> None:
    price = await repo.get_setting_int("premium_price_stars")
    days = await repo.get_setting_int("premium_days")
    await message.answer_invoice(
        title="⭐ Premium",
        description=(
            f"Premium на {days} дней: безлимитные скачивания, без рекламы "
            "и без обязательной подписки на канал."
        ),
        payload=PREMIUM_PAYLOAD,
        currency="XTR",
        prices=[LabeledPrice(label=f"Premium на {days} дней", amount=price)],
    )


@router.message(Command("premium"))
async def cmd_premium(message: Message, repo: Repo, db_user=None) -> None:
    if repo.is_premium(db_user):
        until = datetime.fromisoformat(db_user["premium_until"]).strftime("%d.%m.%Y")
        await message.answer(
            f"⭐ У тебя уже есть Premium до {until}.\n"
            "Можно продлить — оплата добавит дни к текущей подписке."
        )
    await _send_invoice(message, repo)


@router.callback_query(F.data == "buy_premium")
async def buy_premium(callback: CallbackQuery, repo: Repo) -> None:
    await callback.answer()
    if callback.message:
        await _send_invoice(callback.message, repo)


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery) -> None:
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def successful_payment(message: Message, repo: Repo) -> None:
    payment = message.successful_payment
    days = await repo.get_setting_int("premium_days")
    until = await repo.grant_premium(message.from_user.id, days)
    logger.info(
        "Оплата Premium: user=%s, сумма=%s XTR, charge_id=%s",
        message.from_user.id,
        payment.total_amount,
        payment.telegram_payment_charge_id,
    )
    await message.answer(
        f"🎉 Premium активирован до <b>{until.strftime('%d.%m.%Y')}</b>!\n"
        "Безлимитные скачивания без рекламы. Приятного пользования!"
    )
