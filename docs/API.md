# API reference

The browser UI talks to a small JSON API served by the local FastAPI server. A machine-readable
description is available at `/api/openapi.json` (needs the session cookie, like every `/api/*` route).

## Conventions

- Base URL: `http://127.0.0.1:<port>`. The port is the first free one from 8765 (see `--port`).
- **Authentication:** every `/api/*` route except `GET /api/ping` needs the session cookie
  `ytdl_session`. The launcher opens `/?t=<token>`; the server replies `303 /` and sets the cookie
  (`HttpOnly`, `SameSite=Strict`). The token lives in `server.json` in the data folder.
- **Expected outcomes are `200` with a `status` field** (needs sign-in, file exists, offline...),
  because the UI treats them as normal flow. Real mistakes use HTTP errors:
  `401` no cookie, `403` foreign Origin, `400` foreign Host, `404`, `409` another task is running,
  `422` invalid input (`{"detail": ...}`).
- Progress arrives over **Server-Sent Events** (`GET /api/events`), not by polling.

## Status and settings

| Route | Description |
| --- | --- |
| `GET /api/ping` | Public. `{app, version}`. Used by a second launch to find the running instance. |
| `GET /api/status` | `{version, ytdlp_version, platform, frozen, ffmpeg, js_runtimes, browsers, data_dir, settings}` |
| `GET /api/settings` | All settings. |
| `PUT /api/settings` | Partial update, e.g. `{"language": "ar"}`. Returns all settings. `422` for unknown keys, wrong types or invalid values. Changing `cookies_browser` resets `cookies_profile`. |

Settings keys: `language` (`en`/`ar`), `download_dir`, `default_format` (`mp4`/`mp3`),
`default_quality` (`best`/`1080p`/`720p`/`480p`/`360p`), `auto_download`, `open_folder`,
`ask_overwrite`, `clear_url`, `cookies_browser` (`""` = automatic), `cookies_profile`,
`signin_consent` (`ask`/`allow`/`never`), `check_updates`.

## Fetching a video or playlist

`POST /api/fetch` with `{"url": "https://...", "sources": [{"browser", "profile", "label"}]?}`

It always tries **without cookies first**. Pass `sources` only after the user allowed sign-in.

| `status` | Meaning | Extra fields |
| --- | --- | --- |
| `ok` | Success | `type` = `video` or `playlist`, `video` / `playlist` object, `source` (the profile that worked, or `null`) |
| `needs_login` | YouTube wants a signed-in session | `reason` (`age`, `private`, `members`, `bot`, `login`), `message` |
| `signin_failed` | Every given profile was refused | `kind: "rejected"`, `reason`, `detail` |
| `offline` | No internet connection | `problem: "E100"` |
| `error` | Anything else | `message`, `problem` (error code, see `docs/ERRORS.md`) |

`video`: `id, title, channel, duration, duration_text, views, views_text, thumbnail, qualities`
(e.g. `["1080p","720p"]`), `quality` (preselected), `sizes`, `size_texts` (per quality, plus `audio`).

`playlist`: `title, channel, count, entries[]` with `title, url, thumbnail, duration_text`.

## Sign-in flow

Used after `needs_login`. The server decides the next step from the user's saved consent, so the UI
stays thin:

1. `POST /api/signin/prepare` with `{"first_time": false}` returns one of:
   - `consent_needed` - show the explanation dialog (Allow / No), then save the answer with
     `PUT /api/settings {"signin_consent": "allow" | "never"}` and call prepare again with
     `first_time: true` if the answer was Allow.
   - `signin_off` - the user said No: show the red notice, no popup.
   - `choose_account` - several signed-in profiles and no saved choice: show the picker (`sources`),
     save the pick with `PUT /api/settings {"cookies_browser", "cookies_profile"}`.
   - `signin_failed` - `kind` is `locked`, `encrypted`, `not_signed_in`, `none_installed` or
     `other` (with `detail`).
   - `ok` - `sources` are ready.
2. Retry `POST /api/fetch` (or `POST /api/downloads`) with the `sources` / `source`.

Cookies are read in memory by yt-dlp for that one request. Nothing is written to disk.

Settings menus:

- `GET /api/signin/accounts` - `{browsers: [{id, profiles: [{profile, label}]}]}`
- `POST /api/signin/test` - checks, with no network request, which profiles can be used:
  `{installed, results: [{browser, profile, label, readable, signed_in, cookies, kind, error}]}`

## Downloads

`POST /api/downloads`

```json
{"url": "https://...", "title": "Video title", "fmt": "mp4", "quality": "720p",
 "subtitles": "none", "source": {"browser": "firefox", "profile": "abc.default"}, "overwrite": false}
```

- `fmt`: `mp4` or `mp3` (`quality` is ignored for mp3). `quality`: `best` or like `720p`.
  `subtitles`: `none`, `auto` or `manual`. `source` is optional (sign-in).
- `{"status": "exists", "filename": "...", "size": bytes, "modified": unix time}` - the file exists and `ask_overwrite` is on. Ask the
  user, then send the same request with `"overwrite": true`.
- `{"status": "started", "job": {...}}` - follow it over `/api/events`.
- `409` if another download, playlist, FFmpeg install or update is running.

`POST /api/playlists/download`:
`{"title", "fmt", "quality", "entries": [{"title", "url"}]}` (1-500 entries). Existing files are
skipped unless `ask_overwrite` is off. A video that needs sign-in is retried with the user's browser
session only if `signin_consent` is `allow`; otherwise it is counted under `signin`.

`GET /api/jobs` lists known jobs. `POST /api/jobs/{id}/cancel` cancels a running one (`404` if it
already finished).

### Job object

```json
{"id": "a1b2c3", "kind": "video", "title": "...", "state": "running", "phase": "downloading",
 "percent": 62.5, "item_percent": 0, "speed": "4.2 MB/s", "eta": "0:12",
 "current": 0, "total": 0, "message": "", "error": null, "summary": null,
 "filepath": "", "filename": "", "cancelling": false}
```

- `kind`: `video`, `playlist`, `ffmpeg`, `update`.
- `state`: `running`, `done`, `failed`, `cancelled`.
- `phase`: `downloading`, then `converting` while FFmpeg merges or converts.
- Playlist: `current`/`total` count videos, `percent` is overall, `item_percent` the current video,
  `summary` is `{done, skipped, failed, signin}` when finished.
- `error`: `{code, message, problem, reason?}`; `problem` is the error code (`E120`, see `docs/ERRORS.md`). `code` is `needs_login` (offer the sign-in flow),
  `signin_rejected` (already signed in and still refused), `failed`, or `internal`.

## Events

`GET /api/events` is a Server-Sent Events stream. Each message is `data: <json>`:

| `type` | When | Payload |
| --- | --- | --- |
| `hello` | On connect | `jobs`: every known job |
| `job` | Any job change (progress is throttled to about 10 per second) | `job` |
| `history_changed` | A download finished or history was edited | |

Keep the stream open while the page is open: **the server exits about 45 seconds after the last
page disconnects**, unless a task is running or `--keep-running` was given.

## Queue

Several single videos, downloaded one at a time. State is saved in `queue.json` in the data folder, so
the queue survives closing the app. Each video runs as a normal `video` job (same progress, sign-in
fallback, history and subtitle rules). See `docs/PLAN_QUEUE_RESUME.md`.

The Playlist page sends its selected videos here. `POST /api/playlists/download` (a separate, non-resumable playlist job) is still available but the web UI no longer uses it.

Item `status`: `fetching`, `ready`, `queued`, `downloading`, `paused`, `exists` (file on disk, waiting for
the user), `done`, `skipped`, `cancelled`, `failed` (with `error.problem`, an E-code).

| Method | Path | Body / query | Result |
|---|---|---|---|
| GET | `/api/queue` | | `state` (`idle`/`running`/`paused`), `halted` (`disk_full` or empty), `settings`, `items`, `counts`, `estimate_bytes`, `partial_bytes`, `free_bytes`, `max_items` |
| POST | `/api/queue/items` | `{"urls": [...], "fmt"?, "quality"?, "start"?, "partial"?}` (the queue holds 50 in total) | `{"added": [ids], "duplicates": n, "left_out": n}`; 422 above the limit unless `partial` is true (add what fits). `fmt`/`quality` apply to just these links, `start` queues each as soon as its info is loaded (the Playlist page uses all three). Metadata loads in the background |
| PATCH | `/api/queue/items/{id}` | `{"fmt"?, "quality"?}` | quality falls back to the closest lower one |
| DELETE | `/api/queue/items/{id}` | `?discard=true` also deletes its unfinished data | stops it first if it is downloading |
| POST | `/api/queue/items/{id}/cancel` | `{"discard": bool}` | cancel one video, the queue goes on |
| POST | `/api/queue/items/{id}/exists` | `?replace=true|false` | answer for an item in status `exists` |
| PUT | `/api/queue/settings` | `{"sub_langs"?, "on_exists"?: skip/replace/ask, "fmt"?, "quality"?}` | `quality` is applied to every waiting video |
| POST | `/api/queue/start` | | queue every `ready`/`paused` item and start the runner |
| POST | `/api/queue/pause` | | stops the current download, keeps its unfinished data, starts nothing else |
| POST | `/api/queue/resume` | | same as start |
| POST | `/api/queue/retry` | `{"ids"?, "source"?}` | failed/cancelled (and named skipped) items go back to the queue; `source` = signed-in browser profile |
| POST | `/api/queue/clear-finished` | | removes done, skipped and cancelled items |
| DELETE | `/api/queue/partials` | | deletes all unfinished data; 409 while running |

Subtitles: `sub_langs` is one preference for the whole batch. Each video gets the languages from it that it
really has (channel subtitles, or its original automatic caption); the others are skipped silently.
Items listed with `sub_manual`/`sub_auto` show what will be downloaded.

Events: `queue_changed` (reload `GET /api/queue`) and `queue_progress` (`id`, `percent`, `speed`, `eta`,
`phase`, sent about twice a second for the running item).

The server does not exit while the queue is running. With the `queue_auto_resume` setting on, unfinished
items continue by themselves when the app starts.

## History and folders

| Route | Description |
| --- | --- |
| `GET /api/history` | `{entries}`, newest first. Each entry has `id, title, channel, url, format, quality, filesize, filename, filepath, thumbnail, date` plus `exists` (is the file still there) and `size` (bytes on disk, 0 if gone). The response also has `total_bytes` (all existing files) and `existing` (how many). |
| `DELETE /api/history` | Clear all. |
| `DELETE /api/history/{id}` | Remove one. |
| `POST /api/history/{id}/reveal` | Show the file in the file manager. The path comes from the history file, never from the request. |
| `POST /api/folder/open` | Open the download folder. |
| `POST /api/folder/pick` | System folder dialog on this machine: `{status: "ok", path}`, `cancelled`, or `unavailable` (the UI then lets the user type the path). |

## Maintenance

| Route | Description |
| --- | --- |
| `POST /api/ffmpeg/install` | Starts a job (`kind: ffmpeg`) using winget, Homebrew or apt. |
| `GET /api/updates` | `{installed, latest, newer, can_self_update}`. `latest` is `null` when offline. |
| `POST /api/updates/install` | Starts a yt-dlp update job. `400` for a packaged build (download a new release instead). |
| `POST /api/shutdown` | Stop the server (`409` while a task runs). |
| `POST /api/restart` | Start a fresh copy, then stop (used after a yt-dlp update). |

## Subtitles

A fetched video has `subtitles: {manual: [...], auto: [...], translated: [...]}` (language codes).
`manual` are uploaded by the channel, `auto` is the original spoken language, `translated` are YouTube's
on-demand translations (often answered with HTTP 429).

`POST /api/downloads` accepts `sub_manual` and `sub_auto` (lists of those codes). When either is not
empty the video and its subtitles (`.srt`) are saved together in a folder named after the video. If
YouTube refuses the subtitles the video is still saved; the finished job has `message:
"subtitles_skipped"`.

### History cleanup

- `DELETE /api/history?delete_files=true` clears the list and deletes every downloaded file in it (same rules as below). Response adds `deleted`: number of files removed.

- `DELETE /api/history/{id}?delete_file=true` also deletes the downloaded video and its `<name>.<lang>.srt` files, then the per-video folder if it is empty. The path comes from the history file, only `.mp4`/`.mp3` files are touched. Response: `{"status": "ok", "deleted": [names]}`.
- `POST /api/history/prune` removes every entry whose file no longer exists. Response: `{"status": "ok", "removed": n}`.
