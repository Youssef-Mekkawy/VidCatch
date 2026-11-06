# Architecture

VidCatch runs **on the user's own computer**. A small Python server (FastAPI) wraps
yt-dlp; the user interface is a web page (plain HTML, CSS and JavaScript) shown in a browser or an
app-style window.

```
 Browser tab / app window            Local server (127.0.0.1 only)
 ┌──────────────────────┐  HTTP    ┌────────────────────────────────────────┐
 │ vidcatch/web/    │ ───────▶ │ server/api.py      JSON routes         │
 │  HTML, CSS, JS       │ ◀─────── │ server/jobs.py     background jobs     │
 └──────────────────────┘   SSE    │ server/events.py   live progress hub   │
                                   │ server/security.py token, Host, Origin │
                                   ├────────────────────────────────────────┤
                                   │ engine (no web code, easy to test):    │
                                   │ downloader.py  signin.py  config.py    │
                                   │ system.py      updates.py utils.py     │
                                   └────────────────────────────────────────┘
```

Because the server is local, it can use the user's browser sign-in (cookies) and write to the
user's disk. That is also why the server is locked down (see Security).

> The original CustomTkinter desktop app (`app.py`, `ui/`, `theme.py`) still exists while the web
> UI is being built. It uses the same engine modules.

## Modules

| Module | Responsibility |
| --- | --- |
| `downloader.py` | yt-dlp options, metadata, progress hook, download to a temp folder then move. With `resume=True` the folder is a stable `partial_<key>` that survives cancel/failure (see below). JS-runtime retry. No UI code. |
| `signin.py` | Find browser profiles, check (offline) which ones hold a YouTube sign-in, fetch with each. Never writes cookies anywhere. |
| `config.py` | `config.json` and `history.json` in the per-user data folder; validation of settings updates. |
| `system.py` | OS helpers: FFmpeg, JavaScript runtime, browser profile folders, open folder. |
| `updates.py` | PyPI check and `pip install --upgrade yt-dlp[default]`. |
| `server/app.py` | Builds the FastAPI app, mounts `web/`, adds the security middleware. |
| `server/api.py` | All routes. Documented in [API.md](API.md). |
| `server/jobs.py` | `JobManager`: one thread per job, state pushed through the event hub. |
| `server/events.py` | Thread-safe hub: worker threads publish, asyncio queues deliver to each browser. |
| `server/security.py` | ASGI middleware: Host check, Origin check, session cookie, security headers. |
| `server/launcher.py` | CLI entry: single instance, port choice, opening the window, exiting when the page closes. |
| `server/platform_ui.py` | Folder dialog (PowerShell / osascript / zenity) and app-window browser lookup. |
| `server/serialize.py`, `schemas.py` | Shape of responses, validation of requests. |

## Request flow: a download that needs sign-in

1. UI calls `POST /api/fetch` - the server asks yt-dlp **without cookies**.
2. YouTube refuses (age restriction, bot check...) - response `status: needs_login`.
3. UI calls `POST /api/signin/prepare`. The server looks at the saved consent:
   `ask` - UI shows the explanation dialog; `never` - UI shows the red notice; `allow` - the server
   checks which browser profiles hold a YouTube sign-in and returns them.
4. UI retries `POST /api/fetch` / `POST /api/downloads` with the chosen `source`. yt-dlp reads that
   profile's cookies in memory for this single request.

The same rule holds everywhere: **plain first, cookies only after YouTube refused and the user allowed it.**

## Threads and live progress

- Routes are plain `def` functions, so FastAPI runs them in a thread pool; blocking yt-dlp calls are fine.
- A download runs in its own thread (`JobManager`). yt-dlp calls the progress hook, the hook calls
  `Job.set(...)`, which publishes a `job` event.
- `EventHub.publish` is safe from any thread (`call_soon_threadsafe` into each browser's asyncio queue).
- Only one "exclusive" job (download, playlist, FFmpeg install, yt-dlp update) runs at a time.

## Web interface

`vidcatch/web/` is served as-is (no build step, no npm).

```
web/
  index.html            one page, loads css/ and js/app.js
  css/                  tokens.css (colors, sizes: change the look here), base, components, pages
  fonts/                bundled woff2 fonts (OFL, see fonts/LICENSES.md)
  locales/en.json ar.json   all texts; both files must have the same keys (a test checks it)
  js/app.js             shell: header, hash routes (#/ #/playlist #/history #/settings), SSE wiring
  js/state.js           shared state + tiny event bus; pages are rebuilt from it
  js/lib/               api.js (all server calls), events.js (SSE), i18n.js, dom.js, format.js
  js/components/        dialog, notice, banners
  js/views/             download, playlist, history, settings
  js/signin.js          the sign-in consent flow
```

Rules: build DOM with `h()` and text nodes only (never HTML strings: video titles are untrusted),
no inline scripts or styles (the Content-Security-Policy forbids them), every text through `t()`.

## Security

The server can read browser cookies and write files, so a random web page must never drive it:

1. **Host check** - only `127.0.0.1`, `localhost`, `[::1]` on our port (stops DNS rebinding).
2. **Origin check** - a request with a foreign `Origin` is rejected.
3. **Session cookie** - `/api/*` needs the secret token (`HttpOnly`, `SameSite=Strict`), handed over by the launcher in `/?t=...`.
4. The server binds `127.0.0.1` only. Security headers (CSP, `nosniff`, `X-Frame-Options`) go on every response.
5. Input is validated: URLs must be `http(s)`, browser profile names cannot contain path characters
   (yt-dlp treats the value as a path), history actions use paths from our own history file.

## Lifecycle

- `server.json` (port, token, pid) in the data folder marks the running instance. A second launch
  finds it, opens a window on it and exits.
- The page keeps `/api/events` open. About 45 seconds after the last page disconnects (and no task
  runs) the server exits. `--keep-running` disables this.
- `POST /api/restart` starts a new copy (`YTDL_RESTART=1` makes it wait for the old one to let go).

## Data folder

`%APPDATA%\VidCatch` (Windows), `~/Library/Application Support/VidCatch` (macOS),
`~/.config/vidcatch` (Linux), or `YTDL_DATA_DIR`. It holds `config.json`, `history.json`,
`server.json`, `server.log` and `temp/`.

## Tests

`pytest -q` needs no network. Engine tests mock yt-dlp. `tests/test_server_api.py` drives the API
in-process with fake downloads; `tests/test_server_process.py` starts the real server in a
subprocess (cookie hand-off, event stream, single instance, idle exit); `tests/test_server_security.py`
checks the Host/Origin/cookie rules.


## Resumable downloads

`download_media(..., resume=True)` (used by the web jobs) downloads into `temp/partial_<key>/`, where the
key is a hash of video id + format + quality. yt-dlp keeps `.part` files there and continues from them on the
next run (HTTP Range). The folder is removed after success, by `discard_partial`, or by
`cleanup_old_partials` (older than 7 days, run at server start). If a resumed run fails with a range or
corruption error, the folder is wiped and that download starts again once. The final file is staged as
`<name>.tmp` in the destination folder and renamed over the target (`os.replace`), so the real name only ever
points to a complete file. The Tk app still calls `download_media` without `resume` (old behaviour).

## Queue

`server/queue_manager.py` keeps the queue (`queue.json`, atomic writes) and runs one background thread that
starts each item as a normal `video` job and waits for it (`Job.finished`). Metadata for newly added links is
fetched by a second thread, one link at a time. Pause cancels the running job and keeps its partial folder;
a restart turns `downloading` items into `paused`. Routes are in `server/queue_api.py`.
