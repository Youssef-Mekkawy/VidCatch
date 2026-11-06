import json
import subprocess

from vidcatch import updates


def test_parse_version():
    assert updates.parse_version("2026.10.26") == (2026, 10, 26)
    assert updates.parse_version("2026.10.26.post1") == (2026, 10, 26)
    assert updates.parse_version("2026.10.26.dev0+abc") == (2026, 10, 26)


def test_is_newer():
    assert updates.is_newer("2026.11.01", "2026.10.26")
    assert not updates.is_newer("2026.10.26", "2026.10.26")
    assert not updates.is_newer("2026.10.01", "2026.10.26")
    assert not updates.is_newer("", "2026.10.26")
    assert not updates.is_newer("2026.11.01", "")


def test_fetch_latest_version(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, *_args):
            return json.dumps({"info": {"version": "2026.12.01"}}).encode()

    monkeypatch.setattr(updates.urllib.request, "urlopen", lambda *a, **k: FakeResponse())
    assert updates.fetch_latest_version() == "2026.12.01"


def test_fetch_latest_version_offline_returns_none(monkeypatch):
    def boom(*_a, **_k):
        raise OSError("offline")

    monkeypatch.setattr(updates.urllib.request, "urlopen", boom)
    assert updates.fetch_latest_version() is None


def test_update_ytdlp_reports_success_and_failure(monkeypatch):
    def fake_run(code, out="", err=""):
        return lambda *a, **k: subprocess.CompletedProcess(a, code, out, err)

    monkeypatch.setattr(updates.subprocess, "run", fake_run(0, "Successfully installed yt-dlp"))
    assert updates.update_ytdlp() == (True, "Successfully installed yt-dlp")

    monkeypatch.setattr(updates.subprocess, "run", fake_run(1, "", "ERROR: no network"))
    ok, message = updates.update_ytdlp()
    assert not ok and "no network" in message


def test_can_self_update_false_when_frozen(monkeypatch):
    monkeypatch.setattr(updates.sys, "frozen", True, raising=False)
    assert not updates.can_self_update()
