"""Every place that carries the app icon has the right file, size and transparency,
and the files the build scripts / manifest / .desktop point at actually exist."""
import _setup  # noqa: F401  (must stay first)

import json
import os
import re
import unittest

from PIL import Image, IcoImagePlugin

R = _setup.ROOT
HICOLOR = (16, 24, 32, 48, 64, 96, 128, 256, 512)


def _open(rel):
    im = Image.open(os.path.join(R, rel))
    im.load()
    return im


class IconFilesTests(unittest.TestCase):
    def test_hicolor_set_is_complete_and_square_rgba(self):
        for s in HICOLOR:
            im = _open(f"icons/hicolor/{s}x{s}/apps/viddown.png")
            self.assertEqual((im.size, im.mode), ((s, s), "RGBA"), s)
            a = im.getchannel("A")
            self.assertLess(a.getpixel((0, 0)), 40, f"{s}px: corner must be (almost) transparent")
            self.assertEqual(a.getpixel((s // 2, s // 2)), 255, f"{s}px: centre must be opaque")

    def test_pixmap_window_icon_and_extension_icons(self):
        self.assertEqual(_open("icons/pixmaps/viddown.png").size, (64, 64))
        self.assertEqual(_open("resources/icon.png").size, (512, 512))
        for s in (16, 48, 128):
            self.assertEqual(_open(f"browser_extension/icons/icon{s}.png").size, (s, s))

    def test_windows_ico_has_every_size_and_decodes(self):
        with open(os.path.join(R, "windows", "viddown.ico"), "rb") as f:
            ico = IcoImagePlugin.IcoFile(f)
            self.assertEqual(sorted(ico.sizes()), [(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
            for size in ico.sizes():
                frame = ico.getimage(size)
                self.assertEqual((frame.size, frame.mode), (size, "RGBA"))

    def test_all_sizes_are_the_same_picture(self):
        """Not a leftover of the old icon: the 128px and 512px renderings agree on
        the colour of the logo's centre area (warm orange, not the old blue)."""
        for rel in ("resources/icon.png", "icons/hicolor/128x128/apps/viddown.png",
                    "browser_extension/icons/icon128.png", "icons/pixmaps/viddown.png"):
            im = _open(rel).convert("RGB")
            w = im.width
            px = [im.getpixel((x, y)) for x in range(w // 4, 3 * w // 4, max(1, w // 32))
                  for y in range(w // 4, 3 * w // 4, max(1, w // 32))]
            warm = sum(1 for r, g, b in px if r > b + 40)
            blue = sum(1 for r, g, b in px if b > r + 40)
            self.assertGreater(warm, blue, rel)
            self.assertGreater(warm, 0, rel)


class IconReferencesTests(unittest.TestCase):
    def test_manifest_icons_exist(self):
        m = json.load(open(os.path.join(R, "browser_extension", "manifest.json"), encoding="utf-8"))
        paths = set(m["icons"].values()) | set(m["action"]["default_icon"].values())
        self.assertTrue(paths)
        for p in paths:
            self.assertTrue(os.path.isfile(os.path.join(R, "browser_extension", p)), p)

    def test_desktop_file_icon_name_has_a_file(self):
        txt = open(os.path.join(R, "viddown.desktop"), encoding="utf-8").read()
        name = re.search(r"^Icon=(.+)$", txt, re.M).group(1).strip()
        self.assertTrue(os.path.isfile(os.path.join(R, "icons", "hicolor", "256x256", "apps", f"{name}.png")))
        self.assertTrue(os.path.isfile(os.path.join(R, "icons", "pixmaps", f"{name}.png")))

    def test_deb_script_installs_every_hicolor_size(self):
        txt = open(os.path.join(R, "build_deb.sh"), encoding="utf-8").read()
        m = re.search(r"for SIZE in ([0-9 ]+); do", txt)
        self.assertEqual(tuple(int(x) for x in m.group(1).split()), HICOLOR)

    def test_inno_and_pyinstaller_use_the_ico(self):
        self.assertIn("SetupIconFile=viddown.ico", open(os.path.join(R, "windows", "viddown.iss"), encoding="utf-8").read())
        self.assertIn("windows\\viddown.ico", open(os.path.join(R, "build_windows.ps1"), encoding="utf-8").read())

    def test_generator_and_masters_are_present(self):
        for rel in ("tools/make_icons.py", "tools/icon/master_1024.png", "tools/icon/master_small.png"):
            self.assertTrue(os.path.isfile(os.path.join(R, rel)), rel)


if __name__ == "__main__":
    unittest.main()
