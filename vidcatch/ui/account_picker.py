"""Small modal dialog: which signed-in browser account should the app use?"""

from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

from .. import theme
from ..signin import Source
from .layout import font, ghost_button, primary_button


class AccountPicker(ctk.CTkToplevel):
    def __init__(self, app, sources: list[Source]) -> None:
        super().__init__(app.window)
        t, d = app.t, app.direction
        self.result: Source | None = None
        self._sources = sources
        self.title(t["signin_picker_title"])
        self.configure(fg_color=theme.BG)
        self.resizable(False, False)
        self.transient(app.window)

        ctk.CTkLabel(
            self, text=t["signin_picker_text"], font=font(13), wraplength=380,
            anchor=d.anchor, justify=d.justify,
        ).pack(fill="x", padx=22, pady=(20, 10))

        self._choice = tk.IntVar(value=0)
        for index, source in enumerate(sources):
            ctk.CTkRadioButton(
                self, text=source.title, variable=self._choice, value=index, font=font(12),
                fg_color=theme.RED, hover_color=theme.RED_HOVER,
            ).pack(anchor=d.anchor, padx=26, pady=4)

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.pack(fill="x", padx=22, pady=(16, 20))
        buttons.columnconfigure(d.col(0, 2), weight=1)
        primary_button(buttons, t["signin_picker_ok"], self._ok, height=36).grid(
            row=0, column=d.col(0, 2), sticky="ew", padx=d.pad(0, 6)
        )
        ghost_button(buttons, t["signin_picker_cancel"], self._cancel, height=36, width=90).grid(
            row=0, column=d.col(1, 2)
        )
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.after(150, self._grab)

    def _grab(self) -> None:
        try:
            self.lift()
            self.grab_set()
        except tk.TclError:
            pass

    def _ok(self) -> None:
        self.result = self._sources[self._choice.get()]
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()

    def choose(self) -> Source | None:
        """Show the dialog and wait. Returns the chosen source or None if cancelled."""
        self.wait_window(self)
        return self.result
