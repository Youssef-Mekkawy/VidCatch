import threading
from pathlib import Path

import pytest

from ytdownloader import downloader
from ytdownloader.downloader import (
    DownloadCancelled,
    FormatSummary,
    base_options,
    build_download_options,
    clean_error,
    cleanup_stale_temp,
    download_media,
    entry_thumbnail,
    entry_url,
    extract_formats,
    fetch_video_info,
    make_progress_hook,
    pick_quality,
)


# ── Test double for yt_dlp.YoutubeDL ────────────────────────────────────────
class FakeYDL:
    """Minimal stand-in: records options, writes files where yt-dlp would."""

    instances: list["FakeYDL"] = []
    files: dict[str, bytes] = {"abc.mp4": b"video"}
    report_paths = True
    error: Exception | None = None

    def __init__(self, opts):
        self.opts = opts
        self.calls = []
        FakeYDL.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=True):
        self.calls.append((url, download))
        if FakeYDL.error:
            raise FakeYDL.error
        if not download:
            return {"title": "meta", "entries": [{"id": "1"}]}
        out_dir = Path(self.opts["outtmpl"]).parent
        written = []
        for name, data in FakeYDL.files.items():
            (out_dir / name).write_bytes(data)
            written.append(out_dir / name)
        info = {"id": "abc", "title": "Title"}
        if FakeYDL.report_paths:
            info["requested_downloads"] = [{"filepath": str(written[0])}]
        return info


@pytest.fixture
def fake_ydl(monkeypatch):
    FakeYDL.instances = []
    FakeYDL.files = {"abc.mp4": b"video"}
    FakeYDL.report_paths = True
    FakeYDL.error = None
    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(downloader, "detect_js_runtimes", lambda: [])
    return FakeYDL


def job_dirs(root: Path) -> list[Path]:
    return [p for p in root.iterdir()] if root.exists() else []


# ── Options ─────────────────────────────────────────────────────────────────
class TestOptions:
    def test_js_runtimes_passed_when_found(self, monkeypatch):
        monkeypatch.setattr(downloader, "detect_js_runtimes", lambda: ["node", "bun"])
        assert "js_runtimes" not in base_options()  # first attempt = plain yt-dlp
        assert base_options(js=True)["js_runtimes"] == {"node": {}, "bun": {}}

    def test_js_runtimes_omitted_when_none(self, monkeypatch):
        monkeypatch.setattr(downloader, "detect_js_runtimes", lambda: [])
        assert "js_runtimes" not in base_options()

    def test_mp3(self, fake_ydl):
        opts = build_download_options("mp3", None, "none", lambda d: None)
        assert opts["format"] == "bestaudio/best"
        assert opts["postprocessors"][0]["key"] == "FFmpegExtractAudio"
        assert opts["postprocessors"][0]["preferredcodec"] == "mp3"
        assert "merge_output_format" not in opts

    def test_mp4_with_height(self, fake_ydl):
        opts = build_download_options("mp4", "720p", "none", lambda d: None)
        assert opts["format"] == "bestvideo[height<=720]+bestaudio/best[height<=720]"
        assert opts["merge_output_format"] == "mp4"
        assert opts["noplaylist"] is True

    @pytest.mark.parametrize("quality", ["best", None])
    def test_mp4_best(self, fake_ydl, quality):
        opts = build_download_options("mp4", quality, "none", lambda d: None)
        assert opts["format"] == "bestvideo+bestaudio/best"

    def test_subtitles(self, fake_ydl):
        auto = build_download_options("mp4", "720p", "auto", lambda d: None)
        assert auto["writeautomaticsub"] is True and auto["writesubtitles"] is False
        manual = build_download_options("mp4", "720p", "manual", lambda d: None)
        assert manual["writesubtitles"] is True and manual["writeautomaticsub"] is False
        none = build_download_options("mp4", "720p", "none", lambda d: None)
        assert "writesubtitles" not in none

    def test_hook_is_registered(self, fake_ydl):
        hook = lambda d: None  # noqa: E731
        assert build_download_options("mp4", None, "none", hook)["progress_hooks"] == [hook]


# ── Metadata ────────────────────────────────────────────────────────────────
class TestFetch:
    def test_single_video(self, fake_ydl):
        is_playlist, info = fetch_video_info("https://youtu.be/abc")
        assert is_playlist is False
        assert info["title"] == "meta"
        ydl = fake_ydl.instances[-1]
        assert ydl.calls == [("https://youtu.be/abc", False)]
        assert ydl.opts["noplaylist"] is True

    def test_playlist_url_is_normalised(self, fake_ydl):
        is_playlist, _ = fetch_video_info("https://www.youtube.com/watch?v=abc&list=PL123")
        assert is_playlist is True
        ydl = fake_ydl.instances[-1]
        assert ydl.calls == [("https://www.youtube.com/playlist?list=PL123", False)]
        assert ydl.opts["extract_flat"] is True


class TestEntries:
    def test_entry_url(self):
        assert entry_url({"url": "https://x/y"}) == "https://x/y"
        assert entry_url({"webpage_url": "https://x/z"}) == "https://x/z"
        assert entry_url({"id": "abcdefghijk"}) == "https://www.youtube.com/watch?v=abcdefghijk"
        assert entry_url({}) == ""

    def test_entry_thumbnail(self):
        assert entry_thumbnail({"thumbnail": "t1"}) == "t1"
        assert entry_thumbnail({"thumbnails": [{"url": "a"}, {"url": "b"}]}) == "b"
        assert entry_thumbnail({"id": "abcdefghijk"}).endswith("/abcdefghijk/mqdefault.jpg")
        assert entry_thumbnail({"id": "short"}) == ""


class TestFormats:
    INFO = {
        "formats": [
            {"height": 1080, "vcodec": "vp9", "acodec": "none", "ext": "webm", "filesize": 90},
            {"height": 1080, "vcodec": "avc1", "acodec": "none", "ext": "mp4", "filesize": 70},
            {"height": 720, "vcodec": "avc1", "acodec": "none", "ext": "mp4",
             "filesize_approx": 40},
            {"height": 720, "vcodec": "avc1", "acodec": "mp4a", "ext": "mp4", "filesize": 55},
            {"vcodec": "none", "acodec": "opus", "ext": "webm", "filesize": 8},
            {"vcodec": "none", "acodec": "mp4a", "ext": "m4a", "filesize": 12},
            {"height": 90, "vcodec": "none", "acodec": "none", "ext": "mhtml"},  # storyboard
        ]
    }

    def test_heights_sorted_descending_without_storyboards(self):
        summary = extract_formats(self.INFO)
        assert summary.heights == [1080, 720]
        assert summary.labels == ["1080p", "720p"]

    def test_sizes_prefer_mp4_and_best_audio(self):
        summary = extract_formats(self.INFO)
        assert summary.video_sizes[1080] == 70  # mp4, not the larger webm
        assert summary.video_sizes[720] == 55
        assert summary.audio_size == 12

    def test_empty(self):
        assert extract_formats({}) == FormatSummary()


class TestPickQuality:
    HEIGHTS = [1080, 720, 360]

    def test_best_is_highest(self):
        assert pick_quality(self.HEIGHTS, "best") == "1080p"

    def test_exact_match(self):
        assert pick_quality(self.HEIGHTS, "720p") == "720p"

    def test_closest_below(self):
        assert pick_quality(self.HEIGHTS, "480p") == "360p"

    def test_nothing_below_uses_lowest(self):
        assert pick_quality([720, 480], "360p") == "480p"

    def test_no_formats(self):
        assert pick_quality([], "best") == ""


# ── Progress hook ───────────────────────────────────────────────────────────
class TestProgressHook:
    @staticmethod
    def make(cancel=None):
        events = {"progress": [], "finished": 0}
        hook = make_progress_hook(
            lambda pct, speed, eta: events["progress"].append((pct, speed, eta)),
            lambda: events.__setitem__("finished", events["finished"] + 1),
            cancel or threading.Event(),
        )
        return hook, events

    @pytest.fixture
    def clock(self, monkeypatch):
        now = [100.0]
        monkeypatch.setattr(downloader.time, "monotonic", lambda: now[0])
        return now

    def test_reports_percent_speed_and_eta(self, clock):
        hook, events = self.make()
        hook({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 200,
              "speed": 2 * 1024**2, "eta": 75})
        assert events["progress"] == [(25.0, "2.0 MB/s", "1:15")]

    def test_uses_estimate_then_fragments(self, clock):
        hook, events = self.make()
        hook({"status": "downloading", "downloaded_bytes": 30, "total_bytes_estimate": 60})
        clock[0] += 1
        hook({"status": "downloading", "fragment_index": 3, "fragment_count": 12})
        assert [p for p, _, _ in events["progress"]] == [50.0, 25.0]

    def test_percent_is_clamped(self, clock):
        hook, events = self.make()
        hook({"status": "downloading", "downloaded_bytes": 500, "total_bytes": 100})
        assert events["progress"][0][0] == 100.0

    def test_updates_are_throttled(self, clock):
        hook, events = self.make()
        for _ in range(5):
            hook({"status": "downloading", "downloaded_bytes": 1, "total_bytes": 10})
        assert len(events["progress"]) == 1
        clock[0] += 0.5
        hook({"status": "downloading", "downloaded_bytes": 2, "total_bytes": 10})
        assert len(events["progress"]) == 2

    def test_finished_is_not_throttled(self, clock):
        hook, events = self.make()
        hook({"status": "downloading", "downloaded_bytes": 1, "total_bytes": 10})
        hook({"status": "finished"})
        hook({"status": "finished"})
        assert events["finished"] == 2

    def test_cancel_raises(self, clock):
        cancel = threading.Event()
        hook, _ = self.make(cancel)
        cancel.set()
        with pytest.raises(DownloadCancelled):
            hook({"status": "downloading", "downloaded_bytes": 1, "total_bytes": 2})


# ── download_media ──────────────────────────────────────────────────────────
class TestDownloadMedia:
    def test_moves_file_and_removes_temp(self, fake_ydl, tmp_path):
        temp, out = tmp_path / "temp", tmp_path / "out" / "Final.mp4"
        path, info = download_media("http://x", {"format": "best"}, temp, out)
        assert Path(path) == out
        assert out.read_bytes() == b"video"
        assert info["id"] == "abc"
        assert job_dirs(temp) == []

    def test_uses_private_output_template(self, fake_ydl, tmp_path):
        opts = {"format": "best"}
        download_media("http://x", opts, tmp_path / "temp", tmp_path / "o.mp4")
        assert "outtmpl" not in opts  # caller's dict is not modified
        assert fake_ydl.instances[-1].opts["outtmpl"].endswith("%(id)s.%(ext)s")

    def test_overwrites_existing_file(self, fake_ydl, tmp_path):
        out = tmp_path / "o.mp4"
        out.write_bytes(b"old")
        download_media("http://x", {}, tmp_path / "temp", out)
        assert out.read_bytes() == b"video"

    def test_picks_the_media_file_not_the_subtitle(self, fake_ydl, tmp_path):
        fake_ydl.files = {"abc.en.srt": b"1\n00:00", "abc.mp4": b"v" * 100}
        fake_ydl.report_paths = False  # force the directory-scan fallback
        out = tmp_path / "o.mp4"
        download_media("http://x", {}, tmp_path / "temp", out)
        assert out.read_bytes() == b"v" * 100

    def test_subtitles_are_moved_next_to_the_video(self, fake_ydl, tmp_path):
        fake_ydl.files = {"abc.mp4": b"v", "abc.en.srt": b"en", "abc.ar.srt": b"ar"}
        out = tmp_path / "out" / "Clip_720p.mp4"
        download_media("http://x", {}, tmp_path / "temp", out)
        assert (out.parent / "Clip_720p.en.srt").read_bytes() == b"en"
        assert (out.parent / "Clip_720p.ar.srt").read_bytes() == b"ar"

    def test_no_output_raises_and_cleans_up(self, fake_ydl, tmp_path):
        fake_ydl.files = {"abc.part": b"half"}
        fake_ydl.report_paths = False
        temp = tmp_path / "temp"
        with pytest.raises(FileNotFoundError):
            download_media("http://x", {}, temp, tmp_path / "o.mp4")
        assert job_dirs(temp) == []

    def test_errors_propagate_and_clean_up(self, fake_ydl, tmp_path):
        fake_ydl.error = DownloadCancelled("Cancelled.")
        temp = tmp_path / "temp"
        with pytest.raises(DownloadCancelled):
            download_media("http://x", {}, temp, tmp_path / "o.mp4")
        assert job_dirs(temp) == []
        assert not (tmp_path / "o.mp4").exists()


def test_cleanup_stale_temp(tmp_path):
    (tmp_path / "job_old1").mkdir()
    (tmp_path / "job_old1" / "x.part").write_bytes(b"x")
    (tmp_path / "keep_me").mkdir()
    cleanup_stale_temp(tmp_path)
    assert not (tmp_path / "job_old1").exists()
    assert (tmp_path / "keep_me").exists()
    cleanup_stale_temp(tmp_path / "does_not_exist")  # must not raise


def test_clean_error_strips_colour_codes_and_prefix():
    exc = Exception("\x1b[0;31mERROR:\x1b[0m [youtube] abc: HTTP Error 403: Forbidden")
    assert clean_error(exc) == "[youtube] abc: HTTP Error 403: Forbidden"
    assert clean_error(Exception("")) == "Exception"


# ── Cookies / sign-in ───────────────────────────────────────────────────────
def test_base_options_without_cookies():
    opts = downloader.base_options()
    assert "cookiesfrombrowser" not in opts and "cookiefile" not in opts


def test_base_options_browser_profile_cookies():
    assert downloader.base_options(("firefox", "x.default"))["cookiesfrombrowser"] == (
        "firefox",
        "x.default",
    )
    assert downloader.base_options(("edge", ""))["cookiesfrombrowser"] == ("edge",)


def test_download_options_pass_cookies_through():
    opts = downloader.build_download_options(
        "mp4", "720p", "none", lambda d: None, cookies=("brave", "Default")
    )
    assert opts["cookiesfrombrowser"] == ("brave", "Default")


@pytest.mark.parametrize(
    ("message", "reason"),
    [
        ("Sign in to confirm you’re not a bot. Use --cookies-from-browser", "bot"),
        ("Sign in to confirm your age. This video may be inappropriate", "age"),
        ("Private video. Sign in if you've been granted access to this video", "private"),
        ("Join this channel to get access to members-only content", "members"),
        ("Sign in to continue", "login"),
    ],
)
def test_needs_login_and_reason(message, reason):
    assert downloader.needs_login(message)
    assert downloader.login_reason(message) == reason


def test_needs_login_false_for_other_errors():
    assert not downloader.needs_login("HTTP Error 403: Forbidden")
    assert not downloader.needs_login("Video unavailable")


def test_cookie_error_kind():
    assert downloader.cookie_error_kind("Could not copy Chrome cookie database. See ...") == "locked"
    assert downloader.cookie_error_kind("Failed to decrypt with DPAPI") == "encrypted"
    assert downloader.cookie_error_kind("HTTP Error 403") is None


# ── JS runtime fallback ─────────────────────────────────────────────────────
def test_fallback_not_used_when_plain_attempt_works():
    calls = []
    assert downloader.with_js_fallback(lambda js: calls.append(js) or "ok") == "ok"
    assert calls == [False]


def test_fallback_retries_with_js_on_403(monkeypatch):
    monkeypatch.setattr(downloader, "detect_js_runtimes", lambda: ["deno"])
    calls = []

    def run(js):
        calls.append(js)
        if not js:
            raise RuntimeError("HTTP Error 403: Forbidden")
        return "ok"

    assert downloader.with_js_fallback(run) == "ok"
    assert calls == [False, True]


def test_fallback_skipped_without_runtime(monkeypatch):
    monkeypatch.setattr(downloader, "detect_js_runtimes", lambda: [])
    calls = []

    def run(js):
        calls.append(js)
        raise RuntimeError("HTTP Error 403: Forbidden")

    with pytest.raises(RuntimeError):
        downloader.with_js_fallback(run)
    assert calls == [False]


def test_fallback_does_not_retry_cancel_or_unrelated_errors(monkeypatch):
    monkeypatch.setattr(downloader, "detect_js_runtimes", lambda: ["deno"])
    for exc in (downloader.DownloadCancelled(), RuntimeError("Video unavailable")):
        calls = []

        def run(js, exc=exc, calls=calls):
            calls.append(js)
            raise exc

        with pytest.raises(type(exc)):
            downloader.with_js_fallback(run)
        assert calls == [False]
