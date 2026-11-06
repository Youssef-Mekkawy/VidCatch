import re
import string
from pathlib import Path

from vidcatch.i18n import TRANSLATIONS, is_rtl

PACKAGE = Path(__file__).resolve().parent.parent / "vidcatch"
KEY_PATTERN = re.compile(r"""\bt\[\s*["']([a-z0-9_]+)["']\s*\]""")


def used_keys() -> set[str]:
    keys: set[str] = set()
    for path in PACKAGE.rglob("*.py"):
        if path.name == "i18n.py":
            continue
        keys.update(KEY_PATTERN.findall(path.read_text(encoding="utf-8")))
    return keys


def placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def test_languages_have_identical_keys():
    assert set(TRANSLATIONS["en"]) == set(TRANSLATIONS["ar"])


def test_placeholders_match_between_languages():
    for key, english in TRANSLATIONS["en"].items():
        assert placeholders(english) == placeholders(TRANSLATIONS["ar"][key]), key


def test_no_empty_strings():
    for lang, table in TRANSLATIONS.items():
        for key, value in table.items():
            assert value.strip(), f"{lang}.{key} is empty"


def test_every_key_used_in_code_exists():
    missing = used_keys() - set(TRANSLATIONS["en"])
    assert not missing, f"keys used in code but not translated: {sorted(missing)}"


def test_no_unused_keys():
    unused = set(TRANSLATIONS["en"]) - used_keys()
    assert not unused, f"translated but never used: {sorted(unused)}"


def test_rtl_languages():
    assert is_rtl("ar")
    assert not is_rtl("en")
