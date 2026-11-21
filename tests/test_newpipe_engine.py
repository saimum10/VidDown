"""Tests for the NewPipe download engine (core/newpipe_engine.py and its wiring).

No Java and no network needed: the helper's answer is a canned JSON, and for the
"run the helper" tests a tiny fake `java` script stands in for the real one."""
import json
import os
import stat
import sys
import tempfile
import unittest

import _setup  # noqa: F401  (puts the repo root on sys.path)

from core import newpipe_engine as npe

RAW_STREAM = {
    "kind": "stream", "id": "abc123", "url": "https://www.youtube.com/watch?v=abc123",
    "original_url": "https://youtu.be/abc123", "title": "A title", "uploader": "Chan",
    "uploader_url": "https://www.youtube.com/channel/UC1", "duration": 125,
    "view_count": 1000, "like_count": 5, "age_limit": 0, "category": "Music", "tags": ["a", "b"],
    "description": "desc", "stream_type": "VIDEO_STREAM", "hls_url": None, "dash_url": None,
    "thumbnails": [
        {"url": "https://i/small.jpg", "width": 168, "height": 94},
        {"url": "https://i/max.jpg", "width": 1920, "height": 1080},
        {"url": "https://i/hq.jpg", "width": 480, "height": 360},
    ],
    "video_streams": [   # muxed
        {"url": "https://rr1.googlevideo.com/v18", "is_url": True, "delivery": "PROGRESSIVE_HTTP",
         "ext": "mp4", "mime": "video/mp4", "video_only": False, "height": 360, "width": 640,
         "fps": 30, "bitrate": 500000, "codec": "avc1.42001E", "resolution": "360p"},
    ],
    "audio_streams": [
        {"url": "https://rr1.googlevideo.com/a140", "is_url": True, "delivery": "PROGRESSIVE_HTTP",
         "ext": "m4a", "average_bitrate": 128, "codec": "mp4a.40.2", "audio_locale": "en",
         "track_type": "ORIGINAL", "track_name": "English (original)"},
        {"url": "https://rr1.googlevideo.com/a140es", "is_url": True, "delivery": "PROGRESSIVE_HTTP",
         "ext": "m4a", "average_bitrate": 128, "codec": "mp4a.40.2", "audio_locale": "es",
         "track_type": "DUBBED", "track_name": "Spanish (dubbed)"},
        {"url": "https://rr1.googlevideo.com/a251", "is_url": True, "delivery": "PROGRESSIVE_HTTP",
         "ext": "webm", "average_bitrate": 160, "codec": "opus", "audio_locale": "None",
         "track_type": "None"},
    ],
    "subtitles": [
        {"url": "https://s/en.vtt", "lang": "en", "name": "English", "auto": False, "ext": "vtt"},
        {"url": "https://s/bn.vtt", "lang": "bn", "name": "Bengali", "auto": True, "ext": "vtt"},
    ],
}
RAW_STREAM["video_streams"] += [
    {"url": "https://rr1.googlevideo.com/v248", "is_url": True, "delivery": "PROGRESSIVE_HTTP",
     "ext": "webm", "video_only": True, "height": 1080, "width": 1920, "fps": 60,
     "bitrate": 3200000, "codec": "vp9", "resolution": "1080p60"},
    # a DASH manifest given as text, not an address: cannot be downloaded
    {"url": "<MPD>...</MPD>", "is_url": False, "delivery": "DASH", "ext": "mp4",
     "video_only": True, "height": 720},
]

RAW_PLAYLIST = {
    "kind": "playlist", "id": "PL1", "url": "https://www.youtube.com/playlist?list=PL1",
    "title": "My list", "uploader": "Chan", "stream_count": 3, "truncated": False,
    "entries": [
        {"url": "https://www.youtube.com/watch?v=v1", "title": "One", "duration": 61,
         "uploader": "Chan", "thumbnails": [{"url": "https://i/v1.jpg", "width": 320, "height": 180}]},
        {"url": "https://www.youtube.com/watch?v=v2", "title": "Two", "duration": -1,
         "uploader": "Chan", "thumbnails": []},
        {"url": "", "title": "no address"},
    ],
}


class EngineChoiceTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(npe.normalize_engine("newpipe"), "newpipe")
        self.assertEqual(npe.normalize_engine("NewPipe"), "newpipe")
        self.assertEqual(npe.normalize_engine("yt-dlp"), "ytdlp")
        self.assertEqual(npe.normalize_engine("auto"), "auto")
        for junk in (None, "", "other", 5):
            self.assertEqual(npe.normalize_engine(junk), "ytdlp")

    def test_resolve(self):
        key = npe.SETTINGS_KEY
        self.assertEqual(npe.resolve_engine({key: "newpipe"}), "newpipe")
        self.assertEqual(npe.resolve_engine({key: "ytdlp"}), "ytdlp")
        self.assertEqual(npe.resolve_engine({}), "ytdlp")              # old config files
        self.assertEqual(npe.resolve_engine(None), "ytdlp")

    def test_auto_uses_newpipe_for_youtube_and_ytdlp_for_the_rest(self):
        auto = {npe.SETTINGS_KEY: "auto"}
        youtube = ["https://youtu.be/x", "https://www.youtube.com/watch?v=x",
                   "https://m.youtube.com/watch?v=x", "https://music.youtube.com/watch?v=x",
                   "https://youtube.com/shorts/x", "https://www.youtube.com/playlist?list=PL1",
                   "https://www.youtube-nocookie.com/embed/x", "www.youtube.com/watch?v=x",
                   "  https://youtu.be/x  "]
        for url in youtube:
            self.assertEqual(npe.resolve_engine(auto, url), "newpipe", url)
            self.assertTrue(npe.uses_newpipe(auto, url), url)
        other = ["https://vimeo.com/1", "https://soundcloud.com/a/b", "https://example.com/youtube.com",
                 "https://notyoutube.com/watch?v=x", "https://youtube.com.evil.example/x",
                 "https://x.com/a?u=https://youtu.be/x", "", None]
        for url in other:
            self.assertEqual(npe.resolve_engine(auto, url), "ytdlp", repr(url))
            self.assertFalse(npe.uses_newpipe(auto, url), repr(url))

    def test_auto_without_a_url_is_ytdlp(self):
        self.assertEqual(npe.resolve_engine({npe.SETTINGS_KEY: "auto"}), "ytdlp")

    def test_manual_engines_ignore_the_link(self):
        for url in ("https://youtu.be/x", "https://vimeo.com/1"):
            self.assertEqual(npe.resolve_engine({npe.SETTINGS_KEY: "ytdlp"}, url), "ytdlp")
            self.assertEqual(npe.resolve_engine({npe.SETTINGS_KEY: "newpipe"}, url), "newpipe")

    def test_default_setting_exists(self):
        from core import storage
        self.assertEqual(storage.DEFAULT_SETTINGS["download_engine"], "ytdlp")


class VideoConversionTests(unittest.TestCase):
    def setUp(self):
        self.info = npe.to_ytdlp_info(RAW_STREAM)
        self.fmts = {f["format_id"]: f for f in self.info["formats"]}

    def test_basic_fields(self):
        i = self.info
        self.assertEqual((i["_type"], i["id"], i["title"]), ("video", "abc123", "A title"))
        self.assertEqual(i["duration"], 125)
        self.assertEqual(i["uploader"], "Chan")
        self.assertEqual(i["extractor_key"], "NewPipe")
        self.assertFalse(i["is_live"])
        self.assertEqual(i["thumbnail"], "https://i/hq.jpg")         # largest up to 1280 px wide
        self.assertEqual(i["thumbnails"][-1]["url"], "https://i/max.jpg")   # yt-dlp: last = best (for covers)

    def test_formats_are_ytdlp_shaped(self):
        for f in self.info["formats"]:
            self.assertTrue(f["format_id"] and f["url"].startswith("https://"))
            self.assertIn("vcodec", f)
            self.assertIn("acodec", f)
        self.assertEqual(len(self.info["formats"]), 5)               # 2 video + 3 audio, DASH text dropped

    def test_muxed_video_only_and_audio(self):
        muxed, only = self.fmts["np-v0"], self.fmts["np-v1"]
        self.assertEqual((muxed["height"], muxed["ext"], muxed["vcodec"]), (360, "mp4", "avc1.42001E"))
        self.assertNotEqual(muxed["acodec"], "none")                  # has sound
        self.assertEqual((only["height"], only["fps"], only["vcodec"], only["acodec"]),
                         (1080, 60, "vp9", "none"))
        self.assertEqual(only["tbr"], 3200.0)                        # bits -> kbit/s
        audio = self.fmts["np-a0"]
        self.assertEqual((audio["vcodec"], audio["acodec"], audio["abr"]), ("none", "mp4a.40.2", 128.0))

    def test_original_audio_track_wins_over_dubbed(self):
        original, dubbed = self.fmts["np-a0"], self.fmts["np-a1"]
        self.assertGreater(original["language_preference"], dubbed["language_preference"])
        self.assertNotIn("language_preference", self.fmts["np-a2"])  # single-track video: no opinion
        self.assertNotIn("language", self.fmts["np-a2"])             # "None" text is not a language

    def test_youtube_streams_are_read_in_chunks(self):
        for f in self.info["formats"]:
            self.assertEqual(f["downloader_options"]["http_chunk_size"], 10 * 1024 * 1024)
        self.assertTrue(npe.has_googlevideo_streams(self.info))

    def test_other_sites_have_no_chunking(self):
        raw = json.loads(json.dumps(RAW_STREAM))
        for s in raw["video_streams"] + raw["audio_streams"]:
            s["url"] = s["url"].replace("rr1.googlevideo.com", "cdn.example.org")
        info = npe.to_ytdlp_info(raw)
        self.assertFalse(npe.has_googlevideo_streams(info))
        self.assertTrue(all("downloader_options" not in f for f in info["formats"]))

    def test_subtitles_split_manual_and_automatic(self):
        self.assertEqual(list(self.info["subtitles"]), ["en"])
        self.assertEqual(self.info["subtitles"]["en"][0]["ext"], "vtt")
        self.assertEqual(list(self.info["automatic_captions"]), ["bn"])

    def test_live_stream_gets_the_hls_address(self):
        raw = {"kind": "stream", "id": "L", "url": "https://www.youtube.com/watch?v=L", "title": "Live",
               "stream_type": "LIVE_STREAM", "hls_url": "https://m/live.m3u8",
               "video_streams": [], "audio_streams": [], "subtitles": [], "thumbnails": []}
        info = npe.to_ytdlp_info(raw)
        self.assertTrue(info["is_live"])
        self.assertEqual(info["formats"][0]["protocol"], "m3u8_native")

    def test_nothing_downloadable_gives_empty_formats(self):
        raw = dict(RAW_STREAM, video_streams=[], audio_streams=[])
        self.assertEqual(npe.to_ytdlp_info(raw)["formats"], [])

    def test_hls_delivery_uses_hls_protocol(self):
        raw = json.loads(json.dumps(RAW_STREAM))
        raw["video_streams"] = [{"url": "https://pt.example/360.m3u8", "is_url": True, "delivery": "HLS",
                                 "ext": "mp4", "video_only": False, "height": 360}]
        raw["audio_streams"] = []
        f = npe.to_ytdlp_info(raw)["formats"][0]
        self.assertEqual(f["protocol"], "m3u8_native")


class PlaylistConversionTests(unittest.TestCase):
    def test_entries(self):
        info = npe.to_ytdlp_playlist(RAW_PLAYLIST)
        self.assertEqual((info["_type"], info["title"], info["playlist_count"]), ("playlist", "My list", 3))
        self.assertEqual(len(info["entries"]), 2)                    # the one without an address is dropped
        one, two = info["entries"]
        self.assertEqual((one["id"], one["title"], one["duration"]), ("v1", "One", 61))
        self.assertEqual(one["thumbnail"], "https://i/v1.jpg")
        self.assertNotIn("duration", two)                            # -1 means unknown
        self.assertNotIn("thumbnail", two)

    def test_unknown_total(self):
        info = npe.to_ytdlp_playlist(dict(RAW_PLAYLIST, stream_count=-1))
        self.assertNotIn("playlist_count", info)


class PlaylistUrlTests(unittest.TestCase):
    def test_watch_link_with_list_reads_the_playlist(self):
        url, single = npe._playlist_wanted("https://www.youtube.com/watch?v=abc&list=PLxyz", False)
        self.assertEqual((url, single), ("https://www.youtube.com/playlist?list=PLxyz", False))

    def test_single_video_requested(self):
        url = "https://www.youtube.com/watch?v=abc&list=PLxyz"
        self.assertEqual(npe._playlist_wanted(url, True), (url, True))

    def test_mix_list_is_a_single_video(self):
        url = "https://www.youtube.com/watch?v=abc&list=RDabc"
        self.assertEqual(npe._playlist_wanted(url, False), (url, True))

    def test_other_links_are_left_alone(self):
        for url in ("https://www.youtube.com/playlist?list=PL1", "https://soundcloud.com/a/b",
                    "https://www.youtube.com/watch?v=abc"):
            self.assertEqual(npe._playlist_wanted(url, False), (url, False))


@unittest.skipIf(sys.platform == "win32", "uses a shell script as a fake java")
class RunHelperTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.jar = os.path.join(self.dir, "newpipe-helper.jar")
        open(self.jar, "wb").close()
        self.log = os.path.join(self.dir, "args.txt")
        self.java = os.path.join(self.dir, "java")
        self._saved = {k: os.environ.get(k) for k in ("VIDDOWN_JAVA", "VIDDOWN_NEWPIPE_JAR")}
        os.environ["VIDDOWN_JAVA"] = self.java
        os.environ["VIDDOWN_NEWPIPE_JAR"] = self.jar

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _args(self):
        with open(self.log) as f:
            return f.read().split("\n")

    def _fake_java(self, stdout, code=0, stderr=""):
        with open(self.java, "w") as f:
            f.write("#!/bin/sh\n")
            f.write(f'printf "%s\\n" "$@" > "{self.log}"\n')
            f.write(f"printf '%s\\n' '{stdout}'\n")
            if stderr:
                f.write(f"printf '%s\\n' '{stderr}' >&2\n")
            f.write(f"exit {code}\n")
        os.chmod(self.java, os.stat(self.java).st_mode | stat.S_IEXEC)

    def test_success_and_command_line(self):
        self._fake_java(json.dumps(RAW_STREAM))
        raw = npe.run_helper("https://youtu.be/abc123", {"proxy": "http://h:3128"}, noplaylist=True, limit=50)
        self.assertEqual(raw["id"], "abc123")
        args = self._args()
        self.assertIn("-jar", args)
        self.assertIn(self.jar, args)
        self.assertEqual(args[args.index("info") + 1], "https://youtu.be/abc123")
        self.assertEqual(args[args.index("--limit") + 1], "50")
        self.assertEqual(args[args.index("--proxy") + 1], "http://h:3128")
        self.assertIn("--noplaylist", args)

    def test_no_proxy_no_noplaylist_flags_by_default(self):
        self._fake_java(json.dumps(RAW_STREAM))
        npe.run_helper("https://youtu.be/abc123", {})
        args = self._args()
        self.assertNotIn("--proxy", args)
        self.assertNotIn("--noplaylist", args)

    def test_fetch_returns_ytdlp_info(self):
        self._fake_java(json.dumps(RAW_STREAM))
        info = npe.fetch("https://youtu.be/abc123", {}, force_noplaylist=True)
        self.assertEqual(info["_type"], "video")
        self._fake_java(json.dumps(RAW_PLAYLIST))
        info = npe.fetch("https://www.youtube.com/playlist?list=PL1", {})
        self.assertEqual(info["_type"], "playlist")

    def test_helper_error_is_readable(self):
        self._fake_java(json.dumps({"error": "Sign in to confirm", "kind": "SignInConfirmNotBotException"}), code=2)
        with self.assertRaises(RuntimeError) as cm:
            npe.run_helper("https://youtu.be/abc123", {})
        self.assertIn("not a bot", str(cm.exception))

    def test_unknown_site_message(self):
        self._fake_java(json.dumps({"error": "No service can handle the url", "kind": "ExtractionException"}), code=2)
        with self.assertRaises(RuntimeError) as cm:
            npe.run_helper("https://example.org/x", {})
        self.assertIn("does not support this link", str(cm.exception))

    def test_garbage_output(self):
        self._fake_java("not json at all", code=1, stderr="Exception in thread main")
        with self.assertRaises(RuntimeError) as cm:
            npe.run_helper("https://youtu.be/abc123", {})
        self.assertIn("no answer", str(cm.exception))

    def test_old_java(self):
        self._fake_java("", code=1, stderr="java.lang.UnsupportedClassVersionError: bad version")
        with self.assertRaises(RuntimeError) as cm:
            npe.run_helper("https://youtu.be/abc123", {})
        self.assertIn("Java 17", str(cm.exception))

    def test_missing_jar_and_missing_java(self):
        self._fake_java("{}")
        self.assertTrue(npe.is_available())
        os.remove(self.jar)
        self.assertIn("newpipe-helper.jar", npe.unavailable_reason())
        open(self.jar, "wb").close()
        os.environ["VIDDOWN_JAVA"] = os.path.join(self.dir, "nope")
        saved_home, saved_path = os.environ.pop("JAVA_HOME", None), os.environ.get("PATH")
        os.environ["PATH"] = self.dir + "-empty"
        try:
            self.assertIn("Java 17", npe.unavailable_reason())
        finally:
            os.environ["PATH"] = saved_path
            if saved_home is not None:
                os.environ["JAVA_HOME"] = saved_home


@unittest.skipIf(sys.platform == "win32", "uses shell scripts as fake helpers")
class NativeHelperTests(unittest.TestCase):
    """The native helper (viddown-newpipe) is preferred; Java + jar is the fallback."""
    ENV = ("VIDDOWN_JAVA", "VIDDOWN_NEWPIPE_JAR", "VIDDOWN_NEWPIPE_BIN", "VIDDOWN_CACERT", "SSL_CERT_FILE")

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.native = os.path.join(self.dir, "viddown-newpipe")
        self.native_log = os.path.join(self.dir, "native-args.txt")
        self.jar = os.path.join(self.dir, "newpipe-helper.jar")
        open(self.jar, "wb").close()
        self.java = os.path.join(self.dir, "java")
        self.java_log = os.path.join(self.dir, "java-args.txt")
        self._saved = {k: os.environ.get(k) for k in self.ENV}
        for k in ("VIDDOWN_CACERT", "SSL_CERT_FILE"):
            os.environ.pop(k, None)
        os.environ["VIDDOWN_NEWPIPE_BIN"] = self.native
        os.environ["VIDDOWN_JAVA"] = self.java
        os.environ["VIDDOWN_NEWPIPE_JAR"] = self.jar

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _script(self, path, log, stdout, code=0, shebang="#!/bin/sh"):
        with open(path, "w") as f:
            f.write(shebang + "\n")
            f.write(f'printf "%s\\n" "$@" > "{log}"\n')
            f.write(f'printf "CACERT=%s\\n" "$VIDDOWN_CACERT" >> "{log}"\n')
            f.write(f"printf '%s\\n' '{stdout}'\n")
            f.write(f"exit {code}\n")
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)

    def _args(self, log):
        with open(log) as f:
            return f.read().split("\n")

    def test_native_runs_without_java_and_without_jar_flags(self):
        self._script(self.native, self.native_log, json.dumps(RAW_STREAM))
        os.remove(self.jar)
        os.environ["VIDDOWN_JAVA"] = os.path.join(self.dir, "no-java")
        self.assertTrue(npe.is_available())
        raw = npe.run_helper("https://youtu.be/abc123", {"proxy": "http://h:3128"}, noplaylist=True, limit=50)
        self.assertEqual(raw["id"], "abc123")
        args = self._args(self.native_log)
        self.assertNotIn("-jar", args)
        self.assertEqual(args[args.index("info") + 1], "https://youtu.be/abc123")
        self.assertEqual(args[args.index("--limit") + 1], "50")
        self.assertEqual(args[args.index("--proxy") + 1], "http://h:3128")
        self.assertIn("--noplaylist", args)

    def test_native_is_preferred_over_the_java_helper(self):
        self._script(self.native, self.native_log, json.dumps(RAW_STREAM))
        self._script(self.java, self.java_log, json.dumps(dict(RAW_STREAM, id="from-java")))
        self.assertEqual(npe.run_helper("https://youtu.be/abc123", {})["id"], "abc123")
        self.assertFalse(os.path.exists(self.java_log), "java must not run when the native helper answers")

    def test_falls_back_to_java_when_native_cannot_start(self):
        self._script(self.native, self.native_log, "{}", shebang="#!/nonexistent/interpreter")
        self._script(self.java, self.java_log, json.dumps(dict(RAW_STREAM, id="from-java")))
        self.assertEqual(npe.run_helper("https://youtu.be/abc123", {})["id"], "from-java")
        self.assertIn("-jar", self._args(self.java_log))

    def test_falls_back_to_java_when_native_gives_no_answer(self):
        self._script(self.native, self.native_log, "segmentation fault", code=139)
        self._script(self.java, self.java_log, json.dumps(dict(RAW_STREAM, id="from-java")))
        self.assertEqual(npe.run_helper("https://youtu.be/abc123", {})["id"], "from-java")

    def test_a_real_error_answer_is_not_retried_with_java(self):
        self._script(self.native, self.native_log,
                     json.dumps({"error": "Sign in to confirm you are not a bot", "kind": "SignInConfirmNotBotException"}), code=2)
        self._script(self.java, self.java_log, json.dumps(RAW_STREAM))
        with self.assertRaises(RuntimeError) as cm:
            npe.run_helper("https://youtu.be/abc123", {})
        self.assertIn("not a bot", str(cm.exception))
        self.assertFalse(os.path.exists(self.java_log), "an answered error must not be asked again")

    def test_no_answer_and_no_java_reports_the_native_failure(self):
        self._script(self.native, self.native_log, "segmentation fault", code=139)
        os.remove(self.jar)
        with self.assertRaises(RuntimeError) as cm:
            npe.run_helper("https://youtu.be/abc123", {})
        self.assertIn("no answer", str(cm.exception))

    def test_execute_bit_is_repaired(self):
        self._script(self.native, self.native_log, json.dumps(RAW_STREAM))
        os.chmod(self.native, 0o644)
        self.assertEqual(npe.find_native_helper(), os.path.abspath(self.native))
        self.assertTrue(os.access(self.native, os.X_OK))

    def test_nothing_available_names_both_helpers(self):
        os.remove(self.jar)
        reason = npe.unavailable_reason()
        self.assertIn("viddown-newpipe", reason)
        self.assertIn("newpipe-helper.jar", reason)

    def test_ca_bundle_is_passed_only_when_the_helper_has_none_next_to_it(self):
        self._script(self.native, self.native_log, json.dumps(RAW_STREAM))
        npe.run_helper("https://youtu.be/abc123", {})
        with_certifi = [l for l in self._args(self.native_log) if l.startswith("CACERT=")][0]
        try:
            import certifi
            self.assertEqual(with_certifi, "CACERT=" + certifi.where())
        except ImportError:
            self.assertEqual(with_certifi, "CACERT=")
        open(os.path.join(self.dir, "cacert.pem"), "w").close()      # packaged layout: the helper finds it itself
        npe.run_helper("https://youtu.be/abc123", {})
        self.assertEqual([l for l in self._args(self.native_log) if l.startswith("CACERT=")][0], "CACERT=")

    def test_frozen_linux_app_hands_the_helper_the_users_library_path_not_its_own(self):
        from unittest import mock
        with mock.patch.object(sys, "frozen", True, create=True), mock.patch.object(sys, "platform", "linux"):
            with mock.patch.dict(os.environ, {"LD_LIBRARY_PATH": "/app/_internal:/usr/local/lib",
                                              "LD_LIBRARY_PATH_ORIG": "/usr/local/lib"}):
                self.assertEqual(npe._clean_env()["LD_LIBRARY_PATH"], "/usr/local/lib")
            env = {k: v for k, v in os.environ.items() if k != "LD_LIBRARY_PATH_ORIG"}
            env["LD_LIBRARY_PATH"] = "/app/_internal"        # the user had none: the bootloader set only its own
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertNotIn("LD_LIBRARY_PATH", npe._clean_env())

    def test_running_from_source_leaves_the_environment_alone(self):
        from unittest import mock
        with mock.patch.dict(os.environ, {"LD_LIBRARY_PATH": "/mine"}):
            self.assertEqual(npe._clean_env()["LD_LIBRARY_PATH"], "/mine")

    def test_the_helper_really_gets_the_cleaned_environment(self):
        from unittest import mock
        with open(self.native, "w") as f:        # a stand-in that reports the library path it was started with
            f.write("#!/bin/sh\n")
            f.write('printf "LDLP=%s\\n" "$LD_LIBRARY_PATH" > "' + self.native_log + '"\n')
            f.write("printf '%s\\n' '" + json.dumps(RAW_STREAM) + "'\n")
        os.chmod(self.native, 0o755)
        with mock.patch.object(sys, "frozen", True, create=True), mock.patch.object(sys, "platform", "linux"), \
                mock.patch.dict(os.environ, {"LD_LIBRARY_PATH": "/app/_internal", "LD_LIBRARY_PATH_ORIG": "/orig"}):
            npe.run_helper("https://youtu.be/abc123", {})
        self.assertIn("LDLP=/orig", self._args(self.native_log))

    def test_network_failures_get_a_plain_message_not_a_java_class_name(self):
        for kind, msg in (("ConnectException", "java.net.ConnectException"), ("UnknownHostException", "www.youtube.com"),
                          ("HttpTimeoutException", "request timed out"), ("IOException", "java.net.SocketException: reset")):
            with self.subTest(kind=kind):
                self.assertIn("could not reach the server", npe._friendly_error(kind, msg))
        self.assertIn("secure (HTTPS) connection", npe._friendly_error("SSLHandshakeException", "PKIX path building failed"))
        self.assertEqual(npe._friendly_error("ExtractionException", "Something specific"), "Something specific")   # others unchanged

    def test_an_explicit_ca_bundle_wins(self):
        self._script(self.native, self.native_log, json.dumps(RAW_STREAM))
        os.environ["VIDDOWN_CACERT"] = "/my/own/ca.pem"
        npe.run_helper("https://youtu.be/abc123", {})
        self.assertIn("CACERT=/my/own/ca.pem", self._args(self.native_log))


try:
    import yt_dlp  # noqa: F401
    HAVE_YTDLP = True
except ImportError:
    HAVE_YTDLP = False


@unittest.skipUnless(HAVE_YTDLP, "needs yt-dlp installed")
class DownloaderWiringTests(unittest.TestCase):
    def setUp(self):
        from core import downloader
        self.dl = downloader
        self._orig_fetch = npe.fetch
        self._orig_sizes = downloader.fill_exact_sizes
        downloader.fill_exact_sizes = lambda info, settings=None, timeout=8.0: info
        self.calls = []

        def fake_fetch(url, settings=None, force_noplaylist=False, limit=200):
            self.calls.append((url, force_noplaylist))
            if "playlist" in url:
                return npe.to_ytdlp_playlist(RAW_PLAYLIST)
            return npe.to_ytdlp_info(RAW_STREAM)
        npe.fetch = fake_fetch

    def tearDown(self):
        npe.fetch = self._orig_fetch
        self.dl.fill_exact_sizes = self._orig_sizes

    def test_single_video(self):
        info = self.dl.fetch_info("https://youtu.be/abc123", {"download_engine": "newpipe"})
        self.assertEqual(info["id"], "abc123")
        self.assertTrue(self.dl.build_quality_options(info))

    def test_playlist_samples_one_video_for_qualities(self):
        info = self.dl.fetch_info("https://www.youtube.com/playlist?list=PL1", {"download_engine": "newpipe"})
        self.assertEqual(len(info["entries"]), 2)
        self.assertTrue(info["formats"])
        self.assertEqual(self.calls[-1], ("https://www.youtube.com/watch?v=v1", True))

    def test_ytdlp_engine_does_not_touch_newpipe(self):
        self.assertFalse(npe.uses_newpipe({"download_engine": "ytdlp"}, "https://youtu.be/abc123"))


if __name__ == "__main__":
    unittest.main()
