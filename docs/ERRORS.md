# Error codes

Every failure shown to the user carries a code such as `E120`. The code appears in the red box next to
a plain title, what happened, and numbered steps the user can follow. Developers: the registry is
`ytdownloader/errors.py`; the texts are `err_<code>_title|text|steps` in `ytdownloader/web/locales/*.json`.

To add a code: add a rule (or a fixed code) in `errors.py`, add the three texts in **both** languages
and add a row here. `tests/test_errors.py` fails when a text is missing, and
`tests/test_docs.py` fails when a code is missing from this file.

| Code | Name | Title (English) | Typical cause |
| --- | --- | --- | --- |
| `E100` | `no_connection` | No internet connection | `getaddrinfo` / `name or service not known` / `temporary failure in name resolution` |
| `E101` | `timeout` | The connection timed out | `timed out` / `timeout` / `read operation` |
| `E110` | `bot_check` | YouTube wants you to sign in | `not a bot` / `use --cookies` / `sign in` |
| `E111` | `age_restricted` | This video is age-restricted | `confirm your age` / `age-restricted` / `inappropriate for some users` |
| `E112` | `private_video` | This video is private | `private video` |
| `E113` | `members_only` | Members-only video | `members-only` / `join this channel` |
| `E114` | `signin_rejected` | YouTube still said no after sign-in | Set by the app (sign-in flow, validation or connection) |
| `E115` | `browser_locked` | The browser keeps its cookies locked | Set by the app (sign-in flow, validation or connection) |
| `E116` | `cookies_encrypted` | The browser's cookies cannot be read | Set by the app (sign-in flow, validation or connection) |
| `E117` | `not_signed_in` | Not signed in to YouTube in that browser | Set by the app (sign-in flow, validation or connection) |
| `E118` | `no_browser` | No supported browser found | Set by the app (sign-in flow, validation or connection) |
| `E119` | `signin_off` | Sign-in was not used | Set by the app (sign-in flow, validation or connection) |
| `E120` | `video_unavailable` | Video unavailable | `video unavailable` / `has been removed` / `no longer available` |
| `E121` | `geo_blocked` | Not available in your country | `available in your country` / `blocked it in your country` / `geo restricted` |
| `E122` | `copyright_blocked` | Blocked for copyright | `copyright` / `blocked it on copyright` |
| `E123` | `live_not_ready` | The live stream is not ready | `live event will begin` / `premieres in` / `this live event` |
| `E130` | `ffmpeg_missing` | FFmpeg is missing | `ffmpeg is not installed` / `ffprobe and ffmpeg not found` / `ffmpeg not found` |
| `E131` | `ffmpeg_failed` | FFmpeg could not convert the file | `postprocessing` / `ffmpeg exited` / `conversion failed` |
| `E140` | `disk_full` | The disk is full | `no space left` / `disk full` / `errno 28` |
| `E141` | `no_permission` | The app cannot write to the folder | `permission denied` / `access is denied` / `errno 13` |
| `E142` | `bad_file_name` | File name or folder path too long | `file name too long` / `filename too long` / `errno 36` |
| `E143` | `file_in_use` | The file is open in another program | `being used by another process` / `sharing violation` |
| `E150` | `http_forbidden` | YouTube blocked the request (403) | `http error 403` / `forbidden` |
| `E151` | `rate_limited` | Too many requests | `http error 429` / `too many requests` / `rate-limit` |
| `E152` | `server_error` | YouTube has a problem right now | `http error 5` / `bad gateway` / `service unavailable` |
| `E160` | `unsupported_url` | This link is not supported | `unsupported url` / `is not a valid url` / `no suitable extractor` |
| `E161` | `bad_link` | That is not a valid link | Set by the app (sign-in flow, validation or connection) |
| `E170` | `format_unavailable` | That quality is not available | `requested format is not available` / `no video formats found` / `format is not available` |
| `E171` | `subtitles_failed` | Subtitles could not be saved | `no subtitle files` / `unable to download video subtitles` |
| `E180` | `extractor_broken` | YouTube changed something | `unable to extract` / `nsig` / `player response` |
| `E900` | `unknown` | Something went wrong | Set by the app (sign-in flow, validation or connection) |
| `E910` | `internal` | Unexpected error inside the app | Set by the app (sign-in flow, validation or connection) |
| `E920` | `app_unreachable` | The app is not running | Set by the app (sign-in flow, validation or connection) |
| `E930` | `busy` | Another task is running | Set by the app (sign-in flow, validation or connection) |

## Where codes come from

- `POST /api/fetch` returns `problem` with `status: error` and `offline`.
- A failed job has `error.problem` (see `docs/API.md`).
- `classify(message)` maps raw yt-dlp or operating-system text to a code; `E900` means nothing matched.
  If you see `E900` often for the same message, add a rule for it.
- The interface can copy a bug-report block (code, message, app and yt-dlp versions, platform). It never
  contains links, file names or account names.
