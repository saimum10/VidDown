import _setup  # noqa: F401  (must stay first)

import copy
import os
import time
import unittest
from unittest import mock

from core import downloader


class SizeLookupOptionsTests(unittest.TestCase):
    def test_size_lookup_does_not_read_browser_cookies_or_start_js(self):
        seen = {}

        class Ydl:
            def __init__(self, opts):
                seen.update(opts)
            def __enter__(self): return self
            def __exit__(self, *a): return False

        info = {"formats": [{"format_id": "b", "ext": "mp4", "protocol": "https", "vcodec": "avc1",
                             "acodec": "none", "url": "https://x/b.mp4"}]}
        with mock.patch.object(downloader, "_head_size", return_value=5000), \
                mock.patch.object(downloader.yt_dlp, "YoutubeDL", Ydl):
            downloader.fill_exact_sizes(info, {"cookies_browser": "firefox", "use_cookies": True})
        self.assertNotIn("cookiesfrombrowser", seen)
        self.assertNotIn("js_runtimes", seen)
        self.assertEqual(info["formats"][0]["filesize"], 5000)


class CookiesWithoutJsRuntimeTests(unittest.TestCase):
    SETTINGS = {"cookies_browser": "firefox", "use_cookies": True}

    def test_youtube_without_js_leaves_cookies_out(self):
        # A signed-in YouTube request only uses clients that need JS; with no runtime
        # that means "no formats" for a link that works fine without cookies.
        with mock.patch.object(downloader, "js_runtime_found", return_value=False):
            opts = downloader.base_ydl_opts(self.SETTINGS, "https://www.youtube.com/watch?v=abc")
            self.assertNotIn("cookiesfrombrowser", opts)
            other = downloader.base_ydl_opts(self.SETTINGS, "https://vimeo.com/123")
            self.assertIn("cookiesfrombrowser", other)          # other sites keep their cookies
            self.assertIn("cookiesfrombrowser", downloader.base_ydl_opts(self.SETTINGS))   # no link given

    def test_youtube_with_js_keeps_cookies(self):
        with mock.patch.object(downloader, "js_runtime_found", return_value=True):
            opts = downloader.base_ydl_opts(self.SETTINGS, "https://youtu.be/abc")
            self.assertIn("cookiesfrombrowser", opts)

    def test_is_youtube_url(self):
        for url in ("https://www.youtube.com/watch?v=1", "https://youtu.be/1", "https://music.youtube.com/x",
                    "https://m.youtube.com/watch?v=1"):
            self.assertTrue(downloader.is_youtube_url(url), url)
        for url in ("https://notyoutube.com/x", "https://vimeo.com/1", "", None, "not a url"):
            self.assertFalse(downloader.is_youtube_url(url), url)


class FriendlyErrorTests(unittest.TestCase):
    def setUp(self):
        from ui.pages.downloads_page import friendly_error
        self.fe = friendly_error

    def test_known_reasons(self):
        # The message must not claim "YouTube offered nothing": the same error
        # also comes from a codec/quality filter that matched nothing, on any site.
        msg = self.fe("ERROR: [youtube] x: Requested format is not available")
        self.assertIn("No download format matched", msg)
        self.assertNotIn("YouTube offered no", msg)
        self.assertIn("age-restricted", self.fe("ERROR: Sign in to confirm your age"))
        self.assertIn("timed out", self.fe("<urlopen error timed out>"))
        self.assertIn("disk is full", self.fe("[Errno 28] No space left on device"))

    def test_ffmpeg_postprocessing_failure_is_not_blamed_on_missing_ffmpeg(self):
        self.assertIn("converting it failed", self.fe("ERROR: Postprocessing: Conversion failed!"))

    def test_unknown_text_is_shortened(self):
        out = self.fe("ERROR: " + "x" * 400)
        self.assertLessEqual(len(out), 140)
        self.assertFalse(out.startswith("ERROR"))


class PlaylistQualityIsolationTests(unittest.TestCase):
    def test_deepcopy_per_job_keeps_jobs_independent(self):
        q = {"format_selector": "bv*+ba", "audio_only": False, "container": "mp4"}
        a, b = copy.deepcopy(q), copy.deepcopy(q)
        a["container"] = "mkv"
        self.assertEqual(b["container"], "mp4")


class RunInThreadTests(unittest.TestCase):
    def _root(self):
        import tkinter as tk
        try:
            root = tk.Tk()
        except tk.TclError:
            self.skipTest("no display")
        root.withdraw()
        return root

    def _pump(self, root, box, secs=3):
        end = time.time() + secs
        while time.time() < end and "r" not in box:
            root.update()
            time.sleep(0.01)

    def test_result_and_error_reach_the_tk_thread(self):
        from ui.threads import run_in_thread
        root = self._root()
        try:
            box = {}
            run_in_thread(root, lambda: 42, lambda ok, err: box.update(r=(ok, err)))
            self._pump(root, box)
            self.assertEqual(box["r"], (42, None))

            box.clear()
            def boom(): raise ValueError("bad")
            run_in_thread(root, boom, lambda ok, err: box.update(r=(ok, err)))
            self._pump(root, box)
            self.assertIsNone(box["r"][0])
            self.assertIsInstance(box["r"][1], ValueError)
        finally:
            root.destroy()

    def test_destroyed_window_is_not_an_error(self):
        from ui.threads import run_in_thread
        root = self._root()
        called = []
        run_in_thread(root, lambda: time.sleep(0.2), lambda ok, err: called.append(1))
        root.destroy()
        time.sleep(0.5)          # the worker finishes; nothing must blow up
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
