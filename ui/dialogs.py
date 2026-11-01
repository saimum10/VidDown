"""
ui/dialogs.py
The two secondary windows: "Add Download" (hosts the fetch / quality / options
flow) and "Settings" (grouped into General, Cookies and Updates & About).
Both are created once, kept hidden when closed, and keep their state — the
browser extension and the clipboard watcher rely on the Add Download page
staying alive.
"""
import tkinter as tk

from theme import COLORS, FONTS
from ui import icons
from ui.pages.download_page import DownloadPage
from ui.pages.settings_page import SettingsPage
from ui.pages.cookies_page import CookiesPage
from ui.pages.about_page import AboutPage
from ui.widgets import Separator


def _center_on(win, parent, w, h):
    parent.update_idletasks()
    x = parent.winfo_rootx() + max(0, (parent.winfo_width() - w) // 2)
    y = parent.winfo_rooty() + max(0, (parent.winfo_height() - h) // 3)
    win.geometry(f"{w}x{h}+{x}+{y}")


class AddDownloadDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.withdraw()
        self.title("Add Download")
        self.configure(bg=COLORS["bg_dark"])
        self.minsize(720, 600)
        self.page = DownloadPage(self, app)
        self.page.pack(fill="both", expand=True)
        self.page.on_queued = self.hide
        self.protocol("WM_DELETE_WINDOW", self.hide)
        self.bind("<Escape>", lambda _e: self.hide())

    def show(self):
        if not self.winfo_viewable():
            _center_on(self, self.app, 860, 720)
        self.transient(self.app)
        self.deiconify()
        self.lift()
        self.page.focus_url()

    def hide(self):
        self.withdraw()


class SettingsDialog(tk.Toplevel):
    SECTIONS = [
        ("settings", "sliders", "General"),
        ("cookies", "cookie", "Cookies"),
        ("about", "info", "Updates & About"),
    ]

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.withdraw()
        self.title("Settings")
        self.configure(bg=COLORS["bg_dark"])
        self.minsize(760, 560)

        nav = tk.Frame(self, bg=COLORS["bg_panel"], width=190)
        nav.pack(side="left", fill="y")
        nav.pack_propagate(False)
        Separator(self, orient="vertical").pack(side="left", fill="y")
        tk.Label(nav, text="Settings", font=FONTS["page_title"], bg=COLORS["bg_panel"],
                 fg=COLORS["text_primary"], anchor="w").pack(fill="x", padx=18, pady=(16, 10))

        self.content = tk.Frame(self, bg=COLORS["bg_dark"])
        self.content.pack(side="left", fill="both", expand=True)
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)

        self.pages, self._nav_items = {}, {}
        for (key, icon_name, label), cls in zip(self.SECTIONS, (SettingsPage, CookiesPage, AboutPage)):
            page = cls(self.content, app)
            page.grid(row=0, column=0, sticky="nsew")
            self.pages[key] = page
            row = tk.Frame(nav, bg=COLORS["bg_panel"], height=36, cursor="hand2")
            row.pack(fill="x", pady=1)
            row.pack_propagate(False)
            bar = tk.Frame(row, width=3, bg=COLORS["bg_panel"])
            bar.pack(side="left", fill="y", padx=(0, 12))
            ic = tk.Label(row, bd=0)
            ic.pack(side="left")
            name = tk.Label(row, text=label, font=FONTS["nav"], bd=0)
            name.pack(side="left", padx=(10, 0))
            for w in (row, bar, ic, name):
                w.bind("<Button-1>", lambda _e, k=key: self.select(k))
            self._nav_items[key] = (row, bar, ic, name, icon_name)

        self.active = "settings"
        self.protocol("WM_DELETE_WINDOW", self.hide)
        self.bind("<Escape>", lambda _e: self.hide())
        self.select("settings")

    def select(self, key):
        self.active = key
        for k, (row, bar, ic, name, icon_name) in self._nav_items.items():
            on = (k == key)
            bg = COLORS["bg_selected"] if on else COLORS["bg_panel"]
            for w in (row, ic, name):
                w.configure(bg=bg)
            bar.configure(bg=COLORS["accent"] if on else bg)
            name.configure(fg=COLORS["text_primary"] if on else COLORS["text_secondary"],
                           font=FONTS["body_bold"] if on else FONTS["nav"])
            img = icons.icon(icon_name, 16, COLORS["accent"] if on else COLORS["text_muted"])
            ic.configure(image=img)
            ic.image = img
        page = self.pages[key]
        page.tkraise()
        if hasattr(page, "on_show"):
            page.on_show()

    def show(self, key=None):
        if not self.winfo_viewable():
            _center_on(self, self.app, 900, 700)
        if key in self.pages:
            self.select(key)
        self.transient(self.app)
        self.deiconify()
        self.lift()

    def hide(self):
        try:
            self.pages["settings"].flush_pending()
        except Exception:
            pass
        self.withdraw()
