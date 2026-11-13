"""
core/updater.py
yt-dlp update logic — runs in a background thread, never blocks the UI —
plus status/update helpers for ffmpeg, ffprobe, and aria2c, which are
external binaries rather than something this module downloads; see
get_tool_status() and open_apt_upgrade_terminal() below.

How the yt-dlp update works (it must work inside the PyInstaller .deb,
where the system Python / pip are irrelevant to what the app imports):
the newest release wheel is fetched from PyPI, checked against the sha256
PyPI publishes for it, and unpacked into core.ytdlp_loader.OVERRIDE_DIR —
together with the yt-dlp-ejs wheel that exact yt-dlp release pins (YouTube's
JavaScript challenge solver).
main.py makes the app prefer that copy over the bundled one at the next
start, so an update takes effect after restarting VidDown. Nothing is
installed into the system Python.
"""
import datetime
import hashlib
import io
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import zipfile

from core import errorlog, ytdlp_loader
from core.platform_utils import IS_WINDOWS, no_window_kwargs

_PYPI_URL = "https://pypi.org/pypi/yt-dlp/json"


def get_ytdlp_version() -> str:
    """Version of the yt-dlp this process is actually running."""
    try:
        import yt_dlp.version
        return getattr(yt_dlp.version, "__version__", "unknown")
    except Exception:
        return "unknown"


def should_update(settings: dict) -> bool:
    """
    Returns True if enough time has passed since the last update
    and auto-update is enabled in settings.
    """
    if not settings.get("ytdlp_autoupdate", True):
        return False

    interval_days = int(settings.get("ytdlp_update_interval", 7))
    last_str      = settings.get("ytdlp_last_updated", "")

    if not last_str:
        return True  # never updated before

    try:
        last_dt = datetime.datetime.fromisoformat(last_str)
        return (datetime.datetime.now() - last_dt).days >= interval_days
    except (ValueError, TypeError):
        return True


def mark_updated(settings: dict, save_fn):
    """Stamp the current time as last-updated and persist settings."""
    settings["ytdlp_last_updated"] = datetime.datetime.now().isoformat()
    save_fn(settings)


def _http_get(url, timeout=30):
    """GET with the system CA store first; if that fails certificate
    verification (frozen builds can lack one), retry with certifi's bundle."""
    req = urllib.request.Request(url, headers={"User-Agent": "VidDown-updater"})
    contexts = [ssl.create_default_context()]
    try:
        import certifi
        contexts.append(ssl.create_default_context(cafile=certifi.where()))
    except Exception:
        pass
    last_exc = None
    for ctx in contexts:
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                return resp.read()
        except (ssl.SSLError, urllib.error.URLError) as exc:
            reason = getattr(exc, "reason", exc)
            if not isinstance(reason, ssl.SSLError):
                raise
            last_exc = exc
    raise last_exc


def _version_key(text):
    return tuple(int(p) for p in re.findall(r"\d+", text or ""))


def _python_ok(requires):
    """requires like '>=3.10' — True if this interpreter satisfies it."""
    m = re.match(r"\s*>=\s*(\d+)\.(\d+)", requires or "")
    return not m or sys.version_info[:2] >= (int(m.group(1)), int(m.group(2)))


def _install_wheels(wheels):
    """Unpack packages from wheels into OVERRIDE_DIR atomically: build next to
    it, then swap, so a failure never leaves a half-written copy.

    wheels: [(wheel_bytes, package_dir_name), ...] — e.g. (blob, "yt_dlp")."""
    target = ytdlp_loader.OVERRIDE_DIR
    staging, old = target + ".new", target + ".old"
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging)
    for wheel_bytes, pkg in wheels:
        prefix = pkg + "/"
        pkg_root = os.path.join(staging, pkg) + os.sep
        with zipfile.ZipFile(io.BytesIO(wheel_bytes)) as zf:
            for name in zf.namelist():
                # only the package itself; refuse absolute paths / traversal
                if not name.startswith(prefix) or name.endswith("/"):
                    continue
                dest = os.path.normpath(os.path.join(staging, name))
                if not dest.startswith(pkg_root):
                    continue
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with zf.open(name) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)
    if not os.path.isfile(os.path.join(staging, "yt_dlp", "__init__.py")) or \
            not os.path.isfile(os.path.join(staging, "yt_dlp", "version.py")):
        shutil.rmtree(staging, ignore_errors=True)
        raise RuntimeError("downloaded wheel does not contain a complete yt_dlp package")
    # remember which VidDown version downloaded this (see ytdlp_loader.install)
    from version import __version__
    with open(os.path.join(staging, ytdlp_loader.APP_VERSION_MARKER), "w", encoding="utf-8") as f:
        f.write(__version__)
    shutil.rmtree(old, ignore_errors=True)
    if os.path.isdir(target):
        os.replace(target, old)
    os.replace(staging, target)
    shutil.rmtree(old, ignore_errors=True)


def _pinned_ejs_version(requires_dist):
    """The yt-dlp-ejs version a yt-dlp release pins for its "default" extra
    (PyPI's requires_dist), or None. E.g. 'yt-dlp-ejs==0.8.0; extra == "default"'."""
    for line in requires_dist or []:
        m = re.match(r"\s*yt[-_]dlp[-_]ejs\s*==\s*([0-9][\w.]*)", line, re.I)
        if m and "default" in line:
            return m.group(1)
    return None


def _fetch_verified_wheel(project, version):
    """Download project==version's pure-Python wheel from PyPI and check it
    against the sha256 PyPI lists for it. Returns the wheel's bytes."""
    meta = json.loads(_http_get(f"https://pypi.org/pypi/{project}/{version}/json", timeout=20))
    wheel = next((f for f in meta["urls"]
                  if f.get("packagetype") == "bdist_wheel"
                  and f.get("filename", "").endswith("-py3-none-any.whl")), None)
    if not wheel:
        raise RuntimeError(f"no {project} {version} wheel found on PyPI")
    blob = _http_get(wheel["url"], timeout=120)
    if hashlib.sha256(blob).hexdigest() != wheel["digests"]["sha256"]:
        raise RuntimeError(f"{project} download did not match PyPI's checksum")
    return blob


def update_ytdlp(on_done=None, settings=None, save_fn=None):
    """
    Downloads the newest yt-dlp release into the user-writable override
    folder in a background thread (see module docstring).

    on_done(success: bool, message: str) — caller must marshal
    to the UI thread via after() if needed.

    settings + save_fn: if provided, stamps ytdlp_last_updated on success.
    """
    def _finish(ok, msg):
        if not ok:
            errorlog.warning("updater", msg)
        if on_done:
            on_done(ok, msg)

    def _run():
        try:
            data = json.loads(_http_get(_PYPI_URL, timeout=20))
            latest = data["info"]["version"]
            wheel = next((f for f in data["urls"]
                          if f.get("packagetype") == "bdist_wheel"
                          and f.get("filename", "").endswith("-py3-none-any.whl")), None)
            if not wheel:
                _finish(False, "Update failed: no yt-dlp wheel found on PyPI.")
                return
            if not _python_ok(data["info"].get("requires_python")):
                _finish(False, f"Update skipped: yt-dlp {latest} needs Python "
                               f"{data['info'].get('requires_python')}.")
                return

            have = ytdlp_loader.override_version() or get_ytdlp_version()
            if _version_key(have) >= _version_key(latest):
                if settings is not None and save_fn is not None:
                    mark_updated(settings, save_fn)
                _finish(True, f"yt-dlp is already up to date (v{have}).")
                return

            blob = _http_get(wheel["url"], timeout=120)
            if hashlib.sha256(blob).hexdigest() != wheel["digests"]["sha256"]:
                _finish(False, "Update failed: downloaded file did not match PyPI's checksum.")
                return
            wheels = [(blob, "yt_dlp")]
            note = ""
            ejs_version = _pinned_ejs_version(data["info"].get("requires_dist"))
            if ejs_version:
                try:
                    wheels.append((_fetch_verified_wheel("yt-dlp-ejs", ejs_version), "yt_dlp_ejs"))
                except Exception as exc:
                    # yt-dlp itself still updates; YouTube may keep using the
                    # solver bundled with this VidDown until the next try.
                    errorlog.warning("updater", f"yt-dlp-ejs {ejs_version} not updated: {exc}")
                    note = " (the YouTube JS solver could not be updated)"
            _install_wheels(wheels)
            if settings is not None and save_fn is not None:
                mark_updated(settings, save_fn)
            _finish(True, f"yt-dlp v{latest} downloaded{note} — restart VidDown to use it.")
        except Exception as exc:
            _finish(False, f"Update failed: {str(exc)[:200]}")

    threading.Thread(target=_run, daemon=True, name="ytdlp-updater").start()


# --------------------------------------------------- ffmpeg / aria2c tools --
# Unlike yt-dlp (a pip package we control end-to-end above), ffmpeg,
# ffprobe, and aria2c are external binaries core/downloader.py's
# find_binary() locates two possible ways:
#   - "bundled": a static binary shipped inside the app itself (bin/ at
#     packaging time — see bin/README.txt). This app has no control over
#     that copy at runtime; it only ever changes by shipping a new
#     VidDown release with a newer one in bin/.
#   - "system": whatever the OS's own PATH provides (installed via apt).
#     Updating that is the OS package manager's job, which needs the
#     person's own root password — this app deliberately never runs a
#     privileged command on its own. The best it can safely do is report
#     what's currently there and, on request, open a terminal with the
#     right apt command already typed in, for the person to review and
#     confirm themselves.
def _tool_version(path, version_flag):
    """First line of `<path> <version_flag>`'s output, or None."""
    if not path:
        return None
    try:
        result = subprocess.run(
            [path, version_flag], capture_output=True, text=True, timeout=8,
            **no_window_kwargs(),
        )
        for line in (result.stdout or result.stderr or "").splitlines():
            line = line.strip()
            if line:
                return line
    except Exception:
        pass
    return None


def get_tool_status():
    """
    Live status for ffmpeg/ffprobe/aria2c — re-checks find_binary() fresh
    each call (not the module-level constants in core/downloader.py,
    which are only computed once at import time and would go stale if
    e.g. the person installs one of these mid-session).

    Returns {name: {"path": str|None, "source": "bundled"|"system"|"missing",
                     "version": str|None}}
    """
    from core.downloader import app_base_dir, find_binary
    bundled_dir = os.path.abspath(os.path.join(app_base_dir(), "bin"))

    def _status(name, version_flag):
        path = find_binary(name)
        if not path:
            return {"path": None, "source": "missing", "version": None}
        source = "bundled" if os.path.dirname(os.path.abspath(path)) == bundled_dir else "system"
        return {"path": path, "source": source, "version": _tool_version(path, version_flag)}

    return {
        "ffmpeg":  _status("ffmpeg", "-version"),
        "ffprobe": _status("ffprobe", "-version"),
        "aria2c":  _status("aria2c", "--version"),
    }


def get_js_runtime_status():
    """The JavaScript runtime yt-dlp will use for YouTube, found the same way
    core.downloader.js_runtime_opts() picks it.

    Returns {"name": str|None, "path": str|None,
             "source": "bundled"|"system"|"missing", "version": str|None}"""
    from shutil import which
    from core.downloader import find_bundled_binary
    from core.platform_utils import exe_name
    path = find_bundled_binary("deno")
    if path:
        return {"name": "deno", "path": path, "source": "bundled",
                "version": _tool_version(path, "--version")}
    for name in ("deno", "node", "bun"):
        path = which(exe_name(name))
        if path:
            return {"name": name, "path": path, "source": "system",
                    "version": _tool_version(path, "--version")}
    return {"name": None, "path": None, "source": "missing", "version": None}


def open_apt_upgrade_terminal():
    """
    Opens a terminal running `sudo apt update && sudo apt install
    --only-upgrade ffmpeg aria2c`, for the person to type their own
    password and watch it run — never launched with a password embedded,
    never run hidden/silently. Tries a few common terminal emulators;
    x-terminal-emulator is Debian/Ubuntu's own alternatives-system
    symlink, so it's tried first since this app already targets Debian
    specifically (see build_deb.sh).

    Returns True if a terminal was actually launched (not whether the
    apt command itself succeeded — that's up to what the person does
    inside it). Always False on Windows, where there is no apt (the tools
    are bundled and update with a new VidDown release).
    """
    if IS_WINDOWS:
        return False
    cmd = (
        "echo 'Updating ffmpeg and aria2c via apt (this needs your sudo password)...'; "
        "sudo apt update && sudo apt install --only-upgrade ffmpeg aria2c; "
        "echo; read -p 'Done — press Enter to close this window...'"
    )
    terminal_commands = [
        ["x-terminal-emulator", "-e", "bash", "-c", cmd],
        ["gnome-terminal", "--", "bash", "-c", cmd],
        ["konsole", "-e", "bash", "-c", cmd],
        ["xfce4-terminal", "-e", f'bash -c "{cmd}"'],
        ["xterm", "-e", "bash", "-c", cmd],
    ]
    for args in terminal_commands:
        try:
            subprocess.Popen(args)
            return True
        except (FileNotFoundError, OSError):
            continue
    return False
