"""
core/browser_cookies.py
Finds out how to read a browser's cookies, and says plainly why it cannot.

Firefox keeps its cookies in a plain database, so yt-dlp reads it directly. The
Chromium family (Chrome, Chromium, Brave, Edge, Opera, Vivaldi) encrypts them:

  Linux    the key sits in the desktop keyring (GNOME Keyring / KWallet) and
           yt-dlp has to pick the right one. It guesses from the desktop name —
           on XFCE, LXQt, i3 … the guess is often wrong — and it needs the
           `secretstorage` package to talk to GNOME Keyring. A browser installed
           as a Snap or Flatpak keeps its profile somewhere else entirely.
  Windows  Chrome / Edge / Brave 127+ lock cookies to the browser itself
           ("app-bound encryption"); no other program can read them.

So a plain `cookiesfrombrowser=("brave",)` quietly returned nothing. This module
tries the possible profile folders and keyrings once (a "probe"), remembers the
combination that works, and turns the failure into a sentence a person can act on.

Pure Python + a lazy yt-dlp import, so it can be tested without a browser.
"""
import os
import re
import sys

CHROMIUM_BASED = ("chrome", "chromium", "brave", "edge", "opera", "vivaldi")
KEYRINGS = ("GNOMEKEYRING", "KWALLET6", "KWALLET5", "KWALLET", "BASICTEXT")
KEYRING_LABELS = {
    "GNOMEKEYRING": "GNOME Keyring",
    "KWALLET6": "KDE Wallet 6", "KWALLET5": "KDE Wallet 5", "KWALLET": "KDE Wallet",
    "BASICTEXT": "no keyring",
}

# Where the browser keeps its profile when it is NOT the normal package:
# Snap and Flatpak both give the app a private home. (Globs are allowed.)
_LINUX_PACKAGED_DIRS = {
    "chromium": ["~/snap/chromium/common/chromium",
                 "~/.var/app/org.chromium.Chromium/config/chromium"],
    "chrome":   ["~/.var/app/com.google.Chrome/config/google-chrome"],
    "brave":    ["~/snap/brave/*/.config/BraveSoftware/Brave-Browser",
                 "~/.var/app/com.brave.Browser/config/BraveSoftware/Brave-Browser"],
    "edge":     ["~/.var/app/com.microsoft.Edge/config/microsoft-edge"],
    "opera":    ["~/snap/opera/*/.config/opera",
                 "~/.var/app/com.opera.Opera/config/opera"],
    "vivaldi":  ["~/.var/app/com.vivaldi.Vivaldi/config/vivaldi"],
    "firefox":  ["~/.var/app/org.mozilla.firefox/.mozilla/firefox",
                 "~/snap/firefox/common/.mozilla/firefox"],
}

_FAILED_RE = re.compile(r"\((\d+) could not be decrypted\)")


def _linux():
    return sys.platform.startswith("linux")


def is_chromium_based(browser):
    return browser in CHROMIUM_BASED


# ------------------------------------------------------------- profile folders --
def has_cookie_db(root, name="Cookies", max_depth=4):
    """Is there a cookie database called `name` under `root`?"""
    root = os.path.expanduser(root)
    if not os.path.isdir(root):
        return False
    base_depth = root.rstrip(os.sep).count(os.sep)
    for cur, dirs, files in os.walk(root):
        if name in files:
            return True
        if cur.count(os.sep) - base_depth >= max_depth:
            dirs[:] = []
    return False


def packaged_profile_dirs(browser, home=None):
    """Snap / Flatpak profile folders of `browser` that really hold cookies."""
    import glob
    out = []
    if not _linux():
        return out
    for pattern in _LINUX_PACKAGED_DIRS.get(browser, []):
        pattern = pattern.replace("~", home, 1) if home else os.path.expanduser(pattern)
        for path in sorted(glob.glob(pattern)):
            if path not in out and has_cookie_db(
                    path, "cookies.sqlite" if browser == "firefox" else "Cookies"):
                out.append(path)
    return out


# ------------------------------------------------------------------- probing --
class _Collector:
    """A yt-dlp logger that keeps what yt-dlp says instead of printing it."""

    def __init__(self):
        self.messages = []
        self.errors = []
        self.failed = 0

    def debug(self, msg, *a, **k):
        pass

    def info(self, msg, *a, **k):
        m = _FAILED_RE.search(str(msg))
        if m:
            self.failed = int(m.group(1))

    def warning(self, msg, *a, **k):
        self.messages.append(str(msg))

    def error(self, msg, *a, **k):
        self.errors.append(str(msg))


class Probe:
    """What one attempt to read the cookies found."""

    def __init__(self, browser, profile=None, keyring=None):
        self.browser, self.profile, self.keyring = browser, profile, keyring
        self.count = 0          # cookies read, with a real value
        self.failed = 0         # cookies found but unreadable (not decryptable, or came out empty)
        self.problems = []      # raw reasons, for diagnose()
        self.not_found = False  # no cookie database at all

    @property
    def good(self):
        """Every cookie was read (the best possible outcome)."""
        return self.count > 0 and self.failed == 0

    @property
    def usable(self):
        """At least some cookies were read — downloads can use them."""
        return self.count > 0


def probe(browser, profile=None, keyring=None, extract=None):
    """Try to read `browser`'s cookies once. `extract` is a stand-in for
    yt_dlp.cookies.extract_cookies_from_browser (used by the tests)."""
    result = Probe(browser, profile, keyring)
    log = _Collector()
    try:
        if extract is None:
            from yt_dlp.cookies import extract_cookies_from_browser as extract
        kwargs = {"keyring": keyring} if keyring else {}
        jar = extract(browser, profile, log, **kwargs)
        # yt-dlp cannot tell a wrong key from a right one for short cookies: they
        # "decrypt" to an empty string. An empty value is therefore counted as
        # unreadable, not as a cookie that was found.
        total = len(jar)
        result.count = sum(1 for c in jar if getattr(c, "value", True))
        result.failed = total - result.count
    except FileNotFoundError as exc:
        result.not_found = True
        result.problems.append(str(exc))
    except Exception as exc:                   # yt-dlp raises several unrelated kinds
        result.problems.append(f"{type(exc).__name__}: {exc}")
    result.failed += log.failed
    result.problems.extend(log.errors)
    result.problems.extend(log.messages)
    return result


def autodetect(browser, extract=None, home=None):
    """Find a (profile, keyring) that reads the cookies. Returns the best Probe:
    the first fully working one, otherwise the one that read the most."""
    profiles = [None] + packaged_profile_dirs(browser, home)
    keyrings = [None]
    if is_chromium_based(browser) and _linux():
        keyrings += list(KEYRINGS)

    best = None
    seen = []                       # every reason any attempt gave, for the message
    for profile in profiles:
        for keyring in keyrings:
            attempt = probe(browser, profile, keyring, extract)
            if attempt.good:
                return attempt
            seen.extend(p for p in attempt.problems if p not in seen)
            # more readable cookies is better; fewer unreadable ones break a tie
            if best is None or (attempt.count, -attempt.failed) > (best.count, -best.failed):
                best = attempt
            # Another keyring can only help when cookies were found but could not
            # be decrypted, or when talking to the keyring itself went wrong.
            if keyring is None and attempt.not_found:
                break
            if keyring is None and attempt.failed == 0 and attempt.count == 0 \
                    and not _mentions_keyring(attempt.problems):
                break
    if best is not None:
        best.problems = seen
    return best


def _mentions_keyring(problems):
    text = " ".join(problems).lower()
    return any(w in text for w in ("keyring", "kwallet", "secretstorage", "password", "decrypt"))


# ---------------------------------------------------------- settings → yt-dlp --
def ydl_spec(settings):
    """The `cookiesfrombrowser` tuple for yt-dlp, or None when no browser is set."""
    settings = settings or {}
    browser = settings.get("cookies_browser") or ""
    if not browser:
        return None
    profile = settings.get("cookies_profile") or None
    keyring = settings.get("cookies_keyring") or None
    if keyring and not (is_chromium_based(browser) and _linux()):
        keyring = None
    if keyring:
        return (browser, profile, keyring)
    if profile:
        return (browser, profile)
    return (browser,)


def remember(settings, probe_result):
    """Store the combination that worked (or clear a stale one)."""
    ok = probe_result is not None and probe_result.usable
    settings["cookies_profile"] = (probe_result.profile or "") if ok else ""
    settings["cookies_keyring"] = (probe_result.keyring or "") if ok else ""


def forget(settings):
    settings["cookies_profile"] = ""
    settings["cookies_keyring"] = ""


# ----------------------------------------------------------------- messages --
def describe(result, label, platform=None):
    """(ok, text) — a sentence for the person about a Probe."""
    platform = platform or sys.platform
    windows = platform.startswith("win")
    problems = " ".join(result.problems)
    low = problems.lower()
    browser = result.browser

    if result.usable:
        text = f"{label} cookies work correctly! ({result.count} cookies found)"
        if result.keyring in KEYRING_LABELS:
            text += f" Using {KEYRING_LABELS[result.keyring]}."
        elif result.profile:
            text += " (Snap/Flatpak profile)"
        if result.failed:
            # Found, so it counts as working — but say what is missing.
            text += f" {result.failed} more could not be read"
            if "secretstorage" in low:
                text += " (the desktop-keyring component is missing from this VidDown)."
            elif is_chromium_based(browser) and windows:
                text += f" ({label} 127+ protects some cookies from other programs)."
            else:
                text += " (the desktop keyring could not be opened)."
        return True, text

    if result.not_found:
        where = f" in {result.profile}" if result.profile else ""
        return False, (f"No {label} profile with cookies was found{where}. Is {label} installed, "
                       "and have you signed in to a site with it? (Snap and Flatpak versions are "
                       "checked too.)")

    if "could not copy" in low or "database is locked" in low or ("permission" in low and windows):
        return False, f"{label}'s cookie file is locked. Close {label} completely (also in the tray) and try again."

    if result.count and result.failed:
        base = f"Found {result.count} cookies but {result.failed} could not be decrypted."
    elif result.failed:
        base = f"Found cookies but none could be decrypted ({result.failed})."
    else:
        base = f"No cookies found in {label}."

    if is_chromium_based(browser):
        if windows and result.failed:
            return False, (base + f" {label} 127 and newer locks its cookies so that no other program can "
                           "read them. Use Firefox, or export a cookies.txt file with the "
                           "\"Get cookies.txt LOCALLY\" extension and import it below.")
        if "secretstorage" in low:
            return False, (base + " This VidDown build is missing the component that reads the desktop "
                           "keyring (secretstorage). Update VidDown, or import a cookies.txt file below.")
        if result.failed or _mentions_keyring(result.problems):
            return False, (base + " The keyring that holds the key could not be opened — unlock it "
                           "(log in to your desktop session normally) or import a cookies.txt file below.")
        return False, base + f" Is {label} installed, and have you signed in to a site with it?"
    return False, base + f" Is {label} installed, and have you signed in to a site with it?"
