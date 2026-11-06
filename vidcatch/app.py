"""Application window and controller: navigation, shared state, settings, UI-thread queue."""

from __future__ import annotations

import queue
import subprocess
import sys
import threading
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from . import theme
from .config import (
    import_legacy_files,
    load_config,
    load_history,
    save_config,
    temp_dir,
)
from .downloader import cleanup_stale_temp
from .i18n import TRANSLATIONS, is_rtl
from .system import (
    check_ffmpeg,
    detect_js_runtimes,
    get_platform,
    install_ffmpeg,
    open_folder,
)
from .ui.header import Header
from .ui.history_view import HistoryView
from .ui.layout import Direction, ThumbnailLoader, font, ghost_button
from .ui.main_view import MainState, MainView
from .ui.playlist_view import PlaylistState, PlaylistView
from .ui.settings_panel import SettingsPanel
from .updates import (
    can_self_update,
    fetch_latest_version,
    installed_version,
    is_newer,
    update_ytdlp,
)


class App:
    def __init__(self) -> None:
        package_dir = Path(__file__).resolve().parent
        import_legacy_files(Path.cwd(), package_dir.parent)

        self.config = load_config()
        self.lang = self.config["language"]
        self.js_runtimes = detect_js_runtimes()
        self.temp_dir = temp_dir()
        cleanup_stale_temp(self.temp_dir)
        try:
            Path(self.download_dir).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

        if not check_ffmpeg():
            self._ensure_ffmpeg_pre_window()

        self._queue: queue.Queue = queue.Queue()
        self._tasks = 0
        self._closing = False
        self._banner_dismissed = False
        self._update_latest = ""  # newer yt-dlp version found on PyPI, if any
        self._update_state = "available"  # 'available' | 'running' | 'done'
        self._banners: dict[str, ctk.CTkFrame] = {}
        self._settings_open = False
        self._panel_offset = float(theme.PANEL_WIDTH)
        self._anim_id: str | None = None

        self._create_window()
        self.thumbs = ThumbnailLoader(self)

        self.main_state = MainState(fmt=self.config["default_format"])
        self.playlist_state: PlaylistState | None = None
        self.current_view = "download"
        self.header = self.banner = self.content = self.settings = None
        self.main_view = self.history_view = self.playlist_view = None

        self.build_ui()
        self.window.after(30, self._pump)
        if self.config["check_updates"]:
            threading.Thread(target=self._check_updates, daemon=True, name="updates").start()

    # ── Properties ──────────────────────────────────────────────────────────
    @property
    def t(self) -> dict[str, str]:
        return TRANSLATIONS[self.lang]

    @property
    def rtl(self) -> bool:
        return is_rtl(self.lang)

    @property
    def direction(self) -> Direction:
        return Direction(self.rtl)

    @property
    def download_dir(self) -> str:
        return self.config["download_dir"]

    @property
    def busy(self) -> bool:
        return self._tasks > 0

    def begin_task(self) -> None:
        self._tasks += 1

    def end_task(self) -> None:
        self._tasks = max(0, self._tasks - 1)

    # ── Window and UI-thread queue ──────────────────────────────────────────
    def _create_window(self) -> None:
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")
        self.window = ctk.CTk()
        self.window.geometry(theme.WINDOW_SIZE)
        self.window.minsize(*theme.WINDOW_MIN_SIZE)
        self.window.configure(fg_color=theme.BG)
        self.window.rowconfigure(2, weight=1)
        self.window.columnconfigure(0, weight=1)
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)
        self.window.bind("<Escape>", self._on_escape)

    def post(self, callback) -> None:
        """Run `callback` on the UI thread. Safe to call from any thread."""
        self._queue.put(callback)

    def _pump(self) -> None:
        try:
            while True:
                callback = self._queue.get_nowait()
                try:
                    callback()
                except Exception:
                    traceback.print_exc()
        except queue.Empty:
            pass
        if not self._closing:
            self.window.after(30, self._pump)

    def _on_escape(self, _event) -> None:
        if self._settings_open:
            self.toggle_settings()

    def _on_close(self) -> None:
        if self.busy and not messagebox.askyesno(
            self.t["title"], self.t["quit_confirm"], parent=self.window
        ):
            return
        self._closing = True
        for view in (self.main_view, self.playlist_view):
            if view is not None:
                view.cancel_event.set()
        self.thumbs.shutdown()
        self.window.destroy()

    # ── Building the UI ─────────────────────────────────────────────────────
    def build_ui(self) -> None:
        """(Re)build every view from the current language. State objects survive."""
        for widget in (self.header, self.banner, self.content):
            if widget is not None:
                widget.destroy()  # also destroys the views and the settings panel inside content
        self.window.title(self.t["title"])

        self.header = Header(self)
        self.header.grid(row=0, column=0, sticky="ew")
        self._build_banner()

        self.content = ctk.CTkFrame(self.window, fg_color=theme.BG, corner_radius=0)
        self.content.grid(row=2, column=0, sticky="nsew")
        self.content.rowconfigure(0, weight=1)
        self.content.columnconfigure(0, weight=1)

        self.main_view = MainView(self, self.content, self.main_state)
        self.history_view = HistoryView(self, self.content)
        self.playlist_view = (
            PlaylistView(self, self.content, self.playlist_state) if self.playlist_state else None
        )
        for view in (self.main_view, self.history_view, self.playlist_view):
            if view is not None:
                view.grid(row=0, column=0, sticky="nsew")

        self.settings = SettingsPanel(self, self.content)
        self._panel_offset = 0.0 if self._settings_open else float(theme.PANEL_WIDTH)
        if self._settings_open:
            self.settings.lift()
            self._place_panel(0)
            self.header.set_settings_active(True)

        self.show(self.current_view)
        self.refresh_history_badge()

    def _build_banner(self) -> None:
        """Thin notice strips under the header: missing JS runtime, yt-dlp update."""
        self.banner = None
        self._banners = {}
        if not self.js_runtimes and not self._banner_dismissed:
            self._add_banner(
                "js", self.t["js_banner"], [(self.t["js_dismiss"], self._dismiss_banner)]
            )
        if self._update_latest and self._update_state != "dismissed":
            self._render_update_banner()

    def _add_banner(self, key: str, text: str, buttons: list) -> None:
        """Show (or replace) the strip `key`. `buttons` is a list of (label, command)."""
        d = self.direction
        if self.banner is None:
            self.banner = ctk.CTkFrame(self.window, fg_color="transparent", corner_radius=0, height=0)
            self.banner.grid(row=1, column=0, sticky="ew")
            self.banner.columnconfigure(0, weight=1)
        old = self._banners.pop(key, None)
        if old is not None:
            old.destroy()
        count = 2 + len(buttons)
        strip = ctk.CTkFrame(self.banner, fg_color=theme.CARD2, corner_radius=0, height=0)
        strip.grid(row=len(self._banners), column=0, sticky="ew", pady=(0, 1))
        strip.columnconfigure(d.col(1, count), weight=1)
        ctk.CTkFrame(strip, fg_color=theme.RED, width=4, height=1, corner_radius=0).grid(
            row=0, column=d.col(0, count), sticky="ns"
        )
        ctk.CTkLabel(
            strip, text=text, font=font(11), text_color=theme.GREY,
            anchor=d.anchor, justify=d.justify, wraplength=620,
        ).grid(row=0, column=d.col(1, count), sticky="ew", padx=12, pady=8)
        for index, (label, command) in enumerate(buttons):
            ghost_button(
                strip, label, command, width=90, height=26, font=font(11),
                hover_color=theme.GREY2,
            ).grid(row=0, column=d.col(2 + index, count), padx=(0, 12))
        self._banners[key] = strip

    def _remove_banner(self, key: str) -> None:
        strip = self._banners.pop(key, None)
        if strip is not None:
            strip.destroy()
        if not self._banners and self.banner is not None:
            self.banner.destroy()
            self.banner = None

    def _dismiss_banner(self) -> None:
        self._banner_dismissed = True
        self._remove_banner("js")

    # ── yt-dlp updates ──────────────────────────────────────────────────────
    def _check_updates(self) -> None:
        latest = fetch_latest_version()
        if latest and is_newer(latest, installed_version()):
            self.post(lambda: self._on_update_found(latest))

    def _on_update_found(self, latest: str) -> None:
        self._update_latest = latest
        self._update_state = "available"
        self._render_update_banner()

    def _render_update_banner(self) -> None:
        t = self.t
        latest = self._update_latest
        if self._update_state == "done":
            self._add_banner(
                "update", t["update_done"].format(latest=latest),
                [(t["update_restart"], self._restart), (t["js_dismiss"], self._dismiss_update)],
            )
        elif self._update_state == "running":
            self._add_banner("update", t["update_running"], [])
        elif can_self_update():
            self._add_banner(
                "update",
                t["update_available"].format(latest=latest, current=installed_version()),
                [(t["update_btn"], self._start_update), (t["js_dismiss"], self._dismiss_update)],
            )
        else:
            self._add_banner(
                "update", t["update_manual"].format(latest=latest),
                [(t["js_dismiss"], self._dismiss_update)],
            )

    def _dismiss_update(self) -> None:
        self._update_state = "dismissed"
        self._remove_banner("update")

    def _start_update(self) -> None:
        if self.busy:
            messagebox.showinfo(self.t["title"], self.t["task_running"], parent=self.window)
            return
        self.begin_task()  # nothing else may start while pip replaces files
        self._update_state = "running"
        self._render_update_banner()
        threading.Thread(target=self._update_worker, daemon=True, name="pip-update").start()

    def _update_worker(self) -> None:
        ok, output = update_ytdlp()
        self.post(lambda: self._update_finished(ok, output))

    def _update_finished(self, ok: bool, output: str) -> None:
        self.end_task()
        if ok:
            self._update_state = "done"
            self._render_update_banner()
            return
        self._update_state = "available"
        self._render_update_banner()
        messagebox.showerror(
            self.t["title"], self.t["update_failed"].format(error=output), parent=self.window
        )

    def _restart(self) -> None:
        """Start a fresh copy of the app (same command line, so `-m` launches keep working)."""
        if self.busy:
            messagebox.showinfo(self.t["title"], self.t["task_running"], parent=self.window)
            return
        try:
            subprocess.Popen(list(getattr(sys, "orig_argv", [sys.executable, *sys.argv])))
        except OSError:
            return
        self._closing = True
        self.thumbs.shutdown()
        self.window.destroy()

    # ── Navigation ──────────────────────────────────────────────────────────
    def show(self, name: str) -> None:
        """Show 'download', 'history' or 'playlist' (an open playlist is the Download tab)."""
        if name == "download" and self.playlist_view is not None:
            name = "playlist"
        if name == "playlist" and self.playlist_view is None:
            name = "download"
        self.current_view = name
        views = {
            "download": self.main_view,
            "history": self.history_view,
            "playlist": self.playlist_view,
        }
        for key, view in views.items():
            if view is None:
                continue
            if key == name:
                view.grid()
            else:
                view.grid_remove()
        if name == "history":
            self.history_view.refresh()
        self.header.set_tab("history" if name == "history" else "download")

    def open_playlist(self, info: dict) -> None:
        if self.playlist_view is not None:
            self.playlist_view.destroy()
        self.playlist_state = PlaylistState.from_info(info, self.config)
        self.playlist_view = PlaylistView(self, self.content, self.playlist_state)
        self.playlist_view.grid(row=0, column=0, sticky="nsew")
        self.show("playlist")

    def close_playlist(self) -> None:
        if self.playlist_view is not None:
            self.playlist_view.destroy()
        self.playlist_view = None
        self.playlist_state = None
        self.show("download")

    def refresh_history_badge(self) -> None:
        self.header.set_history_count(len(load_history()))

    # ── Settings ────────────────────────────────────────────────────────────
    def update_settings(self, **changes) -> None:
        self.config.update(changes)
        if "download_dir" in changes:
            try:
                Path(self.download_dir).mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
        save_config(self.config)

    def apply_default_format(self, fmt: str) -> None:
        self.main_view.set_format(fmt)

    def open_download_folder(self) -> None:
        try:
            open_folder(self.download_dir)
        except OSError:
            pass

    def set_language(self, lang: str) -> None:
        if lang == self.lang:
            return
        if self.busy:
            messagebox.showinfo(self.t["title"], self.t["busy_lang"], parent=self.window)
            self.settings.reset_language_choice()
            return
        self.lang = lang
        self.update_settings(language=lang)
        # Rebuild after this click handler returns; it may live inside a widget we destroy.
        self.window.after(10, self.build_ui)

    def toggle_language(self) -> None:
        self.set_language("ar" if self.lang == "en" else "en")

    def toggle_settings(self) -> None:
        self._slide_settings(not self._settings_open)

    def _place_panel(self, offset: float) -> None:
        # CustomTkinter takes width from the constructor; only relheight is passed here.
        options = {"y": 0, "relheight": 1.0}
        if self.rtl:
            self.settings.place(relx=0.0, x=-int(offset), anchor="nw", **options)
        else:
            self.settings.place(relx=1.0, x=int(offset), anchor="ne", **options)

    def _slide_settings(self, opening: bool) -> None:
        self._settings_open = opening
        self.header.set_settings_active(opening)
        if self._anim_id is not None:
            self.window.after_cancel(self._anim_id)
            self._anim_id = None
        target = 0.0 if opening else float(theme.PANEL_WIDTH)
        if opening:
            self.settings.lift()

        def step() -> None:
            distance = target - self._panel_offset
            if abs(distance) < 2:
                self._panel_offset = target
                if opening:
                    self._place_panel(target)
                else:
                    self.settings.place_forget()
                self._anim_id = None
                return
            self._panel_offset += distance * 0.28
            self._place_panel(self._panel_offset)
            self._anim_id = self.window.after(12, step)

        step()

    # ── FFmpeg check before the main window exists ──────────────────────────
    def _ensure_ffmpeg_pre_window(self) -> None:
        t = self.t
        root = tk.Tk()
        root.withdraw()
        try:
            intro = {
                "windows": t["ffmpeg_install_win"],
                "mac": t["ffmpeg_install_mac"],
                "linux": t["ffmpeg_install_linux"],
            }[get_platform()]
            messagebox.showinfo(t["ffmpeg_title"], intro, parent=root)

            ok, method = install_ffmpeg()
            if method == "none":
                messagebox.showerror(t["ffmpeg_title"], t["ffmpeg_no_manager"], parent=root)
            elif ok:
                messagebox.showinfo(t["ffmpeg_title"], t["ffmpeg_ok"], parent=root)
                if not check_ffmpeg():
                    messagebox.showwarning(t["ffmpeg_title"], t["ffmpeg_path_hint"], parent=root)
            else:
                messagebox.showerror(t["ffmpeg_title"], t["ffmpeg_manual"], parent=root)
        finally:
            root.destroy()

    def run(self) -> None:
        self.window.mainloop()


def main() -> None:
    App().run()


if __name__ == "__main__":
    main()
