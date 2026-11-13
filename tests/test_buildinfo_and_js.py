import _setup  # noqa: F401  (must stay first)

import os
import sys
import tempfile
import unittest
from unittest import mock

from core import buildinfo


class BuildInfoTests(unittest.TestCase):
    def setUp(self):
        buildinfo.get_variant.cache_clear()
        self.addCleanup(buildinfo.get_variant.cache_clear)

    def test_source_checkout(self):
        self.assertEqual(buildinfo.get_variant(), buildinfo.VARIANT_SOURCE)

    def _frozen(self, marker_text):
        with tempfile.TemporaryDirectory() as tmp:
            if marker_text is not None:
                with open(os.path.join(tmp, "build_variant.txt"), "w") as f:
                    f.write(marker_text)
            with mock.patch.object(sys, "frozen", True, create=True), \
                    mock.patch.object(buildinfo, "_resource_dirs", return_value=[tmp]):
                buildinfo.get_variant.cache_clear()
                return buildinfo.get_variant()

    def test_marker_values(self):
        self.assertEqual(self._frozen("with-js\n"), buildinfo.VARIANT_JS)
        self.assertEqual(self._frozen("no-js"), buildinfo.VARIANT_NOJS)

    def test_old_packages_without_marker_count_as_no_js(self):
        self.assertEqual(self._frozen(None), buildinfo.VARIANT_NOJS)
        self.assertEqual(self._frozen("garbage"), buildinfo.VARIANT_NOJS)


try:
    import yt_dlp  # noqa: F401
    from core import downloader
except ImportError:                    # yt-dlp not installed: skip the engine tests
    downloader = None


@unittest.skipIf(downloader is None, "yt-dlp not installed")
class JsRuntimeOptsTests(unittest.TestCase):
    def test_bundled_deno_is_used_exactly(self):
        with mock.patch.object(downloader, "find_bundled_binary", return_value="/app/bin/deno"):
            self.assertEqual(downloader.js_runtime_opts(), {"deno": {"path": "/app/bin/deno"}})

    def test_without_bundled_runtime_every_known_one_is_enabled(self):
        with mock.patch.object(downloader, "find_bundled_binary", return_value=None):
            self.assertEqual(set(downloader.js_runtime_opts()), {"deno", "node", "bun", "quickjs"})

    def test_options_reach_yt_dlp(self):
        with mock.patch.object(downloader, "find_bundled_binary", return_value=None):
            self.assertIn("js_runtimes", downloader.base_ydl_opts({}))

    def test_rate_limit_parser(self):
        self.assertEqual(downloader._parse_rate_limit("2 MB/s"), 2 * 1024 ** 2)
        self.assertEqual(downloader._parse_rate_limit("500K"), 500 * 1024)
        self.assertIsNone(downloader._parse_rate_limit("fast"))


if __name__ == "__main__":
    unittest.main()
