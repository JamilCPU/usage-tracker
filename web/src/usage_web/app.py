from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .auth import LoginRequired, login_required_handler
from .auth import router as auth_router
from .config import Settings

STATIC_DIR = Path(__file__).parent / "static"


def create_app(settings: Settings, with_pool: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.pool = db.open_pool(settings.USAGE_READER_DSN) if with_pool else None
        try:
            yield
        finally:
            if app.state.pool is not None:
                app.state.pool.close()

    app = FastAPI(title="usage-web", lifespan=lifespan)
    app.state.settings = settings
    app.state.pool = None
    app.include_router(auth_router)
    app.add_exception_handler(LoginRequired, login_required_handler)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    @app.get("/readyz")
    def readyz() -> JSONResponse:
        pool = app.state.pool
        if pool is not None and db.ping(pool):
            return JSONResponse({"ok": True})
        return JSONResponse({"ok": False}, status_code=503)

    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app
