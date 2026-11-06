"""Error codes.

Every failure the user can see gets a short code (E120...) so that
  - the user can search for it or send it in a bug report,
  - the interface can show plain-language steps for that exact problem,
  - developers can see at a glance what went wrong (docs/ERRORS.md lists them all).

`classify()` turns the raw yt-dlp / OS message into a code. The texts shown to the user live in
web/locales/*.json under `err_<code>_title`, `err_<code>_text` and `err_<code>_steps`.
Add a code here, add its three texts in both languages, and the tests tell you if one is missing.
"""

from __future__ import annotations

# code -> (short name for developers, substrings of the lowercase message that identify it)
# Order matters: the first match wins, so specific causes come before general ones.
RULES: list[tuple[str, str, tuple[str, ...]]] = [
    ("E111", "age_restricted", ("confirm your age", "age-restricted", "inappropriate for some users")),
    ("E112", "private_video", ("private video", "playlist does not exist", "playlist is private")),
    ("E113", "members_only", ("members-only", "join this channel")),
    ("E110", "bot_check", ("not a bot", "use --cookies", "sign in")),
    ("E121", "geo_blocked", ("available in your country", "blocked it in your country", "geo restricted", "geo-restricted")),
    ("E122", "copyright_blocked", ("copyright", "blocked it on copyright")),
    ("E123", "live_not_ready", ("live event will begin", "premieres in", "this live event", "is offline")),
    ("E120", "video_unavailable", (
        "video unavailable", "has been removed", "no longer available", "does not exist",
        "account associated", "http error 404", "this video is not available", "been terminated",
    )),
    ("E160", "unsupported_url", ("unsupported url", "is not a valid url", "no suitable extractor")),
    ("E140", "disk_full", ("no space left", "disk full", "errno 28", "not enough space")),
    ("E142", "bad_file_name", ("file name too long", "filename too long", "errno 36", "path too long", "invalid argument")),
    ("E143", "file_in_use", ("being used by another process", "sharing violation")),
    ("E141", "no_permission", ("permission denied", "access is denied", "errno 13", "read-only file system", "operation not permitted")),
    ("E130", "ffmpeg_missing", ("ffmpeg is not installed", "ffprobe and ffmpeg not found", "ffmpeg not found", "ffmpeg or avconv")),
    ("E131", "ffmpeg_failed", ("postprocessing", "ffmpeg exited", "conversion failed", "ffmpeg error")),
    ("E151", "rate_limited", ("http error 429", "too many requests", "rate-limit", "rate limit")),
    ("E150", "http_forbidden", ("http error 403", "forbidden")),
    ("E152", "server_error", ("http error 5", "bad gateway", "service unavailable", "internal server error")),
    ("E101", "timeout", ("timed out", "timeout", "read operation")),
    ("E100", "no_connection", (
        "getaddrinfo", "name or service not known", "temporary failure in name resolution",
        "network is unreachable", "connection refused", "connection reset", "connection aborted",
        "urlopen error", "unable to download webpage", "no internet",
    )),
    ("E170", "format_unavailable", ("requested format is not available", "no video formats found", "format is not available")),
    ("E171", "subtitles_failed", ("no subtitle files", "unable to download video subtitles")),
    ("E180", "extractor_broken", ("unable to extract", "nsig", "player response", "please report this issue", "sabr")),
]

# Codes that are not produced from a message (the app or the sign-in flow decides them).
FIXED: dict[str, str] = {
    "E114": "signin_rejected",     # signed in and YouTube still refused
    "E115": "browser_locked",      # the browser keeps its cookie file locked
    "E116": "cookies_encrypted",   # the cookies cannot be decrypted
    "E117": "not_signed_in",       # the browser is not signed in to YouTube
    "E118": "no_browser",          # no supported browser found
    "E119": "signin_off",          # the user said "No" to sign-in
    "E161": "bad_link",            # the link is not an http(s) link
    "E900": "unknown",             # nothing matched
    "E910": "internal",            # bug inside the app
    "E920": "app_unreachable",     # the page cannot reach the local server
    "E930": "busy",                # another task is running
}

NAMES: dict[str, str] = {code: name for code, name, _ in RULES} | FIXED
ALL_CODES = sorted(NAMES)


def classify(message: str) -> str:
    """The error code for a raw error message ("E900" when nothing matches)."""
    lowered = (message or "").lower()
    for code, _, markers in RULES:
        if any(marker in lowered for marker in markers):
            return code
    return "E900"
