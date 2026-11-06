"""Request bodies. Everything that reaches yt-dlp or the file system is validated here first."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from ..config import COOKIE_BROWSERS

_PROFILE = re.compile(r"[\w .()@+-]{0,100}")
_QUALITY = re.compile(r"best|\d{3,4}p")


def _check_url(value: str) -> str:
    value = value.strip()
    if not re.match(r"https?://\S+$", value, re.IGNORECASE) or len(value) > 2048:
        raise ValueError("Enter a full link that starts with http:// or https://")
    return value


class SourceIn(BaseModel):
    """A browser profile whose cookies may be used for sign-in."""

    browser: str
    profile: str = ""
    label: str = Field(default="", max_length=200)

    @field_validator("browser")
    @classmethod
    def _browser(cls, value: str) -> str:
        if value not in COOKIE_BROWSERS or not value:
            raise ValueError("Unknown browser.")
        return value

    @field_validator("profile")
    @classmethod
    def _profile(cls, value: str) -> str:
        # yt-dlp accepts a path here, so only plain profile folder names are allowed.
        if not _PROFILE.fullmatch(value):
            raise ValueError("Invalid browser profile name.")
        return value


class FetchIn(BaseModel):
    url: str
    sources: list[SourceIn] | None = Field(default=None, max_length=20)

    _url = field_validator("url")(_check_url)


class SigninPrepareIn(BaseModel):
    first_time: bool = False  # the user has just allowed sign-in, so offer the account picker


class DownloadIn(BaseModel):
    url: str
    title: str = Field(default="", max_length=500)
    fmt: Literal["mp4", "mp3"] = "mp4"
    quality: str | None = None
    # Subtitle languages to save next to the video (see GET-less `subtitles` in the fetch result).
    sub_manual: list[str] = Field(default_factory=list, max_length=60)
    sub_auto: list[str] = Field(default_factory=list, max_length=60)
    subtitles_only: bool = False  # save just the subtitles (the video may already be on disk)
    source: SourceIn | None = None
    overwrite: bool = False  # the user confirmed replacing an existing file

    _url = field_validator("url")(_check_url)

    @field_validator("sub_manual", "sub_auto")
    @classmethod
    def _langs(cls, value: list[str]) -> list[str]:
        if not all(re.fullmatch(r"[A-Za-z0-9_-]{1,20}", v) for v in value):
            raise ValueError("Unknown subtitle language.")
        return value

    @field_validator("quality")
    @classmethod
    def _quality(cls, value: str | None) -> str | None:
        if value is not None and not _QUALITY.fullmatch(value):
            raise ValueError("Quality must look like 720p or best.")
        return value


class PlaylistEntryIn(BaseModel):
    title: str = Field(default="", max_length=500)
    url: str

    _url = field_validator("url")(_check_url)


class PlaylistDownloadIn(BaseModel):
    title: str = Field(default="", max_length=500)
    entries: list[PlaylistEntryIn] = Field(min_length=1, max_length=500)
    fmt: Literal["mp4", "mp3"] = "mp4"
    quality: str | None = None

    @field_validator("quality")
    @classmethod
    def _quality(cls, value: str | None) -> str | None:
        if value is not None and not _QUALITY.fullmatch(value):
            raise ValueError("Quality must look like 720p or best.")
        return value
