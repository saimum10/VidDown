"""
core/storage.py
Simple JSON-file based persistence for settings and download history.
No database needed — this keeps the app fully self-contained.

Locations follow the standard XDG layout on Linux (and the usual AppData
folders on Windows — see core/platform_utils.py):
  <config>/config.json      -> user settings
  <data>/history.json       -> download history
  <data>/queue_state.json   -> in-progress/queued jobs, saved on app close
                               and restored on next launch
  <data>/cookies.txt        -> cookies imported from the browser
where <config> is ~/.config/viddown (Linux) or %APPDATA%\\VidDown (Windows)
and <data> is ~/.local/share/viddown (Linux) or %LOCALAPPDATA%\\VidDown (Windows).
"""
import json
import os

from core.platform_utils import config_dir, data_dir, default_output_dir

CONFIG_DIR = config_dir()
DATA_DIR = data_dir()

CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")
QUEUE_STATE_FILE = os.path.join(DATA_DIR, "queue_state.json")
COOKIES_FILE = os.path.join(DATA_DIR, "cookies.txt")

DEFAULT_OUTPUT_DIR = default_output_dir()

DEFAULT_SETTINGS = {
    "output_dir": DEFAULT_OUTPUT_DIR,
    "default_codec": "auto",      # "auto" (smallest file) or "h264" (plays everywhere)
    "default_container": "mp4",   # mp4 / mkv / webm / mov / avi (see core/output_options.py)
    "default_audio_format": "mp3",   # mp3 / m4a / opus / original / flac / wav
    "default_audio_bitrate": 192,    # MP3 only: 128 / 192 / 256 / 320 kbps
    "embed_thumbnail": False,     # put the video's picture into the file as its cover
    "embed_metadata": False,      # write title, uploader, date, description, chapters into the file
    "proxy": "",
    "cookies_file": "",
    "cookies_browser": "",
    "use_cookies": True,
    "user_agent_mode": "default",
    "speed_limit": "",
    "ffmpeg_path": "",
    "aria2c_enabled": True,
    "ytdlp_autoupdate": True,
    "ytdlp_update_interval": 7,
    "ytdlp_last_updated": "",
    "app_update_autocheck": True,     # look for a newer VidDown on GitHub at startup
    "app_update_last_check": "",
    "max_concurrent_downloads": 1,
    "keep_running_on_close": False,
    "theme": "dark",
}

MAX_HISTORY_ITEMS = 200


def _ensure_config_dirs():
    """Create config and data directories — does not touch the output folder."""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)


def _ensure_output_dir(path):
    """Create the output directory — called only right before a download starts."""
    if path:
        os.makedirs(path, exist_ok=True)


def load_settings():
    _ensure_config_dirs()
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            merged = DEFAULT_SETTINGS.copy()
            merged.update(data)
            merged.pop("tray_enabled", None)   # option was removed; drop it from old configs
            return merged
        except (json.JSONDecodeError, OSError):
            pass
    return DEFAULT_SETTINGS.copy()


def _write_json_atomic(path, data, private=False):
    """Write JSON so a crash / full disk / power cut can never leave a
    half-written file: write a temp file next to the target, flush it to
    disk, then atomically swap it in. (Writing straight into the target
    truncated it first, so an interrupted save wiped the person's settings,
    history or queue.) private=True makes the file readable by the owner only
    — settings and the queue can hold proxy credentials and request headers."""
    tmp = path + ".tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                     0o600 if private else 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        if private:
            os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def save_settings(settings):
    """Returns True on success, False if the write failed (disk full,
    permission denied, the drive holding CONFIG_DIR vanished mid-write,
    etc.) — every caller of this used to assume it could never fail and
    had no try/except of its own around it, so an unhandled OSError here
    would propagate straight up and could crash whatever triggered the
    save (the Settings/Cookies pages' auto-save, app shutdown, ...)."""
    _ensure_config_dirs()
    try:
        _write_json_atomic(CONFIG_FILE, settings, private=True)
        return True
    except OSError:
        return False


def load_history():
    _ensure_config_dirs()
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return []


def save_history(history):
    """Returns True/False — see save_settings above for why this can't
    be allowed to raise."""
    _ensure_config_dirs()
    try:
        _write_json_atomic(HISTORY_FILE, history)
        return True
    except OSError:
        return False


def save_queue_state(jobs_data):
    """
    jobs_data: list of plain dicts (url, title, quality, settings,
    options) for every job that was still queued/downloading/paused
    when the app closed. Called from the main window's close handler so
    an in-progress download isn't silently lost.

    Returns True/False — see save_settings above for why this can't be
    allowed to raise. This one matters especially: it's called from
    _do_quit/_force_terminate, right as the app is shutting down — an
    unhandled exception there could interrupt the rest of that shutdown
    sequence (stopping the bridge server, actually closing the window).
    """
    _ensure_config_dirs()
    try:
        _write_json_atomic(QUEUE_STATE_FILE, jobs_data, private=True)
        return True
    except OSError:
        return False


def load_queue_state():
    """Returns the list saved by save_queue_state(), or [] if there's
    nothing to restore. Consumed once at startup — the caller is
    responsible for clearing the file after restoring (see
    clear_queue_state) so a crash loop can't keep re-restoring stale jobs."""
    _ensure_config_dirs()
    if os.path.exists(QUEUE_STATE_FILE):
        try:
            with open(QUEUE_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return []


def clear_queue_state():
    """Called right after load_queue_state() has restored everything,
    so the jobs aren't queued a second time on the next launch."""
    try:
        if os.path.exists(QUEUE_STATE_FILE):
            os.remove(QUEUE_STATE_FILE)
    except OSError:
        pass


def add_history_entry(entry):
    history = load_history()
    history.insert(0, entry)
    history = history[:MAX_HISTORY_ITEMS]
    save_history(history)
    return history


def clear_history():
    save_history([])
    return []
