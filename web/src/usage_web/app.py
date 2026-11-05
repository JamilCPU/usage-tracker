from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import db
from .api import router as api_router
from .auth import LoginRequired, login_required_handler, require_session
from .auth import router as auth_router
from .config import Settings

STATIC_DIR = Path(__file__).parent / "static"
NOT_BUILT_HTML = (
    "<!doctype html><title>usage-web</title>"
    "<p>The frontend is not built. Run the web/frontend build to populate the static directory.</p>"
)


def create_app(settings: Settings, with_pool: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if with_pool and not settings.USAGE_READER_DSN:
            raise RuntimeError("USAGE_READER_DSN is required to serve")
        app.state.pool = db.open_pool(settings.USAGE_READER_DSN) if with_pool else None
        try:
            yield
        finally:
            if app.state.pool is not None:
                app.state.pool.close()

    app = FastAPI(
        title="usage-web", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.settings = settings
    app.state.pool = None
    app.include_router(auth_router)
    app.include_router(api_router)
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

    @app.get("/", dependencies=[Depends(require_session)])
    def index() -> Response:
        index_file = STATIC_DIR / "index.html"
        if index_file.is_file():
            return FileResponse(index_file)
        return HTMLResponse(NOT_BUILT_HTML)

    assets = STATIC_DIR / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")
    return app
