"""Starts the real server in a subprocess and talks to it over HTTP, like the launcher and a
browser do. Covers what the in-process tests cannot: single instance, the event stream, the
cookie hand-off, and shutting down by itself when the page is gone."""

import asyncio
import http.client
import json
import os
import subprocess
import sys
import time

import pytest

from ytdownloader.server import launcher
from ytdownloader.server.events import EventHub


# ── In-process pieces ───────────────────────────────────────────────────────
def test_free_port_prefers_the_default_range_and_skips_busy_ones():
    first = launcher.free_port()
    assert first in launcher.PORT_RANGE
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", first))
        assert launcher.free_port() != first


def test_instance_file_roundtrip(isolated_data_dir):
    assert launcher.read_instance() is None
    launcher.write_instance(8765, "tok")
    info = launcher.read_instance()
    assert info["port"] == 8765 and info["token"] == "tok" and info["pid"] == os.getpid()
    launcher.remove_instance()
    assert launcher.read_instance() is None


def test_stale_instance_file_is_not_a_running_server(isolated_data_dir):
    launcher.write_instance(1, "tok")  # nothing listens there
    assert launcher.running_instance() is None


def test_event_hub_delivers_to_subscribers_from_other_threads():
    async def scenario():
        hub = EventHub()
        queue = hub.subscribe()
        assert hub.count == 1 and hub.ever_connected
        await asyncio.to_thread(hub.publish, {"type": "x"})
        assert await asyncio.wait_for(queue.get(), 1) == {"type": "x"}
        hub.unsubscribe(queue)
        assert hub.count == 0

    asyncio.run(scenario())


def test_event_hub_drops_oldest_when_a_client_is_stuck():
    async def scenario():
        hub = EventHub()
        queue = hub.subscribe()
        for i in range(600):
            hub.publish({"n": i})
        await asyncio.sleep(0.2)
        assert queue.qsize() == 500
        assert (await queue.get())["n"] == 100  # the oldest 100 were dropped

    asyncio.run(scenario())


# ── Real server process ─────────────────────────────────────────────────────
def start_server(data_dir, *flags, env_extra=None):
    env = {**os.environ, "YTDL_DATA_DIR": str(data_dir), **(env_extra or {})}
    proc = subprocess.Popen(
        [sys.executable, "-m", "ytdownloader.server", "--no-browser", *flags],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    info_file = data_dir / "server.json"
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError("server exited early:\n" + proc.stdout.read())
        if info_file.exists():
            info = json.loads(info_file.read_text())
            try:
                conn = http.client.HTTPConnection("127.0.0.1", info["port"], timeout=1)
                conn.request("GET", "/api/ping")
                if conn.getresponse().status == 200:
                    return proc, info
            except OSError:
                pass
        time.sleep(0.1)
    proc.kill()
    raise AssertionError("server did not start")


def request(info, method, path, cookie=None, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", info["port"], timeout=5)
    headers = {"Cookie": cookie} if cookie else {}
    if body is not None:
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
    return conn, conn.getresponse()


@pytest.fixture
def server(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    proc, info = start_server(data_dir, "--keep-running")
    yield proc, info, data_dir
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=10)


def test_cookie_handoff_events_and_shutdown(server):
    proc, info, _ = server

    # The launch link swaps the token for a cookie.
    conn, response = request(info, "GET", f"/?t={info['token']}")
    assert response.status == 303
    cookie = response.getheader("Set-Cookie").split(";")[0]
    conn.close()

    # Without the cookie the API is closed.
    conn, response = request(info, "GET", "/api/status")
    assert response.status == 401
    conn.close()

    conn, response = request(info, "GET", "/api/status", cookie)
    assert response.status == 200 and json.loads(response.read())["app"] == "youtube-downloader"
    conn.close()

    # The event stream starts with a hello message.
    conn, response = request(info, "GET", "/api/events", cookie)
    assert response.status == 200
    assert response.getheader("Content-Type").startswith("text/event-stream")
    first = response.readline().decode()
    assert first.startswith("data: ") and json.loads(first[6:])["type"] == "hello"

    # Settings changes made over the API are saved on disk.
    c2, r2 = request(info, "PUT", "/api/settings", cookie, {"language": "ar"})
    assert r2.status == 200
    c2.close()

    c3, r3 = request(info, "POST", "/api/shutdown", cookie)
    assert r3.status == 200
    c3.close()
    conn.close()
    assert proc.wait(timeout=15) == 0
    assert not (server[2] / "server.json").exists()  # the instance file is cleaned up
    assert json.loads((server[2] / "config.json").read_text())["language"] == "ar"


def test_second_launch_reuses_the_running_server(server, tmp_path):
    _, info, data_dir = server
    result = subprocess.run(
        [sys.executable, "-m", "ytdownloader.server", "--no-browser"],
        env={**os.environ, "YTDL_DATA_DIR": str(data_dir)},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0
    assert "Already running" in result.stdout and str(info["port"]) in result.stdout


def test_server_exits_by_itself_when_the_page_is_gone(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    proc, info = start_server(
        data_dir, "--auto-exit",
        env_extra={"YTDL_IDLE_GRACE": "1", "YTDL_STARTUP_GRACE": "60"},
    )
    try:
        cookie = f"ytdl_session={info['token']}"
        conn, response = request(info, "GET", "/api/events", cookie)
        response.readline()  # hello: a page is connected, so the server stays up
        time.sleep(2)
        assert proc.poll() is None
        conn.close()  # the page is closed
        assert proc.wait(timeout=15) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
