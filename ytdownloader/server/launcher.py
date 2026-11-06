"""Starts the app: finds a running copy or starts the server, opens the window, stops when closed.

    python -m ytdownloader.server                 # app-style window if Edge/Chrome is found
    python -m ytdownloader.server --browser       # always a normal browser tab
    python -m ytdownloader.server --no-browser    # just print the address (development)
    python -m ytdownloader.server --keep-running  # do not exit when the page is closed

Single instance: a running server writes its port and token to `server.json` in the data folder.
A second launch finds it, opens a window on it and exits, so there is never a second server.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

import uvicorn

from ..config import data_dir
from .api import APP_ID
from .app import create_app
from .platform_ui import find_app_browser

PORT_RANGE = range(8765, 8800)
# Seconds without any open page before the server exits (page reloads reconnect within seconds),
# and how long to wait for the very first page. Overridable so tests do not wait a minute.
IDLE_GRACE = float(os.environ.get("YTDL_IDLE_GRACE", 45))
STARTUP_GRACE = float(os.environ.get("YTDL_STARTUP_GRACE", 180))
WATCH_INTERVAL = 2.0 if "YTDL_IDLE_GRACE" not in os.environ else 0.2


# ── Single-instance file ────────────────────────────────────────────────────
def instance_path():
    return data_dir() / "server.json"


def read_instance() -> dict | None:
    try:
        info = json.loads(instance_path().read_text(encoding="utf-8"))
        return info if isinstance(info, dict) and "port" in info and "token" in info else None
    except (OSError, ValueError):
        return None


def ping(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=1.5) as response:
            return json.load(response).get("app") == APP_ID
    except Exception:
        return False


def running_instance() -> dict | None:
    info = read_instance()
    return info if info and ping(int(info["port"])) else None


def write_instance(port: int, token: str) -> None:
    path = instance_path()
    path.write_text(json.dumps({"pid": os.getpid(), "port": port, "token": token}), encoding="utf-8")
    try:
        path.chmod(0o600)  # the token is a secret; no effect on Windows, where %APPDATA% is private
    except OSError:
        pass


def remove_instance() -> None:
    info = read_instance()
    if info and info.get("pid") == os.getpid():
        try:
            instance_path().unlink()
        except OSError:
            pass


def wait_until_free(timeout: float = 15.0) -> None:
    """After a restart the old server is still shutting down; wait for it to let go."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and running_instance():
        time.sleep(0.3)


# ── Ports, windows ──────────────────────────────────────────────────────────
def free_port(preferred: int = 0) -> int:
    candidates = [preferred] if preferred else list(PORT_RANGE)
    for port in candidates:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    if preferred:
        raise SystemExit(f"Port {preferred} is already in use.")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def open_ui(url: str, mode: str) -> None:
    """mode: 'app' (bare window when possible), 'browser' (normal tab) or 'none'."""
    if mode == "none":
        return
    if mode == "app":
        exe = find_app_browser()
        if exe:
            try:
                subprocess.Popen([exe, f"--app={url}", "--window-size=1120,820"])
                return
            except OSError:
                pass
    webbrowser.open(url)


def spawn_replacement() -> None:
    """Start a fresh copy of this program (used after a yt-dlp update)."""
    command = (
        [sys.executable, *sys.argv[1:]]
        if getattr(sys, "frozen", False)
        else [sys.executable, "-m", "ytdownloader.server", *sys.argv[1:]]
    )
    kwargs: dict = {"env": {**os.environ, "YTDL_RESTART": "1"}, "stdin": subprocess.DEVNULL,
                    "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = 0x00000008 | 0x08000000  # DETACHED_PROCESS | CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(command, **kwargs)


# ── Main ────────────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="youtube-downloader", description="YouTube Downloader")
    parser.add_argument("--port", type=int, default=0, help="fixed port (default: first free one)")
    parser.add_argument("--browser", action="store_true", help="open a normal browser tab")
    parser.add_argument("--no-browser", action="store_true", help="do not open anything")
    parser.add_argument("--keep-running", action="store_true", help="do not exit when the page closes")
    parser.add_argument("--auto-exit", action="store_true", help=argparse.SUPPRESS)  # for tests
    return parser


def _watch_idle(server: uvicorn.Server, ctx, keep_running: bool) -> None:
    """Stop the server when the last page has been closed and nothing is downloading."""
    hub, jobs = ctx.hub, ctx.jobs
    last_tick = time.monotonic()
    while not server.should_exit:
        time.sleep(WATCH_INTERVAL)
        now = time.monotonic()
        if now - last_tick > WATCH_INTERVAL + 10:  # the computer was asleep: give pages time to return
            hub.last_activity = now
        last_tick = now
        if keep_running or jobs.any_running() or ctx.queue.active() or hub.count:
            continue
        limit = IDLE_GRACE if hub.ever_connected else STARTUP_GRACE
        if now - hub.last_activity > limit:
            server.should_exit = True


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    mode = "none" if args.no_browser else ("browser" if args.browser else "app")

    if os.environ.get("YTDL_RESTART"):
        wait_until_free()

    existing = running_instance()
    if existing:
        url = f"http://127.0.0.1:{existing['port']}/?t={existing['token']}"
        open_ui(url, mode)
        print("Already running:", f"http://127.0.0.1:{existing['port']}/")
        return 0

    # A windowed .exe has no console, so log to a file instead of stderr.
    logging.basicConfig(
        filename=str(data_dir() / "server.log"), level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    port = free_port(args.port)
    token = secrets.token_urlsafe(32)
    holder: dict = {}

    def stop() -> None:
        holder["server"].should_exit = True

    def restart() -> None:
        spawn_replacement()
        stop()

    app = create_app(token, port, shutdown=stop, restart=restart)
    server = uvicorn.Server(
        uvicorn.Config(
            app, host="127.0.0.1", port=port, log_config=None,
            access_log=False, timeout_graceful_shutdown=2,
        )
    )
    holder["server"] = server
    write_instance(port, token)

    def after_start() -> None:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.1)
        else:
            return
        url = f"http://127.0.0.1:{port}/?t={token}"
        print(f"YouTube Downloader is running at http://127.0.0.1:{port}/")
        if mode == "none":
            print("Open this link once to sign the browser in:", url)
        open_ui(url, mode)
        keep = args.keep_running or (mode == "none" and not args.auto_exit)
        _watch_idle(server, app.state.ctx, keep)

    threading.Thread(target=after_start, daemon=True, name="launcher").start()
    try:
        server.run()
    finally:
        remove_instance()
    return 0
