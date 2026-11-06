import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ytdownloader.config import add_history_entry, load_history, update_config
from ytdownloader.downloader import DownloadCancelled
from ytdownloader.server import api, jobs
from ytdownloader.server.app import create_app
from ytdownloader.signin import Probe, Resolution, Source

TOKEN = "t0ken"
PORT = 8765
URL = "https://www.youtube.com/watch?v=abc123"


@pytest.fixture
def app():
    return create_app(TOKEN, PORT)


@pytest.fixture
def client(app):
    c = TestClient(app, base_url=f"http://127.0.0.1:{PORT}")
    c.headers["cookie"] = f"ytdl_session={TOKEN}"
    return c


@pytest.fixture
def download_dir(tmp_path):
    folder = tmp_path / "downloads"
    update_config({"download_dir": str(folder)})
    return folder


def wait_for(app, job_id, states=("done", "failed", "cancelled"), timeout=5.0):
    job = app.state.ctx.jobs.get(job_id)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if job.state in states:
            return job
        time.sleep(0.02)
    raise AssertionError(f"job stuck in {job.state}")


INFO = {
    "id": "abc123", "title": "Sample / video", "uploader": "Chan", "duration": 212,
    "view_count": 1_200_000, "thumbnail": "https://i.ytimg.com/vi/abc123/hq.jpg",
    "formats": [
        {"height": 1080, "vcodec": "avc1", "acodec": "none", "ext": "mp4", "filesize": 50_000_000},
        {"height": 720, "vcodec": "avc1", "acodec": "none", "ext": "mp4", "filesize": 30_000_000},
        {"vcodec": "none", "acodec": "mp4a", "filesize": 4_000_000},
    ],
}


def fake_download(url, opts, temp_root, output_path):
    """Stands in for yt-dlp: reports progress through the real hook, writes the file."""
    hook = opts["progress_hooks"][0]
    hook({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100,
          "speed": 1_048_576, "eta": 3})
    hook({"status": "finished"})
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * 10)
    return str(path), {"title": "Sample video", "uploader": "Chan", "thumbnail": ""}


# ── Status and settings ─────────────────────────────────────────────────────
def test_status_reports_environment(client):
    data = client.get("/api/status").json()
    assert data["app"] == "youtube-downloader"
    assert data["settings"]["language"] == "en"
    assert isinstance(data["browsers"], list) and isinstance(data["js_runtimes"], list)


def test_settings_roundtrip_and_validation(client):
    assert client.put("/api/settings", json={"language": "ar"}).json()["language"] == "ar"
    assert client.get("/api/settings").json()["language"] == "ar"
    assert client.put("/api/settings", json={"nope": 1}).status_code == 422
    assert client.put("/api/settings", json={"language": "fr"}).status_code == 422
    assert client.put("/api/settings", json={"auto_download": "yes"}).status_code == 422
    assert client.put("/api/settings", json={"cookies_profile": "../../etc"}).status_code == 422
    assert client.put("/api/settings", json={}).status_code == 422


def test_changing_browser_resets_profile(client):
    client.put("/api/settings", json={"cookies_browser": "firefox", "cookies_profile": "abc.default"})
    result = client.put("/api/settings", json={"cookies_browser": "chrome"}).json()
    assert result["cookies_browser"] == "chrome" and result["cookies_profile"] == ""


# ── Fetch ───────────────────────────────────────────────────────────────────
def test_fetch_video(client, monkeypatch):
    monkeypatch.setattr(api, "check_internet", lambda: True)
    monkeypatch.setattr(api, "fetch_video_info", lambda url: (False, INFO))
    data = client.post("/api/fetch", json={"url": URL}).json()
    assert data["status"] == "ok" and data["type"] == "video" and data["source"] is None
    video = data["video"]
    assert video["title"] == "Sample / video"
    assert video["qualities"] == ["1080p", "720p"]
    assert video["quality"] == "1080p"  # default is "best"
    assert video["duration_text"] == "3:32" and video["views_text"] == "1.2M"
    assert video["size_texts"]["720p"] == "28.6 MB" and "audio" in video["size_texts"]


def test_fetch_playlist(client, monkeypatch):
    monkeypatch.setattr(api, "check_internet", lambda: True)
    info = {"title": "Mix", "entries": [{"id": "a" * 11, "title": "One", "duration": 65}, None,
                                        {"id": "", "title": "no url"}]}
    monkeypatch.setattr(api, "fetch_video_info", lambda url: (True, info))
    data = client.post("/api/fetch", json={"url": URL + "&list=PL1"}).json()
    assert data["type"] == "playlist" and data["playlist"]["count"] == 1
    entry = data["playlist"]["entries"][0]
    assert entry["url"].endswith("a" * 11) and entry["duration_text"] == "1:05"


def test_fetch_outcomes(client, monkeypatch):
    monkeypatch.setattr(api, "check_internet", lambda: False)
    assert client.post("/api/fetch", json={"url": URL}).json()["status"] == "offline"

    monkeypatch.setattr(api, "check_internet", lambda: True)

    def sign_in(url):
        raise RuntimeError("ERROR: Sign in to confirm you're not a bot")

    monkeypatch.setattr(api, "fetch_video_info", sign_in)
    data = client.post("/api/fetch", json={"url": URL}).json()
    assert data["status"] == "needs_login" and data["reason"] == "bot"

    def broken(url):
        raise RuntimeError("ERROR: Video unavailable")

    monkeypatch.setattr(api, "fetch_video_info", broken)
    data = client.post("/api/fetch", json={"url": URL}).json()
    assert data == {"status": "error", "message": "Video unavailable", "problem": "E120"}


def test_fetch_with_sources(client, monkeypatch):
    monkeypatch.setattr(api, "check_internet", lambda: True)
    source = Source("firefox", "x.default", "default")
    monkeypatch.setattr(api, "fetch_with_sources", lambda url, srcs: (source, (False, INFO), ""))
    body = {"url": URL, "sources": [{"browser": "firefox", "profile": "x.default"}]}
    data = client.post("/api/fetch", json=body).json()
    assert data["status"] == "ok" and data["source"]["browser"] == "firefox"

    monkeypatch.setattr(
        api, "fetch_with_sources", lambda url, srcs: (None, None, "Private video. Sign in")
    )
    data = client.post("/api/fetch", json=body).json()
    assert data["status"] == "signin_failed" and data["kind"] == "rejected"
    assert data["reason"] == "private"


@pytest.mark.parametrize(
    "body",
    [
        {"url": "not a url"},
        {"url": "file:///etc/passwd"},
        {"url": "javascript:alert(1)"},
        {"url": URL, "sources": [{"browser": "netscape"}]},
        {"url": URL, "sources": [{"browser": "firefox", "profile": "/etc"}]},
        {"url": URL, "sources": [{"browser": "firefox", "profile": "..\\..\\x"}]},
    ],
)
def test_fetch_rejects_bad_input(client, body):
    assert client.post("/api/fetch", json=body).status_code == 422


# ── Sign-in flow ────────────────────────────────────────────────────────────
def test_signin_prepare_follows_consent(client, monkeypatch):
    assert client.post("/api/signin/prepare", json={}).json()["status"] == "consent_needed"

    update_config({"signin_consent": "never"})
    assert client.post("/api/signin/prepare", json={}).json()["status"] == "signin_off"

    update_config({"signin_consent": "allow"})
    a, b = Source("firefox", "a", "A"), Source("firefox", "b", "B")
    monkeypatch.setattr(api, "resolve_sources", lambda browser, profile: Resolution([a, b]))
    data = client.post("/api/signin/prepare", json={}).json()
    assert data["status"] == "ok" and len(data["sources"]) == 2
    data = client.post("/api/signin/prepare", json={"first_time": True}).json()
    assert data["status"] == "choose_account"

    update_config({"cookies_browser": "firefox"})  # a saved choice: no picker
    data = client.post("/api/signin/prepare", json={"first_time": True}).json()
    assert data["status"] == "ok"


def test_signin_prepare_reports_why_it_cannot_help(client, monkeypatch):
    update_config({"signin_consent": "allow"})
    monkeypatch.setattr(
        api, "resolve_sources", lambda b, p: Resolution([], "locked", error="Could not copy")
    )
    data = client.post("/api/signin/prepare", json={}).json()
    assert data == {"status": "signin_failed", "kind": "locked", "detail": "Could not copy"}


def test_signin_test_lists_probe_results(client, monkeypatch):
    source = Source("firefox", "x.default", "default")
    monkeypatch.setattr(api, "candidate_sources", lambda b, p: [source])
    monkeypatch.setattr(api, "probe_source", lambda s: Probe(s, True, True, 7))
    data = client.post("/api/signin/test").json()
    assert data["installed"] and data["results"][0]["signed_in"] is True
    assert data["results"][0]["cookies"] == 7


# ── Downloads ───────────────────────────────────────────────────────────────
def test_download_runs_and_records_history(client, app, monkeypatch, download_dir):
    monkeypatch.setattr(jobs, "download_media", fake_download)
    body = {"url": URL, "title": "Sample / video", "fmt": "mp4", "quality": "720p"}
    started = client.post("/api/downloads", json=body).json()
    assert started["status"] == "started"

    job = wait_for(app, started["job"]["id"])
    assert job.state == "done" and job.percent == 100
    assert job.public()["filename"] == "Sample _ video_720p.mp4"
    assert (download_dir / "Sample _ video_720p.mp4").read_bytes() == b"x" * 10
    assert load_history()[-1]["title"] == "Sample video"

    listed = client.get("/api/history").json()["entries"]
    assert listed[0]["exists"] is True


def test_download_mp3_ignores_quality(client, app, monkeypatch, download_dir):
    monkeypatch.setattr(jobs, "download_media", fake_download)
    body = {"url": URL, "title": "Song", "fmt": "mp3", "quality": "720p"}
    job = wait_for(app, client.post("/api/downloads", json=body).json()["job"]["id"])
    assert job.public()["filename"] == "Song.mp3"


def test_existing_file_needs_confirmation(client, app, monkeypatch, download_dir):
    monkeypatch.setattr(jobs, "download_media", fake_download)
    download_dir.mkdir(parents=True)
    (download_dir / "Clip_720p.mp4").write_bytes(b"old")
    body = {"url": URL, "title": "Clip", "fmt": "mp4", "quality": "720p"}

    data = client.post("/api/downloads", json=body).json()
    assert data["status"] == "exists" and data["filename"] == "Clip_720p.mp4"
    assert data["size"] > 0 and data["modified"] > 0
    assert (download_dir / "Clip_720p.mp4").read_bytes() == b"old"

    body["overwrite"] = True
    job = wait_for(app, client.post("/api/downloads", json=body).json()["job"]["id"])
    assert job.state == "done"
    assert (download_dir / "Clip_720p.mp4").read_bytes() == b"x" * 10


def test_only_one_download_at_a_time_and_cancel(client, app, monkeypatch, download_dir):
    release = threading.Event()

    def slow(url, opts, temp_root, output_path):
        hook = opts["progress_hooks"][0]
        while not release.is_set():
            hook({"status": "downloading", "downloaded_bytes": 1, "total_bytes": 100})
            time.sleep(0.02)
        raise AssertionError("should have been cancelled")

    monkeypatch.setattr(jobs, "download_media", slow)
    body = {"url": URL, "title": "A", "fmt": "mp4", "quality": "best"}
    job_id = client.post("/api/downloads", json=body).json()["job"]["id"]

    assert client.post("/api/downloads", json=body).status_code == 409
    assert client.post("/api/ffmpeg/install").status_code == 409

    assert client.post(f"/api/jobs/{job_id}/cancel").json()["status"] == "cancelling"
    job = wait_for(app, job_id)
    release.set()
    assert job.state == "cancelled"
    assert not any(download_dir.glob("*")) if download_dir.exists() else True
    assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 404  # already finished
    assert client.post("/api/downloads", json=body).status_code != 409  # free again


def test_failed_download_that_needs_sign_in_is_flagged(client, app, monkeypatch, download_dir):
    def refuse(url, opts, temp_root, output_path):
        raise RuntimeError("ERROR: Sign in to confirm your age. This video may be inappropriate")

    monkeypatch.setattr(jobs, "download_media", refuse)
    body = {"url": URL, "title": "A", "fmt": "mp4", "quality": "best"}
    job = wait_for(app, client.post("/api/downloads", json=body).json()["job"]["id"])
    assert job.state == "failed"
    assert job.error["code"] == "needs_login" and job.error["reason"] == "age"

    # The same failure while already signed in means this account cannot open the video.
    body["source"] = {"browser": "firefox", "profile": "x"}
    job = wait_for(app, client.post("/api/downloads", json=body).json()["job"]["id"])
    assert job.error["code"] == "signin_rejected"


def test_other_download_errors_are_plain_failures(client, app, monkeypatch, download_dir):
    def broken(url, opts, temp_root, output_path):
        raise RuntimeError("ERROR: Video unavailable")

    monkeypatch.setattr(jobs, "download_media", broken)
    body = {"url": URL, "title": "A", "fmt": "mp4"}
    job = wait_for(app, client.post("/api/downloads", json=body).json()["job"]["id"])
    assert job.error == {"code": "failed", "message": "Video unavailable", "problem": "E120"}


def test_download_validates_input(client):
    base = {"url": URL, "title": "A"}
    assert client.post("/api/downloads", json={**base, "fmt": "avi"}).status_code == 422
    assert client.post("/api/downloads", json={**base, "quality": "huge"}).status_code == 422
    assert client.post("/api/downloads", json={**base, "sub_auto": ["../x"]}).status_code == 422
    assert client.post("/api/downloads", json={**base, "url": "ftp://x"}).status_code == 422


# ── Playlists ───────────────────────────────────────────────────────────────
def test_playlist_download_counts_done_skipped_failed(client, app, monkeypatch, download_dir):
    download_dir.mkdir(parents=True)
    (download_dir / "Two_720p.mp4").write_bytes(b"old")

    def flaky(url, opts, temp_root, output_path):
        if "bad" in url:
            raise RuntimeError("ERROR: Video unavailable")
        return fake_download(url, opts, temp_root, output_path)

    monkeypatch.setattr(jobs, "download_media", flaky)
    body = {
        "title": "Mix", "fmt": "mp4", "quality": "720p",
        "entries": [
            {"title": "One", "url": "https://youtu.be/one"},
            {"title": "Two", "url": "https://youtu.be/two"},
            {"title": "Three", "url": "https://youtu.be/bad"},
        ],
    }
    job = wait_for(app, client.post("/api/playlists/download", json=body).json()["job"]["id"])
    assert job.state == "done" and job.percent == 100
    assert job.summary == {"done": 1, "skipped": 1, "failed": 1, "signin": 0}
    assert (download_dir / "One_720p.mp4").exists()
    assert (download_dir / "Two_720p.mp4").read_bytes() == b"old"  # not overwritten


def test_playlist_item_that_needs_sign_in_retries_with_cookies_only_when_allowed(
    client, app, monkeypatch, download_dir
):
    seen = []

    def gated(url, opts, temp_root, output_path):
        seen.append(opts.get("cookiesfrombrowser"))
        if not opts.get("cookiesfrombrowser"):
            raise RuntimeError("ERROR: Sign in to confirm you're not a bot")
        return fake_download(url, opts, temp_root, output_path)

    monkeypatch.setattr(jobs, "download_media", gated)
    body = {"title": "M", "fmt": "mp4", "entries": [{"title": "One", "url": "https://youtu.be/one"}]}

    # Consent still "ask": never touches cookies.
    job = wait_for(app, client.post("/api/playlists/download", json=body).json()["job"]["id"])
    assert job.summary["signin"] == 1 and seen == [None]

    update_config({"signin_consent": "allow"})
    monkeypatch.setattr(
        jobs, "resolve_sources", lambda b, p: Resolution([Source("firefox", "x", "x")])
    )
    seen.clear()
    job = wait_for(app, client.post("/api/playlists/download", json=body).json()["job"]["id"])
    assert job.summary["done"] == 1
    assert seen == [None, ("firefox", "x")]  # plain first, cookies only as the retry


def test_playlist_cancel(client, app, monkeypatch, download_dir):
    started = threading.Event()

    def blocking(url, opts, temp_root, output_path):
        hook = opts["progress_hooks"][0]
        started.set()
        while True:
            hook({"status": "downloading", "downloaded_bytes": 1, "total_bytes": 100})
            time.sleep(0.02)

    monkeypatch.setattr(jobs, "download_media", blocking)
    body = {"title": "M", "fmt": "mp3", "entries": [{"title": "A", "url": "https://youtu.be/a"}] * 2}
    job_id = client.post("/api/playlists/download", json=body).json()["job"]["id"]
    assert started.wait(2)
    client.post(f"/api/jobs/{job_id}/cancel")
    job = wait_for(app, job_id)
    assert job.state == "cancelled"
    assert issubclass(DownloadCancelled, Exception)


# ── History, folders, maintenance ───────────────────────────────────────────
def test_history_endpoints(client):
    for i in range(3):
        add_history_entry({"id": f"id{i}", "title": f"T{i}", "filepath": f"/nowhere/{i}.mp4"})
    entries = client.get("/api/history").json()["entries"]
    assert [e["id"] for e in entries] == ["id2", "id1", "id0"]  # newest first
    assert all(e["exists"] is False for e in entries)

    client.delete("/api/history/id1")
    assert [e["id"] for e in client.get("/api/history").json()["entries"]] == ["id2", "id0"]
    client.delete("/api/history")
    assert client.get("/api/history").json()["entries"] == []


def test_reveal_uses_the_path_from_history_only(client, monkeypatch, tmp_path):
    shown = []
    monkeypatch.setattr(api, "reveal_file", lambda p: shown.append(Path(p)))
    real = tmp_path / "v.mp4"
    real.write_bytes(b"x")
    add_history_entry({"id": "a", "title": "T", "filepath": str(real)})
    assert client.post("/api/history/a/reveal").json()["status"] == "ok"
    assert shown == [real]
    assert client.post("/api/history/missing/reveal").status_code == 404


def test_folder_pick_outcomes(client, monkeypatch):
    monkeypatch.setattr(api, "pick_folder", lambda initial: "/chosen")
    assert client.post("/api/folder/pick").json() == {"status": "ok", "path": "/chosen"}
    monkeypatch.setattr(api, "pick_folder", lambda initial: None)
    assert client.post("/api/folder/pick").json()["status"] == "cancelled"

    def no_dialog(initial):
        raise NotImplementedError

    monkeypatch.setattr(api, "pick_folder", no_dialog)
    assert client.post("/api/folder/pick").json()["status"] == "unavailable"


def test_update_check_and_install(client, app, monkeypatch):
    monkeypatch.setattr(api, "installed_version", lambda: "2026.01.01")
    monkeypatch.setattr(api, "fetch_latest_version", lambda: "2026.02.01")
    data = client.get("/api/updates").json()
    assert data["newer"] is True and data["latest"] == "2026.02.01"

    monkeypatch.setattr(api, "fetch_latest_version", lambda: None)  # offline
    assert client.get("/api/updates").json()["newer"] is False

    monkeypatch.setattr(jobs, "update_ytdlp", lambda: (True, "Successfully installed"))
    job = wait_for(app, client.post("/api/updates/install").json()["job"]["id"])
    assert job.state == "done" and job.message == "Successfully installed"

    monkeypatch.setattr(api, "can_self_update", lambda: False)
    assert client.post("/api/updates/install").status_code == 400


def test_ffmpeg_install_job(client, app, monkeypatch):
    monkeypatch.setattr(jobs, "install_ffmpeg", lambda: (True, "winget"))
    monkeypatch.setattr(jobs, "check_ffmpeg", lambda: True)
    job = wait_for(app, client.post("/api/ffmpeg/install").json()["job"]["id"])
    assert job.state == "done" and job.message == "winget"


def test_shutdown_and_restart_refuse_while_busy(app, monkeypatch, download_dir):
    calls = []
    app2 = create_app(TOKEN, PORT, shutdown=lambda: calls.append("stop"),
                      restart=lambda: calls.append("restart"))
    c = TestClient(app2, base_url=f"http://127.0.0.1:{PORT}")
    c.headers["cookie"] = f"ytdl_session={TOKEN}"
    assert c.post("/api/shutdown").json()["status"] == "stopping"
    assert c.post("/api/restart").json()["status"] == "restarting"
    assert calls == ["stop", "restart"]

    release = threading.Event()

    def slow(url, opts, temp_root, output_path):
        release.wait(2)
        return fake_download(url, opts, temp_root, output_path)

    monkeypatch.setattr(jobs, "download_media", slow)
    c.post("/api/downloads", json={"url": URL, "title": "A", "fmt": "mp4"})
    assert c.post("/api/shutdown").status_code == 409
    release.set()


def test_video_is_kept_when_only_the_subtitles_fail(app, client, download_dir, monkeypatch):
    """YouTube answers 429 for subtitles much sooner than for the video itself."""
    calls = []

    def flaky(url, opts, temp_root, output_path):
        calls.append(bool(opts.get("writeautomaticsub")))
        if opts.get("writeautomaticsub"):
            raise RuntimeError("Unable to download video subtitles for 'en': HTTP Error 429: Too Many Requests")
        return fake_download(url, opts, temp_root, output_path)

    monkeypatch.setattr(jobs, "download_media", flaky)
    body = {"url": URL, "title": "Clip", "fmt": "mp4", "quality": "720p", "sub_auto": ["ar-orig"]}
    job = wait_for(app, client.post("/api/downloads", json=body).json()["job"]["id"])
    assert job.state == "done" and job.message == "subtitles_skipped"
    assert calls == [True, False]


def test_subtitle_tracks_group_every_language():
    from ytdownloader.downloader import subtitle_tracks

    info = {
        "subtitles": {"fr": [], "ar": [], "en-GB": [], "live_chat": []},
        "automatic_captions": {"en": [], "ar-orig": [], "ar": [], "de": []},
    }
    assert subtitle_tracks(info) == {
        "manual": ["en-GB", "ar", "fr"], "auto": ["ar-orig"], "translated": ["en", "de"],
    }
    assert subtitle_tracks({"language": "ar", "automatic_captions": {"ar": [], "en": []}})["auto"] == ["ar"]
    assert subtitle_tracks({}) == {"manual": [], "auto": [], "translated": []}


def test_chosen_subtitles_go_with_the_video_into_a_folder(app, client, download_dir, monkeypatch):
    seen = {}

    def spy(url, opts, temp_root, output_path):
        seen.update(langs=opts.get("subtitleslangs"), manual=opts.get("writesubtitles"),
                    auto=opts.get("writeautomaticsub"), path=output_path)
        return fake_download(url, opts, temp_root, output_path)

    monkeypatch.setattr(jobs, "download_media", spy)
    body = {"url": URL, "title": "My: clip", "fmt": "mp4", "quality": "720p",
            "sub_manual": ["en"], "sub_auto": ["ar-orig"]}
    job = wait_for(app, client.post("/api/downloads", json=body).json()["job"]["id"])
    assert seen["langs"] == ["en", "ar-orig"] and seen["manual"] and seen["auto"]
    assert Path(job.filepath).parent == download_dir / "My_ clip"
    assert Path(job.filepath).exists()

    plain = {"url": URL, "title": "Other", "fmt": "mp4", "quality": "720p"}
    job = wait_for(app, client.post("/api/downloads", json=plain).json()["job"]["id"])
    assert Path(job.filepath).parent == download_dir  # no subtitles: no folder
    bad = {**body, "sub_manual": ["../x"]}
    assert client.post("/api/downloads", json=bad).status_code == 422


def test_subtitles_only_saves_beside_an_existing_video(app, client, download_dir, monkeypatch):
    calls = {}

    def fake_subs(url, opts, temp_root, video_path):
        calls.update(skip=opts.get("skip_download"), langs=opts.get("subtitleslangs"), path=Path(video_path))
        out = Path(video_path).with_name(f"{Path(video_path).stem}.ar.srt")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("1\n", encoding="utf-8")
        return [str(out)], {}

    monkeypatch.setattr(jobs, "download_subtitles_only", fake_subs)
    # The video was downloaded earlier, without subtitles, straight into the download folder.
    video = download_dir / "Clip_720p.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"x")

    body = {"url": URL, "title": "Clip", "fmt": "mp4", "quality": "720p",
            "sub_auto": ["ar-orig"], "subtitles_only": True}
    data = client.post("/api/downloads", json=body).json()
    assert data["status"] == "started"  # no "already exists" question for subtitles
    job = wait_for(app, data["job"]["id"])
    assert job.state == "done" and job.message == "subtitles_only"
    assert calls["skip"] and calls["langs"] == ["ar-orig"]
    assert calls["path"] == video
    assert (download_dir / "Clip_720p.ar.srt").exists()
    assert job.summary == {"files": ["Clip_720p.ar.srt"]}

    # Without a video on disk the subtitles go in the folder named after the video.
    video.unlink()
    job = wait_for(app, client.post("/api/downloads", json=body).json()["job"]["id"])
    assert calls["path"] == download_dir / "Clip" / "Clip_720p.mp4"
    assert job.state == "done"

    assert client.post("/api/downloads", json={**body, "sub_auto": []}).status_code == 422


def test_find_existing_video_prefers_exact_name_then_newest(tmp_path):
    from ytdownloader.utils import find_existing_video

    (tmp_path / "Clip").mkdir()
    old = tmp_path / "Clip_480p.mp4"
    old.write_bytes(b"a")
    assert find_existing_video(tmp_path, "Clip", "Clip_720p.mp4") == old
    exact = tmp_path / "Clip" / "Clip_720p.mp4"
    exact.write_bytes(b"b")
    assert find_existing_video(tmp_path, "Clip", "Clip_720p.mp4") == exact
    assert find_existing_video(tmp_path, "Nothing", "Nothing_720p.mp4") is None


def test_folder_open_uses_the_folder_of_a_finished_job(app, client, download_dir, monkeypatch):
    opened = []
    monkeypatch.setattr(api, "open_folder", lambda path: opened.append(Path(path)))
    monkeypatch.setattr(jobs, "download_media", fake_download)
    body = {"url": URL, "title": "Clip", "fmt": "mp4", "quality": "720p", "sub_auto": ["ar-orig"]}
    job = wait_for(app, client.post("/api/downloads", json=body).json()["job"]["id"])
    client.post(f"/api/folder/open?job_id={job.id}")
    client.post("/api/folder/open")
    assert opened == [download_dir / "Clip", download_dir]


def test_history_delete_file_removes_video_subtitles_and_empty_folder(client, download_dir):
    folder = download_dir / "My video"
    folder.mkdir(parents=True)
    video = folder / "My video.mp4"
    video.write_bytes(b"x")
    srt = folder / "My video.en.srt"
    srt.write_text("1")
    other = download_dir / "Other.mp4"
    other.write_bytes(b"x")
    add_history_entry({"id": "a", "title": "T", "filepath": str(video)})
    add_history_entry({"id": "b", "title": "O", "filepath": str(other)})

    result = client.delete("/api/history/a?delete_file=true").json()

    assert sorted(result["deleted"]) == ["My video.en.srt", "My video.mp4"]
    assert not folder.exists()
    assert other.exists()
    assert download_dir.exists()
    # Without the flag the file stays.
    client.delete("/api/history/b")
    assert other.exists()


def test_history_delete_file_ignores_non_media_paths(client, tmp_path):
    secret = tmp_path / "notes.txt"
    secret.write_text("keep")
    add_history_entry({"id": "a", "title": "T", "filepath": str(secret)})
    assert client.delete("/api/history/a?delete_file=true").json()["deleted"] == []
    assert secret.exists()


def test_history_prune_removes_only_missing_files(client, tmp_path):
    real = tmp_path / "real.mp4"
    real.write_bytes(b"x")
    add_history_entry({"id": "ok", "title": "A", "filepath": str(real)})
    add_history_entry({"id": "gone", "title": "B", "filepath": str(tmp_path / "gone.mp4")})
    assert client.post("/api/history/prune").json()["removed"] == 1
    assert [e["id"] for e in client.get("/api/history").json()["entries"]] == ["ok"]


def test_history_clear_all_can_delete_files(client, download_dir):
    download_dir.mkdir(parents=True)
    kept, gone = download_dir / "a.mp4", download_dir / "b.mp4"
    kept.write_bytes(b"x")
    gone.write_bytes(b"x")
    add_history_entry({"id": "a", "title": "A", "filepath": str(kept)})
    add_history_entry({"id": "b", "title": "B", "filepath": str(gone)})
    client.delete("/api/history")
    assert kept.exists() and gone.exists()
    add_history_entry({"id": "b", "title": "B", "filepath": str(gone)})
    assert client.delete("/api/history?delete_files=true").json()["deleted"] == 1
    assert not gone.exists() and kept.exists()
