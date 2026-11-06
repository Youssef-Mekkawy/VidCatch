# Changelog

## 1.0.0 - 2026-11-06

First release under the name **VidCatch** (the project was called YouTube Downloader before).

### Added
- Ready-to-run builds for Windows, Linux and macOS (PyInstaller, `packaging/`). A GitHub Actions workflow builds
  them when a version tag is pushed, smoke-tests each one, and attaches them with SHA-256 checksums to the release.

### Renamed
- The app, the Python package (`vidcatch`), the commands (`vidcatch`, `vidcatch-desktop`) and the data folder
  are now called VidCatch. An existing `YouTubeDownloader` data folder (settings, history, queue) is renamed
  automatically on first start.

### Changed
- The Playlist page now adds the selected videos to the queue (one by one, pause/resume, survives a
  restart) instead of running its own non-resumable job.

### Added
- Settings: **Temporary folder** (where unfinished downloads are kept) can be moved to another drive when the
  system drive is full. Existing unfinished downloads move with it; refused while a download is running.
  `GET /api/storage` returns free space and unfinished-download size.
- Pause on the Download page: a single download can be paused, survives closing the app, and is continued
  from the Queue tab (`POST /api/jobs/{id}/pause`, `POST /api/queue/items/{id}/resume`).
- Settings switch "Continue the queue when the app starts"; the Queue tab warns when unfinished data is
  about to be deleted (kept 7 days).
- History shows each file's size and a total ("12 downloads on disk · 4.8 GB").
- Download queue (engine and API): add up to 50 single videos, one quality for all or per video, a batch
  subtitle language preference, one download at a time, pause/resume/retry/cancel, saved in `queue.json`,
  recovery after a restart and the `queue_auto_resume` setting.
- Queue tab: paste links, one quality for all or per video, subtitle languages for the batch, live
  progress, pause/resume, retry, error codes with steps, estimated total size and free space.
- Resumable downloads (engine): unfinished data is kept in `temp/partial_<key>/` when a download is
  cancelled or fails, and the next run continues from it. Damaged partial data is dropped and fetched
  again. Finished files are moved into place atomically, so a half-written video never appears.
  Unfinished downloads older than 7 days are deleted at start. See `docs/PLAN_QUEUE_RESUME.md`.
- Subtitle picker: lists every language (channel-uploaded, automatic original, automatic translations),
  pick one or more; the video and its subtitles are saved in a folder named after the video.
- Error codes (`E100`...`E930`) with a plain title, an explanation and numbered steps in English and
  Arabic, a collapsible technical detail and a "Copy details" bug-report block. See `docs/ERRORS.md`.
- "Already downloaded" dialog: skip, show the folder, or download again.
- Browser interface (`vidcatch/web/`): Download, Playlist, History and Settings pages, sign-in
  consent dialog and account picker, update and FFmpeg banners, English and Arabic (right-to-left).
  Plain CSS with design tokens, vanilla ES modules, bundled fonts, no build step, works offline.
- Local web server (`python -m vidcatch.server`): a FastAPI JSON API around the existing engine,
  with live progress over Server-Sent Events. The CustomTkinter app keeps working meanwhile. See `docs/API.md` and `docs/ARCHITECTURE.md`.
- Security for the local server: only `127.0.0.1`, Host and Origin checks, a secret session cookie,
  security headers, validated input.
- Single instance (a second launch reuses the running server), automatic exit shortly after the
  last page closes, `--port`, `--browser`, `--no-browser`, `--keep-running`.
- Folder picker without tkinter (PowerShell, osascript, zenity/kdialog).
- Settings updates are validated and reported (`config.update_config`).

## 2.0.0

### Changed
- Restructured into the `vidcatch` package (flat modules plus one `ui/` folder; no mixins).
- Settings and history now live in a per-user folder instead of next to the script
  (`%APPDATA%\VidCatch` on Windows, `~/Library/Application Support/VidCatch`
  on macOS, `~/.config/vidcatch` on Linux). Existing `config.json` and `history.json`
  files from a source checkout are imported automatically on first start.
- Redesigned UI with the same colour palette: segmented controls, a fixed header, a settings panel
  that slides in under it, an empty state for the video card, rounded thumbnails.
- The language is applied by rebuilding the views, which replaces the per-widget right-to-left
  patches. Changing language is blocked while a download is running.
- Emoji and symbols that rendered as empty boxes on Windows were replaced with plain text or
  characters every font has.

### Fixed
- Arabic channel names scrambled the numbers in the video info line; channel, duration and views are
  now separate labels.
- HTTP 403 / missing formats. Every request first runs with plain yt-dlp options. Only if that fails
  with a 403 or missing formats does the app retry once with the installed JavaScript runtime
  (Deno, Node.js or Bun), so users who do not need one never trigger YouTube's stricter checks.
- Downloads with subtitles could move the `.srt` file instead of the video. The finished file is now
  taken from yt-dlp's own result, and subtitles are saved next to the video.
- Cancelling a playlist download could be lost between items; each task now has its own cancel flag.
- The Download button could save a video under the wrong title if the link was edited after
  fetching. The link must now match the fetched video.
- Every download uses its own temp folder, which is removed afterwards. Leftovers from a crash are
  cleaned up on the next start.
- Merged files are always real MP4 files (previously a different container could be renamed to .mp4).
- Settings and history are written atomically, and invalid values in `config.json` fall back to
  defaults instead of breaking the app.

### Added
- Sign-in only when needed and only with your permission. Every request first runs without cookies.
  If YouTube refuses a video (age restriction, private, members-only, bot check) the app explains why,
  asks once (Allow / No), and if allowed uses the YouTube session of a browser profile you choose. If you
  decline, a red notice in the video card says the video needs sign-in and how to enable it, with no popup.
  Settings > Sign-in has the on/off switch, browser and account menus and a **Test sign-in** button.
  Nothing is written to disk: no cookie files, only the browser and profile name in `config.json`.
  Clear messages for a locked cookie database, encrypted cookies, or no signed-in browser.
- yt-dlp update check on start with a banner and one-click update and restart (source installs;
  switch in Settings > Behavior). Frozen builds only show a notice.
- ETA next to the download speed.
- Playlist downloads skip files that already exist (unless "Ask before overwriting" is off) and end
  with a downloaded / skipped / failed summary.
- Tests (`pytest`), lint configuration (`ruff`), GitHub Actions workflow, `pyproject.toml`.

## 1.0

- Initial version.
