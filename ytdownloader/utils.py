"""Pure helpers: formatters and filename handling. No GUI, no network."""

from __future__ import annotations

import re

_INVALID_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
MAX_NAME_LENGTH = 150


def format_speed(bytes_per_sec: float | None) -> str:
    """Human-readable transfer speed, or '' when unknown."""
    if not bytes_per_sec or bytes_per_sec <= 0:
        return ""
    if bytes_per_sec < 1024:
        return f"{bytes_per_sec:.0f} B/s"
    if bytes_per_sec < 1024**2:
        return f"{bytes_per_sec / 1024:.1f} KB/s"
    return f"{bytes_per_sec / 1024**2:.1f} MB/s"


def format_duration(seconds: float | None) -> str:
    """m:ss or h:mm:ss, or '' when unknown."""
    if not seconds:
        return ""
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def format_eta(seconds: float | None) -> str:
    """Remaining time, same format as a duration; '' when unknown."""
    if seconds is None or seconds < 0:
        return ""
    return format_duration(max(int(seconds), 1))


def format_filesize(size: float | None) -> str:
    """KB / MB / GB with one or two decimals, or '' when unknown."""
    if not size or size <= 0:
        return ""
    if size < 1024**2:
        return f"{size / 1024:.1f} KB"
    if size < 1024**3:
        return f"{size / 1024**2:.1f} MB"
    return f"{size / 1024**3:.2f} GB"


def format_count(n: int | None) -> str:
    """Compact number: 950, 12.3K, 4.5M, 1.2B. '' when unknown."""
    if not n or n < 0:
        return ""
    if n < 1000:
        return str(n)
    value = float(n)
    for suffix in ("K", "M", "B"):
        value /= 1000
        if round(value, 1) < 1000 or suffix == "B":
            return f"{value:.1f}".rstrip("0").rstrip(".") + suffix
    return str(n)  # unreachable


def sanitize_filename(name: str) -> str:
    """Make a string safe to use as a file name on Windows, macOS and Linux."""
    name = _INVALID_CHARS.sub("_", name or "")
    name = name.strip(". ")
    name = re.sub(r"_{2,}", "_", name)
    name = re.sub(r" {2,}", " ", name)
    name = name[:MAX_NAME_LENGTH].rstrip(". _")
    if not name:
        return "video"
    if name.split(".")[0].upper() in _RESERVED_NAMES:
        name = f"_{name}"
    return name


def output_filename(title: str, fmt: str, quality: str | None = None) -> str:
    """Final file name for a download, e.g. 'My video_720p.mp4' or 'My song.mp3'."""
    ext = "mp3" if fmt == "mp3" else "mp4"
    suffix = f"_{quality}" if fmt == "mp4" and quality and quality != "best" else ""
    return f"{sanitize_filename(title)}{suffix}.{ext}"


def parse_height(label: str | None) -> int | None:
    """'1080p' -> 1080. Returns None for 'best' or anything unparsable."""
    if not label:
        return None
    match = re.fullmatch(r"\s*(\d{3,4})p\s*", label)
    return int(match.group(1)) if match else None


def find_existing_video(download_dir, title: str, wanted_name: str):
    """The video file already saved for this title, or None.

    Looks in the folder named after the video first (where downloads with subtitles go), then in the
    download folder itself. The file with exactly `wanted_name` wins, otherwise the newest one.
    """
    from pathlib import Path

    stem = sanitize_filename(title)
    base = Path(download_dir)
    for folder in (base / stem, base):
        if not folder.is_dir():
            continue
        exact = folder / wanted_name
        if exact.is_file():
            return exact
        found = [p for p in folder.glob(f"{glob_escape(stem)}*.mp4") if p.is_file()]
        if found:
            return max(found, key=lambda p: p.stat().st_mtime)
    return None


def glob_escape(text: str) -> str:
    """Escape glob wildcards in a file name."""
    return re.sub(r"([*?\[\]])", r"[\1]", text)
