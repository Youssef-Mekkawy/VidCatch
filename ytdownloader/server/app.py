"""Builds the FastAPI application."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .. import APP_NAME, __version__
from ..config import temp_dir
from ..downloader import cleanup_stale_temp
from .api import AppContext, router
from .events import EventHub
from .jobs import JobManager
from .security import SecurityMiddleware

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def create_app(token: str, port: int, *, shutdown=None, restart=None) -> FastAPI:
    """`shutdown` / `restart` are callables supplied by the launcher; tests pass stubs."""
    hub = EventHub()
    app = FastAPI(
        title=APP_NAME, version=__version__,
        docs_url=None, redoc_url=None, openapi_url="/api/openapi.json",
    )
    app.state.ctx = AppContext(
        hub=hub,
        jobs=JobManager(hub),
        shutdown=shutdown or (lambda: None),
        restart=restart or (lambda: None),
    )
    app.include_router(router)

    # Static UI last, so it never shadows /api. html=True serves index.html at "/".
    if WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")

    # Runs for every request, outermost: host/origin/cookie checks and security headers.
    app.add_middleware(SecurityMiddleware, token=token, port=port)

    try:
        cleanup_stale_temp(temp_dir())  # leftovers from a crash
    except OSError:
        pass
    return app
