import pytest


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Every test gets its own settings/history folder, never the real one."""
    monkeypatch.setenv("YTDL_DATA_DIR", str(tmp_path / "data"))
    return tmp_path / "data"
