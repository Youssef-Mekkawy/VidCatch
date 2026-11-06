"""yt-dlp wrappers. No GUI code in here, so everything is easy to test and reuse."""

from __future__ import annotations

import glob
import hashlib
import os
import re
import shutil
import tempfile
import threading
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

import yt_dlp

from .system import detect_js_runtimes
from .utils import format_eta, format_speed, parse_height

T = TypeVar("T")

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")
MEDIA_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".mp3", ".m4a", ".opus", ".ogg"}
PROGRESS_INTERVAL = 0.1  # seconds between UI progress updates


class DownloadCancelled(Exception):
    """Raised from the progress hook when the user cancels."""


def clean_error(exc: BaseException) -> str:
    """yt-dlp error text without terminal colour codes or the 'ERROR:' prefix."""
    text = _ANSI_ESCAPE.sub("", str(exc)).strip()
    return text.removeprefix("ERROR:").strip() or exc.__class__.__name__


def needs_login(message: str) -> bool:
    """True when YouTube refuses the video until a signed-in session is supplied
    (bot check, age restriction, private or members-only video)."""
    lowered = message.lower()
    markers = (
        "not a bot", "use --cookies", "confirm your age", "private video",
        "members-only", "join this channel", "sign in",
        # YouTube hides private playlists (Watch Later, Liked videos) from signed-out requests
        # by pretending they do not exist.
        "playlist does not exist", "playlist is private",
    )
    return any(marker in lowered for marker in markers)


def login_reason(message: str) -> str:
    """Why sign-in is needed: 'age', 'private', 'members', 'bot' or 'login' (unknown)."""
    lowered = message.lower()
    if "confirm your age" in lowered or "age-restricted" in lowered or "inappropriate" in lowered:
        return "age"
    if "private video" in lowered or "playlist does not exist" in lowered or "playlist is private" in lowered:
        return "private"
    if "members-only" in lowered or "join this channel" in lowered:
        return "members"
    if "not a bot" in lowered:
        return "bot"
    return "login"


def cookie_error_kind(message: str) -> str | None:
    """Classify a failure to read a browser's cookies: 'locked', 'encrypted' or None."""
    lowered = message.lower()
    if "could not copy" in lowered and "cookie" in lowered:
        return "locked"
    if "decrypt" in lowered or "dpapi" in lowered:
        return "encrypted"
    return None


def build_subtitle_options(
    sub_manual: list[str], sub_auto: list[str], cookies: tuple[str, str] | None = None, js: bool = False
) -> dict:
    """yt-dlp options that fetch the chosen subtitle languages as .srt and nothing else."""
    opts = base_options(cookies, js)
    opts.update(
        {
            "noplaylist": True,
            "skip_download": True,
            "writesubtitles": bool(sub_manual),
            "writeautomaticsub": bool(sub_auto),
            "subtitleslangs": [*sub_manual, *sub_auto],
            "subtitlesformat": "srt/best",
            "postprocessors": [{"key": "FFmpegSubtitlesConvertor", "format": "srt"}],
        }
    )
    return opts


def subtitle_tracks(info: dict) -> dict[str, list[str]]:
    """Every subtitle language this video offers, in three groups.

    manual:     uploaded by the channel (English and Arabic first).
    auto:       automatic captions in the ORIGINAL spoken language.
    translated: YouTube's on-demand automatic translations into other languages. They are listed
                but marked as risky in the interface: fetching them is what triggers HTTP 429.
    """
    manual = [k for k in (info.get("subtitles") or {}) if k != "live_chat"]
    manual.sort(key=lambda k: (k.split("-")[0] not in ("en", "ar"), k.split("-")[0] != "en", k))
    captions = [k for k in (info.get("automatic_captions") or {}) if k != "live_chat"]
    auto = [k for k in captions if k.endswith("-orig")][:1]
    if not auto and info.get("language") in captions:
        auto = [info["language"]]
    original = {a.removesuffix("-orig") for a in auto}
    translated = [k for k in captions if k not in auto and k.removesuffix("-orig") not in original]
    translated.sort(key=lambda k: (k.split("-")[0] not in ("en", "ar"), k))
    return {"manual": manual[:60], "auto": auto, "translated": translated[:200]}


def is_subtitle_error(exc: BaseException) -> bool:
    """True when the download failed only because the subtitle files could not be fetched.

    YouTube rate-limits its subtitle endpoint (HTTP 429) much sooner than the video itself, so the
    video is still worth downloading without subtitles."""
    return "subtitles" in clean_error(exc).lower()


def should_retry_with_js(exc: BaseException) -> bool:
    """True for failures a JavaScript runtime can fix (blocked streams, missing formats)."""
    if isinstance(exc, DownloadCancelled):
        return False
    text = str(exc).lower()
    markers = (
        "403", "forbidden", "format is not available", "only images",
        "no video formats", "n challenge", "nsig", "signature",
    )
    return any(marker in text for marker in markers)


def with_js_fallback(run: Callable[[bool], T]) -> T:
    """Call run(False) first, exactly like plain yt-dlp. If that fails in a way a JS runtime can
    fix and one is installed, call run(True). Any other error is raised unchanged."""
    try:
        return run(False)
    except Exception as exc:
        if not (should_retry_with_js(exc) and detect_js_runtimes()):
            raise
    return run(True)


# ── Options ─────────────────────────────────────────────────────────────────
def base_options(cookies: tuple[str, str] | None = None, js: bool = False) -> dict:
    """Options shared by every yt-dlp call.

    js=False behaves like plain yt-dlp (what the first attempt uses). js=True also hands yt-dlp
    every installed JavaScript runtime; that is the retry used when YouTube refuses the plain
    request (HTTP 403 / missing formats), because it makes yt-dlp use the web player clients,
    which YouTube is also quicker to bot-check.

    cookies=(browser, profile) makes yt-dlp read that browser profile's cookies in memory for
    this request only (nothing is written to disk). It is only passed when YouTube demanded a
    sign-in and the user allowed it.
    """
    opts: dict = {
        "quiet": True,
        "noprogress": True,
        "socket_timeout": 20,
        "retries": 3,
        "fragment_retries": 3,
    }
    runtimes = detect_js_runtimes() if js else []
    if runtimes:
        opts["js_runtimes"] = {name: {} for name in runtimes}
    if cookies:
        browser, profile = cookies
        opts["cookiesfrombrowser"] = (browser, profile) if profile else (browser,)
    return opts


def build_download_options(
    fmt: str,
    quality: str | None,
    subtitles: str,
    hook: Callable[[dict], None],
    cookies: tuple[str, str] | None = None,
    js: bool = False,
    sub_manual: list[str] | None = None,
    sub_auto: list[str] | None = None,
) -> dict:
    """yt-dlp options for one download.

    fmt: 'mp4' or 'mp3'. quality: '720p', 'best' or None. subtitles: 'none'|'auto'|'manual'.
    The caller (download_media) adds the output template.
    """
    opts = base_options(cookies, js)
    opts.update(
        {
            "noplaylist": True,
            "progress_hooks": [hook],
            "concurrent_fragment_downloads": 4,
        }
    )

    if fmt == "mp3":
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
        ]
        return opts

    height = parse_height(quality)
    if height:
        opts["format"] = f"bestvideo[height<={height}]+bestaudio/best[height<={height}]"
    else:
        opts["format"] = "bestvideo+bestaudio/best"
    # Prefer MP4/M4A streams so the merged file plays everywhere.
    opts["format_sort"] = ["res", "ext:mp4:m4a"]
    opts["merge_output_format"] = "mp4"

    if sub_manual or sub_auto:
        # Exact languages the user ticked: channel-uploaded ones and/or automatic ones.
        opts["writesubtitles"] = bool(sub_manual)
        opts["writeautomaticsub"] = bool(sub_auto)
        opts["subtitleslangs"] = [*(sub_manual or []), *(sub_auto or [])]
        opts["subtitlesformat"] = "srt/best"
        opts["postprocessors"] = [{"key": "FFmpegSubtitlesConvertor", "format": "srt"}]
    elif subtitles in ("auto", "manual"):
        opts["writesubtitles"] = subtitles == "manual"
        opts["writeautomaticsub"] = subtitles == "auto"
        opts["subtitleslangs"] = ["en", "ar"]
        opts["subtitlesformat"] = "srt/best"
        opts["postprocessors"] = [{"key": "FFmpegSubtitlesConvertor", "format": "srt"}]
    return opts


# ── Metadata ────────────────────────────────────────────────────────────────
def fetch_video_info(
    url: str, cookies: tuple[str, str] | None = None
) -> tuple[bool, dict]:
    """Fetch metadata for a video or playlist URL.

    Returns (True, playlist_info) when the URL carries a playlist id, otherwise
    (False, video_info). Raises on network or yt-dlp errors.
    """
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    list_id = (query.get("list") or [None])[0]

    if list_id:
        extra = {"extract_flat": True, "noplaylist": False}
        target = f"https://www.youtube.com/playlist?list={list_id}"
    else:
        extra = {"noplaylist": True}
        target = url

    def attempt(js: bool) -> dict:
        opts = base_options(cookies, js)
        opts.update(extra)
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(target, download=False)

    return bool(list_id), with_js_fallback(attempt)


def entry_url(entry: dict) -> str:
    """Best URL for a (flat) playlist entry."""
    url = entry.get("url") or entry.get("webpage_url") or ""
    if url.startswith("http"):
        return url
    video_id = entry.get("id") or url
    return f"https://www.youtube.com/watch?v={video_id}" if video_id else ""


def entry_thumbnail(entry: dict) -> str:
    """Thumbnail URL for a playlist entry, falling back to YouTube's predictable one."""
    if entry.get("thumbnail"):
        return entry["thumbnail"]
    thumbs = entry.get("thumbnails") or []
    if thumbs and thumbs[-1].get("url"):
        return thumbs[-1]["url"]
    video_id = entry.get("id") or ""
    return f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg" if len(video_id) == 11 else ""


@dataclass
class FormatSummary:
    """What a video offers: available heights and rough sizes for the size estimate."""

    heights: list[int] = field(default_factory=list)  # descending
    video_sizes: dict[int, int] = field(default_factory=dict)
    audio_size: int = 0

    @property
    def labels(self) -> list[str]:
        return [f"{h}p" for h in self.heights]


def extract_formats(info: dict) -> FormatSummary:
    """Summarise a video's formats by height; sizes are best-effort estimates."""
    sizes: dict[int, int] = {}
    mp4_sizes: dict[int, int] = {}
    audio = 0
    for fmt in info.get("formats") or []:
        size = fmt.get("filesize") or fmt.get("filesize_approx") or 0
        height = fmt.get("height")
        vcodec = fmt.get("vcodec") or "none"
        acodec = fmt.get("acodec") or "none"
        if height and vcodec != "none":
            sizes[height] = max(sizes.get(height, 0), size)
            if fmt.get("ext") == "mp4":
                mp4_sizes[height] = max(mp4_sizes.get(height, 0), size)
        elif vcodec == "none" and acodec != "none":
            audio = max(audio, size)
    # Downloads prefer MP4 streams, so use their size when it is known.
    estimates = {h: mp4_sizes.get(h) or s for h, s in sizes.items()}
    return FormatSummary(sorted(estimates, reverse=True), estimates, audio)


def pick_quality(heights: list[int], preferred: str) -> str:
    """Choose a dropdown value: the preferred height, else the closest one below it.

    'best' (or no match at all) selects the highest available.
    """
    if not heights:
        return ""
    ordered = sorted(heights, reverse=True)
    wanted = parse_height(preferred)
    if wanted is None:
        return f"{ordered[0]}p"
    for height in ordered:
        if height <= wanted:
            return f"{height}p"
    return f"{ordered[-1]}p"


# ── Downloading ─────────────────────────────────────────────────────────────
def make_progress_hook(
    on_progress: Callable[[float, str, str], None],
    on_finish: Callable[[], None],
    cancel_event: threading.Event,
) -> Callable[[dict], None]:
    """Build a yt-dlp progress hook.

    on_progress(percent, speed_text, eta_text) is called at most every 0.1 s.
    on_finish() is called when a stream has been downloaded (before merging/converting).
    Setting cancel_event aborts the download with DownloadCancelled.
    """
    last_report = [0.0]

    def hook(d: dict) -> None:
        if cancel_event.is_set():
            raise DownloadCancelled("Cancelled.")

        status = d.get("status")
        if status == "downloading":
            now = time.monotonic()
            if now - last_report[0] < PROGRESS_INTERVAL:
                return
            last_report[0] = now

            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            if total:
                percent = done / total * 100
            elif d.get("fragment_count"):
                percent = (d.get("fragment_index") or 0) / d["fragment_count"] * 100
            else:
                percent = 0.0
            on_progress(
                max(0.0, min(100.0, percent)),
                format_speed(d.get("speed")),
                format_eta(d.get("eta")),
            )
        elif status == "finished":
            on_finish()

    return hook


def _locate_media(info: dict, job_dir: Path) -> Path:
    """Find the finished media file: ask yt-dlp first, then fall back to scanning."""
    for item in info.get("requested_downloads") or []:
        path = item.get("filepath")
        if path and os.path.isfile(path):
            return Path(path)
    candidates = [
        p for p in job_dir.iterdir() if p.is_file() and p.suffix.lower() in MEDIA_EXTENSIONS
    ]
    if not candidates:
        raise FileNotFoundError("No output file was produced by yt-dlp.")
    return max(candidates, key=lambda p: p.stat().st_size)


def _move_subtitles(job_dir: Path, final: Path) -> None:
    """Move .srt files next to the video as '<video name>.<lang>.srt'."""
    for srt in job_dir.glob("*.srt"):
        lang = srt.name[: -len(".srt")].rsplit(".", 1)[-1]
        target = final.with_name(f"{final.stem}.{lang}.srt")
        target.unlink(missing_ok=True)
        shutil.move(str(srt), str(target))


PARTIAL_PREFIX = "partial_"
PARTIAL_MAX_AGE_DAYS = 7
_RESUME_FAILURE = re.compile(r"416|requested range|unable to resume|corrupt|moov atom|invalid data", re.I)


def partial_key(url: str, opts: dict) -> str:
    """Stable name for one download (video + format + quality), so a later run finds its partial data."""
    match = re.search(r"(?:[?&]v=|youtu\.be/|/shorts/|/embed/)([\w-]{11})", url)
    video = match.group(1) if match else url.strip()
    raw = f"{video}|{opts.get('format', '')}|{opts.get('merge_output_format', '')}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def partial_path(temp_root: str | os.PathLike, key: str) -> Path:
    return Path(temp_root) / f"{PARTIAL_PREFIX}{key}"


def _tree_size(folder: Path) -> int:
    total = 0
    for item in folder.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def partial_usage(temp_root: str | os.PathLike) -> int:
    """Bytes used by unfinished downloads that can still be resumed."""
    root = Path(temp_root)
    if not root.is_dir():
        return 0
    return sum(_tree_size(c) for c in root.iterdir() if c.is_dir() and c.name.startswith(PARTIAL_PREFIX))


def discard_partial(temp_root: str | os.PathLike, key: str | None = None) -> None:
    """Delete one unfinished download (by key) or all of them."""
    root = Path(temp_root)
    if key is not None:
        shutil.rmtree(partial_path(root, key), ignore_errors=True)
        return
    if root.is_dir():
        for child in root.iterdir():
            if child.is_dir() and child.name.startswith(PARTIAL_PREFIX):
                shutil.rmtree(child, ignore_errors=True)


def partial_days_left(temp_root: str | os.PathLike, max_age_days: int = PARTIAL_MAX_AGE_DAYS) -> int | None:
    """Days until the oldest unfinished download is deleted (0 = today), or None when there is none."""
    root = Path(temp_root)
    if not root.is_dir():
        return None
    now = time.time()
    left: list[int] = []
    for child in root.iterdir():
        if not (child.is_dir() and child.name.startswith(PARTIAL_PREFIX)):
            continue
        try:
            newest = max([child.stat().st_mtime, *(f.stat().st_mtime for f in child.rglob("*"))])
        except OSError:
            continue
        left.append(max(0, max_age_days - int((now - newest) // 86400)))
    return min(left) if left else None


def cleanup_old_partials(temp_root: str | os.PathLike, max_age_days: int = PARTIAL_MAX_AGE_DAYS) -> int:
    """Delete unfinished downloads nobody touched for `max_age_days`. Returns how many were removed."""
    root = Path(temp_root)
    if not root.is_dir():
        return 0
    limit = time.time() - max_age_days * 86400
    removed = 0
    for child in root.iterdir():
        if not (child.is_dir() and child.name.startswith(PARTIAL_PREFIX)):
            continue
        try:
            newest = max([child.stat().st_mtime, *(f.stat().st_mtime for f in child.rglob("*"))])
        except OSError:
            continue
        if newest < limit:
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
    return removed


def _safe_move(source: Path, final: Path) -> None:
    """Put `source` at `final` so that `final` is either the old file or the complete new one.

    The file is first copied/moved next to the destination under a temporary name and then renamed
    over it, so a crash in the middle never leaves a half-written video under the real name."""
    final.parent.mkdir(parents=True, exist_ok=True)
    staging = final.with_name(final.name + ".tmp")
    staging.unlink(missing_ok=True)
    try:
        shutil.move(str(source), str(staging))
        os.replace(staging, final)
    finally:
        staging.unlink(missing_ok=True)


def download_media(
    url: str,
    opts: dict,
    temp_root: str | os.PathLike,
    output_path: str | os.PathLike,
    resume: bool = False,
) -> tuple[str, dict]:
    """Download one video/audio file, then move it to output_path.

    Returns (final_path, info). Raises DownloadCancelled / yt-dlp errors / FileNotFoundError.

    resume=False: a private temp folder that is always removed (a failed or cancelled download leaves
    nothing behind). resume=True: a stable folder per video+format+quality that is KEPT when the download
    is cancelled or fails, so the next run continues from the `.part` files; it is deleted after success.
    """
    Path(temp_root).mkdir(parents=True, exist_ok=True)
    if resume:
        job_dir = partial_path(temp_root, partial_key(url, opts))
        job_dir.mkdir(parents=True, exist_ok=True)
    else:
        job_dir = Path(tempfile.mkdtemp(prefix="job_", dir=temp_root))
    keep = resume
    try:
        run_opts = dict(opts)
        run_opts["outtmpl"] = str(job_dir / "%(id)s.%(ext)s")
        run_opts["continuedl"] = True
        had_data = resume and any(job_dir.iterdir())
        try:
            with yt_dlp.YoutubeDL(run_opts) as ydl:
                info = ydl.extract_info(url, download=True)
        except DownloadCancelled:
            raise
        except Exception as exc:
            if not (had_data and _RESUME_FAILURE.search(str(exc))):
                raise
            # The saved partial data is damaged or no longer matches the video: start that download again.
            shutil.rmtree(job_dir, ignore_errors=True)
            job_dir.mkdir(parents=True, exist_ok=True)
            with yt_dlp.YoutubeDL(run_opts) as ydl:
                info = ydl.extract_info(url, download=True)

        media = _locate_media(info, job_dir)
        final = Path(output_path)
        _safe_move(media, final)
        _move_subtitles(job_dir, final)
        keep = False
        return str(final), info
    finally:
        if not keep:
            shutil.rmtree(job_dir, ignore_errors=True)


def download_subtitles_only(
    url: str,
    opts: dict,
    temp_root: str | os.PathLike,
    video_path: str | os.PathLike,
) -> tuple[list[str], dict]:
    """Fetch only the subtitle files and save them as '<video name>.<lang>.srt' beside video_path.

    Returns (saved file paths, info). The video itself is not downloaded.
    """
    Path(temp_root).mkdir(parents=True, exist_ok=True)
    job_dir = Path(tempfile.mkdtemp(prefix="job_", dir=temp_root))
    try:
        run_opts = dict(opts)
        run_opts["skip_download"] = True
        run_opts["outtmpl"] = str(job_dir / "%(id)s.%(ext)s")
        with yt_dlp.YoutubeDL(run_opts) as ydl:
            info = ydl.extract_info(url, download=True)
        video = Path(video_path)
        if not list(job_dir.glob("*.srt")):
            raise FileNotFoundError("No subtitle files were produced for the chosen languages.")
        video.parent.mkdir(parents=True, exist_ok=True)
        _move_subtitles(job_dir, video)
        saved = sorted(str(p) for p in video.parent.glob(f"{glob.escape(video.stem)}.*.srt"))
        return saved, info
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


def cleanup_stale_temp(temp_root: str | os.PathLike) -> None:
    """Remove job folders left behind by a crash or a force-quit."""
    root = Path(temp_root)
    if not root.is_dir():
        return
    for child in root.iterdir():
        if child.is_dir() and child.name.startswith("job_"):
            shutil.rmtree(child, ignore_errors=True)
