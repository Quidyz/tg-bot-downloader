import asyncio
import html
import logging
import secrets
import shutil
import time

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import BaseFilter
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    MediaUnion,
    Message,
)
from yt_dlp.utils import DownloadError

from bot.config import Config
from bot.db.repo import Repo
from bot.middlewares.access import AccessMiddleware
from bot.services import url_detector
from bot.services.audio import extract_mp3
from bot.services.downloader import (
    DownloadResult,
    ProbeResult,
    UnsupportedContentError,
    download,
    download_mp3,
    probe_youtube,
)

logger = logging.getLogger(__name__)

router = Router(name="links")
router.message.middleware(AccessMiddleware())

# Долгие загрузки больших файлов на локальный Bot API сервер
UPLOAD_TIMEOUT = 1800

_download_semaphore: asyncio.Semaphore | None = None
_active_users: set[int] = set()

# Ожидающие выбора качества YouTube-запросы: token -> данные запроса.
# Callback data ограничена 64 байтами, поэтому URL храним в памяти по токену.
_YT_PENDING: dict[str, dict] = {}
_YT_TTL = 6 * 3600  # меню живёт 6 часов


def _yt_remember(entry: dict) -> str:
    now = time.time()
    for key in [k for k, v in _YT_PENDING.items() if now - v["ts"] > _YT_TTL]:
        _YT_PENDING.pop(key, None)
    token = secrets.token_urlsafe(6)
    entry["ts"] = now
    _YT_PENDING[token] = entry
    return token


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
    if message.from_user is None:
        return
    user_id = message.from_user.id
    if user_id in _active_users:
        await message.reply("⏳ Я ещё качаю твоё предыдущее видео, подожди немного.")
        return

    # YouTube: сначала показываем карточку с выбором качества
    if platform == "youtube":
        await _youtube_menu(message, url, is_privileged)
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
            media: list[MediaUnion] = [
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


async def _youtube_menu(message: Message, url: str, is_privileged: bool) -> None:
    if message.from_user is None:
        return
    status = await message.reply("⏳ Получаю информацию о видео…")
    try:
        probe = await asyncio.to_thread(probe_youtube, url)
    except Exception as e:
        logger.warning("Не удалось получить информацию о %s: %s", url, e)
        await _edit_status(status, "😔 Не удалось получить информацию о видео. Попробуй позже.")
        return

    token = _yt_remember(
        {
            "url": url,
            "user_id": message.from_user.id,
            "message": message,
            "is_privileged": is_privileged,
            "title": probe.title,
            "uploader": probe.uploader,
        }
    )
    text = _yt_card_text(probe)
    keyboard = _yt_keyboard(token, probe)

    sent = False
    if probe.thumbnail:
        try:
            await message.reply_photo(probe.thumbnail, caption=text, reply_markup=keyboard)
            sent = True
        except Exception:
            pass
    if not sent:
        await message.reply(text, reply_markup=keyboard)
    await _delete_silent(status)


def _yt_card_text(probe: ProbeResult) -> str:
    lines = [f"📺 <b>{html.escape(probe.title[:300])}</b>"]

    author = html.escape(probe.uploader)
    if probe.channel_handle:
        author = f"{author} ({html.escape(probe.channel_handle)})" if author else html.escape(probe.channel_handle)
    if author:
        lines.append(f"👤 {author}")
    if probe.duration:
        lines.append(f"⏱ {_fmt_duration(probe.duration)}")

    stats = []
    if probe.view_count is not None:
        stats.append(f"👁 {_fmt_num(probe.view_count)}")
    if probe.like_count is not None:
        stats.append(f"👍 {_fmt_num(probe.like_count)}")
    if probe.comment_count is not None:
        stats.append(f"💬 {_fmt_num(probe.comment_count)}")
    if stats:
        lines.append(" | ".join(stats))
    if len(probe.upload_date) == 8:
        d = probe.upload_date
        lines.append(f"📅 {d[6:]}.{d[4:6]}.{d[:4]}")

    lines.append("")
    for fmt in probe.formats:
        lines.append(f"✅ {fmt.height}p — {_fmt_size(fmt.size)}")
    if probe.audio_size:
        lines.append(f"✅ MP3 — ~{_fmt_size(probe.audio_size)}")

    lines.append("")
    lines.append("Форматы для скачивания ⤵️")
    return "\n".join(lines)


def _yt_keyboard(token: str, probe: ProbeResult) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=f"📺 {fmt.height}p", callback_data=f"yt:{token}:{fmt.height}")
        for fmt in probe.formats
    ]
    rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
    rows.append([InlineKeyboardButton(text="🎙 MP3", callback_data=f"yt:{token}:mp3")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("yt:"))
async def yt_pick(callback: CallbackQuery, repo: Repo, config: Config) -> None:
    if callback.data is None:
        await callback.answer()
        return
    try:
        _, token, choice = callback.data.split(":", 2)
    except ValueError:
        await callback.answer()
        return

    pending = _YT_PENDING.get(token)
    if not pending:
        await callback.answer("Меню устарело — отправь ссылку ещё раз 🙏", show_alert=True)
        return
    if callback.from_user.id != pending["user_id"]:
        await callback.answer("Это меню для другого пользователя 😉", show_alert=True)
        return

    user_id = callback.from_user.id
    if user_id in _active_users:
        await callback.answer("⏳ Я ещё качаю предыдущий файл, подожди.", show_alert=True)
        return

    await callback.answer()
    orig: Message = pending["message"]
    url: str = pending["url"]
    label = "MP3" if choice == "mp3" else f"{choice}p"

    _active_users.add(user_id)
    status = await orig.reply(f"⏳ Скачиваю {label}…")
    result: DownloadResult | None = None
    try:
        async with _semaphore(config):
            if choice == "mp3":
                result = await asyncio.to_thread(download_mp3, url)
            else:
                result = await asyncio.to_thread(download, url, "youtube", int(choice))

        caption, keyboard = await _build_caption(repo, result, pending["is_privileged"])

        if choice == "mp3" and result.audio_path:
            await orig.reply_audio(
                FSInputFile(result.audio_path),
                caption=caption,
                reply_markup=keyboard,
                title=result.title or pending["title"] or "audio",
                performer=result.uploader or pending["uploader"] or None,
                duration=result.duration,
                request_timeout=UPLOAD_TIMEOUT,
            )
        elif result.video_path:
            await orig.reply_video(
                FSInputFile(result.video_path),
                caption=caption,
                reply_markup=keyboard,
                width=result.width,
                height=result.height,
                duration=result.duration,
                supports_streaming=True,
                request_timeout=UPLOAD_TIMEOUT,
            )
        else:
            raise UnsupportedContentError(url)

        await repo.add_download(user_id, "youtube")
        await _delete_silent(status)
    except UnsupportedContentError:
        await _edit_status(status, "😕 Не нашёл, что скачать в этом формате.")
    except DownloadError as e:
        logger.warning("Ошибка скачивания %s (%s): %s", url, label, e)
        await _edit_status(status, _friendly_error(str(e)))
    except Exception:
        logger.exception("Не удалось обработать %s (%s)", url, label)
        await _edit_status(status, "😔 Не удалось скачать. Попробуй позже.")
    finally:
        _active_users.discard(user_id)
        if result:
            shutil.rmtree(result.temp_dir, ignore_errors=True)


def _fmt_size(size: int | None) -> str:
    if not size:
        return "?"
    if size >= 1024**3:
        return f"{size / 1024**3:.1f} ГБ"
    return f"{size / 1024**2:.1f} МБ"


def _fmt_duration(seconds: int) -> str:
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _fmt_num(value: int) -> str:
    return f"{value:,}".replace(",", " ")


@router.callback_query(F.data == "check_sub")
async def check_sub(callback: CallbackQuery) -> None:
    await callback.answer("Отлично! Теперь отправь ссылку ещё раз 👍", show_alert=True)
    if isinstance(callback.message, Message):
        await _delete_silent(callback.message)


@router.message(F.chat.type == ChatType.PRIVATE, F.text)
async def hint(message: Message) -> None:
    await message.reply(
        "Не вижу здесь ссылки на TikTok, Instagram, YouTube, Pinterest, X (Twitter) или Threads 🤔\n"
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
