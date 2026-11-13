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
        # the clipboard is watched exactly while this window is on screen (also when it is
        # minimised and brought back by the window manager, which never calls show()/hide())
        self.bind("<Map>", lambda e: e.widget is self and self.page.start_clipboard_watch())
        self.bind("<Unmap>", lambda e: e.widget is self and self.page.stop_clipboard_watch())

    def show(self):
        if not self.winfo_viewable():
            _center_on(self, self.app, 860, 720)
        self.transient(self.app)
        self.deiconify()
        self.lift()
        self.page.focus_url()
        self.page.start_clipboard_watch()

    def hide(self):
        self.page.stop_clipboard_watch()
        self.withdraw()


class LazyPages:
    """key -> page, where a page is built the first time somebody asks for it.

    The Settings pages (General, Cookies, About) hold about 330 widgets and, at start-up,
    cost ~50 MB and ~0.7 s - for windows most people open rarely or never. Reading
    pages[key] builds that page; `key in pages`, iteration and built() do not."""

    def __init__(self, factories):
        self._factories = dict(factories)
        self._built = {}

    def __getitem__(self, key):
        if key not in self._built:
            self._built[key] = self._factories[key]()
        return self._built[key]

    def __contains__(self, key):
        return key in self._factories

    def __iter__(self):
        return iter(self._factories)

    def __len__(self):
        return len(self._factories)

    def keys(self):
        return self._factories.keys()

    def get(self, key, default=None):
        return self[key] if key in self._factories else default

    def built(self, key):
        """True once the page exists (nothing is built by asking)."""
        return key in self._built


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

        self._nav_items = {}
        self.pages = LazyPages({key: self._page_factory(cls) for (key, _i, _l), cls
                                in zip(self.SECTIONS, (SettingsPage, CookiesPage, AboutPage))})
        for (key, icon_name, label) in self.SECTIONS:
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
        self._highlight("settings")         # the page itself is built when the window is first shown

    def _page_factory(self, cls):
        def make():
            page = cls(self.content, self.app)
            page.grid(row=0, column=0, sticky="nsew")
            return page
        return make

    def select(self, key):
        self._highlight(key)
        page = self.pages[key]              # builds the page on first use
        page.tkraise()
        if hasattr(page, "on_show"):
            page.on_show()

    def _highlight(self, key):
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

    def show(self, key=None):
        if not self.winfo_viewable():
            _center_on(self, self.app, 900, 700)
        self.select(key if key in self.pages else self.active)
        self.transient(self.app)
        self.deiconify()
        self.lift()
        self.app.schedule_memory_trim(1500)     # the page was just built: its temporary pictures can go

    def flush_pending(self):
        """A typed setting still waiting to apply (only if the General page exists at all)."""
        if self.pages.built("settings"):
            try:
                self.pages["settings"].flush_pending()
            except Exception:
                pass

    def hide(self):
        self.flush_pending()
        self.withdraw()
