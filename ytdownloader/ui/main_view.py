"""The Download tab: URL bar, video info, options, progress."""

from __future__ import annotations

import threading
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from .. import AUTHOR, __version__, theme
from ..config import add_history_entry, make_history_entry
from ..downloader import (
    DownloadCancelled,
    FormatSummary,
    build_download_options,
    clean_error,
    download_media,
    extract_formats,
    fetch_video_info,
    login_reason,
    make_progress_hook,
    needs_login,
    pick_quality,
    with_js_fallback,
)
from ..signin import fetch_with_sources, resolve_sources
from ..system import check_internet, open_folder
from ..utils import (
    format_count,
    format_duration,
    format_filesize,
    output_filename,
    parse_height,
)
from .account_picker import AccountPicker
from .layout import (
    card,
    field_label,
    font,
    primary_button,
    secondary_button,
    segmented,
    thumb_placeholder,
)

WRAP = 460  # wrap width for the title and metadata in the info card


@dataclass
class MainState:
    """Everything the Download tab remembers, so it survives a rebuild (language change)."""

    url: str = ""
    fetched_url: str = ""  # the URL that `info` belongs to
    fmt: str = "mp4"
    subtitles: str = "none"
    quality: str = ""
    info: dict | None = None
    formats: FormatSummary | None = None
    cookies: tuple[str, str] | None = None  # browser profile used for the fetched video, if any
    notice: tuple[str, str, str] | None = None  # (kind, reason, detail) shown as a red box


class MainView(ctk.CTkFrame):
    def __init__(self, app, parent, state: MainState):
        super().__init__(parent, fg_color=theme.BG, corner_radius=0)
        self.app = app
        self.state = state
        self.t = app.t
        self.d = app.direction
        self.cancel_event = threading.Event()

        self.url_var = tk.StringVar(value=state.url)
        self.status_var = tk.StringVar(value=self.t["ready"])
        self.speed_var = tk.StringVar()

        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.body = ctk.CTkScrollableFrame(
            self,
            fg_color=theme.BG,
            corner_radius=0,
            scrollbar_button_color=theme.CARD2,
            scrollbar_button_hover_color=theme.GREY2,
        )
        self.body.grid(row=0, column=0, sticky="nsew")
        self.body.columnconfigure(0, weight=1)

        self._build_url_bar()
        self._build_info_card()
        self._build_options_card()
        self._build_download_card()
        self._build_footer()

        self._render_info()
        self._render_quality()
        self.url_var.trace_add("write", lambda *_: setattr(self.state, "url", self.url_var.get()))

    # ── Layout ──────────────────────────────────────────────────────────────
    def _build_url_bar(self) -> None:
        d, t = self.d, self.t
        bar = card(self.body)
        bar.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        bar.columnconfigure(d.col(0, 2), weight=1)

        self.url_entry = ctk.CTkEntry(
            bar,
            textvariable=self.url_var,
            placeholder_text=t["url_placeholder"],
            height=44,
            fg_color=theme.CARD2,
            border_color=theme.BORDER,
            border_width=1,
            text_color=theme.WHITE,
            font=font(13),
        )
        self.url_entry.grid(row=0, column=d.col(0, 2), sticky="ew", padx=d.pad(14, 6), pady=14)
        self.url_entry.bind("<<Paste>>", self._on_paste)
        self.url_entry.bind("<Return>", lambda _e: self.start_fetch())

        self.btn_fetch = primary_button(
            bar, t["fetch_btn"], self.start_fetch, width=110, height=44, font=font(13, True)
        )
        self.btn_fetch.grid(row=0, column=d.col(1, 2), padx=d.pad(0, 14), pady=14)

    def _build_info_card(self) -> None:
        d = self.d
        info = card(self.body)
        info.grid(row=1, column=0, sticky="ew", padx=16, pady=6)
        info.columnconfigure(d.col(1, 2), weight=1)

        self.thumb = thumb_placeholder(info, theme.THUMB_SIZE)
        self.thumb.grid(row=0, column=d.col(0, 2), padx=16, pady=16, sticky="n")

        meta = ctk.CTkFrame(info, fg_color="transparent")
        meta.grid(row=0, column=d.col(1, 2), padx=d.pad(0, 16), pady=16, sticky="nsew")
        meta.columnconfigure(0, weight=1)

        common = {"anchor": d.anchor, "justify": d.justify, "wraplength": WRAP}
        self.lbl_title = ctk.CTkLabel(meta, text="", font=font(15, True), **common)
        self.lbl_title.grid(row=0, column=0, sticky="ew", pady=(2, 6))
        # Channel, duration and views are separate labels: mixing an Arabic channel name with
        # numbers in one string scrambles the order of the numbers.
        self.meta_row = ctk.CTkFrame(meta, fg_color="transparent")
        self.meta_row.grid(row=1, column=0, sticky="ew", pady=2)
        self.lbl_size = ctk.CTkLabel(meta, text="", font=font(12, True), **common)
        self.lbl_size.grid(row=2, column=0, sticky="ew", pady=(10, 2))
        self.notice_box = ctk.CTkFrame(
            meta, fg_color=theme.DANGER_HOVER, border_width=1, border_color=theme.RED,
            corner_radius=8,
        )
        self.notice_box.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        self.notice_label = ctk.CTkLabel(
            self.notice_box, text="", font=font(12), text_color=theme.WHITE,
            anchor=d.anchor, justify=d.justify, wraplength=WRAP - 28,
        )
        self.notice_label.pack(fill="x", padx=12, pady=10)
        self.notice_box.grid_remove()

    def _build_options_card(self) -> None:
        d, t, s = self.d, self.t, self.state
        options = card(self.body)
        options.grid(row=2, column=0, sticky="ew", padx=16, pady=6)
        options.columnconfigure(0, weight=1)
        options.columnconfigure(1, weight=1)

        fmt_col = ctk.CTkFrame(options, fg_color="transparent")
        fmt_col.grid(row=0, column=d.col(0, 2), sticky="new", padx=16, pady=(16, 10))
        field_label(fmt_col, t["format_label"], d).pack(fill="x", pady=(0, 8))
        self._fmt_labels = {"mp4": t["mp4_label"], "mp3": t["mp3_label"]}
        self.seg_format = segmented(
            fmt_col, list(self._fmt_labels.items()), s.fmt, self._on_format, d, height=34
        )
        self.seg_format.pack(fill="x")

        qual_col = ctk.CTkFrame(options, fg_color="transparent")
        qual_col.grid(row=0, column=d.col(1, 2), sticky="new", padx=16, pady=(16, 10))
        field_label(qual_col, t["quality_label"], d).pack(fill="x", pady=(0, 8))
        self.quality_menu = ctk.CTkOptionMenu(
            qual_col,
            values=[t["select_quality"]],
            command=self._on_quality,
            fg_color=theme.CARD2,
            button_color=theme.RED,
            button_hover_color=theme.RED_HOVER,
            dropdown_fg_color=theme.CARD2,
            dropdown_hover_color=theme.CARD,
            text_color=theme.WHITE,
            text_color_disabled=theme.GREY2,
            font=font(13),
            dropdown_font=font(13),
            height=34,
            anchor=d.anchor,
        )
        self.quality_menu.pack(fill="x")

        subs = ctk.CTkFrame(options, fg_color="transparent")
        subs.grid(row=1, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 16))
        field_label(subs, t["sub_label"], d).pack(side=d.side, padx=d.pad(0, 14))
        segmented(
            subs,
            [("none", t["sub_none"]), ("auto", t["sub_auto"]), ("manual", t["sub_manual"])],
            s.subtitles,
            self._on_subtitles,
            d,
            height=30,
        ).pack(side=d.side)

    def _build_download_card(self) -> None:
        d, t = self.d, self.t
        dl = card(self.body)
        dl.grid(row=3, column=0, sticky="ew", padx=16, pady=6)
        dl.columnconfigure(0, weight=1)

        buttons = ctk.CTkFrame(dl, fg_color="transparent")
        buttons.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))
        buttons.columnconfigure(d.col(0, 2), weight=1)
        self.btn_download = primary_button(
            buttons, t["download_btn"], self.start_download, height=50, font=font(16, True)
        )
        self.btn_download.grid(row=0, column=d.col(0, 2), sticky="ew", padx=d.pad(0, 10))
        self.btn_cancel = secondary_button(
            buttons, t["cancel_btn"], self.cancel, width=120, height=50, font=font(14),
            state="disabled",
        )
        self.btn_cancel.grid(row=0, column=d.col(1, 2))

        self.progress = ctk.CTkProgressBar(
            dl, height=8, fg_color=theme.CARD2, progress_color=theme.RED, corner_radius=4
        )
        self.progress.grid(row=1, column=0, sticky="ew", padx=16, pady=(4, 8))
        self.progress.set(0)

        status = ctk.CTkFrame(dl, fg_color="transparent")
        status.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 16))
        status.columnconfigure(d.col(0, 2), weight=1)
        ctk.CTkLabel(
            status, textvariable=self.status_var, font=font(12), text_color=theme.GREY,
            anchor=d.anchor,
        ).grid(row=0, column=d.col(0, 2), sticky="ew")
        ctk.CTkLabel(
            status, textvariable=self.speed_var, font=font(12), text_color=theme.GREY,
            anchor=d.end_anchor,
        ).grid(row=0, column=d.col(1, 2), sticky=d.end_anchor)

    def _build_footer(self) -> None:
        d, t = self.d, self.t
        footer = ctk.CTkFrame(self.body, fg_color="transparent")
        footer.grid(row=4, column=0, sticky="ew", padx=16, pady=(2, 14))
        footer.columnconfigure(d.col(0, 2), weight=1)
        author = f"{t['author_prefix']} {AUTHOR}" if AUTHOR else ""
        ctk.CTkLabel(
            footer, text=author, font=font(11), text_color=theme.GREY2, anchor=d.anchor
        ).grid(row=0, column=d.col(0, 2), sticky="ew")
        ctk.CTkLabel(
            footer, text=t["version_fmt"].format(version=__version__), font=font(11),
            text_color=theme.GREY2,
        ).grid(row=0, column=d.col(1, 2), sticky=d.end_anchor)

    # ── Rendering from state ────────────────────────────────────────────────
    def _set_meta(self, parts: list[str]) -> None:
        """Show `parts` joined by dots, each in its own label so every part keeps its direction."""
        d = self.d
        for child in self.meta_row.winfo_children():
            child.destroy()
        for column in range(12):  # forget the stretch column of the previous render
            self.meta_row.columnconfigure(column, weight=0)
        items: list[str] = []
        for text in (p for p in parts if p):
            if items:
                items.append("·")
            items.append(text)
        shown = d.order(items)
        offset = 1 if self.app.rtl else 0
        for column, text in enumerate(shown):
            ctk.CTkLabel(
                self.meta_row, text=text, font=font(12), text_color=theme.GREY, width=0,
                wraplength=WRAP, anchor=d.anchor, justify=d.justify,
            ).grid(row=0, column=column + offset, padx=3)
        spacer = 0 if self.app.rtl else len(shown)
        self.meta_row.columnconfigure(spacer, weight=1)

    def _render_info(self) -> None:
        t, s = self.t, self.state
        if not s.info:
            if s.notice:
                self.lbl_title.configure(text=t["notice_title"], text_color=theme.WHITE)
                self.meta_row.grid_remove()
                self.lbl_size.grid_remove()
            else:
                self.lbl_title.configure(text=t["info_empty_title"], text_color=theme.GREY)
                self._set_meta([t["info_empty_hint"]])
                self.meta_row.grid()
                self.lbl_size.grid()
            self.lbl_size.configure(text="")
            self._render_notice()
            return
        self.meta_row.grid()
        self.lbl_size.grid()

        info = s.info
        self.lbl_title.configure(text=info.get("title") or "—", text_color=theme.WHITE)
        self._set_meta(
            [
                info.get("uploader") or info.get("channel") or "",
                format_duration(info.get("duration")),
                f"{format_count(info.get('view_count'))} {t['views']}"
                if info.get("view_count")
                else "",
            ]
        )
        self._render_size()
        self.app.thumbs.load(self.thumb, info.get("thumbnail") or "", theme.THUMB_SIZE)
        self._render_notice()

    def _render_notice(self) -> None:
        """The red box in the video card: why a video cannot be fetched or downloaded."""
        t, notice = self.t, self.state.notice
        if not notice:
            self.notice_box.grid_remove()
            return
        kind, reason, detail = notice
        reason_text = {
            "bot": t["reason_bot"],
            "age": t["reason_age"],
            "private": t["reason_private"],
            "members": t["reason_members"],
        }.get(reason, t["reason_login"])
        messages = {
            "never": t["notice_never"].format(reason=reason_text),
            "locked": t["notice_locked"],
            "encrypted": t["notice_encrypted"],
            "not_signed_in": t["notice_not_signed_in"],
            "none_installed": t["notice_none_installed"],
            "rejected": t["notice_rejected"].format(reason=reason_text),
            "other": t["notice_other"].format(error=detail),
            "no_account": t["notice_no_account"],
        }
        self.notice_label.configure(text=messages.get(kind, messages["other"]))
        self.notice_box.grid()

    def _set_notice(self, kind: str, reason: str = "", detail: str = "") -> None:
        self.state.notice = (kind, reason, detail)
        self._render_info()

    def _render_quality(self) -> None:
        t, s = self.t, self.state
        labels = s.formats.labels if s.formats else []
        if labels:
            if s.quality not in labels:
                s.quality = pick_quality(s.formats.heights, self.app.config["default_quality"])
            self.quality_menu.configure(values=labels)
            self.quality_menu.set(s.quality)
            enabled = s.fmt == "mp4"
        else:
            placeholder = t["no_formats"] if s.info else t["select_quality"]
            self.quality_menu.configure(values=[placeholder])
            self.quality_menu.set(placeholder)
            enabled = False
        self.quality_menu.configure(state="normal" if enabled else "disabled")
        self._render_size()

    def _render_size(self) -> None:
        t, s = self.t, self.state
        if not s.info:
            self.lbl_size.configure(text="")
            return
        size = 0
        if s.formats:
            audio = s.formats.audio_size
            if s.fmt == "mp3":
                size = audio
            else:
                video = s.formats.video_sizes.get(parse_height(s.quality) or 0, 0)
                size = video + audio if video else 0
        text = f"{t['filesize_label']}: {format_filesize(size)}" if size > 0 else t["filesize_unknown"]
        self.lbl_size.configure(text=text, text_color=theme.WHITE if size > 0 else theme.GREY)

    # ── Option callbacks ────────────────────────────────────────────────────
    def _on_format(self, key: str) -> None:
        self.state.fmt = key
        self._render_quality()

    def _on_quality(self, label: str) -> None:
        self.state.quality = label
        self._render_size()

    def _on_subtitles(self, key: str) -> None:
        self.state.subtitles = key

    def set_format(self, fmt: str) -> None:
        """Called by the settings panel when the default format changes."""
        self.state.fmt = fmt
        self.seg_format.set(self._fmt_labels[fmt])
        self._render_quality()

    # ── Fetch ───────────────────────────────────────────────────────────────
    def _on_paste(self, _event=None) -> None:
        self.after(50, self._auto_fetch)

    def _auto_fetch(self) -> None:
        url = self.url_var.get().strip()
        if ("youtube.com" in url or "youtu.be" in url) and not self.app.busy:
            self.start_fetch()

    def start_fetch(self) -> None:
        t = self.t
        url = self.url_var.get().strip()
        if not url:
            messagebox.showerror(t["fetch_err"], t["no_url"], parent=self.app.window)
            return
        if self.app.busy:
            messagebox.showinfo(t["title"], t["task_running"], parent=self.app.window)
            return
        self.state.notice = None
        self.state.cookies = None
        self._render_notice()
        self.app.begin_task()
        self.btn_fetch.configure(state="disabled", text=t["fetching_btn"])
        self.btn_download.configure(state="disabled")
        self.status_var.set(t["fetching"])
        threading.Thread(target=self._fetch_worker, args=(url,), daemon=True, name="fetch").start()

    def _fetch_worker(self, url: str) -> None:
        post = self.app.post
        if not check_internet():
            post(self._fetch_no_internet)
            return
        try:
            is_playlist, info = fetch_video_info(url)  # always without cookies first
        except Exception as exc:
            message = clean_error(exc)
            post(lambda: self._fetch_failed(message, url))
            return
        if is_playlist:
            post(lambda: self._fetch_playlist(info))
        else:
            post(lambda: self._fetch_finished(url, info))

    def _end_fetch(self) -> None:
        self.app.end_task()
        self.btn_fetch.configure(state="normal", text=self.t["fetch_btn"])
        self.btn_download.configure(state="normal")
        self.status_var.set(self.t["ready"])

    def _fetch_no_internet(self) -> None:
        self._end_fetch()
        messagebox.showerror(
            self.t["no_internet_title"], self.t["no_internet_msg"], parent=self.app.window
        )

    def _fetch_failed(self, message: str, url: str = "") -> None:
        self._end_fetch()
        if url and needs_login(message):
            self._login_needed(url, message, self._signin_fetched)
            return
        messagebox.showerror(self.t["fetch_err"], message, parent=self.app.window)

    def _fetch_playlist(self, info: dict) -> None:
        self._end_fetch()
        self.app.open_playlist(info)

    def _fetch_finished(self, url: str, info: dict) -> None:
        self._end_fetch()
        s = self.state
        s.info, s.fetched_url = info, url
        s.formats = extract_formats(info)
        s.quality = pick_quality(s.formats.heights, self.app.config["default_quality"])
        self._render_info()
        self._render_quality()
        if self.app.config["auto_download"]:
            self.after(300, self.start_download)

    # ── Sign-in (only when YouTube refuses a video and the user allowed it) ──
    def _login_needed(self, url: str, message: str, on_success) -> None:
        """YouTube wants a signed-in session. What happens depends on the user's choice:
        ask (first time) -> explain and let them accept or refuse,
        allow -> use their browser session, never -> just show the red notice."""
        t = self.t
        reason = login_reason(message)
        consent = self.app.config["signin_consent"]
        first_time = False
        if consent == "ask":
            reasons = {
                "bot": t["reason_bot"], "age": t["reason_age"], "private": t["reason_private"],
                "members": t["reason_members"],
            }
            prompt = t["signin_prompt"].format(reason=reasons.get(reason, t["reason_login"]))
            allowed = messagebox.askyesno(t["signin_title"], prompt, parent=self.app.window)
            consent = "allow" if allowed else "never"
            first_time = allowed
            self.app.update_settings(signin_consent=consent)
            self.app.settings.refresh_signin()
        if consent != "allow":
            self._set_notice("never", reason)
            return
        self.app.begin_task()
        self.btn_fetch.configure(state="disabled")
        self.btn_download.configure(state="disabled")
        self.status_var.set(t["signin_trying"])
        threading.Thread(
            target=self._resolve_worker, args=(url, reason, first_time, on_success),
            daemon=True, name="signin",
        ).start()

    def _resolve_worker(self, url: str, reason: str, first_time: bool, on_success) -> None:
        cfg = self.app.config
        resolution = resolve_sources(cfg["cookies_browser"], cfg["cookies_profile"])
        self.app.post(lambda: self._resolved(url, reason, first_time, resolution, on_success))

    def _resolved(self, url, reason, first_time, resolution, on_success) -> None:
        if not resolution.usable:
            self._end_fetch()
            self._set_notice(resolution.kind, reason, resolution.error)
            return
        sources = resolution.usable
        if first_time and not self.app.config["cookies_browser"] and len(sources) > 1:
            chosen = AccountPicker(self.app, sources).choose()
            if chosen is None:
                self._end_fetch()
                self._set_notice("no_account", reason)
                return
            self.app.update_settings(
                cookies_browser=chosen.browser, cookies_profile=chosen.profile
            )
            self.app.settings.refresh_signin()
            sources = [chosen]
        threading.Thread(
            target=self._signin_fetch_worker, args=(url, reason, sources, on_success),
            daemon=True, name="signin-fetch",
        ).start()

    def _signin_fetch_worker(self, url: str, reason: str, sources, on_success) -> None:
        source, result, error = fetch_with_sources(url, sources)
        self.app.post(lambda: self._signin_done(url, reason, source, result, error, on_success))

    def _signin_done(self, url, reason, source, result, error, on_success) -> None:
        if source is None:
            self._end_fetch()
            self._set_notice("rejected", reason, error)
            return
        self.state.cookies = source.cookies
        on_success(url, source, result)

    def _signin_fetched(self, url: str, source, result: tuple[bool, dict]) -> None:
        is_playlist, info = result
        if is_playlist:
            self._fetch_playlist(info)
        else:
            self._fetch_finished(url, info)
        self.status_var.set(self.t["signin_ok"].format(name=source.title))

    def _signin_then_download(self, _url: str, source, _result) -> None:
        self._end_fetch()
        self.status_var.set(self.t["signin_ok"].format(name=source.title))
        self.after(100, self.start_download)

    # ── Download ────────────────────────────────────────────────────────────
    def start_download(self) -> None:
        t, s = self.t, self.state
        url = self.url_var.get().strip()
        if not url:
            messagebox.showerror(t["dl_err"], t["no_url"], parent=self.app.window)
            return
        if not s.info or url != s.fetched_url:
            messagebox.showerror(t["dl_err"], t["no_quality"], parent=self.app.window)
            return
        if self.app.busy:
            messagebox.showinfo(t["title"], t["task_running"], parent=self.app.window)
            return

        quality = s.quality if s.fmt == "mp4" and s.formats and s.formats.heights else None
        filename = output_filename(s.info.get("title") or "", s.fmt, quality)
        final = Path(self.app.download_dir) / filename
        if final.exists() and self.app.config["ask_overwrite"]:
            if not messagebox.askyesno(
                t["overwrite_title"], f"{filename}\n{t['file_exists']}", parent=self.app.window
            ):
                return

        self.state.notice = None
        self._render_notice()
        self.cancel_event.clear()
        self.app.begin_task()
        self.btn_download.configure(state="disabled")
        self.btn_fetch.configure(state="disabled")
        self.btn_cancel.configure(state="normal")
        self.progress.configure(progress_color=theme.RED)
        self.progress.set(0)
        self.status_var.set(t["starting"])
        self.speed_var.set("")

        job = {"url": url, "fmt": s.fmt, "quality": quality, "subtitles": s.subtitles,
               "final": str(final), "cookies": s.cookies}
        threading.Thread(
            target=self._download_worker, args=(job,), daemon=True, name="download"
        ).start()

    def cancel(self) -> None:
        self.cancel_event.set()
        self.btn_cancel.configure(state="disabled")
        self.status_var.set(self.t["cancelled"])
        self.speed_var.set("")

    def _download_worker(self, job: dict) -> None:
        post = self.app.post
        hook = make_progress_hook(
            on_progress=lambda pct, speed, eta: post(lambda: self._on_progress(pct, speed, eta)),
            on_finish=lambda: post(self._on_stream_finished),
            cancel_event=self.cancel_event,
        )
        def attempt(js: bool):
            opts = build_download_options(
                job["fmt"], job["quality"], job["subtitles"], hook, cookies=job["cookies"], js=js
            )
            return download_media(job["url"], opts, self.app.temp_dir, job["final"])

        try:
            path, info = with_js_fallback(attempt)
        except Exception as exc:
            if self.cancel_event.is_set() or isinstance(exc, DownloadCancelled):
                post(self._on_cancelled)
            else:
                message = clean_error(exc)
                post(lambda: self._on_failed(message))
        else:
            post(lambda: self._on_done(path, info, job))

    def _on_progress(self, pct: float, speed: str, eta: str) -> None:
        if self.cancel_event.is_set():
            return
        t = self.t
        self.progress.set(pct / 100)
        self.status_var.set(f"{t['downloading']}  {pct:.0f}%")
        parts = []
        if speed:
            parts.append(f"{t['speed']}: {speed}")
        if eta:
            parts.append(f"{t['eta']}: {eta}")
        self.speed_var.set("  ·  ".join(parts))

    def _on_stream_finished(self) -> None:
        if not self.cancel_event.is_set():
            self.status_var.set(self.t["converting"])
            self.speed_var.set("")

    def _finish_task(self) -> None:
        self.app.end_task()
        self.btn_download.configure(state="normal")
        self.btn_fetch.configure(state="normal")
        self.btn_cancel.configure(state="disabled")

    def _on_cancelled(self) -> None:
        self._finish_task()
        self.progress.set(0)
        self.status_var.set(self.t["cancelled"])
        self.speed_var.set("")

    def _on_failed(self, message: str) -> None:
        self._finish_task()
        self.progress.set(0)
        self.status_var.set(self.t["failed"])
        self.speed_var.set("")
        url = self.state.fetched_url
        if url and needs_login(message):
            if self.state.cookies is None:
                self._login_needed(url, message, self._signin_then_download)
            else:  # already signed in and YouTube still says no: the account cannot open it
                self._set_notice("rejected", login_reason(message), message)
            return
        messagebox.showerror(self.t["dl_err"], message, parent=self.app.window)

    def _on_done(self, path: str, info: dict, job: dict) -> None:
        self._finish_task()
        self.progress.set(1.0)
        self.progress.configure(progress_color=theme.GREEN)
        self.after(3000, lambda: self.progress.configure(progress_color=theme.RED))
        self.status_var.set(self.t["completed"])
        self.speed_var.set("")

        add_history_entry(make_history_entry(info, job["url"], job["fmt"], job["quality"], path))
        self.app.refresh_history_badge()

        cfg = self.app.config
        if cfg["clear_url"]:
            self.url_var.set("")
        if cfg["open_folder"]:
            self.after(500, lambda: open_folder(self.app.download_dir))
