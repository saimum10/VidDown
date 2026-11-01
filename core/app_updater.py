"""
core/app_updater.py
Updates VidDown itself from the GitHub releases of github.com/saimum10/VidDown.
(The yt-dlp engine has its own, separate updater in core/updater.py.)

Flow, all of it off the UI thread — the caller (ui/pages/about_page.py) starts
these functions on a worker thread and polls for the result:

    release = fetch_latest_release()              # GitHub API: releases/latest
    is_newer(release.version, __version__)        # numeric, 2.9.0.1 < 2.9.1
    asset   = pick_asset(release, kind, variant)  # the right .deb / Setup .exe
    sha     = fetch_expected_sha256(release, asset)
    path    = download_asset(asset, sha, progress)  # streamed, hash-checked
    install_update(path, kind)                    # pkexec apt / silent Inno Setup

Release asset names are the contract between this module and the build
scripts (build_deb.sh, build_windows.ps1, windows/viddown.iss):

    viddown_<version>-1_<arch>_with-js.deb      viddown_<version>-1_<arch>_no-js.deb
    VidDown_Setup_<version>_x64_with-js.exe     VidDown_Setup_<version>_x64_no-js.exe
    SHA256SUMS.txt                              (written by the release job)

Nothing is installed unless the download's SHA-256 matches the one published
with the release. (That guards against a damaged or tampered download; it is
not a code signature.)

Standard library only (plus core.* helpers) — no Tkinter, no yt-dlp.
"""
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import ssl
import subprocess
import sys
import urllib.error
import urllib.request

from core import buildinfo, errorlog
from core.platform_utils import IS_LINUX, IS_WINDOWS, data_dir
from core.updater import _http_get
from version import __version__

REPO = "saimum10/VidDown"
LATEST_API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE_URL = f"https://github.com/{REPO}/releases/latest"
CHECKSUM_ASSET_NAME = "SHA256SUMS.txt"
UPDATES_DIR = os.path.join(data_dir(), "updates")

# how this copy of VidDown was installed (see install_kind())
KIND_DEB = "deb"
KIND_WIN_INSTALLER = "windows-installer"
KIND_WIN_PORTABLE = "windows-portable"
KIND_SOURCE = "source"
KIND_OTHER = "other"

# install_update() outcomes
OUTCOME_RESTART = "restart"          # installed; restart VidDown to use it
OUTCOME_APP_CLOSES = "app-closes"    # the installer is running; VidDown must quit now
OUTCOME_TERMINAL = "terminal"        # the person finishes the install in a terminal
OUTCOME_CANCELLED = "cancelled"      # the person dismissed the password prompt


class UpdateError(Exception):
    """Something went wrong; str(exc) is written for the person to read."""


class Cancelled(UpdateError):
    """The download was cancelled by the person."""


# ------------------------------------------------------------------ versions
def parse_version(text):
    """'v2.9.0.1' -> (2, 9, 0, 1). Anything non-numeric (a '-beta' tail) is ignored."""
    return tuple(int(p) for p in re.findall(r"\d+", (text or "").lstrip("vV").split("-", 1)[0]))


def is_newer(remote, local):
    """True if `remote` is a higher version than `local`. Missing trailing parts
    count as zero, so 2.9 == 2.9.0 and 2.9.1 > 2.9.0.1."""
    a, b = parse_version(remote), parse_version(local)
    if not a:
        return False
    width = max(len(a), len(b))
    pad = lambda t: t + (0,) * (width - len(t))
    return pad(a) > pad(b)


# ------------------------------------------------------------------ releases
class ReleaseInfo:
    def __init__(self, data):
        self.tag = data.get("tag_name") or ""
        self.version = self.tag.lstrip("vV")
        self.name = data.get("name") or self.tag
        self.url = data.get("html_url") or RELEASES_PAGE_URL
        self.notes = (data.get("body") or "").strip()
        self.published = (data.get("published_at") or "")[:10]
        self.assets = [a for a in (data.get("assets") or []) if a.get("name")]

    def asset(self, name):
        for a in self.assets:
            if a["name"].lower() == name.lower():
                return a
        return None


def fetch_latest_release():
    """The newest published (non-draft, non-prerelease) GitHub release."""
    try:
        data = json.loads(_http_get(LATEST_API_URL, timeout=20))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("No VidDown release has been published on GitHub yet.")
        if exc.code in (403, 429):
            raise UpdateError("GitHub is limiting requests from this network right now — "
                              "try again in a little while.")
        raise UpdateError(f"GitHub answered with an error (HTTP {exc.code}).")
    except (urllib.error.URLError, OSError, TimeoutError):
        raise UpdateError("Could not reach GitHub. Check your internet connection.")
    except ValueError:
        raise UpdateError("GitHub sent a reply VidDown could not read.")
    release = ReleaseInfo(data)
    if not parse_version(release.version):
        raise UpdateError(f"The latest release has an unusable version tag ({release.tag!r}).")
    return release


# ------------------------------------------------- what is installed, what fits
def install_kind():
    """How this VidDown was installed, which decides how it can be updated."""
    if not getattr(sys, "frozen", False):
        return KIND_SOURCE
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    if IS_WINDOWS:
        # Inno Setup puts its uninstaller (unins000.exe) next to the program;
        # the portable zip has none.
        return KIND_WIN_INSTALLER if os.path.isfile(os.path.join(exe_dir, "unins000.exe")) \
            else KIND_WIN_PORTABLE
    if IS_LINUX and exe_dir.startswith("/usr/lib/viddown"):
        return KIND_DEB
    return KIND_OTHER


def machine_arch():
    """Debian-style architecture name of this machine."""
    m = platform.machine().lower()
    return {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(m, m)


def pick_asset(release, kind, variant, arch=None):
    """The release file to download for this install kind + flavour (with-js /
    no-js), or None when the release has no matching file."""
    if kind == KIND_DEB:
        rx = re.compile(rf"^viddown_[^_]+_{re.escape(arch or machine_arch())}_{re.escape(variant)}\.deb$", re.I)
    elif kind == KIND_WIN_INSTALLER:
        rx = re.compile(rf"^VidDown_Setup_[^_]+_x64_{re.escape(variant)}\.exe$", re.I)
    else:
        return None
    return next((a for a in release.assets if rx.match(a["name"])), None)


# ----------------------------------------------------------------- checksums
def parse_checksums(text):
    """sha256sum-style text -> {file name: lowercase hex digest}."""
    out = {}
    for line in (text or "").splitlines():
        m = re.match(r"^\s*([0-9a-fA-F]{64})\s+\*?(.+?)\s*$", line)
        if m:
            out[m.group(2)] = m.group(1).lower()
    return out


def fetch_expected_sha256(release, asset):
    """The SHA-256 GitHub publishes for `asset`: from the release's
    SHA256SUMS.txt, else from the digest GitHub itself computed at upload.
    Refuses (UpdateError) when neither exists — nothing unverified is installed."""
    sums = release.asset(CHECKSUM_ASSET_NAME)
    if sums:
        try:
            text = _http_get(sums["browser_download_url"], timeout=30).decode("utf-8", "replace")
        except (urllib.error.URLError, OSError, TimeoutError):
            raise UpdateError("Could not download the release's checksum list.")
        digest = parse_checksums(text).get(asset["name"])
        if digest:
            return digest
    digest = (asset.get("digest") or "")
    if digest.lower().startswith("sha256:") and re.fullmatch(r"[0-9a-fA-F]{64}", digest[7:]):
        return digest[7:].lower()
    raise UpdateError("This release has no checksum for the file, so VidDown will not install it. "
                      "Download it from the releases page instead.")


# ------------------------------------------------------------------ download
def _ssl_contexts():
    contexts = [ssl.create_default_context()]
    try:
        import certifi
        contexts.append(ssl.create_default_context(cafile=certifi.where()))
    except Exception:
        pass
    return contexts


def _open(url, timeout=30):
    """urlopen with the system CA store first, certifi's bundle as a fallback
    (same policy as core.updater._http_get, but returns the open response so the
    file can be streamed)."""
    req = urllib.request.Request(url, headers={"User-Agent": "VidDown-updater"})
    last = None
    for ctx in _ssl_contexts():
        try:
            return urllib.request.urlopen(req, timeout=timeout, context=ctx)
        except (ssl.SSLError, urllib.error.URLError) as exc:
            if not isinstance(getattr(exc, "reason", exc), ssl.SSLError):
                raise
            last = exc
    raise last


def _sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download_asset(asset, expected_sha256, progress=None, cancel=None):
    """Download `asset` into the updates folder and return its path.

    progress(done_bytes, total_bytes) is called as data arrives; `cancel` is a
    threading.Event — setting it aborts the download (Cancelled). The file only
    gets its final name after its SHA-256 matches `expected_sha256`; a mismatch
    deletes it and raises UpdateError. An identical, already-verified file from
    an earlier attempt is reused."""
    os.makedirs(UPDATES_DIR, exist_ok=True)
    name = os.path.basename(asset["name"])           # never a path
    final = os.path.join(UPDATES_DIR, name)
    part = final + ".part"
    total = int(asset.get("size") or 0)

    # leftovers of earlier versions / failed attempts
    for entry in os.listdir(UPDATES_DIR):
        if entry != name:
            try:
                os.remove(os.path.join(UPDATES_DIR, entry))
            except OSError:
                pass

    if os.path.isfile(final):
        if _sha256_of(final) == expected_sha256:
            if progress:
                progress(total, total)
            return final
        os.remove(final)

    h = hashlib.sha256()
    done = 0
    try:
        with _open(asset["browser_download_url"], timeout=30) as resp, open(part, "wb") as out:
            total = total or int(resp.headers.get("Content-Length") or 0)
            while True:
                if cancel is not None and cancel.is_set():
                    raise Cancelled("Download cancelled.")
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
            out.flush()
            os.fsync(out.fileno())
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        _remove_quietly(part)
        raise UpdateError(f"Download failed: {exc}")
    except BaseException:
        _remove_quietly(part)
        raise

    if h.hexdigest() != expected_sha256 or (total and done != total):
        _remove_quietly(part)
        raise UpdateError("The downloaded file did not match the published checksum, so it was "
                          "deleted. Try again, or download it from the releases page.")
    os.replace(part, final)
    return final


def _remove_quietly(path):
    try:
        os.remove(path)
    except OSError:
        pass


# ------------------------------------------------------------------- install
# Terminal emulators for the "type your password in a terminal" fallback.
def _terminal_commands(script):
    return [
        ["x-terminal-emulator", "-e", "bash", "-c", script],
        ["gnome-terminal", "--", "bash", "-c", script],
        ["konsole", "-e", "bash", "-c", script],
        ["xfce4-terminal", "-e", f"bash -c {shlex.quote(script)}"],
        ["xterm", "-e", "bash", "-c", script],
    ]


def _install_deb(path):
    """`apt-get install --reinstall <file.deb>` with root rights.

    Preferred: pkexec, which shows the desktop's own password dialog — VidDown
    never sees the password. Without pkexec / a desktop session the same command
    is opened in a terminal for the person to confirm. --reinstall makes the
    same version (a switch between the with-js and no-js flavour) install too."""
    deb = os.path.abspath(path)
    has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    if shutil.which("pkexec") and has_display:
        cmd = ["pkexec", "env", "DEBIAN_FRONTEND=noninteractive",
               "apt-get", "install", "-y", "--reinstall", deb]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        except subprocess.TimeoutExpired:
            raise UpdateError("The installation took too long and was stopped.")
        except OSError as exc:
            raise UpdateError(f"Could not start the installer: {exc}")
        if r.returncode == 0:
            return OUTCOME_RESTART
        if r.returncode in (126, 127):          # prompt dismissed / not authorized
            return OUTCOME_CANCELLED
        tail = "\n".join((r.stderr or r.stdout or "").strip().splitlines()[-6:])
        errorlog.error("app-update", f"apt-get exited with {r.returncode}:\n{tail}")
        raise UpdateError(f"The package installer failed (exit code {r.returncode}).\n{tail}".strip())

    script = (f"echo 'Installing the VidDown update (needs your sudo password)...'; "
              f"sudo apt-get install -y --reinstall {shlex.quote(deb)}; "
              f"echo; read -p 'Done — press Enter to close this window...'")
    for args in _terminal_commands(script):
        try:
            subprocess.Popen(args)
            return OUTCOME_TERMINAL
        except (FileNotFoundError, OSError):
            continue
    raise UpdateError("No way to ask for your password was found (pkexec or a terminal). "
                      f"Install the file manually:\n  sudo apt install --reinstall {deb}")


def _launch_windows_installer(path):
    """Start the Inno Setup installer silently (it shows only its progress
    window). The caller must quit VidDown right away: the installer replaces
    the program files, and `windows/viddown.iss` starts VidDown again at the end
    of a silent run."""
    args = [os.path.abspath(path), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
            "/CLOSEAPPLICATIONS"]
    flags = 0x00000008 | 0x00000200            # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen(args, creationflags=flags, close_fds=True)
    except OSError as exc:
        raise UpdateError(f"Could not start the installer: {exc}")
    return OUTCOME_APP_CLOSES


def install_update(path, kind):
    """Install the downloaded, verified file. Returns an OUTCOME_* value."""
    if kind == KIND_DEB:
        return _install_deb(path)
    if kind == KIND_WIN_INSTALLER:
        return _launch_windows_installer(path)
    raise UpdateError("This copy of VidDown cannot update itself.")


def schedule_relaunch():
    """Linux .deb: start VidDown again a moment after this process has quit
    (the single-instance check must not still see the old one)."""
    try:
        subprocess.Popen(["sh", "-c", "sleep 2; exec /usr/bin/viddown"],
                         start_new_session=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


# ------------------------------------------------------------------- summary
def describe_install():
    """One line for the About page: 'VidDown 2.9.0.1 · with JS runtime'."""
    label = buildinfo.variant_label()
    return f"VidDown {__version__}" + (f"  ·  {label}" if label else "")
