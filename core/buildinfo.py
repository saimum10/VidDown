"""
core/buildinfo.py
Which flavour of VidDown is this?

Two packages are built from the same code:
  * "with-js" — ships a JavaScript runtime (Deno). YouTube needs one, together
                with yt-dlp-ejs, to list every format.
  * "no-js"   — smaller; no runtime bundled. (A Deno/Node/Bun already installed
                on the system is still picked up automatically.)

The build scripts write the flavour into resources/build_variant.txt inside the
package. The in-app updater reads it so that it downloads the same flavour
again (unless the person picks the other one).

Standard library only — this is imported very early and must never pull in
yt-dlp or Tkinter.
"""
import functools
import os
import sys

VARIANT_JS = "with-js"
VARIANT_NOJS = "no-js"
VARIANT_SOURCE = "source"          # running from a source checkout, not a package

_MARKER_NAME = "build_variant.txt"

_LABELS = {
    VARIANT_JS: "with JS runtime",
    VARIANT_NOJS: "without JS runtime",
    VARIANT_SOURCE: "running from source",
}


def _resource_dirs():
    """Every place a packaged build can keep resources/ (PyInstaller 5/6 onedir)."""
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        bases = (getattr(sys, "_MEIPASS", ""), exe_dir, os.path.join(exe_dir, "_internal"))
        return [os.path.join(b, "resources") for b in bases if b]
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return [os.path.join(root, "resources")]


@functools.lru_cache(maxsize=1)
def get_variant():
    """VARIANT_JS, VARIANT_NOJS or VARIANT_SOURCE."""
    if not getattr(sys, "frozen", False):
        return VARIANT_SOURCE
    for folder in _resource_dirs():
        try:
            with open(os.path.join(folder, _MARKER_NAME), encoding="utf-8") as f:
                value = f.read().strip().lower()
        except OSError:
            continue
        if value in (VARIANT_JS, VARIANT_NOJS):
            return value
    # A package built before this marker existed never had a JS runtime in it.
    return VARIANT_NOJS


def find_doc(name):
    """Path of a license document shipped with this build ("LICENSE",
    "THIRD_PARTY_NOTICES.md"), or None. Packages keep them in licenses/ (see the
    build scripts); a source checkout has them at its top level."""
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        bases = [b for b in (getattr(sys, "_MEIPASS", ""), exe_dir,
                             os.path.join(exe_dir, "_internal")) if b]
        candidates = [os.path.join(b, sub, name) for b in bases for sub in ("licenses", "")]
    else:
        candidates = [os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), name)]
    return next((c for c in candidates if os.path.isfile(c)), None)


def variant_label(variant=None):
    return _LABELS.get(variant or get_variant(), "")
