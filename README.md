# Telegram-бот для скачивания медиа

Бот скачивает видео/фото/гиф/аудио из **TikTok, Instagram, YouTube, Pinterest, X (Twitter)**.
Отправь боту ссылку — он ответит реплаем: видео в лучшем качестве + отдельно mp3.
Файлы до **2 ГБ** (через локальный сервер Telegram Bot API).

Встроенная монетизация (всё включается/выключается из /admin без перезапуска):
- дневной лимит скачиваний + **Premium** с оплатой Telegram Stars;
- обязательная подписка на канал;
- рекламные вставки к скачанным файлам;
- белый список — полный обход ограничений для своих;
- статистика пользователей и скачиваний.

## Подготовка и запуск

1. Создай бота у [@BotFather](https://t.me/BotFather) и получи `BOT_TOKEN`.
2. На [my.telegram.org](https://my.telegram.org) создай приложение и получи
   `TELEGRAM_API_ID`/`TELEGRAM_API_HASH`.
3. Узнай свой Telegram ID у [@userinfobot](https://t.me/userinfobot).
4. Создай `.env` из `.env.example` и заполни секреты:

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

В `.env` нужны как минимум:

```dotenv
BOT_TOKEN=токен_от_BotFather
ADMIN_IDS=твой_telegram_id
TELEGRAM_API_ID=api_id_с_my.telegram.org
TELEGRAM_API_HASH=api_hash_с_my.telegram.org
TELEGRAM_API_BASE=
DB_PATH=data/bot.db
MAX_CONCURRENT_DOWNLOADS=3
```

### Установка Docker на Debian 12

```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
```

После добавления пользователя в группу Docker перелогинься по SSH или выполни
`newgrp docker`.

Перед первым запуском локального Bot API отвяжи бота от облачного API:

```bash
set -a
. ./.env
set +a
curl -fsS "https://api.telegram.org/bot${BOT_TOKEN}/logOut"
unset BOT_TOKEN TELEGRAM_API_HASH
```

Запусти приложение:

```bash
docker-compose up -d --build
```

Проверь состояние и логи:

```bash
docker-compose ps
docker-compose logs --tail=100 bot
docker-compose logs --tail=100 telegram-bot-api
```

### Автозапуск через systemd

Создай unit-файл:

```bash
sudo tee /etc/systemd/system/tg-bot-downloader.service >/dev/null <<'EOF'
[Unit]
Description=Telegram media downloader bot
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/path/to/your/project
RemainAfterExit=yes
ExecStart=/usr/bin/docker-compose up -d --build
ExecStop=/usr/bin/docker-compose down
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
EOF
```

Включи автозапуск:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now tg-bot-downloader.service
sudo systemctl status tg-bot-downloader.service --no-pager
docker-compose ps
```

После перезагрузки systemd запустит Compose после Docker:

```bash
sudo reboot
```

Проверка после подключения:

```bash
systemctl status tg-bot-downloader.service --no-pager
journalctl -u tg-bot-downloader.service -b --no-pager
docker-compose logs --tail=100 bot
```

Если контейнер приложения упал, его перезапустит политика
`restart: unless-stopped`.

Секреты не отправляй в чат и не коммить в Git. При утечке `BOT_TOKEN`
отзови его через @BotFather и выпусти новый.

## Запуск без Docker (для разработки)

Нужны Python 3.13+, uv, ffmpeg и deno в PATH (deno нужен для YouTube).
Зависимости устанавливаются по lock-файлу:

```bash
uv sync
uv run python -m bot.main
```

Без локального Bot API сервера Telegram ограничивает отправку файлов 50 МБ.

## Первичная настройка

1. Напиши боту `/start` — проверка, что он жив.
2. `/admin` (доступно ID из `ADMIN_IDS`):
   - **⚙️ Механики** — включи нужное: дневной лимит (по умолчанию 10/день),
     подписку на канал (укажи канал и сделай бота его админом), рекламу
     (задай текст и ссылку); там же цена и длительность Premium;
   - **👤 Выдать доступ** — добавь себя и друзей в белый список:
     без лимитов, рекламы и обязательных подписок.
3. `/premium` — покупка Premium пользователями (Telegram Stars).

## Если перестало скачивать

Соцсети регулярно ломают загрузчики — помогает обновление yt-dlp:

```bash
docker-compose build --no-cache bot && docker-compose up -d
```

## Известные ограничения

- Instagram: только публичный контент; при большом трафике Instagram может
  временно троттлить анонимные запросы с IP сервера.
- Файлы больше 2 ГБ Telegram отправить не позволяет.
