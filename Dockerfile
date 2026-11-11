FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        ffmpeg \
        git \
        unzip \
    && rm -rf /var/lib/apt/lists/*

# Deno нужен yt-dlp для части YouTube-экстракторов.
RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh

WORKDIR /app

# uv устанавливает зависимости строго из pyproject.toml и uv.lock.
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /uvx /usr/local/bin/
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Chromium нужен threads-dl/Playwright.
RUN playwright install --with-deps chromium

COPY bot ./bot
COPY scripts ./scripts

CMD ["python", "-m", "bot.main"]
