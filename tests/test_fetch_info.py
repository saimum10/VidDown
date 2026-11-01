import _setup  # noqa: F401  (must stay first)

import unittest
from unittest import mock

from core import downloader

STORYBOARDS_ONLY = {"title": "T", "formats": [
    {"format_id": "sb0", "ext": "mhtml", "vcodec": "none", "acodec": "none", "format_note": "storyboard"}]}
NORMAL = {"title": "T", "formats": [
    {"format_id": "18", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a", "height": 360}]}


def _fake_ydl(info, seen):
    class FakeYDL:
        def __init__(self, opts):
            seen.append(opts)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            return dict(info)
    return FakeYDL


class FetchInfoTests(unittest.TestCase):
    def fetch(self, info, url="https://youtu.be/abc"):
        seen = []
        with mock.patch.object(downloader.yt_dlp, "YoutubeDL", _fake_ydl(info, seen)):
            return downloader.fetch_info(url, {"_force_noplaylist": True}), seen

    def test_probe_never_runs_yt_dlps_format_selection(self):
        _info, seen = self.fetch(NORMAL)
        self.assertTrue(seen[0]["ignore_no_formats_error"])

    def test_normal_video_passes(self):
        info, _ = self.fetch(NORMAL)
        self.assertEqual(info["title"], "T")

    def test_storyboards_only_is_reported_with_the_likely_reason(self):
        with mock.patch.object(downloader, "js_runtime_found", return_value=False):
            with self.assertRaises(RuntimeError) as cm:
                self.fetch(STORYBOARDS_ONLY)
        self.assertIn("JavaScript runtime", str(cm.exception))

    def test_with_a_runtime_the_hint_is_about_updating(self):
        with mock.patch.object(downloader, "js_runtime_found", return_value=True):
            with self.assertRaises(RuntimeError) as cm:
                self.fetch(STORYBOARDS_ONLY)
        self.assertIn("Update yt-dlp", str(cm.exception))
        self.assertNotIn("JavaScript runtime", str(cm.exception))

    def test_other_sites_get_a_plain_message(self):
        with self.assertRaises(RuntimeError) as cm:
            self.fetch({"title": "T", "formats": []}, url="https://example.com/v")
        self.assertIn("No downloadable formats", str(cm.exception))


class SingleVideoUrlTests(unittest.TestCase):
    def test_detection(self):
        yes = ["https://youtu.be/bg7nRe9a-jI", "https://www.youtube.com/watch?v=abc",
               "https://m.youtube.com/watch?v=abc&t=5", "https://www.youtube.com/shorts/abc"]
        no = ["https://www.youtube.com/watch?v=abc&list=PL123", "https://www.youtube.com/playlist?list=PL1",
              "https://www.youtube.com/@name/videos", "https://youtu.be/", "https://vimeo.com/123", ""]
        for u in yes:
            self.assertTrue(downloader.is_single_youtube_video(u), u)
        for u in no:
            self.assertFalse(downloader.is_single_youtube_video(u), u)

    def test_url_bar_uses_the_same_options_as_the_extension_probe(self):
        seen = []
        with mock.patch.object(downloader.yt_dlp, "YoutubeDL", _fake_ydl(NORMAL, seen)):
            downloader.fetch_info("https://youtu.be/abc", {})
            downloader.fetch_info("https://youtu.be/abc", {"_force_noplaylist": True})
            downloader.fetch_info("https://www.youtube.com/playlist?list=PL1", {})
        keys = lambda o: (o.get("noplaylist"), o.get("extract_flat"), o.get("socket_timeout"))
        self.assertEqual(keys(seen[0]), keys(seen[1]))
        self.assertEqual(keys(seen[0]), (True, None, 20))
        self.assertEqual(keys(seen[2]), (None, "in_playlist", None))      # real playlists unchanged


if __name__ == "__main__":
    unittest.main()


class SampleEntryTests(unittest.TestCase):
    def test_link_into_a_playlist_samples_that_video(self):
        entries = [{"id": "a1"}, {"id": "b2"}, {"id": "c3"}]
        self.assertEqual(downloader._sample_entry(entries, "https://youtube.com/watch?v=b2&list=PL1")["id"], "b2")
        self.assertEqual(downloader._sample_entry(entries, "https://youtube.com/playlist?list=PL1")["id"], "a1")
        self.assertEqual(downloader._sample_entry(entries, "https://youtube.com/watch?v=zzz&list=PL1")["id"], "a1")


class ExactSizeTests(unittest.TestCase):
    def _info(self):
        return {"duration": 100, "formats": [
            {"format_id": "a", "ext": "webm", "protocol": "https", "vcodec": "none", "acodec": "opus",
             "url": "https://rr.googlevideo.com/videoplayback?itag=251&clen=1234567&dur=100", "tbr": 999},
            {"format_id": "b", "ext": "mp4", "protocol": "https", "vcodec": "avc1", "acodec": "none",
             "url": "https://rr.googlevideo.com/videoplayback?itag=137", "height": 1080, "tbr": 999},
            {"format_id": "c", "ext": "mp4", "protocol": "m3u8_native", "vcodec": "avc1", "acodec": "mp4a",
             "url": "https://x/manifest.m3u8", "height": 720},
            {"format_id": "d", "ext": "mhtml", "protocol": "mhtml", "vcodec": "none", "acodec": "none",
             "url": "https://x/sb0"},
            {"format_id": "e", "ext": "mp4", "protocol": "https", "vcodec": "avc1", "acodec": "none",
             "url": "https://x/e.mp4", "filesize": 55, "height": 360},
        ]}

    def test_clen_in_the_url_needs_no_request(self):
        self.assertEqual(downloader._clen_from_url("https://h/v?itag=1&clen=42&x=y"), 42)
        self.assertEqual(downloader._clen_from_url("https://h/v/clen/77/x"), 77)
        self.assertIsNone(downloader._clen_from_url("https://h/v?itag=1"))

    def test_fill(self):
        info = self._info()
        with mock.patch.object(downloader, "_head_size", return_value=4_000_000) as head, \
                mock.patch.object(downloader.yt_dlp, "YoutubeDL", _fake_ydl({}, [])):
            downloader.fill_exact_sizes(info, {})
        by_id = {f["format_id"]: f for f in info["formats"]}
        self.assertEqual(by_id["a"]["filesize"], 1_234_567)       # from the URL
        self.assertEqual(by_id["b"]["filesize"], 4_000_000)       # from a HEAD request
        self.assertNotIn("filesize", by_id["c"])                  # manifests are not asked
        self.assertNotIn("filesize", by_id["d"])                  # storyboards neither
        self.assertEqual(by_id["e"]["filesize"], 55)              # already exact: untouched
        self.assertEqual(head.call_count, 1)                      # only "b" needed a request

    def test_failed_lookups_leave_an_estimate(self):
        info = self._info()
        with mock.patch.object(downloader, "_head_size", return_value=None), \
                mock.patch.object(downloader.yt_dlp, "YoutubeDL", _fake_ydl({}, [])):
            downloader.fill_exact_sizes(info, {})
        self.assertNotIn("filesize", {f["format_id"]: f for f in info["formats"]}["b"])

    def test_estimates_are_labelled_exact_sizes_are_not(self):
        tbr_only = {"duration": 100, "formats": [
            {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2", "tbr": 128,
             "filesize_approx": 1_600_000, "protocol": "https"},
            {"format_id": "137", "ext": "mp4", "vcodec": "avc1", "acodec": "none", "height": 1080,
             "width": 1920, "tbr": 4000, "filesize_approx": 50_000_000, "protocol": "https"}]}
        opts = downloader.build_quality_options(tbr_only)
        self.assertTrue(opts[0]["size_label"].startswith("≈ "), opts[0]["size_label"])
        exact = {"duration": 100, "formats": [dict(f, filesize=f["filesize_approx"]) for f in tbr_only["formats"]]}
        opts = downloader.build_quality_options(exact)
        self.assertFalse(opts[0]["size_label"].startswith("≈"))
        self.assertEqual(downloader.size_text(downloader._Size(2048, False)), "≈ 2.0 KB")
        self.assertEqual(downloader.scaled_size_text(downloader._Size(1024, False), 3), "≈ 3.0 KB")
        self.assertEqual(downloader.scaled_size_text(1024, 3), "3.0 KB")
