# PyInstaller recipe for VidCatch. Build with:
#   pyinstaller packaging/vidcatch.spec --noconfirm --distpath output/dist --workpath output/build
# Result: output/dist/VidCatch/ (a folder with VidCatch.exe on Windows). A folder starts faster and triggers
# fewer antivirus warnings than a single self-extracting file.
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).parent
datas = [(str(ROOT / "vidcatch" / "web"), "vidcatch/web")]
binaries = []
hiddenimports = collect_submodules("uvicorn") + collect_submodules("vidcatch")

# yt-dlp loads its site extractors by name, and ships helper packages next to it.
# curl_cffi (browser impersonation) is optional and not needed for YouTube; it is skipped when VC_NO_CURL=1.
packages = ["yt_dlp", "yt_dlp_ejs", "certifi"]
if not os.environ.get("VC_NO_CURL"):
    packages.append("curl_cffi")
for package in packages:
    try:
        d, b, h = collect_all(package)
    except Exception:
        continue
    datas += d
    binaries += b
    hiddenimports += h

icon = ROOT / "packaging" / "vidcatch.ico"

a = Analysis(
    [str(ROOT / "packaging" / "vidcatch_entry.py")],
    pathex=[str(ROOT)],
    datas=datas,
    binaries=binaries,
    hiddenimports=hiddenimports,
    excludes=["customtkinter", "tkinter", "PIL", "pytest"] + (["curl_cffi"] if os.environ.get("VC_NO_CURL") else []),  # the old desktop window is not packaged
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VidCatch",
    console=bool(os.environ.get("VC_CONSOLE")),  # normally no console window; errors go to server.log in the data folder
    icon=str(icon) if icon.exists() and sys.platform.startswith("win") else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="VidCatch")
