"""
core/platform_utils.py
Every OS-specific decision lives here, so the rest of the code stays one
shared code base for Linux (.deb) and Windows (.exe).

Standard library only — core/ytdlp_loader.py imports this before yt-dlp
is available, so nothing here may depend on third-party packages.

Linux locations are unchanged from earlier versions (XDG layout), so
existing installs keep their settings and history.
"""
import os
import signal
import subprocess
import sys

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")


# ------------------------------------------------------------ app folders --
def config_dir():
    """Settings folder. Linux: ~/.config/viddown   Windows: %APPDATA%\\VidDown"""
    if IS_WINDOWS:
        base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
        return os.path.join(base, "VidDown")
    return os.path.expanduser("~/.config/viddown")


def data_dir():
    """History/queue/cookies/log folder.
    Linux: ~/.local/share/viddown   Windows: %LOCALAPPDATA%\\VidDown"""
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
        return os.path.join(base, "VidDown")
    return os.path.expanduser("~/.local/share/viddown")


def default_output_dir():
    """Default download folder: <home>/Videos/VidDown on both systems."""
    return os.path.join(os.path.expanduser("~"), "Videos", "VidDown")


# ------------------------------------------------------------- executables --
def exe_name(name):
    """'ffmpeg' -> 'ffmpeg.exe' on Windows, unchanged elsewhere."""
    if IS_WINDOWS and not name.lower().endswith(".exe"):
        return name + ".exe"
    return name


def no_window_kwargs():
    """Extra subprocess kwargs so a GUI (windowed) Windows app never flashes
    a console window when it runs ffmpeg/aria2c, and never inherits an
    invalid stdin. Empty on Linux/macOS."""
    if IS_WINDOWS:
        return {"creationflags": subprocess.CREATE_NO_WINDOW,
                "stdin": subprocess.DEVNULL}
    return {}


# --------------------------------------------------------------- shutdown --
def kill_own_process_tree():
    """Best effort: kill this process's children (ffmpeg/aria2c) too.
    The caller still ends with os._exit() as the unconditional fallback.

    Linux: kill the whole process group.
    Windows: there are no process groups like that, so ask taskkill to end
    this process and everything it started (/T = whole tree)."""
    if IS_WINDOWS:
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(os.getpid())],
                timeout=5, capture_output=True, **no_window_kwargs(),
            )
        except Exception:
            pass
        return
    try:
        os.killpg(os.getpgid(0), signal.SIGKILL)
    except Exception:
        pass
