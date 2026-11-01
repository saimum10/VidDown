"""Regression tests for: progress bar with merged streams, portrait quality
labels, codec fallback, playlist sampling, process tracking of merge ffmpeg,
Pause-vs-Cancel file handling."""
import _setup  # noqa: F401  (must stay first)

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

try:
    import yt_dlp  # noqa: F401
    from core import downloader
except ImportError:                      # yt-dlp missing: skip
    downloader = None


def _fmt(fid, w, h, v="avc1", a="none", **kw):
    d = {"format_id": fid, "ext": "mp4", "vcodec": v, "acodec": a, "width": w, "height": h,
         "protocol": "https", "url": f"http://x/{fid}", "filesize": 1_000_000}
    d.update(kw)
    return d


@unittest.skipIf(downloader is None, "yt-dlp not installed")
class PortraitLabelTests(unittest.TestCase):
    def test_portrait_video_is_named_by_its_width(self):
        info = {"duration": 30, "formats": [_fmt("a", 1080, 1920), _fmt("b", 720, 1280),
                                            _fmt("c", 480, 854), _fmt("au", None, None, v="none", a="mp4a", ext="m4a")]}
        opts = [o for o in downloader.build_quality_options(info) if not o["audio_only"]]
        labels = [o["label"] for o in opts]
        self.assertEqual([l.split()[0] for l in labels[:3]], ["1080p", "720p", "480p"])
        self.assertTrue(all(o["axis"] == "width" for o in opts))
        self.assertEqual(opts[0]["format_selector"], "bestvideo[width<=1080]+bestaudio/best[width<=1080]")

    def test_landscape_is_unchanged(self):
        info = {"duration": 30, "formats": [_fmt("a", 1920, 1080), _fmt("b", 1280, 720)]}
        opts = [o for o in downloader.build_quality_options(info) if not o["audio_only"]]
        self.assertEqual([o["height"] for o in opts], [1080, 720])
        self.assertEqual(opts[0]["format_selector"], "bestvideo[height<=1080]+bestaudio/best[height<=1080]")
        self.assertEqual(opts[0]["axis"], "height")

    def test_codec_selector_uses_axis_and_ends_with_unfiltered_fallback(self):
        sel = downloader._codec_selector(1080, "AV1", axis="width")
        self.assertIn("bestvideo[width<=1080][vcodec~=", sel)
        self.assertTrue(sel.endswith("/bestvideo[width<=1080]+bestaudio/best[width<=1080]"), sel)

    def test_codec_fallback_really_selects_when_codec_missing(self):
        """A playlist video with no AV1 must still resolve (previously: error)."""
        formats = [_fmt("v", 1920, 1080, v="avc1.64"), _fmt("a", None, None, v="none", a="mp4a", ext="m4a")]
        sel = downloader._codec_selector(1080, "AV1")
        self.assertIsNotNone(downloader._estimate_from(formats, 10, sel))


@unittest.skipIf(downloader is None, "yt-dlp not installed")
class PlaylistFetchTests(unittest.TestCase):
    def _run(self, entries, details, playlist_count=None, url="https://www.youtube.com/playlist?list=PL1"):
        calls = []

        class FakeYDL:
            def __init__(self, opts):
                self.opts = opts

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def extract_info(self, u, download=False):
                calls.append(u)
                if u == url:
                    d = {"_type": "playlist", "title": "P", "entries": iter(entries)}
                    if playlist_count:
                        d["playlist_count"] = playlist_count
                    return d
                r = details[u]
                if isinstance(r, Exception):
                    raise r
                return dict(r)

        with mock.patch.object(downloader.yt_dlp, "YoutubeDL", FakeYDL), \
                mock.patch.object(downloader, "fill_exact_sizes", lambda i, s: i):
            return downloader.fetch_info(url, {}), calls

    def _e(self, i, title=None):
        return {"id": f"id{i}", "title": title or f"T{i}", "url": f"https://www.youtube.com/watch?v=id{i}",
                "webpage_url": f"https://www.youtube.com/watch?v=id{i}"}

    def test_failing_first_video_falls_through_to_the_next(self):
        entries = [self._e(0), self._e(1), self._e(2)]
        good = {"formats": [_fmt("v", 1920, 1080)], "subtitles": {}}
        details = {entries[0]["webpage_url"]: RuntimeError("Video unavailable"),
                   entries[1]["webpage_url"]: good, entries[2]["webpage_url"]: good}
        info, calls = self._run(entries, details)
        self.assertTrue(info["formats"])
        self.assertEqual(len(calls), 3)   # playlist + 2 samples

    def test_private_entries_are_skipped_without_a_request(self):
        entries = [self._e(0, "[Private video]"), self._e(1)]
        good = {"formats": [_fmt("v", 1920, 1080)], "subtitles": {}}
        info, calls = self._run(entries, {entries[1]["webpage_url"]: good})
        self.assertTrue(info["formats"])
        self.assertNotIn(entries[0]["webpage_url"], calls)

    def test_all_samples_failing_is_an_error_not_audio_only(self):
        entries = [self._e(i) for i in range(7)]
        details = {e["webpage_url"]: RuntimeError("boom") for e in entries}
        with self.assertRaises(RuntimeError) as cm:
            self._run(entries, details)
        self.assertIn("tried 5", str(cm.exception))

    def test_truncation_is_reported(self):
        entries = [self._e(i) for i in range(250)]
        good = {"formats": [_fmt("v", 1920, 1080)], "subtitles": {}}
        details = {entries[0]["webpage_url"]: good}
        info, _ = self._run(entries, details, playlist_count=250)
        self.assertEqual(info["_entry_count"], 200)
        self.assertTrue(info["_truncated"])
        self.assertEqual(info["_playlist_total"], 250)

    def test_short_playlist_is_not_flagged(self):
        entries = [self._e(i) for i in range(3)]
        good = {"formats": [_fmt("v", 1920, 1080)], "subtitles": {}}
        info, _ = self._run(entries, {entries[0]["webpage_url"]: good}, playlist_count=3)
        self.assertFalse(info["_truncated"])


@unittest.skipIf(downloader is None, "yt-dlp not installed")
class ProcessTrackingTests(unittest.TestCase):
    def test_postprocessor_popen_is_tracked_and_pause_spares_it(self):
        import yt_dlp.postprocessor.ffmpeg as ppf
        self.assertTrue(getattr(ppf.Popen, "_viddown_tracked", False))
        self.assertTrue(ppf.Popen._viddown_post)

        out = {}

        def worker():
            out["ident"] = threading.get_ident()
            with ppf.Popen([sys.executable, "-c", "import time; time.sleep(30)"]) as p:
                out["proc"] = p
                out["ready"] = True
                time.sleep(30)

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        for _ in range(100):
            if out.get("ready"):
                break
            time.sleep(0.05)
        self.assertTrue(out.get("ready"))
        try:
            downloader._kill_tracked(out["ident"], grace=0.2, include_post=False)   # Pause
            time.sleep(0.3)
            self.assertIsNone(out["proc"].poll(), "Pause must not kill a running merge")
            downloader._kill_tracked(out["ident"], grace=0.5, include_post=True)    # Cancel
            for _ in range(40):
                if out["proc"].poll() is not None:
                    break
                time.sleep(0.05)
            self.assertIsNotNone(out["proc"].poll(), "Cancel must stop the merge's ffmpeg")
        finally:
            try:
                out["proc"].kill()
            except Exception:
                pass
            downloader._forget_tracked(out["ident"])


if __name__ == "__main__":
    unittest.main()
