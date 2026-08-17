FROM python:3.12-slim

RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
# yt-dlp ставим без пина: платформы часто ломают экстракторы,
# при проблемах со скачиванием пересобери образ (docker compose build --no-cache bot)
RUN pip install --no-cache-dir -r requirements.txt && pip install --no-cache-dir -U yt-dlp

COPY bot ./bot

CMD ["python", "-m", "bot.main"]
