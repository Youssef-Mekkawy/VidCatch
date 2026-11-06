"""Sign-in with the user's own browser session, only when YouTube demands it.

How it works: yt-dlp reads the cookies of one browser profile *in memory* for a single request
and sends them only to YouTube. This module never writes cookies anywhere; the app remembers just
the browser and profile *names* in config.json.

Pieces:
    list_profiles / candidate_sources   which browser profiles ("accounts") exist
    probe_source / resolve_sources      check, without any network request, which ones are readable
                                        and signed in to YouTube (and why not, if they are not)
    fetch_with_sources                  fetch a video with the first profile YouTube accepts
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import yt_dlp

from .downloader import clean_error, cookie_error_kind, fetch_video_info
from .system import browser_profile_dirs, detect_browsers

# Cookies YouTube sets only for a signed-in session.
SIGNED_IN_COOKIES = frozenset({"LOGIN_INFO", "__Secure-3PSID", "__Secure-1PSID", "SAPISID"})
CHROMIUM_BROWSERS = frozenset({"chrome", "edge", "brave", "vivaldi", "chromium"})


@dataclass(frozen=True)
class Source:
    """One browser profile whose cookies may be used."""

    browser: str
    profile: str = ""  # folder name of the profile; "" = the browser's most recently used one
    label: str = ""  # what the user sees, e.g. "Profile 1 (name@gmail.com)"

    @property
    def cookies(self) -> tuple[str, str]:
        return (self.browser, self.profile)

    @property
    def title(self) -> str:
        name = self.browser.capitalize()
        return f"{name} - {self.label}" if self.label else name


# ── Finding browser profiles ────────────────────────────────────────────────
def _firefox_profiles(root) -> list[Source]:
    folders = [p for p in root.iterdir() if (p / "cookies.sqlite").exists()]
    folders.sort(key=lambda p: ("default-release" not in p.name, p.name))
    return [Source("firefox", p.name, p.name.split(".", 1)[-1]) for p in folders]


def _chromium_profiles(browser: str, root) -> list[Source]:
    names: dict = {}
    try:
        state = json.loads((root / "Local State").read_text(encoding="utf-8"))
        names = state.get("profile", {}).get("info_cache", {}) or {}
    except (OSError, ValueError):
        pass
    folders = [
        p.name
        for p in root.iterdir()
        if p.is_dir()
        and (p.name == "Default" or p.name.startswith("Profile "))
        and ((p / "Network" / "Cookies").exists() or (p / "Cookies").exists())
    ]
    folders.sort(key=lambda name: (name != "Default", name))
    sources = []
    for folder in folders:
        info = names.get(folder, {})
        label = info.get("name") or folder
        if info.get("user_name"):
            label = f"{label} ({info['user_name']})"
        sources.append(Source(browser, folder, label))
    return sources


def list_profiles(browser: str) -> list[Source]:
    """Profiles of one installed browser, main profile first. Empty if it is not installed."""
    root = browser_profile_dirs().get(browser)
    if root is None or not root.exists():
        return []
    found: list[Source] = []
    try:
        if browser == "firefox":
            found = _firefox_profiles(root)
        elif browser in CHROMIUM_BROWSERS:
            found = _chromium_profiles(browser, root)
    except OSError:
        found = []
    return found or [Source(browser)]


def candidate_sources(browser: str = "", profile: str = "") -> list[Source]:
    """The profiles to try for the user's setting. browser "" means automatic (every browser)."""
    if not browser:
        return [source for name in detect_browsers() for source in list_profiles(name)]
    sources = list_profiles(browser)
    if profile:
        match = [s for s in sources if s.profile == profile]
        return match or [Source(browser, profile, profile)]
    return sources[:1] if sources else [Source(browser)]


# ── Checking a profile (local only, no network) ─────────────────────────────
class _Collector:
    """yt-dlp logger that keeps messages, so 'could not decrypt' can be told apart."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def debug(self, message: str) -> None:
        self.messages.append(str(message))

    info = warning = error = debug


@dataclass
class Probe:
    source: Source
    readable: bool
    signed_in: bool
    cookies: int = 0  # YouTube/Google cookies found
    kind: str = ""  # "" | "locked" | "encrypted" | "not_signed_in" | "other"
    error: str = ""


def probe_source(source: Source) -> Probe:
    """Read the profile's cookies and report whether they contain a YouTube sign-in."""
    log = _Collector()
    opts = {
        "quiet": True,
        "logger": log,
        "cookiesfrombrowser": source.cookies if source.profile else (source.browser,),
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            jar = list(ydl.cookiejar)
    except Exception as exc:
        message = clean_error(exc)
        return Probe(source, False, False, 0, cookie_error_kind(message) or "other", message)
    google = [c for c in jar if c.domain.endswith(("youtube.com", "google.com"))]
    if any(c.name in SIGNED_IN_COOKIES for c in google):
        return Probe(source, True, True, len(google))
    encrypted = "decrypt" in " ".join(log.messages).lower()
    return Probe(source, True, False, len(google), "encrypted" if encrypted else "not_signed_in")


@dataclass
class Resolution:
    usable: list[Source] = field(default_factory=list)  # signed-in profiles, in order
    kind: str = "ok"  # "ok" | "none_installed" | "locked" | "encrypted" | "not_signed_in" | "other"
    probes: list[Probe] = field(default_factory=list)
    error: str = ""


def resolve_sources(browser: str = "", profile: str = "") -> Resolution:
    """Which profiles can be used right now, or the single most useful reason none can."""
    candidates = candidate_sources(browser, profile)
    if not candidates:
        return Resolution(kind="none_installed")
    probes = [probe_source(source) for source in candidates]
    usable = [p.source for p in probes if p.signed_in]
    if usable:
        return Resolution(usable, "ok", probes)
    kinds = {p.kind for p in probes}
    for kind in ("locked", "encrypted", "not_signed_in"):
        if kind in kinds:
            return Resolution([], kind, probes)
    return Resolution([], "other", probes, next((p.error for p in probes if p.error), ""))


# ── Using a profile ─────────────────────────────────────────────────────────
def fetch_with_sources(url: str, sources: list[Source]):
    """Fetch `url` with each profile's cookies until YouTube accepts one.

    Returns (source, (is_playlist, info), "") or (None, None, last_error_text).
    """
    last_error = ""
    for source in sources:
        try:
            return source, fetch_video_info(url, cookies=source.cookies), ""
        except Exception as exc:
            last_error = clean_error(exc)
    return None, None, last_error
