"""The READMEs: short, English only, and every file / link they point to exists."""
import _setup  # noqa: F401  (must stay first)

import os
import re
import unittest

R = _setup.ROOT
README_FILES = ("README.md", os.path.join("browser_extension", "README.md"), os.path.join("bin", "README.txt"))


def _read(rel):
    with open(os.path.join(R, rel), encoding="utf-8") as f:
        return f.read()


class ReadmeTests(unittest.TestCase):
    def test_no_bengali_in_any_readme(self):
        for rel in README_FILES:
            self.assertEqual(re.findall(r"[\u0980-\u09FF]", _read(rel)), [], rel)

    def test_main_readme_is_short(self):
        self.assertLessEqual(len(_read("README.md").splitlines()), 100)

    def test_main_readme_has_the_essentials(self):
        t = _read("README.md")
        for needle in ("VidDown", "resources/icon.png", "## Features", "## Browser extension", "Load unpacked",
                       "## License", "MIT", "SAIMUM HABIB", "releases/latest"):
            self.assertIn(needle, t, needle)

    def test_license_named_in_readme_matches_the_license_file(self):
        lic = _read("LICENSE")
        self.assertTrue(lic.startswith("MIT License"))
        self.assertIn("SAIMUM HABIB", lic)

    def test_no_hard_coded_version_that_would_go_stale(self):
        """The version comes from the release badge; a typed-in number would be wrong after the next build."""
        self.assertNotRegex(_read("README.md"), r"\b\d+\.\d+\.\d+(\.\d+)?\b")

    def test_local_links_and_images_exist(self):
        for rel in ("README.md", os.path.join("browser_extension", "README.md")):
            base = os.path.dirname(os.path.join(R, rel))
            t = _read(rel)
            targets = re.findall(r"\]\(([^)#\s]+)\)", t) + re.findall(r'src="([^"]+)"', t) + re.findall(r'href="([^"]+)"', t)
            for target in targets:
                if re.match(r"[a-z]+:", target):         # http(s):, mailto: ...
                    continue
                self.assertTrue(os.path.exists(os.path.normpath(os.path.join(base, target))), f"{rel}: {target}")

    def test_extension_readme_steps_match_the_app(self):
        t = _read(os.path.join("browser_extension", "README.md"))
        self.assertIn("Load unpacked", t)
        self.assertIn("Developer mode", t)
        self.assertIn("47892", t)
        with open(os.path.join(R, "ui", "pages", "settings_page.py"), encoding="utf-8") as f:
            settings = f.read()
        self.assertIn('group("Browser extension"', settings)          # the path both READMEs tell people to follow
        self.assertIn('"Open folder"', settings)
        with open(os.path.join(R, "core", "bridge_server.py"), encoding="utf-8") as f:
            self.assertIn("47892", f.read())


if __name__ == "__main__":
    unittest.main()
