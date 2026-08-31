import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field

from yt_dlp import YoutubeDL

logger = logging.getLogger(__name__)

# Жёсткий лимит Telegram для локального Bot API — 2 ГБ, берём с запасом
MAX_FILESIZE = 1_900_000_000

VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".avi"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
GIF_EXTS = {".gif"}

# Платформы, где фото-контент yt-dlp не умеет — пробуем gallery-dl
GALLERY_DL_PLATFORMS = {"pinterest", "twitter", "instagram", "tiktok"}


class UnsupportedContentError(Exception):
    """Загрузчики отработали, но ни видео, ни фото не нашлось."""


@dataclass
class DownloadResult:
    temp_dir: str
    video_path: str | None = None
    audio_path: str | None = None
    image_paths: list[str] = field(default_factory=list)
    gif_paths: list[str] = field(default_factory=list)
    title: str = ""
    uploader: str = ""
    duration: int | None = None
    width: int | None = None
    height: int | None = None


@dataclass
class FormatOption:
    height: int
    size: int | None  # байты; None — площадка не сообщила размер


@dataclass
class ProbeResult:
    title: str = ""
    uploader: str = ""
    channel_handle: str = ""
    duration: int | None = None
    view_count: int | None = None
    like_count: int | None = None
    comment_count: int | None = None
    upload_date: str = ""  # YYYYMMDD
    thumbnail: str = ""
    formats: list[FormatOption] = field(default_factory=list)
    audio_size: int | None = None


def probe_youtube(url: str) -> ProbeResult:
    """Получает метаданные и список форматов без скачивания. Блокирующая."""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 3,
    }
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if info is None:
        raise UnsupportedContentError(url)
    entries = info.get("entries")
    if entries:
        entries = [e for e in entries if e]
        if not entries:
            raise UnsupportedContentError(url)
        info = entries[0]

    # Лучшее аудио — для оценки размера MP3 и добавки к video-only форматам
    audio_size = None
    for f in info.get("formats") or []:
        if f.get("vcodec") in (None, "none") and f.get("acodec") not in (None, "none"):
            size = f.get("filesize") or f.get("filesize_approx")
            if size and (audio_size is None or size > audio_size):
                audio_size = int(size)

    # По каждому разрешению — максимальная из известных оценок размера
    by_height: dict[int, int | None] = {}
    for f in info.get("formats") or []:
        height = f.get("height")
        if not height or f.get("vcodec") in (None, "none"):
            continue
        size = f.get("filesize") or f.get("filesize_approx")
        if size:
            size = int(size)
            if f.get("acodec") in (None, "none") and audio_size:
                size += audio_size
        if height not in by_height or (size and (by_height[height] or 0) < size):
            by_height[height] = size

    heights = sorted(by_height, reverse=True)[:9]
    return ProbeResult(
        title=(info.get("title") or "").strip(),
        uploader=(info.get("uploader") or info.get("channel") or "").strip(),
        channel_handle=(info.get("uploader_id") or "").strip(),
        duration=_as_int(info.get("duration")),
        view_count=_as_int(info.get("view_count")),
        like_count=_as_int(info.get("like_count")),
        comment_count=_as_int(info.get("comment_count")),
        upload_date=(info.get("upload_date") or "").strip(),
        thumbnail=(info.get("thumbnail") or "").strip(),
        formats=[FormatOption(h, by_height[h]) for h in heights],
        audio_size=audio_size,
    )


def download_mp3(url: str) -> DownloadResult:
    """Скачивает только аудио и конвертирует в mp3. Блокирующая.

    Вызывающий обязан удалить result.temp_dir после отправки.
    """
    temp_dir = tempfile.mkdtemp(prefix="tgdl_")
    opts = {
        "outtmpl": os.path.join(temp_dir, "%(autonumber)03d.%(ext)s"),
        "format": "bestaudio/best",
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "0",
            }
        ],
        "max_filesize": MAX_FILESIZE,
        "noplaylist": True,
        "quiet": True,
        "noprogress": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "retries": 3,
    }
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception:
        _cleanup_silent(temp_dir)
        raise

    mp3_path = None
    for name in sorted(os.listdir(temp_dir)):
        if name.lower().endswith(".mp3"):
            mp3_path = os.path.join(temp_dir, name)
            break
    if info is None or mp3_path is None:
        _cleanup_silent(temp_dir)
        raise UnsupportedContentError(url)

    return DownloadResult(
        temp_dir=temp_dir,
        audio_path=mp3_path,
        title=(info.get("title") or "").strip(),
        uploader=(info.get("uploader") or "").strip(),
        duration=_as_int(info.get("duration")),
    )


def download(url: str, platform: str = "", max_height: int | None = None) -> DownloadResult:
    """Скачивает медиа в temp-папку. Блокирующая — вызывать через asyncio.to_thread.

    Вызывающий обязан удалить result.temp_dir после отправки.
    """
    temp_dir = tempfile.mkdtemp(prefix="tgdl_")
    try:
        return _download_ytdlp(url, temp_dir, max_height)
    except Exception as ytdlp_error:
        # Фото-пины Pinterest, фото-твиты и т.п.: yt-dlp видит только видео,
        # поэтому при неудаче пробуем gallery-dl (кроме YouTube — там всегда видео).
        if platform not in GALLERY_DL_PLATFORMS:
            _cleanup_silent(temp_dir)
            raise
        _clear_dir(temp_dir)
        result = _download_gallery_dl(url, temp_dir)
        if result is not None:
            return result
        _cleanup_silent(temp_dir)
        raise ytdlp_error


def _download_ytdlp(
    url: str, temp_dir: str, max_height: int | None = None
) -> DownloadResult:
    if max_height:
        fmt = (
            f"bestvideo*[height<={max_height}]+bestaudio"
            f"/best[height<={max_height}]/best"
        )
    else:
        fmt = "bestvideo*+bestaudio/best"
    opts = {
        "outtmpl": os.path.join(temp_dir, "%(autonumber)03d.%(ext)s"),
        # Лучшее разрешение; при равном разрешении предпочитаем h264/aac —
        # такие файлы Telegram стримит без проблем.
        # YouTube: с 2025 нужен JS-рантайм (deno в Docker-образе),
        # без него подписи не расшифровываются и сервер отдаёт 403.
        "format": fmt,
        "format_sort": ["res", "fps", "vcodec:h264", "acodec:aac"],
        "merge_output_format": "mp4",
        "max_filesize": MAX_FILESIZE,
        "noplaylist": True,
        "playlist_items": "1:10",  # галереи/карусели: не больше 10 элементов
        "quiet": True,
        "noprogress": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "retries": 3,
    }
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)

    if info is None:
        raise UnsupportedContentError(url)

    # Для галерей метаданные лежат в первом entry
    meta = info
    entries = info.get("entries")
    if entries:
        entries = [e for e in entries if e]
        if entries:
            meta = entries[0]

    result = DownloadResult(
        temp_dir=temp_dir,
        title=(info.get("title") or meta.get("title") or "").strip(),
        uploader=(info.get("uploader") or meta.get("uploader") or "").strip(),
        duration=_as_int(meta.get("duration")),
        width=_as_int(meta.get("width")),
        height=_as_int(meta.get("height")),
    )
    _collect_files(temp_dir, result)

    if not result.video_path and not result.image_paths and not result.gif_paths:
        raise UnsupportedContentError(url)
    return result


def _download_gallery_dl(url: str, temp_dir: str) -> DownloadResult | None:
    """Fallback для фото/гифок. Возвращает None, если ничего не скачалось."""
    cmd = [
        "gallery-dl",
        "--quiet",
        "--directory", temp_dir,
        "--range", "1-10",
        url,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        logger.warning("gallery-dl не установлен — фоллбэк для фото недоступен")
        return None
    if proc.returncode != 0:
        logger.info("gallery-dl не справился с %s: %s", url, proc.stderr.strip()[:500])

    result = DownloadResult(temp_dir=temp_dir)
    _collect_files(temp_dir, result)
    if not result.video_path and not result.image_paths and not result.gif_paths:
        return None
    return result


def _collect_files(temp_dir: str, result: DownloadResult) -> None:
    videos: list[str] = []
    for root, _dirs, files in os.walk(temp_dir):
        for name in sorted(files):
            path = os.path.join(root, name)
            ext = os.path.splitext(name)[1].lower()
            if ext in VIDEO_EXTS:
                videos.append(path)
            elif ext in IMAGE_EXTS:
                result.image_paths.append(path)
            elif ext in GIF_EXTS:
                result.gif_paths.append(path)

    if videos:
        # Карусель с несколькими видео — берём самое большое (обычно основное)
        result.video_path = max(videos, key=os.path.getsize)


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clear_dir(path: str) -> None:
    for name in os.listdir(path):
        full = os.path.join(path, name)
        if os.path.isdir(full):
            shutil.rmtree(full, ignore_errors=True)
        else:
            try:
                os.remove(full)
            except OSError:
                pass


def _cleanup_silent(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)
