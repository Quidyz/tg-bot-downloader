from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    bot_token: str
    admin_ids: str = ""
    telegram_api_base: str = ""
    db_path: str = "data/bot.db"
    max_concurrent_downloads: int = 3

    @property
    def admin_id_list(self) -> list[int]:
        return [int(x) for x in self.admin_ids.replace(" ", "").split(",") if x]
