"""Check PyPI for a newer yt-dlp and update it. YouTube changes often, so an old yt-dlp is the
most common reason downloads suddenly break."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.request

from .system import _NO_WINDOW

PYPI_URL = "https://pypi.org/pypi/yt-dlp/json"
REQUIREMENT = "yt-dlp[default]"


def installed_version() -> str:
    try:
        from yt_dlp.version import __version__

        return __version__
    except Exception:
        return ""


def parse_version(text: str) -> tuple[int, ...]:
    """'2026.10.26' -> (2026, 10, 26). Non-numeric suffixes such as '.post1' are ignored."""
    return tuple(int(part) for part in re.findall(r"\d+", text.split("+")[0].split("-")[0]))[:3]


def is_newer(latest: str, current: str) -> bool:
    if not latest or not current:
        return False
    try:
        return parse_version(latest) > parse_version(current)
    except ValueError:
        return False


def fetch_latest_version(timeout: float = 6.0) -> str | None:
    """Latest yt-dlp release on PyPI, or None when offline or PyPI is unreachable."""
    try:
        request = urllib.request.Request(PYPI_URL, headers={"User-Agent": "vidcatch"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return str(json.load(response)["info"]["version"])
    except Exception:
        return None


def can_self_update() -> bool:
    """pip can only update a source install, not a frozen (PyInstaller) build."""
    return not getattr(sys, "frozen", False)


def update_ytdlp(timeout: float = 300.0) -> tuple[bool, str]:
    """Run pip to upgrade yt-dlp. Returns (success, last lines of pip's output)."""
    command = [sys.executable, "-m", "pip", "install", "--upgrade", REQUIREMENT]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, creationflags=_NO_WINDOW
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    output = (result.stderr.strip() or result.stdout.strip()).splitlines()[-4:]
    return result.returncode == 0, "\n".join(output)
