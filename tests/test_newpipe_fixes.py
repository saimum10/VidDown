"""Tests for the NewPipe engine fixes: quality sizes, cookies, playlists.

No Java, no network and no browser needed: the helper's answers are canned, and the
cookie jar is a real cookies.txt in a temp folder."""
import os
import stat
import tempfile
import unittest
from unittest import mock

import _setup  # noqa: F401  (puts the repo root on sys.path)

from core import downloader
from core import newpipe_cookies as npc
from core import newpipe_engine as npe

YT_URL = "https://www.youtube.com/watch?v=abc123"
LIST_URL = "https://www.youtube.com/playlist?list=PL1"
CHANNEL_URL = "https://www.youtube.com/@somechannel/videos"


def _video(height, itag_bitrate, content_length=None, url=None, only=True):
    s = {"url": url or f"https://rr1.googlevideo.com/videoplayback?itag={height}", "is_url": True,
         "delivery": "PROGRESSIVE_HTTP", "ext": "mp4", "video_only": only, "height": height,
         "width": height * 16 // 9, "fps": 30, "codec": "avc1.640028", "resolution": f"{height}p",
         "bitrate": None, "itag_bitrate": itag_bitrate}
    if content_length:
        s["content_length"] = content_length
    return s


def _stream_answer(videos, audios=None):
    return {"kind": "stream", "id": "abc123", "url": YT_URL, "title": "T", "duration": 600,
            "stream_type": "VIDEO_STREAM", "video_streams": videos,
            "audio_streams": audios or [{"url": "https://rr1.googlevideo.com/videoplayback?itag=140",
                                         "is_url": True, "delivery": "PROGRESSIVE_HTTP", "ext": "m4a",
                                         "average_bitrate": 128, "codec": "mp4a.40.2",
                                         "content_length": 9_600_000}],
            "subtitles": [], "thumbnails": []}


def _playlist_answer():
    return {"kind": "playlist", "id": "PL1", "url": LIST_URL, "title": "L", "stream_count": 2,
            "entries": [{"url": "https://www.youtube.com/watch?v=v1", "title": "One", "duration": 61},
                        {"url": "https://www.youtube.com/watch?v=v2", "title": "Two", "duration": 62}]}


class SizeTests(unittest.TestCase):
    """Every quality must show ITS OWN size."""

    def _labels(self, raw):
        info = npe.to_ytdlp_info(raw)
        with mock.patch.object(downloader, "_head_size", return_value=None):
            downloader.fill_exact_sizes(info, {})
        return {o["height"]: o["size_bytes"] for o in downloader.build_quality_options(info)
                if not o.get("audio_only")}

    def test_the_helpers_exact_length_becomes_the_formats_filesize(self):
        info = npe.to_ytdlp_info(_stream_answer([_video(360, 600_000, 30_000_000)]))
        video = [f for f in info["formats"] if f.get("vcodec") != "none"][0]
        audio = [f for f in info["formats"] if f.get("vcodec") == "none"][0]
        self.assertEqual(video["filesize"], 30_000_000)
        self.assertEqual(audio["filesize"], 9_600_000)

    def test_qualities_with_exact_lengths_differ(self):
        sizes = self._labels(_stream_answer([_video(360, 600_000, 30_000_000),
                                             _video(720, 2_500_000, 120_000_000),
                                             _video(1080, 4_500_000, 250_000_000)]))
        self.assertEqual(sizes[360], 39_600_000)
        self.assertEqual(sizes[720], 129_600_000)
        self.assertEqual(sizes[1080], 259_600_000)

    def test_without_lengths_the_itag_bitrate_still_separates_qualities(self):
        # the plain 'bitrate' getter came back empty; the ItagItem's bitrate fills in
        sizes = self._labels(_stream_answer([_video(360, 600_000), _video(720, 2_500_000),
                                             _video(1080, 4_500_000)]))
        self.assertEqual(len(set(sizes.values())), 3)
        self.assertLess(sizes[360], sizes[720])
        self.assertLess(sizes[720], sizes[1080])

    def test_a_tiny_or_missing_length_is_not_trusted(self):
        info = npe.to_ytdlp_info(_stream_answer([_video(360, 600_000, 12)]))
        video = [f for f in info["formats"] if f.get("vcodec") != "none"][0]
        self.assertNotIn("filesize", video)

    def test_content_range_total(self):
        self.assertEqual(downloader._content_range_total("bytes 0-0/12345678"), 12345678)
        self.assertIsNone(downloader._content_range_total("bytes 0-0/*"))
        self.assertIsNone(downloader._content_range_total(None))

    def _fake_ydl(self, head=None, ranged=None):
        """A yt-dlp stand-in. `head` / `ranged`: a headers dict, or an Exception to raise."""
        class Resp:
            def __init__(self, headers, status=206):
                self.headers, self.status = headers, status

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        ydl = mock.Mock()

        def urlopen(req):
            outcome = ranged if req.headers.get("Range") else head
            if isinstance(outcome, Exception):
                raise outcome
            return Resp(outcome, 206 if req.headers.get("Range") else 200)
        ydl.urlopen = urlopen
        return ydl

    def test_head_with_a_length_is_enough(self):
        ydl = self._fake_ydl(head={"Content-Length": "5000000"}, ranged=OSError("must not be asked"))
        self.assertEqual(downloader._head_size(ydl, {"url": "https://h/v"}), 5_000_000)

    def test_refused_head_falls_back_to_a_one_byte_range_request(self):
        ydl = self._fake_ydl(head=OSError("403"), ranged={"Content-Range": "bytes 0-0/7000000"})
        self.assertEqual(downloader._head_size(ydl, {"url": "https://h/v"}), 7_000_000)

    def test_head_without_a_length_also_falls_back(self):
        ydl = self._fake_ydl(head={}, ranged={"Content-Range": "bytes 0-0/8000000"})
        self.assertEqual(downloader._head_size(ydl, {"url": "https://h/v"}), 8_000_000)

    def test_nothing_known_stays_unknown(self):
        ydl = self._fake_ydl(head=OSError("x"), ranged=OSError("y"))
        self.assertIsNone(downloader._head_size(ydl, {"url": "https://h/v"}))


class CookieFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.jar = os.path.join(self.tmp.name, "cookies.txt")
        with open(self.jar, "w", encoding="utf-8") as fh:
            fh.write("# Netscape HTTP Cookie File\n")
            for line in (
                ".youtube.com\tTRUE\t/\tTRUE\t4102444800\tSID\tabc",
                ".google.com\tTRUE\t/\tTRUE\t4102444800\tNID\tgoogle-cookie",
                ".example.com\tTRUE\t/\tFALSE\t4102444800\tsess\tother-site",
                ".bank.example.org\tTRUE\t/\tTRUE\t4102444800\tSECRET\tmust-not-leak",
            ):
                fh.write(line + "\n")
        npc._cache.update(key=None, when=0.0, rows=[])
        self.settings = {"cookies_file": self.jar, "use_cookies": True}

    def _written(self, url):
        path = npc.write_cookie_file(url, self.settings)
        self.assertIsNotNone(path)
        self.addCleanup(npc.remove, path)
        with open(path, encoding="utf-8") as fh:
            return path, fh.read()

    def test_configured(self):
        self.assertTrue(npc.configured(self.settings))
        self.assertTrue(npc.configured({"cookies_browser": "firefox"}))
        self.assertFalse(npc.configured({}))
        self.assertFalse(npc.configured({"cookies_file": os.path.join(self.tmp.name, "nope.txt")}))
        self.assertFalse(npc.configured(dict(self.settings, use_cookies=False)))

    def test_youtube_gets_youtube_and_google_cookies_only(self):
        _path, text = self._written(YT_URL)
        self.assertIn("SID", text)
        self.assertIn("NID", text)
        self.assertNotIn("other-site", text)
        self.assertNotIn("must-not-leak", text)

    def test_another_site_gets_only_its_own_cookies(self):
        _path, text = self._written("https://www.example.com/video/1")
        self.assertIn("other-site", text)
        self.assertNotIn("abc", text)
        self.assertNotIn("must-not-leak", text)       # bank.example.org is another site

    def test_the_file_is_private(self):
        path, _text = self._written(YT_URL)
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_no_matching_cookie_means_no_file(self):
        self.assertIsNone(npc.write_cookie_file("https://nothing-here.test/v", self.settings))

    def test_no_cookie_setup_means_no_file(self):
        self.assertIsNone(npc.write_cookie_file(YT_URL, {}))

    def test_sites(self):
        self.assertEqual(npc.sites_for("https://youtu.be/x"), npc._YOUTUBE_SITES)
        self.assertEqual(npc.sites_for("https://m.youtube.com/watch?v=1"), npc._YOUTUBE_SITES)
        self.assertEqual(npc.sites_for("https://soundcloud.com/a/b"), ("soundcloud.com",))
        self.assertEqual(npc.sites_for("https://www.bbc.co.uk/x"), ("bbc.co.uk",))


class CookieRetryTests(unittest.TestCase):
    """The first try is without cookies; a link that wants a sign-in gets a second try WITH them."""

    LOGIN_ERROR = npe.HelperError("age-restricted", kind="AgeRestrictedContentException", detail="age")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        jar = os.path.join(self.tmp.name, "c.txt")
        with open(jar, "w", encoding="utf-8") as fh:
            fh.write("# Netscape HTTP Cookie File\n")
            fh.write(".youtube.com\tTRUE\t/\tTRUE\t4102444800\tSID\tabc\n")
        npc._cache.update(key=None, when=0.0, rows=[])
        self.with_cookies = {"cookies_file": jar}
        self.calls = []

    def _fake_run_one(self, results):
        results = list(results)

        def run_one(kind, prefix, url, settings, noplaylist, limit, cookies_file=None):
            self.calls.append(cookies_file)
            outcome = results.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        return run_one

    def _patched(self, results):
        return mock.patch.multiple(
            npe, unavailable_reason=lambda: None, _launchers=lambda: [("native", ["x"])],
            _run_one=self._fake_run_one(results))

    def test_the_command_line_carries_the_cookie_file_only_when_given(self):
        base = npe._build_command(["x"], YT_URL, {}, False, 5)
        self.assertNotIn("--cookies", base)
        with_cookies = npe._build_command(["x"], YT_URL, {}, False, 5, cookies_file="/tmp/c.txt")
        self.assertEqual(with_cookies[with_cookies.index("--cookies") + 1], "/tmp/c.txt")

    def test_first_try_has_no_cookies_and_a_success_ends_it(self):
        with self._patched([_stream_answer([_video(360, 600_000)])]):
            npe.fetch(YT_URL, self.with_cookies)
        self.assertEqual(self.calls, [None])

    def test_a_login_error_is_retried_with_cookies(self):
        with self._patched([self.LOGIN_ERROR, _stream_answer([_video(360, 600_000)])]):
            info = npe.fetch(YT_URL, self.with_cookies)
        self.assertEqual(len(self.calls), 2)
        self.assertIsNone(self.calls[0])
        self.assertIsNotNone(self.calls[1])
        self.assertEqual(info["id"], "abc123")

    def test_the_cookie_file_is_deleted_afterwards(self):
        with self._patched([self.LOGIN_ERROR, _stream_answer([_video(360, 600_000)])]):
            npe.fetch(YT_URL, self.with_cookies)
        self.assertFalse(os.path.exists(self.calls[1]))

    def test_no_cookies_set_up_means_no_retry(self):
        with self._patched([self.LOGIN_ERROR]):
            with self.assertRaises(npe.HelperError):
                npe.fetch(YT_URL, {})
        self.assertEqual(len(self.calls), 1)

    def test_an_error_that_cookies_cannot_fix_is_not_retried(self):
        gone = npe.HelperError("This video is not available in your country.",
                               kind="GeographicRestrictionException")
        with self._patched([gone]):
            with self.assertRaises(npe.HelperError):
                npe.fetch(YT_URL, self.with_cookies)
        self.assertEqual(len(self.calls), 1)

    def test_the_second_failure_is_the_one_reported(self):
        second = npe.HelperError("still age-restricted", kind="AgeRestrictedContentException")
        with self._patched([self.LOGIN_ERROR, second]):
            with self.assertRaises(npe.HelperError) as ctx:
                npe.fetch(YT_URL, self.with_cookies)
        self.assertIs(ctx.exception, second)

    def test_needs_login(self):
        for kind in ("SignInConfirmNotBotException", "AgeRestrictedContentException",
                     "PrivateContentException", "PaidContentException"):
            self.assertTrue(npe.HelperError("x", kind=kind).needs_login, kind)
        self.assertTrue(npe.HelperError("x", kind="ContentNotAvailableException",
                                        detail='Got error LOGIN_REQUIRED: "Sign in to confirm you are not a bot"').needs_login)
        self.assertFalse(npe.HelperError("x", kind="GeographicRestrictionException").needs_login)
        self.assertFalse(npe.HelperError("x", kind="ConnectException").needs_login)


class PlaylistFallbackTests(unittest.TestCase):
    """A playlist NewPipe cannot list is listed by yt-dlp - and still downloaded one video at a time."""

    SETTINGS = {"download_engine": "newpipe"}

    def _ytdlp_listing(self):
        return {"_type": "playlist", "title": "L", "entries": [{"id": "v1", "title": "One"}],
                "formats": [], "from": "yt-dlp"}

    def test_helper_failure_on_a_youtube_playlist_falls_back(self):
        boom = RuntimeError("NewPipe returned no answer (exit code 1).")
        fake_ydl = mock.MagicMock()
        fake_ydl.__enter__.return_value.extract_info.return_value = self._ytdlp_listing()
        with mock.patch.object(npe, "is_available", return_value=True), \
             mock.patch.object(downloader, "_fetch_info_newpipe", side_effect=boom), \
             mock.patch.object(downloader, "fill_exact_sizes"), \
             mock.patch.object(downloader, "_sample_candidates", return_value=[]), \
             mock.patch.object(downloader.yt_dlp, "YoutubeDL", return_value=fake_ydl):
            info = downloader.fetch_info(LIST_URL, self.SETTINGS)
        self.assertEqual(info.get("from"), "yt-dlp")

    def test_channel_links_fall_back_too(self):
        boom = npe.HelperError("The NewPipe engine does not read channel pages yet.",
                               kind="IllegalArgumentException")
        self.assertTrue(downloader._newpipe_may_fall_back(CHANNEL_URL, boom))

    def test_a_missing_helper_is_not_hidden(self):
        boom = RuntimeError("The NewPipe engine is not part of this build.")
        with mock.patch.object(npe, "is_available", return_value=False):
            self.assertFalse(downloader._newpipe_may_fall_back(LIST_URL, boom))

    def test_no_network_is_not_hidden(self):
        boom = npe.HelperError(npe._NETWORK_HINT, kind="ConnectException")
        with mock.patch.object(npe, "is_available", return_value=True):
            self.assertFalse(downloader._newpipe_may_fall_back(LIST_URL, boom))

    def test_a_plain_video_failure_is_not_switched_to_ytdlp(self):
        boom = npe.HelperError("This video is not available in your country.",
                               kind="GeographicRestrictionException")
        with mock.patch.object(npe, "is_available", return_value=True):
            self.assertFalse(downloader._newpipe_may_fall_back(YT_URL, boom))

    def test_a_login_error_on_a_plain_video_falls_back(self):
        boom = npe.HelperError("age", kind="AgeRestrictedContentException")
        self.assertTrue(downloader._newpipe_may_fall_back(YT_URL, boom))

    def test_other_sites_only_fall_back_for_logins(self):
        boom = RuntimeError("NewPipe found no downloadable streams for this link.")
        with mock.patch.object(npe, "is_available", return_value=True):
            self.assertFalse(downloader._newpipe_may_fall_back("https://soundcloud.com/a/sets/b", boom))

    def test_a_working_newpipe_playlist_is_left_alone(self):
        def fake_fetch(url, settings=None, force_noplaylist=False, limit=200):
            if "playlist?list=" in url:
                return npe.to_ytdlp_playlist(_playlist_answer())
            return npe.to_ytdlp_info(_stream_answer([_video(360, 600_000, 30_000_000)]))
        with mock.patch.object(npe, "fetch", side_effect=fake_fetch), \
             mock.patch.object(downloader, "_head_size", return_value=None):
            info = downloader.fetch_info(LIST_URL, self.SETTINGS)
        self.assertEqual(info["extractor"], "NewPipe")
        self.assertEqual([e["title"] for e in info["entries"]], ["One", "Two"])
        self.assertTrue(info["formats"])


if __name__ == "__main__":
    unittest.main()
