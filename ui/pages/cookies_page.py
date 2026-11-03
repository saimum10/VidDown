"""
ui/pages/cookies_page.py

Simplified cookies page — uses yt-dlp's built-in --cookies-from-browser
instead of browser_cookie3. No file extraction needed; yt-dlp reads
directly from the selected browser's cookie store.

Layout:
  Use Cookies toggle
  ─────────────────────
  Browser selector (dropdown)
  [Verify] button  ←  runs a quick yt-dlp test
  Status label
  ─────────────────────
  Manual cookies.txt file import (fallback for edge cases)
"""
import tkinter as tk
from ui.widgets import (SettingsGroup, Dropdown, EntryField, RoundedButton, ToggleSwitch)
from tkinter import filedialog

from theme import COLORS, FONTS
from ui.scrollable import ScrollableFrame
from ui.threads import run_in_thread
from core import browser_cookies
from core.downloader import USER_AGENT_PRESETS, USER_AGENT_LABELS

# Browsers yt-dlp supports via --cookies-from-browser
BROWSERS = [
    ("chrome",   "Google Chrome"),
    ("chromium", "Chromium"),
    ("firefox",  "Mozilla Firefox"),
    ("edge",     "Microsoft Edge"),
    ("brave",    "Brave"),
    ("opera",    "Opera"),
    ("vivaldi",  "Vivaldi"),
    ("safari",   "Safari (macOS)"),
]
BROWSER_LABELS  = [label for _, label in BROWSERS]
BROWSER_KEYS    = {label: key for key, label in BROWSERS}
BROWSER_DISPLAY = {key: label for key, label in BROWSERS}


class CookiesPage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=COLORS["bg_dark"])
        self.app = app

        scroll = ScrollableFrame(self, bg=COLORS["bg_dark"])
        scroll.pack(fill="both", expand=True)
        body = scroll.body

        tk.Label(
            body, text="Cookies", font=FONTS["page_title"],
            bg=COLORS["bg_dark"], fg=COLORS["text_primary"], anchor="w",
        ).pack(fill="x", padx=24, pady=(18, 14))

        card = COLORS["bg_card"]

        def group(title=None, footer=None):
            g = SettingsGroup(body, title, footer)
            g.pack(fill="x", padx=24, pady=(0, 18))
            return g

        # ── Use cookies (saves immediately — no Save button) ───────────────
        g = group("Sign-in", "Cookies let downloads reach videos that need you to be signed in. "
                             "They are read directly from the browser you pick.")
        self.use_cookies_var = tk.BooleanVar(value=self.app.settings.get("use_cookies", True))
        g.row("Use browser cookies", "Send your browser's cookies with download requests.",
              lambda p: ToggleSwitch(p, self.use_cookies_var, bg=card, command=self._on_toggle))

        saved_key = self.app.settings.get("cookies_browser", "")
        saved_label = BROWSER_DISPLAY.get(saved_key, "")
        PLACEHOLDER = "Select a browser"
        self._browser_var = tk.StringVar(value=saved_label if saved_label else PLACEHOLDER)
        g.row("Browser", "Which browser to read cookies from.",
              lambda p: Dropdown(p, self._browser_var, [PLACEHOLDER, *BROWSER_LABELS],
                                 command=self._on_browser_changed, bg=card))
        status = g.custom()
        self._status_var = tk.StringVar(value="")
        self._status_label = tk.Label(status, textvariable=self._status_var, font=FONTS["small"],
                                      bg=card, fg=COLORS["text_muted"], anchor="w", justify="left",
                                      wraplength=420)
        self._status_label.pack(side="left", fill="x", expand=True)
        RoundedButton(status, "Verify", command=self._verify, padx=14, pady=7, bg=card).pack(side="right")

        # ── User agent ──────────────────────────────────────────────────────
        g = group("Identity")
        ua_modes = [("default", "Automatic (yt-dlp default)"), ("random", "Random (recommended to try)")]
        ua_modes.extend((key, label) for key, label in USER_AGENT_LABELS.items())
        self._ua_key_by_label = {label: key for key, label in ua_modes}
        self._ua_label_by_key = {key: label for key, label in ua_modes}
        saved_ua = self.app.settings.get("user_agent_mode", "default")
        self._ua_var = tk.StringVar(value=self._ua_label_by_key.get(saved_ua, "Automatic (yt-dlp default)"))
        g.row("User-Agent", "The browser or device identity sent with download requests. "
                            "Random picks one profile per queued download.",
              lambda p: Dropdown(p, self._ua_var, list(self._ua_label_by_key.values()),
                                 command=self._on_user_agent_changed, bg=card),
              stacked=True)

        # ── Manual cookies.txt ──────────────────────────────────────────────
        g = group("Fallback")
        self._file_var = tk.StringVar(value=self.app.settings.get("cookies_file", ""))

        def file_control(p):
            wrap = tk.Frame(p, bg=card)
            EntryField(wrap, self._file_var, "Path to a cookies.txt file (Netscape format)",
                       font=FONTS["small"], bg=card).pack(side="left", fill="x", expand=True)
            RoundedButton(wrap, "Browse", command=self._browse_file, padx=12, pady=7,
                          bg=card).pack(side="left", padx=(8, 0))
            RoundedButton(wrap, "Clear", command=self._clear_file, variant="danger", padx=10,
                          pady=7, bg=card).pack(side="left", padx=(4, 0))
            return wrap
        g.row("Manual cookies.txt",
              'Optional. Export cookies.txt with the "Get cookies.txt LOCALLY" browser extension, '
              "then import it here.", file_control, stacked=True)

        self._update_status_label()

    # ──────────────────────────────────────────────── handlers ────────────
    def _on_toggle(self):
        self.app.settings["use_cookies"] = self.use_cookies_var.get()
        self.app.save_settings()
        self._update_status_label()

    def _on_browser_changed(self, label):
        if label.startswith("—"):          # placeholder selected
            self.app.settings["cookies_browser"] = ""
        else:
            self.app.settings["cookies_browser"] = BROWSER_KEYS.get(label, "")
        browser_cookies.forget(self.app.settings)      # what worked for one browser means nothing for another
        self.app.save_settings()
        self._update_status_label()
        if self.app.settings.get("cookies_browser"):
            self._verify(self.app.settings["cookies_browser"])      # find the profile / keyring now, so downloads can use cookies at once

    def _on_user_agent_changed(self, label):
        key = self._ua_key_by_label.get(label, "default")
        self.app.settings["user_agent_mode"] = key
        # A newly chosen mode must not inherit a previous job's random UA.
        self.app.settings.pop("_resolved_user_agent", None)
        self.app.save_settings()
        if key == "random":
            self._set_status("Random User-Agent will be selected for each queued download.", ok=None)
        elif key == "default":
            self._set_status("Using yt-dlp's default User-Agent behavior.", ok=True)
        else:
            self._set_status(f"User-Agent set to: {label}", ok=True)

    def _verify(self, key=None):
        key = key or BROWSER_KEYS.get(self._browser_var.get(), "")
        if not key:
            self._set_status("Please select a browser first.", ok=False)
            return

        self._set_status("Verifying… (running yt-dlp test)", ok=None)

        label = BROWSER_DISPLAY.get(key, key)

        def work():
            # In-process on purpose: in the packaged app sys.executable is
            # VidDown itself, so `sys.executable -m yt_dlp` just relaunched the
            # app, which woke the running window and exited 0 — a false "works".
            # Tries the usual profile folder, Snap/Flatpak ones, and every
            # keyring a Chromium-based browser may have used.
            return browser_cookies.autodetect(key)

        def done(result, exc):
            if exc is not None:
                text = str(exc).strip()
                err_msg = text.splitlines()[-1] if text else type(exc).__name__
                self._set_status(f"Verification failed: {err_msg}", ok=False)
                return
            ok, text = browser_cookies.describe(result, label)
            if ok:                                   # remember how, so downloads use it too
                browser_cookies.remember(self.app.settings, result)
                self.app.save_settings()
            else:
                browser_cookies.forget(self.app.settings)
                self.app.save_settings()
            self._set_status(text, ok=ok)

        run_in_thread(self, work, done, name="cookie-verify")

    def _set_status(self, msg, ok=None):
        color = (COLORS["success"] if ok is True
                 else COLORS["danger"] if ok is False
                 else COLORS["text_muted"])
        self._status_var.set(msg)
        self._status_label.configure(fg=color)

    def _update_status_label(self):
        key = self.app.settings.get("cookies_browser", "")
        use = self.app.settings.get("use_cookies", True)
        if not use:
            self._set_status("Cookies are disabled.", ok=False)
        elif key:
            self._set_status(
                f"Using {BROWSER_DISPLAY.get(key, key)} cookies automatically.", ok=True
            )
        else:
            self._set_status("No browser selected. Select one above.", ok=None)

    def _browse_file(self):
        path = filedialog.askopenfilename(
            title="Select cookies.txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if path:
            self._file_var.set(path)
            self.app.settings["cookies_file"] = path
            self.app.save_settings()

    def _clear_file(self):
        self._file_var.set("")
        self.app.settings["cookies_file"] = ""
        self.app.save_settings()

    def on_show(self):
        self._update_status_label()
