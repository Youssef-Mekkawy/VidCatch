"""Operating-system helpers: connectivity, FFmpeg, JavaScript runtime, opening folders."""

from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import sys
from pathlib import Path

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def get_platform() -> str:
    """'windows', 'mac' or 'linux'."""
    system = platform.system().lower()
    if system == "windows":
        return "windows"
    if system == "darwin":
        return "mac"
    return "linux"


def check_internet(timeout: float = 4.0) -> bool:
    """Quick connectivity check against public DNS servers."""
    for host in ("1.1.1.1", "8.8.8.8"):
        try:
            with socket.create_connection((host, 53), timeout=timeout):
                return True
        except OSError:
            continue
    return False


def check_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def detect_js_runtimes() -> list[str]:
    """Installed JavaScript runtimes yt-dlp can use (deno, node, bun).

    yt-dlp needs one to solve YouTube's player challenges. Only Deno is enabled by
    default, so downloader.base_options passes whatever is found here explicitly.
    """
    return [name for name in ("deno", "node", "bun") if shutil.which(name)]


def browser_profile_dirs() -> dict[str, Path]:
    """Where each browser keeps its profile on this OS (only used to see if it is installed)."""
    home = Path.home()
    plat = get_platform()
    if plat == "windows":
        roaming = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        return {
            "firefox": roaming / "Mozilla" / "Firefox" / "Profiles",
            "edge": local / "Microsoft" / "Edge" / "User Data",
            "chrome": local / "Google" / "Chrome" / "User Data",
            "brave": local / "BraveSoftware" / "Brave-Browser" / "User Data",
            "vivaldi": local / "Vivaldi" / "User Data",
            "opera": roaming / "Opera Software" / "Opera Stable",
            "chromium": local / "Chromium" / "User Data",
        }
    if plat == "mac":
        support = home / "Library" / "Application Support"
        return {
            "firefox": support / "Firefox" / "Profiles",
            "safari": home / "Library" / "Cookies" / "Cookies.binarycookies",
            "edge": support / "Microsoft Edge",
            "chrome": support / "Google" / "Chrome",
            "brave": support / "BraveSoftware" / "Brave-Browser",
            "vivaldi": support / "Vivaldi",
            "opera": support / "com.operasoftware.Opera",
            "chromium": support / "Chromium",
        }
    config = home / ".config"
    return {
        "firefox": home / ".mozilla" / "firefox",
        "edge": config / "microsoft-edge",
        "chrome": config / "google-chrome",
        "brave": config / "BraveSoftware" / "Brave-Browser",
        "vivaldi": config / "vivaldi",
        "opera": config / "opera",
        "chromium": config / "chromium",
    }


def detect_browsers() -> list[str]:
    """Installed browsers whose cookies yt-dlp can read, best choice first.

    Firefox is listed first because it is the one whose cookie database is not locked while the
    browser is open and is not encrypted with a per-app key (Chrome and Edge often fail on Windows).
    """
    found = {name for name, path in browser_profile_dirs().items() if path.exists()}
    order = ("firefox", "edge", "chrome", "brave", "vivaldi", "opera", "chromium", "safari")
    return [name for name in order if name in found]


def install_ffmpeg() -> tuple[bool, str]:
    """Try to install FFmpeg with the platform package manager.

    Returns (success, method) where method is 'winget', 'brew', 'apt' or 'none'
    when no package manager was found.
    """
    plat = get_platform()
    if plat == "windows":
        command, method = ["winget", "install", "--id", "Gyan.FFmpeg", "-e", "--silent"], "winget"
    elif plat == "mac":
        command, method = ["brew", "install", "ffmpeg"], "brew"
    else:
        # -n: never prompt for a password (there is no terminal to type it in).
        command, method = ["sudo", "-n", "apt-get", "install", "-y", "ffmpeg"], "apt"

    if shutil.which(command[0]) is None:
        return False, "none"
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=900,
            creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return False, method
    return result.returncode == 0, method


def open_folder(path: str | os.PathLike) -> None:
    """Open a folder in the system file manager."""
    folder = Path(path)
    folder.mkdir(parents=True, exist_ok=True)
    if sys.platform.startswith("win"):
        os.startfile(folder)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(folder)])
    else:
        subprocess.Popen(["xdg-open", str(folder)])


def reveal_file(path: str | os.PathLike) -> None:
    """Show a file in the file manager (selected where the platform supports it)."""
    file = Path(path)
    if sys.platform.startswith("win"):
        subprocess.Popen(["explorer", "/select,", str(file)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(file)])
    else:
        subprocess.Popen(["xdg-open", str(file.parent)])
