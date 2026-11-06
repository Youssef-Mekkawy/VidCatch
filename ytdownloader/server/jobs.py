"""Background jobs: single downloads, playlist downloads, FFmpeg install and yt-dlp update.

A job runs in its own thread and reports through `Job.set(...)`, which pushes the new state to every
connected browser. The job logic is the same as the old desktop app's workers; only the reporting
changed (events instead of widget updates).

Only one "exclusive" job (download, playlist, FFmpeg install, yt-dlp update) runs at a time. They
all touch shared files or the yt-dlp install, and the UI shows one progress area.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..config import (
    add_history_entry,
    load_config,
    make_history_entry,
    temp_dir,
)
from ..downloader import (
    DownloadCancelled,
    build_download_options,
    build_subtitle_options,
    clean_error,
    download_media,
    download_subtitles_only,
    is_subtitle_error,
    login_reason,
    make_progress_hook,
    needs_login,
    with_js_fallback,
)
from ..errors import classify
from ..signin import resolve_sources
from ..system import check_ffmpeg, install_ffmpeg, open_folder
from ..updates import update_ytdlp
from ..utils import output_filename
from .events import EventHub

EXCLUSIVE_KINDS = frozenset({"video", "playlist", "ffmpeg", "update"})
FINISHED_STATES = frozenset({"done", "failed", "cancelled"})
KEEP_FINISHED = 20


class Busy(Exception):
    """Another exclusive job is still running."""


@dataclass
class Job:
    kind: str
    title: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    state: str = "running"  # running | done | failed | cancelled
    phase: str = ""  # "" | downloading | converting
    percent: float = 0.0  # 0-100; for a playlist this is the overall progress
    item_percent: float = 0.0  # playlist: progress of the current video
    speed: str = ""
    eta: str = ""
    current: int = 0  # playlist: 1-based index of the video being downloaded
    total: int = 0
    message: str = ""
    error: dict | None = None  # {"code", "message", "reason"?}
    summary: dict | None = None  # playlist: {"done", "skipped", "failed", "signin"}
    filepath: str = ""
    cancelling: bool = False
    started: float = field(default_factory=time.time)
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    finished: threading.Event = field(default_factory=threading.Event, repr=False)  # set when the thread ends
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _on_change: Callable[[Job], None] | None = field(default=None, repr=False)

    def set(self, **fields) -> None:
        with self._lock:
            for key, value in fields.items():
                setattr(self, key, value)
        if self._on_change:
            self._on_change(self)

    def public(self) -> dict:
        with self._lock:
            return {
                "id": self.id,
                "kind": self.kind,
                "title": self.title,
                "state": self.state,
                "phase": self.phase,
                "percent": round(self.percent, 1),
                "item_percent": round(self.item_percent, 1),
                "speed": self.speed,
                "eta": self.eta,
                "current": self.current,
                "total": self.total,
                "message": self.message,
                "error": self.error,
                "summary": self.summary,
                "filepath": self.filepath,
                "filename": Path(self.filepath).name if self.filepath else "",
                "cancelling": self.cancelling,
            }


@dataclass
class VideoParams:
    url: str
    fmt: str
    quality: str | None
    final: str
    cookies: tuple[str, str] | None = None
    sub_manual: tuple[str, ...] = ()
    sub_auto: tuple[str, ...] = ()
    subtitles_only: bool = False


@dataclass
class PlaylistParams:
    entries: list[dict]  # [{"title", "url"}]
    fmt: str
    quality: str | None
    out_dir: str
    overwrite: bool


class JobManager:
    def __init__(self, hub: EventHub) -> None:
        self.hub = hub
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    # ── Registry ────────────────────────────────────────────────────────────
    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def snapshot(self) -> list[dict]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.started)
        return [job.public() for job in jobs]

    def busy(self) -> bool:
        with self._lock:
            return any(
                j.kind in EXCLUSIVE_KINDS and j.state not in FINISHED_STATES
                for j in self._jobs.values()
            )

    def any_running(self) -> bool:
        with self._lock:
            return any(j.state not in FINISHED_STATES for j in self._jobs.values())

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job is None or job.state in FINISHED_STATES:
            return False
        job.cancel_event.set()
        job.set(cancelling=True)
        return True

    def _publish(self, job: Job) -> None:
        self.hub.publish({"type": "job", "job": job.public()})

    def _prune(self) -> None:
        with self._lock:
            finished = [j for j in self._jobs.values() if j.state in FINISHED_STATES]
            finished.sort(key=lambda j: j.started)
            for job in finished[:-KEEP_FINISHED]:
                del self._jobs[job.id]

    def _start(self, job: Job, target: Callable[[Job], None]) -> Job:
        with self._lock:
            if job.kind in EXCLUSIVE_KINDS and any(
                j.kind in EXCLUSIVE_KINDS and j.state not in FINISHED_STATES
                for j in self._jobs.values()
            ):
                raise Busy()
            job._on_change = self._publish
            self._jobs[job.id] = job

        def runner() -> None:
            try:
                target(job)
            except Exception as exc:  # a bug must end the job, never hang the UI
                job.set(
                    state="failed", phase="", speed="", eta="",
                    error={"code": "internal", "message": clean_error(exc), "problem": "E910"},
                )
            finally:
                job.finished.set()
                self._prune()

        self._publish(job)
        threading.Thread(target=runner, daemon=True, name=f"job-{job.kind}").start()
        return job

    # ── Public starters ─────────────────────────────────────────────────────
    def start_video(self, title: str, params: VideoParams) -> Job:
        job = Job(kind="video", title=title)
        return self._start(job, lambda j: self._run_video(j, params))

    def start_playlist(self, title: str, params: PlaylistParams) -> Job:
        job = Job(kind="playlist", title=title, total=len(params.entries))
        return self._start(job, lambda j: self._run_playlist(j, params))

    def start_ffmpeg_install(self) -> Job:
        return self._start(Job(kind="ffmpeg"), self._run_ffmpeg)

    def start_update(self) -> Job:
        return self._start(Job(kind="update"), self._run_update)

    # ── Workers ─────────────────────────────────────────────────────────────
    def _record_history(self, info: dict, url: str, fmt: str, quality: str | None, path: str) -> None:
        add_history_entry(make_history_entry(info, url, fmt, quality, path))
        self.hub.publish({"type": "history_changed"})

    def _run_subtitles(self, job: Job, p: VideoParams) -> None:
        job.set(percent=0.0, phase="downloading")

        def run(js: bool):
            opts = build_subtitle_options(list(p.sub_manual), list(p.sub_auto), p.cookies, js)
            return download_subtitles_only(p.url, opts, temp_dir(), p.final)

        try:
            saved, _info = with_js_fallback(run)
        except Exception as exc:
            self._fail(job, exc, signed_in=p.cookies is not None)
            return
        job.set(
            state="done", phase="", percent=100.0, speed="", eta="", filepath=saved[0],
            message="subtitles_only", summary={"files": [Path(f).name for f in saved]},
        )
        if load_config()["open_folder"]:
            try:
                open_folder(Path(saved[0]).parent)
            except OSError:
                pass

    def _run_video(self, job: Job, p: VideoParams) -> None:
        if p.subtitles_only:
            self._run_subtitles(job, p)
            return
        hook = make_progress_hook(
            on_progress=lambda pct, speed, eta: job.set(
                percent=pct, speed=speed, eta=eta, phase="downloading"
            ),
            on_finish=lambda: job.set(phase="converting", speed="", eta=""),
            cancel_event=job.cancel_event,
        )

        def attempt(with_subtitles: bool):
            def run(js: bool):
                opts = build_download_options(
                    p.fmt, p.quality, "none", hook, cookies=p.cookies, js=js,
                    sub_manual=list(p.sub_manual) if with_subtitles else None,
                    sub_auto=list(p.sub_auto) if with_subtitles else None,
                )
                return download_media(p.url, opts, temp_dir(), p.final, resume=True)

            return run

        subtitles_skipped = False
        try:
            try:
                path, info = with_js_fallback(attempt(True))
            except Exception as exc:
                if not (p.sub_manual or p.sub_auto) or job.cancel_event.is_set() or not is_subtitle_error(exc):
                    raise
                # YouTube refused the subtitle files (usually HTTP 429): keep the video.
                subtitles_skipped = True
                job.set(percent=0.0, phase="downloading")
                path, info = with_js_fallback(attempt(False))
        except Exception as exc:
            self._fail(job, exc, signed_in=p.cookies is not None)
            return

        self._record_history(info, p.url, p.fmt, p.quality, path)
        job.set(
            state="done", phase="", percent=100.0, speed="", eta="", filepath=path,
            message="subtitles_skipped" if subtitles_skipped else "",
        )
        if load_config()["open_folder"]:
            try:
                open_folder(Path(path).parent)
            except OSError:
                pass

    @staticmethod
    def _fail(job: Job, exc: BaseException, signed_in: bool) -> None:
        if job.cancel_event.is_set() or isinstance(exc, DownloadCancelled):
            job.set(state="cancelled", phase="", percent=0.0, speed="", eta="")
            return
        message = clean_error(exc)
        error: dict = {"code": "failed", "message": message, "problem": classify(message)}
        if needs_login(message):
            error = {
                # "signin_rejected": we were already signed in and YouTube still said no.
                "code": "signin_rejected" if signed_in else "needs_login",
                "reason": login_reason(message),
                "message": message,
                "problem": "E114" if signed_in else classify(message),
            }
        job.set(state="failed", phase="", speed="", eta="", error=error)

    def _run_playlist(self, job: Job, p: PlaylistParams) -> None:
        total = len(p.entries)
        done = skipped = failed = signin = 0
        consent_allow = load_config()["signin_consent"] == "allow"
        looked_up, found_cookies = False, None  # sign-in is resolved lazily, once

        for n, entry in enumerate(p.entries):
            if job.cancel_event.is_set():
                break
            title = entry.get("title") or f"video_{n + 1}"
            url = entry.get("url") or ""
            job.set(
                current=n + 1, total=total, title=title, phase="downloading",
                percent=n / total * 100, item_percent=0.0, speed="", eta="",
            )
            if not url:
                failed += 1
                continue

            final = Path(p.out_dir) / output_filename(title, p.fmt, p.quality)
            if final.exists() and not p.overwrite:
                skipped += 1
                job.set(percent=(n + 1) / total * 100)
                continue

            hook = make_progress_hook(
                on_progress=lambda pct, speed, eta, n=n: job.set(
                    percent=(n + pct / 100) / total * 100, item_percent=pct,
                    speed=speed, eta=eta, phase="downloading",
                ),
                on_finish=lambda: job.set(phase="converting", speed="", eta=""),
                cancel_event=job.cancel_event,
            )

            cookies_now = None  # every item starts without cookies
            outcome, info, path = "failed", {}, ""
            for attempt_no in range(2):
                try:
                    path, info = self._download_item(url, p, final, hook, cookies_now)
                    outcome = "done"
                    break
                except Exception as exc:
                    if job.cancel_event.is_set() or isinstance(exc, DownloadCancelled):
                        outcome = "cancelled"
                        break
                    if not needs_login(clean_error(exc)):
                        break
                    outcome = "signin"
                    if attempt_no == 0 and consent_allow:
                        if not looked_up:
                            looked_up = True
                            cfg = load_config()
                            found = resolve_sources(cfg["cookies_browser"], cfg["cookies_profile"])
                            found_cookies = found.usable[0].cookies if found.usable else None
                        if found_cookies:
                            cookies_now = found_cookies
                            continue
                    break

            if outcome == "cancelled":
                break
            if outcome == "done":
                done += 1
                self._record_history(info, url, p.fmt, p.quality, path)
            elif outcome == "signin":
                signin += 1
            else:
                failed += 1
            job.set(percent=(n + 1) / total * 100, item_percent=100.0)

        summary = {"done": done, "skipped": skipped, "failed": failed, "signin": signin}
        if job.cancel_event.is_set():
            job.set(state="cancelled", phase="", speed="", eta="", summary=summary)
        else:
            job.set(state="done", phase="", percent=100.0, speed="", eta="", summary=summary)

    @staticmethod
    def _download_item(url: str, p: PlaylistParams, final: Path, hook, cookies):
        def attempt(js: bool):
            opts = build_download_options(p.fmt, p.quality, "none", hook, cookies=cookies, js=js)
            return download_media(url, opts, temp_dir(), final, resume=True)

        return with_js_fallback(attempt)

    @staticmethod
    def _run_ffmpeg(job: Job) -> None:
        ok, method = install_ffmpeg()
        ok = ok and check_ffmpeg()
        job.set(
            state="done" if ok else "failed", message=method,
            error=None if ok else {"code": "ffmpeg", "message": method},
        )

    @staticmethod
    def _run_update(job: Job) -> None:
        ok, output = update_ytdlp()
        job.set(
            state="done" if ok else "failed", message=output,
            error=None if ok else {"code": "update", "message": output},
        )
