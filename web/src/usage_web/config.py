from typing import Literal

from pydantic import model_validator
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
    # Server-side calls to the issuer connect here (Host/SNI stay the issuer's); in-cluster Traefik.
    OAUTH_CONNECT_HOST: str | None = None
    AUTH_MODE: Literal["oauth", "none"] = "oauth"
    MIGRATIONS_DIR: str = "/app/migrations"
    PORT: int = 8080

    @model_validator(mode="after")
    def _check_auth(self) -> "Settings":
        if self.AUTH_MODE == "oauth":
            if not self.OAUTH_CLIENT_ID:
                raise ValueError("OAUTH_CLIENT_ID is required when AUTH_MODE=oauth")
            if len(self.SESSION_SECRET) < 16:
                raise ValueError("SESSION_SECRET must be at least 16 characters")
        return self
