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


if __name__ == "__main__":
    unittest.main()
