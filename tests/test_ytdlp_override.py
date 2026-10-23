import _setup  # noqa: F401  (must stay first)

import io
import os
import tempfile
import unittest
import zipfile
from unittest import mock

from core import updater, ytdlp_loader


def _make_override(root, marker):
    os.makedirs(os.path.join(root, "yt_dlp"))
    open(os.path.join(root, "yt_dlp", "__init__.py"), "w").close()
    if marker is not None:
        with open(os.path.join(root, ytdlp_loader.APP_VERSION_MARKER), "w") as f:
            f.write(marker)


class StaleOverrideTests(unittest.TestCase):
    def _case(self, marker, app_version):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        override = os.path.join(tmp.name, "ytdlp_override")
        _make_override(override, marker)
        with mock.patch.object(ytdlp_loader, "OVERRIDE_DIR", override), \
                mock.patch.object(ytdlp_loader, "_app_version", return_value=app_version):
            removed = ytdlp_loader.discard_stale_override()
        return removed, os.path.isdir(override)

    def test_kept_for_the_same_app_version(self):
        self.assertEqual(self._case("2.9.1", "2.9.1"), (False, True))

    def test_dropped_after_an_app_upgrade(self):
        self.assertEqual(self._case("2.9.0.1", "2.9.1"), (True, False))

    def test_dropped_when_it_has_no_marker(self):
        self.assertEqual(self._case(None, "2.9.1"), (True, False))


def _wheel(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


class InstallWheelsTests(unittest.TestCase):
    def test_installs_both_packages_marker_and_blocks_traversal(self):
        ytdlp = _wheel({"yt_dlp/__init__.py": "", "yt_dlp/version.py": "__version__='1'",
                        "yt_dlp/../evil.py": "x", "other/readme.txt": "x"})
        ejs = _wheel({"yt_dlp_ejs/__init__.py": "", "yt_dlp_ejs/yt/solver/core.min.js": "js"})
        with tempfile.TemporaryDirectory() as tmp:
            override = os.path.join(tmp, "ytdlp_override")
            with mock.patch.object(ytdlp_loader, "OVERRIDE_DIR", override):
                updater._install_wheels([(ytdlp, "yt_dlp"), (ejs, "yt_dlp_ejs")])
            self.assertTrue(os.path.isfile(os.path.join(override, "yt_dlp", "version.py")))
            self.assertTrue(os.path.isfile(os.path.join(override, "yt_dlp_ejs", "yt", "solver", "core.min.js")))
            self.assertTrue(os.path.isfile(os.path.join(override, ytdlp_loader.APP_VERSION_MARKER)))
            self.assertFalse(os.path.exists(os.path.join(override, "evil.py")))
            self.assertFalse(os.path.exists(os.path.join(tmp, "evil.py")))
            self.assertFalse(os.path.exists(os.path.join(override, "other")))

    def test_incomplete_yt_dlp_wheel_is_rejected_and_nothing_replaced(self):
        broken = _wheel({"yt_dlp/__init__.py": ""})          # no version.py
        with tempfile.TemporaryDirectory() as tmp:
            override = os.path.join(tmp, "ytdlp_override")
            os.makedirs(override)
            with mock.patch.object(ytdlp_loader, "OVERRIDE_DIR", override):
                with self.assertRaises(RuntimeError):
                    updater._install_wheels([(broken, "yt_dlp")])
            self.assertTrue(os.path.isdir(override))
            self.assertFalse(os.path.exists(override + ".new"))


class PinnedEjsTests(unittest.TestCase):
    def test_finds_the_default_extra_pin(self):
        req = ['brotli; extra == "default"', 'yt-dlp-ejs==0.8.0; extra == "default"',
               'yt-dlp-ejs==0.8.0; extra == "pin"']
        self.assertEqual(updater._pinned_ejs_version(req), "0.8.0")

    def test_none_when_absent(self):
        self.assertIsNone(updater._pinned_ejs_version(['requests; extra == "default"']))
        self.assertIsNone(updater._pinned_ejs_version(None))


if __name__ == "__main__":
    unittest.main()
