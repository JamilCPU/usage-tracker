"""Load usage-sync configuration from a TOML file."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

from usage_sync.redact import DEFAULT_APPS, DEFAULT_DOMAINS


class ConfigError(Exception):
    """Raised for an unreadable or invalid configuration file."""


def default_config_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "usage-sync"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return (Path(xdg) if xdg else Path.home() / ".config") / "usage-sync"


@dataclass
class Config:
    ingest_url: str
    device: str
    aw_url: str = "http://127.0.0.1:5600"
    aw_timeout_seconds: int = 10
    interval_seconds: int = 60
    batch_size: int = 1000
    settle_seconds: int = 120
    overlap_seconds: int = 3600
    redact_apps: list[str] = field(default_factory=lambda: list(DEFAULT_APPS))
    redact_domains: list[str] = field(default_factory=lambda: list(DEFAULT_DOMAINS))
    token_file: Path | None = None
    ca_file: str | None = None

    def __post_init__(self) -> None:
        if self.token_file is None:
            self.token_file = default_config_dir() / "token"
        else:
            self.token_file = Path(self.token_file)

    @classmethod
    def load(cls, path: Path) -> Config:
        path = Path(path)
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ConfigError(f"cannot read config {path}: {exc}") from exc
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"invalid TOML in {path}: {exc}") from exc
        known = {f.name for f in fields(cls)}
        unknown = sorted(set(data) - known)
        if unknown:
            raise ConfigError(f"unknown config keys: {', '.join(unknown)}")
        missing = [k for k in ("ingest_url", "device") if not data.get(k)]
        if missing:
            raise ConfigError(f"missing required config keys: {', '.join(missing)}")
        data.setdefault("token_file", str(path.parent / "token"))
        return cls(**data)
