"""Slide-in settings panel."""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk

from .. import APP_NAME, AUTHOR, __version__, theme
from ..signin import Source, list_profiles, resolve_sources
from ..system import detect_browsers
from .layout import (
    card,
    field_label,
    font,
    quality_menu,
    secondary_button,
    section_header,
    segmented,
)

INNER_WIDTH = theme.PANEL_WIDTH - 30


class SettingsPanel(ctk.CTkFrame):
    def __init__(self, app, parent):
        super().__init__(
            parent,
            fg_color=theme.PANEL_BG,
            corner_radius=0,
            border_width=1,
            border_color=theme.BORDER,
            width=theme.PANEL_WIDTH,
        )
        self.app = app
        self.t = app.t
        self.d = app.direction
        self._vars: list[tk.BooleanVar] = []  # keep references so switches stay bound
        self.grid_propagate(False)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        self._build_header()
        self.body = ctk.CTkScrollableFrame(
            self,
            fg_color="transparent",
            corner_radius=0,
            scrollbar_button_color=theme.CARD2,
            scrollbar_button_hover_color=theme.GREY2,
        )
        self.body.grid(row=1, column=0, sticky="nsew")
        self.body.columnconfigure(0, weight=1)
        self._build_body()

    # ── Layout ──────────────────────────────────────────────────────────────
    def _build_header(self) -> None:
        d, t = self.d, self.t
        header = ctk.CTkFrame(self, fg_color=theme.CARD, corner_radius=0)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(d.col(0, 2), weight=1)
        ctk.CTkFrame(header, fg_color=theme.RED, height=3, corner_radius=0).grid(
            row=0, column=0, columnspan=2, sticky="ew"
        )
        ctk.CTkLabel(
            header, text=t["settings_title"], font=font(15, True), text_color=theme.WHITE,
            anchor=d.anchor,
        ).grid(row=1, column=d.col(0, 2), padx=16, pady=12, sticky="ew")
        ctk.CTkButton(
            header, text="×", width=32, height=32, corner_radius=8, fg_color="transparent",
            hover_color=theme.GREY2, text_color=theme.GREY, font=font(20),
            command=self.app.toggle_settings,
        ).grid(row=1, column=d.col(1, 2), padx=10, pady=8)

    def _build_body(self) -> None:
        d, t, cfg = self.d, self.t, self.app.config
        body = self.body
        row = 0

        def place(widget, pady=(0, 12)) -> None:
            nonlocal row
            widget.grid(row=row, column=0, sticky="ew", padx=14, pady=pady)
            row += 1

        # Download
        place(section_header(body, t["section_download"], d), pady=(14, 8))
        place(field_label(body, t["s_folder"], d), pady=(0, 4))

        path_box = ctk.CTkFrame(body, fg_color=theme.CARD2, corner_radius=8)
        path_box.columnconfigure(0, weight=1)
        self.folder_label = ctk.CTkLabel(
            path_box, text=self.app.download_dir, font=font(11), text_color=theme.GREY,
            anchor="w", justify="left", wraplength=INNER_WIDTH - 24,
        )
        self.folder_label.grid(row=0, column=0, sticky="ew", padx=12, pady=10)
        place(path_box, pady=(0, 8))

        buttons = ctk.CTkFrame(body, fg_color="transparent")
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)
        secondary_button(buttons, t["s_browse"], self._browse, height=34).grid(
            row=0, column=d.col(0, 2), sticky="ew", padx=d.pad(0, 4)
        )
        secondary_button(
            buttons, t["s_open_dl_folder"], self.app.open_download_folder, height=34
        ).grid(row=0, column=d.col(1, 2), sticky="ew", padx=d.pad(4, 0))
        place(buttons, pady=(0, 14))

        place(field_label(body, t["s_default_format"], d), pady=(0, 4))
        place(
            segmented(
                body, [("mp4", "MP4"), ("mp3", "MP3")], cfg["default_format"],
                self._on_default_format, d, height=34,
            ),
            pady=(0, 14),
        )
        place(field_label(body, t["s_default_quality"], d), pady=(0, 4))
        place(quality_menu(body, t, cfg["default_quality"], self._on_default_quality), pady=(0, 6))

        # Sign-in: use the user's own browser session, only when a video needs it
        place(section_header(body, t["section_signin"], d), pady=(14, 8))
        place(self._signin_switch(), pady=(0, 6))
        self.signin_hint = ctk.CTkLabel(
            body, text="", font=font(10), text_color=theme.GREY2, height=0,
            anchor=d.anchor, justify=d.justify, wraplength=INNER_WIDTH,
        )
        place(self.signin_hint, pady=(0, 8))
        place(field_label(body, t["s_signin_browser"], d), pady=(0, 4))
        self.browser_menu = self._option_menu(body, self._on_browser)
        place(self.browser_menu, pady=(0, 10))
        place(field_label(body, t["s_signin_account"], d), pady=(0, 4))
        self.account_menu = self._option_menu(body, self._on_account)
        place(self.account_menu, pady=(0, 10))
        self.test_button = secondary_button(body, t["s_signin_test"], self._test_signin, height=34)
        place(self.test_button, pady=(0, 6))
        self.test_result = ctk.CTkLabel(
            body, text="", font=font(11), text_color=theme.GREY,
            anchor=d.anchor, justify=d.justify, wraplength=INNER_WIDTH,
        )
        place(self.test_result, pady=(0, 6))
        place(
            ctk.CTkLabel(
                body, text=t["s_signin_privacy"], font=font(10), text_color=theme.GREY2,
                anchor=d.anchor, justify=d.justify, wraplength=INNER_WIDTH,
            ),
            pady=(0, 10),
        )
        self.refresh_signin()

        # Behavior
        place(section_header(body, t["section_behavior"], d), pady=(14, 8))
        for key, label in (
            ("auto_download", t["s_auto_download"]),
            ("open_folder", t["s_open_folder"]),
            ("ask_overwrite", t["s_ask_overwrite"]),
            ("clear_url", t["s_clear_url"]),
            ("check_updates", t["s_check_updates"]),
        ):
            place(self._toggle(body, key, label), pady=(0, 6))

        # Language
        place(section_header(body, t["section_language"], d), pady=(14, 8))
        place(field_label(body, t["s_lang_label"], d), pady=(0, 4))
        self._lang_labels = {"en": "English", "ar": "العربية"}
        self.lang_seg = segmented(
            body, list(self._lang_labels.items()), self.app.lang, self.app.set_language, d,
            height=34,
        )
        place(self.lang_seg, pady=(0, 6))

        # About
        place(section_header(body, t["section_about"], d), pady=(14, 8))
        about = card(body, fg_color=theme.CARD2, corner_radius=8)
        ctk.CTkLabel(
            about, text=f"▶  {APP_NAME}", font=font(14, True), text_color=theme.RED
        ).pack(pady=(14, 2))
        ctk.CTkLabel(
            about, text=t["version_fmt"].format(version=__version__), font=font(11),
            text_color=theme.GREY,
        ).pack()
        if AUTHOR:
            ctk.CTkLabel(
                about, text=f"{t['author_prefix']} {AUTHOR}", font=font(11), text_color=theme.GREY
            ).pack()
        ctk.CTkLabel(
            about, text=t["s_about_desc"], font=font(11), text_color=theme.GREY,
            wraplength=INNER_WIDTH - 24,
        ).pack(pady=(8, 0))
        ctk.CTkLabel(
            about, text=t["s_about_powered"], font=font(10), text_color=theme.GREY2,
            wraplength=INNER_WIDTH - 24,
        ).pack(pady=(2, 14))
        place(about, pady=(0, 24))

    def _toggle(self, parent, key: str, label: str) -> ctk.CTkFrame:
        d = self.d
        frame = ctk.CTkFrame(parent, fg_color=theme.CARD2, corner_radius=8)
        frame.columnconfigure(d.col(0, 2), weight=1)
        var = tk.BooleanVar(value=bool(self.app.config[key]))
        self._vars.append(var)
        ctk.CTkLabel(
            frame, text=label, font=font(12), text_color=theme.WHITE, anchor=d.anchor,
            justify=d.justify, wraplength=INNER_WIDTH - 100,
        ).grid(row=0, column=d.col(0, 2), sticky="ew", padx=12, pady=10)
        ctk.CTkSwitch(
            frame, text="", variable=var, onvalue=True, offvalue=False, width=44,
            fg_color=theme.GREY2, progress_color=theme.RED, button_color=theme.WHITE,
            button_hover_color=theme.GREY,
            command=lambda: self.app.update_settings(**{key: var.get()}),
        ).grid(row=0, column=d.col(1, 2), padx=12, pady=10)
        return frame

    def reset_language_choice(self) -> None:
        """Put the language selector back on the active language (after a blocked change)."""
        self.lang_seg.set(self._lang_labels[self.app.lang])

    # ── Actions ─────────────────────────────────────────────────────────────
    def _browse(self) -> None:
        folder = filedialog.askdirectory(initialdir=self.app.download_dir, parent=self.app.window)
        if folder:
            self.app.update_settings(download_dir=folder)
            self.folder_label.configure(text=self.app.download_dir)

    # ── Sign-in ─────────────────────────────────────────────────────────────
    def _signin_switch(self) -> ctk.CTkFrame:
        d = self.d
        frame = ctk.CTkFrame(self.body, fg_color=theme.CARD2, corner_radius=8)
        frame.columnconfigure(d.col(0, 2), weight=1)
        self.signin_var = tk.BooleanVar(value=self.app.config["signin_consent"] == "allow")
        ctk.CTkLabel(
            frame, text=self.t["s_signin_switch"], font=font(12), text_color=theme.WHITE,
            anchor=d.anchor, justify=d.justify, wraplength=INNER_WIDTH - 130,
        ).grid(row=0, column=d.col(0, 2), sticky="ew", padx=12, pady=10)
        ctk.CTkSwitch(
            frame, text="", variable=self.signin_var, onvalue=True, offvalue=False, width=44,
            fg_color=theme.GREY2, progress_color=theme.RED, button_color=theme.WHITE,
            button_hover_color=theme.GREY, command=self._on_signin_switch,
        ).grid(row=0, column=d.col(1, 2), padx=12, pady=10)
        return frame

    def _option_menu(self, parent, command) -> ctk.CTkOptionMenu:
        return ctk.CTkOptionMenu(
            parent, values=[""], command=command,
            fg_color=theme.CARD2, button_color=theme.RED, button_hover_color=theme.RED_HOVER,
            dropdown_fg_color=theme.CARD2, dropdown_hover_color=theme.CARD,
            text_color=theme.WHITE, font=font(12), dropdown_font=font(12), height=34,
        )

    def _on_signin_switch(self) -> None:
        self.app.update_settings(signin_consent="allow" if self.signin_var.get() else "never")
        self.refresh_signin()

    def refresh_signin(self) -> None:
        """Re-read the sign-in settings into the controls (also called when the app changes them)."""
        t, cfg = self.t, self.app.config
        self.signin_var.set(cfg["signin_consent"] == "allow")
        self.signin_hint.configure(
            text=t["s_signin_undecided"] if cfg["signin_consent"] == "ask" else ""
        )
        browsers = detect_browsers()
        if cfg["cookies_browser"] and cfg["cookies_browser"] not in browsers:
            browsers.append(cfg["cookies_browser"])
        self._browser_labels = {"": t["s_signin_auto"], **{b: b.capitalize() for b in browsers}}
        self.browser_menu.configure(values=list(self._browser_labels.values()))
        self.browser_menu.set(self._browser_labels[cfg["cookies_browser"]])
        self._fill_accounts()

    def _fill_accounts(self) -> None:
        t, cfg = self.t, self.app.config
        browser = cfg["cookies_browser"]
        if not browser:
            self._account_sources: list[Source] = []
            self.account_menu.configure(values=[t["s_signin_account_auto"]], state="disabled")
            self.account_menu.set(t["s_signin_account_auto"])
            return
        sources = list_profiles(browser) or [Source(browser)]
        self._account_sources = sources
        labels = [s.label or browser.capitalize() for s in sources]
        self.account_menu.configure(values=labels, state="normal")
        chosen = next((i for i, s in enumerate(sources) if s.profile == cfg["cookies_profile"]), 0)
        self.account_menu.set(labels[chosen])

    def _on_browser(self, label: str) -> None:
        key = next((k for k, v in self._browser_labels.items() if v == label), "")
        profile = ""
        if key:
            sources = list_profiles(key)
            profile = sources[0].profile if sources else ""
        self.app.update_settings(cookies_browser=key, cookies_profile=profile)
        self._fill_accounts()

    def _on_account(self, label: str) -> None:
        labels = [s.label or self.app.config["cookies_browser"].capitalize() for s in self._account_sources]
        if label in labels:
            self.app.update_settings(cookies_profile=self._account_sources[labels.index(label)].profile)

    def _test_signin(self) -> None:
        if self.app.busy:
            messagebox.showinfo(self.t["title"], self.t["task_running"], parent=self.app.window)
            return
        cfg = self.app.config
        self.app.begin_task()  # keeps the language switch and new downloads out while testing
        self.test_button.configure(state="disabled", text=self.t["s_signin_testing"])
        self.test_result.configure(text="")
        threading.Thread(
            target=self._test_worker, args=(cfg["cookies_browser"], cfg["cookies_profile"]),
            daemon=True, name="signin-test",
        ).start()

    def _test_worker(self, browser: str, profile: str) -> None:
        resolution = resolve_sources(browser, profile)
        self.app.post(lambda: self._test_done(resolution))

    def _test_done(self, resolution) -> None:
        t = self.t
        self.app.end_task()
        self.test_button.configure(state="normal", text=t["s_signin_test"])
        if resolution.kind == "none_installed":
            self.test_result.configure(text=t["test_none"], text_color=theme.RED)
            return
        lines = []
        for probe in resolution.probes:
            if probe.signed_in:
                status = t["test_ok"]
            elif probe.kind == "locked":
                status = t["test_locked"]
            elif probe.kind == "encrypted":
                status = t["test_encrypted"]
            elif probe.kind == "not_signed_in":
                status = t["test_not_signed_in"]
            else:
                status = t["test_other"]
            lines.append(f"{probe.source.title}: {status}")
        self.test_result.configure(
            text="\n".join(lines), text_color=theme.GREEN if resolution.usable else theme.RED
        )

    def _on_default_format(self, key: str) -> None:
        self.app.update_settings(default_format=key)
        self.app.apply_default_format(key)

    def _on_default_quality(self, key: str) -> None:
        self.app.update_settings(default_quality=key)
