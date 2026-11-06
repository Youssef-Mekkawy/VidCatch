import json

from vidcatch import config


def test_defaults_when_no_file():
    cfg = config.load_config()
    assert cfg == config.DEFAULT_CONFIG


def test_round_trip():
    cfg = config.load_config()
    cfg.update(language="ar", default_format="mp3", auto_download=True)
    config.save_config(cfg)
    assert config.load_config()["language"] == "ar"
    assert config.load_config()["default_format"] == "mp3"
    assert config.load_config()["auto_download"] is True


def test_invalid_values_fall_back_to_defaults():
    config.config_path().write_text(
        json.dumps(
            {
                "language": "xx",
                "default_format": "avi",
                "default_quality": "9999p",
                "auto_download": "yes",  # wrong type
                "unknown_key": 1,
            }
        ),
        encoding="utf-8",
    )
    cfg = config.load_config()
    assert cfg["language"] == "en"
    assert cfg["default_format"] == "mp4"
    assert cfg["default_quality"] == "best"
    assert cfg["auto_download"] is False
    assert "unknown_key" not in cfg


def test_legacy_capitalised_quality_is_normalised():
    config.config_path().write_text(json.dumps({"default_quality": "Best"}), encoding="utf-8")
    assert config.load_config()["default_quality"] == "best"


def test_corrupt_file_is_ignored():
    config.config_path().write_text("{not json", encoding="utf-8")
    assert config.load_config() == config.DEFAULT_CONFIG


def test_save_ignores_unknown_keys():
    config.save_config({"language": "ar", "junk": 1})
    stored = json.loads(config.config_path().read_text(encoding="utf-8"))
    assert "junk" not in stored
    assert stored["language"] == "ar"


class TestHistory:
    @staticmethod
    def entry(i):
        return {"id": str(i), "title": f"video {i}"}

    def test_empty_by_default(self):
        assert config.load_history() == []

    def test_add_remove_clear(self):
        for i in range(3):
            config.add_history_entry(self.entry(i))
        assert [e["id"] for e in config.load_history()] == ["0", "1", "2"]
        config.remove_history_entry("1")
        assert [e["id"] for e in config.load_history()] == ["0", "2"]
        config.clear_history()
        assert config.load_history() == []

    def test_limit_keeps_newest(self, monkeypatch):
        monkeypatch.setattr(config, "HISTORY_LIMIT", 5)
        for i in range(8):
            config.add_history_entry(self.entry(i))
        assert [e["id"] for e in config.load_history()] == ["3", "4", "5", "6", "7"]

    def test_non_list_file_is_ignored(self):
        config.history_path().write_text('{"a": 1}', encoding="utf-8")
        assert config.load_history() == []

    def test_make_history_entry(self, tmp_path):
        media = tmp_path / "Clip_720p.mp4"
        media.write_bytes(b"x" * 2048)
        entry = config.make_history_entry(
            {"title": "Clip", "uploader": "Me", "thumbnail": "http://t/x.jpg"},
            "http://youtu.be/x", "mp4", "720p", str(media),
        )
        assert entry["title"] == "Clip"
        assert entry["channel"] == "Me"
        assert entry["quality"] == "720p"
        assert entry["filesize"] == 2048
        assert entry["filename"] == "Clip_720p.mp4"
        assert entry["id"]

    def test_make_history_entry_audio_and_missing_file(self, tmp_path):
        entry = config.make_history_entry({}, "u", "mp3", None, str(tmp_path / "gone.mp3"))
        assert entry["quality"] == "audio"
        assert entry["filesize"] == 0
        assert entry["title"] == "gone"


def test_signin_settings_defaults_and_validation():
    cfg = config.load_config()
    assert cfg["signin_consent"] == "ask"
    assert cfg["cookies_browser"] == "" and cfg["cookies_profile"] == ""
    assert "cookies_file" not in cfg  # no cookie files are ever stored

    config.save_config({**cfg, "cookies_browser": "firefox", "cookies_profile": "x.default"})
    loaded = config.load_config()
    assert (loaded["cookies_browser"], loaded["cookies_profile"]) == ("firefox", "x.default")

    config.save_config({**cfg, "cookies_browser": "netscape", "cookies_profile": "x"})
    loaded = config.load_config()
    assert (loaded["cookies_browser"], loaded["cookies_profile"]) == ("", "")

    config.save_config({**cfg, "signin_consent": "maybe"})
    assert config.load_config()["signin_consent"] == "ask"


def test_old_cookies_file_setting_is_dropped():
    config.config_path().write_text(json.dumps({"cookies_file": "D:/cookies.txt"}))
    assert "cookies_file" not in config.load_config()
    config.save_config(config.load_config())
    assert "cookies_file" not in json.loads(config.config_path().read_text())


def test_old_data_folder_is_adopted(tmp_path, monkeypatch):
    from vidcatch import config

    monkeypatch.delenv("YTDL_DATA_DIR", raising=False)
    monkeypatch.setattr(config.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    old = tmp_path / "youtube-downloader"
    old.mkdir()
    (old / "history.json").write_text("[]")

    folder = config.data_dir()
    assert folder == tmp_path / "vidcatch"
    assert (folder / "history.json").exists() and not old.exists()
