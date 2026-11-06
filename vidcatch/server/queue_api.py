"""Queue routes: /api/queue/...  (see docs/API.md)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..config import temp_dir
from ..downloader import partial_usage
from .api import _ctx
from .queue_manager import QueueFull
from .schemas import QueueAddIn, QueueDiscardIn, QueueItemPatch, QueueRetryIn, QueueSettingsIn

router = APIRouter(prefix="/api/queue")


def _queue(request: Request):
    return _ctx(request).queue


@router.get("")
def get_queue(request: Request) -> dict:
    return _queue(request).snapshot()


@router.post("/items")
def add_items(body: QueueAddIn, request: Request) -> dict:
    try:
        return _queue(request).add(body.urls, body.fmt, body.quality, body.start, body.partial)
    except QueueFull as exc:
        raise HTTPException(422, "The queue holds at most 50 videos.") from exc


@router.patch("/items/{item_id}")
def patch_item(item_id: str, body: QueueItemPatch, request: Request) -> dict:
    if not _queue(request).update_item(item_id, fmt=body.fmt, quality=body.quality):
        raise HTTPException(404, "No such queue item.")
    return {"status": "ok"}


@router.delete("/items/{item_id}")
def delete_item(item_id: str, request: Request, discard: bool = False) -> dict:
    if not _queue(request).remove_item(item_id, discard):
        raise HTTPException(404, "No such queue item.")
    return {"status": "ok"}


@router.post("/items/{item_id}/cancel")
def cancel_item(item_id: str, body: QueueDiscardIn, request: Request) -> dict:
    if not _queue(request).cancel_item(item_id, body.discard):
        raise HTTPException(404, "No unfinished queue item with that id.")
    return {"status": "ok"}


@router.post("/items/{item_id}/exists")
def resolve_exists(item_id: str, request: Request, replace: bool = False) -> dict:
    """The file is already on disk: replace it (replace=true) or skip this video."""
    if not _queue(request).resolve_exists(item_id, replace):
        raise HTTPException(404, "That item is not waiting for a decision.")
    return {"status": "ok"}


@router.post("/items/{item_id}/resume")
def resume_item(item_id: str, request: Request) -> dict:
    """Continue one paused video."""
    if not _queue(request).resume_item(item_id):
        raise HTTPException(404, "That item is not paused.")
    return {"status": "ok"}


@router.put("/settings")
def put_settings(body: QueueSettingsIn, request: Request) -> dict:
    _queue(request).update_settings(body.model_dump(exclude_none=True))
    return {"status": "ok"}


@router.post("/start")
def start(request: Request) -> dict:
    return {"status": "ok", "queued": _queue(request).start()}


@router.post("/pause")
def pause(request: Request) -> dict:
    _queue(request).pause()
    return {"status": "ok"}


@router.post("/resume")
def resume(request: Request) -> dict:
    return {"status": "ok", "queued": _queue(request).resume()}


@router.post("/retry")
def retry(body: QueueRetryIn, request: Request) -> dict:
    source = (body.source.browser, body.source.profile) if body.source else None
    return {"status": "ok", "retried": _queue(request).retry(body.ids, source)}


@router.post("/clear-finished")
def clear_finished(request: Request) -> dict:
    return {"status": "ok", "removed": _queue(request).clear_finished()}


@router.delete("/partials")
def delete_partials(request: Request) -> dict:
    """Delete the saved unfinished data of every video (paused items restart from zero)."""
    queue = _queue(request)
    if queue.active():
        raise HTTPException(409, "Pause the queue first.")
    queue.discard_partials()
    return {"status": "ok", "partial_bytes": partial_usage(temp_dir())}
