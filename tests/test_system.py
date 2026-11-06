from ytdownloader import system


def test_detect_browsers_orders_firefox_first(tmp_path, monkeypatch):
    monkeypatch.setattr(system, "get_platform", lambda: "linux")
    monkeypatch.setattr(system.Path, "home", staticmethod(lambda: tmp_path))
    for folder in (".config/google-chrome", ".mozilla/firefox", ".config/microsoft-edge"):
        (tmp_path / folder).mkdir(parents=True)
    assert system.detect_browsers() == ["firefox", "edge", "chrome"]


def test_detect_browsers_none_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(system, "get_platform", lambda: "linux")
    monkeypatch.setattr(system.Path, "home", staticmethod(lambda: tmp_path))
    assert system.detect_browsers() == []


def test_detect_browsers_windows_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(system, "get_platform", lambda: "windows")
    monkeypatch.setattr(system.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    (tmp_path / "Local" / "Google" / "Chrome" / "User Data").mkdir(parents=True)
    (tmp_path / "Roaming" / "Mozilla" / "Firefox" / "Profiles").mkdir(parents=True)
    assert system.detect_browsers() == ["firefox", "chrome"]
