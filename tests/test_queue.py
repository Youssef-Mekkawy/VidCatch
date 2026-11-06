"""The download queue: adding links, quality, subtitles, running one by one, pause/resume, recovery."""

import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vidcatch.config import data_dir, load_history, update_config
from vidcatch.server import api, jobs
from vidcatch.server.app import create_app
from vidcatch.server.queue_manager import QueueManager, match_languages, video_key

TOKEN = "t0ken"
PORT = 8765


def link(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def make_info(video_id: str, **extra) -> dict:
    return {
        "id": video_id, "title": f"Title {video_id}", "uploader": "Chan", "duration": 100,
        "thumbnail": "", "subtitles": {"en": [], "ar": []}, "automatic_captions": {"fr-orig": [], "fr": []},
        "formats": [
            {"height": 1080, "vcodec": "avc1", "acodec": "none", "ext": "mp4", "filesize": 50_000_000},
            {"height": 720, "vcodec": "avc1", "acodec": "none", "ext": "mp4", "filesize": 30_000_000},
            {"height": 480, "vcodec": "avc1", "acodec": "none", "ext": "mp4", "filesize": 10_000_000},
            {"vcodec": "none", "acodec": "mp4a", "filesize": 4_000_000},
        ],
        **extra,
    }


@pytest.fixture
def download_dir(tmp_path):
    folder = tmp_path / "downloads"
    update_config({"download_dir": str(folder)})
    return folder


@pytest.fixture
def app(monkeypatch, download_dir):
    monkeypatch.setattr(api, "fetch_video_info", lambda url: (False, make_info(video_key(url))))
    return create_app(TOKEN, PORT)


@pytest.fixture
def client(app):
    c = TestClient(app, base_url=f"http://127.0.0.1:{PORT}")
    c.headers["cookie"] = f"ytdl_session={TOKEN}"
    return c


def wait(client, check, timeout=6.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snap = client.get("/api/queue").json()
        if check(snap):
            return snap
        time.sleep(0.03)
    raise AssertionError(f"timed out; last state: {snap}")


def statuses(snap):
    return [i["status"] for i in snap["items"]]


def by_id(snap, video_id):
    return next(i for i in snap["items"] if i["url"].endswith(video_id))


def add(client, *ids):
    result = client.post("/api/queue/items", json={"urls": [link(i) for i in ids]})
    assert result.status_code == 200, result.text
    wait(client, lambda s: all(st != "fetching" for st in statuses(s)))
    return result.json()


def fake_download(url, opts, temp_root, output_path, **_):
    hook = opts["progress_hooks"][0]
    hook({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100, "speed": 1_048_576, "eta": 3})
    hook({"status": "finished"})
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * 10)
    return str(path), {"title": Path(output_path).stem, "uploader": "Chan", "thumbnail": ""}


# ── Adding ──────────────────────────────────────────────────────────────────
def test_add_loads_metadata_in_the_background(client):
    result = add(client, "aaaaaaaaaaa", "bbbbbbbbbbb")
    assert len(result["added"]) == 2
    snap = client.get("/api/queue").json()
    first = snap["items"][0]
    assert first["status"] == "ready" and first["title"] == "Title aaaaaaaaaaa"
    assert first["qualities"] == ["1080p", "720p", "480p"]
    assert first["est_bytes"] > 0
    assert first["tracks"] == {"manual": ["en", "ar"], "auto": ["fr-orig"]}


def test_duplicates_are_ignored_and_the_limit_is_50(client):
    add(client, "aaaaaaaaaaa")
    again = client.post("/api/queue/items", json={"urls": [link("aaaaaaaaaaa"), "https://youtu.be/aaaaaaaaaaa"]})
    assert again.json() == {"added": [], "duplicates": 2, "left_out": 0}
    ids = [f"v{n:010d}" for n in range(50)]
    assert client.post("/api/queue/items", json={"urls": [link(i) for i in ids]}).status_code == 422
    assert client.post("/api/queue/items", json={"urls": ["not a link"]}).status_code == 422


def test_playlist_link_and_failed_fetch_are_reported(client, monkeypatch):
    def fetch(url):
        if "pppppppppp" in url:
            return True, {"entries": []}
        if "gggggggggg" in url:
            raise RuntimeError("ERROR: [youtube] x: Video unavailable. This video has been removed")
        return False, make_info(video_key(url))

    monkeypatch.setattr(api, "fetch_video_info", fetch)
    add(client, "pppppppppp", "gggggggggg")
    snap = client.get("/api/queue").json()
    assert by_id(snap, "pppppppppp")["error"]["code"] == "playlist"
    gone = by_id(snap, "gggggggggg")
    assert gone["status"] == "failed" and gone["error"]["problem"].startswith("E")


# ── Quality and subtitles ───────────────────────────────────────────────────
def test_quality_for_all_or_per_video_falls_back_to_the_closest_lower(client):
    add(client, "aaaaaaaaaaa", "bbbbbbbbbbb")
    client.put("/api/queue/settings", json={"quality": "600p"})
    snap = client.get("/api/queue").json()
    assert [i["quality"] for i in snap["items"]] == ["480p", "480p"]
    item = snap["items"][0]
    client.patch(f"/api/queue/items/{item['id']}", json={"quality": "1080p"})
    snap = client.get("/api/queue").json()
    assert snap["items"][0]["quality"] == "1080p" and snap["items"][1]["quality"] == "480p"
    assert snap["estimate_bytes"] == (50_000_000 + 4_000_000) + (10_000_000 + 4_000_000)


def test_language_matching():
    assert match_languages(["en", "ar"], ["en", "ar", "fr"]) == ["en", "ar"]
    assert match_languages(["ar"], ["ar-orig"]) == ["ar-orig"]
    assert match_languages(["en"], ["en-US", "en-GB"]) == ["en-US"]
    assert match_languages(["de"], ["en"]) == []


def test_subtitles_are_taken_when_they_exist_and_go_in_the_video_folder(client, monkeypatch, download_dir):
    seen = {}

    def spy(url, opts, temp_root, output_path, **_):
        seen[url] = (opts.get("subtitleslangs"), str(output_path))
        return fake_download(url, opts, temp_root, output_path)

    monkeypatch.setattr(jobs, "download_media", spy)
    monkeypatch.setattr(api, "fetch_video_info", lambda url: (False, make_info(
        video_key(url), subtitles={"en": []} if "aaaaaaaaaaa" in url else {})))
    add(client, "aaaaaaaaaaa", "bbbbbbbbbbb")
    client.put("/api/queue/settings", json={"sub_langs": ["en", "ar", "fr"]})
    snap = client.get("/api/queue").json()
    assert by_id(snap, "aaaaaaaaaaa")["sub_manual"] == ["en"]
    assert by_id(snap, "aaaaaaaaaaa")["sub_auto"] == ["fr-orig"]
    client.post("/api/queue/start")
    wait(client, lambda s: statuses(s) == ["done", "done"])
    langs, path = seen[link("aaaaaaaaaaa")]
    assert set(langs) == {"en", "fr-orig"}
    assert Path(path).parent == download_dir / "Title aaaaaaaaaaa"
    # No manual track for the second video: only its automatic original caption is requested.
    assert seen[link("bbbbbbbbbbb")][0] == ["fr-orig"]


# ── Running ─────────────────────────────────────────────────────────────────
def test_runs_one_by_one_in_order_and_records_history(client, monkeypatch, download_dir):
    order, running = [], []

    def tracked(url, opts, temp_root, output_path, **_):
        running.append(1)
        assert len(running) == 1, "two downloads ran at the same time"
        order.append(url)
        time.sleep(0.05)
        result = fake_download(url, opts, temp_root, output_path)
        running.pop()
        return result

    monkeypatch.setattr(jobs, "download_media", tracked)
    add(client, "aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc")
    assert client.post("/api/queue/start").json()["queued"] == 3
    wait(client, lambda s: statuses(s) == ["done"] * 3)
    assert order == [link("aaaaaaaaaaa"), link("bbbbbbbbbbb"), link("ccccccccccc")]
    assert len(load_history()) == 3
    assert all(Path(i["filepath"]).is_file() for i in client.get("/api/queue").json()["items"])


def test_a_failed_video_does_not_stop_the_rest(client, monkeypatch):
    def flaky(url, opts, temp_root, output_path, **kw):
        if "bbbbbbbbbbb" in url:
            raise PermissionError("[Errno 13] Permission denied: 'C:\\\\x.mp4'")
        return fake_download(url, opts, temp_root, output_path)

    monkeypatch.setattr(jobs, "download_media", flaky)
    add(client, "aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc")
    client.post("/api/queue/start")
    snap = wait(client, lambda s: "downloading" not in statuses(s) and "queued" not in statuses(s))
    assert statuses(snap) == ["done", "failed", "done"]
    assert snap["items"][1]["error"]["problem"].startswith("E")
    monkeypatch.setattr(jobs, "download_media", fake_download)  # the problem is fixed, then retry
    client.post("/api/queue/retry", json={})
    wait(client, lambda s: statuses(s) == ["done"] * 3)


def test_runner_keeps_the_server_alive_only_while_working(client, app, monkeypatch):
    gate = threading.Event()

    def slow(url, opts, temp_root, output_path, **kw):
        gate.wait(5)
        return fake_download(url, opts, temp_root, output_path)

    monkeypatch.setattr(jobs, "download_media", slow)
    add(client, "aaaaaaaaaaa")
    assert not app.state.ctx.queue.active()
    client.post("/api/queue/start")
    wait(client, lambda s: statuses(s) == ["downloading"])
    assert app.state.ctx.queue.active()
    gate.set()
    wait(client, lambda s: statuses(s) == ["done"])
    time.sleep(0.2)
    assert not app.state.ctx.queue.active()


def test_existing_file_follows_the_batch_rule(client, download_dir, monkeypatch):
    monkeypatch.setattr(jobs, "download_media", fake_download)
    add(client, "aaaaaaaaaaa", "bbbbbbbbbbb")
    for name in ("Title aaaaaaaaaaa_1080p.mp4", "Title bbbbbbbbbbb_1080p.mp4"):
        download_dir.mkdir(parents=True, exist_ok=True)
        (download_dir / name).write_bytes(b"old")

    client.put("/api/queue/settings", json={"on_exists": "skip"})
    client.post("/api/queue/start")
    wait(client, lambda s: statuses(s) == ["skipped", "skipped"])
    assert (download_dir / "Title aaaaaaaaaaa_1080p.mp4").read_bytes() == b"old"

    client.put("/api/queue/settings", json={"on_exists": "ask"})
    assert client.post("/api/queue/retry", json={}).json()["retried"] == 0  # skipped ones need naming
    ids = [i["id"] for i in client.get("/api/queue").json()["items"]]
    assert client.post("/api/queue/retry", json={"ids": ids}).json()["retried"] == 2
    snap = wait(client, lambda s: statuses(s) == ["exists", "exists"])
    first, second = (i["id"] for i in snap["items"])
    client.post(f"/api/queue/items/{first}/exists?replace=true")
    client.post(f"/api/queue/items/{second}/exists?replace=false")
    wait(client, lambda s: statuses(s) == ["done", "skipped"])
    assert (download_dir / "Title aaaaaaaaaaa_1080p.mp4").read_bytes() == b"x" * 10

    client.put("/api/queue/settings", json={"on_exists": "replace"})
    client.post("/api/queue/retry", json={"ids": ids})
    wait(client, lambda s: statuses(s) == ["done", "done"])


# ── Pause, resume, cancel, remove ───────────────────────────────────────────
def test_pause_keeps_progress_and_resume_continues(client, monkeypatch):
    started, calls = threading.Event(), []

    def pausable(url, opts, temp_root, output_path, **kw):
        calls.append(url)
        if len(calls) == 1:
            started.set()
            hook = opts["progress_hooks"][0]
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:  # the real hook raises when cancel is requested
                hook({"status": "downloading", "downloaded_bytes": 10, "total_bytes": 100, "speed": 1, "eta": 1})
                time.sleep(0.02)
            raise AssertionError("never cancelled")
        return fake_download(url, opts, temp_root, output_path)

    monkeypatch.setattr(jobs, "download_media", pausable)
    add(client, "aaaaaaaaaaa", "bbbbbbbbbbb")
    client.post("/api/queue/start")
    assert started.wait(3)
    client.post("/api/queue/pause")
    snap = wait(client, lambda s: statuses(s) == ["paused", "queued"] and s["state"] == "paused")
    assert not client.app.state.ctx.queue.active()
    client.post("/api/queue/resume")
    wait(client, lambda s: statuses(s) == ["done", "done"])
    assert calls == [link("aaaaaaaaaaa"), link("aaaaaaaaaaa"), link("bbbbbbbbbbb")]
    assert snap["items"][0]["error"] is None


def test_cancel_with_discard_and_remove(client, monkeypatch, tmp_path):
    started = threading.Event()

    def stuck(url, opts, temp_root, output_path, **kw):
        started.set()
        hook = opts["progress_hooks"][0]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            hook({"status": "downloading", "downloaded_bytes": 1, "total_bytes": 100, "speed": 1, "eta": 1})
            time.sleep(0.02)

    monkeypatch.setattr(jobs, "download_media", stuck)
    add(client, "aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc")
    snap = client.get("/api/queue").json()
    waiting = by_id(snap, "ccccccccccc")["id"]
    client.post("/api/queue/start")
    assert started.wait(3)
    first = by_id(client.get("/api/queue").json(), "aaaaaaaaaaa")["id"]
    assert client.post(f"/api/queue/items/{first}/cancel", json={"discard": True}).status_code == 200
    wait(client, lambda s: by_id(s, "aaaaaaaaaaa")["status"] == "cancelled")
    assert client.delete(f"/api/queue/items/{waiting}").status_code == 200
    client.post("/api/queue/pause")
    snap = wait(client, lambda s: s["state"] == "paused" and not s["counts"].get("downloading"))
    assert [i["url"][-11:] for i in snap["items"]] == ["aaaaaaaaaaa", "bbbbbbbbbbb"]
    assert client.delete("/api/queue/items/nope").status_code == 404
    assert client.post("/api/queue/clear-finished").json()["removed"] == 1


def test_disk_full_stops_the_queue(client, monkeypatch):
    def full(url, opts, temp_root, output_path, **kw):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(jobs, "download_media", full)
    add(client, "aaaaaaaaaaa", "bbbbbbbbbbb")
    client.post("/api/queue/start")
    snap = wait(client, lambda s: s["halted"] == "disk_full")
    assert statuses(snap) == ["failed", "queued"]
    assert snap["state"] == "paused"


# ── Saved state and restart ─────────────────────────────────────────────────
def test_state_is_saved_and_a_restart_recovers_it(client, app, monkeypatch):
    add(client, "aaaaaaaaaaa", "bbbbbbbbbbb")
    saved = json.loads((data_dir() / "queue.json").read_text(encoding="utf-8"))
    assert len(saved["items"]) == 2 and "percent" not in saved["items"][0]
    saved["items"][0]["status"] = "downloading"  # the app was closed in the middle of this one
    saved["items"].append({"id": "zz", "url": link("ccccccccccc"), "status": "fetching", "title": "", "fmt": "mp4"})
    (data_dir() / "queue.json").write_text(json.dumps(saved), encoding="utf-8")

    fresh = create_app(TOKEN, PORT)
    queue = fresh.state.ctx.queue
    items = {i["url"][-11:]: i["status"] for i in queue.snapshot()["items"]}
    assert items["aaaaaaaaaaa"] == "paused"
    assert items["bbbbbbbbbbb"] == "ready"
    time.sleep(0.5)  # the unfinished fetch is picked up again
    assert {i["url"][-11:]: i["status"] for i in queue.snapshot()["items"]}["ccccccccccc"] == "ready"
    assert not queue.active()  # nothing restarts by itself unless the setting is on


def test_auto_resume_setting_restarts_paused_items(client, monkeypatch):
    monkeypatch.setattr(jobs, "download_media", fake_download)
    add(client, "aaaaaaaaaaa")
    saved = json.loads((data_dir() / "queue.json").read_text(encoding="utf-8"))
    saved["items"][0]["status"] = "paused"
    (data_dir() / "queue.json").write_text(json.dumps(saved), encoding="utf-8")
    update_config({"queue_auto_resume": True})
    fresh = create_app(TOKEN, PORT)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and fresh.state.ctx.queue.snapshot()["items"][0]["status"] != "done":
        time.sleep(0.05)
    assert fresh.state.ctx.queue.snapshot()["items"][0]["status"] == "done"


def test_unreadable_state_file_starts_empty(tmp_path, app):
    (data_dir() / "queue.json").write_text("{not json", encoding="utf-8")
    q = QueueManager(app.state.ctx.hub, app.state.ctx.jobs, lambda url, source: (False, {}))
    assert q.snapshot()["items"] == []
    (data_dir() / "queue.json").write_text("[]", encoding="utf-8")
    assert QueueManager(app.state.ctx.hub, app.state.ctx.jobs, lambda url, source: (False, {})).snapshot()["items"] == []


def test_partials_endpoint_refuses_while_running(client, app, monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(jobs, "download_media", lambda *a, **k: (gate.wait(5), fake_download(*a, **k))[1])
    add(client, "aaaaaaaaaaa")
    client.post("/api/queue/start")
    wait(client, lambda s: statuses(s) == ["downloading"])
    assert client.delete("/api/queue/partials").status_code == 409
    gate.set()
    wait(client, lambda s: statuses(s) == ["done"])
    time.sleep(0.2)
    assert client.delete("/api/queue/partials").json()["partial_bytes"] == 0


def test_playlist_style_add_starts_by_itself_with_its_own_format_and_quality(client, monkeypatch):
    monkeypatch.setattr(jobs, "download_media", fake_download)
    add_result = client.post("/api/queue/items", json={
        "urls": [link("aaaaaaaaaaa"), link("bbbbbbbbbbb")], "quality": "480p", "start": True}).json()
    assert len(add_result["added"]) == 2
    snap = wait(client, lambda s: statuses(s) == ["done", "done"])  # no Start press needed
    assert [i["quality"] for i in snap["items"]] == ["480p", "480p"]


def test_partial_add_takes_what_fits(client):
    first = [link(f"v{n:010d}") for n in range(48)]
    client.post("/api/queue/items", json={"urls": first})
    wait(client, lambda s: all(st != "fetching" for st in statuses(s)), timeout=20)
    more = [link(f"w{n:010d}") for n in range(5)]
    assert client.post("/api/queue/items", json={"urls": more}).status_code == 422
    result = client.post("/api/queue/items", json={"urls": more, "partial": True}).json()
    assert len(result["added"]) == 2 and result["left_out"] == 3


# ── Downloads started on the Download page ──────────────────────────────────
def start_single(client, video_id="sssssssssss", **extra):
    body = {"url": link(video_id), "title": f"Single {video_id}", "fmt": "mp4", "quality": "720p",
            "channel": "Chan", "duration_text": "1:40", **extra}
    result = client.post("/api/downloads", json=body).json()
    assert result["status"] == "started", result
    return result["job"]


def cancellable(started):
    def run(url, opts, temp_root, output_path, **kw):
        started.set()
        hook = opts["progress_hooks"][0]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            hook({"status": "downloading", "downloaded_bytes": 10, "total_bytes": 100, "speed": 1, "eta": 1})
            time.sleep(0.02)
        raise AssertionError("never cancelled")

    return run


def test_single_download_is_tracked_and_leaves_the_queue_when_done(client, monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(jobs, "download_media", lambda *a, **k: (gate.wait(5), fake_download(*a, **k))[1])
    job = start_single(client)
    assert job["queue_item"] or client.get("/api/queue").json()["items"]
    snap = wait(client, lambda s: statuses(s) == ["downloading"])
    assert snap["items"][0]["title"] == "Single sssssssssss" and snap["items"][0]["quality"] == "720p"
    gate.set()
    wait(client, lambda s: s["items"] == [])  # it is in History, not in the queue list


def test_pause_a_single_download_then_continue_it_from_the_queue(client, monkeypatch):
    started, calls = threading.Event(), []

    def first_then_ok(url, opts, temp_root, output_path, **kw):
        calls.append(url)
        if len(calls) == 1:
            return cancellable(started)(url, opts, temp_root, output_path)
        return fake_download(url, opts, temp_root, output_path)

    monkeypatch.setattr(jobs, "download_media", first_then_ok)
    job = start_single(client)
    assert started.wait(3)
    assert client.post(f"/api/jobs/{job['id']}/pause").json()["status"] == "pausing"
    snap = wait(client, lambda s: statuses(s) == ["paused"])
    paused = snap["items"][0]
    fresh = client.get("/api/jobs").json()["jobs"][0]
    assert fresh["state"] == "cancelled" and fresh["message"] == "paused" and fresh["queue_item"] == paused["id"]

    assert client.post(f"/api/queue/items/{paused['id']}/resume").status_code == 200
    wait(client, lambda s: statuses(s) == ["done"])
    assert len(calls) == 2 and len(load_history()) == 1
    assert client.post(f"/api/queue/items/{paused['id']}/resume").status_code == 404  # no longer paused


def test_cancel_a_single_download_removes_it_from_the_queue(client, monkeypatch):
    started = threading.Event()
    monkeypatch.setattr(jobs, "download_media", cancellable(started))
    job = start_single(client)
    assert started.wait(3)
    client.post(f"/api/jobs/{job['id']}/cancel")
    wait(client, lambda s: s["items"] == [])


def test_pause_unknown_job_and_subtitles_only_are_not_tracked(client, monkeypatch):
    assert client.post("/api/jobs/nope/pause").status_code == 404

    def fake_subs(url, opts, temp_root, video_path):
        Path(video_path).parent.mkdir(parents=True, exist_ok=True)
        saved = Path(video_path).with_suffix(".en.srt")
        saved.write_text("1")
        return [str(saved)], {}

    monkeypatch.setattr(jobs, "download_subtitles_only", fake_subs)
    job = start_single(client, sub_manual=["en"], subtitles_only=True)
    assert job["queue_item"] == ""
    assert client.get("/api/queue").json()["items"] == []


def test_a_paused_single_survives_a_restart(client, monkeypatch):
    started = threading.Event()
    monkeypatch.setattr(jobs, "download_media", cancellable(started))
    job = start_single(client)
    assert started.wait(3)
    client.post(f"/api/jobs/{job['id']}/pause")
    wait(client, lambda s: statuses(s) == ["paused"])
    fresh = create_app(TOKEN, PORT)
    items = fresh.state.ctx.queue.snapshot()["items"]
    assert [i["status"] for i in items] == ["paused"] and items[0]["title"] == "Single sssssssssss"
