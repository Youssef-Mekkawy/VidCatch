"""The JSON API. Every route is documented in docs/API.md.

Conventions:
- Routes are plain `def` so FastAPI runs them in a worker thread; yt-dlp and disk access block.
- "Expected" outcomes (needs sign-in, file exists, offline...) are 200 responses with a `status`
  field, because the UI handles them as normal flow. Real mistakes use HTTP errors (4xx).
"""

from __future__ import annotations

import asyncio
import glob
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from .. import __version__
from ..config import (
    clear_history,
    data_dir,
    default_temp_dir,
    load_config,
    load_history,
    remove_history_entry,
    temp_dir,
    update_config,
    validate_settings_patch,
)
from ..downloader import (
    PARTIAL_PREFIX,
    clean_error,
    fetch_video_info,
    login_reason,
    needs_login,
    partial_usage,
)
from ..errors import classify
from ..signin import (
    Source,
    candidate_sources,
    fetch_with_sources,
    list_profiles,
    probe_source,
    resolve_sources,
)
from ..system import (
    check_ffmpeg,
    check_internet,
    detect_browsers,
    detect_js_runtimes,
    get_platform,
    open_folder,
    reveal_file,
)
from ..updates import can_self_update, fetch_latest_version, installed_version, is_newer
from ..utils import find_existing_video, plan_output
from .events import EventHub
from .jobs import Busy, JobManager, PlaylistParams, VideoParams
from .platform_ui import pick_folder
from .queue_manager import free_space
from .schemas import (
    DownloadIn,
    FetchIn,
    PlaylistDownloadIn,
    SigninPrepareIn,
    SourceIn,
)
from .serialize import playlist_payload, video_payload

APP_ID = "vidcatch"


@dataclass
class AppContext:
    """Everything the routes share. One instance per running server."""

    hub: EventHub
    jobs: JobManager
    queue: Any  # QueueManager
    shutdown: Any  # callable: stop the server
    restart: Any  # callable: start a fresh copy of the app, then stop this one


router = APIRouter(prefix="/api")


def _ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def _source_json(source: Source) -> dict:
    return {
        "browser": source.browser,
        "profile": source.profile,
        "label": source.label,
        "title": source.title,
    }


def _to_source(item: SourceIn) -> Source:
    return Source(item.browser, item.profile, item.label or item.profile)


# ── Status and settings ─────────────────────────────────────────────────────
@router.get("/ping")
def ping() -> dict:
    """Unauthenticated. A second launch uses it to find the running instance."""
    return {"app": APP_ID, "version": __version__}


@router.get("/status")
def status() -> dict:
    cfg = load_config()
    return {
        "app": APP_ID,
        "version": __version__,
        "ytdlp_version": installed_version(),
        "platform": get_platform(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "ffmpeg": check_ffmpeg(),
        "js_runtimes": detect_js_runtimes(),
        "browsers": detect_browsers(),
        "data_dir": str(data_dir()),
        "settings": cfg,
    }


@router.get("/settings")
def get_settings() -> dict:
    return load_config()


def _change_temp_dir(request: Request, new_value: str) -> None:
    """Check the new temporary folder and move unfinished downloads into it."""
    ctx = _ctx(request)
    if ctx.jobs.any_running() or ctx.queue.active():
        raise HTTPException(409, "Finish or pause the running downloads first.")
    old = temp_dir()
    target = Path(new_value).expanduser() if new_value else default_temp_dir()
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".write_test"
        probe.write_text("ok")
        probe.unlink()
    except OSError as exc:
        raise HTTPException(422, f"Cannot write to that folder: {exc.strerror or exc}") from exc
    if target.resolve() == old.resolve():
        return
    for child in old.iterdir() if old.is_dir() else []:
        if child.is_dir() and child.name.startswith(PARTIAL_PREFIX):
            try:
                shutil.move(str(child), str(target / child.name))
            except OSError:
                pass  # that download will simply start again


@router.put("/settings")
def put_settings(patch: dict, request: Request) -> dict:
    try:
        clean = validate_settings_patch(patch)
        if "temp_dir" in clean and clean["temp_dir"] != load_config().get("temp_dir", ""):
            _change_temp_dir(request, clean["temp_dir"])
        return update_config(patch)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/storage")
def storage() -> dict:
    """Disk numbers for the Settings page and the Queue tab."""
    cfg = load_config()
    temp = temp_dir()
    return {
        "download_dir": cfg["download_dir"],
        "download_free": free_space(cfg["download_dir"]),
        "temp_dir": str(temp),
        "temp_free": free_space(str(temp)),
        "partial_bytes": partial_usage(temp),
    }


# ── Fetching metadata ───────────────────────────────────────────────────────
def _fetch_ok(is_playlist: bool, info: dict, source: Source | None) -> dict:
    out: dict = {"status": "ok", "source": _source_json(source) if source else None}
    if is_playlist:
        out.update(type="playlist", playlist=playlist_payload(info))
    else:
        out.update(type="video", video=video_payload(info, load_config()))
    return out


@router.post("/fetch")
def fetch(body: FetchIn) -> dict:
    """Always tries without cookies first. With `sources` (after the user allowed sign-in) it tries
    each profile until YouTube accepts one."""
    if not check_internet():
        return {"status": "offline", "problem": "E100"}

    if body.sources:
        source, result, error = fetch_with_sources(body.url, [_to_source(s) for s in body.sources])
        if source is None:
            return {
                "status": "signin_failed", "kind": "rejected",
                "reason": login_reason(error), "detail": error,
            }
        is_playlist, info = result
        return _fetch_ok(is_playlist, info, source)

    try:
        is_playlist, info = fetch_video_info(body.url)
    except Exception as exc:
        message = clean_error(exc)
        if needs_login(message):
            return {"status": "needs_login", "reason": login_reason(message), "message": message}
        return {"status": "error", "message": message, "problem": classify(message)}
    return _fetch_ok(is_playlist, info, None)


# ── Sign-in (only used after YouTube refused a video) ───────────────────────
@router.post("/signin/prepare")
def signin_prepare(body: SigninPrepareIn) -> dict:
    """Decide what the UI must do next after a `needs_login` result.

    consent_needed  -> explain and ask (Allow / No), save the answer with PUT /api/settings
    signin_off      -> the user said No: show the red notice
    choose_account  -> several signed-in profiles and no saved choice: let the user pick one
    signin_failed   -> cannot read any signed-in browser (kind: locked, encrypted, ...)
    ok              -> `sources` are ready to pass to POST /api/fetch or POST /api/downloads
    """
    cfg = load_config()
    if cfg["signin_consent"] == "ask":
        return {"status": "consent_needed"}
    if cfg["signin_consent"] != "allow":
        return {"status": "signin_off"}

    resolution = resolve_sources(cfg["cookies_browser"], cfg["cookies_profile"])
    if not resolution.usable:
        return {"status": "signin_failed", "kind": resolution.kind, "detail": resolution.error}
    sources = [_source_json(s) for s in resolution.usable]
    if body.first_time and not cfg["cookies_browser"] and len(sources) > 1:
        return {"status": "choose_account", "sources": sources}
    return {"status": "ok", "sources": sources}


@router.get("/signin/accounts")
def signin_accounts() -> dict:
    """Installed browsers and their profiles, for the Settings menus."""
    return {
        "browsers": [
            {
                "id": name,
                "profiles": [{"profile": s.profile, "label": s.label} for s in list_profiles(name)],
            }
            for name in detect_browsers()
        ]
    }


@router.post("/signin/test")
def signin_test() -> dict:
    """Settings > Sign-in > Test: check, without any network request, which profiles can be used."""
    cfg = load_config()
    sources = candidate_sources(cfg["cookies_browser"], cfg["cookies_profile"])
    results = []
    for source in sources:
        probe = probe_source(source)
        results.append(
            {
                **_source_json(source),
                "readable": probe.readable,
                "signed_in": probe.signed_in,
                "cookies": probe.cookies,
                "kind": probe.kind,
                "error": probe.error,
            }
        )
    return {"installed": bool(sources), "results": results}


# ── Downloads ───────────────────────────────────────────────────────────────
@router.post("/downloads")
def start_download(body: DownloadIn, request: Request) -> dict:
    cfg = load_config()
    fmt = body.fmt
    quality = body.quality if fmt == "mp4" else None
    title = body.title.strip() or "video"
    sub_manual = body.sub_manual if fmt == "mp4" else []
    sub_auto = body.sub_auto if fmt == "mp4" else []
    # With subtitles, the video and its .srt files go together in a folder named after the video.
    final, filename = plan_output(cfg["download_dir"], title, fmt, quality, bool(sub_manual or sub_auto))

    if body.subtitles_only:
        if not (sub_manual or sub_auto):
            raise HTTPException(422, "Choose at least one subtitle language.")
        # Beside the video if it is already saved, otherwise in the folder named after the video.
        existing = find_existing_video(cfg["download_dir"], title, filename)
        final = existing or final
    elif final.exists() and cfg["ask_overwrite"] and not body.overwrite:
        info = final.stat()
        return {"status": "exists", "filename": filename, "size": info.st_size, "modified": info.st_mtime}

    cookies = (body.source.browser, body.source.profile) if body.source else None
    params = VideoParams(
        url=body.url, fmt=fmt, quality=quality,
        sub_manual=tuple(sub_manual), sub_auto=tuple(sub_auto), subtitles_only=body.subtitles_only,
        final=str(final), cookies=cookies,
    )
    ctx = _ctx(request)
    try:
        job = ctx.jobs.start_video(title, params)
    except Busy as exc:
        raise HTTPException(409, "Another task is still running.") from exc
    if not body.subtitles_only:  # subtitles-only is quick and not resumable
        ctx.queue.track_video(job, params, title, {
            "channel": body.channel, "thumbnail": body.thumbnail, "duration_text": body.duration_text})
    return {"status": "started", "job": job.public()}


@router.post("/playlists/download")
def start_playlist(body: PlaylistDownloadIn, request: Request) -> dict:
    cfg = load_config()
    quality = body.quality if body.fmt == "mp4" else None
    params = PlaylistParams(
        entries=[{"title": e.title.strip(), "url": e.url} for e in body.entries],
        fmt=body.fmt,
        quality=quality,
        out_dir=str(Path(cfg["download_dir"]).expanduser()),
        overwrite=not cfg["ask_overwrite"],
    )
    try:
        job = _ctx(request).jobs.start_playlist(body.title.strip() or "Playlist", params)
    except Busy as exc:
        raise HTTPException(409, "Another task is still running.") from exc
    return {"status": "started", "job": job.public()}


@router.get("/jobs")
def list_jobs(request: Request) -> dict:
    return {"jobs": _ctx(request).jobs.snapshot()}


@router.post("/jobs/{job_id}/pause")
def pause_job(job_id: str, request: Request) -> dict:
    """Pause a download started on the Download page. It continues from the Queue tab."""
    if not _ctx(request).queue.pause_job(job_id):
        raise HTTPException(404, "No pausable download with that id.")
    return {"status": "pausing"}


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request) -> dict:
    if not _ctx(request).jobs.cancel(job_id):
        raise HTTPException(404, "No running job with that id.")
    return {"status": "cancelling"}


@router.get("/events")
async def events(request: Request) -> StreamingResponse:
    """Server-Sent Events: live job progress. Staying connected also tells the server the page is
    still open (it exits on its own shortly after the last page closes)."""
    ctx = _ctx(request)

    async def stream():
        queue = ctx.hub.subscribe()
        try:
            hello = {"type": "hello", "jobs": ctx.jobs.snapshot()}
            yield f"data: {json.dumps(hello)}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    if await request.is_disconnected():
                        break
                    yield ": keep-alive\n\n"
                    continue
                yield f"data: {json.dumps(event)}\n\n"
        except asyncio.CancelledError:
            return  # the server is shutting down: end the stream quietly instead of logging a traceback
        finally:
            ctx.hub.unsubscribe(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


# ── History ─────────────────────────────────────────────────────────────────
@router.get("/history")
def get_history() -> dict:
    """Newest first. Each entry says whether its file still exists and how big it is; `total_bytes`
    and `existing` summarise what the downloads use on disk."""
    entries, total, existing = [], 0, 0
    for entry in reversed(load_history()):
        path = Path(entry.get("filepath") or "")
        size = 0
        try:
            if entry.get("filepath") and path.is_file():
                size = path.stat().st_size
        except OSError:
            pass
        exists = bool(entry.get("filepath")) and path.is_file()
        if exists:
            total += size
            existing += 1
        entries.append({**entry, "exists": exists, "size": size})
    return {"entries": entries, "total_bytes": total, "existing": existing}


@router.delete("/history")
def delete_all_history(request: Request, delete_files: bool = False) -> dict:
    """Clear the history. With delete_files=true every downloaded file listed in it is deleted too."""
    deleted = 0
    if delete_files:
        for entry in load_history():
            if entry.get("filepath"):
                deleted += len(_delete_download_files(Path(entry["filepath"])))
    clear_history()
    _ctx(request).hub.publish({"type": "history_changed"})
    return {"status": "ok", "deleted": deleted}


def _delete_download_files(path: Path) -> list[str]:
    """Delete a downloaded video and the subtitle files saved with it (`<name>.<lang>.srt`).

    Only called with a path read from our own history file. Removes the folder too when it was the
    per-video folder and is now empty. Returns the names of the deleted files."""
    deleted: list[str] = []
    if path.suffix.lower() not in (".mp4", ".mp3"):
        return deleted
    candidates = [path, *path.parent.glob(f"{glob.escape(path.stem)}.*.srt")]
    for file in candidates:
        try:
            if file.is_file():
                file.unlink()
                deleted.append(file.name)
        except OSError:
            continue
    download_dir = Path(load_config()["download_dir"]).expanduser()
    folder = path.parent
    if folder != download_dir and folder.is_dir():
        try:
            folder.rmdir()  # only succeeds when empty
        except OSError:
            pass
    return deleted


@router.post("/history/prune")
def prune_history(request: Request) -> dict:
    """Remove every entry whose file is no longer on disk."""
    gone = [e["id"] for e in load_history() if not (e.get("filepath") and Path(e["filepath"]).is_file())]
    for entry_id in gone:
        remove_history_entry(entry_id)
    if gone:
        _ctx(request).hub.publish({"type": "history_changed"})
    return {"status": "ok", "removed": len(gone)}


@router.delete("/history/{entry_id}")
def delete_history_entry(entry_id: str, request: Request, delete_file: bool = False) -> dict:
    """Remove one entry. With delete_file=true the downloaded file (and its subtitles) is deleted
    from the computer too; the path comes from the history file, never from the request."""
    deleted: list[str] = []
    if delete_file:
        entry = next((e for e in load_history() if e.get("id") == entry_id), None)
        if entry and entry.get("filepath"):
            deleted = _delete_download_files(Path(entry["filepath"]))
    remove_history_entry(entry_id)
    _ctx(request).hub.publish({"type": "history_changed"})
    return {"status": "ok", "deleted": deleted}


@router.post("/history/{entry_id}/reveal")
def reveal_history_entry(entry_id: str) -> dict:
    """Show a finished download in the file manager. The path comes from our own history file,
    never from the request."""
    entry = next((e for e in load_history() if e.get("id") == entry_id), None)
    if entry is None:
        raise HTTPException(404, "No such history entry.")
    path = Path(entry.get("filepath") or "")
    try:
        if path.is_file():
            reveal_file(path)
        else:
            open_folder(path.parent if str(path) else Path(load_config()["download_dir"]))
    except OSError as exc:
        raise HTTPException(500, f"Could not open the folder: {exc}") from exc
    return {"status": "ok"}


# ── Folders ─────────────────────────────────────────────────────────────────
@router.post("/folder/open")
def open_download_folder(request: Request, job_id: str = "") -> dict:
    """Open the download folder, or the folder a finished job saved into (path taken from the job,
    never from the request)."""
    folder = Path(load_config()["download_dir"]).expanduser()
    job = _ctx(request).jobs.get(job_id) if job_id else None
    if job and job.filepath:
        folder = Path(job.filepath).parent
    try:
        open_folder(folder)
    except OSError as exc:
        raise HTTPException(500, f"Could not open the folder: {exc}") from exc
    return {"status": "ok"}


@router.post("/folder/pick")
def pick_download_folder() -> dict:
    """Open the system folder dialog on this machine."""
    try:
        chosen = pick_folder(load_config()["download_dir"])
    except NotImplementedError:
        return {"status": "unavailable"}
    if not chosen:
        return {"status": "cancelled"}
    return {"status": "ok", "path": chosen}


# ── Maintenance ─────────────────────────────────────────────────────────────
@router.post("/ffmpeg/install")
def ffmpeg_install(request: Request) -> dict:
    try:
        job = _ctx(request).jobs.start_ffmpeg_install()
    except Busy as exc:
        raise HTTPException(409, "Another task is still running.") from exc
    return {"status": "started", "job": job.public()}


@router.get("/updates")
def updates() -> dict:
    """Is a newer yt-dlp on PyPI? `latest` is null when offline."""
    current = installed_version()
    latest = fetch_latest_version()
    return {
        "installed": current,
        "latest": latest,
        "newer": bool(latest) and is_newer(latest, current),
        "can_self_update": can_self_update(),
    }


@router.post("/updates/install")
def updates_install(request: Request) -> dict:
    if not can_self_update():
        raise HTTPException(400, "This build cannot update itself. Download a newer release.")
    try:
        job = _ctx(request).jobs.start_update()
    except Busy as exc:
        raise HTTPException(409, "Another task is still running.") from exc
    return {"status": "started", "job": job.public()}


@router.post("/shutdown")
def shutdown(request: Request) -> dict:
    ctx = _ctx(request)
    if ctx.jobs.any_running():
        raise HTTPException(409, "A task is still running.")
    ctx.shutdown()
    return {"status": "stopping"}


@router.post("/restart")
def restart(request: Request) -> dict:
    ctx = _ctx(request)
    if ctx.jobs.any_running():
        raise HTTPException(409, "A task is still running.")
    ctx.restart()
    return {"status": "restarting"}
