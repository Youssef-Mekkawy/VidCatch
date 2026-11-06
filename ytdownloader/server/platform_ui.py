"""Things that need the operating system's own UI: a folder picker and an app-style browser window.

No tkinter here on purpose: it is missing from many Linux Python installs and is awkward to call
from a server thread, so each OS uses its own built-in tool instead.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from ..system import _NO_WINDOW, get_platform


def pick_folder(initial: str = "") -> str | None:
    """Show the system folder dialog and return the chosen path, or None if cancelled.

    Raises NotImplementedError when this machine has no usable dialog tool (the UI then lets the
    user type the path instead).
    """
    start = initial if initial and Path(initial).expanduser().exists() else str(Path.home())
    plat = get_platform()
    if plat == "windows":
        quoted = start.replace("'", "''")
        script = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
            f"$d.SelectedPath = '{quoted}'; $d.ShowNewFolderButton = $true; "
            "if ($d.ShowDialog() -eq 'OK') { Write-Output $d.SelectedPath }"
        )
        command = ["powershell", "-NoProfile", "-STA", "-Command", script]
    elif plat == "mac":
        command = [
            "osascript", "-e",
            'POSIX path of (choose folder with prompt "Choose a folder")',
        ]
    elif shutil.which("zenity"):
        command = ["zenity", "--file-selection", "--directory", f"--filename={start}/"]
    elif shutil.which("kdialog"):
        command = ["kdialog", "--getexistingdirectory", start]
    else:
        raise NotImplementedError("No folder dialog available.")

    if shutil.which(command[0]) is None:
        raise NotImplementedError("No folder dialog available.")
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=600, creationflags=_NO_WINDOW
        )
    except (OSError, subprocess.SubprocessError):
        return None
    chosen = result.stdout.strip()
    return chosen or None


def find_app_browser() -> str | None:
    """A Chromium-based browser that can open a page as a bare app window (--app=URL)."""
    plat = get_platform()
    candidates: list[str] = []
    if plat == "windows":
        roots = [os.environ.get(k, "") for k in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
        for root in filter(None, roots):
            candidates += [
                str(Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe"),
                str(Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe"),
                str(Path(root) / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe"),
            ]
    elif plat == "mac":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
    else:
        for name in (
            "google-chrome", "google-chrome-stable", "microsoft-edge", "microsoft-edge-stable",
            "brave-browser", "chromium", "chromium-browser",
        ):
            found = shutil.which(name)
            if found:
                candidates.append(found)
    return next((c for c in candidates if Path(c).is_file()), None)
