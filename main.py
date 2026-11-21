#!/usr/bin/env python3
"""
VidDown (SAIMUM HABIB) — entry point.

A standalone, self-contained video downloader for Debian-based Linux and Windows,
built with Tkinter + yt-dlp + bundled ffmpeg/ffprobe/aria2c so the end
user never has to install any of those separately.

Run during development with:
    python3 main.py
"""
import os
import sys

# Windows GUI builds (PyInstaller --windowed) have no console, so sys.stdout /
# sys.stderr / sys.stdin are None. Anything that prints or logs to them would
# crash with "'NoneType' object has no attribute 'write'" — point them at the
# null device instead. (No effect on Linux or when run from a terminal.)
for _stream_name, _mode in (("stdout", "w"), ("stderr", "w"), ("stdin", "r")):
    if getattr(sys, _stream_name, None) is None:
        setattr(sys, _stream_name, open(os.devnull, _mode, encoding="utf-8"))

# Windows: give the process its own taskbar identity so the taskbar shows
# VidDown's icon and groups its windows correctly (instead of "python").
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("SAIMUMHABIB.VidDown")
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Prefer a yt-dlp the in-app updater downloaded over the bundled one. This has
# to happen before anything imports yt_dlp.
from core import ytdlp_loader
ytdlp_loader.install()


def _check_dependencies():
    """Friendly message box instead of a raw traceback if something
    required isn't installed yet (mainly useful while developing)."""
    missing = []
    # verify_or_rollback() falls back to the bundled yt-dlp if a downloaded
    # update turns out to be broken, so a bad update can never stop startup.
    if not ytdlp_loader.verify_or_rollback():
        missing.append("yt-dlp")
    return missing


def _is_already_running():
    """
    True if another VidDown instance's bridge server is already listening.

    Desktop download managers (IDM included) enforce single-instance for
    exactly this reason: without it, launching a second copy leaves two
    processes racing for the same local port, the second one's browser-
    extension integration silently never works, and the person has no
    obvious way to know why. Checking this before startup — rather than
    just reporting the bind failure afterwards — stops that class of bug
    at the root instead of only reporting the symptom.
    """
    import socket
    from core.bridge_server import PORT
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=0.5) as sock:
            sock.sendall(b"GET /ping HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
            sock.settimeout(0.5)
            data = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
        return b'"app": "VidDown"' in data
    except OSError:
        return False  # nothing listening, or it's not us — either way, safe to start


def _wake_existing_instance():
    """Asks an already-running instance to bring its window to front.
    Mirrors _is_already_running()'s raw-socket approach so this works
    without needing Tkinter or any app code loaded. Returns True only
    if the running instance actually confirmed it handled /show —
    main() falls back to the old "already running" dialog otherwise,
    so a person is never just left with nothing happening."""
    import socket
    from core.bridge_server import PORT
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=0.5) as sock:
            sock.sendall(b"GET /show HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
            sock.settimeout(0.5)
            data = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
        return b'"ok": true' in data
    except OSError:
        return False


def main():
    missing = _check_dependencies()
    if missing:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Missing Required Modules",
            "The following modules are not installed: " + ", ".join(missing) +
            "\n\nInstall with:\npip install " + " ".join(missing) +
            ("" if sys.platform == "win32" else " --break-system-packages"),
        )
        return

    is_autostart = "--autostart" in sys.argv

    if _is_already_running():
        if is_autostart:
            # Autostart launch, but something's already running — nothing
            # to do, and definitely nothing that should pop a dialog at
            # login.
            return
        if _wake_existing_instance():
            # The other instance (possibly hidden, started via autostart)
            # just brought itself to front — no need to bother the person
            # with a dialog on top of that.
            return
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo(
            "VidDown Is Already Running",
            "Another VidDown window is already open.\n\n"
            "Look for it in your taskbar — only one copy can run at a time "
            "so the browser extension always knows which one to talk to.",
        )
        return

    from ui.main_window import MainWindow
    app = MainWindow(start_hidden=is_autostart)
    app.mainloop()


if __name__ == "__main__":
    main()
