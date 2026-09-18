FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl unzip ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

# Deno — JS-рантайм для yt-dlp: без него YouTube не расшифровывает
# ссылки на видео и отдаёт HTTP 403
RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh

WORKDIR /app

# Playwright + Chromium — тяжёлый слой (100+ МБ). Ставим его ЗДЕСЬ, раньше и
# отдельно от requirements.txt, чтобы обновление yt-dlp/gallery-dl/threads-dl
# ниже не заставляло каждый раз перекачивать браузер заново.
RUN pip install --no-cache-dir playwright \
    && playwright install --with-deps chromium

COPY requirements.txt .
# yt-dlp, gallery-dl и threads-dl (git-зависимость в requirements.txt) часто
# обновляются отдельно от этого файла — Docker не видит новых коммитов по
# неизменившемуся requirements.txt и переиспользует старый закэшированный слой.
# ARG ниже форсит пересборку ТОЛЬКО этого слоя (и Chromium выше не трогает):
#   docker compose build --build-arg CACHEBUST=$(date +%s) bot
ARG CACHEBUST=1
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir -U yt-dlp gallery-dl

COPY bot ./bot

CMD ["python", "-m", "bot.main"]
