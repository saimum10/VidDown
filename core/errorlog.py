"""
core/errorlog.py
A small persistent log so problems can be looked at (and saved as a .txt to
send along) instead of vanishing. Everything goes to one file next to the
other VidDown data; the in-app viewer simply reads that file.

Never raises: logging must not be able to break the app it reports on.
Obvious secrets (proxy passwords, cookie/authorization headers, token-like
query parameters) are masked before anything is written.
"""
import datetime
import os
import platform
import re
import sys
import threading
import traceback

from core.storage import DATA_DIR

LOG_FILE = os.path.join(DATA_DIR, "viddown.log")
_MAX_BYTES = 1_000_000          # rotate to viddown.log.1 beyond this
_lock = threading.Lock()

_REDACTIONS = [
    (re.compile(r"(\w+://)[^/\s:@]+:[^/\s@]+@"), r"\1***:***@"),
    (re.compile(r"(?i)\b(cookie|set-cookie|authorization|proxy-authorization)\s*[:=]\s*[^\n]+"), r"\1: ***"),
    (re.compile(r"(?i)([?&](?:token|key|signature|sig|password|passwd|auth|access_token)=)[^&\s]+"), r"\1***"),
]


def _redact(text):
    for pattern, repl in _REDACTIONS:
        text = pattern.sub(repl, text)
    return text


def log(level, source, message, exc=None):
    """Append one entry. `exc` may be an exception instance to include its traceback."""
    try:
        text = f"{message}"
        if exc is not None:
            tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).rstrip()
            text += "\n" + tb
        text = _redact(text)
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        first, *rest = text.splitlines() or [""]
        entry = f"{stamp} [{level}] {source}: {first}\n"
        entry += "".join(f"    {line}\n" for line in rest)
        with _lock:
            os.makedirs(DATA_DIR, exist_ok=True)
            try:
                if os.path.getsize(LOG_FILE) > _MAX_BYTES:
                    os.replace(LOG_FILE, LOG_FILE + ".1")
            except OSError:
                pass
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(entry)
    except Exception:
        pass


def info(source, message):
    log("INFO", source, message)


def warning(source, message, exc=None):
    log("WARNING", source, message, exc)


def error(source, message, exc=None):
    log("ERROR", source, message, exc)


def read_text(max_lines=3000):
    """The most recent part of the log ("" if there is none yet)."""
    try:
        with _lock, open(LOG_FILE, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-max_lines:])
    except OSError:
        return ""


def clear():
    try:
        with _lock:
            for path in (LOG_FILE, LOG_FILE + ".1"):
                try:
                    os.remove(path)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


def count_problems():
    """(errors, warnings) currently in the log file."""
    text = read_text(max_lines=100000)
    return (len(re.findall(r"^\S+ \S+ \[ERROR\]", text, re.M)),
            len(re.findall(r"^\S+ \S+ \[WARNING\]", text, re.M)))


def system_header():
    """Context lines that make a saved log useful in a bug report."""
    try:
        from version import __version__
    except Exception:
        __version__ = "unknown"
    try:
        import yt_dlp.version
        ytdlp = yt_dlp.version.__version__
    except Exception:
        ytdlp = "unknown"
    return (
        f"VidDown {__version__}\n"
        f"yt-dlp {ytdlp}\n"
        f"Python {platform.python_version()} on {platform.platform()}\n"
        f"Exported {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        + "-" * 60 + "\n"
    )


def export_text():
    body = read_text(max_lines=100000)
    return system_header() + (body if body.strip() else "(log is empty — no problems recorded)\n")


def install_hooks(tk_root=None):
    """Record crashes that would otherwise only reach a terminal nobody sees."""
    original_excepthook = sys.excepthook

    def _excepthook(exc_type, exc, tb):
        if not issubclass(exc_type, KeyboardInterrupt):
            log("ERROR", "crash", f"Unhandled exception: {exc_type.__name__}: {exc}", exc)
        original_excepthook(exc_type, exc, tb)

    sys.excepthook = _excepthook

    original_thread_hook = threading.excepthook

    def _thread_hook(args):
        if args.exc_type is not SystemExit:
            log("ERROR", f"thread:{getattr(args.thread, 'name', '?')}",
                f"Unhandled exception: {args.exc_type.__name__}: {args.exc_value}", args.exc_value)
        original_thread_hook(args)

    threading.excepthook = _thread_hook

    if tk_root is not None:
        def _tk_hook(exc_type, exc, tb):
            log("ERROR", "ui", f"Error in a UI callback: {exc_type.__name__}: {exc}", exc)
            traceback.print_exception(exc_type, exc, tb)
        tk_root.report_callback_exception = _tk_hook
