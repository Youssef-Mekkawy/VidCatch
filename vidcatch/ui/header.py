"""Top bar: logo, Download/History tabs, language toggle and settings button."""

from __future__ import annotations

import customtkinter as ctk

from .. import theme
from .layout import font


class Header(ctk.CTkFrame):
    def __init__(self, app):
        super().__init__(app.window, fg_color=theme.CARD, corner_radius=0)
        self.app = app
        d, t = app.direction, app.t
        self._history_count = 0
        self.columnconfigure(1, weight=1)

        accent = ctk.CTkFrame(self, fg_color=theme.RED, height=3, corner_radius=0)
        accent.grid(row=0, column=0, columnspan=3, sticky="ew")

        ctk.CTkLabel(
            self, text=f"▶  {t['title']}", font=font(19, True), text_color=theme.WHITE
        ).grid(row=1, column=d.col(0, 3), padx=20, pady=12, sticky=d.anchor)

        tabs = ctk.CTkFrame(self, fg_color="transparent")
        tabs.grid(row=1, column=1, pady=10)
        self.btn_download = ctk.CTkButton(
            tabs, text=t["tab_download"], width=130, height=32, corner_radius=8,
            command=lambda: app.show("download"),
        )
        self.btn_history = ctk.CTkButton(
            tabs, text=t["tab_history"], width=130, height=32, corner_radius=8,
            command=lambda: app.show("history"),
        )
        for button in d.order([self.btn_download, self.btn_history]):
            button.pack(side="left", padx=2)

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=1, column=d.col(2, 3), padx=16, pady=10, sticky=d.end_anchor)
        self.btn_lang = ctk.CTkButton(
            actions, text=t["lang_switch"], width=90, height=32, corner_radius=8,
            fg_color=theme.CARD2, hover_color=theme.GREY2, text_color=theme.WHITE,
            border_color=theme.BORDER, border_width=1, font=font(12),
            command=app.toggle_language,
        )
        self.btn_settings = ctk.CTkButton(
            actions, text="⚙", width=36, height=32, corner_radius=8,
            fg_color=theme.CARD2, hover_color=theme.RED, text_color=theme.WHITE,
            border_color=theme.BORDER, border_width=1, font=font(16),
            command=app.toggle_settings,
        )
        for button in (self.btn_lang, self.btn_settings):
            button.pack(side=d.side, padx=3)

        self.set_tab("download")

    def set_tab(self, name: str) -> None:
        """Highlight the active tab ('download' or 'history')."""
        for key, button in (("download", self.btn_download), ("history", self.btn_history)):
            if key == name:
                button.configure(
                    fg_color=theme.RED, hover_color=theme.RED_HOVER, text_color=theme.WHITE,
                    border_width=0, font=font(12, True),
                )
            else:
                button.configure(
                    fg_color=theme.CARD2, hover_color=theme.GREY2, text_color=theme.GREY,
                    border_width=1, border_color=theme.BORDER, font=font(12),
                )

    def set_history_count(self, count: int) -> None:
        self._history_count = count
        label = self.app.t["tab_history"]
        self.btn_history.configure(text=f"{label}  ({count})" if count else label)

    def set_settings_active(self, active: bool) -> None:
        self.btn_settings.configure(
            fg_color=theme.RED if active else theme.CARD2,
            hover_color=theme.RED_HOVER if active else theme.RED,
        )
