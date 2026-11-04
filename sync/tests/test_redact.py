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
