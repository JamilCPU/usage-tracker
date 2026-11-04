"""Blank sensitive titles/URLs before events leave the device."""

from __future__ import annotations

DEFAULT_APPS = ["1password", "bitwarden", "keepass", "lastpass"]
DEFAULT_DOMAINS = [
    "bank",
    "chase.com",
    "wellsfargo.com",
    "capitalone.com",
    "paypal.com",
    "venmo.com",
]


def _hit(value: str | None, needles: list[str]) -> bool:
    if not value:
        return False
    low = value.lower()
    return any(n.lower() in low for n in needles if n)


class Redactor:
    def __init__(self, apps: list[str], domains: list[str]) -> None:
        self.apps = list(apps)
        self.domains = list(domains)

    def apply(self, event: dict) -> dict:
        out = dict(event)
        if _hit(out.get("app"), self.apps) or _hit(out.get("domain"), self.domains):
            out["title"] = None
            out["url"] = None
        return out
