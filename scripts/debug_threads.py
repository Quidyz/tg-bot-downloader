"""Диагностика: сохраняет скриншот и текст страницы Threads для конкретного URL.

Запуск внутри контейнера бота:
    docker-compose exec bot python scripts/debug_threads.py "https://www.threads.com/share/BAWaaKWm6U/"

Файлы сохранятся в /app/data (примонтирован на хосте в ./data), заберёшь их оттуда.
"""

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright


async def main(url: str) -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--disable-gpu", "--no-sandbox"])
        page = await browser.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)

        await page.screenshot(path="/app/data/threads_debug.png", full_page=True)
        html = await page.content()
        await asyncio.to_thread(Path("/app/data/threads_debug.html").write_text, html, encoding="utf-8")
        text = await page.evaluate("() => document.body.innerText")
        await asyncio.to_thread(Path("/app/data/threads_debug.txt").write_text, text, encoding="utf-8")

        print("final url:", page.url)
        print("body text (first 1500 chars):")
        print(text[:1500])

        await browser.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <threads-url>")
        raise SystemExit(1)
    asyncio.run(main(sys.argv[1]))
