"""Start-up cost: the Settings pages are built on first use, memory is handed back after
start-up and when the window is hidden, and the clipboard is watched only while it matters."""
import _setup  # noqa: F401  (must stay first)

import json
import os
import subprocess
import sys
import unittest
from unittest import mock

from core import platform_utils
from ui.dialogs import LazyPages

R = _setup.ROOT


def _read(*parts):
    with open(os.path.join(R, *parts), encoding="utf-8") as f:
        return f.read()


class LazyPagesTests(unittest.TestCase):
    def setUp(self):
        self.made = []
        self.pages = LazyPages({"a": lambda: self.made.append("a") or "page-a",
                                "b": lambda: self.made.append("b") or "page-b"})

    def test_nothing_is_built_by_looking(self):
        self.assertIn("a", self.pages)
        self.assertNotIn("zzz", self.pages)
        self.assertEqual(list(self.pages), ["a", "b"])
        self.assertEqual(len(self.pages), 2)
        self.assertEqual(sorted(self.pages.keys()), ["a", "b"])
        self.assertFalse(self.pages.built("a"))
        self.assertEqual(self.made, [])

    def test_a_page_is_built_once_on_first_use(self):
        self.assertEqual(self.pages["a"], "page-a")
        self.assertEqual(self.pages["a"], "page-a")
        self.assertEqual(self.made, ["a"])
        self.assertTrue(self.pages.built("a"))
        self.assertFalse(self.pages.built("b"))

    def test_get_and_unknown_keys(self):
        self.assertIsNone(self.pages.get("zzz"))
        self.assertEqual(self.pages.get("b"), "page-b")
        with self.assertRaises(KeyError):
            self.pages["zzz"]


class TrimMemoryTests(unittest.TestCase):
    def setUp(self):
        self._lib = platform_utils._libc

    def tearDown(self):
        platform_utils._libc = self._lib

    def test_it_returns_a_bool_and_never_raises(self):
        self.assertIsInstance(platform_utils.trim_memory(), bool)

    def test_not_linux_does_nothing(self):
        with mock.patch.object(platform_utils.sys, "platform", "win32"):
            self.assertFalse(platform_utils.trim_memory())

    def test_a_system_without_malloc_trim_is_fine(self):
        platform_utils._libc = None
        import ctypes
        with mock.patch.object(ctypes, "CDLL", side_effect=OSError("no libc.so.6")):
            self.assertFalse(platform_utils.trim_memory())

    @unittest.skipUnless(sys.platform.startswith("linux"), "glibc only")
    def test_it_really_gives_memory_back(self):
        try:
            import psutil
        except ImportError:
            self.skipTest("psutil not installed")
        proc = psutil.Process()
        junk = [bytearray(2000) for _ in range(40000)]      # ~80 MB of small blocks
        del junk
        before = proc.memory_info().rss
        platform_utils.trim_memory()
        self.assertLess(proc.memory_info().rss, before + 1_000_000, "RSS must not grow")


class MainWindowProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            res = subprocess.run([sys.executable, os.path.join(R, "tests", "_main_window_probe.py"), R],
                                 capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            raise AssertionError("the probe hung")
        lines = [l for l in res.stdout.splitlines() if l.startswith("{")]
        if not lines:
            raise AssertionError(f"probe printed no result.\nSTDOUT: {res.stdout[-800:]}\nSTDERR: {res.stderr[-1500:]}")
        cls.out = json.loads(lines[-1])
        if cls.out.get("no_display"):
            raise unittest.SkipTest("no display for Tk")

    def test_settings_pages_are_not_built_at_start(self):
        self.assertEqual(self.out["built_at_start"], {"settings": False, "cookies": False, "about": False})

    def test_the_pages_mapping_still_answers_for_every_name(self):
        self.assertEqual(self.out["pages_know_the_keys"], [True, True, True, True, True, True, False])

    def test_opening_one_page_builds_only_that_one(self):
        self.assertEqual(self.out["after_opening_cookies"], {"settings": False, "cookies": True, "about": False})
        self.assertTrue(self.out["settings_page_is_raised_and_built"])

    def test_closing_the_window_before_the_general_page_exists_is_safe(self):
        self.assertTrue(self.out["flush_before_build_builds_nothing"])

    def test_the_startup_update_check_does_not_build_the_about_page(self):
        self.assertTrue(self.out["start_up_check_uses_the_quiet_path"])
        self.assertEqual(self.out["update_button_told"], ["999.0.0"])
        self.assertTrue(self.out["about_still_not_built"])
        self.assertTrue(self.out["latest_release_remembered"])
        self.assertTrue(self.out["a_failed_check_changes_nothing"])

    def test_the_about_page_shows_what_the_quiet_check_found(self):
        self.assertTrue(self.out["about_shows_the_known_release"])

    def test_memory_trim_is_debounced_and_runs_once(self):
        self.assertTrue(self.out["trim_job_scheduled_once"])
        self.assertTrue(self.out["trim_runs_once_for_six_requests"])
        self.assertTrue(self.out["trim_job_cleared"])

    def test_clipboard_is_watched_only_while_the_add_download_window_is_open(self):
        self.assertEqual(self.out["clipboard_checks_while_closed"], 0)
        self.assertGreaterEqual(self.out["clipboard_checks_while_open"], 1)
        self.assertTrue(self.out["watch_stops_on_withdraw"])
        self.assertEqual(self.out["clipboard_checks_after_withdraw"], 0)
        self.assertTrue(self.out["watch_restarts_on_map"])


class SourceTests(unittest.TestCase):
    def test_start_up_and_hiding_schedule_a_trim(self):
        main = _read("ui", "main_window.py")
        self.assertIn("self.schedule_memory_trim(3000)", main)
        self.assertIn("self.schedule_memory_trim(1000)", main)

    def test_no_code_reads_the_settings_pages_directly_any_more(self):
        """pages["settings"] etc. would build a page; only the places that mean to are allowed."""
        for folder in ("ui", "core"):
            for root, _, files in os.walk(os.path.join(R, folder)):
                for name in files:
                    if name.endswith(".py") and name not in ("dialogs.py", "main_window.py"):
                        with open(os.path.join(root, name), encoding="utf-8") as f:
                            src = f.read()
                        self.assertNotIn('pages["settings"]', src, name)
                        self.assertNotIn('pages["about"]', src, name)


if __name__ == "__main__":
    unittest.main()
