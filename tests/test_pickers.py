"""File pickers / message boxes: they open over the window in use and stay on top."""
import _setup  # noqa: F401  (must stay first)

import os
import re
import time
import tkinter as tk
import unittest
from tkinter import commondialog

from ui import pickers

R = _setup.ROOT


def _root():
    try:
        root = tk.Tk()
    except tk.TclError:
        raise unittest.SkipTest("no display for Tk")
    root.geometry("300x200+10+10")
    return root


def _pump(root, seconds):
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        time.sleep(0.01)


class PickerSourceTests(unittest.TestCase):
    def test_every_file_picker_call_names_its_parent(self):
        """A picker opened without parent= attaches to the main window and can hide behind
        the Add Download / Settings windows. (install() is the safety net; this keeps the
        call sites right too.)"""
        bad = []
        for folder, _, files in os.walk(os.path.join(R, "ui")):
            for name in files:
                if not name.endswith(".py"):
                    continue
                src = open(os.path.join(folder, name), encoding="utf-8").read()
                for m in re.finditer(r"filedialog\.(askdirectory|askopenfilename|asksaveasfilename|askopenfilenames)\(", src):
                    depth, end = 1, 0
                    for i, ch in enumerate(src[m.end():], 0):
                        depth += (ch == "(") - (ch == ")")
                        if depth == 0:
                            end = i
                            break
                    if "parent=" not in src[m.end(): m.end() + end]:
                        bad.append(f"{name}: {src[m.start(): m.end() + 40]!r}")
        self.assertEqual(bad, [])

    def test_main_window_installs_the_fix(self):
        src = open(os.path.join(R, "ui", "main_window.py"), encoding="utf-8").read()
        self.assertIn("pickers.install(self)", src)


class PickerBehaviourTests(unittest.TestCase):
    def setUp(self):
        self._orig_show = commondialog.Dialog.show
        self._orig_flag = pickers._installed
        pickers._installed = False
        self.root = _root()

    def tearDown(self):
        commondialog.Dialog.show = self._orig_show
        pickers._installed = self._orig_flag
        _setup.destroy_tk(self.root)

    def _recorder(self):
        seen = {}

        def recorder(dialog, **options):
            seen["parent"] = dialog.options.get("parent")
            seen["master"] = dialog.master
            return "ok"
        commondialog.Dialog.show = recorder
        return seen

    def test_dialog_without_parent_gets_the_window_that_has_the_focus(self):
        seen = self._recorder()
        side = tk.Toplevel(self.root)                    # like the Add Download / Settings window
        side.geometry("200x150+400+50")
        _pump(self.root, 0.2)
        self.root.focus_displayof = lambda: side         # what Tk reports when `side` has the focus
        pickers.install(self.root)
        self.assertEqual(commondialog.Dialog(command="tk_messageBox").show(), "ok")
        self.assertIs(seen["parent"], side)
        self.assertIs(seen["master"], side)

    def test_with_no_focused_window_the_visible_dialog_window_is_used(self):
        seen = self._recorder()
        side = tk.Toplevel(self.root)
        side.geometry("200x150+400+50")
        _pump(self.root, 0.2)
        self.root.focus_displayof = lambda: None         # e.g. the click went to the taskbar
        pickers.install(self.root)
        commondialog.Dialog(command="tk_messageBox").show()
        self.assertIs(seen["parent"], side)

    def test_explicit_parent_is_respected(self):
        seen = {}
        commondialog.Dialog.show = lambda d, **o: seen.update(parent=d.options.get("parent")) or "ok"
        other = tk.Toplevel(self.root)
        _pump(self.root, 0.1)
        pickers.install(self.root)
        commondialog.Dialog(parent=self.root, command="tk_messageBox").show()
        self.assertIs(seen["parent"], self.root)
        del other

    def test_falls_back_to_the_main_window_when_nothing_else_is_open(self):
        seen = {}
        commondialog.Dialog.show = lambda d, **o: seen.update(parent=d.options.get("parent")) or "ok"
        _pump(self.root, 0.1)
        pickers.install(self.root)
        commondialog.Dialog(command="tk_messageBox").show()
        self.assertIs(seen["parent"], self.root)

    def test_install_is_idempotent(self):
        pickers.install(self.root)
        first = commondialog.Dialog.show
        pickers.install(self.root)
        self.assertIs(commondialog.Dialog.show, first)

    def test_tk_made_dialogs_are_invisible_to_winfo_children_but_found_by_the_helper(self):
        """The reason the helper talks to Tcl directly: tkinter does not list windows that
        Tk itself created, such as the file chooser."""
        self.root.tk.call("toplevel", ".__tk_fakedialog")
        self.assertNotIn(".__tk_fakedialog", [str(w) for w in self.root.winfo_children()])
        self.assertIn(".__tk_fakedialog", pickers._tcl_dialog_windows(self.root))

    def _topmost(self, path):
        return int(self.root.tk.call("wm", "attributes", path, "-topmost"))

    def _require_topmost_support(self):
        """-topmost is carried out by the window manager; a bare X server (no WM) cannot
        report it. Probe it the same way the code under test uses it (a Tk-made window,
        driven through raw Tcl) and skip when it cannot work here."""
        call = self.root.tk.call
        call("toplevel", ".__probe")
        call("wm", "geometry", ".__probe", "50x50+700+500")
        _pump(self.root, 0.3)
        call("wm", "attributes", ".__probe", "-topmost", 1)
        _pump(self.root, 0.2)
        works = int(call("wm", "attributes", ".__probe", "-topmost")) == 1
        call("destroy", ".__probe")
        if not works:
            self.skipTest("no window manager that supports -topmost")

    def test_a_dialog_that_opens_is_kept_on_top_until_the_watcher_stops(self):
        self._require_topmost_support()
        stop = pickers.keep_on_top(self.root)
        self.root.tk.call("toplevel", ".__tk_fakedialog")
        self.root.tk.call("wm", "geometry", ".__tk_fakedialog", "200x100+50+50")
        _pump(self.root, 0.6)
        self.assertEqual(self._topmost(".__tk_fakedialog"), 1)
        stop()
        self.root.tk.call("toplevel", ".__tk_later")
        self.root.tk.call("wm", "geometry", ".__tk_later", "200x100+60+60")
        _pump(self.root, 0.5)
        self.assertEqual(self._topmost(".__tk_later"), 0, "the watcher must stop with the dialog")

    def test_ordinary_windows_are_not_touched(self):
        stop = pickers.keep_on_top(self.root)
        side = tk.Toplevel(self.root)
        side.geometry("200x100+80+80")
        _pump(self.root, 0.5)
        self.assertEqual(int(side.attributes("-topmost")), 0)
        stop()

    def test_tooltips_and_toasts_do_not_count_as_the_window_in_use(self):
        tip = tk.Toplevel(self.root)
        tip.overrideredirect(True)
        tip.geometry("100x30+300+300")
        _pump(self.root, 0.2)
        self.assertIs(pickers.active_window(self.root), self.root)


if __name__ == "__main__":
    unittest.main()
