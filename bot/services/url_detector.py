import re

# Порядок важен: первая совпавшая платформа выигрывает
PLATFORM_PATTERNS: dict[str, re.Pattern] = {
    "tiktok": re.compile(
        r"https?://(?:www\.|vm\.|vt\.|m\.)?tiktok\.com/\S+", re.IGNORECASE
    ),
    "instagram": re.compile(
        r"https?://(?:www\.)?instagram\.com/(?:[\w.]+/)?(?:reel|reels|p|tv|share|stories)/\S+",
        re.IGNORECASE,
    ),
    "youtube": re.compile(
        r"https?://(?:www\.|m\.|music\.)?(?:youtube\.com/(?:watch\?\S*v=|shorts/|live/)|youtu\.be/)\S+",
        re.IGNORECASE,
    ),
    "pinterest": re.compile(
        r"https?://(?:[a-z]{2,3}\.)?(?:pinterest\.[a-z.]{2,6}|pin\.it)/\S+",
        re.IGNORECASE,
    ),
    "twitter": re.compile(
        r"https?://(?:www\.|mobile\.)?(?:twitter\.com|x\.com)/(?:i/(?:web/)?status|\w{1,15}/status(?:es)?)/\d+\S*",
        re.IGNORECASE,
    ),
    "threads": re.compile(
        r"https?://(?:www\.)?threads\.(?:net|com)/(?:@[\w.]+/post/[\w-]+|t/[\w-]+|share/[\w-]+)",
        re.IGNORECASE,
    ),
}


def detect(text: str) -> tuple[str, str] | None:
    """Возвращает (платформа, url) для первой поддерживаемой ссылки в тексте."""
    for platform, pattern in PLATFORM_PATTERNS.items():
        match = pattern.search(text)
        if match:
            return platform, match.group(0).rstrip(").,!»\"'")
    return None
