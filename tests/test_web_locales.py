"""The web UI texts: both languages must have the same keys, and every key the JS uses must exist."""
import json
import re
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "ytdownloader" / "web"


def load(lang):
    return json.loads((WEB / "locales" / f"{lang}.json").read_text(encoding="utf-8"))


def test_languages_have_same_keys():
    en, ar = load("en"), load("ar")
    assert set(en) == set(ar)
    assert all(v.strip() for v in en.values()) and all(v.strip() for v in ar.values())


def test_placeholders_match_between_languages():
    en, ar = load("en"), load("ar")
    find = lambda text: sorted(re.findall(r"\{(\w+)\}", text))  # noqa: E731
    assert {k for k in en if find(en[k]) != find(ar[k])} == set()


def test_every_key_used_by_javascript_exists():
    en = load("en")
    used = set()
    for path in (WEB / "js").rglob("*.js"):
        used |= set(re.findall(r'\bt\(\s*"([a-z0-9_]+)"', path.read_text(encoding="utf-8")))
    # keys chosen at run time (see components/notice.js, signin test results, ffmpeg banner)
    used |= {"reason_bot", "reason_age", "reason_private", "reason_members", "reason_login",
             "test_ok", "test_locked", "test_encrypted", "test_not_signed_in", "test_other",
             "ffmpeg_install_win", "ffmpeg_install_mac", "ffmpeg_install_linux"}
    assert sorted(k for k in used if k not in en) == []


def test_pages_use_no_inline_scripts_or_styles():
    """The CSP forbids them, so they would silently not work."""
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert "<script>" not in html and " style=" not in html and "onclick=" not in html
    for path in (WEB / "js").rglob("*.js"):
        text = path.read_text(encoding="utf-8")
        assert "innerHTML" not in text, path
        assert 'style: "' not in text, path


def test_no_unused_texts():
    """Texts nobody uses rot and get translated for nothing."""
    used = set()
    for path in (WEB / "js").rglob("*.js"):
        text = path.read_text(encoding="utf-8")
        used |= set(re.findall(r'\bt\(\s*"([a-z0-9_]+)"', text))
        used |= set(re.findall(r'"((?:reason|test|ffmpeg_install)_[a-z_]+)"', text))
    unused = [k for k in load("en") if k not in used and not k.startswith("err_E")]
    assert unused == []
