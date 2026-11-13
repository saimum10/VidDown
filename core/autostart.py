"""
core/autostart.py
Lets VidDown's browser-extension bridge server come up automatically at
login, without the person needing to remember to open the app first. Only
ever touches the system when Settings explicitly toggles this on/off —
nothing here runs on every launch. The autostart entry itself (present or
absent) is the single source of truth for whether this is on; nothing is
duplicated into settings.json, so there's nothing to fall out of sync.

  Linux:   an XDG autostart .desktop file in ~/.config/autostart
  Windows: a value in HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run
           (per-user, no admin rights needed)
"""
import os
import sys

from core.downloader import app_base_dir
from core.platform_utils import IS_WINDOWS

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_VALUE = "VidDown"


def _win_command():
    """Command line stored in the Windows Run key (always starts hidden)."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --autostart'
    exe = sys.executable
    pythonw = os.path.join(os.path.dirname(exe), "pythonw.exe")   # no console window
    if os.path.isfile(pythonw):
        exe = pythonw
    main_py = os.path.join(app_base_dir(), "main.py")
    return f'"{exe}" "{main_py}" --autostart'


def _win_is_enabled():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, _RUN_VALUE)
        return True
    except OSError:
        return False


def _win_enable():
    import winreg
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, _RUN_VALUE, 0, winreg.REG_SZ, _win_command())
        return True, None
    except OSError as exc:
        return False, str(exc)


def _win_disable():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, _RUN_VALUE)
        return True, None
    except FileNotFoundError:
        return True, None           # nothing to remove
    except OSError as exc:
        return False, str(exc)


AUTOSTART_DIR  = os.path.expanduser("~/.config/autostart")
AUTOSTART_FILE = os.path.join(AUTOSTART_DIR, "viddown-autostart.desktop")


def _desktop_quote(arg):
    """Quote one argument for a .desktop Exec= line. The spec uses double
    quotes (shlex's single quotes are NOT understood there) and requires
    backslash-escaping of \\ " $ ` inside them; a literal % must be %%."""
    arg = arg.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
    return '"' + arg.replace("%", "%%") + '"'


def _launch_command():
    """Command line to relaunch VidDown hidden, for the autostart entry.
    Prefers the installed .deb's stable /usr/bin/viddown wrapper (works
    even if PyInstaller's internal folder layout ever changes); falls
    back to the frozen binary's own path, or `python3 main.py` when
    running from source during development."""
    if getattr(sys, "frozen", False):
        if os.path.isfile("/usr/bin/viddown"):
            return "/usr/bin/viddown --autostart"
        return f"{_desktop_quote(sys.executable)} --autostart"
    main_py = os.path.join(app_base_dir(), "main.py")
    return f"{_desktop_quote(sys.executable)} {_desktop_quote(main_py)} --autostart"


def is_enabled():
    if IS_WINDOWS:
        return _win_is_enabled()
    return os.path.isfile(AUTOSTART_FILE)


def enable():
    """Turns autostart on (.desktop file / Run key). Returns (ok, error_message)."""
    if IS_WINDOWS:
        return _win_enable()
    try:
        os.makedirs(AUTOSTART_DIR, exist_ok=True)
        content = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=VidDown (background)\n"
            "Comment=Starts the VidDown bridge server so the browser "
            "extension works right after login\n"
            f"Exec={_launch_command()}\n"
            "X-GNOME-Autostart-enabled=true\n"
            "NoDisplay=true\n"
            "Terminal=false\n"
        )
        with open(AUTOSTART_FILE, "w") as f:
            f.write(content)
        return True, None
    except OSError as exc:
        return False, str(exc)


def disable():
    """Turns autostart off, if it was on. Returns (ok, error_message)."""
    if IS_WINDOWS:
        return _win_disable()
    try:
        if os.path.isfile(AUTOSTART_FILE):
            os.remove(AUTOSTART_FILE)
        return True, None
    except OSError as exc:
        return False, str(exc)
