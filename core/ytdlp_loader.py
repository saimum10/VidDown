"""
core/ytdlp_loader.py
Lets VidDown run a newer yt-dlp than the one frozen into the .deb / .exe.

Why this exists: the packaged app imports the yt-dlp that PyInstaller
bundled, so `pip install -U yt-dlp` into the system Python never changed
what the app actually runs. Instead the updater unpacks a verified yt-dlp
wheel into a user-writable folder (OVERRIDE_DIR) and this module makes
Python prefer that copy over the bundled one at the next start.

The override folder holds two packages that always travel together:
yt_dlp and yt_dlp_ejs (the JavaScript challenge solver YouTube needs; yt-dlp
pins the exact ejs version it works with).

An override is only trusted while it belongs to the VidDown version that
downloaded it: a newer VidDown ships a newer bundled yt-dlp, which a leftover
download from the previous version must not shadow (see install()).

Must be imported and install()ed BEFORE anything imports yt_dlp, and must
itself only use the standard library.
"""
import importlib.abc
import importlib.machinery
import os
import shutil
import sys

from core.platform_utils import data_dir

OVERRIDE_DIR = os.path.join(data_dir(), "ytdlp_override")
OVERRIDE_PACKAGES = ("yt_dlp", "yt_dlp_ejs")
APP_VERSION_MARKER = ".viddown_app_version"


def _app_version():
    try:
        from version import __version__
        return __version__
    except Exception:
        return ""


class _OverrideFinder(importlib.abc.MetaPathFinder):
    """Resolves yt_dlp / yt_dlp_ejs and their submodules from OVERRIDE_DIR,
    ahead of the PyInstaller frozen importer (which would otherwise win). A
    package the override folder doesn't contain falls through to the bundled one."""

    def find_spec(self, fullname, path=None, target=None):
        top = fullname.split(".", 1)[0]
        if top not in OVERRIDE_PACKAGES:
            return None
        search = [OVERRIDE_DIR] if fullname == top else path
        if not search:
            return None
        return importlib.machinery.PathFinder.find_spec(fullname, search, target)


def _finder_installed():
    return any(isinstance(f, _OverrideFinder) for f in sys.meta_path)


def has_override():
    return os.path.isfile(os.path.join(OVERRIDE_DIR, "yt_dlp", "__init__.py"))


def override_version():
    """Version string of the downloaded copy, or None (read from its
    version.py without importing it)."""
    import re
    try:
        with open(os.path.join(OVERRIDE_DIR, "yt_dlp", "version.py"), encoding="utf-8") as f:
            m = re.search(r"__version__\s*=\s*['\"]([^'\"]+)['\"]", f.read())
        return m.group(1) if m else None
    except OSError:
        return None


def override_app_version():
    """The VidDown version that downloaded the override (None if unknown)."""
    try:
        with open(os.path.join(OVERRIDE_DIR, APP_VERSION_MARKER), encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def discard_stale_override():
    """Delete an override that a DIFFERENT VidDown version downloaded (or that
    carries no marker at all). The new package's bundled yt-dlp is at least as
    new, and an old override would otherwise shadow it — for good if automatic
    updates are switched off. Returns True if something was removed."""
    if not has_override():
        return False
    if override_app_version() == _app_version():
        return False
    shutil.rmtree(OVERRIDE_DIR, ignore_errors=True)
    return True


def install():
    """Prefer the downloaded yt-dlp if there is one. Returns True if active."""
    if not _finder_installed():
        discard_stale_override()
        if has_override():
            sys.meta_path.insert(0, _OverrideFinder())
    return _finder_installed()


def verify_or_rollback():
    """Import yt_dlp now. If the downloaded copy is broken, switch back to
    the bundled one (and set the bad copy aside) instead of leaving the app
    unable to start. Returns True if yt_dlp imported."""
    try:
        import yt_dlp  # noqa: F401
        import yt_dlp.utils  # noqa: F401
        return True
    except Exception:
        if not _finder_installed():
            return False   # nothing to roll back — yt-dlp really is missing
    sys.meta_path[:] = [f for f in sys.meta_path if not isinstance(f, _OverrideFinder)]
    for name in [n for n in sys.modules
                 if n.split(".", 1)[0] in OVERRIDE_PACKAGES]:
        sys.modules.pop(name, None)
    try:
        broken = f"{OVERRIDE_DIR}.broken"
        shutil.rmtree(broken, ignore_errors=True)
        os.replace(OVERRIDE_DIR, broken)
    except OSError:
        shutil.rmtree(OVERRIDE_DIR, ignore_errors=True)
    try:
        import yt_dlp  # noqa: F401
        return True
    except Exception:
        return False
