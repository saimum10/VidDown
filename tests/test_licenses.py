import _setup  # noqa: F401  (must stay first)

import os
import unittest

from core import buildinfo

ROOT = _setup.ROOT


class LicenseFilesTests(unittest.TestCase):
    def test_license_files_exist_and_are_found(self):
        for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
            self.assertTrue(os.path.isfile(os.path.join(ROOT, name)), name)
            self.assertEqual(os.path.realpath(buildinfo.find_doc(name)),
                             os.path.realpath(os.path.join(ROOT, name)))
        self.assertIsNone(buildinfo.find_doc("no-such-file.txt"))

    def test_license_is_mit_and_names_the_author(self):
        with open(os.path.join(ROOT, "LICENSE"), encoding="utf-8") as f:
            text = f.read()
        self.assertTrue(text.startswith("MIT License"))
        self.assertIn("SAIMUM HABIB", text)

    def test_notices_cover_every_tool_the_build_scripts_bundle(self):
        with open(os.path.join(ROOT, "THIRD_PARTY_NOTICES.md"), encoding="utf-8") as f:
            notices = f.read().lower()
        for name in ("yt-dlp", "yt-dlp-ejs", "ffmpeg", "aria2", "deno", "pillow", "pyinstaller",
                     "certifi", "websockets", "pycryptodomex", "brotli", "requests", "urllib3",
                     "secretstorage", "jeepney", "cryptography"):
            self.assertIn(name, notices, f"{name} is bundled but missing from THIRD_PARTY_NOTICES.md")
        self.assertIn("mutagen", notices)       # the deliberate exclusion is documented

    def test_build_scripts_ship_the_license_files_and_exclude_mutagen(self):
        for script in ("build_deb.sh", "build_windows.ps1"):
            with open(os.path.join(ROOT, script), encoding="utf-8") as f:
                text = f.read()
            self.assertIn("THIRD_PARTY_NOTICES.md", text, script)
            self.assertIn("LICENSE", text, script)
            self.assertIn("mutagen", text, script)


if __name__ == "__main__":
    unittest.main()
