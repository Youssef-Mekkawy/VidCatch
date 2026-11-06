import json
from pathlib import Path

import pytest

from ytdownloader.errors import ALL_CODES, classify

LOCALES = Path(__file__).resolve().parent.parent / "ytdownloader" / "web" / "locales"


@pytest.mark.parametrize(
    ("message", "code"),
    [
        ("Video unavailable. This video has been removed by the uploader", "E120"),
        ("Sign in to confirm your age", "E111"),
        ("Sign in to confirm you're not a bot", "E110"),
        ("Private video. Sign in if you've been granted access", "E112"),
        ("Join this channel to get access to members-only content", "E113"),
        ("The uploader has not made this video available in your country", "E121"),
        ("[Errno 28] No space left on device", "E140"),
        ("[Errno 13] Permission denied: 'C:\\\\x'", "E141"),
        ("OSError: [Errno 36] File name too long", "E142"),
        ("HTTP Error 403: Forbidden", "E150"),
        ("HTTP Error 429: Too Many Requests", "E151"),
        ("HTTP Error 503: Service Unavailable", "E152"),
        ("Unsupported URL: https://example.com", "E160"),
        ("Requested format is not available", "E170"),
        ("ffmpeg not found. Please install", "E130"),
        ("<urlopen error [Errno -3] Temporary failure in name resolution>", "E100"),
        ("The read operation timed out", "E101"),
        ("Unable to extract uploader id", "E180"),
        ("something nobody has seen", "E900"),
        ("", "E900"),
    ],
)
def test_classify(message, code):
    assert classify(message) == code


def test_every_code_has_texts_in_both_languages():
    for lang in ("en", "ar"):
        texts = json.loads((LOCALES / f"{lang}.json").read_text(encoding="utf-8"))
        missing = [
            f"err_{code}_{part}" for code in ALL_CODES for part in ("title", "text")
            if f"err_{code}_{part}" not in texts
        ]
        assert missing == [], lang


def test_private_playlist_counts_as_needing_sign_in():
    from ytdownloader.downloader import login_reason, needs_login

    message = "[youtube:tab] LL: The playlist does not exist."
    assert needs_login(message) and login_reason(message) == "private"
    assert classify(message) == "E112"
