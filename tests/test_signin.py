import json
import sqlite3
import sys
import time

import pytest

from vidcatch import signin, system
from vidcatch.signin import Probe, Source


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setattr(system, "get_platform", lambda: "linux")
    monkeypatch.setattr(system.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def make_firefox_profile(home, name, cookie_names=()):
    folder = home / ".mozilla" / "firefox" / name
    folder.mkdir(parents=True)
    db = sqlite3.connect(folder / "cookies.sqlite")
    db.execute(
        "CREATE TABLE moz_cookies (id INTEGER PRIMARY KEY, originAttributes TEXT NOT NULL "
        "DEFAULT '', name TEXT, value TEXT, host TEXT, path TEXT, expiry INTEGER, "
        "lastAccessed INTEGER, creationTime INTEGER, isSecure INTEGER, isHttpOnly INTEGER, "
        "inBrowserElement INTEGER DEFAULT 0, sameSite INTEGER DEFAULT 0, "
        "rawSameSite INTEGER DEFAULT 0, schemeMap INTEGER DEFAULT 0)"
    )
    for cookie in cookie_names:
        db.execute(
            "INSERT INTO moz_cookies (name, value, host, path, expiry, lastAccessed, "
            "creationTime, isSecure, isHttpOnly) VALUES (?, 'v', '.youtube.com', '/', ?, 1, 1, 1, 1)",
            (cookie, int(time.time()) + 9999),
        )
    db.commit()
    db.close()


def make_chrome_profiles(home, profiles):
    root = home / ".config" / "google-chrome"
    info = {}
    for folder, (name, email) in profiles.items():
        (root / folder / "Network").mkdir(parents=True)
        (root / folder / "Network" / "Cookies").write_bytes(b"")
        info[folder] = {"name": name, "user_name": email}
    (root / "Local State").write_text(json.dumps({"profile": {"info_cache": info}}))


def test_firefox_profiles_default_release_first(fake_home):
    make_firefox_profile(fake_home, "zz99.work")
    make_firefox_profile(fake_home, "ab12cd.default-release")
    assert [s.profile for s in signin.list_profiles("firefox")] == [
        "ab12cd.default-release",
        "zz99.work",
    ]
    assert [s.label for s in signin.list_profiles("firefox")] == ["default-release", "work"]


def test_chrome_profiles_use_names_and_emails(fake_home):
    make_chrome_profiles(
        fake_home, {"Profile 2": ("Spare", "spare@gmail.com"), "Default": ("Main", "")}
    )
    sources = signin.list_profiles("chrome")
    assert [s.profile for s in sources] == ["Default", "Profile 2"]
    assert [s.label for s in sources] == ["Main", "Spare (spare@gmail.com)"]
    assert sources[1].title == "Chrome - Spare (spare@gmail.com)"


def test_not_installed_browser_has_no_profiles(fake_home):
    assert signin.list_profiles("firefox") == []


def test_candidate_sources_automatic_and_specific(fake_home):
    make_firefox_profile(fake_home, "ab12cd.default-release")
    make_chrome_profiles(fake_home, {"Default": ("Main", ""), "Profile 1": ("Other", "")})
    automatic = signin.candidate_sources()
    assert [s.browser for s in automatic] == ["firefox", "chrome", "chrome"]  # firefox first
    assert [s.profile for s in signin.candidate_sources("chrome")] == ["Default"]
    assert [s.profile for s in signin.candidate_sources("chrome", "Profile 1")] == ["Profile 1"]


def test_resolve_prefers_signed_in_profiles_in_order(monkeypatch):
    sources = [Source("firefox", "a"), Source("chrome", "Default"), Source("edge", "Default")]
    monkeypatch.setattr(signin, "candidate_sources", lambda *a: sources)
    results = {
        "firefox": Probe(sources[0], True, False, 1, "not_signed_in"),
        "chrome": Probe(sources[1], True, True, 5),
        "edge": Probe(sources[2], True, True, 5),
    }
    monkeypatch.setattr(signin, "probe_source", lambda s: results[s.browser])
    resolution = signin.resolve_sources()
    assert resolution.kind == "ok"
    assert [s.browser for s in resolution.usable] == ["chrome", "edge"]


@pytest.mark.parametrize(
    ("kinds", "expected"),
    [
        (["not_signed_in", "locked"], "locked"),
        (["not_signed_in", "encrypted"], "encrypted"),
        (["not_signed_in", "not_signed_in"], "not_signed_in"),
    ],
)
def test_resolve_failure_kind_priority(monkeypatch, kinds, expected):
    sources = [Source("firefox", str(i)) for i in range(len(kinds))]
    monkeypatch.setattr(signin, "candidate_sources", lambda *a: sources)
    monkeypatch.setattr(
        signin, "probe_source",
        lambda s: Probe(s, kinds[int(s.profile)] == "not_signed_in", False, 0, kinds[int(s.profile)]),
    )
    resolution = signin.resolve_sources()
    assert resolution.kind == expected and not resolution.usable


def test_resolve_none_installed(monkeypatch):
    monkeypatch.setattr(signin, "candidate_sources", lambda *a: [])
    assert signin.resolve_sources().kind == "none_installed"


def test_fetch_with_sources_uses_first_accepted(monkeypatch):
    seen = []

    def fake_fetch(url, cookies=None):
        seen.append(cookies)
        if cookies == ("firefox", "a"):
            raise RuntimeError("Sign in to confirm you’re not a bot")
        return False, {"title": "ok"}

    monkeypatch.setattr(signin, "fetch_video_info", fake_fetch)
    sources = [Source("firefox", "a"), Source("edge", "Default"), Source("chrome", "Default")]
    source, result, error = signin.fetch_with_sources("u", sources)
    assert source == sources[1] and result == (False, {"title": "ok"}) and error == ""
    assert seen == [("firefox", "a"), ("edge", "Default")]


def test_fetch_with_sources_all_rejected(monkeypatch):
    def fake_fetch(url, cookies=None):
        raise RuntimeError("Private video. Sign in if you've been granted access")

    monkeypatch.setattr(signin, "fetch_video_info", fake_fetch)
    source, result, error = signin.fetch_with_sources("u", [Source("firefox", "a")])
    assert source is None and result is None and "Private video" in error


@pytest.mark.skipif(sys.platform != "linux", reason="yt-dlp resolves browser paths per OS")
def test_probe_real_firefox_cookie_database(fake_home):
    make_firefox_profile(fake_home, "ab12cd.default-release", ["LOGIN_INFO", "VISITOR_INFO1_LIVE"])
    make_firefox_profile(fake_home, "zz99.work", ["PREF"])
    signed_in = signin.probe_source(signin.list_profiles("firefox")[0])
    assert signed_in.readable and signed_in.signed_in and signed_in.cookies == 2
    anonymous = signin.probe_source(signin.list_profiles("firefox")[1])
    assert anonymous.readable and not anonymous.signed_in and anonymous.kind == "not_signed_in"
    resolution = signin.resolve_sources("firefox")
    assert resolution.kind == "ok" and resolution.usable[0].profile == "ab12cd.default-release"
