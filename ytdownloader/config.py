"""Settings and download history, stored as JSON in a per-user data folder."""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path

APP_DIR_NAME = "YouTubeDownloader"
HISTORY_LIMIT = 200
SUPPORTED_LANGUAGES = ("en", "ar")
SUPPORTED_FORMATS = ("mp4", "mp3")
QUALITY_CHOICES = ("best", "1080p", "720p", "480p", "360p")

DEFAULT_CONFIG: dict = {
    "language": "en",
    "download_dir": str(Path.home() / "Downloads" / "YouTube-Downloader"),
    "default_format": "mp4",
    "default_quality": "best",
    "auto_download": False,
    "open_folder": False,
    "ask_overwrite": True,
    "clear_url": False,
    "cookies_browser": "",  # "" = automatic
    "cookies_profile": "",  # browser profile folder name, "" = the browser's main profile
    "signin_consent": "ask",  # "ask" (not decided yet) | "allow" | "never"
    "check_updates": True,
    "queue_auto_resume": False,  # continue unfinished queue items when the app starts
}

# Browsers yt-dlp can read cookies from ('' = automatic).
COOKIE_BROWSERS = ("", "chrome", "edge", "firefox", "brave", "chromium", "opera", "vivaldi", "safari")

_history_lock = threading.Lock()


# ── Locations ───────────────────────────────────────────────────────────────
def data_dir() -> Path:
    """Per-user folder for settings, history and temp files (created on demand).

    Override with the YTDL_DATA_DIR environment variable (used by the tests).
    """
    override = os.environ.get("YTDL_DATA_DIR")
    if override:
        path = Path(override)
    elif sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        path = Path(base) / APP_DIR_NAME
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        path = Path(base) / "youtube-downloader"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return data_dir() / "config.json"


def history_path() -> Path:
    return data_dir() / "history.json"


def temp_dir() -> Path:
    path = data_dir() / "temp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def import_legacy_files(*folders: Path) -> list[str]:
    """Copy config.json / history.json from an old project folder into the data folder.

    Earlier versions stored these next to the script. Files are only copied when the
    new location has no file of that name yet, so this is safe to call on every start.
    Returns the names that were imported.
    """
    imported = []
    for name in ("config.json", "history.json"):
        target = data_dir() / name
        if target.exists():
            continue
        for folder in folders:
            source = Path(folder) / name
            if source.is_file():
                try:
                    shutil.copy2(source, target)
                    imported.append(name)
                except OSError:
                    pass
                break
    return imported


# ── JSON helpers ────────────────────────────────────────────────────────────
def _read_json(path: Path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _write_json(path: Path, data) -> None:
    """Write atomically so a crash never leaves a half-written file."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


# ── Settings ────────────────────────────────────────────────────────────────
def sanitize_config(stored: object) -> dict:
    """Merge stored values over the defaults, replacing missing or invalid ones with defaults."""
    cfg = dict(DEFAULT_CONFIG)
    if isinstance(stored, dict):
        for key, default in DEFAULT_CONFIG.items():
            value = stored.get(key)
            if isinstance(value, type(default)):
                cfg[key] = value
    if cfg["language"] not in SUPPORTED_LANGUAGES:
        cfg["language"] = DEFAULT_CONFIG["language"]
    if cfg["default_format"] not in SUPPORTED_FORMATS:
        cfg["default_format"] = DEFAULT_CONFIG["default_format"]
    if cfg["cookies_browser"] not in COOKIE_BROWSERS:
        cfg["cookies_browser"] = ""
        cfg["cookies_profile"] = ""
    if cfg["signin_consent"] not in ("ask", "allow", "never"):
        cfg["signin_consent"] = "ask"
    cfg["default_quality"] = str(cfg["default_quality"]).lower()
    if cfg["default_quality"] not in QUALITY_CHOICES:
        cfg["default_quality"] = DEFAULT_CONFIG["default_quality"]
    return cfg


def load_config() -> dict:
    """Load settings, falling back to defaults for missing or invalid values."""
    return sanitize_config(_read_json(config_path(), {}))


def save_config(cfg: dict) -> None:
    _write_json(config_path(), {k: cfg.get(k, v) for k, v in DEFAULT_CONFIG.items()})


_config_lock = threading.Lock()


def validate_settings_patch(patch: object) -> dict:
    """Check a partial settings update from the web UI. Raises ValueError with a readable message
    (unlike load_config, which silently repairs, a user action should be told what is wrong)."""
    if not isinstance(patch, dict) or not patch:
        raise ValueError("Send an object with at least one setting.")
    clean: dict = {}
    for key, value in patch.items():
        if key not in DEFAULT_CONFIG:
            raise ValueError(f"Unknown setting: {key}")
        if not isinstance(value, type(DEFAULT_CONFIG[key])):
            raise ValueError(f"Wrong type for {key}.")
        clean[key] = value
    choices = {
        "language": SUPPORTED_LANGUAGES,
        "default_format": SUPPORTED_FORMATS,
        "default_quality": QUALITY_CHOICES,
        "cookies_browser": COOKIE_BROWSERS,
        "signin_consent": ("ask", "allow", "never"),
    }
    for key, allowed in choices.items():
        if key in clean and clean[key] not in allowed:
            raise ValueError(f"Invalid value for {key}.")
    if "download_dir" in clean and not clean["download_dir"].strip():
        raise ValueError("The download folder cannot be empty.")
    if "cookies_profile" in clean and (
        len(clean["cookies_profile"]) > 100 or any(c in clean["cookies_profile"] for c in "/\\")
    ):
        raise ValueError("Invalid browser profile name.")
    return clean


def update_config(patch: dict) -> dict:
    """Validate and apply a partial update, save it, and return the full new settings."""
    clean = validate_settings_patch(patch)
    with _config_lock:
        cfg = load_config()
        cfg.update(clean)
        if "cookies_browser" in clean and "cookies_profile" not in clean:
            cfg["cookies_profile"] = ""  # a profile belongs to one browser
        save_config(cfg)
        return cfg


# ── History ─────────────────────────────────────────────────────────────────
def load_history() -> list[dict]:
    data = _read_json(history_path(), [])
    return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []


def save_history(entries: list[dict]) -> None:
    _write_json(history_path(), entries[-HISTORY_LIMIT:])


def add_history_entry(entry: dict) -> None:
    with _history_lock:
        entries = load_history()
        entries.append(entry)
        save_history(entries)


def remove_history_entry(entry_id: str) -> None:
    with _history_lock:
        save_history([e for e in load_history() if e.get("id") != entry_id])


def clear_history() -> None:
    with _history_lock:
        save_history([])


def make_history_entry(info: dict, url: str, fmt: str, quality: str | None, filepath: str) -> dict:
    """Build a history record for a finished download."""
    try:
        size = os.path.getsize(filepath)
    except OSError:
        size = 0
    return {
        "id": str(uuid.uuid4()),
        "title": info.get("title") or Path(filepath).stem,
        "channel": info.get("uploader") or info.get("channel") or "",
        "url": url,
        "format": fmt,
        "quality": (quality or "best") if fmt == "mp4" else "audio",
        "filesize": size,
        "filename": Path(filepath).name,
        "filepath": filepath,
        "thumbnail": info.get("thumbnail") or "",
        "date": time.strftime("%Y-%m-%d  %H:%M"),
    }
