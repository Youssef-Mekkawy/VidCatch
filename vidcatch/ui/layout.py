"""Shared UI building blocks: fonts, RTL helper, styled widgets, thumbnail loader."""

from __future__ import annotations

import tkinter
import urllib.request
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageOps

from .. import theme
from ..config import QUALITY_CHOICES

_fonts: dict[tuple[int, bool], ctk.CTkFont] = {}


def font(size: int = 12, bold: bool = False) -> ctk.CTkFont:
    """Cached CTkFont in the platform UI font."""
    key = (size, bold)
    if key not in _fonts:
        _fonts[key] = ctk.CTkFont(
            family=theme.font_family(), size=size, weight="bold" if bold else "normal"
        )
    return _fonts[key]


# ── Right-to-left support ───────────────────────────────────────────────────
class Direction:
    """Mirrors grid columns, anchors and padding for right-to-left languages.

    Views are rebuilt when the language changes, so each one just asks this helper
    where things go instead of re-flipping widgets by hand.
    """

    def __init__(self, rtl: bool):
        self.rtl = rtl

    @property
    def anchor(self) -> str:
        return "e" if self.rtl else "w"

    @property
    def end_anchor(self) -> str:
        return "w" if self.rtl else "e"

    @property
    def justify(self) -> str:
        return "right" if self.rtl else "left"

    @property
    def side(self) -> str:
        """pack() side for the start of a row."""
        return "right" if self.rtl else "left"

    @property
    def end_side(self) -> str:
        return "left" if self.rtl else "right"

    def col(self, index: int, count: int) -> int:
        """Grid column for the index-th item of a row of `count` columns."""
        return count - 1 - index if self.rtl else index

    def pad(self, start: int, end: int) -> tuple[int, int]:
        """Horizontal padding given as (start, end); swapped for RTL."""
        return (end, start) if self.rtl else (start, end)

    def order(self, items: Sequence) -> list:
        return list(reversed(items)) if self.rtl else list(items)


# ── Styled widgets ──────────────────────────────────────────────────────────
def card(parent, **kwargs) -> ctk.CTkFrame:
    kwargs.setdefault("fg_color", theme.CARD)
    kwargs.setdefault("corner_radius", theme.RADIUS)
    return ctk.CTkFrame(parent, **kwargs)


def primary_button(parent, text: str, command: Callable, **kwargs) -> ctk.CTkButton:
    opts = {
        "fg_color": theme.RED,
        "hover_color": theme.RED_HOVER,
        "text_color": theme.WHITE,
        "text_color_disabled": theme.GREY,
        "font": font(13, True),
        "corner_radius": 8,
    }
    opts.update(kwargs)
    return ctk.CTkButton(parent, text=text, command=command, **opts)


def secondary_button(parent, text: str, command: Callable, **kwargs) -> ctk.CTkButton:
    opts = {
        "fg_color": theme.CARD2,
        "hover_color": theme.GREY2,
        "border_color": theme.BORDER,
        "border_width": 1,
        "text_color": theme.WHITE,
        "text_color_disabled": theme.GREY2,
        "font": font(12),
        "corner_radius": 8,
    }
    opts.update(kwargs)
    return ctk.CTkButton(parent, text=text, command=command, **opts)


def ghost_button(parent, text: str, command: Callable, **kwargs) -> ctk.CTkButton:
    opts = {
        "fg_color": "transparent",
        "hover_color": theme.DANGER_HOVER,
        "border_color": theme.BORDER,
        "border_width": 1,
        "text_color": theme.GREY,
        "text_color_disabled": theme.GREY2,
        "font": font(12),
        "corner_radius": 8,
    }
    opts.update(kwargs)
    return ctk.CTkButton(parent, text=text, command=command, **opts)


def field_label(parent, text: str, direction: Direction) -> ctk.CTkLabel:
    return ctk.CTkLabel(
        parent, text=text, font=font(12, True), text_color=theme.GREY, anchor=direction.anchor
    )


def segmented(
    parent,
    items: Sequence[tuple[str, str]],
    current: str,
    command: Callable[[str], None],
    direction: Direction,
    **kwargs,
) -> ctk.CTkSegmentedButton:
    """Segmented control over (key, label) pairs. `command` receives the key."""
    ordered = direction.order(items)
    labels = [label for _, label in ordered]
    key_by_label = {label: key for key, label in ordered}
    label_by_key = {key: label for key, label in ordered}
    opts = {
        "fg_color": theme.CARD2,
        "selected_color": theme.RED,
        "selected_hover_color": theme.RED_HOVER,
        "unselected_color": theme.CARD2,
        "unselected_hover_color": theme.GREY2,
        "text_color": theme.WHITE,
        "font": font(12),
        "corner_radius": 8,
    }
    opts.update(kwargs)
    seg = ctk.CTkSegmentedButton(
        parent, values=labels, command=lambda label: command(key_by_label[label]), **opts
    )
    seg.set(label_by_key.get(current, labels[0]))
    return seg


def quality_labels(t: dict[str, str]) -> list[tuple[str, str]]:
    """(key, label) pairs for the default-quality dropdowns."""
    return [(q, t["s_quality_best"] if q == "best" else q) for q in QUALITY_CHOICES]


def quality_menu(
    parent, t: dict[str, str], current: str, command: Callable[[str], None], **kwargs
) -> ctk.CTkOptionMenu:
    """Dropdown over the fixed quality choices. `command` receives the key ('best', '720p', …)."""
    items = quality_labels(t)
    key_by_label = {label: key for key, label in items}
    label_by_key = dict(items)
    opts = {
        "fg_color": theme.CARD2,
        "button_color": theme.RED,
        "button_hover_color": theme.RED_HOVER,
        "dropdown_fg_color": theme.CARD2,
        "dropdown_hover_color": theme.CARD,
        "text_color": theme.WHITE,
        "font": font(12),
        "dropdown_font": font(12),
        "height": 34,
    }
    opts.update(kwargs)
    menu = ctk.CTkOptionMenu(
        parent,
        values=[label for _, label in items],
        command=lambda label: command(key_by_label[label]),
        **opts,
    )
    menu.set(label_by_key.get(current, items[0][1]))
    return menu


def section_header(parent, text: str, direction: Direction) -> ctk.CTkFrame:
    """Small red caps label with a hairline, used to group settings."""
    frame = ctk.CTkFrame(parent, fg_color="transparent")
    label_col, line_col = direction.col(0, 2), direction.col(1, 2)
    frame.columnconfigure(line_col, weight=1)
    ctk.CTkLabel(frame, text=text.upper(), font=font(10, True), text_color=theme.RED).grid(
        row=0, column=label_col, sticky=direction.anchor
    )
    line = ctk.CTkFrame(frame, fg_color=theme.BORDER, height=1, corner_radius=0)
    line.grid(row=0, column=line_col, sticky="ew", padx=direction.pad(10, 0), pady=6)
    return frame


# ── Thumbnails ──────────────────────────────────────────────────────────────
THUMB_RADIUS = 8
_SUPERSAMPLE = 4


def _round_corners(image: Image.Image, radius: int = THUMB_RADIUS) -> Image.Image:
    """Return the image as RGBA with anti-aliased rounded corners."""
    scale = _SUPERSAMPLE
    mask = Image.new("L", (image.width * scale, image.height * scale), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, mask.width - 1, mask.height - 1), radius=radius * scale, fill=255
    )
    rounded = image.convert("RGBA")
    rounded.putalpha(mask.resize(image.size, Image.Resampling.LANCZOS))
    return rounded


def _placeholder_pil(size: tuple[int, int]) -> Image.Image:
    """Grey rounded box with a play triangle, shown until a thumbnail arrives."""
    scale = _SUPERSAMPLE
    big = Image.new("RGBA", (size[0] * scale, size[1] * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(big)
    draw.rounded_rectangle(
        (0, 0, big.width - 1, big.height - 1), radius=THUMB_RADIUS * scale, fill=theme.CARD2
    )
    cx, cy = big.width / 2, big.height / 2
    h = min(big.width, big.height) * 0.30
    w = h * 0.9
    draw.polygon(
        [(cx - w / 3, cy - h / 2), (cx - w / 3, cy + h / 2), (cx + 2 * w / 3, cy)],
        fill=theme.GREY2,
    )
    return big.resize(size, Image.Resampling.LANCZOS)


_placeholders: dict[tuple[int, int], ctk.CTkImage] = {}


class ThumbnailLoader:
    """Downloads thumbnails on a small thread pool and applies them on the UI thread."""

    def __init__(self, app):
        self.app = app
        self._cache: dict[tuple[str, tuple[int, int]], ctk.CTkImage] = {}
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="thumb")

    def load(self, label: ctk.CTkLabel, url: str, size: tuple[int, int]) -> None:
        if not url:
            return
        key = (url, size)
        cached = self._cache.get(key)
        if cached is not None:
            self._apply(label, cached)
        else:
            self._pool.submit(self._fetch, label, key)

    def _fetch(self, label: ctk.CTkLabel, key: tuple[str, tuple[int, int]]) -> None:
        url, size = key
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(request, timeout=8) as response:
                data = response.read()
            image = _round_corners(
                ImageOps.fit(
                    Image.open(BytesIO(data)).convert("RGB"), size, Image.Resampling.LANCZOS
                )
            )
        except Exception:
            return

        def apply() -> None:
            ctk_image = ctk.CTkImage(light_image=image, dark_image=image, size=size)
            self._cache[key] = ctk_image
            self._apply(label, ctk_image)

        self.app.post(apply)

    @staticmethod
    def _apply(label: ctk.CTkLabel, image: ctk.CTkImage) -> None:
        try:
            label.configure(image=image, text="")
        except (tkinter.TclError, ValueError):
            pass  # the row was destroyed before the image arrived

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


def thumb_placeholder(parent, size: tuple[int, int]) -> ctk.CTkLabel:
    """Label that shows a rounded placeholder until ThumbnailLoader swaps in the real image."""
    if size not in _placeholders:
        pil = _placeholder_pil(size)
        _placeholders[size] = ctk.CTkImage(light_image=pil, dark_image=pil, size=size)
    return ctk.CTkLabel(
        parent, text="", image=_placeholders[size], width=size[0], height=size[1],
        fg_color="transparent", corner_radius=0,
    )
