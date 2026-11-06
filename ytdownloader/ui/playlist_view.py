"""Playlist picker: choose videos from a playlist and download them in one go."""

from __future__ import annotations

import threading
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from .. import theme
from ..config import add_history_entry, make_history_entry
from ..downloader import (
    DownloadCancelled,
    build_download_options,
    clean_error,
    download_media,
    entry_thumbnail,
    entry_url,
    make_progress_hook,
    needs_login,
    with_js_fallback,
)
from ..signin import resolve_sources
from ..utils import format_count, format_duration, output_filename
from .layout import (
    card,
    font,
    primary_button,
    quality_menu,
    secondary_button,
    segmented,
    thumb_placeholder,
)

TITLE_WRAP = 380


@dataclass
class PlaylistState:
    """Playlist contents and choices, kept by the App so a rebuild does not lose them."""

    title: str
    entries: list[dict]
    checked: list[bool] = field(default_factory=list)
    fmt: str = "mp4"
    quality: str = "best"

    @classmethod
    def from_info(cls, info: dict, config: dict) -> PlaylistState:
        entries = [e for e in (info.get("entries") or []) if e]
        return cls(
            title=info.get("title") or "Playlist",
            entries=entries,
            checked=[True] * len(entries),
            fmt=config["default_format"],
            quality=config["default_quality"],
        )


class PlaylistView(ctk.CTkFrame):
    def __init__(self, app, parent, state: PlaylistState):
        super().__init__(parent, fg_color=theme.BG, corner_radius=0)
        self.app = app
        self.state = state
        self.t = app.t
        self.d = app.direction
        self.cancel_event = threading.Event()
        self.vars: list[tk.BooleanVar] = []
        self.status_var = tk.StringVar(value="")
        self.speed_var = tk.StringVar(value="")

        self.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=1)
        self._build_toolbar()
        self._build_list()
        self._build_bottom()
        self._update_count()

    # ── Layout ──────────────────────────────────────────────────────────────
    def _build_toolbar(self) -> None:
        d, t, s = self.d, self.t, self.state
        bar = ctk.CTkFrame(self, fg_color=theme.CARD, corner_radius=0)
        bar.grid(row=0, column=0, sticky="ew", pady=(2, 0))
        bar.columnconfigure(d.col(1, 4), weight=1)

        self.btn_back = primary_button(
            bar, t["playlist_back"], self.app.close_playlist, width=90, height=32,
            font=font(12, True),
        )
        self.btn_back.grid(row=0, column=d.col(0, 4), padx=12, pady=10)
        ctk.CTkLabel(
            bar,
            text=f"{s.title}  ·  {len(s.entries)} {t['playlist_videos']}",
            font=font(13, True),
            text_color=theme.WHITE,
            anchor=d.anchor,
        ).grid(row=0, column=d.col(1, 4), padx=8, sticky="ew")
        secondary_button(
            bar, t["playlist_select_all"], lambda: self._select_all(True), width=96, height=30,
            font=font(11),
        ).grid(row=0, column=d.col(2, 4), padx=d.pad(0, 4))
        secondary_button(
            bar, t["playlist_deselect_all"], lambda: self._select_all(False), width=96, height=30,
            font=font(11), text_color=theme.GREY,
        ).grid(row=0, column=d.col(3, 4), padx=d.pad(0, 12))

    def _build_list(self) -> None:
        self.list = ctk.CTkScrollableFrame(
            self,
            fg_color=theme.BG,
            corner_radius=0,
            scrollbar_button_color=theme.CARD2,
            scrollbar_button_hover_color=theme.GREY2,
        )
        self.list.grid(row=1, column=0, sticky="nsew")
        self.list.columnconfigure(0, weight=1)
        for index, entry in enumerate(self.state.entries):
            self._build_row(index, entry)

    def _build_row(self, index: int, entry: dict) -> None:
        d, t = self.d, self.t
        var = tk.BooleanVar(value=self.state.checked[index])
        self.vars.append(var)

        row = card(self.list, corner_radius=8)
        row.grid(row=index, column=0, sticky="ew", padx=12, pady=(10 if index == 0 else 0, 6))
        row.columnconfigure(d.col(3, 4), weight=1)

        ctk.CTkCheckBox(
            row, text="", variable=var, width=24, checkbox_width=22, checkbox_height=22,
            fg_color=theme.RED, hover_color=theme.RED_HOVER, checkmark_color=theme.WHITE,
            border_color=theme.GREY2, command=self._on_check,
        ).grid(row=0, column=d.col(0, 4), padx=d.pad(12, 4), pady=14)
        ctk.CTkLabel(
            row, text=f"{index + 1:02d}", font=font(10, True), text_color=theme.GREY2, width=26,
        ).grid(row=0, column=d.col(1, 4), padx=4)

        thumb = thumb_placeholder(row, theme.ROW_THUMB_SIZE)
        thumb.grid(row=0, column=d.col(2, 4), padx=8, pady=10)
        self.app.thumbs.load(thumb, entry_thumbnail(entry), theme.ROW_THUMB_SIZE)

        meta = ctk.CTkFrame(row, fg_color="transparent")
        meta.grid(row=0, column=d.col(3, 4), padx=d.pad(4, 12), pady=10, sticky="nsew")
        meta.columnconfigure(0, weight=1)
        ctk.CTkLabel(
            meta, text=entry.get("title") or f"Video {index + 1}", font=font(13, True),
            text_color=theme.WHITE, anchor=d.anchor, justify=d.justify, wraplength=TITLE_WRAP,
        ).grid(row=0, column=0, sticky="ew")

        views = format_count(entry.get("view_count"))
        parts = [
            format_duration(entry.get("duration")),
            f"{views} {t['views']}" if views else "",
            entry.get("uploader") or entry.get("channel") or "",
        ]
        details = "  ·  ".join(p for p in parts if p)
        if details:
            ctk.CTkLabel(
                meta, text=details, font=font(11), text_color=theme.GREY, anchor=d.anchor,
            ).grid(row=1, column=0, sticky="ew", pady=(4, 0))

    def _build_bottom(self) -> None:
        d, t, s = self.d, self.t, self.state
        bottom = ctk.CTkFrame(self, fg_color=theme.CARD, corner_radius=0)
        bottom.grid(row=2, column=0, sticky="ew")
        bottom.columnconfigure(d.col(2, 5), weight=1)

        segmented(
            bottom, [("mp4", "MP4"), ("mp3", "MP3")], s.fmt, self._on_format, d, height=34,
            width=150,
        ).grid(row=0, column=d.col(0, 5), padx=d.pad(14, 8), pady=12)
        self.quality = quality_menu(bottom, t, s.quality, self._on_quality, width=110, height=34)
        self.quality.grid(row=0, column=d.col(1, 5), padx=4, pady=12)
        self._sync_quality_state()

        self.btn_download = primary_button(
            bottom, "", self.start_download, width=210, height=40, font=font(14, True)
        )
        self.btn_download.grid(row=0, column=d.col(3, 5), padx=8, pady=12)
        self.btn_cancel = secondary_button(
            bottom, t["cancel_btn"], self.cancel, width=100, height=40, font=font(13),
            state="disabled",
        )
        self.btn_cancel.grid(row=0, column=d.col(4, 5), padx=d.pad(0, 14), pady=12)

        self.progress = ctk.CTkProgressBar(
            self, height=6, fg_color=theme.CARD2, progress_color=theme.RED, corner_radius=3
        )
        self.progress.grid(row=3, column=0, sticky="ew")
        self.progress.set(0)

        status = ctk.CTkFrame(self, fg_color=theme.CARD2, corner_radius=0)
        status.grid(row=4, column=0, sticky="ew")
        status.columnconfigure(d.col(0, 2), weight=1)
        ctk.CTkLabel(
            status, textvariable=self.status_var, font=font(11), text_color=theme.GREY,
            anchor=d.anchor,
        ).grid(row=0, column=d.col(0, 2), sticky="ew", padx=12, pady=6)
        ctk.CTkLabel(
            status, textvariable=self.speed_var, font=font(11), text_color=theme.GREY,
            anchor=d.end_anchor,
        ).grid(row=0, column=d.col(1, 2), sticky=d.end_anchor, padx=12, pady=6)

    # ── Selection ───────────────────────────────────────────────────────────
    def _select_all(self, value: bool) -> None:
        for var in self.vars:
            var.set(value)
        self._on_check()

    def _on_check(self) -> None:
        self.state.checked = [v.get() for v in self.vars]
        self._update_count()

    def _update_count(self) -> None:
        count = sum(self.state.checked)
        self.btn_download.configure(text=f"↓  {self.t['playlist_download_sel']}  ({count})")

    def _on_format(self, key: str) -> None:
        self.state.fmt = key
        self._sync_quality_state()

    def _on_quality(self, key: str) -> None:
        self.state.quality = key

    def _sync_quality_state(self) -> None:
        self.quality.configure(state="normal" if self.state.fmt == "mp4" else "disabled")

    # ── Download ────────────────────────────────────────────────────────────
    def start_download(self) -> None:
        t, s = self.t, self.state
        selected = [(i, e) for i, (e, on) in enumerate(zip(s.entries, s.checked, strict=False)) if on]
        if not selected:
            messagebox.showwarning(t["title"], t["playlist_none_sel"], parent=self.app.window)
            return
        if self.app.busy:
            messagebox.showinfo(t["title"], t["task_running"], parent=self.app.window)
            return

        self.cancel_event.clear()
        self.app.begin_task()
        self.btn_download.configure(state="disabled")
        self.btn_back.configure(state="disabled")
        self.btn_cancel.configure(state="normal")
        self.progress.configure(progress_color=theme.RED)
        self.progress.set(0)
        self.speed_var.set("")

        job = {
            "selected": selected,
            "fmt": s.fmt,
            "quality": s.quality if s.fmt == "mp4" else None,
            "out_dir": self.app.download_dir,
            "overwrite": not self.app.config["ask_overwrite"],
        }
        threading.Thread(
            target=self._worker, args=(job,), daemon=True, name="playlist"
        ).start()

    def cancel(self) -> None:
        self.cancel_event.set()
        self.btn_cancel.configure(state="disabled")
        self.status_var.set(self.t["cancelled"])
        self.speed_var.set("")

    def _worker(self, job: dict) -> None:
        post = self.app.post
        fmt, quality = job["fmt"], job["quality"]
        total = len(job["selected"])
        done = skipped = failed = signin = 0
        tried_signin, found_cookies = False, None  # looked up lazily, only if an item needs it

        for n, (index, entry) in enumerate(job["selected"]):
            if self.cancel_event.is_set():
                break
            title = entry.get("title") or f"video_{index + 1}"
            url = entry_url(entry)
            post(lambda n=n, title=title: self._on_item_start(n, total, title))
            if not url:
                failed += 1
                continue

            final = Path(job["out_dir"]) / output_filename(title, fmt, quality)
            if final.exists() and not job["overwrite"]:
                skipped += 1
                post(lambda n=n: self.progress.set((n + 1) / total))
                continue

            hook = make_progress_hook(
                on_progress=lambda pct, speed, eta, n=n: post(
                    lambda: self._on_item_progress(n, total, pct, speed, eta)
                ),
                on_finish=lambda: post(self._on_stream_finished),
                cancel_event=self.cancel_event,
            )
            cookies_now = None  # every item starts without cookies
            outcome = "failed"
            for attempt_no in range(2):
                try:
                    path, info = self._download_item(url, fmt, quality, final, hook, cookies_now)
                    outcome = "done"
                    break
                except Exception as exc:
                    if self.cancel_event.is_set() or isinstance(exc, DownloadCancelled):
                        outcome = "cancelled"
                        break
                    if not needs_login(clean_error(exc)):
                        break
                    outcome = "signin"
                    if attempt_no == 0 and self.app.config["signin_consent"] == "allow":
                        if not tried_signin:
                            tried_signin = True
                            cfg = self.app.config
                            found = resolve_sources(cfg["cookies_browser"], cfg["cookies_profile"])
                            found_cookies = found.usable[0].cookies if found.usable else None
                        if found_cookies:
                            cookies_now = found_cookies
                            continue
                    break
            if outcome == "cancelled":
                break
            if outcome == "done":
                done += 1
                add_history_entry(make_history_entry(info, url, fmt, quality, path))
                post(self.app.refresh_history_badge)
            elif outcome == "signin":
                signin += 1
            else:
                failed += 1
            post(lambda n=n: self.progress.set((n + 1) / total))

        post(lambda: self._on_finished(done, skipped, failed, signin))

    def _download_item(self, url, fmt, quality, final, hook, cookies):
        def attempt(js: bool):
            opts = build_download_options(fmt, quality, "none", hook, cookies=cookies, js=js)
            return download_media(url, opts, self.app.temp_dir, final)

        return with_js_fallback(attempt)

    def _on_item_start(self, n: int, total: int, title: str) -> None:
        if not self.cancel_event.is_set():
            self.status_var.set(f"{self.t['playlist_progress']}  {n + 1}/{total}:  {title[:50]}")
            self.speed_var.set("")

    def _on_item_progress(self, n: int, total: int, pct: float, speed: str, eta: str) -> None:
        if self.cancel_event.is_set():
            return
        t = self.t
        self.progress.set((n + pct / 100) / total)
        parts = []
        if speed:
            parts.append(f"{t['speed']}: {speed}")
        if eta:
            parts.append(f"{t['eta']}: {eta}")
        self.speed_var.set("  ·  ".join(parts))

    def _on_stream_finished(self) -> None:
        if not self.cancel_event.is_set():
            self.speed_var.set(self.t["converting"])

    def _on_finished(self, done: int, skipped: int, failed: int, signin: int = 0) -> None:
        t = self.t
        self.app.end_task()
        self.btn_download.configure(state="normal")
        self.btn_back.configure(state="normal")
        self.btn_cancel.configure(state="disabled")
        self.speed_var.set("")
        if self.cancel_event.is_set():
            self.status_var.set(t["cancelled"])
            return
        summary = t["playlist_summary"].format(done=done, skipped=skipped, failed=failed)
        if signin:
            summary += "  ·  " + t["playlist_need_signin"].format(n=signin)
        self.status_var.set(f"{t['playlist_done']}:  {summary}")
        self.progress.set(1.0)
        self.progress.configure(progress_color=theme.GREEN)
        self.after(3000, lambda: self.progress.configure(progress_color=theme.RED))
