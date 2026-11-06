"""Start the packaged app, ask it for its status, stop it. Usage: python smoke_test.py <path to VidCatch executable>"""

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path


def main(exe: str) -> int:
    data = Path(tempfile.mkdtemp(prefix="vidcatch-smoke-"))
    env = {**os.environ, "YTDL_DATA_DIR": str(data)}  # keep SYSTEMROOT etc.: Windows networking needs them
    out = data / "output.txt"
    with open(out, "w") as sink:
        proc = subprocess.Popen([exe, "--no-browser", "--port", "8799"], env=env, stdout=sink, stderr=subprocess.STDOUT)
    try:
        info = data / "server.json"
        for _ in range(120):
            if info.exists():
                break
            if proc.poll() is not None:
                break
            time.sleep(0.5)
        else:
            print("The app did not start (no server.json).")
            return 1
        if not info.exists():
            print(f"The app stopped at start-up (exit code {proc.returncode}). Its output:")
            print(out.read_text(errors="replace")[-3000:] or "(nothing; it is probably a windowed build)")
            return 1
        time.sleep(1.5)
        token = json.loads(info.read_text())["token"]
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
        opener.open(f"http://127.0.0.1:8799/?t={token}", timeout=10).read()
        status = json.load(opener.open("http://127.0.0.1:8799/api/status", timeout=10))
        assert status["app"] == "vidcatch" and status["frozen"] is True, status
        page = opener.open("http://127.0.0.1:8799/", timeout=10).read().decode()
        assert "VidCatch" in page
        print("OK:", status["version"], "yt-dlp", status["ytdlp_version"])
        return 0
    finally:
        proc.terminate()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
