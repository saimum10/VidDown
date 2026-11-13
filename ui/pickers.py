"""File / folder pickers and message boxes that cannot hide behind the app's other windows.

The problem: tkinter's dialogs (filedialog.askdirectory, askopenfilename, messagebox ...)
attach to the MAIN window unless a `parent` is given. VidDown's Add Download and
Settings windows are separate top-level windows, so a picker opened from them is
not above them: clicking one of them (or the taskbar icon) raised that window over
the picker, which then sat hidden behind it until the app icon was clicked again.

install(app) fixes this once for every dialog in the program:
  1. a dialog opened without a `parent` gets the window the person is working in
     (the focused window, else the visible Add Download / Settings window, else the main window);
  2. while the dialog is open it is kept on top of all the app's windows, so clicking
     another VidDown window cannot push it down. It disappears with the dialog.
"""
import tkinter as tk
from tkinter import commondialog

_installed = False
_POLL_MS = 150


def _is_dialog_window(w):
    """A real, visible top-level of ours (not a tooltip / toast, which have no frame)."""
    try:
        if not isinstance(w, tk.Toplevel) or not w.winfo_viewable():
            return False
        if w.overrideredirect():
            return False
        return not str(w).rsplit(".", 1)[-1].startswith("__tk")
    except tk.TclError:
        return False


def active_window(app):
    """The top-level the person is working in right now."""
    try:
        focused = app.focus_displayof()
        if focused is not None:
            return focused.winfo_toplevel()
    except (tk.TclError, KeyError):
        pass
    for win in reversed(app.winfo_children()):        # newest window first
        if _is_dialog_window(win):
            return win
    return app


def _tcl_dialog_windows(window):
    """Paths of the dialogs Tk itself created on `window` (file pickers, message boxes ...).
    They exist only on the Tcl side: tkinter's winfo_children() silently skips them,
    so they have to be listed with the raw `winfo children` command."""
    call = window.tk.call
    found = []
    for path in window.tk.splitlist(call("winfo", "children", str(window))):
        if str(path).rsplit(".", 1)[-1].startswith("__tk"):
            found.append(str(path))
    return found


def keep_on_top(window):
    """Keep every dialog that opens on `window` above all other windows until it closes.
    Returns a function that stops the watching."""
    state = {"on": True, "done": set()}

    def poll():
        if not state["on"]:
            return
        try:
            call = window.tk.call
            for path in _tcl_dialog_windows(window):
                try:
                    if not int(call("winfo", "viewable", path)):
                        continue
                    if path not in state["done"]:
                        call("wm", "attributes", path, "-topmost", 1)   # above the other windows of the app
                        state["done"].add(path)
                    call("raise", path)                                  # and above anything raised since
                except tk.TclError:
                    pass                                                 # closed between listing and use
            window.after(_POLL_MS, poll)
        except tk.TclError:                                              # the window itself is gone
            state["on"] = False

    try:
        window.after(20, poll)
    except tk.TclError:
        state["on"] = False
    return lambda: state.update(on=False)


def install(app):
    """Patch tkinter's common dialogs (file pickers, message boxes ...) once per process."""
    global _installed
    if _installed:
        return
    original_show = commondialog.Dialog.show

    def show(self, **options):
        parent = options.get("parent") or self.options.get("parent") or self.master
        if parent is None:
            try:
                parent = active_window(app)
            except Exception:
                parent = app
            self.master = parent
            self.options["parent"] = parent
        try:
            host = parent.winfo_toplevel()
        except (tk.TclError, AttributeError):
            host = app
        stop = keep_on_top(host)
        try:
            return original_show(self, **options)
        finally:
            stop()

    commondialog.Dialog.show = show
    _installed = True


def uninstall_for_tests():
    """Not used by the app: lets the tests start from an unpatched tkinter."""
    global _installed
    _installed = False
