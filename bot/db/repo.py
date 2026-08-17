from datetime import datetime, timedelta, timezone

import aiosqlite

# Настройки монетизации, редактируются из /admin без перезапуска
DEFAULT_SETTINGS = {
    "limit_enabled": "1",
    "daily_limit": "10",
    "channel_sub_enabled": "0",
    "channel_id": "",       # @username канала или -100...
    "channel_url": "",      # ссылка для кнопки «Подписаться»
    "ads_enabled": "0",
    "ad_text": "",
    "ad_url": "",
    "premium_price_stars": "100",
    "premium_days": "30",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_start() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


class Repo:
    def __init__(self, conn: aiosqlite.Connection):
        self.conn = conn

    # ---------- users ----------

    async def upsert_user(
        self, user_id: int, username: str | None, first_name: str | None
    ) -> aiosqlite.Row:
        now = _now()
        await self.conn.execute(
            """
            INSERT INTO users (id, username, first_name, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                last_seen = excluded.last_seen
            """,
            (user_id, username, first_name, now, now),
        )
        await self.conn.commit()
        return await self.get_user(user_id)

    async def get_user(self, user_id: int) -> aiosqlite.Row | None:
        cur = await self.conn.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        return await cur.fetchone()

    async def find_user_by_username(self, username: str) -> aiosqlite.Row | None:
        cur = await self.conn.execute(
            "SELECT * FROM users WHERE lower(username) = lower(?)",
            (username.lstrip("@"),),
        )
        return await cur.fetchone()

    async def set_whitelist(self, user_id: int, value: bool) -> None:
        await self.conn.execute(
            "UPDATE users SET is_whitelisted = ? WHERE id = ?", (int(value), user_id)
        )
        await self.conn.commit()

    async def grant_premium(self, user_id: int, days: int) -> datetime:
        user = await self.get_user(user_id)
        now = datetime.now(timezone.utc)
        base = now
        if user and user["premium_until"]:
            current = datetime.fromisoformat(user["premium_until"])
            if current > now:
                base = current
        until = base + timedelta(days=days)
        await self.conn.execute(
            "UPDATE users SET premium_until = ? WHERE id = ?",
            (until.isoformat(), user_id),
        )
        await self.conn.commit()
        return until

    async def revoke_premium(self, user_id: int) -> None:
        await self.conn.execute(
            "UPDATE users SET premium_until = NULL WHERE id = ?", (user_id,)
        )
        await self.conn.commit()

    @staticmethod
    def is_premium(user: aiosqlite.Row | None) -> bool:
        if not user or not user["premium_until"]:
            return False
        return datetime.fromisoformat(user["premium_until"]) > datetime.now(timezone.utc)

    # ---------- downloads ----------

    async def add_download(self, user_id: int, platform: str) -> None:
        await self.conn.execute(
            "INSERT INTO downloads (user_id, platform, created_at) VALUES (?, ?, ?)",
            (user_id, platform, _now()),
        )
        await self.conn.commit()

    async def count_downloads_today(self, user_id: int) -> int:
        cur = await self.conn.execute(
            "SELECT COUNT(*) FROM downloads WHERE user_id = ? AND created_at >= ?",
            (user_id, _today_start()),
        )
        row = await cur.fetchone()
        return row[0]

    # ---------- stats ----------

    async def stats(self) -> dict:
        async def one(sql: str, params: tuple = ()) -> int:
            cur = await self.conn.execute(sql, params)
            row = await cur.fetchone()
            return row[0]

        today = _today_start()
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        now = _now()

        by_platform_cur = await self.conn.execute(
            "SELECT platform, COUNT(*) AS cnt FROM downloads GROUP BY platform ORDER BY cnt DESC"
        )
        by_platform = {row["platform"]: row["cnt"] for row in await by_platform_cur.fetchall()}

        return {
            "users_total": await one("SELECT COUNT(*) FROM users"),
            "users_new_today": await one(
                "SELECT COUNT(*) FROM users WHERE first_seen >= ?", (today,)
            ),
            "users_active_today": await one(
                "SELECT COUNT(*) FROM users WHERE last_seen >= ?", (today,)
            ),
            "downloads_today": await one(
                "SELECT COUNT(*) FROM downloads WHERE created_at >= ?", (today,)
            ),
            "downloads_week": await one(
                "SELECT COUNT(*) FROM downloads WHERE created_at >= ?", (week_ago,)
            ),
            "downloads_total": await one("SELECT COUNT(*) FROM downloads"),
            "premium_active": await one(
                "SELECT COUNT(*) FROM users WHERE premium_until > ?", (now,)
            ),
            "whitelisted": await one(
                "SELECT COUNT(*) FROM users WHERE is_whitelisted = 1"
            ),
            "by_platform": by_platform,
        }

    # ---------- settings ----------

    async def ensure_default_settings(self) -> None:
        for key, value in DEFAULT_SETTINGS.items():
            await self.conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value)
            )
        await self.conn.commit()

    async def get_setting(self, key: str) -> str:
        cur = await self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = await cur.fetchone()
        return row[0] if row else DEFAULT_SETTINGS.get(key, "")

    async def get_setting_bool(self, key: str) -> bool:
        return (await self.get_setting(key)) == "1"

    async def get_setting_int(self, key: str) -> int:
        try:
            return int(await self.get_setting(key))
        except ValueError:
            return int(DEFAULT_SETTINGS.get(key, "0"))

    async def set_setting(self, key: str, value: str) -> None:
        await self.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await self.conn.commit()

    async def toggle_setting(self, key: str) -> bool:
        new_value = not await self.get_setting_bool(key)
        await self.set_setting(key, "1" if new_value else "0")
        return new_value
