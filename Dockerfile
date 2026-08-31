FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl unzip ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Deno — JS-рантайм для yt-dlp: без него YouTube не расшифровывает
# ссылки на видео и отдаёт HTTP 403
RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh

WORKDIR /app

COPY requirements.txt .
# yt-dlp и gallery-dl ставим без пина: платформы часто ломают экстракторы,
# при проблемах со скачиванием пересобери образ (docker compose build --no-cache bot)
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir -U yt-dlp gallery-dl

COPY bot ./bot

CMD ["python", "-m", "bot.main"]
