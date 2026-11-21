"""
core/bridge_server.py

Tiny local-only HTTP server so the VidDown browser extension (Chrome /
Brave / Edge) can hand off a video page URL to this already-running
desktop app — the same basic idea as how IDM and other download managers
talk to their browser companion extensions.

SECURITY NOTE: binds to 127.0.0.1 ONLY. It is never reachable from any
other machine on the network — only processes running on this same
computer (i.e. the browser extension) can reach it. There is no
authentication, but requests are only accepted with a loopback Host header
and — except /ping and /show — an extension Origin, so ordinary websites
(simple cross-site GETs, DNS rebinding) cannot drive it.

Endpoints:
    GET /ping
        -> {"app": "VidDown", "version": "...", "status": "ok"}

    GET /add?url=...&title=...
        Manual fallback (toolbar icon click): adds the URL to the
        Download page and starts a fetch automatically.

    GET /probe?url=...&title=...[&referer=...]
        (referer is only sent for media links the extension sniffed from
        network traffic; it is forwarded to yt-dlp as Referer/Origin.)
        Hover-button panel: fetches the video's available qualities
        (yt-dlp) and returns them so the extension can render its own
        quality-picker panel directly on the page. Result is cached
        in-memory (keyed by URL) so the follow-up /download call doesn't
        need to re-fetch.
        -> {"ok": true, "title": "...", "qualities": [
               {"index": 0, "label": "1080p (Full HD)", "size_label": "305.2 MB"},
               ...
           ]}

    GET /download?url=...&quality_index=N&title=...
        Hover-button panel: the person picked a quality in the browser
        panel -> queue the download directly (same as clicking DOWNLOAD
        in the app), using the cached /probe result for that URL.
        -> {"ok": true, "job_id": "..."} or {"ok": false, "error": "..."}

    GET /job_status?job_id=...
        Polled by the browser panel's progress popup (like IDM's little
        floating progress window) to show live % / speed / status for a
        download that was started via /download.
        -> {"ok": true, "status": "downloading", "progress": 42.3,
            "speed": "3.1 MB/s", "eta": "00:12", "title": "...",
            "error": null}

    GET /show
        Not used by the extension — called by a second `viddown`
        process launch (see main.py) to bring this already-running
        instance's window to the front, since it may currently be
        hidden (started via the autostart entry).
        -> {"ok": true} or {"ok": false, "error": "..."}
"""
import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from core import errorlog
from version import __version__

PORT = 47892  # fixed port — the browser extension is hard-coded to this too

_PROBE_CACHE_MAX = 40        # keep at most this many probed videos in memory
_PROBE_CACHE_TTL = 600       # seconds — re-probe if older than this


def _headers_from_qs(qs):
    """Referer (+ matching Origin) the extension saw on a sniffed media
    request. Empty dict for ordinary page URLs, which need neither."""
    referer = (qs.get("referer") or [""])[0].strip()
    if not referer.lower().startswith(("http://", "https://")):
        return {}
    headers = {"Referer": referer}
    parts = urlparse(referer)
    if parts.scheme and parts.netloc:
        headers["Origin"] = f"{parts.scheme}://{parts.netloc}"
    return headers


_EXTENSION_ORIGIN_PREFIXES = ("chrome-extension://", "moz-extension://")
_ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}", "127.0.0.1", "localhost"}
# Paths that need no token: main.py's own raw-socket calls (no Origin header)
# and the extension's first call, which is how it learns the token.
_OPEN_PATHS = {"/ping", "/show"}

# Random per-run secret. /ping hands it out, but a web page can neither read
# that response (we send no CORS headers to web origins, so a cross-site fetch
# is opaque) nor guess the token — so only the extension (whose host
# permissions let it read responses) can make the real requests. This does not
# depend on the browser sending an Origin header on the extension's requests.
_TOKEN = secrets.token_urlsafe(24)


class _BridgeHandler(BaseHTTPRequestHandler):
    # the default BaseHTTPRequestHandler logs every request to stderr —
    # silence that, it would otherwise spam the terminal on every page
    # the person hovers a video on.
    def log_message(self, fmt, *args):
        pass

    def _origin(self):
        return (self.headers.get("Origin") or "").strip()

    def _send_cors_headers(self):
        # Only the browser extension is ever echoed back. "*" would let any
        # website read these responses.
        origin = self._origin()
        if origin.startswith(_EXTENSION_ORIGIN_PREFIXES):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def _host_ok(self):
        return (self.headers.get("Host") or "").strip().lower() in _ALLOWED_HOSTS

    def _request_allowed(self, path, qs):
        """Blocks web pages from driving this local server. Rules: the Host
        header must be loopback (stops DNS rebinding); an Origin header, if
        present, must be an extension's; and everything beyond /ping and
        /show must carry the per-run token."""
        if not self._host_ok():
            return False
        origin = self._origin()
        if origin and not origin.startswith(_EXTENSION_ORIGIN_PREFIXES):
            return False
        if path in _OPEN_PATHS:
            return True
        supplied = (qs.get("t") or [""])[0]
        return secrets.compare_digest(supplied, _TOKEN)

    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            # extension's background service worker calls this cross-origin —
            # allow it explicitly (harmless: this server only runs on
            # 127.0.0.1, so "anyone" here only ever means "this same machine").
            self._send_cors_headers()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            # The extension's tab was closed, or navigated away, right as
            # this reply was being sent — nothing left to send it to. A
            # normal race, not a real error; nothing to do but drop it.
            pass

    def do_OPTIONS(self):
        # CORS preflight — answered for safety even though everything
        # here is a plain GET
        origin = self._origin()
        if not self._host_ok() or (origin and not origin.startswith(_EXTENSION_ORIGIN_PREFIXES)):
            self._send_json({"ok": False, "error": "forbidden"}, status=403)
            return
        self.send_response(204)
        self._send_cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)

        if not self._request_allowed(parsed.path, qs):
            self._send_json({"ok": False, "error": "forbidden"}, status=403)
            return

        if parsed.path == "/ping":
            self._send_json({"app": "VidDown", "version": __version__, "status": "ok",
                             "token": _TOKEN})
            return

        if parsed.path == "/add":
            self._handle_add(qs)
            return

        if parsed.path == "/probe":
            self._handle_probe(qs)
            return

        if parsed.path == "/download":
            self._handle_download(qs)
            return

        if parsed.path == "/job_status":
            self._handle_job_status(qs)
            return

        if parsed.path == "/show":
            self._handle_show()
            return

        self._send_json({"ok": False, "error": "not found"}, status=404)

    # ------------------------------------------------------------------ /add
    def _handle_add(self, qs):
        url = (qs.get("url") or [""])[0].strip()
        title = (qs.get("title") or [""])[0].strip()
        if not url:
            self._send_json({"ok": False, "error": "missing url"}, status=400)
            return
        callback = BridgeServer.on_add_callback
        if callback is None:
            self._send_json({"ok": False, "error": "VidDown not ready yet"}, status=503)
            return
        try:
            callback(url, title)
            self._send_json({"ok": True})
        except Exception as exc:
            errorlog.error("bridge", f"/add failed: {exc}")
            self._send_json({"ok": False, "error": str(exc)}, status=500)

    # ---------------------------------------------------------------- /probe
    def _handle_probe(self, qs):
        url = (qs.get("url") or [""])[0].strip()
        title = (qs.get("title") or [""])[0].strip()
        if not url:
            self._send_json({"ok": False, "error": "missing url"}, status=400)
            return

        cached = BridgeServer.get_cached_probe(url)
        if cached is not None:
            # The cached "is_duplicate" flags go stale as soon as something is
            # downloaded, so recompute them against the current queue/history.
            checker = BridgeServer.duplicate_checker
            if checker is not None:
                try:
                    flags = checker(cached["info"], cached["options"])
                    for q, flag in zip(cached["qualities"], flags):
                        q["is_duplicate"] = bool(flag)
                except Exception:
                    pass
            self._send_json({"ok": True, "title": cached["title"], "qualities": cached["qualities"]})
            return

        prober = BridgeServer.prober
        if prober is None:
            self._send_json({"ok": False, "error": "VidDown not ready yet"}, status=503)
            return
        try:
            # this blocks the current request-handling thread while yt-dlp
            # fetches video info (a few seconds) — fine, ThreadingHTTPServer
            # gives every request its own thread, so it doesn't block /ping,
            # /add, or the Tk UI at all.
            headers = _headers_from_qs(qs)
            info, options = prober(url, headers) if headers else prober(url)
            video_title = info.get("title") or title or url
            qualities = [
                {
                    "index": i,
                    "label": o["label"],
                    "size_label": o["size_label"],
                    "is_duplicate": bool(o.get("is_duplicate", False)),
                }
                for i, o in enumerate(options)
            ]
            BridgeServer.cache_probe(url, video_title, info, options, qualities, headers)
            self._send_json({"ok": True, "title": video_title, "qualities": qualities})
        except Exception as exc:
            errorlog.warning("bridge", f"/probe failed: {exc}")
            self._send_json({"ok": False, "error": str(exc)}, status=500)

    # ------------------------------------------------------------- /download
    def _handle_download(self, qs):
        url = (qs.get("url") or [""])[0].strip()
        title = (qs.get("title") or [""])[0].strip()
        try:
            quality_index = int((qs.get("quality_index") or ["-1"])[0])
        except ValueError:
            quality_index = -1

        if not url or quality_index < 0:
            self._send_json({"ok": False, "error": "missing url/quality_index"}, status=400)
            return

        cached = BridgeServer.get_cached_probe(url, allow_expired=True)
        if cached is None or quality_index >= len(cached["options"]):
            self._send_json(
                {"ok": False, "error": "Quality list expired — please re-open the panel"},
                status=409,
            )
            return

        downloader_cb = BridgeServer.on_download_callback
        if downloader_cb is None:
            self._send_json({"ok": False, "error": "VidDown not ready yet"}, status=503)
            return
        try:
            headers = cached.get("headers") or {}
            option = cached["options"][quality_index]
            if headers:
                job_id = downloader_cb(url, cached["title"] or title, option, headers)
            else:
                job_id = downloader_cb(url, cached["title"] or title, option)
            self._send_json({"ok": True, "job_id": job_id})
        except Exception as exc:
            errorlog.error("bridge", f"/download failed: {exc}")
            self._send_json({"ok": False, "error": str(exc)}, status=500)

    # ----------------------------------------------------------- /job_status
    def _handle_job_status(self, qs):
        job_id = (qs.get("job_id") or [""])[0].strip()
        if not job_id:
            self._send_json({"ok": False, "error": "missing job_id"}, status=400)
            return
        status = BridgeServer.get_job_status(job_id)
        if status is None:
            self._send_json({"ok": False, "error": "unknown job_id"}, status=404)
            return
        status.pop("ts", None)   # internal bookkeeping only, not for the extension
        self._send_json({"ok": True, **status})

    # -------------------------------------------------------------- /show
    def _handle_show(self):
        """Used by a second `viddown`/`main.py` launch (see main.py's
        _is_already_running flow) to bring an already-running instance's
        window to the front instead of just refusing to start — mainly
        matters when that instance is currently hidden because it was
        started by the autostart entry."""
        callback = BridgeServer.on_show_callback
        if callback is None:
            self._send_json({"ok": False, "error": "VidDown not ready yet"}, status=503)
            return
        try:
            callback()
            self._send_json({"ok": True})
        except Exception as exc:
            errorlog.error("bridge", f"/show failed: {exc}")
            self._send_json({"ok": False, "error": str(exc)}, status=500)


class _BridgeHTTPServer(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a *second* process bind a port that is
    # already in use, which would silently break the single-instance check
    # and the "port already in use" warning. Linux keeps the default.
    allow_reuse_address = (os.name != "nt")


class BridgeServer:
    """Runs the local bridge server in a background daemon thread.

    Call BridgeServer.start(on_add, on_download, prober) once at app
    startup:
      - on_add(url, title): the toolbar-icon manual fallback.
      - on_download(url, title, quality_option) -> job_id: person picked
        a quality in the browser panel -> queue it for download directly
        and return the new job's id so the browser panel can poll
        /job_status for live progress (like IDM's mini progress window).
      - prober(url) -> (info, quality_options): runs yt-dlp to fetch the
        video's metadata + build the quality list (core.downloader's
        fetch_info + build_quality_options).
      - on_show(): brings the app window to the front. Used when a
        second launch (see main.py) finds this instance already running
        — most relevant when it's currently hidden after an autostart
        launch, so there's still a way back to the GUI.

    The app's main window calls BridgeServer.update_job_status(...) from
    its own DownloadManager on_update callback, so this class always has
    a fresh copy of every job's progress for the browser panel to poll.

    All callbacks are called from the server's background request
    threads, NOT the Tk main thread — on_add/on_download must hop back
    onto the main thread themselves (e.g. via `app.after(0, ...)`) before
    touching any widgets. prober() doesn't touch the UI at all, so it can
    run directly on the request thread.
    """
    _httpd = None
    _thread = None
    on_add_callback = None
    on_download_callback = None
    on_show_callback = None
    prober = None
    duplicate_checker = None   # (info, options) -> [bool, ...]
    port = PORT

    _cache = {}          # url -> {"title", "info", "options", "qualities", "ts"}
    _cache_lock = threading.Lock()

    _jobs = {}            # job_id -> status dict (see update_job_status)
    _jobs_lock = threading.Lock()
    _JOBS_MAX = 30         # bound memory — evict oldest finished jobs first

    @classmethod
    def start(cls, on_add, on_download=None, prober=None, on_show=None,
              duplicate_checker=None):
        cls.on_add_callback = on_add
        cls.on_download_callback = on_download
        cls.prober = prober
        cls.duplicate_checker = duplicate_checker
        cls.on_show_callback = on_show
        if cls._httpd is not None:
            return True  # already running
        try:
            cls._httpd = _BridgeHTTPServer(("127.0.0.1", PORT), _BridgeHandler)
        except OSError:
            # Port already in use — maybe another VidDown instance is
            # already running, or something else grabbed it. The rest of
            # the app works fine without this feature, so we just skip it
            # silently rather than crashing the whole app over it.
            cls._httpd = None
            return False
        cls._thread = threading.Thread(target=cls._httpd.serve_forever, daemon=True)
        cls._thread.start()
        return True

    @classmethod
    def is_running(cls):
        return cls._httpd is not None

    @classmethod
    def stop(cls):
        if cls._httpd is not None:
            cls._httpd.shutdown()
            cls._httpd = None

    # --------------------------------------------------------- probe cache
    @classmethod
    def cache_probe(cls, url, title, info, options, qualities, headers=None):
        with cls._cache_lock:
            cls._cache[url] = {
                "title": title, "info": info, "options": options,
                "qualities": qualities, "ts": time.time(),
                "headers": headers or {},
            }
            # bound the cache size — evict the oldest entry if needed
            if len(cls._cache) > _PROBE_CACHE_MAX:
                oldest_url = min(cls._cache, key=lambda u: cls._cache[u]["ts"])
                if oldest_url != url:
                    cls._cache.pop(oldest_url, None)

    @classmethod
    def get_cached_probe(cls, url, allow_expired=False):
        with cls._cache_lock:
            entry = cls._cache.get(url)
        if entry is None:
            return None
        if not allow_expired and (time.time() - entry["ts"]) > _PROBE_CACHE_TTL:
            return None
        return entry

    # ------------------------------------------------------------ job status
    @classmethod
    def update_job_status(cls, job):
        """
        Called by the app's main window (from its DownloadManager
        on_update callback) every time a job's state changes, so the
        browser panel's progress popup always has fresh data to poll.
        Only stores plain data — never the DownloadJob object itself.
        """
        with cls._jobs_lock:
            cls._jobs[job.id] = {
                "status":   job.status,
                "progress": job.progress,
                "speed":    job.speed,
                "eta":      job.eta,
                "title":    job.title,
                "error":    job.error,
                "ts":       time.time(),
            }
            if len(cls._jobs) > cls._JOBS_MAX:
                # evict the oldest finished/errored job first, never one
                # that's still actively downloading
                finished = [(jid, j) for jid, j in cls._jobs.items()
                            if j["status"] in ("completed", "error", "cancelled")]
                if finished:
                    oldest_id = min(finished, key=lambda kv: kv[1]["ts"])[0]
                    cls._jobs.pop(oldest_id, None)

    @classmethod
    def get_job_status(cls, job_id):
        with cls._jobs_lock:
            entry = cls._jobs.get(job_id)
            return dict(entry) if entry else None
