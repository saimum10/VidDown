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


class NewerOverrideSurvivesAppUpgradeTests(unittest.TestCase):
    """A VidDown version bump must not throw away a downloaded yt-dlp that is
    NEWER than the bundled one (that made working sites fail after upgrading)."""

    def _case(self, override_ver, bundled_ver, marker="2.9.0.2", app_version="2.9.0.4"):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        override = os.path.join(tmp.name, "ytdlp_override")
        _make_override(override, marker)
        if override_ver:
            with open(os.path.join(override, "yt_dlp", "version.py"), "w") as f:
                f.write(f"__version__ = '{override_ver}'\n")
        with mock.patch.object(ytdlp_loader, "OVERRIDE_DIR", override), \
                mock.patch.object(ytdlp_loader, "_app_version", return_value=app_version), \
                mock.patch.object(ytdlp_loader, "bundled_version", return_value=bundled_ver):
            removed = ytdlp_loader.discard_stale_override()
            kept = os.path.isdir(override)
            stamp = ytdlp_loader.override_app_version() if kept else None
        return removed, kept, stamp

    def test_newer_override_is_kept_and_adopted_for_the_new_version(self):
        self.assertEqual(self._case("2026.10.21", "2026.06.01"), (False, True, "2.9.0.4"))

    def test_older_override_is_still_dropped(self):
        self.assertEqual(self._case("2026.01.01", "2026.06.01"), (True, False, None))

    def test_same_version_is_dropped_the_bundled_one_is_as_good(self):
        self.assertEqual(self._case("2026.06.01", "2026.06.01"), (True, False, None))

    def test_unknown_bundled_version_keeps_the_old_safe_behaviour(self):
        self.assertEqual(self._case("2026.10.21", None), (True, False, None))

    def test_numeric_not_textual_comparison(self):
        self.assertEqual(self._case("2026.10.9", "2026.9.30"), (False, True, "2.9.0.4"))

    def test_bundled_version_is_read_from_the_installed_package(self):
        import yt_dlp.version
        self.assertEqual(ytdlp_loader.bundled_version(), yt_dlp.version.__version__)


class BundledVersionByImportTests(unittest.TestCase):
    """The frozen-build fallback must read the version AND leave no trace, so
    an override installed afterwards is what actually gets imported."""

    def test_reads_version_and_cleans_sys_modules(self):
        # in a fresh interpreter: this test must not disturb the modules the
        # rest of the suite has already imported and patched
        import subprocess, sys, textwrap
        code = textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {_setup.ROOT!r})
            from core import ytdlp_loader as L
            ver = L._bundled_version_by_import()
            leaked = [n for n in sys.modules if n.split('.', 1)[0] in L.OVERRIDE_PACKAGES]
            import yt_dlp.version as real
            print(ver == real.__version__ and ver is not None, leaked)
        """)
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.stdout.strip(), "True []", out.stderr)

    def test_not_used_when_the_override_finder_is_active(self):
        with mock.patch.object(ytdlp_loader, "_finder_installed", return_value=True):
            self.assertIsNone(ytdlp_loader._bundled_version_by_import())

    def test_failure_means_none(self):
        import builtins
        real_import = builtins.__import__

        def boom(name, *a, **k):
            if name.startswith("yt_dlp"):
                raise ImportError("nope")
            return real_import(name, *a, **k)
        with mock.patch("builtins.__import__", boom):
            self.assertIsNone(ytdlp_loader._bundled_version_by_import())

    def test_override_wins_after_the_probe(self):
        """End to end in a fresh interpreter: probe the bundled version, then
        install the override finder; `import yt_dlp` must come from the override."""
        import subprocess, sys, textwrap
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ov = os.path.join(tmp.name, "ytdlp_override")
        os.makedirs(os.path.join(ov, "yt_dlp"))
        with open(os.path.join(ov, "yt_dlp", "__init__.py"), "w") as f:
            f.write("FROM_OVERRIDE = True\n")
        with open(os.path.join(ov, "yt_dlp", "version.py"), "w") as f:
            f.write("__version__ = '2099.01.01'\n")
        code = textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {_setup.ROOT!r})
            from core import ytdlp_loader as L
            L.OVERRIDE_DIR = {ov!r}
            probed = L._bundled_version_by_import()
            sys.meta_path.insert(0, L._OverrideFinder())
            import yt_dlp
            print(probed is not None, getattr(yt_dlp, "FROM_OVERRIDE", False))
        """)
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.stdout.strip(), "True True", out.stderr)
