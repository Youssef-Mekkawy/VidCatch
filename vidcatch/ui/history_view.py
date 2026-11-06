"""The History tab: list of finished downloads."""

from __future__ import annotations

import os
from tkinter import messagebox

import customtkinter as ctk

from .. import theme
from ..config import clear_history, load_history, remove_history_entry
from ..system import reveal_file
from ..utils import format_filesize
from .layout import card, font, ghost_button, primary_button, secondary_button, thumb_placeholder

TITLE_WRAP = 400


class HistoryView(ctk.CTkFrame):
    def __init__(self, app, parent):
        super().__init__(parent, fg_color=theme.BG, corner_radius=0)
        self.app = app
        self.t = app.t
        self.d = app.direction
        d, t = self.d, self.t

        self.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=1)

        toolbar = ctk.CTkFrame(self, fg_color=theme.CARD, corner_radius=0)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(2, 0))
        toolbar.columnconfigure(1, weight=1)
        primary_button(
            toolbar, t["history_back"], lambda: app.show("download"), width=90, height=32,
            font=font(12, True),
        ).grid(row=0, column=d.col(0, 3), padx=16, pady=10)
        ctk.CTkLabel(
            toolbar, text=t["history_title"], font=font(14, True), text_color=theme.WHITE
        ).grid(row=0, column=1, pady=10)
        ghost_button(
            toolbar, t["history_clear"], self._clear, width=110, height=32,
            hover_color=theme.GREY2,
        ).grid(row=0, column=d.col(2, 3), padx=16, pady=10)

        self.list = ctk.CTkScrollableFrame(
            self,
            fg_color=theme.BG,
            corner_radius=0,
            scrollbar_button_color=theme.CARD2,
            scrollbar_button_hover_color=theme.GREY2,
        )
        self.list.grid(row=1, column=0, sticky="nsew")
        self.list.columnconfigure(0, weight=1)

    def refresh(self) -> None:
        for child in self.list.winfo_children():
            child.destroy()
        entries = list(reversed(load_history()))
        if not entries:
            self._build_empty()
            return
        for index, entry in enumerate(entries):
            self._build_row(index, entry)

    def _build_empty(self) -> None:
        t = self.t
        box = ctk.CTkFrame(self.list, fg_color="transparent")
        box.grid(row=0, column=0, pady=80)
        ctk.CTkLabel(box, text=t["history_empty"], font=font(16, True), text_color=theme.GREY).pack()
        ctk.CTkLabel(
            box, text=t["history_empty_hint"], font=font(12), text_color=theme.GREY2
        ).pack(pady=(6, 0))

    def _build_row(self, index: int, entry: dict) -> None:
        d, t = self.d, self.t
        row = card(self.list)
        row.grid(row=index, column=0, sticky="ew", padx=16, pady=(12 if index == 0 else 0, 8))
        row.columnconfigure(d.col(1, 3), weight=1)

        thumb = thumb_placeholder(row, theme.ROW_THUMB_SIZE)
        thumb.grid(row=0, column=d.col(0, 3), padx=12, pady=12)
        self.app.thumbs.load(thumb, entry.get("thumbnail", ""), theme.ROW_THUMB_SIZE)

        meta = ctk.CTkFrame(row, fg_color="transparent")
        meta.grid(row=0, column=d.col(1, 3), sticky="nsew", pady=12)
        meta.columnconfigure(0, weight=1)

        ctk.CTkLabel(
            meta, text=entry.get("title") or "—", font=font(13, True), text_color=theme.WHITE,
            anchor=d.anchor, justify=d.justify, wraplength=TITLE_WRAP,
        ).grid(row=0, column=0, sticky="ew")

        fmt = (entry.get("format") or "").lower()
        badge = fmt.upper()
        if fmt == "mp4" and entry.get("quality"):
            badge += f" · {entry['quality']}"
        line = ctk.CTkFrame(meta, fg_color="transparent")
        line.grid(row=1, column=0, sticky=d.anchor, pady=(4, 0))
        ctk.CTkLabel(
            line, text=f" {badge} ", font=font(10, True), text_color=theme.WHITE,
            fg_color=theme.RED if fmt == "mp4" else theme.BLUE, corner_radius=4,
        ).pack(side=d.side, padx=d.pad(0, 8))
        if entry.get("channel"):
            ctk.CTkLabel(
                line, text=entry["channel"], font=font(11), text_color=theme.GREY
            ).pack(side=d.side)

        details = "  ·  ".join(
            p for p in (format_filesize(entry.get("filesize")), entry.get("date", "")) if p
        )
        ctk.CTkLabel(
            meta, text=details, font=font(10), text_color=theme.GREY2, anchor=d.anchor,
        ).grid(row=2, column=0, sticky="ew", pady=(4, 0))

        actions = ctk.CTkFrame(row, fg_color="transparent")
        actions.grid(row=0, column=d.col(2, 3), padx=12, pady=12)
        path = entry.get("filepath", "")
        secondary_button(
            actions, t["history_open_file"], lambda p=path: self._reveal(p), width=124, height=30,
        ).pack(pady=(0, 6))
        entry_id = entry.get("id", "")
        ghost_button(
            actions, t["history_delete"], lambda: self._delete(entry_id),
            width=124, height=30,
        ).pack()

    def _reveal(self, path: str) -> None:
        if not path or not os.path.exists(path):
            messagebox.showwarning(self.t["title"], self.t["history_file_gone"], parent=self.app.window)
            return
        reveal_file(path)

    def _delete(self, entry_id: str) -> None:
        remove_history_entry(entry_id)
        self.refresh()
        self.app.refresh_history_badge()

    def _clear(self) -> None:
        if messagebox.askyesno(
            self.t["history_title"], self.t["history_confirm_clear"], parent=self.app.window
        ):
            clear_history()
            self.refresh()
            self.app.refresh_history_badge()
