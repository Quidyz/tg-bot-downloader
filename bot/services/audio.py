import asyncio
import logging
import os

logger = logging.getLogger(__name__)


async def extract_mp3(
    video_path: str, out_dir: str, title: str = "", performer: str = ""
) -> str | None:
    """Извлекает аудиодорожку в mp3. Возвращает None, если дорожки нет или ffmpeg упал."""
    out_path = os.path.join(out_dir, "audio.mp3")
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vn", "-codec:a", "libmp3lame", "-q:a", "2",
    ]
    if title:
        cmd += ["-metadata", f"title={title}"]
    if performer:
        cmd += ["-metadata", f"artist={performer}"]
    cmd.append(out_path)

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()

    if process.returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        logger.warning(
            "ffmpeg не смог извлечь аудио из %s: %s",
            video_path,
            (stderr or b"")[-500:].decode(errors="replace"),
        )
        return None
    return out_path
