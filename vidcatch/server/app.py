"""Builds the FastAPI application."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .. import APP_NAME, __version__
from ..config import temp_dir
from ..downloader import cleanup_old_partials, cleanup_stale_temp
from . import api as api_module
from .api import AppContext, router
from .events import EventHub
from .jobs import JobManager
from .queue_api import router as queue_router
from .queue_manager import QueueManager
from .security import SecurityMiddleware

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def create_app(token: str, port: int, *, shutdown=None, restart=None) -> FastAPI:
    """`shutdown` / `restart` are callables supplied by the launcher; tests pass stubs."""
    hub = EventHub()
    jobs = JobManager(hub)

    def fetch(url, source):
        # Looked up at call time so tests can replace api.fetch_video_info.
        if source:
            from ..signin import Source, fetch_with_sources

            found, result, error = fetch_with_sources(url, [Source(source[0], source[1], source[1])])
            if found is None:
                raise RuntimeError(error)
            return result
        return api_module.fetch_video_info(url)

    app = FastAPI(
        title=APP_NAME, version=__version__,
        docs_url=None, redoc_url=None, openapi_url="/api/openapi.json",
    )
    app.state.ctx = AppContext(
        hub=hub,
        jobs=jobs,
        queue=QueueManager(hub, jobs, fetch),
        shutdown=shutdown or (lambda: None),
        restart=restart or (lambda: None),
    )
    app.include_router(router)
    app.include_router(queue_router)

    # Static UI last, so it never shadows /api. html=True serves index.html at "/".
    if WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")

    # Runs for every request, outermost: host/origin/cookie checks and security headers.
    app.add_middleware(SecurityMiddleware, token=token, port=port)

    app.state.ctx.queue.maybe_autostart()
    try:
        cleanup_stale_temp(temp_dir())  # leftovers from a crash
        cleanup_old_partials(temp_dir())  # unfinished downloads older than 7 days
    except OSError:
        pass
    return app
