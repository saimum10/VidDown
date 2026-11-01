import _setup  # noqa: F401  (must stay first)

import os
import re
import shutil
import subprocess
import unittest

EXT = os.path.join(_setup.ROOT, "browser_extension")


class PopupTests(unittest.TestCase):
    def test_popup_has_no_inline_script(self):
        """MV3 extension pages forbid inline <script>: it never runs, and the
        popup stayed on 'Checking connection…' with a dead button."""
        html = open(os.path.join(EXT, "popup.html"), encoding="utf-8").read()
        for m in re.finditer(r"<script\b([^>]*)>(.*?)</script>", html, re.S | re.I):
            self.assertIn("src=", m.group(1), "inline <script> in popup.html")
            self.assertEqual(m.group(2).strip(), "")
        self.assertTrue(os.path.isfile(os.path.join(EXT, "popup.js")))
        self.assertNotRegex(html, r"\son(click|load|change|error)\s*=")


@unittest.skipIf(shutil.which("node") is None, "node not installed")
class SniffSelectionTests(unittest.TestCase):
    def test_selection_rules(self):
        res = subprocess.run(["node", os.path.join(_setup.ROOT, "tests", "sniff_select.test.js")],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_all_scripts_parse(self):
        for name in ("background.js", "content.js", "popup.js"):
            res = subprocess.run(["node", "--check", os.path.join(EXT, name)],
                                 capture_output=True, text=True, timeout=60)
            self.assertEqual(res.returncode, 0, f"{name}: {res.stderr}")


class HoverTests(unittest.TestCase):
    def test_hover_does_not_prefetch_by_default(self):
        src = open(os.path.join(EXT, "content.js"), encoding="utf-8").read()
        self.assertRegex(src, r"const PREFETCH_ON_HOVER = false;")
        # the only place a prefetch message is sent sits behind that switch
        self.assertEqual(src.count('type: "prefetch_video"'), 1)
        head = src[:src.index('type: "prefetch_video"')]
        self.assertIn("if (!PREFETCH_ON_HOVER)", head[head.rindex("function _triggerPrefetch"):])


if __name__ == "__main__":
    unittest.main()
