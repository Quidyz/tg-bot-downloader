import asyncio
import html
import logging
import shutil

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import BaseFilter
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)
from yt_dlp.utils import DownloadError

from bot.config import Config
from bot.db.repo import Repo
from bot.middlewares.access import AccessMiddleware
from bot.services import url_detector
from bot.services.audio import extract_mp3
from bot.services.downloader import DownloadResult, UnsupportedContentError, download

logger = logging.getLogger(__name__)

router = Router(name="links")
router.message.middleware(AccessMiddleware())

# Долгие загрузки больших файлов на локальный Bot API сервер
UPLOAD_TIMEOUT = 1800

_download_semaphore: asyncio.Semaphore | None = None
_active_users: set[int] = set()


def _semaphore(config: Config) -> asyncio.Semaphore:
    global _download_semaphore
    if _download_semaphore is None:
        _download_semaphore = asyncio.Semaphore(config.max_concurrent_downloads)
    return _download_semaphore


class SupportedLinkFilter(BaseFilter):
    async def __call__(self, message: Message) -> bool | dict:
        text = message.text or message.caption or ""
        detected = url_detector.detect(text)
        if not detected:
            return False
        return {"platform": detected[0], "url": detected[1]}


@router.message(SupportedLinkFilter())
async def handle_link(
    message: Message,
    bot: Bot,
    platform: str,
    url: str,
    repo: Repo,
    config: Config,
    is_privileged: bool = False,
) -> None:
    user_id = message.from_user.id
    if user_id in _active_users:
        await message.reply("⏳ Я ещё качаю твоё предыдущее видео, подожди немного.")
        return

    _active_users.add(user_id)
    status = await message.reply("⏳ Скачиваю…")
    result: DownloadResult | None = None
    try:
        async with _semaphore(config):
            result = await asyncio.to_thread(download, url, platform)

        caption, keyboard = await _build_caption(repo, result, is_privileged)

        if result.video_path:
            await message.reply_video(
                FSInputFile(result.video_path),
                caption=caption,
                reply_markup=keyboard,
                width=result.width,
                height=result.height,
                duration=result.duration,
                supports_streaming=True,
                request_timeout=UPLOAD_TIMEOUT,
            )
            audio_path = await extract_mp3(
                result.video_path, result.temp_dir, result.title, result.uploader
            )
            if audio_path:
                await message.reply_audio(
                    FSInputFile(audio_path),
                    title=result.title or "audio",
                    performer=result.uploader or None,
                    request_timeout=UPLOAD_TIMEOUT,
                )

        if result.image_paths:
            media = [
                InputMediaPhoto(
                    media=FSInputFile(path),
                    caption=caption if i == 0 and not result.video_path else None,
                )
                for i, path in enumerate(result.image_paths[:10])
            ]
            await message.reply_media_group(media, request_timeout=UPLOAD_TIMEOUT)
            # У media group нет кнопок — рекламную кнопку шлём отдельным сообщением
            if keyboard and not result.video_path:
                ad_text = await repo.get_setting("ad_text")
                if ad_text:
                    await message.answer(html.escape(ad_text), reply_markup=keyboard)

        for gif_path in result.gif_paths[:10]:
            await message.reply_animation(
                FSInputFile(gif_path), request_timeout=UPLOAD_TIMEOUT
            )

        await repo.add_download(user_id, platform)
        await _delete_silent(status)
    except UnsupportedContentError:
        await _edit_status(
            status, "😕 Не нашёл в этой ссылке видео или фото, которые можно скачать."
        )
    except DownloadError as e:
        logger.warning("Ошибка скачивания %s: %s", url, e)
        await _edit_status(status, _friendly_error(str(e)))
    except Exception:
        logger.exception("Не удалось обработать %s", url)
        await _edit_status(status, "😔 Не удалось скачать. Попробуй позже.")
    finally:
        _active_users.discard(user_id)
        if result:
            shutil.rmtree(result.temp_dir, ignore_errors=True)


@router.callback_query(F.data == "check_sub")
async def check_sub(callback: CallbackQuery) -> None:
    await callback.answer("Отлично! Теперь отправь ссылку ещё раз 👍", show_alert=True)
    if callback.message:
        await _delete_silent(callback.message)


@router.message(F.chat.type == ChatType.PRIVATE, F.text)
async def hint(message: Message) -> None:
    await message.reply(
        "Не вижу здесь ссылки на TikTok, Instagram, YouTube, Pinterest или X (Twitter) 🤔\n"
        "Отправь мне ссылку на видео или фото — и я его скачаю."
    )


async def _build_caption(
    repo: Repo, result: DownloadResult, is_privileged: bool
) -> tuple[str | None, InlineKeyboardMarkup | None]:
    parts = []
    if result.title:
        parts.append(f"<b>{html.escape(result.title[:800])}</b>")

    keyboard = None
    if not is_privileged and await repo.get_setting_bool("ads_enabled"):
        ad_text = await repo.get_setting("ad_text")
        ad_url = await repo.get_setting("ad_url")
        if ad_text:
            parts.append(f"<i>{html.escape(ad_text)}</i>")
        if ad_url:
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="👉 Подробнее", url=ad_url)]]
            )

    return ("\n\n".join(parts) or None), keyboard


def _friendly_error(error: str) -> str:
    lowered = error.lower()
    if "private" in lowered or "login" in lowered or "rate-limit" in lowered:
        return "🔒 Это видео недоступно: оно приватное или площадка требует входа в аккаунт."
    if "unavailable" in lowered or "removed" in lowered or "404" in lowered:
        return "😕 Видео недоступно — возможно, его удалили."
    if "file is larger" in lowered or "max-filesize" in lowered:
        return "😔 Файл больше 2 ГБ — Telegram не позволит его отправить."
    return "😔 Не удалось скачать. Проверь ссылку или попробуй позже."


async def _edit_status(status: Message, text: str) -> None:
    try:
        await status.edit_text(text)
    except Exception:
        pass


async def _delete_silent(message: Message) -> None:
    try:
        await message.delete()
    except Exception:
        pass
