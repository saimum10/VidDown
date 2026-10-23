"""
ui/pages/settings_page.py
Lets the user override output directory, proxy, speed limit, ffmpeg
path, and whether aria2c is used for faster downloads. (Cookies have
their own dedicated page now — see ui/pages/cookies_page.py.)
Everything is persisted via core.storage as soon as "Save" is pressed.
"""
import os
import subprocess
import sys
import tkinter as tk
from tkinter import filedialog, messagebox

from theme import COLORS, FONTS
from core import storage
from core import output_options as oo
from core import autostart
from core.platform_utils import IS_WINDOWS
from core.downloader import app_base_dir, _parse_rate_limit
from core.bridge_server import BridgeServer
from ui.scrollable import ScrollableFrame
from ui.widgets import (SettingsGroup, RoundedButton, EntryField, SegmentedControl,
                        Stepper, ToggleSwitch)


class SettingsPage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=COLORS["bg_dark"])
        self.app = app

        self._apply_job = None   # pending debounced auto-apply of the text fields

        scroll = ScrollableFrame(self, bg=COLORS["bg_dark"])
        scroll.pack(fill="both", expand=True)
        page = scroll.body
        card = COLORS["bg_card"]

        tk.Label(page, text="General", font=FONTS["page_title"], bg=COLORS["bg_dark"],
                 fg=COLORS["text_primary"], anchor="w").pack(fill="x", padx=24, pady=(18, 14))

        s = self.app.settings

        def group(title=None, footer=None):
            g = SettingsGroup(page, title, footer)
            g.pack(fill="x", padx=24, pady=(0, 18))
            return g

        # ── Appearance ──────────────────────────────────────────────────────
        g = group("Appearance")
        self.theme_var = tk.StringVar(value=s.get("theme", "dark"))
        g.row("Theme", "Takes effect after restarting VidDown.",
              lambda p: SegmentedControl(p, [("dark", "Dark"), ("light", "Light")],
                                         self.theme_var, bg=card))

        # ── Downloads ───────────────────────────────────────────────────────
        g = group("Downloads")
        self.output_dir_var = tk.StringVar(value=s.get("output_dir", storage.DEFAULT_OUTPUT_DIR))
        g.row("Output folder", "Where finished downloads are saved.",
              lambda p: self._field_control(p, self.output_dir_var, "Folder for downloaded files", browse="dir"),
              stacked=True)

        self.max_concurrent_var = tk.IntVar(value=s.get("max_concurrent_downloads", 1))
        g.row("Simultaneous downloads", "Extra videos wait in the queue until a slot is free.",
              lambda p: Stepper(p, self.max_concurrent_var, 1, 10, bg=card))

        self.speed_var = tk.StringVar(value=s.get("speed_limit", ""))
        g.row("Speed limit", "Optional. Leave empty for no limit.",
              lambda p: self._field_control(p, self.speed_var, "e.g. 500K (KB/s) or 2M (MB/s)"),
              stacked=True)

        self.aria2c_var = tk.BooleanVar(value=s.get("aria2c_enabled", True))
        g.row("Use aria2c", "Splits each download into several connections for higher speed.",
              lambda p: ToggleSwitch(p, self.aria2c_var, bg=card,
                                     command=lambda: self._autosave_toggle("aria2c_enabled", self.aria2c_var)))

        self.codec_var = tk.StringVar(value=s.get("default_codec", "auto"))
        g.row("Video codec", "Auto picks the smallest file. H.264 plays on almost every device "
                             "but makes larger files. You can still choose per video when adding it.",
              lambda p: SegmentedControl(p, [("auto", "Auto"), ("h264", "H.264")],
                                         self.codec_var, bg=card))

        # ── Output format ───────────────────────────────────────────────────
        g = group("Output format",
                  "Defaults for new downloads, including ones started from the browser extension. "
                  "You can still change them for each video on the Download page.")
        self.container_var = tk.StringVar(value=oo.normalize_container(s.get("default_container")))
        g.row("Video container", "MP4 plays almost everywhere. MKV holds any codec and several subtitle "
                                 "tracks. WebM, MOV and AVI only fit some codecs, so they are offered "
                                 "when the video has one.",
              lambda p: SegmentedControl(p, [(k, oo.CONTAINERS[k]["label"]) for k in oo.CONTAINER_ORDER],
                                         self.container_var, bg=card))
        self.afmt_var = tk.StringVar(value=oo.normalize_audio_format(s.get("default_audio_format")))
        g.row("Audio format", "Original, M4A and Opus keep the site's own audio without re-encoding "
                              "where possible. MP3 is converted.",
              lambda p: SegmentedControl(p, [(k, oo.AUDIO_FORMATS[k]["label"]) for k in oo.AUDIO_FORMAT_ORDER],
                                         self.afmt_var, bg=card))
        self.abitrate_var = tk.StringVar(value=str(oo.normalize_audio_bitrate(s.get("default_audio_bitrate"))))
        g.row("MP3 bitrate", "Only used for MP3. Higher is larger and closer to the original.",
              lambda p: SegmentedControl(p, [(str(b), f"{b} kbps") for b in oo.MP3_BITRATES],
                                         self.abitrate_var, bg=card))
        self.embed_thumb_var = tk.BooleanVar(value=bool(s.get("embed_thumbnail", False)))
        g.row("Embed thumbnail", "Adds the video's picture as the file's cover (not possible in WebM, "
                                 "AVI or WAV).",
              lambda p: ToggleSwitch(p, self.embed_thumb_var, bg=card,
                                     command=lambda: self._autosave_toggle("embed_thumbnail",
                                                                           self.embed_thumb_var)))
        self.embed_meta_var = tk.BooleanVar(value=bool(s.get("embed_metadata", False)))
        g.row("Embed metadata", "Writes title, uploader, date, description and chapters into the file.",
              lambda p: ToggleSwitch(p, self.embed_meta_var, bg=card,
                                     command=lambda: self._autosave_toggle("embed_metadata",
                                                                           self.embed_meta_var)))

        # ── Network and tools ───────────────────────────────────────────────
        g = group("Network and tools")
        self.proxy_var = tk.StringVar(value=s.get("proxy", ""))
        g.row("Proxy", "Optional. HTTP or SOCKS5; add user:password@ if it needs a login.",
              lambda p: self._field_control(p, self.proxy_var, "e.g. http://host:port or socks5://host:port"),
              stacked=True)

        self.ffmpeg_var = tk.StringVar(value=s.get("ffmpeg_path", ""))
        g.row("FFmpeg location", "Leave empty to use the ffmpeg bundled with VidDown.",
              lambda p: self._field_control(p, self.ffmpeg_var,
                                  "e.g. C:\\ffmpeg\\bin\\ffmpeg.exe" if IS_WINDOWS else "e.g. /usr/bin/ffmpeg",
                                  browse="file"),
              stacked=True)

        # ── Browser extension ───────────────────────────────────────────────
        g = group("Browser extension",
                  "Adds a download button on videos in Chrome, Brave or Edge. To install: open "
                  "chrome://extensions, turn on Developer mode, choose Load unpacked and select "
                  "the extension folder.")
        status = g.custom()
        self._ext_status_dot = tk.Label(status, text="\u25cf", font=FONTS["body"], bg=card)
        self._ext_status_dot.pack(side="left")
        self._ext_status_label = tk.Label(status, text="", font=FONTS["body"], bg=card,
                                          fg=COLORS["text_secondary"])
        self._ext_status_label.pack(side="left", padx=(8, 0))

        # Ground truth is the .desktop file itself, not settings.json, so this
        # always reflects reality even if it was changed by hand elsewhere.
        self.autostart_var = tk.BooleanVar(value=autostart.is_enabled())
        g.row("Start on boot", "Automatically turn on the download server after every restart.",
              lambda p: ToggleSwitch(p, self.autostart_var, bg=card, command=self._autosave_autostart))

        # Plain window withdraw(): read at close time, so no restart is needed.
        self.keep_running_var = tk.BooleanVar(value=s.get("keep_running_on_close", False))
        g.row("Keep running when the window is closed",
              "The browser extension keeps working while the window is hidden.",
              lambda p: ToggleSwitch(p, self.keep_running_var, bg=card,
                                     command=lambda: self._autosave_toggle("keep_running_on_close",
                                                                           self.keep_running_var)))
        g.row("Extension files", "The folder to load in your browser.",
              lambda p: RoundedButton(p, "Open folder", command=self._open_extension_folder,
                                      padx=14, pady=7, bg=card))

        # ── Reset ───────────────────────────────────────────────────────────
        g = group("Reset")
        g.row("Reset all settings", "Restores every setting to its default value.",
              lambda p: RoundedButton(p, "Reset to default", command=self._reset, variant="danger",
                                      padx=14, pady=7, bg=card))

        # No Save button: everything applies by itself. The theme and the
        # simultaneous-downloads stepper act at once; text fields apply shortly
        # after typing stops (see _schedule_apply), so half-typed values are
        # not saved.
        self.theme_var.trace_add("write", lambda *_: self._apply_fields())
        self.codec_var.trace_add("write", lambda *_: self._apply_fields())
        for var in (self.container_var, self.afmt_var, self.abitrate_var):
            var.trace_add("write", lambda *_: self._apply_fields())
        self.max_concurrent_var.trace_add("write", lambda *_: self._apply_fields())
        for var in (self.output_dir_var, self.proxy_var, self.speed_var, self.ffmpeg_var):
            var.trace_add("write", self._schedule_apply)

        self._refresh_extension_status()

    def _field_control(self, parent, var, placeholder, browse=None):
        """A text field (plus an optional Browse button) for use inside a row."""
        card = COLORS["bg_card"]
        wrap = tk.Frame(parent, bg=card)
        field = EntryField(wrap, var, placeholder, bg=card)
        field.pack(side="left", fill="x", expand=True)
        field.entry.bind("<FocusOut>", lambda _e: self.flush_pending(), add="+")
        field.entry.bind("<Return>", lambda _e: self.flush_pending(), add="+")
        if browse in ("dir", "file"):
            pick = self._browse_dir if browse == "dir" else self._browse_file
            RoundedButton(wrap, "Browse", command=lambda: pick(var), padx=12, pady=7,
                          bg=card).pack(side="left", padx=(8, 0))
        return wrap

    def _browse_dir(self, var):
        chosen = filedialog.askdirectory()
        if chosen:
            var.set(chosen)

    def _browse_file(self, var):
        chosen = filedialog.askopenfilename()
        if chosen:
            var.set(chosen)

    # ── auto-apply ────────────────────────────────────────────────────────
    # Messages go to the main window's status bar, which is always visible.
    _APPLY_DELAY_MS = 700

    def _schedule_apply(self, *_):
        """Text fields: apply a moment after typing stops."""
        if self._apply_job is not None:
            self.after_cancel(self._apply_job)
        self._apply_job = self.after(self._APPLY_DELAY_MS, self._apply_fields)

    def flush_pending(self):
        """Apply right now if a typed change is still waiting (focus-out,
        Enter, or the app closing)."""
        if self._apply_job is not None:
            self._apply_fields()

    def _apply_fields(self):
        if self._apply_job is not None:
            try:
                self.after_cancel(self._apply_job)
            except Exception:
                pass
            self._apply_job = None
        theme_changed = self.theme_var.get() != self.app.settings.get("theme", "dark")
        speed = self.speed_var.get().strip()
        self.app.settings.update({
            "theme": self.theme_var.get(),
            "output_dir": self.output_dir_var.get().strip() or storage.DEFAULT_OUTPUT_DIR,
            "proxy": self.proxy_var.get().strip(),
            "speed_limit": speed,
            "ffmpeg_path": self.ffmpeg_var.get().strip(),
            "max_concurrent_downloads": self.max_concurrent_var.get(),
            "default_codec": self.codec_var.get(),
            "default_container": oo.normalize_container(self.container_var.get()),
            "default_audio_format": oo.normalize_audio_format(self.afmt_var.get()),
            "default_audio_bitrate": oo.normalize_audio_bitrate(self.abitrate_var.get()),
        })
        if not self.app.save_settings():
            self.app.set_status("Settings could not be written to disk — applies for this session only")
        elif speed and _parse_rate_limit(speed) is None:
            self.app.set_status("Speed limit not understood (use e.g. 500K or 2M) — no limit applied")
        elif theme_changed:
            self.app.set_status("Settings saved — restart VidDown to see the new theme")
        else:
            self.app.set_status("Settings saved")

    def _autosave_toggle(self, key, var):
        self.app.settings[key] = bool(var.get())
        if self.app.save_settings():
            self.app.set_status("Settings saved")
        else:
            self.app.set_status("Settings could not be written to disk — applies for this session only")

    def _autosave_autostart(self):
        wanted = self.autostart_var.get()
        ok, err = autostart.enable() if wanted else autostart.disable()
        if ok:
            self.app.set_status("Auto start " + ("enabled" if wanted else "disabled"))
        else:
            self.autostart_var.set(autostart.is_enabled())   # show the real state
            messagebox.showwarning("Auto start", f"Auto start could not be changed:\n{err}")

    def _reset(self):
        if not messagebox.askyesno("Confirm", "Reset all settings to default?"):
            return
        d = storage.DEFAULT_SETTINGS
        self.theme_var.set(d["theme"])
        self.output_dir_var.set(d["output_dir"])
        self.proxy_var.set(d["proxy"])
        self.speed_var.set(d["speed_limit"])
        self.ffmpeg_var.set(d["ffmpeg_path"])
        self.aria2c_var.set(d["aria2c_enabled"])
        self.codec_var.set(d["default_codec"])
        self.container_var.set(d["default_container"])
        self.afmt_var.set(d["default_audio_format"])
        self.abitrate_var.set(str(d["default_audio_bitrate"]))
        self.embed_thumb_var.set(d["embed_thumbnail"])
        self.embed_meta_var.set(d["embed_metadata"])
        self.max_concurrent_var.set(d["max_concurrent_downloads"])
        self.keep_running_var.set(False)
        self.autostart_var.set(False)
        self.app.settings.update({
            "aria2c_enabled": d["aria2c_enabled"],
            "embed_thumbnail": d["embed_thumbnail"],
            "embed_metadata": d["embed_metadata"],
            "keep_running_on_close": False,
            "ytdlp_autoupdate": d["ytdlp_autoupdate"],
            "ytdlp_update_interval": d["ytdlp_update_interval"],
        })
        self._apply_fields()                      # writes everything above
        if autostart.is_enabled():
            autostart.disable()
        about = getattr(self.app, "pages", {}).get("about")   # its two vars live there
        if about is not None:
            about.autoupdate_var.set(d["ytdlp_autoupdate"])
            about.interval_var.set("1 day" if d["ytdlp_update_interval"] == 1 else "1 week")
        self.app.set_status("Settings reset to default")

    def _extension_dir(self):
        """Locate the unpacked browser extension in source and PyInstaller builds.

        PyInstaller 6 onedir stores --add-data resources under _internal,
        while older builds and source checkouts keep them beside the launcher.
        Return the first existing extension directory; keep the normal path as
        a useful fallback for the error message if the bundle is incomplete.
        """
        app_dir = app_base_dir()
        candidates = []
        meipass = getattr(sys, "_MEIPASS", "")
        executable_dir = os.path.dirname(os.path.abspath(sys.executable))

        if meipass:
            candidates.append(os.path.join(meipass, "browser_extension"))
        candidates.extend([
            os.path.join(app_dir, "browser_extension"),
            os.path.join(executable_dir, "_internal", "browser_extension"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "browser_extension"),
        ])

        seen = set()
        for candidate in candidates:
            candidate = os.path.abspath(candidate)
            if candidate in seen:
                continue
            seen.add(candidate)
            if os.path.isdir(candidate) and os.path.isfile(os.path.join(candidate, "manifest.json")):
                return candidate

        # Return a deterministic path so the UI can report where it looked.
        return os.path.abspath(candidates[0] if candidates else os.path.join(app_dir, "browser_extension"))

    def _refresh_extension_status(self):
        running = BridgeServer.is_running()
        if running:
            self._ext_status_dot.configure(fg=COLORS["success"])
            self._ext_status_label.configure(
                text="Connected — the extension can reach this app right now."
            )
        else:
            self._ext_status_dot.configure(fg=COLORS["danger"])
            self._ext_status_label.configure(
                text="Not available — port 47892 could not be opened "
                     "(another VidDown instance may be running)."
            )

    def _open_extension_folder(self):
        path = self._extension_dir()
        if not os.path.isdir(path):
            self.app.set_status(f"Extension folder not found: {path}")
            return
        try:
            if sys.platform.startswith("linux"):
                subprocess.Popen(["xdg-open", path])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                os.startfile(path)  # Windows
        except Exception as exc:
            self.app.set_status(f"Couldn't open folder automatically — path: {path} ({exc})")

    def on_show(self):
        self._refresh_extension_status()
