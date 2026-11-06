# Plan: Queue, Pause/Resume and Storage Overview

Status: **proposal, waiting for approval. No code has been written.**

## 1. Goals

1. Add several single-video links (from any channels) and download them one by one.
2. Choose quality once for all videos, or per video, before starting.
3. Download subtitles when they exist, using one language preference for the whole batch.
4. Pause, resume, cancel and recover downloads, even after closing the app or a crash, without corrupt files.
5. Show how much disk space downloads use (queue estimate, partial files, History total).

## 2. Decisions already agreed

| Topic | Decision |
|---|---|
| Concurrency | One download at a time (a later setting could allow 2–3, default 1) |
| Subtitle languages | Chosen once per batch; each video gets the ones that exist, the rest are skipped silently |
| Subtitle files | Folder named after the video containing the video and its `.srt` files (as today) |
| Already downloaded | One choice for the whole batch: skip all / replace all / ask each |
| Sign-in | Existing setting and consent flow, asked once per batch |
| Persistence | Queue and unfinished jobs survive closing the app |
| UI location | New **Queue** tab |
| Limit | Max 50 links in the queue |
| Pause | Really stops the download and keeps the partial file |
| After restart | Unfinished items show as Paused; auto-resume is a setting, off by default |
| Old partials | Cleared after 7 days, with a notice before |
| Playlists | Use the same engine; resume at the video where they stopped |
| Free-space check | Warn only, never block |
| Units | Translated (GB / جيجابايت) |

## 3. Today's limitations (why this needs engine work)

- `JobManager` allows one job at a time and raises `Busy` otherwise.
- Each download uses a random temp folder that is **always removed**, so a stopped download starts again from zero.
- Job state exists only in memory; after a restart the app does not know a download was in progress.

## 4. Design

### 4.1 Stable partial folders
- Partial data lives in `<data dir>/partial/<video id>__<fmt>__<quality>/` instead of a random temp folder.
- yt-dlp writes `.part` and `.ytdl` files there and continues from them on the next run (resume is yt-dlp's `continuedl`, on by default).
- The folder is deleted only when the download finishes, the user discards it, or it is older than 7 days.

### 4.2 Safe finish
- The final file is created in the download folder only after merge/convert succeeds, by moving it as the last step.
- A half-written file can never appear as a finished video.
- On resume we validate: if the partial data looks damaged (size mismatch, ffmpeg error), that piece is discarded and downloaded again.

### 4.3 Saved state
- One JSON file in the data dir (`queue.json`), written atomically (write to temp, then rename) on every state change.
- Each item stores: id, url, title, channel, thumbnail, duration, format, quality, subtitle languages, status, bytes done, size estimate, error code, timestamps.
- Statuses: `ready`, `queued`, `downloading`, `paused`, `done`, `failed`, `skipped`, `cancelled`.
- On startup, anything that was `downloading` becomes `paused` (the process is gone).

### 4.4 Queue runner
- A single worker takes the next `queued` item, runs the existing download logic, updates state, moves on.
- A failure marks that item `failed` with its E-code and continues with the next one.
- Pause: stop the running yt-dlp (existing cancel mechanism) but keep the partial folder and mark `paused`.
- Cancel: asks "Keep progress" or "Discard".
- Retry on a failed item; "Retry all failed" on the toolbar.

### 4.5 Sign-in
- If any item returns `needs_login`, use the existing prepare/consent flow once for the batch, then retry those items with the chosen source.

### 4.6 Playlists
- The playlist page adds its videos to the same queue (same engine, same persistence).
- Resume continues at the first item that is not `done`.

### 4.7 Storage overview
- **Queue tab, before start:** "7 videos · about 1.2 GB" (estimate from reported format sizes, labelled "about").
- **Queue tab, running:** "340 MB of 1.2 GB".
- **Partial files:** "Unfinished downloads use 420 MB" with a Clear button.
- **Free space:** compare the estimate with free space on the download drive; show a warning only.
- **History:** header line "12 videos · 4.8 GB on disk" (existing files only) plus a size on each entry.

## 5. User interface (plain CSS tokens, as in the rest of the app)

**Queue tab**
1. Add area: textarea (one link per line) with "Add" button, and a counter (n / 50).
2. List of items: thumbnail, title, channel, duration, status badge, size estimate, per-item quality select, per-item remove.
3. Batch bar: "Apply quality to all", subtitle languages picker, "If file exists" choice (skip / replace / ask).
4. Totals line and free-space warning.
5. Controls: Start, Pause, Resume, Cancel, Retry failed, Clear finished.
6. Per item while running: progress bar, speed, ETA. Failed items show the E-code and a short fix.

All strings go through `t()` in `en.json` and `ar.json`. Titles are rendered as text nodes only (no HTML building from titles).

## 6. Server API (new, documented in `docs/API.md`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/queue` | Items, totals, partial-files size |
| POST | `/api/queue/items` | Add links (validates, fetches info, max 50) |
| PATCH | `/api/queue/items/{id}` | Change quality / subtitle languages |
| DELETE | `/api/queue/items/{id}` | Remove (optionally discard partial) |
| POST | `/api/queue/start` | Start the runner |
| POST | `/api/queue/pause` | Pause current download |
| POST | `/api/queue/resume` | Resume |
| POST | `/api/queue/retry` | Retry failed items |
| DELETE | `/api/queue/partials` | Clear unfinished data |
| GET | `/api/storage` | History total, partial total, free space |

SSE gets a `queue_changed` event. Paths for deletes always come from the saved state, never from the request.

## 7. Edge cases

- Same video added twice: ignored with a message.
- Link is a playlist: offered to the Playlist page instead.
- Video unavailable / private / live: that item fails with its code, the queue continues.
- Requested quality missing: falls back to the closest lower one and the item says so.
- Disk full: item fails with a clear code, queue pauses so nothing else fails the same way.
- Power loss mid-merge: partial folder survives; final file is never half-written.
- Download folder changed while items are queued: items use the folder at start time.
- Server auto-exits ~45 s after the last page closes; with a running queue it must **not** exit (new rule), or it must pause cleanly first. To decide: keep running while the queue is active.

## 8. Testing

- Unit: state file read/write and atomic replace, status transitions, restart recovery (`downloading` → `paused`), 50-link cap, duplicate detection, quality fallback, partial cleanup after 7 days, safe-delete rules.
- API: every endpoint above, including error cases.
- Mock-server end-to-end with Playwright: add links, change quality, start, pause, restart server, resume, failure in the middle, subtitles present/absent.
- Locale test keeps both languages complete and unused-key free.
- Real-YouTube check by you on Windows (the part I cannot test): a real pause/resume of a large video, closing the app mid-download.

## 9. Work order

1. Engine: stable partial folders and safe finish (no UI yet), tests.
2. Saved queue state and runner (pause/resume/retry/recovery), API, tests.
3. Queue tab UI: add links, per-item and batch quality, subtitles, start/pause.
4. Storage overview (queue totals, free space, partials, History sizes).
5. Playlist page moved onto the queue.
6. Docs (API, ARCHITECTURE, ERRORS for any new codes, CHANGELOG, README) and a delivery zip after each step, so you can test incrementally.

## 10. Open points

- Server auto-exit while a queue is active (see section 7): proposed to stay alive until the queue is idle.
- Whether to show a desktop notification when the queue finishes (optional, later).
- Optional later setting: 2–3 parallel downloads.
