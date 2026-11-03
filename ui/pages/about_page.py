"""
ui/pages/about_page.py
Developer credit, GitHub link, and version info.
"""
import os
import webbrowser
import re
import threading
import tkinter as tk
from tkinter import messagebox

from theme import COLORS, FONTS
import datetime

from core import app_updater, buildinfo, ytdlp_loader
from core.storage import save_settings
from core.updater import (update_ytdlp, get_ytdlp_version, get_tool_status,
                          get_js_runtime_status, open_apt_upgrade_terminal)
from core import errorlog
from core.platform_utils import IS_WINDOWS
from ui.log_viewer import LogViewer, save_log_dialog
from ui.widgets import (SettingsGroup, RoundedButton, SegmentedControl, ToggleSwitch,
                        SlimProgress)
from ui.scrollable import ScrollableFrame
from core.downloader import app_base_dir, human_size
from version import __version__

GITHUB_USERNAME = "saimum10"
GITHUB_URL      = f"https://github.com/{GITHUB_USERNAME}"


def _find_logo_path():
    """Same resources/icon.png lookup main_window.py's window/taskbar icon
    uses (duplicated rather than imported from there — importing from
    ui.main_window here would be a circular import, since main_window.py
    is what imports AboutPage in the first place)."""
    candidates = [
        os.path.join(app_base_dir(), "resources", "icon.png"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "resources", "icon.png"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return os.path.normpath(path)
    return None


class AboutPage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=COLORS["bg_dark"])
        self.app = app

        scroll = ScrollableFrame(self, bg=COLORS["bg_dark"])
        scroll.pack(fill="both", expand=True)
        body = scroll.body

        tk.Label(body, text="Updates & About", font=FONTS["page_title"],
                 bg=COLORS["bg_dark"], fg=COLORS["text_primary"], anchor="w",
                 ).pack(fill="x", padx=24, pady=(18, 14))

        # ── identity: logo on the left, details beside it (flat, no box) ──
        card = tk.Frame(body, bg=COLORS["bg_dark"])
        card.pack(fill="x", padx=24, pady=(0, 18))
        logo_path = _find_logo_path()
        if logo_path:
            try:
                from PIL import Image, ImageTk
                img = Image.open(logo_path).resize((72, 72), Image.LANCZOS)
                self._logo_photo = ImageTk.PhotoImage(img)  # keep a reference
                tk.Label(card, image=self._logo_photo, bg=COLORS["bg_dark"]).pack(side="left", padx=(0, 16))
            except Exception:
                pass
        info = tk.Frame(card, bg=COLORS["bg_dark"])
        info.pack(side="left", fill="x", expand=True)
        tk.Label(info, text=f"VidDown {__version__}", font=FONTS["section"], bg=COLORS["bg_dark"],
                 fg=COLORS["text_primary"], anchor="w").pack(fill="x")
        tk.Label(info, text="Smart & self-contained video downloader", font=FONTS["small"],
                 bg=COLORS["bg_dark"], fg=COLORS["text_muted"], anchor="w").pack(fill="x", pady=(1, 6))
        tk.Label(info, text="Developer: SAIMUM HABIB  \u00b7  License: MIT", font=FONTS["small"],
                 bg=COLORS["bg_dark"], fg=COLORS["text_secondary"], anchor="w").pack(fill="x")
        links = tk.Frame(info, bg=COLORS["bg_dark"])
        links.pack(anchor="w", pady=(10, 0))
        RoundedButton(links, "GitHub profile", command=lambda: webbrowser.open(GITHUB_URL),
                      padx=12, pady=6).pack(side="left")
        RoundedButton(links, "Licenses", command=self._open_licenses,
                      padx=12, pady=6).pack(side="left", padx=(8, 0))

        cbg = COLORS["bg_card"]
        sett = self.app.settings

        def group(title=None, footer=None):
            g = SettingsGroup(body, title, footer)
            g.pack(fill="x", padx=24, pady=(0, 18))
            return g

        # ── VidDown (the app itself) — updates from GitHub ─────────────────
        self._build_app_update_group(group, cbg, sett)

        # ── yt-dlp updates (both controls save immediately) ────────────────
        g = group("yt-dlp updates", "yt-dlp is the engine that reads each video site. Sites change often, "
                                    "so keeping it current fixes most \"download failed\" problems.")
        self.autoupdate_var = tk.BooleanVar(value=sett.get("ytdlp_autoupdate", True))
        g.row("Update automatically", "Check for a newer yt-dlp in the background.",
              lambda p: ToggleSwitch(p, self.autoupdate_var, bg=cbg, command=self._on_autoupdate_changed))
        self.interval_var = tk.StringVar(
            value="1 day" if sett.get("ytdlp_update_interval", 7) == 1 else "1 week")
        g.row("Check every", None,
              lambda p: SegmentedControl(p, [("1 day", "1 day"), ("1 week", "1 week")],
                                         self.interval_var, command=self._on_interval_changed, bg=cbg))
        row = g.custom()
        text = tk.Frame(row, bg=cbg)
        text.pack(side="left", fill="x", expand=True)
        self._ver_label = tk.Label(text, text="", font=FONTS["body"], bg=cbg, fg=COLORS["text_primary"],
                                   anchor="w", justify="left", wraplength=400)
        self._ver_label.pack(fill="x")
        self._upd_status = tk.Label(text, text="", font=FONTS["small"], bg=cbg, fg=COLORS["success"],
                                    anchor="w", justify="left", wraplength=400)
        self._upd_status.pack(fill="x")
        self._upd_btn = RoundedButton(row, "Update now", command=self._manual_update, padx=14, pady=7, bg=cbg)
        self._upd_btn.pack(side="right", padx=(16, 0))
        self._refresh_version()

        # ── ffmpeg / ffprobe / aria2c ──────────────────────────────────────
        if IS_WINDOWS:
            tools_hint = ("ffmpeg, ffprobe, aria2c and (in the \"with JS runtime\" build) Deno are bundled "
                          "inside VidDown, so they update together with a new VidDown release.")
        else:
            tools_hint = ("ffmpeg, ffprobe and aria2c are not pip packages, so they update differently: "
                          "each is either bundled inside VidDown (it changes with a new VidDown release) "
                          "or provided by your system via apt. For a system copy, the button opens a "
                          "terminal with the apt command already typed in. You enter your own sudo "
                          "password there; VidDown never runs a privileged command on its own.")
        g = group("Tools", tools_hint)
        self._tools_status_label = tk.Label(g.custom(), text="Checking\u2026", font=FONTS["small"], bg=cbg,
                                            fg=COLORS["text_secondary"], anchor="w", justify="left",
                                            wraplength=520)
        self._tools_status_label.pack(fill="x")
        if not IS_WINDOWS:
            g.row("Update system tools", "Opens a terminal with the apt upgrade command.",
                  lambda p: RoundedButton(p, "Update via apt", command=self._update_other_tools,
                                          padx=14, pady=7, bg=cbg))
        self._refresh_tools_status()

        # ── error log ──────────────────────────────────────────────────────
        g = group("Troubleshooting", "Failed downloads, fetch errors and crashes are recorded here. "
                                     "Passwords, cookies and token-like URL parameters are masked.")
        row = g.custom()
        self._log_summary = tk.Label(row, text="", font=FONTS["body"], bg=cbg, anchor="w")
        self._log_summary.pack(side="left", fill="x", expand=True)
        RoundedButton(row, "Save as .txt", command=self._save_log, padx=12, pady=7, bg=cbg
                      ).pack(side="right", padx=(8, 0))
        RoundedButton(row, "View log", command=self._open_log_viewer, padx=12, pady=7, bg=cbg
                      ).pack(side="right")
        self._viewer = None
        self._refresh_log_summary()

    # ── VidDown app update ────────────────────────────────────────────────
    def _build_app_update_group(self, group, cbg, sett):
        self._app_kind = app_updater.install_kind()
        self._app_release = None       # newest release found by the last check
        self._app_asset = None         # the file that would be installed
        self._app_state = "idle"       # idle | checking | available | busy
        self._app_cancel = None
        self._app_prog = {}
        self._app_updatable = self._app_kind in (app_updater.KIND_DEB, app_updater.KIND_WIN_INSTALLER)

        g = group("VidDown updates",
                  "Updates come from github.com/saimum10/VidDown. A downloaded file is only "
                  "installed after its SHA-256 matches the one published with the release.")
        row = g.custom()
        text = tk.Frame(row, bg=cbg)
        text.pack(side="left", fill="x", expand=True)
        tk.Label(text, text=app_updater.describe_install(), font=FONTS["body"], bg=cbg,
                 fg=COLORS["text_primary"], anchor="w", justify="left").pack(fill="x")
        self._app_status = tk.Label(text, text="", font=FONTS["small"], bg=cbg, fg=COLORS["text_muted"],
                                    anchor="w", justify="left", wraplength=400)
        self._app_status.pack(fill="x")
        self._app_bar = SlimProgress(text, bg=cbg)          # packed only while downloading
        btns = tk.Frame(row, bg=cbg)
        btns.pack(side="right", padx=(16, 0))
        self._app_notes_btn = RoundedButton(btns, "Release notes", padx=12, pady=7, bg=cbg,
                                            command=self._open_release_page)
        self._app_btn = RoundedButton(btns, "Check for updates", command=self._on_app_button,
                                      padx=14, pady=7, bg=cbg)
        self._app_btn.pack(side="right")

        if self._app_updatable and buildinfo.get_variant() != buildinfo.VARIANT_SOURCE:
            self._app_variant_var = tk.StringVar(value=buildinfo.get_variant())
            g.row("Build type",
                  "\"With JS runtime\" includes Deno, which YouTube needs to offer all video "
                  "formats (bigger download). \"Without\" is smaller; it still uses a Deno, "
                  "Node or Bun you have installed yourself. Pick the other one and update to "
                  "switch.",
                  lambda p: SegmentedControl(
                      p, [(buildinfo.VARIANT_JS, "With JS runtime"), (buildinfo.VARIANT_NOJS, "Without")],
                      self._app_variant_var, command=self._on_variant_changed, bg=cbg))
        else:
            self._app_variant_var = tk.StringVar(value=buildinfo.VARIANT_NOJS)

        self._app_auto_var = tk.BooleanVar(value=sett.get("app_update_autocheck", True))
        g.row("Check automatically", "Look for a new VidDown release shortly after it starts.",
              lambda p: ToggleSwitch(p, self._app_auto_var, bg=cbg, command=self._on_app_auto_changed))

        if not self._app_updatable:
            why = {
                app_updater.KIND_SOURCE: "Running from source — update with git pull. "
                                         "Checking still tells you when a release is out.",
                app_updater.KIND_WIN_PORTABLE: "This is the portable version. VidDown can tell you "
                                               "when a release is out; download the new zip from the "
                                               "releases page.",
            }.get(self._app_kind, "This copy was not installed from a VidDown package, so it can tell "
                                  "you about new releases but not install them itself.")
            self._set_app_status(why)

    def _set_app_status(self, text, color=None):
        self._app_status.configure(text=text, fg=color or COLORS["text_muted"])

    def _open_release_page(self):
        webbrowser.open(self._app_release.url if self._app_release else app_updater.RELEASES_PAGE_URL)

    def _on_app_auto_changed(self):
        self.app.settings["app_update_autocheck"] = bool(self._app_auto_var.get())
        self.app.save_settings()

    def _on_variant_changed(self):
        # a different build type may or may not exist in the release we already know about
        if self._app_release is not None and self._app_state in ("available", "idle"):
            self._show_check_result(self._app_release)

    def _run_async(self, work, on_done):
        """Run work() on a thread; this (Tk) thread polls for the result — a
        worker must never call after() itself."""
        box = {}

        def _target():
            try:
                box["ok"] = work()
            except BaseException as exc:      # reported to the person, never lost
                box["err"] = exc
        threading.Thread(target=_target, daemon=True, name="app-update").start()

        def _poll():
            if "ok" in box or "err" in box:
                on_done(box.get("ok"), box.get("err"))
            else:
                self.after(150, _poll)
        self.after(150, _poll)

    def _on_app_button(self):
        if self._app_state == "busy":
            if self._app_cancel is not None:
                self._app_cancel.set()
            return
        if self._app_state == "available" and self._app_asset is not None:
            self._start_install()
        elif self._app_state == "available":
            self._open_release_page()
        elif self._app_state != "checking":
            self.check_for_updates(silent=False)

    def check_for_updates(self, silent=False):
        """Ask GitHub for the newest release. silent=True (the automatic check at
        startup) stays quiet unless there is something to report."""
        if self._app_state in ("checking", "busy"):
            return
        self._app_state = "checking"
        self._app_btn.configure(state="disabled", text="Checking…")
        if not silent:
            self._set_app_status("Checking GitHub…")

        def _done(release, err):
            self._app_state = "idle"
            self._app_btn.configure(state="normal", text="Check for updates")
            if err is not None:
                errorlog.info("app-update", f"update check failed: {err}")
                if not silent:
                    self._set_app_status(str(err) if isinstance(err, app_updater.UpdateError)
                                         else "Update check failed.", COLORS["danger"])
                return
            self.app.settings["app_update_last_check"] = datetime.datetime.now().isoformat()
            self.app.save_settings()
            self._show_check_result(release, announce=True)

        self._run_async(app_updater.fetch_latest_release, _done)

    def _show_check_result(self, release, announce=False):
        """Work out what to offer for `release` and show it."""
        self._app_release = release
        self._app_asset = None
        newer = app_updater.is_newer(release.version, __version__)
        chosen = self._app_variant_var.get()
        installed = buildinfo.get_variant()
        switching = (self._app_updatable and installed != buildinfo.VARIANT_SOURCE
                     and chosen != installed)
        self._app_notes_btn.pack(side="right", padx=(0, 8))
        if announce:
            self.app.on_app_update_checked(release if newer else None)

        if not self._app_updatable:
            if newer:
                self._app_state = "available"
                self._set_app_status(f"Version {release.version} is available.", COLORS["success"])
                self._app_btn.configure(text="Open releases page")
            else:
                self._app_state = "idle"
                self._set_app_status(f"You're up to date (version {__version__}).", COLORS["success"])
            return

        asset = app_updater.pick_asset(release, self._app_kind, chosen) if (newer or switching) else None
        if (newer or switching) and asset is None:
            self._app_state = "available"
            label = buildinfo.variant_label(chosen)
            self._set_app_status(f"Version {release.version} is out, but it has no build {label} "
                                 f"for this system. Open the releases page to see what is offered.",
                                 COLORS["warning"])
            self._app_btn.configure(text="Open releases page")
        elif newer:
            self._app_state, self._app_asset = "available", asset
            when = f" ({release.published})" if release.published else ""
            self._set_app_status(f"Version {release.version} is available{when}.", COLORS["success"])
            self._app_btn.configure(text=f"Download & install {release.version}")
        elif switching:
            self._app_state, self._app_asset = "available", asset
            self._set_app_status(f"Switch to the build {buildinfo.variant_label(chosen)} "
                                 f"(version {release.version}).", COLORS["success"])
            self._app_btn.configure(text="Download & install")
        else:
            self._app_state = "idle"
            self._set_app_status(f"You're up to date (version {__version__}).", COLORS["success"])
            self._app_btn.configure(text="Check for updates")

    def _start_install(self):
        release, asset = self._app_release, self._app_asset
        if self._app_kind == app_updater.KIND_WIN_INSTALLER:
            question = ("VidDown will download the update, then close, install it and start again.\n\n"
                        "Downloads in progress are paused and come back paused.\n\nContinue?")
        else:
            question = ("VidDown will download the update, then ask for your administrator "
                        "password (a system dialog) to install it.\n\nContinue?")
        if not messagebox.askyesno("Update VidDown", question, parent=self.winfo_toplevel()):
            return

        self._app_state = "busy"
        self._app_cancel = threading.Event()
        prog = self._app_prog = {"done": 0, "total": 0, "phase": "download"}
        self._app_btn.configure(text="Cancel")
        self._app_bar.pack(fill="x", pady=(6, 2))
        self._app_bar.set(0, indeterminate=True)
        cancel, kind = self._app_cancel, self._app_kind

        def _work():
            sha = app_updater.fetch_expected_sha256(release, asset)
            path = app_updater.download_asset(
                asset, sha, cancel=cancel,
                progress=lambda done, total: prog.update(done=done, total=total))
            prog["phase"] = "install"
            return app_updater.install_update(path, kind)

        def _tick():
            if self._app_state != "busy":
                return
            if prog["phase"] == "download":
                done, total = prog["done"], prog["total"]
                if total:
                    self._app_bar.set(100.0 * done / total)
                    self._set_app_status(f"Downloading {human_size(done)} of {human_size(total)}…")
            else:
                self._app_btn.configure(state="disabled")
                self._app_bar.set(0, indeterminate=True)
                self._set_app_status("Installing…")
            self.after(200, _tick)
        _tick()

        self._run_async(_work, self._on_install_done)

    def _on_install_done(self, outcome, err):
        self._app_bar.pack_forget()
        self._app_state = "available"
        self._app_btn.configure(state="normal", text="Try again")
        top = self.winfo_toplevel()
        if isinstance(err, app_updater.Cancelled):
            self._set_app_status("Download cancelled.")
            return
        if err is not None:
            msg = str(err) if isinstance(err, app_updater.UpdateError) else f"Unexpected error: {err}"
            errorlog.error("app-update", msg, err if not isinstance(err, app_updater.UpdateError) else None)
            self._set_app_status(msg, COLORS["danger"])
            return
        if outcome == app_updater.OUTCOME_CANCELLED:
            self._set_app_status("Cancelled — nothing was changed.")
        elif outcome == app_updater.OUTCOME_APP_CLOSES:
            self._set_app_status("Installing — VidDown closes now and starts again when done.",
                                 COLORS["success"])
            self.after(400, self.app.quit_for_update)
        elif outcome == app_updater.OUTCOME_TERMINAL:
            self._app_btn.configure(text="Check for updates")
            self._app_state = "idle"
            self._set_app_status("Finish the installation in the terminal window, then start "
                                 "VidDown again.", COLORS["success"])
        else:   # OUTCOME_RESTART
            self._app_btn.configure(text="Check for updates")
            self._app_state = "idle"
            self._set_app_status("Update installed.", COLORS["success"])
            if messagebox.askyesno("Update installed",
                                   "VidDown was updated. Restart it now?", parent=top):
                app_updater.schedule_relaunch()
                self.app.quit_for_update()

    # ── update card behaviour ─────────────────────────────────────────────
    def _refresh_version(self):
        running = get_ytdlp_version()
        pending = ytdlp_loader.override_version()
        text = f"Current version: {running}"
        if pending and pending != running:
            text += f"  •  v{pending} downloaded — restart VidDown to use it"
        self._ver_label.configure(text=text)

    def _open_licenses(self):
        from ui.license_viewer import LicenseViewer
        LicenseViewer(self.winfo_toplevel())

    def _save_flash(self, ok):
        if ok:
            self._upd_status.configure(text="Saved", fg=COLORS["success"])
        else:
            self._upd_status.configure(
                text="Could not write settings to disk — applies for this session only.",
                fg=COLORS["danger"])

    def _on_autoupdate_changed(self):
        self.app.settings["ytdlp_autoupdate"] = bool(self.autoupdate_var.get())
        self._save_flash(self.app.save_settings())

    def _on_interval_changed(self):
        self.app.settings["ytdlp_update_interval"] = 1 if self.interval_var.get() == "1 day" else 7
        self._save_flash(self.app.save_settings())

    def _manual_update(self):
        self._upd_btn.configure(state="disabled", text="Updating…")
        self._upd_status.configure(text="Updating yt-dlp…", fg=COLORS["text_muted"])

        # The update runs on a background thread. It only drops its result in
        # `box`; this (Tk) thread polls for it. Calling self.after() from the
        # worker thread is not safe in Tkinter.
        box = {}
        update_ytdlp(on_done=lambda ok, msg: box.update(result=(ok, msg)),
                     settings=self.app.settings, save_fn=save_settings)

        def _poll():
            if "result" in box:
                self._on_update_done(*box["result"])
            else:
                self.after(150, _poll)
        self.after(150, _poll)

    def _on_update_done(self, ok, msg):
        self._upd_btn.configure(state="normal", text="Update now")
        self._upd_status.configure(text=msg,
                                   fg=COLORS["success"] if ok else COLORS["danger"])
        self._refresh_version()

    # ── other tools behaviour ─────────────────────────────────────────────
    def _refresh_tools_status(self):
        """Check ffmpeg/ffprobe/aria2c off the UI thread; the worker only drops
        its text in `box`, this thread polls for it (no after() from a worker)."""
        box = {}

        def _work():
            status = get_tool_status()
            lines = []
            for name in ("ffmpeg", "ffprobe", "aria2c"):
                info = status[name]
                if info["source"] == "missing":
                    lines.append(f"{name}  \u00b7  not found")
                else:
                    tag = "bundled" if info["source"] == "bundled" else "system"
                    m = re.search(r"version\s+(\S+)", info["version"] or "")
                    ver = m.group(1) if m else ((info["version"] or "version unknown").splitlines()[0][:40])
                    lines.append(f"{name} {ver}  \u00b7  {tag}")
            js = get_js_runtime_status()
            if js["source"] == "missing":
                lines.append("JS runtime  \u00b7  none (YouTube may offer fewer formats)")
            else:
                m = re.search(r"(\d+(?:\.\d+)+)", js["version"] or "")
                lines.append(f"{js['name']} {m.group(1) if m else ''}".rstrip()
                             + f"  \u00b7  {js['source']} JS runtime")
            box["text"] = "\n".join(lines)

        threading.Thread(target=_work, daemon=True).start()

        def _poll():
            if "text" in box:
                self._tools_status_label.configure(text=box["text"])
            else:
                self.after(150, _poll)
        self.after(150, _poll)

    def _update_other_tools(self):
        if open_apt_upgrade_terminal():
            self.app.set_status("Terminal opened — finish the update there with your sudo password")
            # re-check a bit later; harmless whether or not the apt step finished
            self.after(8000, self._refresh_tools_status)
        else:
            messagebox.showerror(
                "No Terminal Found",
                "Couldn't launch a terminal automatically. Run this manually in a terminal:\n\n"
                "sudo apt update && sudo apt install --only-upgrade ffmpeg aria2c"
            )

    # ── error log behaviour ───────────────────────────────────────────────
    def on_show(self):
        self._refresh_log_summary()

    def _refresh_log_summary(self):
        errors, warnings = errorlog.count_problems()
        if errors or warnings:
            self._log_summary.configure(
                text=f"{errors} error{'s' if errors != 1 else ''}, "
                     f"{warnings} warning{'s' if warnings != 1 else ''} recorded",
                fg=COLORS["danger"] if errors else COLORS["warning"])
        else:
            self._log_summary.configure(text="No problems recorded.", fg=COLORS["success"])

    def _open_log_viewer(self):
        if self._viewer is not None and self._viewer.winfo_exists():
            self._viewer.refresh()
            self._viewer.lift()
            return
        self._viewer = LogViewer(self.winfo_toplevel(), self.app)

    def _save_log(self):
        path = save_log_dialog(self.winfo_toplevel())
        if path:
            self.app.set_status(f"Log saved to {path}")
