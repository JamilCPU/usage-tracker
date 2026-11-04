from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    USAGE_READER_DSN: str
    USAGE_OWNER_DSN: str | None = None
    USAGE_TZ: str = "America/Chicago"
    SESSION_SECRET: str
    PUBLIC_URL: str = "https://usage.lab"
    OAUTH_ISSUER: str = "https://gateway.lab"
    OAUTH_CLIENT_ID: str | None = None
    OAUTH_SCOPE: str | None = None
    OAUTH_CA_FILE: str | None = None
    AUTH_MODE: Literal["oauth", "none"] = "oauth"
    MIGRATIONS_DIR: str = "/app/migrations"
    PORT: int = 8080
