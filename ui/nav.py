"""
ui/nav.py
Compact category navigation for the download list: icon, name and (only when
there is something to count) a number. The active item is marked with an
accent bar and a tinted background — not by colour alone.
"""
import tkinter as tk

from theme import COLORS, FONTS
from ui import icons
from ui.widgets import RoundedButton, Separator

ITEMS = [
    ("all", "list", "All Downloads"),
    ("downloading", "download", "Downloading"),
    ("queued", "clock", "Queued"),
    ("paused", "pause", "Paused"),
    ("completed", "check-circle", "Completed"),
    ("failed", "alert", "Failed"),
]


class _NavItem(tk.Frame):
    def __init__(self, parent, key, icon_name, label, on_click):
        super().__init__(parent, bg=COLORS["bg_panel"], height=36, cursor="hand2")
        self.key, self.icon_name, self.active, self.hover = key, icon_name, False, False
        self.pack_propagate(False)
        self.bar = tk.Frame(self, width=3, bg=COLORS["bg_panel"])
        self.bar.pack(side="left", fill="y", padx=(0, 10))
        self.icon_lbl = tk.Label(self, bd=0)
        self.icon_lbl.pack(side="left")
        self.name_lbl = tk.Label(self, text=label, font=FONTS["nav"], anchor="w", bd=0)
        self.name_lbl.pack(side="left", padx=(10, 0), fill="x", expand=True)
        self.count_lbl = tk.Label(self, font=FONTS["small"], bd=0)
        self.count_lbl.pack(side="right", padx=(0, 14))
        for w in (self, self.bar, self.icon_lbl, self.name_lbl, self.count_lbl):
            w.bind("<Button-1>", lambda _e: on_click(key))
            w.bind("<Enter>", lambda _e: self._set(hover=True))
            w.bind("<Leave>", lambda _e: self._set(hover=False))
        self.paint()

    def _set(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)
        self.paint()

    def paint(self, count=None, danger=False):
        bg = COLORS["bg_selected"] if self.active else COLORS["bg_hover"] if self.hover else COLORS["bg_panel"]
        fg = COLORS["text_primary"] if self.active else COLORS["text_secondary"]
        for w in (self, self.icon_lbl, self.name_lbl, self.count_lbl):
            w.configure(bg=bg)
        self.bar.configure(bg=COLORS["accent"] if self.active else bg)
        self.name_lbl.configure(fg=fg, font=FONTS["body_bold"] if self.active else FONTS["nav"])
        img = icons.icon(self.icon_name, 16, COLORS["accent"] if self.active else COLORS["text_muted"])
        self.icon_lbl.configure(image=img)
        self.icon_lbl.image = img
        if count is not None:
            self.count_lbl.configure(text=str(count) if count else "",
                                     fg=COLORS["danger"] if (danger and count) else COLORS["text_muted"])


class CategoryNav(tk.Frame):
    def __init__(self, parent, on_select, on_terminate=None):
        super().__init__(parent, bg=COLORS["bg_panel"], width=206)
        self.pack_propagate(False)
        self.on_select = on_select
        self.active = "all"
        self.items = {}
        self._counts = {}
        tk.Frame(self, bg=COLORS["bg_panel"], height=10).pack(fill="x")
        for key, icon_name, label in ITEMS:
            item = _NavItem(self, key, icon_name, label, self.select)
            item.pack(fill="x", pady=1)
            self.items[key] = item
        self.items["all"].active = True
        self.items["all"].paint()

        if on_terminate is not None:
            footer = tk.Frame(self, bg=COLORS["bg_panel"])
            footer.pack(side="bottom", fill="x", pady=(0, 10))
            Separator(footer).pack(fill="x", pady=(0, 10))
            RoundedButton(footer, "Terminate", variant="danger", icon="power", command=on_terminate,
                          bg=COLORS["bg_panel"], tooltip="Immediately stop VidDown, the browser bridge and any running downloads"
                          ).pack(anchor="w", padx=10)

    def select(self, key, notify=True):
        self.active = key
        for k, item in self.items.items():
            item.active = (k == key)
            item.paint(self._counts.get(k), danger=(k == "failed"))
        if notify:
            self.on_select(key)

    def set_counts(self, counts):
        self._counts = counts
        for k, item in self.items.items():
            item.paint(counts.get(k, 0), danger=(k == "failed"))
