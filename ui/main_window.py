"""
ui/main_window.py
Assembles the app: a toolbar (add / resume / pause / search / settings),
category navigation, one unified download list, a status bar, and two
secondary windows — Add Download and Settings. Also owns the shared app state
(settings, history, the DownloadManager) and the handful of actions the pages
call into (queue_download, save_settings, remove_history_entries, ...).
"""
import datetime
import os
import tkinter as tk
from tkinter import messagebox

from theme import COLORS, FONTS, init_fonts, load_theme
from core import storage, errorlog, app_updater
from core.downloader import (DownloadManager, DownloadJob, FFMPEG_PATH, apply_codec,
                              app_base_dir, fetch_info, build_quality_options,
                              serialize_job, deserialize_job, quality_short_label,
                              apply_default_outputs, job_quality)
from core.bridge_server import BridgeServer
from core.platform_utils import kill_own_process_tree
from version import __version__
from ui.nav import CategoryNav
from ui.pages.downloads_page import DownloadsPage, human_size
from ui.dialogs import AddDownloadDialog, SettingsDialog
from ui.widgets import RoundedButton, IconButton, EntryField, Separator


def _find_icon():
    """
    Look for icon.png in a few likely places:
      1. <app_base_dir>/resources/icon.png  (installed .deb path or dev checkout)
      2. Next to this .py file
      3. Fallback: None (Tk will use its default feather icon)
    """
    candidates = [
        os.path.join(app_base_dir(), "resources", "icon.png"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "resources", "icon.png"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return os.path.normpath(path)
    return None


class MainWindow(tk.Tk):
    def __init__(self, start_hidden=False):
        # Settings must be loaded, and the theme applied, before
        # super().__init__() and any widget construction below — every
        # page does `from theme import COLORS` and reads colors at
        # construction time, so COLORS has to already hold the right
        # palette by then (see theme.py's load_theme() docstring).
        self.settings = storage.load_settings()
        load_theme(self.settings.get("theme", "dark"))

        # className="viddown" sets the X11 WM_CLASS property Linux desktop
        # environments use to match this running window to viddown.desktop
        # (see its StartupWMClass=viddown) and show that entry's declared
        # icon in the taskbar/dock/alt-tab switcher. Without this, Tk
        # defaults WM_CLASS to something generic — the icon files are all
        # there and correct, but nothing points the desktop environment at
        # them for THIS window, so it falls back to a generic placeholder
        # icon instead (this is what the "X" placeholder icon people can
        # see in their taskbar is — not a missing/broken icon file).
        super().__init__(className="viddown")
        errorlog.install_hooks(self)      # crashes / UI-callback errors go to the log
        errorlog.info("app", f"VidDown {__version__} started")
        init_fonts(self)

        self.title("VidDown (SAIMUM HABIB)")
        self.geometry("1180x760")
        self.minsize(940, 620)
        self.configure(bg=COLORS["bg_dark"])

        # ---- window icon (taskbar + title bar) ----
        icon_path = _find_icon()
        if icon_path:
            try:
                from PIL import Image, ImageTk
                img = Image.open(icon_path)
                # Provide multiple sizes so Tk can pick the best one per context
                sizes = [16, 32, 48, 64, 128, 256]
                photos = [ImageTk.PhotoImage(img.resize((s, s), Image.LANCZOS)) for s in sizes]
                self.iconphoto(True, *photos)
                self._icon_refs = photos  # prevent garbage collection
            except Exception:
                pass  # icon is cosmetic — never block on it

        self.history = storage.load_history()
        self.all_jobs = {}   # job.id -> job, tracks every queued/active/paused
                              # job so it can be saved on close and restored
                              # on the next launch (see _save_queue_state /
                              # _restore_queue_state)
        self.download_manager = DownloadManager(
            on_update=self._on_job_update,
            max_concurrent=self.settings.get("max_concurrent_downloads", 1),
            live_settings=self.settings,
        )

        # Local bridge server — allows browser extension to send URLs
        # to this app over 127.0.0.1:47892 (never exposed to the network)
        self._bridge_started = BridgeServer.start(
            on_add=self._on_extension_url,
            on_download=self._on_extension_download,
            prober=self._probe_for_extension,
            on_show=self._show_window,
            duplicate_checker=self._duplicate_flags,
        )

        self._build_toolbar()
        self._build_status_bar()
        self._build_body()
        self._bind_shortcuts()
        self.on_downloads_changed()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        if not self._bridge_started:
            # Most likely cause: port 47892 is already held by another
            # VidDown instance still running in the background. The rest
            # of the app works fine either way, so this is a warning, not
            # a blocking error — but it explains exactly why the browser
            # extension would show "VidDown is not running".
            self.set_status(
                "Browser extension unavailable — port 47892 is already in use "
                "(another VidDown instance may still be running)"
            )

        # restore any jobs that were still queued/downloading/paused when
        # the app was last closed — must run after self.pages exists
        self._restore_queue_state()

        # yt-dlp periodic update check — 60s delay so startup is not affected
        self.after(60_000, self._maybe_update_ytdlp)
        # VidDown's own update check (GitHub releases) — also delayed, and at most twice a day
        self.after(20_000, self._maybe_check_app_update)

        if start_hidden:
            # Autostart launch (see main.py --autostart): the whole point
            # is just getting the bridge server alive at login without an
            # unexpected window popping up. Everything above still runs
            # normally (settings, queue restore, bridge server) — only
            # the window itself stays hidden until _show_window() is
            # called, either from the taskbar/app-menu icon (main.py asks
            # this instance to show itself instead of starting a second
            # one) or if the person wants to check on things.
            self.withdraw()

    def _safe_after(self, callback):
        """self.after(0, callback), but tolerant of the app already being
        closed. Every caller here is a background thread (bridge server
        request thread or DownloadManager worker)
        hopping onto the Tk main thread — if that thread loses the race
        with shutdown, self.after() itself can raise
        RuntimeError("main thread is not in main loop"); there's nothing
        useful left to update at that point anyway."""
        try:
            self.after(0, callback)
        except RuntimeError:
            pass

    def _show_window(self):
        """Bring the window to front. Called from the bridge server's
        /show handler — that runs on a request thread, hence the hop
        back onto the Tk main thread via after()."""
        self._safe_after(lambda: (self.deiconify(), self.lift(), self.focus_force()))

    def _maybe_update_ytdlp(self):
        from core.updater import should_update, update_ytdlp
        from core.storage import save_settings
        if should_update(self.settings):
            update_ytdlp(
                on_done=lambda ok, msg: self._safe_after(
                    lambda: self.set_status(msg)
                ),
                settings=self.settings,
                save_fn=save_settings,
            )

    # ----------------------------------------------------- VidDown's own updates
    def _maybe_check_app_update(self):
        """Automatic check shortly after start. Never for a source checkout, never
        when switched off, and not again within 12 hours of the last check."""
        if not self.settings.get("app_update_autocheck", True):
            return
        if app_updater.install_kind() == app_updater.KIND_SOURCE:
            return
        last = self.settings.get("app_update_last_check") or ""
        try:
            if last and (datetime.datetime.now() - datetime.datetime.fromisoformat(last)
                         < datetime.timedelta(hours=12)):
                return
        except ValueError:
            pass
        self.pages["about"].check_for_updates(silent=True)

    def on_app_update_checked(self, release):
        """About page -> here, on the Tk thread. `release` is the newer release,
        or None when VidDown is up to date: shows or hides the toolbar button."""
        if release is None:
            self._update_btn.pack_forget()
            return
        self._update_btn.configure(text=f"Update to {release.version}")
        if not self._update_btn.winfo_ismapped():
            self._update_btn.pack(side="right", padx=(0, 10))
        self.set_status(f"VidDown {release.version} is available — click Update in the toolbar")

    def quit_for_update(self):
        """Quit cleanly so an installer can replace the program files: running
        downloads are paused and the queue is saved (it comes back paused)."""
        self._do_quit()

    # ================================================================ layout
    def _build_toolbar(self):
        panel = COLORS["bg_panel"]
        bar = tk.Frame(self, bg=panel, height=58)
        bar.pack(side="top", fill="x")
        bar.pack_propagate(False)

        brand = tk.Frame(bar, bg=panel)
        brand.pack(side="left", padx=(18, 20))
        icon_path = _find_icon()
        if icon_path:
            try:
                from PIL import Image, ImageTk
                self._brand_img = ImageTk.PhotoImage(Image.open(icon_path).resize((26, 26), Image.LANCZOS))
                tk.Label(brand, image=self._brand_img, bg=panel, bd=0).pack(side="left", padx=(0, 9))
            except Exception:
                pass
        tk.Label(brand, text="VidDown", font=FONTS["logo"], bg=panel,
                 fg=COLORS["text_primary"]).pack(side="left")

        self.add_btn = RoundedButton(bar, "Add Download", icon="plus", variant="primary", bg=panel,
                                     padx=14, pady=8, command=lambda: self.show_page("download"),
                                     tooltip="Add a download  (Ctrl+N)")
        self.add_btn.pack(side="left", padx=(0, 8))
        self.resume_btn = RoundedButton(bar, "Resume", icon="play", bg=panel, padx=12, pady=8,
                                        command=lambda: self.downloads.resume_selection_or_all(),
                                        tooltip="Resume the selected downloads, or all paused ones")
        self.resume_btn.pack(side="left", padx=(0, 6))
        self.pause_btn = RoundedButton(bar, "Pause", icon="pause", bg=panel, padx=12, pady=8,
                                       command=lambda: self.downloads.pause_selection_or_all(),
                                       tooltip="Pause the selected downloads, or all active ones")
        self.pause_btn.pack(side="left")

        IconButton(bar, "settings", command=lambda: self.show_page("settings"), size=40, icon_size=26, bg=panel,
                   tooltip="Settings  (Ctrl+,)").pack(side="right", padx=(6, 16))
        # Shown only while a newer VidDown release is known (see on_app_update_checked)
        self._update_btn = RoundedButton(bar, "Update available", icon="download", variant="primary",
                                         bg=panel, padx=12, pady=8,
                                         command=lambda: self.show_page("about"),
                                         tooltip="A new VidDown version is available — open Updates")
        self.search_var = tk.StringVar()
        self.search_field = EntryField(bar, self.search_var, "Search downloads", icon="search",
                                       clearable=True, bg=panel, width=270)
        self.search_field.pack(side="right", pady=11)
        self._search_job = None
        self.search_var.trace_add("write", self._on_search_changed)
        Separator(self).pack(side="top", fill="x")

    def _build_body(self):
        body = tk.Frame(self, bg=COLORS["bg_dark"])
        body.pack(side="top", fill="both", expand=True)

        self.nav = CategoryNav(body, on_select=self._on_category_selected, on_terminate=self._on_terminate_app)
        self.nav.pack(side="left", fill="y")
        Separator(body, orient="vertical").pack(side="left", fill="y")

        self.content = tk.Frame(body, bg=COLORS["bg_dark"])
        self.content.pack(side="left", fill="both", expand=True)
        self.downloads = DownloadsPage(self.content, self)
        self.downloads.pack(fill="both", expand=True)

        # Secondary windows. The page objects live inside them but stay
        # reachable by the same names the rest of the app already uses.
        self.add_dialog = AddDownloadDialog(self)
        self.settings_dialog = SettingsDialog(self)
        self.pages = {
            "download": self.add_dialog.page,
            "queue": self.downloads,
            "history": self.downloads,
            **self.settings_dialog.pages,
        }

    def _build_status_bar(self):
        Separator(self).pack(side="bottom", fill="x")
        bar = tk.Frame(self, bg=COLORS["bg_panel"], height=28)
        bar.pack(side="bottom", fill="x")
        bar.pack_propagate(False)

        self.status_var = tk.StringVar(value="Ready")
        tk.Label(bar, textvariable=self.status_var, font=FONTS["small"], bg=COLORS["bg_panel"],
                 fg=COLORS["text_secondary"]).pack(side="left", padx=16)

        self.summary_var = tk.StringVar(value="Idle")
        tk.Label(bar, textvariable=self.summary_var, font=FONTS["small"], bg=COLORS["bg_panel"],
                 fg=COLORS["text_secondary"]).pack(side="right", padx=16)
        if not FFMPEG_PATH:
            tk.Label(bar, text="ffmpeg not found", font=FONTS["small_bold"], bg=COLORS["bg_panel"],
                     fg=COLORS["warning"]).pack(side="right", padx=(0, 12))
        self._summary_job = None

    # ============================================================ navigation
    def show_page(self, key):
        """Old page names keep working: "download" opens the Add Download
        window, "queue"/"history" pick a list category, and the rest open
        the matching Settings section."""
        if key == "download":
            self.add_dialog.show()
        elif key == "queue":
            self.nav.select("all")
        elif key == "history":
            self.nav.select("completed")
        elif key in self.settings_dialog.pages:
            self.settings_dialog.show(key)

    def _on_category_selected(self, key):
        self.downloads.set_category(key)

    def _on_search_changed(self, *_):
        if self._search_job is not None:
            self.after_cancel(self._search_job)
        self._search_job = self.after(150, lambda: self.downloads.set_search(self.search_var.get()))

    def on_downloads_changed(self):
        """List or categories changed: refresh counts, toolbar state, summary."""
        if getattr(self, "downloads", None) is None:
            return                       # still being constructed
        counts = self.downloads.counts()
        self.nav.set_counts(counts)
        self.pause_btn.configure(state="normal" if (counts["downloading"] or counts["queued"]) else "disabled")
        self.resume_btn.configure(state="normal" if counts["paused"] else "disabled")
        self._update_summary()

    def schedule_summary_update(self):
        if self._summary_job is None:
            self._summary_job = self.after(400, self._update_summary)

    def _update_summary(self):
        self._summary_job = None
        jobs = list(self.all_jobs.values())
        active = [j for j in jobs if j.status == "downloading"]
        queued = [j for j in jobs if j.status == "queued"]
        speed = sum(getattr(j, "speed_bps", 0.0) for j in active if j.speed)
        parts = []
        if active:
            parts.append(f"{len(active)} active")
        if queued:
            parts.append(f"{len(queued)} queued")
        if speed:
            parts.append(f"\u2193 {human_size(speed)}/s")
        self.summary_var.set("  \u00b7  ".join(parts) if parts else "Idle")

    def _bind_shortcuts(self):
        self.bind_all("<Control-n>", lambda _e: self.show_page("download"))
        self.bind_all("<Control-comma>", lambda _e: self.show_page("settings"))
        self.bind_all("<Control-f>", self._focus_search)
        self.bind_all("<Delete>", self._key_delete)
        self.bind_all("<Control-a>", self._key_select_all)
        self.bind_all("<Escape>", self._key_escape)

    def _focus_in_text_field(self):
        return isinstance(self.focus_get(), (tk.Entry, tk.Text))

    def _main_has_focus(self):
        focus = self.focus_get()
        return focus is not None and focus.winfo_toplevel() is self

    def _focus_search(self, _e=None):
        if self._main_has_focus():
            self.search_field.entry.focus_set()
            self.search_field.entry.select_range(0, "end")
            return "break"

    def _key_delete(self, _e=None):
        if self._main_has_focus() and not self._focus_in_text_field():
            self.downloads.remove_selection()

    def _key_select_all(self, _e=None):
        if self._main_has_focus() and not self._focus_in_text_field():
            self.downloads.select_all()
            return "break"

    def _key_escape(self, _e=None):
        if not self._main_has_focus():
            return
        if self.search_var.get():
            self.search_var.set("")
        else:
            self.downloads.clear_selection()
        self.downloads.focus_list()

    def remove_history_entries(self, entries):
        """Takes completed entries off the list (never touches the files)."""
        drop = {id(e) for e in entries}
        self.history = [e for e in self.history if id(e) not in drop]
        storage.save_history(self.history)
        self.downloads.refresh()

    def set_status(self, text):
        self.status_var.set(text)

    # ======================================================= shared actions
    def save_settings(self):
        """Returns True if the settings file was written, False if not."""
        ok = storage.save_settings(self.settings)
        self.download_manager.set_max_concurrent(
            self.settings.get("max_concurrent_downloads", 1)
        )
        return ok

    def add_history_entry(self, entry):
        self.history = storage.add_history_entry(entry)
        self.pages["history"].refresh()

    def clear_history(self):
        self.history = storage.clear_history()
        self.pages["history"].refresh()

    def find_duplicate_title(self, expected_title):
        """Checks whether a job with this exact title (which already
        includes the quality tag, e.g. 'Some Video [1080p]') is already
        queued/downloading/paused, or sits in the download history.
        Returns 'queue', 'history', or None. Shared by the Download page
        and the browser-extension flow (which calls this from the bridge
        server's background thread — hence the list() snapshot below,
        since all_jobs is mutated in place on the main thread and a bare
        `for job in self.all_jobs.values()` could raise "dictionary
        changed size during iteration" if that happens mid-loop)."""
        for job in list(self.all_jobs.values()):
            if job.title == expected_title:
                return "queue"
        for entry in self.history:
            if entry.get("title") == expected_title:
                return "history"
        return None

    def queue_downloads(self, jobs, start=True):
        """Queue many jobs at once (a whole playlist). Everything that used to
        run once PER job - re-laying out the whole list, rewriting and fsync-ing
        queue_state.json - now runs once for the batch. Queueing 200 playlist
        videos one by one froze the window for a long time (the Download
        button looked dead), because each job redid that work for all the
        jobs before it."""
        jobs = list(jobs)
        if not jobs:
            return
        for job in jobs:
            self.all_jobs[job.id] = job
        self.pages["queue"].add_job_rows(jobs)
        if start:
            for job in jobs:
                self.download_manager.add_job(job)
        self._save_queue_state()

    def queue_download(self, job, start=True):
        """start=False creates the Queue-page row and tracks the job in
        all_jobs (so it can still be saved/restored, paused/resumed, etc.)
        without handing it to the worker pool — used when restoring a job
        that was paused when the app last closed, so it comes back paused
        instead of immediately downloading again."""
        self.all_jobs[job.id] = job
        if start:
            self.download_manager.add_job(job)
        self.pages["queue"].add_job_row(job)
        self._save_queue_state()

    def _on_job_update(self, job):
        # This runs on the DownloadManager's background thread -> hop back
        # onto the Tk main thread before touching any widgets.
        self._safe_after(lambda: self._handle_job_update_ui(job))

    def _handle_job_update_ui(self, job):
        self.pages["queue"].update_job_row(job)
        BridgeServer.update_job_status(job)   # let the browser panel poll this
        # job.status is read NOW, not when the update was queued, so several
        # queued updates can all see "completed". Only the first one may add
        # the history entry / do the terminal bookkeeping; a terminal job that
        # is already out of all_jobs has been handled.
        if job.status in ("completed", "error", "cancelled") and job.id not in self.all_jobs:
            return
        if job.status == "error":
            errorlog.error("download", f"{job.title} — {job.error or 'unknown error'}\nurl: {job.url}")
        if job.status == "completed":
            self.set_status(f"Completed: {job.title}")
            self.add_history_entry({
                "title": job.title,
                "filepath": job.filepath,
                "filesize": job.filesize,
                "url": job.url,
                "finished_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            })
        elif job.status == "error":
            self.set_status(f"Error: {job.title} — {job.error}")
        elif job.status == "downloading":
            self.set_status(f"Downloading: {job.title} ({job.progress:.0f}%)")

        if job.status in ("completed", "error", "cancelled"):
            # terminal state — no longer needs to be tracked for restore-on-close
            self.all_jobs.pop(job.id, None)

        # Persist to disk right now, not just on a clean app close.
        # queue_state.json used to only get rewritten in _on_close — so a
        # crash, force-kill, or any exit that skips that handler left
        # whatever was last saved sitting on disk, regardless of what
        # actually happened during the session. A job removed from
        # all_jobs (completed/error/cancelled) or changed to "paused"
        # would still be sitting in that stale file, and come back on
        # the next launch. Skip the frequent per-tick "downloading"
        # progress updates though — persisting those doesn't matter
        # (a resumed job just starts at status="queued" and yt-dlp picks
        # up the partial file anyway) and would mean rewriting this file
        # several times a second while something is actively downloading.
        if job.status != "downloading":
            self._save_queue_state()

    # ============================================ browser extension callbacks
    def _on_extension_url(self, url, title):
        """Called from bridge server thread -> hop to Tk main thread."""
        self._safe_after(lambda: self._handle_extension_url(url, title))

    def _handle_extension_url(self, url, title):
        """Bring app to front and start fetching the URL (single fetch only)."""
        self.deiconify()
        self.lift()
        self.focus_force()
        self.show_page("download")
        dl_page = self.pages["download"]
        dl_page.url_var.set(url)
        # Cancel the debounce triggered by url_var.set — we call _on_fetch directly
        if dl_page._debounce_id is not None:
            dl_page.after_cancel(dl_page._debounce_id)
            dl_page._debounce_id = None
        dl_page._on_fetch()
        self.set_status(f"Browser extension: {title or url}")

    def _probe_for_extension(self, url, headers=None):
        """Runs on bridge server thread — no UI, no after() needed.
        Forces noplaylist so a playlist URL only probes the first video.
        `headers` (Referer/Origin) is only set for links the extension
        sniffed from the page's network traffic."""
        # merge settings with noplaylist so we never block on a full playlist fetch
        probe_settings = dict(self.settings)
        probe_settings["_force_noplaylist"] = True
        if headers:
            probe_settings["_http_headers"] = dict(headers)
        info    = fetch_info(url, probe_settings)
        options = build_quality_options(info)
        # The browser panel has no pickers: Settings' audio format / container apply
        # (so its list already says "Audio Only (Opus)" and shows the matching size).
        apply_default_outputs(options, self.settings)

        # Tag each quality with whether that exact (title + quality)
        # combo is already queued/downloading or sits in history — the
        # browser panel uses this to warn before starting a duplicate,
        # same as the Download page does for app-initiated downloads.
        for opt, flag in zip(options, self._duplicate_flags(info, options)):
            opt["is_duplicate"] = flag

        return info, options

    def _duplicate_flags(self, info, options):
        """One bool per quality option: is that exact (title + quality) combo
        already queued/downloading or in history? Also used by the bridge to
        refresh cached probe results."""
        base_title = info.get("title") or ""
        flags = []
        for opt in options:
            tag = quality_short_label(opt)
            expected_title = f"{base_title} [{tag}]" if tag else base_title
            flags.append(self.find_duplicate_title(expected_title) is not None)
        return flags

    def _on_extension_download(self, url, title, quality_option, headers=None):
        """
        Quality picked in browser panel -> queue download.
        Runs on the bridge server's request thread. DownloadJob() itself
        never touches Tk, so it's created right here — that lets us return
        its id synchronously to the browser panel (for /job_status
        polling). Only the actual queueing + UI update is marshaled onto
        the main thread via after().
        """
        out_dir = self.settings.get("output_dir", "")
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)  # thread-safe; caller (bridge_server) catches errors

        # The browser panel has no codec picker, so Settings' default applies there.
        if self.settings.get("default_codec") == "h264":
            quality_option = apply_codec(quality_option, "H.264")

        settings_snapshot = dict(self.settings)
        if headers:
            settings_snapshot["_http_headers"] = dict(headers)
        options = {
            "subtitles":       [],
            "custom_filename": "",
            "trim":            None,
            "embed_thumbnail": bool(self.settings.get("embed_thumbnail", False)),
            "embed_metadata":  bool(self.settings.get("embed_metadata", False)),
        }
        job = DownloadJob(url, title, job_quality(quality_option), settings_snapshot, **options)
        # push status BEFORE returning job_id — closes the race window
        # where the browser panel could poll /job_status before this
        # job exists there at all
        BridgeServer.update_job_status(job)
        self._safe_after(lambda: self._finish_extension_download(job))
        return job.id

    def _finish_extension_download(self, job):
        # Deliberately NOT calling deiconify/lift/focus_force here — the
        # whole point of the extension is downloading without leaving the
        # browser. Stealing focus on every single download (as this used
        # to do) would yank the person out of whatever they were watching
        # every time they picked a quality. The extension's own progress
        # popup already shows them it worked; the app window doesn't need
        # to interrupt them for it.
        self.queue_download(job)
        self.set_status(f"Browser download queued: {job.title}")

    def _on_close(self):
        if self.settings.get("keep_running_on_close", False):
            # Hide the window instead of quitting — the bridge server and any
            # active downloads keep running, only the window goes away.
            # Reopening the app from its icon (main.py's /show wake-up) is the
            # way back in.
            self.withdraw()
            return
        # Otherwise quit fully; hiding without this setting would leave a
        # window nobody can get back to.
        self._do_quit()

    def _do_quit(self):
        # Stop running downloads — and the aria2c processes yt-dlp started
        # for them — before the window goes away. Without this a quit left
        # aria2c running in the background, still writing to the output
        # folder, and a later Resume could collide with it.
        try:
            self.download_manager.shutdown()
        except Exception:
            pass
        try:
            self.pages["settings"].flush_pending()   # a typed change still waiting to apply
        except Exception:
            pass
        BridgeServer.stop()
        self.save_settings()
        self._save_queue_state(now=True)
        self.destroy()

    def _on_terminate_app(self):
        """The navigation footer's emergency "Terminate" button — a stronger,
        unconditional version of Quit for when something's stuck and the
        person just wants everything gone right now, no assumptions about
        a graceful shutdown actually completing."""
        if not messagebox.askyesno(
            "Terminate VidDown?",
            "This immediately kills VidDown completely — the app window, "
            "the background bridge server the browser extension talks to, "
            "and any in-progress downloads.\n\n"
            "This can't be undone, and any download running right now "
            "will be left incomplete.\n\nContinue?",
            icon="warning",
        ):
            return
        self._force_terminate()

    def _force_terminate(self):
        # Best-effort graceful bits first — each wrapped separately so one
        # failing doesn't stop the rest, and none of them can block the
        # unconditional kill below for long even if something's wrong.
        # PAUSE the running jobs (what a normal quit does), never cancel them:
        # a cancelled job's worker deletes its partial files while the queue
        # snapshot below still lists the job as resumable, so "Resume" after
        # the next start began from zero.
        # (Plain flag setters on purpose: this button exists for a hung app, so
        # nothing here may wait on the manager's lock.)
        try:
            for job in list(self.all_jobs.values()):
                job.pause()
        except Exception:
            pass
        try:
            BridgeServer.stop()
        except Exception:
            pass
        try:
            self.save_settings()
            self._save_queue_state(now=True)
        except Exception:
            pass

        # The actual "kill everything" step. killpg targets this
        # process's whole process group — for VidDown launched normally
        # (its .desktop entry, an app launcher, double-clicking the
        # icon), that covers any ffmpeg/aria2c child process yt-dlp may
        # have started too, not just this Python process itself. (If
        # VidDown was instead launched directly from an interactive
        # terminal that didn't give it its own process group, this can
        # also end that terminal session — an accepted trade-off for
        # what this specific button is for.) os._exit() right after is
        # the unconditional fallback either way: unlike self.destroy() /
        # sys.exit(), it can't hang waiting on Tk or a thread to unwind
        # cleanly — which is exactly the assumption this button exists
        # to not have to rely on.
        # (On Windows there are no process groups; platform_utils uses
        # `taskkill /T` to end this process and its ffmpeg/aria2c children.)
        kill_own_process_tree()
        os._exit(1)

    _save_pending = False

    def _save_queue_state(self, now=False):
        """Persist the queue. Normal calls are coalesced: however many jobs
        change within ~0.4 s, queue_state.json is rewritten once (each rewrite
        serialises every job and fsyncs, so doing it per job made a big queue
        crawl). now=True writes immediately - used on quit/terminate."""
        if now:
            self._save_pending = False
            self._write_queue_state()
            return
        if self._save_pending:
            return
        self._save_pending = True
        try:
            self.after(400, self._flush_queue_save)
        except Exception:
            self._save_pending = False
            self._write_queue_state()

    def _flush_queue_save(self):
        if self._save_pending:
            self._save_pending = False
            self._write_queue_state()

    def _write_queue_state(self):
        """Snapshot every job still queued/downloading/paused so closing
        the app mid-download doesn't lose it. Completed/error/cancelled
        jobs are already gone from all_jobs by this point (see
        _handle_job_update_ui), so no extra filtering is needed here."""
        try:
            data = [serialize_job(job) for job in self.all_jobs.values()]
            storage.save_queue_state(data)
        except Exception:
            pass  # never block shutdown over a persistence hiccup

    def _restore_queue_state(self):
        """Re-queues jobs saved by _save_queue_state on the previous run.
        Every restored job comes back paused — regardless of whether it
        was 'queued', 'downloading', or already 'paused' when the app
        last closed — so nothing starts downloading on its own the moment
        the app opens. Queue shows each one with a Resume button; the
        person decides what continues. yt-dlp will resume from any
        partial .part file it finds on disk once resumed, so that
        doesn't necessarily mean starting the download over from 0%."""
        saved = storage.load_queue_state()
        if not saved:
            return
        storage.clear_queue_state()  # consume once — avoid re-restoring on a crash loop

        restored_jobs = []
        for data in saved:
            try:
                job = deserialize_job(data)
            except Exception:
                continue  # skip a corrupt/incompatible entry rather than crashing startup
            job.status = "paused"
            restored_jobs.append(job)
        restored = len(restored_jobs)
        self.queue_downloads(restored_jobs, start=False)

        if restored:
            self.set_status(
                f"Restored {restored} download{'s' if restored != 1 else ''} from last "
                f"session — paused, resume from the Queue page"
            )
