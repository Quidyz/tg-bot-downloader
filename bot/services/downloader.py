import os
import tempfile
from dataclasses import dataclass, field

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

# Жёсткий лимит Telegram для локального Bot API — 2 ГБ, берём с запасом
MAX_FILESIZE = 1_900_000_000

VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".avi"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


class UnsupportedContentError(Exception):
    """yt-dlp отработал, но ни видео, ни фото не нашлось."""


@dataclass
class DownloadResult:
    temp_dir: str
    video_path: str | None = None
    image_paths: list[str] = field(default_factory=list)
    title: str = ""
    uploader: str = ""
    duration: int | None = None
    width: int | None = None
    height: int | None = None


def download(url: str) -> DownloadResult:
    """Скачивает медиа в temp-папку. Блокирующая — вызывать через asyncio.to_thread.

    Вызывающий обязан удалить result.temp_dir после отправки.
    """
    temp_dir = tempfile.mkdtemp(prefix="tgdl_")
    opts = {
        "outtmpl": os.path.join(temp_dir, "%(autonumber)03d.%(ext)s"),
        # Лучшее разрешение; при равном разрешении предпочитаем h264/aac —
        # такие файлы Telegram стримит без проблем
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
        # YouTube: используем old player API, пропускаем dash/hls
        "extractor_args": {
            "youtube": [
                "player_client=web",
                "skip=dash,hls",
            ]
        },
    }
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception:
        _cleanup_silent(temp_dir)
        raise

    if info is None:
        _cleanup_silent(temp_dir)
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

    videos: list[str] = []
    for name in sorted(os.listdir(temp_dir)):
        path = os.path.join(temp_dir, name)
        ext = os.path.splitext(name)[1].lower()
        if ext in VIDEO_EXTS:
            videos.append(path)
        elif ext in IMAGE_EXTS:
            result.image_paths.append(path)

    if videos:
        # Карусель с несколькими видео — берём самое большое (обычно основное)
        result.video_path = max(videos, key=os.path.getsize)

    if not result.video_path and not result.image_paths:
        _cleanup_silent(temp_dir)
        raise UnsupportedContentError(url)

    return result


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _cleanup_silent(path: str) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)
