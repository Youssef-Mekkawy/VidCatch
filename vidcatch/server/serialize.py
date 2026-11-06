"""Turn yt-dlp metadata into the small, stable JSON the web UI consumes."""

from __future__ import annotations

from ..downloader import entry_thumbnail, entry_url, extract_formats, pick_quality, subtitle_tracks
from ..utils import format_count, format_duration, format_filesize


def video_payload(info: dict, config: dict) -> dict:
    formats = extract_formats(info)
    return {
        "id": info.get("id") or "",
        "title": info.get("title") or "",
        "channel": info.get("uploader") or info.get("channel") or "",
        "duration": info.get("duration") or 0,
        "duration_text": format_duration(info.get("duration")),
        "views": info.get("view_count") or 0,
        "views_text": format_count(info.get("view_count")),
        "thumbnail": info.get("thumbnail") or "",
        # Subtitle languages that exist: {"manual": [...], "auto": [original language]}
        "subtitles": subtitle_tracks(info),
        # Qualities this video really offers (e.g. ["1080p", "720p"]) and the one to preselect.
        "qualities": formats.labels,
        "quality": pick_quality(formats.heights, config["default_quality"]),
        # Rough sizes for the "about 58 MB" hint; keys are quality labels, "audio" is MP3 audio.
        "sizes": {f"{h}p": s for h, s in formats.video_sizes.items() if s},
        "size_texts": {
            **{f"{h}p": format_filesize(s) for h, s in formats.video_sizes.items() if s},
            **({"audio": format_filesize(formats.audio_size)} if formats.audio_size else {}),
        },
    }


def playlist_payload(info: dict) -> dict:
    entries = []
    for entry in (e for e in (info.get("entries") or []) if e):
        url = entry_url(entry)
        if not url:
            continue
        entries.append(
            {
                "title": entry.get("title") or "",
                "url": url,
                "thumbnail": entry_thumbnail(entry),
                "duration_text": format_duration(entry.get("duration")),
            }
        )
    return {
        "title": info.get("title") or "Playlist",
        "channel": info.get("uploader") or info.get("channel") or "",
        "count": len(entries),
        "entries": entries,
    }
