import _setup  # noqa: F401  (must stay first)

import hashlib
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from core import browser_cookies as bc

try:
    import yt_dlp.cookies as ytc
    from yt_dlp.aes import aes_cbc_encrypt_bytes
except Exception:                      # pragma: no cover
    ytc = None


def _encrypt(password, value, prefix=b"v10"):
    key = hashlib.pbkdf2_hmac("sha1", password, b"saltysalt", 1, 16)
    data = value.encode()
    pad = 16 - len(data) % 16
    data += bytes([pad]) * pad
    return prefix + bytes(aes_cbc_encrypt_bytes(data, key, b" " * 16))


def make_cookie_db(path, password, prefix):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE meta (key TEXT, value TEXT)")
    con.execute("INSERT INTO meta VALUES ('version', '10')")
    con.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT, encrypted_value BLOB,"
                " path TEXT, expires_utc INTEGER, is_secure INTEGER)")
    for i in range(3):
        con.execute("INSERT INTO cookies VALUES (?,?,?,?,?,?,?)",
                    (".example.com", f"c{i}", "", _encrypt(password, f"value{i}", prefix), "/", 0, 1))
    con.commit()
    con.close()


@unittest.skipIf(ytc is None, "yt-dlp not importable")
class ProbeWithRealDatabases(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        env = {"XDG_CONFIG_HOME": self.tmp, "XDG_CURRENT_DESKTOP": "LXQt", "HOME": self.tmp}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_basic_text_cookies_are_read_without_a_keyring(self):
        make_cookie_db(os.path.join(self.tmp, "BraveSoftware/Brave-Browser/Default/Cookies"),
                       b"peanuts", b"v10")
        result = bc.autodetect("brave")
        self.assertTrue(result.good, result.problems)
        self.assertEqual(result.count, 3)

    def test_keyring_cookies_need_the_right_keyring(self):
        # LXQt makes yt-dlp assume "no keyring", but this browser used the GNOME one.
        make_cookie_db(os.path.join(self.tmp, "chromium/Default/Cookies"), b"gnome-secret", b"v11")
        plain = bc.probe("chromium")
        self.assertEqual(plain.count, 0)
        self.assertGreater(plain.failed, 0)
        with mock.patch.object(ytc, "_get_gnome_keyring_password", return_value=b"gnome-secret"):
            result = bc.autodetect("chromium")
        self.assertTrue(result.good, result.problems)
        self.assertEqual(result.keyring, "GNOMEKEYRING")

    def test_some_readable_cookies_still_count_as_working(self):
        # What a real Brave looks like when its keyring cannot be opened: a few
        # cookies are stored in the clear, the rest are encrypted. The old Verify
        # called that "found"; it must not turn into a failure.
        path = os.path.join(self.tmp, "BraveSoftware/Brave-Browser/Default/Cookies")
        make_cookie_db(path, b"unreachable-keyring-password", b"v11")
        con = sqlite3.connect(path)
        con.execute("INSERT INTO cookies VALUES ('.example.com','plain1','abc',x'', '/', 0, 1)")
        con.execute("INSERT INTO cookies VALUES ('.example.com','plain2','def',x'', '/', 0, 1)")
        con.commit(); con.close()
        result = bc.autodetect("brave")
        self.assertTrue(result.usable)
        self.assertEqual(result.count, 2)
        self.assertEqual(result.failed, 3)
        ok, text = bc.describe(result, "Brave")
        self.assertTrue(ok, text)
        self.assertIn("2 cookies found", text)
        self.assertIn("3 more could not be read", text)
        s = {"cookies_browser": "brave"}
        bc.remember(s, result)                 # still remembered: downloads can use what was read

    def test_snap_profile_is_found_when_the_normal_folder_is_empty(self):
        make_cookie_db(os.path.join(self.tmp, "snap/chromium/common/chromium/Default/Cookies"),
                       b"peanuts", b"v10")
        self.assertEqual(bc.packaged_profile_dirs("chromium", home=self.tmp),
                         [os.path.join(self.tmp, "snap/chromium/common/chromium")])
        result = bc.autodetect("chromium", home=self.tmp)
        self.assertTrue(result.good, result.problems)
        self.assertTrue(result.profile.endswith("snap/chromium/common/chromium"))

    def test_nothing_installed_is_reported_as_not_found(self):
        result = bc.autodetect("vivaldi", home=self.tmp)
        self.assertTrue(result.not_found)
        ok, text = bc.describe(result, "Vivaldi")
        self.assertFalse(ok)
        self.assertIn("No Vivaldi profile", text)


class AutodetectCollectsReasons(unittest.TestCase):
    def test_a_missing_secretstorage_reaches_the_message(self):
        calls = []

        def extract(browser, profile, logger, **kw):
            calls.append(kw.get("keyring"))
            if kw.get("keyring") == "GNOMEKEYRING":
                logger.error("secretstorage not available")
            logger.info("Extracted 0 cookies from brave (40 could not be decrypted)")
            return []

        with mock.patch.object(bc, "_linux", return_value=True):
            r = bc.autodetect("brave", extract=extract, home=tempfile.mkdtemp())
        self.assertIn("GNOMEKEYRING", calls)
        ok, text = bc.describe(r, "Brave", platform="linux")
        self.assertFalse(ok)
        self.assertIn("secretstorage", text)


class SettingsAndMessages(unittest.TestCase):
    def test_spec(self):
        self.assertIsNone(bc.ydl_spec({}))
        self.assertEqual(bc.ydl_spec({"cookies_browser": "firefox"}), ("firefox",))
        self.assertEqual(bc.ydl_spec({"cookies_browser": "brave", "cookies_profile": "/p"}), ("brave", "/p"))
        with mock.patch.object(bc, "_linux", return_value=True):
            self.assertEqual(bc.ydl_spec({"cookies_browser": "brave", "cookies_keyring": "KWALLET5"}),
                             ("brave", None, "KWALLET5"))
            # a keyring is meaningless for Firefox
            self.assertEqual(bc.ydl_spec({"cookies_browser": "firefox", "cookies_keyring": "KWALLET5"}),
                             ("firefox",))

    def test_remember_and_forget(self):
        s = {}
        good = bc.Probe("brave", "/p", "GNOMEKEYRING")
        good.count = 5
        bc.remember(s, good)
        self.assertEqual((s["cookies_profile"], s["cookies_keyring"]), ("/p", "GNOMEKEYRING"))
        bc.remember(s, bc.Probe("brave"))          # a failed probe clears it
        self.assertEqual((s["cookies_profile"], s["cookies_keyring"]), ("", ""))

    def test_messages_explain_the_cause(self):
        r = bc.Probe("brave")
        r.failed = 40
        r.problems = ["secretstorage not available"]
        ok, text = bc.describe(r, "Brave", platform="linux")
        self.assertFalse(ok)
        self.assertIn("secretstorage", text)

        r = bc.Probe("chrome")
        r.failed = 40                          # nothing readable at all
        ok, text = bc.describe(r, "Google Chrome", platform="win32")
        self.assertIn("Firefox", text)
        self.assertIn("cookies.txt", text)

        r = bc.Probe("edge")
        r.problems = ["Could not copy Chrome cookie database"]
        self.assertIn("Close", bc.describe(r, "Microsoft Edge", platform="win32")[1])

        r = bc.Probe("firefox")
        self.assertIn("No cookies found", bc.describe(r, "Mozilla Firefox")[1])


if __name__ == "__main__":
    unittest.main()
