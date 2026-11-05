import pytest

from usage_sync.redact import DEFAULT_APPS, DEFAULT_DOMAINS, Redactor


def _ev(**kw):
    base = {
        "app": "Code.exe",
        "title": "t",
        "url": "https://x.example.com/a",
        "domain": "x.example.com",
    }
    return {**base, **kw}


def test_redactor_blanks_title_for_matching_app():
    out = Redactor(DEFAULT_APPS, DEFAULT_DOMAINS).apply(_ev(app="1Password.exe"))
    assert out["title"] is None and out["url"] is None


def test_redactor_keeps_domain_blank_url():
    out = Redactor(["x"], ["MyBank.com"]).apply(_ev(app=None, domain="www.mybank.com"))
    assert out["title"] is None and out["url"] is None
    assert out["domain"] == "www.mybank.com"


def test_redactor_returns_new_dict():
    src = _ev(app="KeePassXC")
    out = Redactor(DEFAULT_APPS, DEFAULT_DOMAINS).apply(src)
    assert out is not src
    assert src["title"] == "t" and src["url"] is not None


def test_non_matching_event_unchanged():
    src = _ev()
    out = Redactor(DEFAULT_APPS, DEFAULT_DOMAINS).apply(src)
    assert out == src and out is not src


def test_defaults():
    assert DEFAULT_APPS == ["1password", "bitwarden", "keepass", "lastpass"]
    assert "bank" in DEFAULT_DOMAINS


def _win(title, **kw):
    return _ev(kind="window", app="firefox.exe", title=title, url=None, domain=None, **kw)


def test_window_title_with_domain_needle_is_blanked_but_app_kept():
    out = Redactor(DEFAULT_APPS, DEFAULT_DOMAINS).apply(_win("Chase.com - Accounts - Firefox"))
    assert out["title"] is None and out["app"] == "firefox.exe"
    out = Redactor([], ["mybank"]).apply(_win("MyBank | Statement"))
    assert out["title"] is None


@pytest.mark.parametrize(
    "title",
    [
        "News - Mozilla Firefox Private Browsing",
        "Tab - Microsoft Edge [InPrivate]",
        "Home - Google Chrome (Incognito)",
        "Docs - Safari Private Window",
    ],
)
def test_window_private_markers_blank_title(title):
    out = Redactor(DEFAULT_APPS, DEFAULT_DOMAINS).apply(_win(title))
    assert out["title"] is None and out["app"] == "firefox.exe"


def test_window_normal_title_untouched_and_markers_configurable():
    red = Redactor(DEFAULT_APPS, DEFAULT_DOMAINS)
    assert (
        red.apply(_win("report.py - Visual Studio Code"))["title"]
        == "report.py - Visual Studio Code"
    )
    assert Redactor([], [], ["secret tab"]).apply(_win("My Secret Tab"))["title"] is None
    assert Redactor([], [], []).apply(_win("Incognito"))["title"] == "Incognito"


def test_title_rules_only_for_window_events():
    out = Redactor([], ["bank"], ["incognito"]).apply(_ev(kind="web", title="incognito bank"))
    assert out["title"] == "incognito bank"
