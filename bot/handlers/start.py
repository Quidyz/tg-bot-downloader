from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

router = Router(name="start")

START_TEXT = (
    "Привет! Это бот для скачивания видео/фото/аудио из популярных социальных сетей.\n"
    "\n"
    "💻 Как пользоваться:\n"
    "1. Зайди в одну из социальных сетей.\n"
    "2. Выбери интересное видео/фото.\n"
    "3. Нажми кнопку «Скопировать ссылку».\n"
    "4. Отправь ссылку боту и получи скаченный файл!\n"
    "\n"
    "🔗 Бот может скачивать из:\n"
    "• TikTok\n"
    "• Instagram\n"
    "• YouTube\n"
    "• Pinterest"
)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(START_TEXT)
