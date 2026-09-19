from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
BANNER_PATH = ASSETS / "banner.png"
TEMPLATES = ROOT / "templates"
STATIC = ROOT / "static"
DATA_DIR = ROOT / "data"


class Settings(BaseSettings):
    """Runtime configuration loaded from environment / `.env`."""

    model_config = SettingsConfigDict(
        env_file=str(ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    bot_token: str = ""
    admin_ids: str = "8058806494"
    admin_password: str = "admin"
    panel_secret: str = "dev-secret-change-me"
    panel_host: str = "127.0.0.1"
    panel_port: int = 8080
    bot_username: str = ""
    public_panel_url: str = "http://127.0.0.1:8080"

    database_url: str = "sqlite+aiosqlite:///./data/bot.db"

    ton_api_base: str = "https://tonapi.io"
    ton_api_key: str = ""
    toncenter_url: str = "https://toncenter.com/api/v2"
    toncenter_api_key: str = ""
    getgems_api: str = "https://api.getgems.io/graphql"

    monitor_chat_id: int = 0
    monitor_topic_id: int = 0
    deals_chat_id: int = 0
    deals_topic_id: int = 0
    payouts_chat_id: int = 0
    payouts_topic_id: int = 0

    marketplace_secret: str = ""
    # Bothost отдаёт публичный домен на порт из PORT, если включён веб-интерфейс.
    api_port: int = Field(default=0, validation_alias=AliasChoices("api_port", "port"))
    default_payout_rate: float = 70.0
    min_withdraw: float = 0.1

    ton_wallet_mnemonic: str = ""
    ton_wallet_version: str = "auto"
    ton_auto_withdraw: bool = True

    @field_validator("bot_username")
    @classmethod
    def strip_at(cls, value: str) -> str:
        return value.lstrip("@").strip()

    @property
    def admin_id_set(self) -> set[int]:
        result: set[int] = {OWNER_ID}
        for chunk in self.admin_ids.split(","):
            item = chunk.strip()
            if item.lstrip("-").isdigit():
                result.add(int(item))
        return result

    @property
    def sqlalchemy_url(self) -> str:
        url = self.database_url.strip()
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+asyncpg://", 1)
        if url.startswith("sqlite://") and "+aiosqlite" not in url:
            return url.replace("sqlite://", "sqlite+aiosqlite://", 1)
        return url

    def is_sqlite(self) -> bool:
        return self.sqlalchemy_url.startswith("sqlite")


OWNER_ID = 8058806494

settings = Settings()

# Backward-compatible module aliases used across the app.
BOT_TOKEN = settings.bot_token
ADMIN_IDS = settings.admin_id_set
ADMIN_PASSWORD = settings.admin_password
PANEL_SECRET = settings.panel_secret
PANEL_HOST = settings.panel_host
PANEL_PORT = settings.panel_port
BOT_USERNAME = settings.bot_username
DB_PATH = DATA_DIR / "bot.db"


def is_admin(user_id: int) -> bool:
    return user_id in settings.admin_id_set


def as_dict() -> dict[str, Any]:
    return settings.model_dump()
