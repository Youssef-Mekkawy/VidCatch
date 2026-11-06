from pathlib import Path

from vidcatch.errors import ALL_CODES

ROOT = Path(__file__).resolve().parent.parent


def test_every_error_code_is_documented():
    doc = (ROOT / "docs" / "ERRORS.md").read_text(encoding="utf-8")
    assert [c for c in ALL_CODES if f"`{c}`" not in doc] == []
