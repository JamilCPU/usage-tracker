import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner, URLSafeTimedSerializer

from .config import Settings

router = APIRouter(prefix="/auth")

OAUTH_COOKIE = "usage_oauth"
SESSION_COOKIE = "usage_session"
OAUTH_MAX_AGE = 600
SESSION_MAX_AGE = 30 * 24 * 3600
METADATA_PATH = "/.well-known/oauth-authorization-server"


@dataclass(frozen=True)
class Session:
    sub: str
    iat: int = 0


class LoginRequired(Exception):
    pass


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _secure(settings: Settings) -> bool:
    return settings.PUBLIC_URL.startswith("https://")


def _client(request: Request) -> httpx.Client:
    client = getattr(request.app.state, "http_client", None)
    if client is None:
        settings = _settings(request)
        client = httpx.Client(verify=settings.OAUTH_CA_FILE or True, timeout=10)
        request.app.state.http_client = client
    return client


def _metadata(request: Request) -> dict:
    cached = getattr(request.app.state, "oauth_metadata", None)
    if cached:
        return cached
    url = _settings(request).OAUTH_ISSUER.rstrip("/") + METADATA_PATH
    try:
        resp = _client(request).get(url)
        resp.raise_for_status()
        meta = resp.json()
        meta["authorization_endpoint"], meta["token_endpoint"]  # noqa: B018
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise HTTPException(503, "identity provider unavailable") from exc
    request.app.state.oauth_metadata = meta
    return meta


def _safe_next(value: str | None) -> str:
    if value and value.startswith("/") and not value.startswith("//") and "\\" not in value:
        return value
    return "/"


def _oauth_serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.SESSION_SECRET, salt="usage-oauth")


def _session_signer(settings: Settings) -> TimestampSigner:
    return TimestampSigner(settings.SESSION_SECRET, salt="usage-session")


@router.get("/login")
def login(request: Request, next: str | None = None) -> RedirectResponse:
    settings = _settings(request)
    meta = _metadata(request)
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    state = secrets.token_urlsafe(24)
    scope = settings.OAUTH_SCOPE
    if not scope:
        scopes = meta.get("scopes_supported") or []
        scope = scopes[0] if scopes else None
    params = {
        "response_type": "code",
        "client_id": settings.OAUTH_CLIENT_ID or "",
        "redirect_uri": settings.PUBLIC_URL.rstrip("/") + "/auth/callback",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if scope:
        params["scope"] = scope
    resp = RedirectResponse(f"{meta['authorization_endpoint']}?{urlencode(params)}", 302)
    payload = {"verifier": verifier, "state": state, "next": _safe_next(next)}
    resp.set_cookie(
        OAUTH_COOKIE,
        _oauth_serializer(settings).dumps(payload),
        max_age=OAUTH_MAX_AGE,
        httponly=True,
        secure=_secure(settings),
        samesite="lax",
    )
    return resp


@router.get("/callback")
def callback(request: Request, code: str = "", state: str = "") -> RedirectResponse:
    settings = _settings(request)
    raw = request.cookies.get(OAUTH_COOKIE)
    if not raw:
        raise HTTPException(400, "missing login state")
    try:
        saved = _oauth_serializer(settings).loads(raw, max_age=OAUTH_MAX_AGE)
    except BadSignature as exc:
        raise HTTPException(400, "invalid login state") from exc
    if not code or not secrets.compare_digest(str(saved.get("state", "")), state):
        raise HTTPException(400, "state mismatch")
    meta = _metadata(request)
    form = {
        "grant_type": "authorization_code",
        "client_id": settings.OAUTH_CLIENT_ID or "",
        "code": code,
        "redirect_uri": settings.PUBLIC_URL.rstrip("/") + "/auth/callback",
        "code_verifier": saved["verifier"],
    }
    try:
        token = _client(request).post(meta["token_endpoint"], data=form)
        token.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(502, "token exchange failed") from exc
    resp = RedirectResponse(_safe_next(saved.get("next")), 302)
    value = json.dumps({"sub": "user", "iat": int(time.time())})
    resp.set_cookie(
        SESSION_COOKIE,
        _session_signer(settings).sign(value).decode(),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=_secure(settings),
        samesite="lax",
    )
    resp.delete_cookie(OAUTH_COOKIE)
    return resp


@router.get("/logout")
def logout() -> RedirectResponse:
    resp = RedirectResponse("/", 302)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


def _read_session(request: Request, settings: Settings) -> Session | None:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return None
    try:
        data = json.loads(_session_signer(settings).unsign(raw, max_age=SESSION_MAX_AGE))
    except (BadSignature, SignatureExpired, ValueError):
        return None
    return Session(sub=str(data.get("sub", "user")), iat=int(data.get("iat", 0)))


def require_session(request: Request) -> Session:
    settings = _settings(request)
    if settings.AUTH_MODE == "none":
        return Session(sub="anonymous")
    session = _read_session(request, settings)
    if session is not None:
        return session
    path = request.url.path
    if path.startswith("/api/"):
        raise HTTPException(401, "login required")
    raise LoginRequired(path)


def login_required_handler(request: Request, exc: LoginRequired) -> RedirectResponse:
    return RedirectResponse(f"/auth/login?{urlencode({'next': str(exc)})}", 302)
