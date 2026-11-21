"""
ui/license_viewer.py
Read-only window showing VidDown's own license and the third-party notices.
"""
import tkinter as tk
import tkinter.font as tkfont

from core import buildinfo
from theme import COLORS, FONTS
from ui.widgets import RoundedButton

_DOCS = (("LICENSE", "VidDown license"),
         ("THIRD_PARTY_NOTICES.md", "Third-party software"))


def read_licenses():
    """[(heading, text), ...] for every license document this build ships."""
    out = []
    for name, heading in _DOCS:
        path = buildinfo.find_doc(name)
        text = None
        if path:
            try:
                with open(path, encoding="utf-8") as f:
                    text = f.read()
            except OSError:
                text = None
        out.append((heading, text or f"({name} was not found in this installation. "
                                     "See https://github.com/saimum10/VidDown)"))
    return out


class LicenseViewer(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("VidDown \u2014 Licenses")
        self.geometry("820x600")
        self.minsize(520, 320)
        self.configure(bg=COLORS["bg_dark"])

        bar = tk.Frame(self, bg=COLORS["bg_dark"])
        bar.pack(fill="x", padx=14, pady=(12, 8))
        tk.Label(bar, text="Open-source licenses", font=FONTS["section"], bg=COLORS["bg_dark"],
                 fg=COLORS["text_primary"], anchor="w").pack(side="left")
        RoundedButton(bar, "Close", command=self.destroy, padx=12, pady=6).pack(side="right")
        RoundedButton(bar, "Copy", command=self._copy, padx=12, pady=6).pack(side="right", padx=(0, 6))

        body = tk.Frame(self, bg=COLORS["bg_dark"])
        body.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        sb = tk.Scrollbar(body)
        sb.pack(side="right", fill="y")
        mono = tkfont.nametofont("TkFixedFont").copy()
        mono.configure(size=10)
        self.text = tk.Text(body, wrap="word", font=mono, bg=COLORS["bg_input"],
                            fg=COLORS["text_primary"], relief="flat", padx=10, pady=8,
                            yscrollcommand=sb.set, insertbackground=COLORS["text_primary"])
        self.text.pack(side="left", fill="both", expand=True)
        sb.configure(command=self.text.yview)
        self.text.tag_configure("h", foreground=COLORS["accent"], font=FONTS["body_bold"],
                                spacing1=10, spacing3=6)

        self._plain = []
        for heading, text in read_licenses():
            self.text.insert("end", heading + "\n", "h")
            self.text.insert("end", text.strip() + "\n\n")
            self._plain.append(f"{heading}\n{'=' * len(heading)}\n{text.strip()}\n")
        self.text.configure(state="disabled")
        self.bind("<Escape>", lambda _e: self.destroy())

    def _copy(self):
        self.clipboard_clear()
        self.clipboard_append("\n".join(self._plain))
