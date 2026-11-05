"""Load usage-sync configuration from a TOML file."""

from __future__ import annotations

import math
import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

from usage_sync.mapping import IDLE_APPS
from usage_sync.redact import DEFAULT_APPS, DEFAULT_DOMAINS


class ConfigError(Exception):
    """Raised for an unreadable or invalid configuration file."""


def default_config_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "usage-sync"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return (Path(xdg) if xdg else Path.home() / ".config") / "usage-sync"


def parse_connect_host(value: object) -> tuple[str, int | None]:
    """Parse `host`, `host:port`, `[v6]` or `[v6]:port` into (host, port or None)."""
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("ingest_connect_host must be a non-empty string")
    text = value.strip()
    if text.startswith("["):
        host, closed, rest = text[1:].partition("]")
        if not closed or (rest and not rest.startswith(":")):
            raise ConfigError(f"invalid ingest_connect_host {value!r}")
        port_text = rest[1:]
        has_port = bool(rest)
    else:
        host, sep, port_text = text.partition(":")
        has_port = bool(sep)
    if not host:
        raise ConfigError(f"invalid ingest_connect_host {value!r}")
    if not has_port:
        return host, None
    if not (port_text.isascii() and port_text.isdecimal()) or not 1 <= int(port_text) <= 65535:
        raise ConfigError(f"invalid port in ingest_connect_host {value!r}")
    return host, int(port_text)


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
    idle_apps: list[str] = field(default_factory=lambda: list(IDLE_APPS))
    token_file: Path | None = None
    ca_file: str | None = None
    ingest_connect_host: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.idle_apps, list) or not all(
            isinstance(a, str) for a in self.idle_apps
        ):
            raise ConfigError("idle_apps must be a list of strings")
        timeout = self.aw_timeout_seconds
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, int | float)
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ConfigError("aw_timeout_seconds must be a number greater than 0")
        if self.ingest_connect_host is not None:
            parse_connect_host(self.ingest_connect_host)
            if not self.ingest_url.lower().startswith("https://"):
                raise ConfigError("ingest_connect_host requires an https ingest_url")
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
