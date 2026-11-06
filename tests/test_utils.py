import pytest

from ytdownloader.utils import (
    format_count,
    format_duration,
    format_eta,
    format_filesize,
    format_speed,
    output_filename,
    parse_height,
    sanitize_filename,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, ""), (0, ""), (512, "512 B/s"), (2048, "2.0 KB/s"), (5 * 1024**2, "5.0 MB/s")],
)
def test_format_speed(value, expected):
    assert format_speed(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, ""), (0, ""), (59, "0:59"), (65, "1:05"), (3725, "1:02:05")],
)
def test_format_duration(value, expected):
    assert format_duration(value) == expected


def test_format_eta():
    assert format_eta(None) == ""
    assert format_eta(-1) == ""
    assert format_eta(0) == "0:01"  # never shows 0:00 while still downloading
    assert format_eta(125) == "2:05"


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, ""), (0, ""), (2048, "2.0 KB"), (6_400_000, "6.1 MB"), (3 * 1024**3, "3.00 GB")],
)
def test_format_filesize(value, expected):
    assert format_filesize(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ""),
        (0, ""),
        (950, "950"),
        (12_300, "12.3K"),
        (1_000_000, "1M"),
        (1_234_567, "1.2M"),
        (999_999, "1M"),
        (4_500_000_000, "4.5B"),
    ],
)
def test_format_count(value, expected):
    assert format_count(value) == expected


class TestSanitizeFilename:
    def test_replaces_invalid_characters(self):
        assert sanitize_filename('a/b\\c:d*e?f"g<h>i|j') == "a_b_c_d_e_f_g_h_i_j"

    def test_keeps_unicode(self):
        assert sanitize_filename("سبب وقوع الزلزال #ترند") == "سبب وقوع الزلزال #ترند"

    def test_collapses_repeats_and_trims(self):
        assert sanitize_filename("  ..a   b__c..  ") == "a b_c"

    def test_empty_falls_back(self):
        assert sanitize_filename("") == "video"
        assert sanitize_filename("???") == "video"

    def test_reserved_windows_names(self):
        assert sanitize_filename("CON") == "_CON"
        assert sanitize_filename("lpt1.txt") == "_lpt1.txt"

    def test_length_is_limited(self):
        assert len(sanitize_filename("x" * 500)) <= 150

    def test_control_characters_removed(self):
        assert "\n" not in sanitize_filename("line1\nline2")


class TestOutputFilename:
    def test_video_with_quality(self):
        assert output_filename("My video", "mp4", "720p") == "My video_720p.mp4"

    def test_best_quality_has_no_suffix(self):
        assert output_filename("My video", "mp4", "best") == "My video.mp4"
        assert output_filename("My video", "mp4", None) == "My video.mp4"

    def test_audio_ignores_quality(self):
        assert output_filename("Song", "mp3", "720p") == "Song.mp3"

    def test_no_double_underscore_when_title_ends_with_symbol(self):
        assert output_filename("What?", "mp4", "720p") == "What_720p.mp4"


@pytest.mark.parametrize(
    ("label", "expected"),
    [("1080p", 1080), ("360p", 360), (" 720p ", 720), ("best", None), ("", None), (None, None)],
)
def test_parse_height(label, expected):
    assert parse_height(label) == expected
