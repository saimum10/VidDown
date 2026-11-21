"""
core/newpipe_cookies.py
Cookies for the NewPipe engine.

yt-dlp reads the browser's cookies (or a cookies.txt) itself. The NewPipe helper is a
separate program, so it gets what it needs as a small Netscape cookies.txt written
for one run (`--cookies FILE`):

  * only the cookies of the site being read (for YouTube: youtube.com and google.com,
    where the sign-in cookies live) - never the whole browser profile,
  * in a private temp file (mode 0600) that is deleted as soon as the helper is done.

Reading a browser's cookie database can be slow (a keyring may have to be unlocked), and
a playlist asks the helper many times, so the cookies read are kept in memory for a short
while.

yt-dlp is imported only when cookies are really read, so importing this module is cheap.
"""
import os
import tempfile
import threading
import time
from urllib.parse import urlparse

from core import browser_cookies

_YOUTUBE_SITES = ("youtube.com", "google.com", "youtube-nocookie.com")
_TWO_PART_SUFFIXES = {"co", "com", "org", "net", "gov", "ac", "edu"}
_CACHE_SECONDS = 120

_cache_lock = threading.Lock()
_cache = {"key": None, "when": 0.0, "rows": []}


def configured(settings):
    """True when the person set up cookies (a browser, or a cookies.txt that exists) and has
    not turned them off. Says nothing about whether they can be read."""
    s = settings or {}
    if not s.get("use_cookies", True):
        return False
    if s.get("cookies_browser"):
        return True
    path = s.get("cookies_file") or ""
    return bool(path) and os.path.isfile(path)


def _host(url):
    url = (url or "").strip()
    if "//" not in url:
        url = "//" + url
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _registrable(host):
    """'www.example.co.uk' -> 'example.co.uk', 'm.example.com' -> 'example.com'."""
    parts = [p for p in host.split(".") if p]
    if len(parts) <= 2:
        return ".".join(parts)
    if len(parts[-1]) == 2 and parts[-2] in _TWO_PART_SUFFIXES:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def sites_for(url):
    """The cookie domains worth sending for `url`."""
    host = _host(url)
    if not host:
        return ()
    if any(host == s or host.endswith("." + s) for s in _YOUTUBE_SITES) or host == "youtu.be":
        return _YOUTUBE_SITES
    return (_registrable(host),)


def _domain_wanted(cookie_domain, sites):
    domain = (cookie_domain or "").lower().lstrip(".")
    return any(domain == s or domain.endswith("." + s) for s in sites)


def _settings_key(settings):
    s = settings or {}
    path = s.get("cookies_file") or ""
    try:
        mtime = os.path.getmtime(path) if path else 0
    except OSError:
        mtime = 0
    return (browser_cookies.ydl_spec(s), path, mtime)


def _read_all(settings):
    """Every cookie yt-dlp can read for these settings, as plain tuples (cached for a while)."""
    key = _settings_key(settings)
    now = time.monotonic()
    with _cache_lock:
        if _cache["key"] == key and now - _cache["when"] < _CACHE_SECONDS:
            return _cache["rows"]

    import yt_dlp                      # lazy: only when cookies are really read
    opts = {"quiet": True, "no_warnings": True, "noprogress": True}
    spec = browser_cookies.ydl_spec(settings)
    cookie_file = (settings or {}).get("cookies_file") or ""
    if spec:
        opts["cookiesfrombrowser"] = spec
    elif cookie_file and os.path.isfile(cookie_file):
        opts["cookiefile"] = cookie_file
    else:
        return []
    with yt_dlp.YoutubeDL(opts) as ydl:
        jar = ydl.cookiejar
        rows = [(c.domain, bool(c.domain_specified), c.path or "/", bool(c.secure),
                 int(c.expires or 0), c.name, c.value if c.value is not None else "")
                for c in jar if c.name]
    with _cache_lock:
        _cache.update(key=key, when=time.monotonic(), rows=rows)
    return rows


def rows_for(url, settings):
    """The cookies that belong to the site of `url`: [(domain, include_subdomains, path, secure,
    expires, name, value)]. Empty when none are set up, none can be read, or none match."""
    if not configured(settings):
        return []
    sites = sites_for(url)
    if not sites:
        return []
    try:
        rows = _read_all(settings)
    except Exception:
        return []                       # an unreadable browser must not break the download
    return [r for r in rows if _domain_wanted(r[0], sites)]


def write_cookie_file(url, settings):
    """Write the cookies of `url`'s site to a private temp file and return its path, or None when
    there is nothing to send. The caller deletes the file (see remove())."""
    rows = rows_for(url, settings)
    if not rows:
        return None
    fd, path = tempfile.mkstemp(prefix="viddown-cookies-", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# Netscape HTTP Cookie File\n")
            for domain, subdomains, cpath, secure, expires, name, value in rows:
                if any(ch in (name + value) for ch in "\t\r\n"):
                    continue
                dotted = domain if domain.startswith(".") else ("." + domain if subdomains else domain)
                fh.write("\t".join((dotted, "TRUE" if subdomains or dotted.startswith(".") else "FALSE",
                                    cpath, "TRUE" if secure else "FALSE", str(max(expires, 0)),
                                    name, value)) + "\n")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return path
    except Exception:
        remove(path)
        raise


def remove(path):
    if not path:
        return
    try:
        os.remove(path)
    except OSError:
        pass
