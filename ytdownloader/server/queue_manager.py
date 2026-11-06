"""The download queue: many single videos, downloaded one by one, saved to disk so nothing is lost.

State lives in `queue.json` (written atomically after every structural change). The runner is one
thread that takes the next `queued` item, runs it as a normal `video` job (so progress, sign-in
fallback, history and subtitle handling are exactly the same as on the Download page) and waits for it.

Item statuses
    fetching     metadata is being loaded (one link at a time, in the background)
    ready        fetched, waiting for Start
    queued       will be downloaded
    downloading  running now
    paused       stopped by the user or by a restart; the partial file is kept and resumes
    exists       file already on disk and the batch rule is "ask": waiting for the user's choice
    done / skipped / cancelled / failed   finished (failed carries an error with an E-code)
"""

from __future__ import annotations

import re
import shutil
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from pathlib import Path

from ..config import _read_json, _write_json, data_dir, load_config, temp_dir
from ..downloader import (
    build_download_options,
    clean_error,
    discard_partial,
    extract_formats,
    login_reason,
    needs_login,
    partial_days_left,
    partial_key,
    partial_usage,
    pick_quality,
    subtitle_tracks,
)
from ..errors import classify
from ..signin import resolve_sources
from ..utils import format_duration, parse_height, plan_output
from .events import EventHub
from .jobs import Busy, JobManager, VideoParams

MAX_ITEMS = 50
ACTIVE = frozenset({"fetching", "ready", "queued", "downloading", "paused", "exists"})
FINISHED = frozenset({"done", "skipped", "cancelled", "failed"})
EXISTS_POLICIES = ("skip", "replace", "ask")
_VIDEO_ID = re.compile(r"(?:[?&]v=|youtu\.be/|/shorts/|/embed/)([\w-]{11})")


class QueueFull(Exception):
    """Adding these links would pass the 50-item limit."""


def video_key(url: str) -> str:
    """Identity of a video link, so the same video is not queued twice."""
    match = _VIDEO_ID.search(url)
    return match.group(1) if match else url.strip()


def match_languages(wanted: list[str], available: list[str]) -> list[str]:
    """The available subtitle codes that satisfy the wanted languages (exact code first, then the
    first one of the same language, e.g. 'en' -> 'en-US'). 'ar-orig' counts as 'ar'."""
    chosen: list[str] = []
    for want in wanted:
        base = want.lower()
        exact = [a for a in available if a.lower().removesuffix("-orig") == base]
        loose = [a for a in available if a.lower().split("-")[0] == base.split("-")[0]]
        pick = (exact or loose[:1])
        for code in pick:
            if code not in chosen:
                chosen.append(code)
    return chosen


class QueueManager:
    def __init__(
        self,
        hub: EventHub,
        jobs: JobManager,
        fetch: Callable[[str, tuple[str, str] | None], tuple[bool, dict]],
        path: Path | None = None,
    ) -> None:
        self.hub = hub
        self.jobs = jobs
        self._fetch = fetch  # fetch(url, source or None) -> (is_playlist, info)
        self._path = path
        self._lock = threading.RLock()
        self._items: list[dict] = []
        self.settings: dict = {"sub_langs": [], "on_exists": "ask", "fmt": "", "quality": ""}
        self._runner: threading.Thread | None = None
        self._fetcher: threading.Thread | None = None
        self._fetch_queue: deque[str] = deque()
        self._pause = False
        self._halted = ""  # why the runner stopped by itself (e.g. "disk_full")
        self._current: tuple[str, str] | None = None  # (item id, job id)
        self._stop_reason: dict[str, tuple[str, bool]] = {}  # item id -> (pause|cancel|remove, discard)
        self._auto_cookies: tuple[str, str] | None = None
        self._auto_looked_up = False
        self._load()

    # ── Persistence ─────────────────────────────────────────────────────────
    @property
    def path(self) -> Path:
        return self._path or (data_dir() / "queue.json")

    def _load(self) -> None:
        data = _read_json(self.path, {})
        if not isinstance(data, dict):
            return
        items = [i for i in data.get("items", []) if isinstance(i, dict) and i.get("id") and i.get("url")]
        for item in items:
            if item.get("status") == "downloading":
                item["status"] = "paused"  # the process that was downloading it is gone
            if item.get("status") not in ACTIVE | FINISHED:
                item["status"] = "failed"
        self._items = items[:MAX_ITEMS]
        stored = data.get("settings")
        if isinstance(stored, dict):
            langs = stored.get("sub_langs")
            if isinstance(langs, list):
                self.settings["sub_langs"] = [str(x) for x in langs][:60]
            if stored.get("on_exists") in EXISTS_POLICIES:
                self.settings["on_exists"] = stored["on_exists"]
            if stored.get("fmt") in ("", "mp4", "mp3"):
                self.settings["fmt"] = stored["fmt"]
        for item in self._items:
            if item["status"] == "fetching":
                self._fetch_queue.append(item["id"])
        if self._fetch_queue:
            self._ensure_fetcher()

    def _save(self) -> None:
        with self._lock:
            payload = {"version": 1, "settings": dict(self.settings), "items": [dict(i) for i in self._items]}
        for item in payload["items"]:
            for key in ("percent", "speed", "eta", "phase"):
                item.pop(key, None)  # live values are not worth saving
        _write_json(self.path, payload)

    def _changed(self) -> None:
        self._save()
        self.hub.publish({"type": "queue_changed"})

    # ── Reading ─────────────────────────────────────────────────────────────
    def active(self) -> bool:
        """True while the runner thread is working (the server must stay alive)."""
        return bool(self._runner and self._runner.is_alive())

    def state(self) -> str:
        if self._pause or self._halted:
            return "paused"  # also while the runner thread is still finishing its last item
        return "running" if self.active() else "idle"

    def _find(self, item_id: str) -> dict | None:
        return next((i for i in self._items if i["id"] == item_id), None)

    def _planned_subs(self, item: dict) -> tuple[list[str], list[str]]:
        if item.get("fmt") != "mp4":
            return [], []
        tracks = item.get("tracks") or {}
        manual = match_languages(self.settings["sub_langs"], tracks.get("manual", []))
        have = {m.lower().split("-")[0] for m in manual}
        auto = [a for a in match_languages(self.settings["sub_langs"], tracks.get("auto", []))
                if a.lower().split("-")[0] not in have]
        return manual, auto

    def _estimate(self, item: dict) -> int:
        sizes = item.get("sizes") or {}
        if item.get("fmt") == "mp3":
            return int(sizes.get("audio", 0))
        return int(sizes.get(item.get("quality") or "", 0)) + int(sizes.get("audio", 0))

    def _public(self, item: dict) -> dict:
        manual, auto = self._planned_subs(item)
        out = {k: item.get(k) for k in (
            "id", "url", "title", "channel", "thumbnail", "duration_text", "fmt", "quality",
            "qualities", "tracks", "status", "error", "filepath", "message", "percent", "speed", "eta", "phase",
        )}
        out["sub_manual"], out["sub_auto"] = manual, auto
        out["est_bytes"] = self._estimate(item)
        out["partial_bytes"] = 0
        if item["status"] in ("paused", "downloading", "queued"):
            folder = Path(temp_dir()) / f"partial_{item.get('pkey', '')}"
            if item.get("pkey") and folder.is_dir():
                out["partial_bytes"] = sum(f.stat().st_size for f in folder.rglob("*") if f.is_file())
        return out

    def snapshot(self) -> dict:
        with self._lock:
            items = [self._public(i) for i in self._items]
            settings = dict(self.settings)
            estimate = sum(
                self._estimate(i) for i in self._items if i["status"] in ("ready", "queued", "downloading", "paused", "exists")
            )
        counts: dict[str, int] = {}
        for item in items:
            counts[item["status"]] = counts.get(item["status"], 0) + 1
        return {
            "state": self.state(),
            "halted": self._halted,
            "settings": settings,
            "items": items,
            "counts": counts,
            "estimate_bytes": estimate,
            "partial_bytes": partial_usage(temp_dir()),
            "partial_days_left": partial_days_left(temp_dir()),
            "free_bytes": free_space(load_config()["download_dir"]),
            "max_items": MAX_ITEMS,
        }

    # ── Adding links (metadata is loaded in the background, one at a time) ──
    def add(
        self,
        urls: list[str],
        fmt: str | None = None,
        quality: str | None = None,
        start: bool = False,
        partial: bool = False,
    ) -> dict:
        """Queue links. `fmt`/`quality` apply to just these links; `start` queues each as soon as its
        info is loaded (the Playlist page); `partial` adds what fits instead of refusing the whole batch."""
        added, duplicates, left_out = [], 0, 0
        with self._lock:
            known = {video_key(i["url"]) for i in self._items if i["status"] in ACTIVE}
            fresh: list[str] = []
            for url in urls:
                key = video_key(url)
                if key in known:
                    duplicates += 1
                    continue
                known.add(key)
                fresh.append(url)
            room = MAX_ITEMS - len(self._items)
            if len(fresh) > room:
                if not partial:
                    raise QueueFull()
                left_out = len(fresh) - max(room, 0)
                fresh = fresh[: max(room, 0)]
            cfg = load_config()
            for url in fresh:
                item = {
                    "id": uuid.uuid4().hex[:10], "url": url, "title": "", "status": "fetching",
                    "fmt": fmt or self.settings["fmt"] or cfg["default_format"], "quality": None, "added": time.time(),
                }
                if quality:
                    item["want_quality"] = quality
                if start:
                    item["autostart"] = True
                self._items.append(item)
                self._fetch_queue.append(item["id"])
                added.append(item["id"])
        if added:
            self._changed()
            self._ensure_fetcher()
        return {"added": added, "duplicates": duplicates, "left_out": left_out}

    def _ensure_fetcher(self) -> None:
        with self._lock:
            if self._fetcher and self._fetcher.is_alive():
                return
            self._fetcher = threading.Thread(target=self._fetch_loop, daemon=True, name="queue-fetch")
            self._fetcher.start()

    def _fetch_loop(self) -> None:
        while True:
            with self._lock:
                if not self._fetch_queue:
                    return
                item = self._find(self._fetch_queue.popleft())
                if item is None or item["status"] != "fetching":
                    continue
                url = item["url"]
                cookies = self._source_of(item)
            try:
                is_playlist, info = self._fetch(url, cookies)
            except Exception as exc:
                self._fetch_failed(item, exc)
                continue
            with self._lock:
                if item not in self._items:
                    continue
                if is_playlist:
                    item.update(status="failed", error={
                        "code": "playlist", "problem": "E161",
                        "message": "This link is a playlist. Use the Playlist page for it."})
                else:
                    self._fill(item, info)
            self._changed()
            self._ensure_runner_if_started()

    def _fetch_failed(self, item: dict, exc: Exception) -> None:
        message = clean_error(exc)
        error = {"code": "failed", "message": message, "problem": classify(message)}
        if needs_login(message):
            error = {"code": "needs_login", "reason": login_reason(message), "message": message, "problem": classify(message)}
        with self._lock:
            if item in self._items:
                item.update(status="failed", error=error)
        self._changed()

    def _fill(self, item: dict, info: dict) -> None:
        formats = extract_formats(info)
        tracks = subtitle_tracks(info)
        sizes = {f"{h}p": s for h, s in formats.video_sizes.items() if s}
        if formats.audio_size:
            sizes["audio"] = formats.audio_size
        wanted = item.pop("want_quality", None) or self.settings["quality"] or load_config()["default_quality"]
        item.update(
            status="ready", error=None, title=info.get("title") or item["url"],
            channel=info.get("uploader") or info.get("channel") or "",
            thumbnail=info.get("thumbnail") or "", duration_text=format_duration(info.get("duration")),
            qualities=formats.labels, sizes=sizes,
            tracks={"manual": tracks["manual"], "auto": tracks["auto"]},
            quality=pick_quality(formats.heights, wanted) or None,
        )
        item["pkey"] = self._pkey(item)
        if item.pop("autostart", False):
            item["status"] = "queued"

    @staticmethod
    def _opts_for_key(item: dict) -> dict:
        return build_download_options(item.get("fmt", "mp4"), item.get("quality"), "none", lambda d: None)

    def _pkey(self, item: dict) -> str:
        return partial_key(item["url"], self._opts_for_key(item))

    def _source_of(self, item: dict) -> tuple[str, str] | None:
        source = item.get("source")
        return (source["browser"], source.get("profile", "")) if isinstance(source, dict) else None

    # ── Changing items and settings ─────────────────────────────────────────
    def update_item(self, item_id: str, fmt: str | None = None, quality: str | None = None) -> bool:
        with self._lock:
            item = self._find(item_id)
            if item is None or item["status"] in ("downloading", "done", "skipped"):
                return item is not None
            if fmt in ("mp4", "mp3"):
                item["fmt"] = fmt
            if quality is not None and item.get("qualities"):
                heights = [h for h in (parse_height(q) for q in item["qualities"]) if h]
                item["quality"] = pick_quality(heights, quality) or item.get("quality")
            if item.get("pkey"):
                item["pkey"] = self._pkey(item)
        self._changed()
        return True

    def update_settings(self, patch: dict) -> None:
        with self._lock:
            if "sub_langs" in patch:
                self.settings["sub_langs"] = list(dict.fromkeys(patch["sub_langs"]))[:60]
            if patch.get("on_exists") in EXISTS_POLICIES:
                self.settings["on_exists"] = patch["on_exists"]
            if patch.get("fmt") in ("mp4", "mp3"):
                self.settings["fmt"] = patch["fmt"]
                for item in self._items:
                    if item["status"] in ("fetching", "ready", "queued", "paused", "exists"):
                        item["fmt"] = patch["fmt"]
                        if item.get("pkey"):
                            item["pkey"] = self._pkey(item)
            if patch.get("quality"):
                self.settings["quality"] = patch["quality"]
                for item in self._items:
                    if item["status"] in ("ready", "queued", "paused", "exists") and item.get("qualities"):
                        heights = [h for h in (parse_height(q) for q in item["qualities"]) if h]
                        item["quality"] = pick_quality(heights, patch["quality"]) or item.get("quality")
                        item["pkey"] = self._pkey(item)
        self._changed()

    def resolve_exists(self, item_id: str, replace: bool) -> bool:
        with self._lock:
            item = self._find(item_id)
            if item is None or item["status"] != "exists":
                return False
            item["status"] = "queued" if replace else "skipped"
            item["force"] = replace
        self._changed()
        self._ensure_runner_if_started()
        return True

    # ── Running ─────────────────────────────────────────────────────────────
    def start(self) -> int:
        """Queue every ready or paused item and start the runner. Returns how many are queued."""
        count = 0
        with self._lock:
            self._pause = False
            self._halted = ""
            for item in self._items:
                if item["status"] in ("ready", "paused"):
                    item["status"] = "queued"
                    item["error"] = None
                if item["status"] == "queued":
                    count += 1
        self._changed()
        self._ensure_runner()
        return count

    def _ensure_runner_if_started(self) -> None:
        if self.active():
            return
        if any(i["status"] == "queued" for i in self._items) and not self._pause and not self._halted:
            self._ensure_runner()

    def _ensure_runner(self) -> None:
        with self._lock:
            if self.active():
                return
            self._runner = threading.Thread(target=self._run, daemon=True, name="queue-runner")
            self._runner.start()

    def pause(self) -> None:
        """Stop the running download (its partial file is kept) and do not start the next one."""
        with self._lock:
            self._pause = True
            current = self._current
            if current:
                self._stop_reason[current[0]] = ("pause", False)
        if current:
            self.jobs.cancel(current[1])
        self._changed()

    def resume(self) -> int:
        return self.start()

    def maybe_autostart(self) -> None:
        """After a restart: continue unfinished items when the setting asks for it."""
        if load_config().get("queue_auto_resume") and any(i["status"] in ("paused", "queued") for i in self._items):
            self.start()

    def cancel_item(self, item_id: str, discard: bool = False) -> bool:
        with self._lock:
            item = self._find(item_id)
            if item is None or item["status"] in FINISHED:
                return False
            if item["status"] == "downloading" and self._current and self._current[0] == item_id:
                self._stop_reason[item_id] = ("cancel", discard)
                job_id = self._current[1]
            else:
                job_id = ""
                item["status"] = "cancelled"
                if discard and item.get("pkey"):
                    discard_partial(temp_dir(), item["pkey"])
        if job_id:
            self.jobs.cancel(job_id)
        else:
            self._changed()
        return True

    def remove_item(self, item_id: str, discard: bool = False) -> bool:
        with self._lock:
            item = self._find(item_id)
            if item is None:
                return False
            if item["status"] == "downloading" and self._current and self._current[0] == item_id:
                self._stop_reason[item_id] = ("remove", discard)
                job_id = self._current[1]
            else:
                job_id = ""
                self._items.remove(item)
                if discard and item.get("pkey"):
                    discard_partial(temp_dir(), item["pkey"])
        if job_id:
            self.jobs.cancel(job_id)
        else:
            self._changed()
        return True

    def retry(self, ids: list[str] | None = None, source: tuple[str, str] | None = None) -> int:
        """Failed or cancelled items (and skipped ones, when named) go back to the queue (items whose metadata never loaded
        are fetched again, optionally with a signed-in browser profile)."""
        refetch = False
        count = 0
        with self._lock:
            for item in self._items:
                # "Retry all" never touches skipped videos (that could overwrite files unasked); a skipped
                # video is retried only when named, and then the batch rule is applied again.
                allowed = ("failed", "cancelled") if ids is None else ("failed", "cancelled", "skipped")
                if item["status"] not in allowed or (ids is not None and item["id"] not in ids):
                    continue
                if source:
                    item["source"] = {"browser": source[0], "profile": source[1]}
                item["error"] = None
                if item.get("qualities"):
                    item["status"] = "queued"
                else:
                    item["status"] = "fetching"
                    self._fetch_queue.append(item["id"])
                    refetch = True
                count += 1
            self._pause = False
            self._halted = ""
        self._changed()
        if refetch:
            self._ensure_fetcher()
        self._ensure_runner_if_started()
        return count

    def clear_finished(self) -> int:
        with self._lock:
            keep = [i for i in self._items if i["status"] not in ("done", "skipped", "cancelled")]
            removed = len(self._items) - len(keep)
            self._items = keep
        if removed:
            self._changed()
        return removed

    def discard_partials(self) -> None:
        """Delete all saved unfinished data (items that were waiting to resume restart from zero)."""
        discard_partial(temp_dir())
        self.hub.publish({"type": "queue_changed"})

    # ── The runner thread ───────────────────────────────────────────────────
    def _next_item(self) -> dict | None:
        with self._lock:
            return next((i for i in self._items if i["status"] == "queued"), None)

    def _run(self) -> None:
        try:
            while not self._pause and not self._halted:
                item = self._next_item()
                if item is None:
                    break
                self._run_item(item)
        finally:
            with self._lock:
                self._runner = None
            self.hub.publish({"type": "queue_changed"})
            # Something may have been queued while this thread was finishing (e.g. Resume right after Pause).
            self._ensure_runner_if_started()

    def _auto_source(self) -> tuple[str, str] | None:
        """Signed-in browser profile for items YouTube refused (only when the user allowed sign-in)."""
        cfg = load_config()
        if cfg["signin_consent"] != "allow":
            return None
        if not self._auto_looked_up:
            self._auto_looked_up = True
            found = resolve_sources(cfg["cookies_browser"], cfg["cookies_profile"])
            self._auto_cookies = found.usable[0].cookies if found.usable else None
        return self._auto_cookies

    def _run_item(self, item: dict) -> None:
        cfg = load_config()
        manual, auto = self._planned_subs(item)
        final, _name = plan_output(cfg["download_dir"], item["title"], item["fmt"],
                                   item["quality"] if item["fmt"] == "mp4" else None, bool(manual or auto))
        if final.exists() and not item.pop("force", False):
            policy = self.settings["on_exists"]
            with self._lock:
                item["status"] = {"skip": "skipped", "replace": "queued", "ask": "exists"}[policy]
                if policy == "skip":
                    item["message"] = "exists"
                if policy == "replace":
                    item["force"] = True
            self._changed()
            return

        cookies = self._source_of(item)
        tried_auto = False
        while True:
            params = VideoParams(
                url=item["url"], fmt=item["fmt"], quality=item["quality"] if item["fmt"] == "mp4" else None,
                final=str(final), cookies=cookies, sub_manual=tuple(manual), sub_auto=tuple(auto),
            )
            job = self._start_job(item, params)
            if job is None:
                return
            result = self._wait(item, job)
            if result == "failed" and job.error and job.error.get("code") == "needs_login" and not tried_auto:
                tried_auto = True
                found = self._auto_source()
                if found and not cookies:
                    cookies = found
                    continue
            break
        self._finish_item(item, job, result)

    def _start_job(self, item: dict, params: VideoParams):
        with self._lock:
            item.update(status="downloading", percent=0.0, speed="", eta="", phase="")
        self._changed()
        while True:
            try:
                job = self.jobs.start_video(item["title"], params)
                break
            except Busy:  # another download (Download page) is running: wait for it
                if self._pause:
                    with self._lock:
                        item["status"] = "queued"
                    self._changed()
                    return None
                time.sleep(1.0)
        with self._lock:
            self._current = (item["id"], job.id)
        return job

    def _wait(self, item: dict, job) -> str:
        while not job.finished.wait(0.5):
            pub = job.public()
            with self._lock:
                item.update(percent=pub["percent"], speed=pub["speed"], eta=pub["eta"], phase=pub["phase"])
            self.hub.publish({"type": "queue_progress", "id": item["id"], "percent": pub["percent"],
                              "speed": pub["speed"], "eta": pub["eta"], "phase": pub["phase"]})
        return job.state

    def _finish_item(self, item: dict, job, result: str) -> None:
        with self._lock:
            self._current = None
            reason, discard = self._stop_reason.pop(item["id"], ("", False))
            item.update(percent=0.0, speed="", eta="", phase="")
            if reason == "remove":
                if item in self._items:
                    self._items.remove(item)
                if discard and item.get("pkey"):
                    discard_partial(temp_dir(), item["pkey"])
            elif result == "done":
                item.update(status="done", filepath=job.filepath, error=None,
                            message="subtitles_skipped" if job.message == "subtitles_skipped" else "")
            elif result == "cancelled":
                if reason == "pause":
                    item["status"] = "paused"
                else:
                    item["status"] = "cancelled"
                    if discard and item.get("pkey"):
                        discard_partial(temp_dir(), item["pkey"])
            else:
                item.update(status="failed", error=job.error)
                message = (job.error or {}).get("message", "").lower()
                if "no space left" in message or "disk full" in message:
                    self._halted = "disk_full"  # every other item would fail the same way
        self._changed()


def free_space(folder: str) -> int:
    """Free bytes on the drive that holds `folder` (which may not exist yet)."""
    path = Path(folder).expanduser()
    while not path.exists() and path != path.parent:
        path = path.parent
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return 0
