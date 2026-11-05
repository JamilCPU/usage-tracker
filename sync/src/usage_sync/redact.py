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
# Window titles of browsers in a private/incognito window carry one of these.
PRIVATE_TITLE_MARKERS = ("private browsing", "inprivate", "incognito", "private window")


def _hit(value: str | None, needles: list[str]) -> bool:
    if not value:
        return False
    low = value.lower()
    return any(n.lower() in low for n in needles if n)


class Redactor:
    def __init__(
        self,
        apps: list[str],
        domains: list[str],
        title_markers: list[str] | tuple[str, ...] = PRIVATE_TITLE_MARKERS,
    ) -> None:
        self.apps = list(apps)
        self.domains = list(domains)
        self.title_markers = list(title_markers)

    def apply(self, event: dict) -> dict:
        out = dict(event)
        if _hit(out.get("app"), self.apps) or _hit(out.get("domain"), self.domains):
            out["title"] = None
            out["url"] = None
        elif out.get("kind") == "window" and (
            _hit(out.get("title"), self.domains) or _hit(out.get("title"), self.title_markers)
        ):
            # A window titled with a bank's name or a private-window marker leaks what the
            # domain and incognito rules hide for web events; keep the app, drop the title.
            out["title"] = None
        return out
