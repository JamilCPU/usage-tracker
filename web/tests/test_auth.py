import json
from typing import Annotated
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient

from usage_web.app import create_app
from usage_web.auth import Session, require_session
from usage_web.config import Settings

ISSUER = "https://gateway.test"
METADATA = {
    "issuer": ISSUER,
    "authorization_endpoint": f"{ISSUER}/authorize",
    "token_endpoint": f"{ISSUER}/token",
    "scopes_supported": ["homestudio:read", "automations:read"],
    "code_challenge_methods_supported": ["S256"],
}


def make_settings(**kw):
    base = {
        "USAGE_READER_DSN": "postgresql://unused",
        "SESSION_SECRET": "test-secret-1234567890",
        "OAUTH_CLIENT_ID": "cid",
        "OAUTH_ISSUER": ISSUER,
        "AUTH_MODE": "oauth",
    }
    return Settings(**{**base, **kw})


class FakeIssuer:
    def __init__(self, token_status=200):
        self.token_status = token_status
        self.token_forms = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/oauth-authorization-server":
            return httpx.Response(200, json=METADATA)
        if request.url.path == "/token":
            self.token_forms.append(parse_qs(request.content.decode()))
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": "x", "token_type": "bearer"})
        return httpx.Response(404)


@pytest.fixture
def issuer():
    return FakeIssuer()


def build(settings, issuer):
    app = create_app(settings, with_pool=False)
    app.state.http_client = httpx.Client(transport=httpx.MockTransport(issuer))
    router = APIRouter()

    @router.get("/api/ping")
    def api_ping(session: Annotated[Session, Depends(require_session)]):
        return {"sub": session.sub}

    @router.get("/page")
    def page(session: Annotated[Session, Depends(require_session)]):
        return {"sub": session.sub}

    app.include_router(router)
    return app


@pytest.fixture
def client(issuer):
    with TestClient(
        build(make_settings(), issuer), follow_redirects=False, base_url="https://usage.lab"
    ) as c:
        yield c


def start_login(client, next_path="/page"):
    resp = client.get("/auth/login", params={"next": next_path})
    assert resp.status_code == 302
    state = parse_qs(urlparse(resp.headers["location"]).query)["state"][0]
    return resp, state


def test_login_redirects_with_pkce_and_state(client):
    resp, _ = start_login(client)
    loc = resp.headers["location"]
    assert loc.startswith(f"{ISSUER}/authorize?")
    assert "code_challenge_method=S256" in loc
    assert "state=" in loc
    assert "client_id=cid" in loc
    assert "redirect_uri=https%3A%2F%2Fusage.lab%2Fauth%2Fcallback" in loc
    assert "scope=homestudio%3Aread" in loc
    assert "usage_oauth" in resp.headers["set-cookie"]
    cookie = resp.headers["set-cookie"]
    assert "Max-Age=600" in cookie
    assert "SameSite=lax" in cookie
    assert "Secure" in cookie
    assert "HttpOnly" in cookie


def test_callback_rejects_bad_state(client):
    start_login(client)
    assert client.get("/auth/callback?code=abc&state=wrong").status_code == 400


def test_callback_without_cookie_is_400(client):
    assert client.get("/auth/callback?code=abc&state=s").status_code == 400


def test_callback_sets_session_cookie(client, issuer):
    _, state = start_login(client)
    resp = client.get(f"/auth/callback?code=abc&state={state}")
    assert resp.status_code == 302
    assert resp.headers["location"] == "/page"
    cookies = resp.headers.get_list("set-cookie")
    assert any(c.startswith("usage_session=") for c in cookies)
    form = issuer.token_forms[0]
    assert form["grant_type"] == ["authorization_code"]
    assert form["client_id"] == ["cid"]
    assert form["code"] == ["abc"]
    assert len(form["code_verifier"][0]) >= 43
    assert client.get("/page").status_code == 200


def test_callback_token_failure_is_not_a_session(issuer):
    issuer.token_status = 400
    with TestClient(
        build(make_settings(), issuer), follow_redirects=False, base_url="https://usage.lab"
    ) as c:
        _, state = start_login(c)
        resp = c.get(f"/auth/callback?code=abc&state={state}")
        assert resp.status_code == 502
        assert "usage_session" not in resp.headers.get("set-cookie", "")


def test_api_without_session_is_401(client):
    resp = client.get("/api/ping")
    assert resp.status_code == 401
    assert resp.json() == {"detail": "login required"}


def test_page_without_session_redirects_to_login(client):
    resp = client.get("/page")
    assert resp.status_code == 302
    assert resp.headers["location"] == "/auth/login?next=%2Fpage"


def test_tampered_session_is_rejected(client):
    client.cookies.set("usage_session", json.dumps({"sub": "x"}) + ".bad.sig")
    assert client.get("/api/ping").status_code == 401


def test_logout_clears_session(client):
    _, state = start_login(client)
    client.get(f"/auth/callback?code=abc&state={state}")
    resp = client.get("/auth/logout")
    assert resp.status_code == 302
    assert resp.headers["location"] == "/"
    assert client.get("/api/ping").status_code == 401


def test_auth_mode_none_allows(issuer):
    app = build(make_settings(AUTH_MODE="none", OAUTH_CLIENT_ID=None), issuer)
    with TestClient(app, follow_redirects=False, base_url="https://usage.lab") as c:
        assert c.get("/api/ping").status_code == 200
        assert c.get("/healthz").json() == {"ok": True}


def test_open_redirect_next_is_neutralised(client):
    _, state = start_login(client, "https://evil.example/")
    done = client.get(f"/auth/callback?code=abc&state={state}")
    assert done.headers["location"] == "/"


def test_callback_rejects_non_ascii_state(client):
    start_login(client)
    assert (
        client.get("/auth/callback", params={"code": "abc", "state": "\u00e9"}).status_code == 400
    )


def test_settings_require_client_id_in_oauth_mode():
    with pytest.raises(ValueError, match="OAUTH_CLIENT_ID"):
        make_settings(OAUTH_CLIENT_ID=None)
    with pytest.raises(ValueError, match="SESSION_SECRET"):
        make_settings(SESSION_SECRET="short")


def test_page_redirect_preserves_query(client):
    resp = client.get("/page?range=7d")
    assert resp.headers["location"] == "/auth/login?next=%2Fpage%3Frange%3D7d"
