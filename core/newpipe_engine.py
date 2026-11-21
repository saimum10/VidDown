"""
core/newpipe_engine.py
The NewPipe download engine.

NewPipe Extractor is a Java library, so VidDown runs it as a small helper
program and reads its JSON answer. The helper comes in two forms:

  * the NATIVE helper, bin/newpipe/viddown-newpipe[.exe]: NewPipe Extractor
    compiled to a native executable by the viddown-native-bundle repo; needs no
    Java. Preferred whenever it is present.
  * the Java helper (newpipe_helper/, built to newpipe-helper.jar): needs Java
    17+. Used when there is no native helper, and as a fallback if the native one
    cannot start or gives no answer.

This module:

  * picks the engine for a download ("auto" / "ytdlp" / "newpipe"),
  * finds the helper (native, else Java + jar), and runs it,
  * turns the helper's answer into the same info dict yt-dlp produces
    (formats, subtitles, thumbnails, playlist entries).

Because the info dict has yt-dlp's shape, everything downstream stays shared:
the quality list, size estimates, codec / container choices, subtitles, trimming,
merging with ffmpeg, progress, pause and cancel. NewPipe only does what it is
good at - finding the streams - and yt-dlp's downloader fetches them.

Cookies: the extractor has no sign-in of its own, so the cookies of the person's browser
(or cookies.txt) are handed to the helper as a small file (`--cookies FILE`, see
core/newpipe_cookies.py) and sent with its requests. They are used when a link needs them
(age limit, private, members only, "confirm you are not a bot") - the first try is always
without, because YouTube's JS-free clients can fail when a request carries login cookies.
When even that is not enough, core/downloader.py hands the link to yt-dlp.

Standard library only (this file is imported before yt-dlp is guaranteed).
"""
import json
import os
import subprocess
import sys
from shutil import which
from urllib.parse import urlparse, parse_qs

from core import newpipe_cookies
from core.platform_utils import exe_name, no_window_kwargs

# ------------------------------------------------------------------ engines --
ENGINE_AUTO = "auto"
ENGINE_YTDLP = "ytdlp"
ENGINE_NEWPIPE = "newpipe"

ENGINE_ORDER = (ENGINE_AUTO, ENGINE_YTDLP, ENGINE_NEWPIPE)
ENGINE_LABELS = {ENGINE_AUTO: "Auto", ENGINE_YTDLP: "yt-dlp", ENGINE_NEWPIPE: "NewPipe"}
DEFAULT_ENGINE = ENGINE_YTDLP

SETTINGS_KEY = "download_engine"


def normalize_engine(value):
    """Any stored / typed value -> one of the three engine keys (default: yt-dlp)."""
    value = str(value or "").strip().lower().replace("-", "").replace("_", "")
    return value if value in ENGINE_ORDER else DEFAULT_ENGINE


def _auto_pick(url, settings):
    """Auto mode: choose an engine for this link - no network, no probing.

    A YouTube link (youtube.com, youtu.be, music./m. subdomains, youtube-nocookie.com)
    goes to NewPipe; every other link goes to yt-dlp. The decision is one host
    comparison on the text of the link (microseconds), so it costs nothing to
    make it again for every fetch and every download.
    """
    return ENGINE_NEWPIPE if is_youtube(url) else ENGINE_YTDLP


def resolve_engine(settings, url=None):
    """The engine that really runs for `url`: 'ytdlp' or 'newpipe' (never 'auto')."""
    engine = normalize_engine((settings or {}).get(SETTINGS_KEY))
    if engine == ENGINE_AUTO:
        engine = _auto_pick(url, settings or {})
    return engine if engine in (ENGINE_YTDLP, ENGINE_NEWPIPE) else ENGINE_YTDLP


def uses_newpipe(settings, url=None):
    return resolve_engine(settings, url) == ENGINE_NEWPIPE


# --------------------------------------------------------- finding the helper --
_JAR_NAME = "newpipe-helper.jar"
_NATIVE_NAME = "viddown-newpipe"


def _app_base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _bundle_roots():
    """Folders that can hold bin/ - across PyInstaller layouts, like find_bundled_binary()."""
    roots = []
    if getattr(sys, "frozen", False):
        executable_dir = os.path.dirname(os.path.abspath(sys.executable))
        for base in (getattr(sys, "_MEIPASS", ""), executable_dir,
                     os.path.join(executable_dir, "_internal")):
            if base:
                roots.append(base)
    else:
        roots.append(_app_base_dir())
    return roots


def find_helper_jar():
    """Path of newpipe-helper.jar, or None. Order: environment override, the
    package's bin/newpipe/, then the Gradle build folder (running from source)."""
    env = os.environ.get("VIDDOWN_NEWPIPE_JAR", "").strip()
    if env and os.path.isfile(env):
        return os.path.abspath(env)
    candidates = [os.path.join(root, "bin", "newpipe", _JAR_NAME) for root in _bundle_roots()]
    candidates.append(os.path.join(_app_base_dir(), "newpipe_helper", "build", "libs", _JAR_NAME))
    for path in candidates:
        if os.path.isfile(path):
            return os.path.abspath(path)
    return None


def find_java():
    """Path of a Java launcher, or None. Order: environment override, the Java
    runtime shipped in bin/jre/, JAVA_HOME, then PATH."""
    env = os.environ.get("VIDDOWN_JAVA", "").strip()
    if env and os.path.isfile(env):
        return os.path.abspath(env)
    java = exe_name("java")
    for root in _bundle_roots():
        path = os.path.join(root, "bin", "jre", "bin", java)
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return os.path.abspath(path)
    home = os.environ.get("JAVA_HOME", "").strip()
    if home:
        path = os.path.join(home, "bin", java)
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return os.path.abspath(path)
    return which(java)


def _is_runnable(path):
    """A file we may execute. A missing execute bit (a zip extracted without permissions) is
    repaired on the spot, because a helper that is present but silently skipped is worse."""
    if not os.path.isfile(path):
        return False
    if os.name == "nt" or os.access(path, os.X_OK):
        return True
    try:
        os.chmod(path, os.stat(path).st_mode | 0o111)
    except OSError:
        return False
    return os.access(path, os.X_OK)


def find_native_helper():
    """Path of the native helper (viddown-newpipe[.exe]), or None. Order: environment
    override VIDDOWN_NEWPIPE_BIN, then the package's bin/newpipe/."""
    env = os.environ.get("VIDDOWN_NEWPIPE_BIN", "").strip()
    if env and _is_runnable(env):
        return os.path.abspath(env)
    name = exe_name(_NATIVE_NAME)
    for root in _bundle_roots():
        path = os.path.join(root, "bin", "newpipe", name)
        if _is_runnable(path):
            return os.path.abspath(path)
    return None


def unavailable_reason():
    """Why the NewPipe engine cannot run here, in plain words; None when it can."""
    if find_native_helper():
        return None
    if not find_helper_jar():
        return ("The NewPipe engine is not part of this build (neither the native helper "
                "viddown-newpipe nor newpipe-helper.jar was found in bin/newpipe). Put the helper "
                "from the native bundle there, or choose yt-dlp in Settings -> Download engine.")
    if not find_java():
        return ("The NewPipe engine needs Java 17 or newer, and none was found. Install a Java "
                "runtime (for example OpenJDK 17), or choose yt-dlp in Settings -> Download engine.")
    return None


def is_available():
    return unavailable_reason() is None


# ------------------------------------------------------------ running the helper --
_HELPER_TIMEOUT = 120      # seconds; a 200-video playlist is read page by page
_KIND_HINTS = {
    "ReCaptchaException": "The site asked for a human check (too many requests). Wait a while, "
                          "or try again from another network.",
    "SignInConfirmNotBotException": "YouTube wants to confirm you are not a bot. Choose your signed-in "
                                    "browser under Settings -> Cookies and try again, or try again later.",
    "AgeRestrictedContentException": "This video is age-restricted. Choose a browser that is signed in "
                                     "to YouTube under Settings -> Cookies and try again.",
    "PrivateContentException": "This video is private.",
    "GeographicRestrictionException": "This video is not available in your country.",
    "PaidContentException": "This video is for paying members only.",
    "LiveNotStartedException": "This live stream has not started yet.",
    "UnsupportedURLException": "NewPipe does not support this link. Switch the Download engine to yt-dlp.",
}

# Network failures come back from the helper under the Java class name, with no message of their own.
_NETWORK_KINDS = ("ConnectException", "UnknownHostException", "NoRouteToHostException", "SocketException",
                  "SocketTimeoutException", "HttpTimeoutException", "HttpConnectTimeoutException",
                  "ClosedChannelException", "EOFException")
_NETWORK_HINT = ("NewPipe could not reach the server. Check your internet connection (and the proxy in "
                 "Settings, if you use one), then try again.")
_TLS_HINT = ("NewPipe could not make a secure (HTTPS) connection. Check that the date and time of this "
             "computer are right, or switch the Download engine to yt-dlp.")


class HelperError(RuntimeError):
    """The helper ANSWERED with an error (as opposed to not running at all). Keeps the Java
    class name (`kind`) and the helper's own words (`detail`) next to the readable message, so
    callers can tell "needs a signed-in browser" from "no network"."""

    def __init__(self, message, kind=None, detail=None):
        super().__init__(message)
        self.kind = kind or ""
        self.detail = detail or ""

    @property
    def needs_login(self):
        """True when the video is there but wants a signed-in visitor: age limit, private, members
        only, or YouTube's "confirm you are not a bot" check. Cookies are the cure."""
        if self.kind in _LOGIN_KINDS:
            return True
        text = f"{self.kind} {self.detail}".lower()
        return any(word in text for word in _LOGIN_WORDS)


_LOGIN_KINDS = ("SignInConfirmNotBotException", "AgeRestrictedContentException",
                "PrivateContentException", "PaidContentException")
_LOGIN_WORDS = ("sign in", "log in", "login", "not a bot", "age-restricted", "age restricted",
                "confirm your age", "members-only", "members only", "private video")


def _friendly_error(kind, message):
    message = (message or "").strip()
    if "No service can handle" in message:          # NewPipe's own wording for an unknown site
        kind = "UnsupportedURLException"
    if kind in _NETWORK_KINDS or (message or "").startswith("java.net."):
        return _NETWORK_HINT
    if kind in ("SSLHandshakeException", "SSLException", "SSLPeerUnverifiedException", "CertPathValidatorException"):
        return _TLS_HINT
    hint = _KIND_HINTS.get(kind or "")
    if hint:
        return f"{hint}" + (f" ({message})" if message and message not in hint else "")
    if kind == "ContentNotAvailableException":
        return message or "This content is not available."
    return message or kind or "NewPipe could not read this link."


def _build_command(prefix, url, settings, noplaylist, limit, cookies_file=None):
    """`prefix` starts the helper: [native exe] or [java, flags, '-jar', jar]."""
    cmd = list(prefix) + ["info", url, "--limit", str(int(limit))]
    proxy = (settings or {}).get("proxy")
    if proxy:
        cmd += ["--proxy", str(proxy)]
    if noplaylist:
        cmd.append("--noplaylist")
    if cookies_file:
        cmd += ["--cookies", str(cookies_file)]       # a helper that does not know it ignores it
    return cmd


def _launchers():
    """Ways to run the helper, best first: [(kind, argv prefix)]. The native helper needs no
    Java; Java + jar is the fallback."""
    found = []
    native = find_native_helper()
    if native:
        found.append(("native", [native]))
    jar, java = find_helper_jar(), find_java()
    if jar and java:
        found.append(("jar", [java, "-XX:+UseSerialGC", "-jar", jar]))
    return found


def _clean_env():
    """Environment for a helper program. A PyInstaller app on Linux starts with LD_LIBRARY_PATH pointing
    at its own _internal folder, and every child process inherits it - so the helper (a native
    executable, or java) would load the bundled copies of libz / libstdc++ / ... instead of the system's.
    The bootloader keeps the user's own value in LD_LIBRARY_PATH_ORIG; put that back."""
    env = dict(os.environ)
    if getattr(sys, "frozen", False) and sys.platform.startswith("linux"):
        original = env.get("LD_LIBRARY_PATH_ORIG")
        if original is None:
            env.pop("LD_LIBRARY_PATH", None)
        else:
            env["LD_LIBRARY_PATH"] = original
    return env


def _native_env(exe):
    """Environment for the native helper. It finds the CA bundle (cacert.pem) next to itself; when
    that file is missing (running from source, a hand-copied exe) hand it certifi's bundle."""
    env = _clean_env()
    if env.get("VIDDOWN_CACERT") or env.get("SSL_CERT_FILE"):
        return env
    if os.path.isfile(os.path.join(os.path.dirname(exe), "cacert.pem")):
        return env
    try:
        import certifi
        env["VIDDOWN_CACERT"] = certifi.where()
    except Exception:
        pass
    return env


class _HelperUnusable(RuntimeError):
    """This helper could not be started or gave no answer at all - worth trying the next one.
    (An ANSWER that says 'error' is a real result and is never retried.)"""


def _run_one(kind, prefix, url, settings, noplaylist, limit, cookies_file=None):
    cmd = _build_command(prefix, url, settings, noplaylist, limit, cookies_file)
    kwargs = dict(no_window_kwargs())
    kwargs.setdefault("stdin", subprocess.DEVNULL)
    kwargs["env"] = _native_env(prefix[0]) if kind == "native" else _clean_env()
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=_HELPER_TIMEOUT, **kwargs)
    except subprocess.TimeoutExpired:
        raise RuntimeError("NewPipe took too long to answer. Check your connection and try again.")
    except OSError as exc:
        raise _HelperUnusable(f"NewPipe could not be started: {exc}")

    out = proc.stdout.decode("utf-8", "replace")
    err = proc.stderr.decode("utf-8", "replace")
    data = None
    for line in reversed(out.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                data = json.loads(line)
            except ValueError:
                data = None
            break
    if data is None:
        if "UnsupportedClassVersionError" in err:
            raise _HelperUnusable("The installed Java is too old for the NewPipe engine - Java 17 or "
                                  "newer is needed.")
        tail = err.strip().splitlines()[-1] if err.strip() else f"exit code {proc.returncode}"
        raise _HelperUnusable(f"NewPipe returned no answer ({tail}).")
    if data.get("error"):
        raise HelperError(_friendly_error(data.get("kind"), data.get("error")),
                          kind=data.get("kind"), detail=data.get("error"))
    return data


def run_helper(url, settings=None, noplaylist=False, limit=200, with_cookies=False):
    """Ask the helper about `url`; returns its JSON answer as a dict. Raises
    RuntimeError with a readable message when it cannot run or reports an error (an error
    the helper itself reports is a HelperError).
    Tries the native helper first; if it cannot start or answers nothing, the Java helper
    (when available) gets the same question.
    `with_cookies`: send the person's cookies for this site (see core/newpipe_cookies.py)."""
    reason = unavailable_reason()
    if reason:
        raise RuntimeError(reason)
    cookies_file = newpipe_cookies.write_cookie_file(url, settings) if with_cookies else None
    try:
        failure = None
        for kind, prefix in _launchers():
            try:
                return _run_one(kind, prefix, url, settings, noplaylist, limit, cookies_file)
            except _HelperUnusable as exc:
                failure = exc
        raise failure if failure else RuntimeError(unavailable_reason() or "NewPipe could not be started.")
    finally:
        newpipe_cookies.remove(cookies_file)


# ------------------------------------------------------------ yt-dlp style info --
_YT_HOSTS = ("youtube.com", "youtu.be", "youtube-nocookie.com")
_CHUNK = 10 * 1024 * 1024       # YouTube stream URLs must be fetched in ~10 MB pieces
_AUDIO_FALLBACK_CODEC = {"m4a": "mp4a.40.2", "mp4": "mp4a.40.2", "webm": "opus", "opus": "opus",
                         "ogg": "vorbis", "mp3": "mp3", "weba": "opus"}
_ORIGINAL_TRACK = "ORIGINAL"


def _int(value, default=None):
    try:
        if value in (None, "", "null", "None"):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _host(url):
    try:
        return (urlparse(url or "").hostname or "").lower()
    except ValueError:
        return ""


def is_youtube(url):
    """True for a YouTube link. Pure string check - fast, never touches the network.
    A link pasted without 'https://' (www.youtube.com/watch?v=..) still counts."""
    url = (url or "").strip()
    if not url:
        return False
    if "//" not in url:
        url = "//" + url          # urlparse only finds the host after '//'
    host = _host(url)
    return any(host == h or host.endswith("." + h) for h in _YT_HOSTS)


def _is_googlevideo(url):
    return _host(url).endswith("googlevideo.com")


def _kbps(value):
    """The helper reports bitrates as the library does - bits per second for
    video, kilobits for audio - so anything above 10 000 is taken as bits."""
    n = _int(value)
    if not n or n <= 0:
        return None
    return round(n / 1000.0, 1) if n > 10_000 else float(n)


def _codecs(text):
    """'avc1.640028' -> ('avc1.640028', None); 'avc1.42001E, mp4a.40.2' -> both."""
    parts = [p.strip() for p in str(text or "").replace(";", ",").split(",") if p.strip()]
    return (parts[0] if parts else None), (parts[1] if len(parts) > 1 else None)


def _ext(stream, default):
    ext = str(stream.get("ext") or "").strip().lower().lstrip(".")
    if ext:
        return ext
    mime = str(stream.get("mime") or "").lower()
    if "/" in mime:
        return mime.split("/", 1)[1].split(";")[0].strip() or default
    return default


def _downloadable(stream):
    """Only streams with a real address that a plain HTTP(S) or HLS download can fetch."""
    url = stream.get("url")
    if not url or not str(url).startswith(("http://", "https://")):
        return False
    if stream.get("is_url") is False:                      # the "address" is manifest text
        return False
    return str(stream.get("delivery") or "PROGRESSIVE_HTTP").upper() in ("PROGRESSIVE_HTTP", "HLS", "NONE", "NULL")


def _delivery_fields(stream):
    url = stream["url"]
    fields = {"url": url, "protocol": "https" if url.startswith("https") else "http"}
    if str(stream.get("delivery") or "").upper() == "HLS":
        fields["protocol"] = "m3u8_native"
    elif _is_googlevideo(url):
        fields["downloader_options"] = {"http_chunk_size": _CHUNK}
    return fields


def _exact_size(stream):
    """The stream's length in bytes when the helper knows it (YouTube's adaptive streams), else None.
    Without it a quality list can only guess from the bitrate - and the bitrate of a video stream is
    often missing, or only a peak, which made every quality look alike."""
    n = _int(stream.get("content_length"), 0)
    return n if n and n > 1000 else None


def _stream_kbps(stream, *keys):
    for key in keys:
        value = _kbps(stream.get(key))
        if value:
            return value
    return None


def _video_format(stream, n):
    ext = _ext(stream, "mp4")
    vcodec, acodec = _codecs(stream.get("codec"))
    only = bool(stream.get("video_only"))
    fmt = {
        "format_id": f"np-v{n}",
        "ext": ext,
        "vcodec": vcodec or "unknown",
        "acodec": "none" if only else (acodec or "unknown"),
        "width": _int(stream.get("width")) if _int(stream.get("width"), 0) > 0 else None,
        "height": _int(stream.get("height")) if _int(stream.get("height"), 0) > 0 else None,
        "fps": _int(stream.get("fps")) if _int(stream.get("fps"), 0) > 0 else None,
        "tbr": _stream_kbps(stream, "bitrate", "itag_bitrate"),
        "filesize": _exact_size(stream),
        "format_note": stream.get("resolution") or None,
    }
    fmt.update(_delivery_fields(stream))
    return fmt


def _audio_format(stream, n):
    ext = _ext(stream, "m4a")
    acodec = _codecs(stream.get("codec"))[0] or _AUDIO_FALLBACK_CODEC.get(ext) or "unknown"
    abr = _stream_kbps(stream, "average_bitrate", "itag_average_bitrate", "bitrate", "itag_bitrate")
    track_type = str(stream.get("track_type") or "").upper()
    language = str(stream.get("audio_locale") or "").strip()
    if language.lower() in ("none", "null"):
        language = ""
    # Videos with several audio tracks (original + auto-dubbed): the original
    # one must win "bestaudio", the dubbed ones must never be picked by accident.
    if track_type == _ORIGINAL_TRACK:
        preference = 10
    elif track_type in ("DUBBED", "DESCRIPTIVE", "SECONDARY"):
        preference = -10
    else:
        preference = None
    track_name = stream.get("track_name")
    fmt = {
        "format_id": f"np-a{n}",
        "ext": ext,
        "vcodec": "none",
        "acodec": acodec,
        "abr": abr,
        "tbr": abr,
        "filesize": _exact_size(stream),
        "language": language or None,
        "language_preference": preference,
        "format_note": track_name if track_name and str(track_name) != "None" else None,
    }
    fmt.update(_delivery_fields(stream))
    return fmt


def _clean(d):
    """Drop None values so yt-dlp's own defaults apply."""
    return {k: v for k, v in d.items() if v is not None}


def _thumbnails(items):
    thumbs = []
    for i, t in enumerate(items or []):
        url = t.get("url")
        if not url:
            continue
        thumbs.append(_clean({"id": str(i), "url": url,
                              "width": _int(t.get("width")) if _int(t.get("width"), 0) > 0 else None,
                              "height": _int(t.get("height")) if _int(t.get("height"), 0) > 0 else None}))
    thumbs.sort(key=lambda t: (t.get("width") or 0) * (t.get("height") or 0))
    for i, t in enumerate(thumbs):
        t["preference"] = i - len(thumbs)         # yt-dlp: the last one is the best
    return thumbs


def _best_thumbnail(thumbs, max_width=None):
    """Largest thumbnail (not wider than max_width when given)."""
    pool = [t for t in thumbs if not max_width or (t.get("width") or 0) <= max_width] or thumbs
    return pool[-1]["url"] if pool else None


def _entry_thumbnail(thumbs):
    """A list row only needs a small picture: first one at least 240 px wide."""
    for t in thumbs:
        if (t.get("width") or 0) >= 240:
            return t["url"]
    return thumbs[-1]["url"] if thumbs else None


def _video_id(url):
    try:
        parsed = urlparse(url or "")
    except ValueError:
        return ""
    vid = (parse_qs(parsed.query).get("v") or [""])[0]
    if vid:
        return vid
    if _host(url) == "youtu.be":
        return parsed.path.strip("/")
    return ""


def to_ytdlp_info(raw):
    """The helper's 'stream' answer -> a yt-dlp style video info dict."""
    video_streams = [s for s in raw.get("video_streams") or [] if _downloadable(s)]
    audio_streams = [s for s in raw.get("audio_streams") or [] if _downloadable(s)]

    formats = []
    for n, s in enumerate(video_streams):
        formats.append(_clean(_video_format(s, n)))
    for n, s in enumerate(audio_streams):
        formats.append(_clean(_audio_format(s, n)))

    stream_type = str(raw.get("stream_type") or "")
    is_live = "LIVE" in stream_type and "POST_LIVE" not in stream_type
    hls = raw.get("hls_url")
    if hls and (is_live or not formats):
        formats.append({"format_id": "np-hls", "url": hls, "protocol": "m3u8_native",
                        "ext": "mp4", "vcodec": "unknown", "acodec": "unknown",
                        "format_note": "HLS"})

    subtitles, auto_captions = {}, {}
    for s in raw.get("subtitles") or []:
        url, lang = s.get("url"), s.get("lang")
        if not url or not lang or not str(url).startswith(("http://", "https://")):
            continue
        target = auto_captions if s.get("auto") else subtitles
        target.setdefault(lang, []).append(
            _clean({"ext": _ext(s, "vtt"), "url": url, "name": s.get("name")}))

    thumbs = _thumbnails(raw.get("thumbnails"))
    page_url = raw.get("url") or raw.get("original_url") or ""
    duration = _int(raw.get("duration"))
    view_count = _int(raw.get("view_count"))
    like_count = _int(raw.get("like_count"))
    age = _int(raw.get("age_limit"))
    category = raw.get("category")
    tags = raw.get("tags")

    info = _clean({
        "_type": "video",
        "id": raw.get("id") or _video_id(page_url) or page_url,
        "title": raw.get("title") or "Untitled",
        "fulltitle": raw.get("title") or "Untitled",
        "webpage_url": page_url,
        "original_url": raw.get("original_url") or page_url,
        "webpage_url_domain": _host(page_url) or None,
        "extractor": "NewPipe",
        "extractor_key": "NewPipe",
        "duration": duration if duration and duration > 0 else None,
        "uploader": raw.get("uploader") or None,
        "channel": raw.get("uploader") or None,
        "uploader_url": raw.get("uploader_url") or None,
        "channel_url": raw.get("uploader_url") or None,
        "view_count": view_count if view_count is not None and view_count >= 0 else None,
        "like_count": like_count if like_count is not None and like_count >= 0 else None,
        "description": raw.get("description") or None,
        "age_limit": age if age and age > 0 else 0,
        "categories": [category] if category else None,
        "tags": [t for t in tags if t] if isinstance(tags, list) else None,
        "thumbnails": thumbs or None,
        "thumbnail": _best_thumbnail(thumbs, max_width=1280),
        "is_live": is_live,
        "live_status": "is_live" if is_live else "not_live",
        "formats": formats,
        "subtitles": subtitles,
        "automatic_captions": auto_captions,
    })
    return info


def to_ytdlp_playlist(raw):
    """The helper's 'playlist' answer -> a yt-dlp style flat playlist info dict."""
    entries = []
    for e in raw.get("entries") or []:
        url = e.get("url")
        if not url:
            continue
        thumbs = _thumbnails(e.get("thumbnails"))
        duration = _int(e.get("duration"))
        entries.append(_clean({
            "_type": "url",
            "id": _video_id(url),
            "url": url,
            "webpage_url": url,
            "title": e.get("title") or None,
            "duration": duration if duration and duration > 0 else None,
            "uploader": e.get("uploader") or None,
            "thumbnail": _entry_thumbnail(thumbs),
            "ie_key": "NewPipe",
        }))
    total = _int(raw.get("stream_count"), -1)
    return _clean({
        "_type": "playlist",
        "id": raw.get("id"),
        "title": raw.get("title") or "Playlist",
        "uploader": raw.get("uploader") or None,
        "webpage_url": raw.get("url"),
        "extractor": "NewPipe",
        "extractor_key": "NewPipe",
        "playlist_count": total if total >= 0 else None,
        "entries": entries,
    })


# ------------------------------------------------------------------ public API --
def _playlist_wanted(url, force_noplaylist):
    """For a YouTube watch link that also names a playlist (watch?v=..&list=..), read
    the playlist - like yt-dlp does - unless a single video was asked for or the list
    is one of YouTube's auto 'mixes' (RD...), which only exist as a watch link."""
    if force_noplaylist:
        return url, True
    try:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
    except ValueError:
        return url, False
    list_id = (query.get("list") or [""])[0]
    if is_youtube(url) and list_id and parsed.path.rstrip("/") == "/watch":
        if list_id.startswith("RD"):
            return url, True
        return f"https://www.youtube.com/playlist?list={list_id}", False
    return url, False


def _ask_helper(target, settings, noplaylist, limit):
    """run_helper() with the cookie rule: first without cookies (the normal, fast, safe way);
    only if that fails because the link wants a signed-in visitor, once more WITH the person's
    cookies (when any are set up). The failure of the second try is the one reported, since it
    says what is still wrong."""
    try:
        return run_helper(target, settings, noplaylist=noplaylist, limit=limit)
    except HelperError as exc:
        if not (exc.needs_login and newpipe_cookies.configured(settings)):
            raise
    return run_helper(target, settings, noplaylist=noplaylist, limit=limit, with_cookies=True)


def fetch(url, settings=None, force_noplaylist=False, limit=200):
    """Read a link with NewPipe and return yt-dlp style info: a video dict
    (with formats) or a flat playlist dict (with entries)."""
    settings = settings or {}
    force = bool(force_noplaylist or settings.get("_force_noplaylist"))
    target, noplaylist = _playlist_wanted(url, force)
    raw = _ask_helper(target, settings, noplaylist, limit)
    if raw.get("kind") == "playlist":
        return to_ytdlp_playlist(raw)
    return to_ytdlp_info(raw)


def has_googlevideo_streams(info):
    """True when the video's streams are YouTube ones (they need chunked downloads,
    which aria2c does not do)."""
    return any(_is_googlevideo(f.get("url")) for f in (info.get("formats") or []))
