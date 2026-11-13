import _setup  # noqa: F401  (must stay first)

import hashlib
import http.server
import os
import tempfile
import threading
import unittest
from unittest import mock

from core import app_updater as au


def _release(names, digests=None):
    digests = digests or {}
    assets = []
    for n in names:
        a = {"name": n, "browser_download_url": f"https://example.invalid/{n}", "size": 0}
        if n in digests:
            a["digest"] = digests[n]
        assets.append(a)
    return au.ReleaseInfo({"tag_name": "v2.9.1", "assets": assets})


ALL_ASSETS = [
    "viddown_2.9.1-1_amd64_with-js.deb", "viddown_2.9.1-1_amd64_no-js.deb",
    "VidDown_Setup_2.9.1_x64_with-js.exe", "VidDown_Setup_2.9.1_x64_no-js.exe",
    "VidDown_2.9.1_windows_x64_portable_with-js.zip", "SHA256SUMS.txt",
]


class VersionTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(au.parse_version("v2.9.0.1"), (2, 9, 0, 1))
        self.assertEqual(au.parse_version("2.9.0.1-beta"), (2, 9, 0, 1))
        self.assertEqual(au.parse_version(""), ())

    def test_is_newer(self):
        self.assertTrue(au.is_newer("2.9.1", "2.9.0.1"))
        self.assertTrue(au.is_newer("v2.10", "2.9.9"))
        self.assertTrue(au.is_newer("2.9.0.2", "2.9.0.1"))
        self.assertFalse(au.is_newer("2.9.0.1", "2.9.0.1"))
        self.assertFalse(au.is_newer("2.9", "2.9.0"))
        self.assertFalse(au.is_newer("2.9.0.1", "2.9.1"))
        self.assertFalse(au.is_newer("", "1.0"))
        self.assertFalse(au.is_newer("nonsense", "1.0"))


class AssetTests(unittest.TestCase):
    def test_pick_deb_by_arch_and_variant(self):
        r = _release(ALL_ASSETS)
        self.assertEqual(au.pick_asset(r, au.KIND_DEB, "with-js", "amd64")["name"],
                         "viddown_2.9.1-1_amd64_with-js.deb")
        self.assertEqual(au.pick_asset(r, au.KIND_DEB, "no-js", "amd64")["name"],
                         "viddown_2.9.1-1_amd64_no-js.deb")
        self.assertIsNone(au.pick_asset(r, au.KIND_DEB, "with-js", "arm64"))

    def test_pick_windows_installer_not_portable(self):
        r = _release(ALL_ASSETS)
        self.assertEqual(au.pick_asset(r, au.KIND_WIN_INSTALLER, "no-js")["name"],
                         "VidDown_Setup_2.9.1_x64_no-js.exe")

    def test_legacy_unsuffixed_names_do_not_match(self):
        r = _release(["viddown_2.9.0.1-1_amd64.deb", "VidDown_Setup_2.9.0.1_x64.exe"])
        self.assertIsNone(au.pick_asset(r, au.KIND_DEB, "no-js", "amd64"))
        self.assertIsNone(au.pick_asset(r, au.KIND_WIN_INSTALLER, "no-js"))

    def test_other_kinds_get_nothing(self):
        r = _release(ALL_ASSETS)
        for kind in (au.KIND_SOURCE, au.KIND_WIN_PORTABLE, au.KIND_OTHER):
            self.assertIsNone(au.pick_asset(r, kind, "with-js"))


class ChecksumTests(unittest.TestCase):
    H1, H2 = "a" * 64, "B" * 64

    def test_parse(self):
        text = f"{self.H1}  one.deb\n{self.H2} *two.exe\nnot a line\n"
        self.assertEqual(au.parse_checksums(text), {"one.deb": self.H1, "two.exe": self.H2.lower()})

    def test_expected_from_sums_file(self):
        r = _release(["x.deb", "SHA256SUMS.txt"])
        with mock.patch.object(au, "_http_get", return_value=f"{self.H1}  x.deb\n".encode()):
            self.assertEqual(au.fetch_expected_sha256(r, r.asset("x.deb")), self.H1)

    def test_falls_back_to_github_digest(self):
        r = _release(["x.deb"], digests={"x.deb": "sha256:" + self.H1})
        self.assertEqual(au.fetch_expected_sha256(r, r.asset("x.deb")), self.H1)

    def test_refuses_without_any_checksum(self):
        r = _release(["x.deb"])
        with self.assertRaises(au.UpdateError):
            au.fetch_expected_sha256(r, r.asset("x.deb"))

    def test_refuses_when_file_missing_from_sums_and_no_digest(self):
        r = _release(["x.deb", "SHA256SUMS.txt"])
        with mock.patch.object(au, "_http_get", return_value=f"{self.H1}  other.deb\n".encode()):
            with self.assertRaises(au.UpdateError):
                au.fetch_expected_sha256(r, r.asset("x.deb"))


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.serve = os.path.join(self.tmp.name, "serve")
        os.makedirs(self.serve)
        self.payload = os.urandom(900_000)
        with open(os.path.join(self.serve, "f.bin"), "wb") as f:
            f.write(self.payload)
        handler = lambda *a, **k: _Quiet(*a, directory=self.serve, **k)
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.updates = os.path.join(self.tmp.name, "updates")
        self.patch = mock.patch.object(au, "UPDATES_DIR", self.updates)
        self.patch.start()
        self.asset = {"name": "f.bin", "size": len(self.payload),
                      "browser_download_url": f"http://127.0.0.1:{self.httpd.server_port}/f.bin"}
        self.sha = hashlib.sha256(self.payload).hexdigest()

    def tearDown(self):
        self.patch.stop()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def test_download_verifies_and_reports_progress(self):
        seen = []
        path = au.download_asset(self.asset, self.sha, progress=lambda d, t: seen.append((d, t)))
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), self.payload)
        self.assertEqual(seen[-1], (len(self.payload), len(self.payload)))
        self.assertFalse(os.path.exists(path + ".part"))

    def test_checksum_mismatch_deletes_everything(self):
        with self.assertRaises(au.UpdateError):
            au.download_asset(self.asset, "0" * 64)
        self.assertEqual(os.listdir(self.updates), [])

    def test_cancel(self):
        ev = threading.Event()
        ev.set()
        with self.assertRaises(au.Cancelled):
            au.download_asset(self.asset, self.sha, cancel=ev)
        self.assertEqual(os.listdir(self.updates), [])

    def test_verified_file_is_reused_and_old_files_are_purged(self):
        au.download_asset(self.asset, self.sha)
        stale = os.path.join(self.updates, "old-version.deb")
        open(stale, "w").close()
        self.httpd.shutdown()                    # a second download would now fail
        path = au.download_asset(self.asset, self.sha)
        self.assertTrue(os.path.isfile(path))
        self.assertFalse(os.path.exists(stale))

    def test_asset_name_cannot_escape_the_folder(self):
        evil = dict(self.asset, name="../../evil.bin")
        path = au.download_asset(evil, self.sha)
        self.assertEqual(os.path.dirname(path), self.updates)


class InstallDebTests(unittest.TestCase):
    def _run(self, returncode):
        proc = mock.Mock(returncode=returncode, stdout="", stderr="boom")
        with mock.patch.object(au.shutil, "which", return_value="/usr/bin/pkexec"), \
                mock.patch.dict(os.environ, {"DISPLAY": ":0"}), \
                mock.patch.object(au.subprocess, "run", return_value=proc) as run, \
                mock.patch.object(au, "errorlog"):
            try:
                return au._install_deb("/tmp/x.deb"), run
            except au.UpdateError as exc:
                return exc, run

    def test_success(self):
        out, run = self._run(0)
        self.assertEqual(out, au.OUTCOME_RESTART)
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[0], "pkexec")
        self.assertIn("--reinstall", cmd)
        self.assertEqual(cmd[-1], "/tmp/x.deb")

    def test_dismissed_prompt_is_not_an_error(self):
        for code in (126, 127):
            self.assertEqual(self._run(code)[0], au.OUTCOME_CANCELLED)

    def test_failure_raises(self):
        self.assertIsInstance(self._run(100)[0], au.UpdateError)

    def test_terminal_fallback_without_pkexec(self):
        with mock.patch.object(au.shutil, "which", return_value=None), \
                mock.patch.object(au.subprocess, "Popen") as popen:
            self.assertEqual(au._install_deb("/tmp/a b.deb"), au.OUTCOME_TERMINAL)
        script = popen.call_args[0][0][-1]
        self.assertIn("'/tmp/a b.deb'", script)      # shell-quoted


if __name__ == "__main__":
    unittest.main()
