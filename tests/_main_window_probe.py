"""Run by test_startup_memory.py in a fresh process (own HOME, so the user's settings and
queue are never touched). Builds the real MainWindow and prints what it saw as one JSON line."""
import json
import os
import sys
import tempfile
import time
from types import SimpleNamespace

home = tempfile.mkdtemp()
os.environ["HOME"] = home
os.environ["XDG_CONFIG_HOME"] = os.path.join(home, ".config")
os.environ["XDG_DATA_HOME"] = os.path.join(home, ".local", "share")
ROOT = sys.argv[1]
sys.path.insert(0, ROOT)

import tkinter as tk
try:
    tk.Tk().destroy()
except tk.TclError:
    print(json.dumps({"no_display": True}))
    sys.exit(0)

from core import platform_utils, app_updater
from ui.main_window import MainWindow

out = {}
app = MainWindow(start_hidden=False)
app.update()
pages = app.settings_dialog.pages


def pump(seconds):
    app.after(int(seconds * 1000), app.quit)
    app.mainloop()


# --- lazy Settings pages -------------------------------------------------------------
out["built_at_start"] = {k: pages.built(k) for k in ("settings", "cookies", "about")}
out["pages_know_the_keys"] = [k in app.pages for k in ("download", "queue", "history", "settings", "cookies", "about", "nope")]
app.settings_dialog.flush_pending()
out["flush_before_build_builds_nothing"] = not pages.built("settings")
app.show_page("cookies")
app.update()
out["after_opening_cookies"] = {k: pages.built(k) for k in ("settings", "cookies", "about")}
app.show_page("settings")
app.update()
out["settings_page_is_raised_and_built"] = pages.built("settings")
app.settings_dialog.hide()
app.update()

# --- the quiet update check does not build the About page ---------------------------
shown = []
app.on_app_update_checked = lambda release: shown.append(None if release is None else release.version)
release = SimpleNamespace(version="999.0.0", published="", url="https://example.invalid", notes="", assets=[], body="")
app._quiet_update_result(release, None)
out["update_button_told"] = shown
out["about_still_not_built"] = not pages.built("about")
out["latest_release_remembered"] = getattr(app, "_latest_release", None) is release
app._quiet_update_result(None, RuntimeError("offline"))
out["a_failed_check_changes_nothing"] = shown == ["999.0.0"] and not pages.built("about")
app.show_page("about")
app.update()
out["about_shows_the_known_release"] = pages["about"]._app_release is release

# --- start-up check path ---------------------------------------------------------------
called = []
app._check_app_update_quietly = lambda: called.append(1)
real_kind = app_updater.install_kind
app_updater.install_kind = lambda: "deb"
app.settings["app_update_autocheck"] = True
app.settings["app_update_last_check"] = ""
app._maybe_check_app_update()
app_updater.install_kind = real_kind
out["start_up_check_uses_the_quiet_path"] = called == [1]

# --- memory trim is debounced and runs once ------------------------------------------
runs = []
platform_utils.trim_memory = lambda: runs.append(1) or True
app._trim_job = None
for _ in range(6):
    app.schedule_memory_trim(120)
out["trim_job_scheduled_once"] = app._trim_job is not None
pump(0.5)
out["trim_runs_once_for_six_requests"] = runs == [1]
out["trim_job_cleared"] = app._trim_job is None

# --- clipboard watch only while the Add Download window is open ---------------------
page = app.add_dialog.page
n = [0]
orig = page._monitor_clipboard


def counting():
    n[0] += 1
    orig()


page._monitor_clipboard = counting
pump(2.0)
out["clipboard_checks_while_closed"] = n[0]
app.add_dialog.show()
app.update()
pump(2.0)
out["clipboard_checks_while_open"] = n[0]
n[0] = 0
app.add_dialog.withdraw()          # not hide(): e.g. the window manager closed/minimised it
app.update()
pump(0.5)
out["watch_stops_on_withdraw"] = page._clip_job is None
pump(2.0)
out["clipboard_checks_after_withdraw"] = n[0]
app.add_dialog.deiconify()
app.update()
pump(0.6)
out["watch_restarts_on_map"] = n[0] >= 1

print(json.dumps(out))
app.destroy()
