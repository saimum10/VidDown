"""
core/downloader.py
The download engine. Wraps yt-dlp (imported as a Python module, not via
subprocess, so PyInstaller can bundle it directly into the app) and looks
for bundled ffmpeg/ffprobe/aria2c binaries first, falling back to the
system PATH only if a bundled copy isn't found.

DownloadManager runs jobs on a background thread so the GUI never blocks;
all progress callbacks are handed back to the caller (MainWindow), which
is responsible for marshalling any UI updates onto the Tkinter main thread.
"""
import os
import sys
import threading
import uuid
import random
import glob
import re
import signal
import time
import subprocess
import tempfile
from shutil import which

import yt_dlp

from core import output_options as oo
from core import browser_cookies
from core import errorlog
from core.platform_utils import exe_name, no_window_kwargs
from core.storage import _ensure_output_dir


# --------------------------------------------------------- bundled tools --
def app_base_dir():
    """Return the application root for source and PyInstaller builds."""
    if getattr(sys, "frozen", False):
        # In PyInstaller 6 onedir builds, resources are under _internal while
        # the launcher lives one directory above. Older builds used the root.
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def find_bundled_binary(name):
    """Path of a tool shipped INSIDE this package (bin/), across PyInstaller
    layouts — never the system PATH. None if this build doesn't include it."""
    name = exe_name(name)          # 'ffmpeg' -> 'ffmpeg.exe' on Windows
    candidates = []
    if getattr(sys, "frozen", False):
        executable_dir = os.path.dirname(os.path.abspath(sys.executable))
        meipass = getattr(sys, "_MEIPASS", "")
        # PyInstaller 5, PyInstaller 6 onedir, and onefile extraction layouts.
        for base in (meipass, executable_dir, os.path.join(executable_dir, "_internal")):
            if base:
                candidates.append(os.path.join(base, "bin", name))
    else:
        candidates.append(os.path.join(app_base_dir(), "bin", name))

    for candidate in candidates:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return os.path.abspath(candidate)
    return None


def find_binary(name):
    """Find bundled tools across PyInstaller layouts, then use system PATH."""
    return find_bundled_binary(name) or which(exe_name(name))  # may be None


def resolve_ffmpeg_path(settings=None):
    """Prefer a valid user override, then bundled ffmpeg, then system PATH."""
    custom = ((settings or {}).get("ffmpeg_path") or "").strip()
    if custom:
        # yt-dlp accepts either the ffmpeg executable itself or its directory.
        if os.path.isfile(custom) and os.access(custom, os.X_OK):
            return os.path.abspath(custom)
        if os.path.isdir(custom):
            for name in ("ffmpeg", "ffmpeg.exe"):
                candidate = os.path.join(custom, name)
                if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                    return os.path.abspath(candidate)
        # A stale saved override must not mask the copy bundled with VidDown.
    return find_binary("ffmpeg")


FFMPEG_PATH = find_binary("ffmpeg")
FFPROBE_PATH = find_binary("ffprobe")
ARIA2C_PATH = find_binary("aria2c")

_ARIA2C_OK = {}


def usable_aria2c():
    """ARIA2C_PATH, but only if that binary really runs (checked once). A
    placeholder script or a copy missing its shared libraries must not be
    handed to yt-dlp as the external downloader — every download would then
    fail instead of quietly using yt-dlp's own downloader."""
    path = ARIA2C_PATH
    if not path:
        return None
    if path not in _ARIA2C_OK:
        try:
            r = subprocess.run([path, "--version"], capture_output=True, timeout=8,
                               **no_window_kwargs())
            _ARIA2C_OK[path] = r.returncode == 0 and b"aria2" in r.stdout.lower()
        except Exception:
            _ARIA2C_OK[path] = False
    return path if _ARIA2C_OK[path] else None


# Curated, plausible browser User-Agent presets. These are request headers only;
# they do not turn yt-dlp into the selected browser or bypass site permissions.
USER_AGENT_PRESETS = {
    "chrome_windows": ("Google Chrome — Windows PC", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    "edge_windows": ("Microsoft Edge — Windows PC", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"),
    "firefox_windows": ("Mozilla Firefox — Windows PC", "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0"),
    "brave_windows": ("Brave — Windows PC", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    "opera_windows": ("Opera — Windows PC", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 OPR/113.0.0.0"),
    "vivaldi_windows": ("Vivaldi — Windows PC", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    "chrome_linux": ("Google Chrome — Linux PC", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    "firefox_linux": ("Mozilla Firefox — Linux PC", "Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0"),
    "safari_mac": ("Safari — macOS", "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15"),
    "chrome_android": ("Chrome — Android Phone", "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"),
    "firefox_android": ("Firefox — Android Phone", "Mozilla/5.0 (Android 14; Mobile; rv:133.0) Gecko/133.0 Firefox/133.0"),
    "samsung_android": ("Samsung Internet — Android Phone", "Mozilla/5.0 (Linux; Android 14; SM-S921B) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/27.0 Chrome/131.0.0.0 Mobile Safari/537.36"),
    "chrome_android_tablet": ("Chrome — Android Tablet", "Mozilla/5.0 (Linux; Android 14; Pixel Tablet) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    "safari_iphone": ("Safari — iPhone", "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1"),
    "safari_ipad": ("Safari — iPad Tablet", "Mozilla/5.0 (iPad; CPU OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1"),
}
USER_AGENT_LABELS = {key: value[0] for key, value in USER_AGENT_PRESETS.items()}

def choose_user_agent(settings):
    """Return the configured UA; Random picks once per queued job when possible."""
    mode = (settings or {}).get("user_agent_mode", "default")
    if mode in ("", "default", "automatic"):
        return ""
    if mode == "random":
        return random.choice(list(USER_AGENT_PRESETS.values()))[1]
    preset = USER_AGENT_PRESETS.get(mode)
    return preset[1] if preset else ""


# ------------------------------------------------------------- utilities --
QUALITY_LADDER = [
    (2160, "4K (2160p)"),
    (1440, "1440p (2K)"),
    (1080, "1080p (Full HD)"),
    (720, "720p (HD)"),
    (480, "480p"),
    (360, "360p"),
    (240, "240p"),
    (144, "144p"),
]


def human_size(num_bytes):
    if not num_bytes:
        return ""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


class _Size(int):
    """A size in bytes that remembers whether it is exact. A size worked out from a
    bitrate x duration (what YouTube gives for many streams) can be far from the
    real file, so the label then says "≈"."""
    exact = True

    def __new__(cls, value, exact=True):
        obj = super().__new__(cls, value)
        obj.exact = bool(exact)
        return obj


def size_text(num_bytes):
    """human_size(), prefixed with "≈" when the size is only an estimate."""
    text = human_size(num_bytes)
    return f"\u2248 {text}" if text and getattr(num_bytes, "exact", True) is False else text


def scaled_size_text(num_bytes, count):
    """size_text() for `count` copies of a download (a playlist)."""
    if not num_bytes:
        return ""
    return size_text(_Size(num_bytes * count, getattr(num_bytes, "exact", True)))


# JavaScript runtimes yt-dlp can use (YouTube's signature/"n" challenges are
# solved in JS, via the yt-dlp-ejs package).
_JS_RUNTIME_NAMES = ("deno", "node", "bun", "quickjs")


def js_runtime_opts():
    """Value for yt-dlp's `js_runtimes` option.

    "with-js" builds ship Deno inside the package: use exactly that copy, so it
    works on a machine that has no JavaScript runtime at all. Otherwise enable
    every runtime yt-dlp knows — it only uses the ones it actually finds on the
    system (a missing one is simply skipped), so the "no-js" build still
    benefits from a Deno/Node/Bun the person has installed.
    """
    deno = find_bundled_binary("deno")
    if deno:
        return {"deno": {"path": deno}}
    return {name: {} for name in _JS_RUNTIME_NAMES}


def is_youtube_url(url):
    """Any youtube.com / youtu.be link (video, playlist, channel …)."""
    from urllib.parse import urlparse
    try:
        host = (urlparse((url or "").strip()).hostname or "").lower()
    except ValueError:
        return False
    return host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com")


def cookies_usable_for(url):
    """Can browser cookies be sent for this link?

    yt-dlp treats a YouTube request that carries login cookies as "signed in" and
    then uses only the clients that need a JavaScript runtime (the JS-free client
    does not support cookies). On a computer with no JS runtime — the "no-js"
    build — that leaves no client at all: "no downloadable formats", although the
    same link downloads fine without cookies. So there the cookies are left out."""
    return not (is_youtube_url(url) and not js_runtime_found())


def base_ydl_opts(settings=None, url=None):
    """yt-dlp options shared by every call. Pass `url` (the link about to be used)
    so the cookie setting can be judged per link — see cookies_usable_for()."""
    settings = settings or {}
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
    }
    ffmpeg_path = resolve_ffmpeg_path(settings)
    if ffmpeg_path:
        opts["ffmpeg_location"] = ffmpeg_path
    opts["js_runtimes"] = js_runtime_opts()
    cookies_browser = settings.get("cookies_browser", "")
    cookies_file    = settings.get("cookies_file", "")
    use_cookies     = settings.get("use_cookies", True) and (url is None or cookies_usable_for(url))

    if use_cookies and cookies_browser:
        # yt-dlp built-in browser extraction — handles Chrome encryption properly
        # (browser, profile, keyring): the combination Settings → Cookies → Verify
        # found works, or just the browser when nothing was remembered.
        opts["cookiesfrombrowser"] = browser_cookies.ydl_spec(settings)
    elif use_cookies and cookies_file and os.path.isfile(cookies_file):
        # legacy: manual cookies.txt file
        opts["cookiefile"] = cookies_file
    proxy = settings.get("proxy")
    if proxy:
        opts["proxy"] = proxy
    # Set by the browser-extension bridge for links sniffed from the page's
    # network traffic — many CDNs reject a bare request without the page's
    # Referer/Origin, so yt-dlp (and aria2c, via yt-dlp) must send them too.
    extra_headers = settings.get("_http_headers")
    if extra_headers:
        opts["http_headers"] = dict(extra_headers)
    # The selected UA is applied after page-sniffed headers so the user's
    # explicit Cookies-page choice wins. Preserve Referer/Origin from sniffing.
    selected_ua = settings.get("_resolved_user_agent") or choose_user_agent(settings)
    if selected_ua:
        headers = dict(opts.get("http_headers") or {})
        headers["User-Agent"] = selected_ua
        opts["http_headers"] = headers
    return opts



def _stop_process(pid, proc_path, grace=0.6):
    """SIGTERM a process, give it a moment to exit, then SIGKILL it if it is
    still there. Callers must already have established that it is ours."""
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if not os.path.exists(proc_path):
            return
        time.sleep(0.05)
    if os.path.exists(proc_path):
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def _terminate_matching_aria2c(output_dir, artifact_paths):
    """Stop only aria2c processes that appear to belong to this download.

    yt-dlp normally stops its external downloader itself, but on some builds a
    child aria2c can outlive cancellation. On Linux, identify it by both the
    VidDown output directory and one of this job's output filenames; never kill
    every aria2c process on the machine.
    """
    if not sys.platform.startswith("linux") or not os.path.isdir("/proc"):
        return
    # list(...) takes an atomic snapshot: the download thread keeps adding to
    # this set while we iterate, and a "set changed size during iteration"
    # error here would silently kill the watcher thread.
    names = {os.path.basename(p) for p in list(artifact_paths) if p}
    if not names:
        return
    # yt-dlp passes the output dir to aria2c exactly as configured, so a
    # symlinked download folder never matches its realpath(). Accept both.
    out_dirs = {os.path.realpath(output_dir or "."), os.path.abspath(output_dir or ".")}
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == os.getpid():
            continue
        try:
            with open(os.path.join(entry.path, "cmdline"), "rb") as f:
                raw = f.read()
            argv = [x.decode(errors="ignore") for x in raw.split(b"\0") if x]
            if not argv or "aria2c" not in os.path.basename(argv[0]).lower():
                continue
            joined = "\0".join(argv)
            if not any(d in joined for d in out_dirs) or not any(name in joined for name in names):
                continue
            _stop_process(pid, entry.path)
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
            continue


def terminate_child_aria2c():
    """Stop every aria2c that is a *direct child of this process*.

    For application shutdown only: when VidDown quits, nothing it spawned
    should keep running. Matching on parent PID (not on name alone) means an
    unrelated aria2c started by the user or another program is never touched.
    """
    if not sys.platform.startswith("linux") or not os.path.isdir("/proc"):
        return
    me = os.getpid()
    victims = []
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        try:
            with open(os.path.join(entry.path, "stat"), "rb") as f:
                stat = f.read().decode(errors="ignore")
            comm = stat[stat.index("(") + 1: stat.rindex(")")]
            ppid = int(stat[stat.rindex(")") + 1:].split()[1])
        except (OSError, ValueError):
            continue
        if ppid == me and "aria2c" in comm.lower():
            victims.append((int(entry.name), entry.path))
    for pid, proc_path in victims:
        _stop_process(pid, proc_path, grace=0.5)


# ---------------------------------------------------------------------------
# Cancelling a download
#
# 1. The exception raised from a progress hook must NOT be DownloadError.
#    yt-dlp's fragment downloader (HLS / DASH — "part by part" downloads)
#    catches DownloadError inside its per-fragment loop and treats it as "skip
#    this fragment", then carries on with the next one. Raising DownloadError
#    therefore never stopped a fragmented download: it kept requesting every
#    remaining fragment, creating/copying .part-FragN files, until the very
#    end. DownloadCancelled is not a DownloadError, so it propagates out.
#
# 2. yt-dlp runs external downloaders (ffmpeg for trimmed clips, aria2c, curl)
#    with a blocking proc.wait(). No progress hook fires while that wait is in
#    progress, so no exception can interrupt it. We remember every child a
#    download thread spawns and terminate exactly those children on
#    cancel / pause / quit.
# ---------------------------------------------------------------------------
try:
    CancelDownload = yt_dlp.utils.DownloadCancelled
except AttributeError:  # very old yt-dlp: best effort
    CancelDownload = yt_dlp.utils.DownloadError

_TRACK_LOCK = threading.Lock()
_TRACKED = {}            # worker thread id -> {"procs": [Popen, ...], "outputs": set()}
PROCESS_TRACKING = False


def _guess_child_outputs(args):
    """Best effort: the temp file(s) a downloader child is going to write, so a
    killed child's partial output can be deleted afterwards."""
    outs = set()
    try:
        argv = [str(a) for a in (args if isinstance(args, (list, tuple)) else [args])]
    except Exception:
        return outs
    if not argv:
        return outs
    exe = os.path.basename(argv[0]).lower()
    if "ffmpeg" in exe:
        last = argv[-1]
        if last.startswith("file:"):
            last = last[5:]
        if last and not last.startswith("-"):
            outs.add(last)
    elif "aria2c" in exe:
        dir_ = out = None
        for i, a in enumerate(argv[:-1]):
            if a == "--dir":
                dir_ = argv[i + 1]
            elif a == "--out":
                out = argv[i + 1]
        if dir_ and out:
            outs.add(os.path.normpath(os.path.join(dir_, out)))
    return outs


def _register_process(proc, args, post=False):
    """Remember a child process started by this download thread.

    post=True marks a postprocessing child (merge / convert / embed): those
    are only killed on Cancel and on application shutdown, never on Pause —
    finishing a merge costs seconds and leaves a complete file for Resume.
    """
    ident = threading.get_ident()
    with _TRACK_LOCK:
        entry = _TRACKED.setdefault(ident, {"procs": [], "outputs": set(), "post": set()})
        entry["procs"] = [p for p in entry["procs"] if p.poll() is None]
        entry["procs"].append(proc)
        if post:
            entry["post"].add(id(proc))
        entry["outputs"].update(_guess_child_outputs(args))


def _kill_tracked(ident, grace=0.6, include_post=True):
    """Terminate (then kill) the tracked child processes of one download thread.

    include_post=False spares postprocessing children (see _register_process).
    """
    with _TRACK_LOCK:
        entry = _TRACKED.get(ident)
        procs = [p for p in entry["procs"]
                 if include_post or id(p) not in entry["post"]] if entry else []
    live = [p for p in procs if p.poll() is None]
    for p in live:
        try:
            p.terminate()
        except OSError:
            pass
    deadline = time.monotonic() + grace
    while live and time.monotonic() < deadline:
        time.sleep(0.05)
        live = [p for p in live if p.poll() is None]
    for p in live:
        try:
            p.kill()
        except OSError:
            pass


def _forget_tracked(ident):
    """Drop a finished download thread's entry; return the temp outputs it saw."""
    with _TRACK_LOCK:
        entry = _TRACKED.pop(ident, None)
    return set(entry["outputs"]) if entry else set()


def terminate_tracked_processes():
    """Application shutdown: stop every downloader child VidDown started."""
    with _TRACK_LOCK:
        idents = list(_TRACKED)
    for ident in idents:
        _kill_tracked(ident, grace=0.5)


def _terminate_by_marker(marker):
    """Fallback when process tracking is unavailable (Linux only): stop direct
    children of this process whose command line contains a job-unique marker."""
    if not marker or not sys.platform.startswith("linux") or not os.path.isdir("/proc"):
        return
    me = os.getpid()
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        try:
            with open(os.path.join(entry.path, "stat"), "rb") as f:
                stat = f.read().decode(errors="ignore")
            ppid = int(stat[stat.rindex(")") + 1:].split()[1])
            if ppid != me:
                continue
            with open(os.path.join(entry.path, "cmdline"), "rb") as f:
                joined = f.read().decode(errors="ignore")
            if marker in joined:
                _stop_process(int(entry.name), entry.path)
        except (OSError, ValueError):
            continue


def _wrap_popen(module, post):
    """Replace module.Popen (yt-dlp's Popen) by a subclass that registers every
    child process with our tracker. Idempotent. Returns True when active."""
    base = getattr(module, "Popen", None)
    if base is None:
        return False
    if getattr(base, "_viddown_tracked", False):
        return True

    class _TrackedPopen(base):
        _viddown_tracked = True
        _viddown_post = post

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            try:
                _register_process(self, args[0] if args else kwargs.get("args"), post=post)
            except Exception:
                pass

    module.Popen = _TrackedPopen
    return True


def _install_process_tracking():
    """Make yt-dlp's subprocesses visible to us. Returns True if active.

    Downloader children (aria2c, ffmpeg-as-downloader) live in
    yt_dlp.downloader.external; the ffmpeg that MERGES video+audio, converts
    or embeds uses a different import of Popen in yt_dlp.postprocessor.*, so
    those modules are wrapped too (as "post" children) — otherwise a Cancel
    or quit during the merge could not stop ffmpeg.
    """
    global PROCESS_TRACKING
    try:
        import yt_dlp.downloader.external as _ext
        if not _wrap_popen(_ext, post=False):
            return False
    except Exception:
        return False
    for name in ("yt_dlp.postprocessor.ffmpeg", "yt_dlp.postprocessor.embedthumbnail"):
        try:
            import importlib
            _wrap_popen(importlib.import_module(name), post=True)
        except Exception:
            pass   # postprocessor tracking is an extra; the downloader's is what matters
    PROCESS_TRACKING = True
    return True


_install_process_tracking()


# ---------------------------------------------------------------------------
# Trim helpers
# ---------------------------------------------------------------------------
def _hms_dash(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02d}-{seconds % 3600 // 60:02d}-{seconds % 60:02d}"


def trim_tag(trim):
    """'trim 00-01-30 to 00-02-45' for an enabled trim dict, else ''.

    The tag goes into the file name and the job title. Without it a trimmed
    download and a full download of the same video share one file name, and
    yt-dlp then silently keeps whichever file was there first ("has already
    been downloaded") — which is how a trimmed job could end up as the full video.
    """
    try:
        if trim and trim.get("enabled"):
            return f"trim {_hms_dash(trim['start_seconds'])} to {_hms_dash(trim['end_seconds'])}"
    except (KeyError, TypeError, ValueError):
        pass
    return ""


def _read_ffmpeg_progress(path):
    """Parse the tail of an `ffmpeg -progress <file>` log -> {key: value} of the
    latest block (out_time_us, total_size, speed, progress ...)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 4096))
            tail = f.read().decode(errors="ignore")
    except OSError:
        return {}
    last_complete, block = {}, {}
    for line in tail.splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        block[key.strip()] = value.strip()
        if key.strip() == "progress":      # ffmpeg ends every report block with this key
            last_complete, block = block, {}
    return last_complete


# Suffixes yt-dlp / aria2c give to files that are *only* temporary. They are
# matched against the END of the file name — a title that merely contains
# ".Part." / ".tmp" in the middle ("Movie.Part.1.mp4") is an ordinary file.
_TEMP_SUFFIX_RES = (
    re.compile(r"\.part-Frag\d+$", re.IGNORECASE),
    re.compile(r"\.part$", re.IGNORECASE),
    re.compile(r"\.(?:ytdl|aria2|tmp)$", re.IGNORECASE),
    re.compile(r"\.frag\d+$", re.IGNORECASE),
)
_TEMP_GLOB_SUFFIXES = (
    ".part", ".part.*", ".part-Frag*", ".ytdl", ".aria2",
    ".frag*", ".tmp", ".f*.part", ".f*.part.*",
)


def _cleanup_cancelled_fragments(artifact_paths, owned_files=()):
    """Remove the files a cancelled job left behind.

    artifact_paths: every path yt-dlp's hooks reported (final names and
        ``.part`` names). Only recognisably *temporary* files are removed
        from these — a normal media file is never deleted just because a
        hook mentioned it.
    owned_files: files this job provably created itself (it saw bytes being
        downloaded into them, or its merger/audio-extractor produced them):
        the per-stream ``.f137.mp4`` pieces that were waiting to be merged,
        and a merged output that finished before the cancel landed. Those
        are removed too, exactly and only by name.
    """
    candidates = set()
    for raw_path in set(artifact_paths) | set(owned_files):
        if not raw_path:
            continue
        path = os.path.abspath(raw_path)
        candidates.add(path)
        # Normalize chains like .part-Frag4.aria2 down to the base output.
        changed = True
        while changed:
            changed = False
            for pattern in _TEMP_SUFFIX_RES:
                stripped = pattern.sub("", path)
                if stripped != path:
                    path = stripped
                    candidates.add(path)
                    changed = True
                    break

    to_remove = set()
    for base in candidates:
        if any(p.search(base) for p in _TEMP_SUFFIX_RES) and os.path.isfile(base):
            to_remove.add(base)
        # glob.escape: the default file name is "Title [720p].ext" and "[...]"
        # is a character class to glob — unescaped, none of the wildcard
        # patterns below would ever match a real file.
        escaped = glob.escape(base)
        for suffix in _TEMP_GLOB_SUFFIXES:
            to_remove.update(glob.glob(escaped + suffix))
    for owned in owned_files:
        if owned:
            to_remove.add(os.path.abspath(owned))

    for candidate in to_remove:
        try:
            if os.path.isfile(candidate) or os.path.islink(candidate):
                os.remove(candidate)
        except OSError:
            pass


_THUMB_EXTS = (".jpg", ".jpeg", ".png", ".webp")
_STREAM_ID_RE = re.compile(r"\.f[\w-]+$")      # the ".f137" yt-dlp puts on a per-stream file


def _thumbnail_siblings(artifact_paths):
    """Thumbnail files yt-dlp writes next to a download ("Title [720p].webp" for
    "Title [720p].f137.mp4.part"): the names a cancelled job may have left behind."""
    found = set()
    for raw in artifact_paths:
        if not raw:
            continue
        path = os.path.abspath(raw)
        changed = True
        while changed:                       # drop .part / .ytdl / .frag… (chains too)
            changed = False
            for pattern in _TEMP_SUFFIX_RES:
                stripped = pattern.sub("", path)
                if stripped != path:
                    path, changed = stripped, True
                    break
        stem = _STREAM_ID_RE.sub("", os.path.splitext(path)[0])
        found.update(stem + ext for ext in _THUMB_EXTS)
    return found


def _discard_partial_files(job):
    """Delete everything a cancelled job left on disk (see above)."""
    try:
        owned = set(job._owned_paths) | set(job._owned_outputs)
        paths = set(job._artifact_paths)
        if job.options.get("embed_thumbnail"):
            # the cover picture was fetched for this job and is useless without it
            owned |= {p for p in _thumbnail_siblings(paths | owned) if os.path.isfile(p)}
        if paths or owned:
            _cleanup_cancelled_fragments(paths, owned)
    except Exception:
        pass


def js_runtime_found():
    """Is a JavaScript runtime (bundled Deno, or Deno/Node/Bun/QuickJS on the system)
    available for yt-dlp? YouTube lists no downloadable formats without one."""
    return bool(find_bundled_binary("deno")) or any(which(n) for n in _JS_RUNTIME_NAMES)


def _has_usable_formats(info):
    """True when `info` lists at least one real audio/video stream (storyboard
    picture sheets, which YouTube still returns without a JS runtime, do not count)."""
    for f in info.get("formats") or []:
        if f.get("ext") == "mhtml" or "storyboard" in str(f.get("format_note") or "").lower():
            continue
        if f.get("vcodec") not in (None, "none") or f.get("acodec") not in (None, "none"):
            return True
    return False


def no_formats_message(url):
    """Plain-language reason shown when a link yields no downloadable streams."""
    if "youtu" in (url or "").lower():
        if not js_runtime_found():
            return ("YouTube returned no downloadable formats. YouTube needs a JavaScript runtime "
                    "and VidDown could not find one. Install the \"with JS runtime\" build of VidDown, "
                    "or install Deno (or Node.js) on this computer, then try again.")
        return ("YouTube returned no downloadable formats. Update yt-dlp (Settings) and try again; "
                "if the video is age-restricted, private or region-locked, add your browser cookies "
                "on the Cookies page.")
    return "No downloadable formats were found for this link."


_CLEN_RE = re.compile(r"[?&/]clen[=/](\d+)")


def _clen_from_url(url):
    """YouTube stream URLs carry their exact byte length as `clen=` — the size is
    known without any request."""
    m = _CLEN_RE.search(url or "")
    return int(m.group(1)) if m else None


def _head_size(ydl, fmt):
    """Content-Length of a stream, from a HEAD request; None if it cannot be had."""
    from yt_dlp.networking import Request
    try:
        req = Request(fmt["url"], method="HEAD", headers=dict(fmt.get("http_headers") or {}))
        with ydl.urlopen(req) as resp:
            value = resp.headers.get("Content-Length")
        size = int(value) if value else 0
    except Exception:
        return None
    return size if size > 1000 else None      # a tiny answer is an error page, not the stream


def fill_exact_sizes(info, settings=None, timeout=8.0):
    """Give every real stream in `info` an exact `filesize` where it can be had.

    Many YouTube streams arrive with only a bitrate, from which yt-dlp guesses
    `filesize_approx`: that guess ignores variable bitrate, so the size shown
    before downloading was far from the file you got (and looked nearly the same
    for every video of the same length). The exact length is in the stream URL
    (`clen=`) or in a HEAD request's Content-Length; this reads one or the other.
    Anything that cannot be had in time simply stays an estimate (labelled "≈").
    Runs on the fetching thread — never call it from the UI thread."""
    import concurrent.futures
    todo = []
    for f in info.get("formats") or []:
        if (f.get("filesize") or not f.get("url") or f.get("ext") == "mhtml"
                or str(f.get("protocol") or "") not in ("http", "https")
                or (f.get("vcodec") in (None, "none") and f.get("acodec") in (None, "none"))):
            continue
        clen = _clen_from_url(f["url"])
        if clen:
            f["filesize"] = clen
        else:
            todo.append(f)
    todo = todo[:60]
    if not todo:
        return info
    try:
        opts = base_ydl_opts(settings)
        # The URLs are already signed: reading the browser's cookie database
        # (slow, may need a keyring unlock) or starting a JS runtime adds nothing.
        opts.pop("cookiesfrombrowser", None)
        opts.pop("js_runtimes", None)
        opts["socket_timeout"] = min(timeout, 8)
        with yt_dlp.YoutubeDL(opts) as ydl:
            pool = concurrent.futures.ThreadPoolExecutor(max_workers=8)
            futures = {pool.submit(_head_size, ydl, f): f for f in todo}
            done, _pending = concurrent.futures.wait(futures, timeout=timeout)
            pool.shutdown(wait=False, cancel_futures=True)
            for fut in done:
                size = fut.result()
                if size:
                    futures[fut]["filesize"] = size
    except Exception:
        pass            # sizes are cosmetic: fetching must never fail because of them
    return info


def _sample_entry(entries, url):
    """The playlist entry whose qualities/subtitles stand for the list: the video the
    link itself points at (watch?v=ID&list=...), else the first one. Using entry 0
    every time showed the wrong video's formats for a link into the middle of a list."""
    from urllib.parse import urlparse, parse_qs
    try:
        wanted = (parse_qs(urlparse(url or "").query).get("v") or [""])[0]
    except ValueError:
        wanted = ""
    if wanted:
        for e in entries:
            if e.get("id") == wanted:
                return e
    return entries[0]


_PLAYLIST_CAP = 200     # entries read from a playlist
_SAMPLE_TRIES = 5       # videos tried to learn a playlist's qualities
_UNAVAILABLE_TITLES = ("[private video]", "[deleted video]")


def _entry_page_url(entry):
    video_id = entry.get("id") or ""
    return (entry.get("webpage_url")
            or entry.get("original_url")
            or (f"https://www.youtube.com/watch?v={video_id}" if video_id else None)
            or entry.get("url"))


def _sample_candidates(entries, url):
    """The preferred sample video first, then the others in list order -
    leaving out entries yt-dlp already marked as private/deleted."""
    first = _sample_entry(entries, url)
    ordered = [first] + [e for e in entries if e is not first]
    return [e for e in ordered
            if str(e.get("title") or "").strip().lower() not in _UNAVAILABLE_TITLES] or ordered


def is_single_youtube_video(url):
    """True for a plain YouTube video link (watch / youtu.be / shorts / live) that carries
    no playlist (`list=`) — such a link never needs the playlist-style fetch."""
    from urllib.parse import urlparse
    try:
        u = urlparse((url or "").strip())
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    if host.startswith(("www.", "m.", "music.")):
        host = host.split(".", 1)[1]
    if "list=" in (u.query or ""):
        return False
    if host == "youtu.be":
        return len(u.path.strip("/")) > 0
    if host == "youtube.com":
        return u.path == "/watch" or u.path.startswith(("/shorts/", "/live/", "/embed/"))
    return False


def fetch_info(url, settings=None):
    """
    Fetch metadata for a URL without downloading anything.
    For playlists uses extract_flat for speed, then samples the first
    entry for quality/subtitle info. Entries are capped at 200 to avoid
    hanging on very large playlists.
    """
    import itertools
    settings = settings or {}
    opts = base_ydl_opts(settings, url)
    # Only metadata is wanted here. Without this yt-dlp still runs its default format
    # selection and aborts with a bare "Requested format is not available" when a site
    # lists no streams — we report that case ourselves, with the likely reason.
    opts["ignore_no_formats_error"] = True
    # _force_noplaylist is set by bridge server probe to avoid fetching full playlists.
    # A plain YouTube video link gets exactly the same treatment from the URL bar, so
    # both ways of fetching ask yt-dlp for the same thing (the extension's way is the
    # one that has to keep working).
    if settings.get("_force_noplaylist") or is_single_youtube_video(url):
        opts.update({
            "skip_download": True,
            "noplaylist":    True,
            "socket_timeout": 20,   # a dead/slow host fails fast instead of hanging the probe
        })
    else:
        opts.update({
            "skip_download":    True,
            "extract_flat":     "in_playlist",
            "playlistend":      _PLAYLIST_CAP,
        })
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    is_playlist = info.get("_type") == "playlist" or "entries" in info
    if not is_playlist and not _has_usable_formats(info):
        raise RuntimeError(no_formats_message(url))
    if not is_playlist:
        fill_exact_sizes(info, settings)
    if is_playlist:
        # consume at most 200 entries (may be a lazy generator)
        raw   = info.get("entries") or []
        entries = list(itertools.islice(raw, _PLAYLIST_CAP))
        info["entries"]       = entries
        info["_entry_count"]  = len(entries)
        info.setdefault("formats",   [])
        info.setdefault("subtitles", {})

        # Playlists longer than the cap are cut off; say so (the download page
        # shows a notice) instead of silently offering only the first 200.
        try:
            total = int(info.get("playlist_count") or info.get("n_entries") or 0)
        except (TypeError, ValueError):
            total = 0
        info["_playlist_total"] = total or None
        info["_truncated"]      = bool(total > len(entries) or len(entries) >= _PLAYLIST_CAP)

        # Fetch one video's formats so quality options can be shown. If that
        # video cannot be read (private, deleted, geo-blocked - common for the
        # first item of an old playlist) try the next few instead of silently
        # ending up with no video qualities at all.
        if entries:
            tried, last_error = 0, None
            for sample in _sample_candidates(entries, url):
                sample_url = _entry_page_url(sample)
                if not sample_url:
                    continue
                tried += 1
                detail_opts = base_ydl_opts(settings, sample_url)
                detail_opts.update({
                    "skip_download": True,
                    "noplaylist":    True,
                    "ignore_no_formats_error": True,
                })
                try:
                    with yt_dlp.YoutubeDL(detail_opts) as ydl2:
                        first_info = ydl2.extract_info(sample_url, download=False)
                    if not _has_usable_formats(first_info):
                        last_error = no_formats_message(sample_url)
                        continue
                    info["formats"]   = first_info.get("formats",   [])
                    info["subtitles"] = first_info.get("subtitles", {})
                    fill_exact_sizes(info, settings)
                    last_error = None
                    break
                except Exception as exc:
                    last_error = str(exc)
                if tried >= _SAMPLE_TRIES:
                    break
            if tried and last_error is not None and not info.get("formats"):
                raise RuntimeError(
                    f"Could not read the available qualities of this playlist "
                    f"(tried {tried} video{'s' if tried != 1 else ''}). {last_error}")
    return info


def yt_thumb_url(video_id: str) -> str:
    """Construct a YouTube thumbnail URL from a video ID."""
    return f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg" if video_id else ""


def _format_bytes(fmt, duration):
    """Best available size for one format: exact, then yt-dlp's own estimate,
    then bitrate x duration. Returns None if nothing usable is known."""
    size = fmt.get("filesize") or fmt.get("filesize_approx")
    if size:
        return size
    tbr = fmt.get("tbr")
    if tbr and duration:
        return int(tbr * 1000 / 8 * duration)
    return None


def estimate_download_size(info, format_selector):
    """Size of what yt-dlp will really download for `format_selector`.

    The quality list used to show the largest video-only stream of a given
    height — usually the biggest codec (e.g. H.264) — while the download then
    picks yt-dlp's preferred codec (often VP9/AV1, which is smaller) and adds
    the audio stream. So the number looked too high. Here yt-dlp's own format
    selector is run over the same format list, and the sizes of every stream it
    chooses (video + audio) are added up.

    Returns bytes, or None when any chosen stream has no known size (better to
    show nothing than a misleading partial number).
    """
    return _estimate_from(info.get("formats") or [], info.get("duration"), format_selector)


def make_size_estimator(info):
    """estimate_download_size() bound to one video, for sizes worked out later
    (when the person picks a container) without keeping the whole info dict."""
    formats, duration = info.get("formats") or [], info.get("duration")
    return lambda format_selector: _estimate_from(formats, duration, format_selector)


def _estimate_from(formats, duration, format_selector):
    if not formats:
        return None
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
            selector = ydl.build_format_selector(format_selector)
            ctx = {
                "formats": formats,
                "has_merged_format": any("none" not in (f.get("acodec"), f.get("vcodec")) for f in formats),
                "incomplete_formats": (all(f.get("vcodec") == "none" for f in formats)
                                       or all(f.get("acodec") == "none" for f in formats)),
            }
            chosen = next(iter(selector(ctx)), None)
    except Exception:
        return None
    if not chosen:
        return None
    parts = chosen.get("requested_formats") or [chosen]
    sizes = [_format_bytes(f, duration) for f in parts]
    if not sizes or any(sz is None for sz in sizes):
        return None
    return _Size(sum(sizes), exact=all(f.get("filesize") for f in parts))


# Video codec families, most efficient first. The regex is what yt-dlp's
# format filter matches against a format's vcodec string.
CODEC_FAMILIES = [
    ("AV1",   r"^av01"),
    ("VP9",   r"^(vp9|vp09)"),
    ("H.265", r"^(hev1|hvc1|h265|hevc)"),
    ("H.264", r"^(avc1|h264)"),
]
_CODEC_RE = {name: re.compile(rx) for name, rx in CODEC_FAMILIES}


def codec_family(vcodec):
    """'avc1.640028' -> 'H.264', 'vp09.00.40.08' -> 'VP9', ...; None if unknown."""
    v = (vcodec or "").lower()
    if not v or v == "none":
        return None
    for name, rx in _CODEC_RE.items():
        if rx.match(v):
            return name
    return None


def _short_side(fmt):
    """(value, axis) a video quality is named after.

    Landscape and square video are named by their height (1920x1080 -> 1080p).
    Portrait video (Shorts, Reels, TikTok: 1080x1920) is named by its WIDTH —
    going by height used to list such videos as 1920p / 1280p / 854p and made
    a "1080p" request impossible. `axis` is the yt-dlp filter field to use.
    """
    def num(v):
        # yt-dlp normally hands over ints; tolerate floats / numeric strings /
        # junk from odd extractors instead of raising on the comparison below
        try:
            return int(float(v)) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None
    w, h = num(fmt.get("width")), num(fmt.get("height"))
    if w and h and w < h:
        return w, "width"
    return h, "height"


def _codec_selector(height, family, audio_rx=None, axis="height"):
    """Format selector for `family` video at `height` (the quality number, on
    the `axis` field). `audio_rx` (a regex on yt-dlp's acodec) prefers audio
    the target container can hold as it is.

    The last alternative drops the codec filter again: in a playlist the
    codec was chosen from the FIRST video, and a later video without that
    codec would otherwise fail with "Requested format is not available"
    instead of being downloaded in its best available codec.
    """
    rx = dict(CODEC_FAMILIES)[family]
    flt = f"[vcodec~='{rx}']"
    dim = f"[{axis}<={height}]"
    video = f"bestvideo{dim}{flt}"
    fallback = f"best{dim}{flt}"
    any_codec = f"bestvideo{dim}+bestaudio/best{dim}"
    if audio_rx:
        return f"{video}+bestaudio[acodec~='{audio_rx}']/{video}+bestaudio/{fallback}/{any_codec}"
    if family == "H.264":
        # the compatibility choice: AAC (m4a) audio too, since Opus inside an
        # .mp4 is what some TVs and phones refuse to play
        return f"{video}+bestaudio[ext=m4a]/{video}+bestaudio/{fallback}/{any_codec}"
    return f"{video}+bestaudio/{fallback}/{any_codec}"


def _container_selector_and_size(option, container, codec):
    """(format_selector, size_bytes) that gives `codec` video in `container`,
    or None when this quality cannot make that combination."""
    spec = oo.CONTAINERS[container]
    if spec["codecs"] is None:                       # mp4 / mkv: any codec fits
        if codec == "Auto":
            base = option.get("_auto") or option
        else:
            base = (option.get("codec_choices") or {}).get(codec)
            if not base:
                return None
        return base["format_selector"], base.get("size_bytes")
    present = option.get("codecs") or []
    if codec != "Auto":
        family = codec if codec in spec["codecs"] and codec in present else None
    else:
        family = next((f for f in spec["codecs"] if f in present), None)
    if not family:
        return None
    selector = _codec_selector(option["height"], family, spec["audio"],
                               option.get("axis") or "height")
    estimate = option.get("_estimate")
    return selector, (estimate(selector) if estimate else None)


def _retarget(option, container, codec):
    """Copy of `option` with the selector/size for this container+codec. A
    container the quality cannot make falls back to MP4 (always possible)."""
    res = _container_selector_and_size(option, container, codec)
    if res is None:
        container = oo.DEFAULT_CONTAINER
        res = _container_selector_and_size(option, container, codec) \
            or (option["format_selector"], option.get("size_bytes"))
    selector, size = res
    return dict(option, codec=codec, container=container, format_selector=selector,
                size_bytes=size, size_label=size_text(size))


def apply_codec(option, codec):
    """Copy of a quality option set to a specific codec ("Auto" = yt-dlp's own
    choice). Unknown or unavailable codecs leave the option unchanged. The
    container chosen earlier is kept when it can hold the codec (otherwise the
    option falls back to MP4)."""
    if not option or option.get("audio_only"):
        return option
    if codec != "Auto" and codec not in (option.get("codec_choices") or {}):
        return option
    return _retarget(option, oo.normalize_container(option.get("container")), codec)


def apply_container(option, container):
    """Copy of a video quality option set to `container` ("mp4", "mkv", "webm",
    "mov", "avi"), with the format selector and size that go with it. Options
    that carry no codec information (a plain direct link) are returned unchanged
    — their file is kept as it is."""
    if not option or option.get("audio_only") or not option.get("codecs"):
        return option
    return _retarget(option, oo.normalize_container(container), option.get("codec") or "Auto")


def apply_audio_format(option, fmt, bitrate=None):
    """Copy of the audio option set to an audio format (see output_options) and,
    for MP3, a bitrate in kbps. Other options are returned unchanged."""
    if not option or not option.get("audio_only"):
        return option
    fmt = oo.normalize_audio_format(fmt)
    bitrate = oo.normalize_audio_bitrate(bitrate if bitrate is not None else option.get("audio_bitrate"))
    source = ((option.get("audio_choices") or {}).get(fmt) or {}).get("source_bytes")
    size = oo.audio_size_bytes(fmt, bitrate, option.get("duration"), source)
    if size and (fmt in ("flac", "wav") or (fmt != "mp3" and getattr(source, "exact", True) is False)):
        size = _Size(size, exact=False)
    return dict(option, audio_format=fmt, audio_bitrate=bitrate,
                label=oo.audio_label(fmt, bitrate),
                format_selector=oo.AUDIO_FORMATS[fmt]["selector"],
                size_bytes=size, size_label=size_text(size))


def apply_default_outputs(options, settings):
    """Apply Settings' default audio format / container to every quality in
    `options`, in place — for places with no pickers of their own (the browser
    extension's panel)."""
    settings = settings or {}
    for i, opt in enumerate(options):
        if opt.get("audio_only"):
            options[i].update(apply_audio_format(
                opt, settings.get("default_audio_format"), settings.get("default_audio_bitrate")))
        elif opt.get("codecs"):
            options[i].update(apply_container(opt, settings.get("default_container")))
    return options


def job_quality(option):
    """The part of a quality option a job needs, as an independent copy: the
    download page keeps changing its own dicts (codec, container …) after a
    job was queued, which must not reach back into that job."""
    return {k: option.get(k) for k in _QUALITY_SAFE_KEYS if k in option}


def build_quality_options(info):
    """
    Build a list of quality options from available formats.
    One entry per unique height (highest first), plus Audio Only.
    Each entry includes both size_bytes (for dynamic scaling) and size_label.
    """
    formats = info.get("formats") or []
    available_heights = set()      # {(quality number, axis)} - see _short_side()

    for fmt in formats:
        height, axis = _short_side(fmt)
        if not height:
            continue
        # Storyboards / thumbnail sheets (e.g. YouTube's mhtml "sb0..sb3") carry
        # a height but are not video: without this they showed up as bogus
        # 27p / 45p / 90p quality options.
        if (fmt.get("vcodec") == "none" or fmt.get("ext") == "mhtml"
                or "storyboard" in str(fmt.get("format_note") or "").lower()):
            continue
        available_heights.add((height, axis))

    ladder_map = {h: label for h, label in QUALITY_LADDER}
    estimator = make_size_estimator(info)

    options = []
    for height, axis in sorted(available_heights, reverse=True):
        label      = ladder_map.get(height, f"{height}p")
        selector   = f"bestvideo[{axis}<={height}]+bestaudio/best[{axis}<={height}]"
        size_bytes = estimate_download_size(info, selector)

        # Which codecs exist at exactly this height, and what each would cost.
        present = {codec_family(f.get("vcodec")) for f in formats
                   if _short_side(f) == (height, axis) and f.get("vcodec") not in (None, "none")
                   and f.get("ext") != "mhtml"}
        codecs, choices = [], {}
        for family, _rx in CODEC_FAMILIES:
            if family in present:
                sel = _codec_selector(height, family, axis=axis)
                size = estimate_download_size(info, sel)
                codecs.append(family)
                choices[family] = {"format_selector": sel, "size_bytes": size,
                                   "size_label": size_text(size)}
        options.append({
            "height":          height,
            "axis":            axis,             # "height", or "width" for portrait video
            "label":           label,
            "size_bytes":      size_bytes,
            "size_label":      size_text(size_bytes),
            "format_selector": selector,
            "audio_only":      False,
            "codec":           "Auto",
            "container":       oo.DEFAULT_CONTAINER,
            "_estimate":       estimator,        # sizes for containers worked out on demand
            "codecs":          codecs,           # available here, most efficient first
            "codec_choices":   choices,
            "_auto":           {"format_selector": selector, "size_bytes": size_bytes,
                                "size_label": size_text(size_bytes)},
        })

    # A direct .mp4/.webm link (or a stream whose formats carry no height)
    # produces zero entries above — without this the panel would show
    # "No qualities available" for a link that downloads perfectly well.
    if not options and formats:
        selector = "best/bestvideo+bestaudio"
        best_size = estimate_download_size(info, selector)
        options.append({
            "height":          0,
            "label":           "Original (best available)",
            "size_bytes":      best_size or None,
            "size_label":      size_text(best_size),
            "format_selector": selector,
            "audio_only":      False,
        })

    # Audio: the format (MP3 / M4A / Opus / original …) is picked later with
    # apply_audio_format(); here only what that choice needs to know is stored —
    # the duration (MP3/FLAC/WAV sizes follow it) and the size of the source
    # stream (M4A/Opus/original are copies of it when the site serves them).
    duration = info.get("duration")
    sources = {}
    for key, spec in oo.AUDIO_FORMATS.items():
        sel = spec["selector"]
        if sel not in sources:
            sources[sel] = estimate_download_size(info, sel)
    audio = {
        "height":          0,
        "label":           "",
        "size_bytes":      None,
        "size_label":      "",
        "format_selector": "bestaudio/best",
        "audio_only":      True,
        "duration":        duration,
        "audio_choices":   {key: {"source_bytes": sources.get(spec["selector"])}
                            for key, spec in oo.AUDIO_FORMATS.items()},
    }
    options.append(apply_audio_format(audio, oo.DEFAULT_AUDIO_FORMAT, oo.DEFAULT_AUDIO_BITRATE))
    return options


_SUB_LABELS = {
    "bn": "Bengali", "en": "English", "hi": "Hindi", "ur": "Urdu",
    "ar": "Arabic", "es": "Spanish", "fr": "French", "de": "German",
    "ja": "Japanese", "zh": "Chinese", "ko": "Korean", "ru": "Russian",
    "pt": "Portuguese", "tr": "Turkish", "id": "Indonesian", "it": "Italian",
}


def available_subtitles(info):
    """Return a sorted [(lang_code, label), ...] list of manual subtitles."""
    subs = info.get("subtitles") or {}
    result = []
    for code in subs:
        base = code.split("-")[0]
        result.append((code, _SUB_LABELS.get(base, code)))
    result.sort(key=lambda pair: pair[1])
    return result


def _parse_rate_limit(text):
    """'500K', '2M', '1G', also '500KB', '2 MB/s', '1.5MiB/s' → bytes/second.
    A bare number is bytes/second. Unrecognised text → None (no limit)."""
    m = re.fullmatch(r"([\d.]+)\s*([KMG]?)(?:I?B)?(?:/S)?", (text or "").strip().upper())
    if not m:
        return None
    try:
        value = float(m.group(1))
    except ValueError:
        return None
    return value * {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3}[m.group(2)]


# --------------------------------------------------------- download jobs --
# Keys build_quality_options() actually produces. The download page later
# attaches its own UI extras onto these same dicts for rendering (e.g.
# Tkinter widget refs in _row_widgets/_dot/_size_widget) — serialize_job
# strips all of that back off so the result is safe to json.dump.
_QUALITY_SAFE_KEYS = ("height", "axis", "label", "size_bytes", "size_label",
                      "format_selector", "audio_only", "codec",
                      "container", "audio_format", "audio_bitrate")


def quality_short_label(quality):
    """'1080p (Full HD)' -> '1080p' — a clean tag for titles/filenames.
    Used both for the downloaded file's name and for job.title (shown in
    the queue/history UI), so the two always agree. Returns '' for
    audio-only downloads or a quality dict with no usable label."""
    if not quality or quality.get("audio_only"):
        return ""
    raw = (quality.get("label") or "").split(" (")[0].strip()
    if not raw:
        height = quality.get("height")
        raw = f"{height}p" if height else ""
    codec = quality.get("codec")
    if raw and codec and codec != "Auto":
        raw = f"{raw} {codec}"          # e.g. "1080p AV1" — a chosen codec is part of the name
    return raw


def serialize_job(job):
    """Plain-dict snapshot of a job, safe for json.dump. Used to persist
    the queue across app restarts (see storage.save_queue_state).
    Saves base_title (no quality suffix) — DownloadJob.__init__ re-derives
    the display title on restore, so it's never suffixed twice.
    Also saves status: a paused job should come back paused (and NOT
    auto-start downloading) — only "paused" is meaningful here, since
    completed/error/cancelled jobs are never in all_jobs by the time this
    runs, and a "downloading" job restarts as "queued" either way."""
    return {
        "url":      job.url,
        "title":    job.base_title,
        "quality":  {k: job.quality.get(k) for k in _QUALITY_SAFE_KEYS},
        "settings": dict(job.settings),
        "options": {
            "subtitles":       list(job.options.get("subtitles") or []),
            "custom_filename": job.options.get("custom_filename") or "",
            "trim":            job.options.get("trim"),
            "output_dir_override": job.options.get("output_dir_override") or "",
            "embed_thumbnail": bool(job.options.get("embed_thumbnail")),
            "embed_metadata":  bool(job.options.get("embed_metadata")),
        },
        "status": "paused" if job.status == "paused" else "queued",
    }


def deserialize_job(data):
    """Rebuilds a DownloadJob from serialize_job() output. Restores as
    status='paused' if it was saved that way — the caller must check
    this and NOT hand a paused job to DownloadManager.add_job (see
    main_window._restore_queue_state). Otherwise starts fresh at
    status='queued'/progress=0; yt-dlp will pick up and resume any
    partial .part file it finds already sitting in the output directory."""
    options = data.get("options") or {}
    job = DownloadJob(
        data["url"], data["title"], data["quality"], data["settings"],
        subtitles=options.get("subtitles", []),
        custom_filename=options.get("custom_filename", ""),
        trim=options.get("trim"),
        output_dir_override=options.get("output_dir_override", ""),
        embed_thumbnail=bool(options.get("embed_thumbnail", False)),
        embed_metadata=bool(options.get("embed_metadata", False)),
    )
    if data.get("status") == "paused":
        job.status = "paused"
    return job


class DownloadJob:
    """One item in the download queue."""

    def __init__(self, url, title, quality, settings_snapshot, **options):
        self.id = uuid.uuid4().hex[:8]
        self.url = url
        self.base_title = title or url   # raw, no quality suffix — what serialize_job saves
        tag = quality_short_label(quality)
        self.title = f"{self.base_title} [{tag}]" if tag else self.base_title
        ttag = trim_tag(options.get("trim"))
        if ttag:
            self.title += f" [{ttag}]"
        self.quality = quality
        self.settings = settings_snapshot
        # Freeze Random once for this queued job so metadata/download requests
        # use the same identity instead of changing halfway through a job.
        if self.settings.get("user_agent_mode") == "random" and not self.settings.get("_resolved_user_agent"):
            self.settings["_resolved_user_agent"] = choose_user_agent(self.settings)
        self.options = options  # subtitles, trim, custom_filename, playlist_mode, ...
        self.status = "queued"  # queued|downloading|paused|completed|error|cancelled
        self.progress = 0.0
        self.speed = ""
        self.speed_bps = 0.0   # numeric speed, for the overall-speed summary
        self.eta = ""
        self.error = None
        self.notes = []        # remarks about what could not be done (e.g. "AVI cannot hold subtitles")
        self.filepath = None
        self.filesize = None
        self._stop_flag = threading.Event()
        self._stop_reason = None
        # Everything below is only read/written while holding
        # DownloadManager._lock, or by the one worker thread running this job.
        #
        # Resume was requested while the previous run was still winding down:
        # that worker re-queues the job itself once it has fully stopped.
        self._resume_pending = False
        # Every path yt-dlp's hooks have reported for this job, across all of
        # its runs (pause/resume included) — kept on the job, not local to a
        # run, so a job cancelled while paused can still be cleaned up.
        self._artifact_paths = set()
        # Files this job proved it created (it saw bytes downloaded into them).
        self._owned_paths = set()
        # Merged / audio-extracted outputs this job's own postprocessors made.
        self._owned_outputs = set()

    # Low-level flag setters. Anything that can race with a worker thread
    # should go through DownloadManager.pause()/cancel() instead, which do
    # the same thing under the manager's lock and keep the queue consistent.
    def pause(self):
        self._stop_reason = "paused"
        self._stop_flag.set()

    def cancel(self):
        self._stop_reason = "cancelled"
        self.status = "cancelled"
        self._stop_flag.set()


class DownloadManager:
    """
    Runs DownloadJobs on a pool of background worker threads — the number
    of workers running at once is capped by `max_concurrent` (1-10, set
    from Settings). `on_update(job)` is invoked from a worker thread every
    time a job's state changes — the caller must marshal any UI work it
    does inside that callback onto the main thread itself (e.g. via
    `after`).

    Threading model: every change to a job's pause/cancel/resume state, to
    the queue and to active_jobs happens while holding self._lock, and so
    does every decision a worker makes about what to do with a job (start
    it, skip it, mark it done/failed/stopped). That makes each of those
    steps atomic with respect to the others — a Pause, Resume or Cancel can
    never interleave halfway through a worker's start/finish bookkeeping.
    on_update() is always called OUTSIDE the lock.
    """

    def __init__(self, on_update, max_concurrent=1, live_settings=None):
        self.on_update = on_update
        self.queue = []
        self.active_jobs = {}   # job.id -> job, currently downloading
        self._workers = []
        self._lock = threading.Lock()
        self._shutting_down = False
        # A live reference to the app's actual settings dict (not a copy).
        # Used only to re-check output_dir right when a download starts —
        # so changing the download folder in Settings affects jobs that
        # were already queued/paused before the change, not just new ones.
        # Everything else (proxy, cookies, aria2c, ...) still comes from
        # each job's own settings snapshot, same as before.
        self.live_settings = live_settings
        self.max_concurrent = max(1, min(10, max_concurrent))

    def set_max_concurrent(self, n):
        """Called from Settings when the person changes the slider/dropdown.
        Lowering it never interrupts jobs already downloading — it only
        affects how many NEW jobs can start at once. Raising it can spin
        up additional workers immediately if the queue is non-empty."""
        with self._lock:
            self.max_concurrent = max(1, min(10, n))
        self._ensure_workers()

    def add_job(self, job):
        with self._lock:
            self.queue.append(job)
        self.on_update(job)
        self._ensure_workers()

    # ------------------------------------------------ pause / resume / cancel
    def pause(self, job):
        """Pause a queued or downloading job. Returns False (and does
        nothing) if the job is in any other state — e.g. it finished a
        moment before the click, which must not turn "completed" into
        "paused". A queued job is taken out of the queue so a later resume
        can never leave two entries for the same job in it."""
        with self._lock:
            if job.status not in ("queued", "downloading"):
                return False
            job._resume_pending = False        # a pause cancels a not-yet-applied resume
            if job in self.queue:
                self.queue.remove(job)
            job.pause()
            job.status = "paused"
        self.on_update(job)
        return True

    def resume(self, job):
        """Resume a paused job. The UI sees "queued" right away.

        If the previous run is still winding down (an external aria2c takes
        a moment to exit after a pause) the stop flag must stay set until
        that worker has really stopped — clearing it early makes the old
        worker ignore the pause and keep downloading, and then the job runs
        a second time on top of it. So in that case nothing is re-queued
        here: the old worker re-queues the job itself in _worker_loop once
        it has fully stopped."""
        with self._lock:
            if job.status != "paused":
                return False
            job.speed = ""
            job.eta = ""
            job.status = "queued"
            wind_down = job.id in self.active_jobs
            if wind_down:
                job._resume_pending = True
            else:
                job._resume_pending = False
                job._stop_flag.clear()
                job._stop_reason = None
                if job not in self.queue:
                    self.queue.append(job)
        self.on_update(job)
        if not wind_down:
            self._ensure_workers()
        return True

    def cancel(self, job):
        """Cancel/dismiss a job from any state. Safe to call for a job that
        is queued, downloading, paused, errored or restored-but-never-started.
        Completed and already-cancelled jobs are left alone."""
        discard_now = False
        with self._lock:
            previous = job.status
            if previous in ("completed", "cancelled"):
                return False
            job._resume_pending = False
            if job in self.queue:
                self.queue.remove(job)
            job.cancel()                      # status="cancelled", stop flag set
            running = job.id in self.active_jobs
            # A running worker removes its own leftovers when it unwinds
            # (see _run_job). When no worker is running, whatever an earlier
            # run left on disk (.part files of a paused job) would stay
            # forever, so clear it here. That includes an ERRORED job: it only
            # gets here when the person dismisses it ("Clear failed", "Remove
            # from list", Delete key) - Retry builds a new job without calling
            # cancel(), so it still finds the partial data. Dismissing used to
            # leave the .part / .ytdl / .fNNN files in the download folder for good.
            discard_now = (not running) and previous in ("paused", "queued", "error")
        self.on_update(job)
        if discard_now and (job._artifact_paths or job._owned_paths):
            threading.Thread(target=_discard_partial_files, args=(job,), daemon=True).start()
        return True

    def shutdown(self):
        """Called when the app quits: stop every running download — and the
        aria2c processes yt-dlp started for them — so nothing keeps running
        (and writing files) after the window is gone. Interrupted jobs are
        flagged as paused; the queue snapshot taken right after restores them
        as paused, exactly like any other job that was mid-download."""
        with self._lock:
            self._shutting_down = True
            for job in list(self.active_jobs.values()):
                job._resume_pending = False
                job.pause()
        # The window is closing; nobody is left to receive UI updates.
        self.on_update = lambda job: None
        terminate_tracked_processes()   # ffmpeg (trim) / aria2c / curl children
        terminate_child_aria2c()

    # ------------------------------------------------------------- workers
    def _ensure_workers(self):
        with self._lock:
            self._workers = [w for w in self._workers if w.is_alive()]
            slots_free = self.max_concurrent - len(self._workers)
            needed = max(0, min(slots_free, len(self.queue)))
            for _ in range(needed):
                w = threading.Thread(target=self._worker_loop, daemon=True)
                self._workers.append(w)
                w.start()

    def _deregister_worker_locked(self):
        me = threading.current_thread()
        self._workers = [w for w in self._workers if w is not me]

    def _worker_loop(self):
        """One worker thread: pulls jobs off the shared queue one at a
        time until it's empty, then exits. Multiple workers running this
        concurrently is what gives us parallel downloads."""
        while True:
            with self._lock:
                if self._shutting_down:
                    self._deregister_worker_locked()
                    return
                if not self.queue or len(self.active_jobs) >= self.max_concurrent:
                    # either nothing left, or we're already at the
                    # concurrency cap (can happen right after the person
                    # lowers max_concurrent) — this worker steps aside.
                    # It removes itself from self._workers in this SAME locked
                    # step: otherwise a job added a moment later would still
                    # count this exiting thread as a free-slot holder, no new
                    # worker would start, and the job would sit in the queue.
                    self._deregister_worker_locked()
                    return
                job = self.queue.pop(0)
                # pause()/cancel() normally take a job out of the queue
                # themselves; this is the belt-and-suspenders check, made
                # in the same locked step as the pop so nothing can slip in
                # between "decided to run it" and "marked it active".
                skip = job.status in ("cancelled", "paused") or job._stop_flag.is_set()
                if not skip:
                    self.active_jobs[job.id] = job
            if skip:
                self.on_update(job)
                continue

            try:
                self._run_job(job)
            finally:
                # belt-and-suspenders: _run_job already catches everything
                # itself, but this guarantees active_jobs can never leak
                # an entry even if that ever changes.
                with self._lock:
                    self.active_jobs.pop(job.id, None)
                    # Resume was clicked while this run was still stopping:
                    # now that it has fully stopped, hand the job back to the
                    # queue (this loop, or another worker, picks it up).
                    if job._resume_pending:
                        job._resume_pending = False
                        if job.status == "queued":
                            job._stop_flag.clear()
                            job._stop_reason = None
                            if job not in self.queue:
                                self.queue.append(job)

    # --------------------------------------------------- state bookkeeping
    @staticmethod
    def _settle_stopped_locked(job):
        """The run was stopped by pause/cancel — set the matching status.
        Caller must hold self._lock."""
        reason = job._stop_reason or "cancelled"
        if reason == "paused" and job._resume_pending:
            job.status = "queued"       # Resume already clicked; shown as waiting
        else:
            job.status = reason

    def _conclude(self, job, outcome, error=None):
        """Atomically record how a run ended: "completed" or "error" — unless a
        pause/cancel got there first, in which case that wins. Doing this under
        the lock is what stops a late "error"/"completed" write from
        overwriting a Cancel and resurrecting the job's card in the UI."""
        with self._lock:
            if job._stop_flag.is_set():
                self._settle_stopped_locked(job)
            elif outcome == "completed":
                job.status = "completed"
            else:
                job.status = "error"
                job.error = error

    def _run_job(self, job):
        """Runs one job on this worker thread and always leaves it with a
        final status — see _conclude. A cancelled job's leftovers are removed
        here, after the download and its aria2c child have fully stopped."""
        try:
            self._run_job_inner(job)
        except Exception as exc:
            # _run_job_inner's own try/except only covers the actual
            # yt-dlp download call — a setup-phase failure before that
            # (can't create the output dir, a malformed quality dict,
            # etc.) would otherwise propagate straight out of the
            # worker thread, leaving the job stuck showing "downloading"
            # forever with no error and never freed from active_jobs.
            self._conclude(job, "error", str(exc))
            self.on_update(job)
        finally:
            if job.status == "cancelled":
                _discard_partial_files(job)

    def _run_job_inner(self, job):
        with self._lock:
            if job._stop_flag.is_set():
                # Paused/cancelled between being picked and actually starting.
                # Don't flip the status to "downloading" over the top of it.
                self._settle_stopped_locked(job)
                stopped_early = True
            else:
                stopped_early = False
                job.status   = "downloading"
                job.progress = 0.0
                job.speed    = ""
                job.eta      = ""
        self.on_update(job)
        if stopped_early:
            return

        opts = base_ydl_opts(job.settings, job.url)
        # Re-check the CURRENT output directory right now, not the one
        # frozen into job.settings when this job was first created —
        # otherwise changing the download folder in Settings would only
        # affect brand-new jobs, not ones that were already queued or
        # paused before the change (which is what people actually expect).
        # A folder the person picked for THIS download on the Download page
        # (output_dir_override) always wins; the live Settings folder only
        # applies to jobs that did not choose their own.
        override = (job.options.get("output_dir_override") or "").strip()
        if override:
            out_dir = override
        elif self.live_settings is not None:
            out_dir = self.live_settings.get("output_dir") or job.settings.get("output_dir") or "."
        else:
            out_dir = job.settings.get("output_dir") or "."
        _ensure_output_dir(out_dir)

        quality = job.quality

        trim = job.options.get("trim")
        trim_enabled = bool(trim and trim.get("enabled"))
        if trim_enabled and not resolve_ffmpeg_path(job.settings):
            raise RuntimeError(
                "VidDown could not locate ffmpeg for trimming. The packaged build should "
                "include it; please rebuild/reinstall the app package or set a valid "
                "FFmpeg Path in Settings."
            )
        if trim_enabled:
            try:
                from yt_dlp.utils import download_range_func
                opts["download_ranges"] = download_range_func(
                    None, [(trim["start_seconds"], trim["end_seconds"])]
                )
                opts["force_keyframes_at_cuts"] = True
            except ImportError:
                trim_enabled = False  # bundled yt-dlp too old for trimming; download continues without it

        # The trim range is part of the file name: otherwise a trimmed download
        # and a full download of the same video (or two different trims) share
        # one name, and yt-dlp skips the download because "the file already
        # exists" — the user then gets the OLD file (often the full video).
        ttag = trim_tag(trim) if trim_enabled else ""
        filename_tmpl = job.options.get("custom_filename")
        if not filename_tmpl:
            tag = quality_short_label(quality)
            filename_tmpl = "%(title)s"
            if tag:
                filename_tmpl += f" [{tag}]"
            if ttag:
                filename_tmpl += f" [{ttag}]"
            filename_tmpl += ".%(ext)s"
        elif ttag and ttag not in filename_tmpl:
            head, sep, tail = filename_tmpl.rpartition(".%(ext)s")
            filename_tmpl = f"{head} [{ttag}]{sep}{tail}" if sep else f"{filename_tmpl} [{ttag}]"
        opts["outtmpl"] = os.path.join(out_dir, filename_tmpl)

        opts["format"] = quality["format_selector"]

        # Container / audio format / subtitles / metadata / cover: what the
        # finished file should look like (see core/output_options.py).
        plan = oo.plan_postprocessing(quality, job.options)
        if plan["merge_output_format"]:
            opts["merge_output_format"] = plan["merge_output_format"]
        if plan["subtitles"]:
            opts["writesubtitles"]  = True
            opts["subtitleslangs"]  = plan["subtitles"]
            # prefer the format the target container takes without conversion
            opts["subtitlesformat"] = "vtt/best" if plan["subtitle_format"] == "vtt" else "srt/vtt/best"
        if plan["writethumbnail"]:
            opts["writethumbnail"] = True
        opts["postprocessors"] = plan["postprocessors"]
        job.notes = list(plan["notes"])

        # an individual video URL is passed in, so a playlist never arrives here
        opts["noplaylist"] = True

        # aria2c (external downloader) fetches the WHOLE file itself,
        # completely unaware of yt-dlp's own download_ranges/
        # force_keyframes_at_cuts set above — handing it a trimmed job
        # would silently download the entire video instead of just the
        # trimmed part, ignoring the trim range entirely. A trimmed
        # download always uses yt-dlp's own native downloader instead,
        # the only one that actually understands download_ranges — the
        # aria2c speed boost only applies to non-trimmed downloads.
        aria2c_path = usable_aria2c()
        if not trim_enabled and aria2c_path and job.settings.get("aria2c_enabled", True):
            opts["external_downloader"] = aria2c_path
            # Keyed "aria2c", not "default": yt-dlp applies the "default" args
            # to EVERY external downloader, so when it fell back to ffmpeg for
            # a format, ffmpeg was handed aria2c's "-x 16 -k 1M" and failed.
            opts["external_downloader_args"] = {
                "aria2c": [
                    "-x", "16", "-k", "1M",
                    # aria2c's default progress summary is every 60s — far
                    # too slow for a live UI (short videos finish before
                    # a single update arrives). 1s makes progress/speed
                    # actually show up, and also makes pause/cancel react
                    # in ~1s instead of up to a minute.
                    "--summary-interval=1",
                ]
            }

        # A trimmed download is one long ffmpeg run during which yt-dlp reports
        # no progress at all (the bar used to sit at 0% until it finished).
        # Ask ffmpeg to log its progress to a job-unique file we poll below.
        trim_progress_file = None
        if trim_enabled:
            trim_progress_file = os.path.join(
                tempfile.gettempdir(), f"viddown_{job.id}_{uuid.uuid4().hex[:6]}.ffprogress"
            )
            opts["external_downloader_args"] = {
                "ffmpeg": ["-progress", trim_progress_file, "-nostats"]
            }

        rate = _parse_rate_limit(job.settings.get("speed_limit"))
        if rate:
            opts["ratelimit"] = rate

        import time as _time

        _phase    = [0]       # 0=video, 1=audio
        _multi    = [None]    # True: separate video+audio streams get merged; False: one file
        _last_upd = [0.0]     # throttle timestamp
        _ffmpeg_done = [False]  # trimmed download: ffmpeg has finished writing the clip

        def _scale(pct):
            """Map one stream's 0..1 progress onto the job bar. Two streams
            (video then audio) share 0-92%; a single file / audio-only
            download uses the whole 0-92% instead of stalling at 60%."""
            if _multi[0]:
                return round(pct * 60, 1) if _phase[0] == 0 else round(60 + pct * 32, 1)
            return round(pct * 92, 1)

        def progress_hook(d):
            status = d.get("status")
            if _multi[0] is None:
                inf = d.get("info_dict") or {}
                rf = inf.get("requested_formats")
                # yt-dlp deletes 'requested_formats' from the per-stream
                # info_dict it hands to the hooks, so for a normal video+audio
                # download the line above alone is always False and the bar
                # used to run 0->92% for the video and then again 0->92% for
                # the audio. A stream that has exactly one of video/audio
                # (the other codec is "none") means the other half follows
                # and the two get merged.
                vc, ac = inf.get("vcodec"), inf.get("acodec")
                one_sided = (vc is not None and ac is not None
                             and (vc == "none") != (ac == "none"))
                _multi[0] = bool(rf and len(rf) > 1) or (
                    one_sided and not job.quality.get("audio_only"))
            for path in (d.get("filename"), d.get("tmpfilename")):
                if path:
                    job._artifact_paths.add(os.path.abspath(path))
            # Bytes arriving for a path means THIS job is creating that file
            # (an already-complete file yt-dlp skips never reports
            # "downloading"). Only such files may be deleted on Cancel.
            if status == "downloading" and d.get("filename"):
                job._owned_paths.add(os.path.abspath(d["filename"]))
            # A trimmed clip is written by ffmpeg, which never reports
            # "downloading" — its single "finished" event is the only proof
            # that this job created the file (yt-dlp skips existing files
            # without any hook). Without this a cancelled trim left its
            # finished clip behind in the download folder.
            if status == "finished" and trim_enabled and d.get("filename"):
                _ffmpeg_done[0] = True
                job._owned_paths.add(os.path.abspath(d["filename"]))
            # DownloadCancelled, NOT DownloadError: yt-dlp's fragment loop
            # swallows DownloadError and moves on to the next fragment (see
            # the long comment above CancelDownload).
            if job._stop_flag.is_set():
                raise CancelDownload("Cancelled by user")

            if status == "downloading":
                total      = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes") or 0

                if total > 0:
                    job.progress = _scale(downloaded / total)
                else:
                    # aria2c (external downloader) doesn't always report
                    # total_bytes/downloaded_bytes reliably through yt-dlp's
                    # parser — without this fallback the bar just sits
                    # frozen at 0% even though the download is progressing.
                    # yt-dlp still computes its own formatted percentage
                    # string in most cases, so use that instead.
                    percent_str = (d.get("_percent_str") or "").strip().rstrip("%")
                    if percent_str:
                        try:
                            job.progress = _scale(float(percent_str) / 100)
                        except ValueError:
                            pass

                speed_raw = d.get("speed")
                job.speed_bps = float(speed_raw or 0)
                if speed_raw is not None:
                    job.speed = human_size(speed_raw) + "/s"
                else:
                    job.speed = (d.get("_speed_str") or "").strip()

                eta_raw = d.get("eta")
                if eta_raw is not None:
                    m, s = divmod(int(eta_raw), 60)
                    job.eta = f"{m:02d}:{s:02d}"
                else:
                    job.eta = (d.get("_eta_str") or "").strip()

                # throttle: max ~10 UI updates/sec so Tkinter can repaint
                now = _time.monotonic()
                if now - _last_upd[0] >= 0.1:
                    _last_upd[0] = now
                    self.on_update(job)

            elif status == "finished":
                _phase[0] += 1
                # Best-effort placeholder — this is the per-stream file
                # (e.g. the standalone audio track before it gets merged
                # into the video, or the pre-conversion file before audio
                # extraction), not the final output. postprocessor_hook
                # below overwrites this with the real final path once
                # merging/extraction/subtitle-embedding actually happens;
                # this progress_hooks value only ends up being the one
                # that sticks when there's no postprocessing to do at all
                # (a single-file download with no merge, no subtitles,
                # not audio-only) — in that case it's already correct.
                job.filepath = d.get("filename")
                job.speed    = ""
                # "Merging…" only when another step really follows (merge,
                # audio extraction, subtitle embedding).
                more_steps = bool(_multi[0] or plan["postprocessors"] or plan["embed_thumbnail"])
                job.eta      = "Merging…" if more_steps else ""
                # A trimmed clip arrives as ONE ffmpeg output (video+audio
                # already together), so its only "finished" event must not
                # drop the bar back to the 60% "video done" mark.
                job.progress = 60.0 if (_multi[0] and _phase[0] == 1 and not trim_enabled) else 92.0
                self.on_update(job)

        def postprocessor_hook(d):
            status = d.get("status")
            # Merging/converting never calls progress_hook, so a Cancel that
            # arrives in between would otherwise run every remaining
            # postprocessing step to completion. Stop before the next one
            # starts. (Pause is left alone on purpose: finishing the merge
            # costs seconds and leaves a complete file for Resume.)
            if (status == "started" and job._stop_flag.is_set()
                    and job._stop_reason == "cancelled"):
                raise CancelDownload("Cancelled by user")
            # Runs after each postprocessing step (merger, subtitle
            # converter/embedder, audio extractor — there can be several
            # in sequence). info_dict['filepath'] reflects the file as it
            # exists *after* that step, so simply always taking the latest
            # one means job.filepath ends up as the truly final path once
            # every step has run — this is what fixed the History "Open"
            # button pointing at an already-deleted intermediate file.
            if status == "finished":
                info = d.get("info_dict") or {}
                fp = info.get("filepath")
                if fp:
                    job.filepath = fp
                    # A merge / audio extraction that ran in this job, on
                    # streams this job downloaded, produced a file that is
                    # ours too — Cancel must be able to remove it.
                    if d.get("postprocessor") in ("Merger", "ExtractAudio", "VideoRemuxer") and job._owned_paths:
                        job._owned_outputs.add(os.path.abspath(fp))

        opts["progress_hooks"] = [progress_hook]
        opts["postprocessor_hooks"] = [postprocessor_hook]

        watcher_stop = threading.Event()
        worker_ident = threading.get_ident()
        clip_len = max(1, int(trim["end_seconds"]) - int(trim["start_seconds"])) if trim_enabled else 0
        run_started = time.monotonic()

        def _update_trim_progress(state):
            """Show real progress for a trimmed (ffmpeg) download."""
            info = _read_ffmpeg_progress(trim_progress_file)
            raw = info.get("out_time_us") or info.get("out_time_ms") or ""
            try:
                done = int(raw) / 1_000_000          # seconds of clip written so far
            except ValueError:
                return
            if done <= 0:
                return
            frac = min(done / clip_len, 0.99)
            job.progress = round(frac * 90, 1)
            speed = (info.get("speed") or "").strip()
            job.speed = speed if speed.endswith("x") else ""
            elapsed = time.monotonic() - run_started
            if frac > 0.02 and elapsed > 1:
                m, s = divmod(int(elapsed * (1 - frac) / frac), 60)
                job.eta = f"{m:02d}:{s:02d}"
            now = time.monotonic()
            if now - state["last_push"] >= 0.3:
                state["last_push"] = now
                self.on_update(job)

        def _watch_job_processes():
            # yt-dlp runs ffmpeg / aria2c with a blocking wait during which no
            # progress hook fires, so a Cancel/Pause can't interrupt it from
            # inside the hook. This watcher terminates exactly the child
            # processes this job's download thread started, then yt-dlp sees
            # the child exit and unwinds.
            state = {"last_push": 0.0}
            while not watcher_stop.wait(0.20):
                if job._stop_flag.is_set():
                    # Pause lets a running merge finish (seconds, and it leaves
                    # a complete file for Resume); Cancel stops everything.
                    _kill_tracked(worker_ident,
                                  include_post=(job._stop_reason != "paused"))
                    if not PROCESS_TRACKING and trim_progress_file:
                        _terminate_by_marker(trim_progress_file)
                    # Older fallback: match aria2c by output dir + file names.
                    if aria2c_path and job.settings.get("aria2c_enabled", True) and not trim_enabled:
                        _terminate_matching_aria2c(out_dir, job._artifact_paths)
                elif trim_progress_file and not _ffmpeg_done[0]:
                    try:
                        _update_trim_progress(state)
                    except Exception:
                        pass   # progress is cosmetic — never kill the watcher

        watcher = threading.Thread(target=_watch_job_processes, daemon=True)
        watcher.start()
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                if plan["embed_thumbnail"]:
                    try:
                        from core import cover_embed  # imports yt-dlp: load it only when needed
                        cover_embed.attach(ydl)
                    except Exception as exc:          # a cover is a decoration, not a reason to fail
                        errorlog.warning("download", f"Cover embedding unavailable: {exc}")
                        job.notes = list(getattr(job, "notes", None) or []) + [
                            "The cover picture could not be embedded."]
                ydl.download([job.url])
            self._conclude(job, "completed")
            if job.status == "completed" and job.filepath and os.path.exists(job.filepath):
                try:
                    job.filesize = os.path.getsize(job.filepath)
                except OSError:
                    pass   # size is cosmetic — never turn a finished download into an error
        except Exception as exc:
            self._conclude(job, "error", str(exc))
        finally:
            watcher_stop.set()
            watcher.join(timeout=1.0)
            # Temp files of any killed ffmpeg/aria2c child → cleanup list.
            job._artifact_paths.update(os.path.abspath(p) for p in _forget_tracked(worker_ident))
            if trim_progress_file:
                try:
                    os.remove(trim_progress_file)
                except OSError:
                    pass
        self.on_update(job)
