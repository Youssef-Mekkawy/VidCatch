# YouTube Downloader

A desktop app for downloading videos, audio and playlists from YouTube. Built with
[CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) and
[yt-dlp](https://github.com/yt-dlp/yt-dlp). Dark theme, English and Arabic (RTL) interface.

![Main window](docs/screenshots/main.png)

## Features

- Download video (MP4) or audio only (MP3) in the quality you choose (best, 1080p, 720p, 480p, 360p)
- Playlist support with per-item progress, a downloaded / skipped / failed summary, and cancel
- Download queue: paste many links, choose one quality for all or per video, and download them one by one
- Pause and resume: unfinished downloads are kept (for 7 days) and continue from where they stopped, even after
  closing the app
- Subtitle picker: every language the video has; the video and its `.srt` files go in a folder named after it
- Storage overview: total size of your downloads in History, estimated size and free space in the queue
- Live progress with speed and ETA, cancel at any time
- Download history with "Show in folder" and "Remove"
- Settings panel: download folder, default format and quality, browser cookies for sign-in, overwrite prompt, and more
- English and Arabic interface with full right-to-left layout
- Videos are always tried without logging in first. Only if YouTube refuses one (age-restricted,
  private, members-only, or its "confirm you're not a bot" check) does the app offer to use the YouTube
  session of a browser you are already logged in with, and only if you allow it. You choose the browser
  and the account (browser profile) in Settings, and can turn it off at any time. Nothing is saved: no
  cookie files, only the browser and profile name
- Checks for a newer yt-dlp on start and updates it with one click (can be turned off in Settings)
- Settings and history are stored in a per-user folder, not next to the code

| History | Settings | Playlist |
| --- | --- | --- |
| ![History](docs/screenshots/history.png) | ![Settings](docs/screenshots/settings.png) | ![Playlist](docs/screenshots/playlist.png) |

> The screenshots use sample data.

## Requirements

- Python 3.10 or newer, with Tk (included in the Windows and macOS installers; on Debian/Ubuntu
  install `python3-tk`)
- [FFmpeg](https://ffmpeg.org/) — needed to merge video and audio and to make MP3 files.
  The app offers to install it on first start (winget on Windows, Homebrew on macOS, apt on Linux).
- A JavaScript runtime — [Deno](https://deno.com/) (recommended), [Node.js](https://nodejs.org/)
  or [Bun](https://bun.sh/). Optional: the app first tries without one and uses it only as a
  retry when YouTube answers `HTTP Error 403` or hides formats. The app shows a banner if none is found.

## Install and run

```bash
git clone <your-repo-url>
cd youtube-downloader

python -m venv .venv
# Windows: .venv\Scripts\activate      macOS/Linux: source .venv/bin/activate

pip install -r requirements.txt
python main.py
```

You can also install it as a package, which adds a `youtube-downloader` command:

```bash
pip install .
youtube-downloader
```

Keep yt-dlp up to date, because YouTube changes often:

```bash
pip install -U "yt-dlp[default]"
```

## Web version (preview)

The next version replaces the desktop window with a web page served from your own computer:

```bash
python -m ytdownloader.server --no-browser --keep-running   # prints the local address; open it in a browser
```

Developer documentation: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/API.md](docs/API.md).

## Where your data is stored

| OS | Folder |
| --- | --- |
| Windows | `%APPDATA%\YouTubeDownloader` |
| macOS | `~/Library/Application Support/YouTubeDownloader` |
| Linux | `~/.config/youtube-downloader` |

The folder holds `config.json`, `history.json`, `queue.json` (the download queue) and a `temp/` folder used
while downloading (it keeps the unfinished downloads under `partial_*` for up to 7 days).
Set the `YTDL_DATA_DIR` environment variable to use a different location.
A `config.json` or `history.json` from an older version next to the script is imported on first start.

## Project structure

```
main.py                  Launcher (python main.py)
ytdownloader/
  app.py                 Main window, task tracking, language and settings panel
  downloader.py          yt-dlp wrapper: options, formats, progress, download
  config.py              Settings and history storage
  system.py              FFmpeg, JS runtime detection, open folder, internet check
  signin.py              Browser profiles, cookie checks, sign-in fetch (nothing is written to disk)
  updates.py             yt-dlp update check (PyPI) and one-click update
  server/                Local web server: API, background jobs, security, launcher
  web/                   Browser UI files (placeholder page for now)
  i18n.py                English and Arabic strings
  theme.py               Colors, sizes, fonts
  utils.py               Formatting and file name helpers
  ui/
    layout.py            Shared widgets, RTL helper, thumbnail loader
    header.py            Tabs, language button, settings button
    main_view.py         Single video: fetch, preview, download
    playlist_view.py     Playlist selection and download
    history_view.py      Download history
    settings_panel.py    Slide-in settings
    account_picker.py    "Which account?" dialog
tests/                   pytest suite (no network needed)
```

Worker threads never touch widgets directly. They call `app.post(fn)`, which queues the call and
runs it on the Tk main thread.

## Development

```bash
pip install -e ".[dev]"

ruff check .      # lint
pytest -q         # tests
```

CI runs lint, a byte-compile check and the tests on Linux and Windows (Python 3.10 and 3.12).

When you add a string, add it to both languages in `ytdownloader/i18n.py`; the tests fail if the
two languages drift apart or a key is unused.

## Troubleshooting

**`HTTP Error 403: Forbidden`, or "Only images are available" in the log**
Install Deno or Node.js, restart the app, and update yt-dlp (`pip install -U "yt-dlp[default]"`).

**"Sign in needed" (age-restricted, private, members-only, or the bot check)**
YouTube will not give these videos to anonymous visitors. The first time it happens the app explains and
asks for permission. If you allow it, it reads the YouTube session of a browser where you are logged in.
Cookies stay on your PC and are sent only to YouTube; the app stores just the browser and profile name.

- Open Settings > Sign-in to pick the browser and account, and press **Test sign-in** to see which
  browsers can be used.
- **Firefox** is the most reliable, even while it is open.
- **Chrome and Edge on Windows** often lock their cookie database while open (close the browser fully),
  and newer versions encrypt cookies in a way yt-dlp may not be able to read. Use Firefox if the test
  reports "locked" or "encrypted".
- Using an account with yt-dlp can get it limited
  ([details](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies)), so a spare
  account is safer.
- If it still fails: update yt-dlp (the app offers this, or run `pip install -U "yt-dlp[default]"`) and
  try without a VPN.

**"FFmpeg not found"**
Install FFmpeg and make sure `ffmpeg` works in a terminal, then restart the app.

**Arabic text looks disconnected on Linux**
Some Tk builds on Linux do not shape Arabic letters. Windows and macOS render it correctly.

## Legal

Download only content you own or have permission to download. Respect YouTube's Terms of Service
and the copyright laws of your country. This project is not affiliated with YouTube or Google.

## License

[MIT](LICENSE)
