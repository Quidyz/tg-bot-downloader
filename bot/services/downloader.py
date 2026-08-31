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
    image_paths: list[str] = field(default_factory=list)
    gif_paths: list[str] = field(default_factory=list)
    title: str = ""
    uploader: str = ""
    duration: int | None = None
    width: int | None = None
    height: int | None = None


def download(url: str, platform: str = "") -> DownloadResult:
    """Скачивает медиа в temp-папку. Блокирующая — вызывать через asyncio.to_thread.

    Вызывающий обязан удалить result.temp_dir после отправки.
    """
    temp_dir = tempfile.mkdtemp(prefix="tgdl_")
    try:
        return _download_ytdlp(url, temp_dir)
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


def _download_ytdlp(url: str, temp_dir: str) -> DownloadResult:
    opts = {
        "outtmpl": os.path.join(temp_dir, "%(autonumber)03d.%(ext)s"),
        # Лучшее разрешение; при равном разрешении предпочитаем h264/aac —
        # такие файлы Telegram стримит без проблем.
        # YouTube: с 2025 нужен JS-рантайм (deno в Docker-образе),
        # без него подписи не расшифровываются и сервер отдаёт 403.
        "format": "bestvideo*+bestaudio/best",
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
