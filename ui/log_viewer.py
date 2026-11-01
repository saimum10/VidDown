"""
ui/log_viewer.py
Window for reading VidDown's error log, plus the "save as .txt" action that is
shared with the About page.
"""
import datetime
import os
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox

from theme import COLORS, FONTS
from core import errorlog
from ui.widgets import RoundedButton


def save_log_dialog(parent):
    """Ask where to save, write the log (with a system-info header) as UTF-8
    text. Returns the saved path, or None if cancelled / failed."""
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = filedialog.asksaveasfilename(
        parent=parent, title="Save error log",
        defaultextension=".txt", filetypes=[("Text file", "*.txt")],
        initialdir=os.path.expanduser("~"), initialfile=f"viddown-log-{stamp}.txt",
    )
    if not path:
        return None
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(errorlog.export_text())
    except OSError as exc:
        messagebox.showerror("Could not save", f"The log could not be saved:\n{exc}", parent=parent)
        return None
    return path


class LogViewer(tk.Toplevel):
    def __init__(self, parent, app=None):
        super().__init__(parent)
        self.app = app
        self.title("VidDown — Error Log")
        self.geometry("860x540")
        self.minsize(560, 320)
        self.configure(bg=COLORS["bg_dark"])

        bar = tk.Frame(self, bg=COLORS["bg_dark"])
        bar.pack(fill="x", padx=14, pady=(12, 8))
        self.summary = tk.Label(bar, text="", font=FONTS["small"], bg=COLORS["bg_dark"],
                                fg=COLORS["text_secondary"], anchor="w")
        self.summary.pack(side="left")
        RoundedButton(bar, "Clear", command=self._clear, padx=12, pady=6).pack(side="right")
        RoundedButton(bar, "Save as .txt", command=self._save, variant="primary",
                      padx=12, pady=6).pack(side="right", padx=(0, 6))
        RoundedButton(bar, "Copy", command=self._copy, padx=12, pady=6).pack(side="right", padx=(0, 6))
        RoundedButton(bar, "⟳ Refresh", command=self.refresh, padx=12, pady=6).pack(side="right", padx=(0, 6))

        body = tk.Frame(self, bg=COLORS["bg_dark"])
        body.pack(fill="both", expand=True, padx=14, pady=(0, 6))
        sb = tk.Scrollbar(body)
        sb.pack(side="right", fill="y")
        mono = tkfont.nametofont("TkFixedFont").copy()
        mono.configure(size=10)
        self.text = tk.Text(body, wrap="word", font=mono, bg=COLORS["bg_input"],
                            fg=COLORS["text_primary"], relief="flat", padx=10, pady=8,
                            yscrollcommand=sb.set, state="disabled",
                            insertbackground=COLORS["text_primary"])
        self.text.pack(side="left", fill="both", expand=True)
        sb.configure(command=self.text.yview)
        self.text.tag_configure("error", foreground=COLORS["danger"])
        self.text.tag_configure("warning", foreground=COLORS["warning"])
        self.text.tag_configure("info", foreground=COLORS["text_muted"])
        self.text.tag_configure("trace", foreground=COLORS["text_secondary"])

        self.note = tk.Label(self, text=f"Log file: {errorlog.LOG_FILE}", font=FONTS["tiny"],
                             bg=COLORS["bg_dark"], fg=COLORS["text_muted"], anchor="w")
        self.note.pack(fill="x", padx=14, pady=(0, 8))
        self.refresh()

    def refresh(self):
        content = errorlog.read_text()
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        if not content.strip():
            self.text.insert("end", "No problems recorded yet.\n", "info")
        for line in content.splitlines():
            tag = ("error" if "] [ERROR]" in line[:40] or " [ERROR] " in line[:40]
                   else "warning" if " [WARNING] " in line[:40]
                   else "info" if " [INFO] " in line[:40]
                   else "trace")
            self.text.insert("end", line + "\n", tag)
        self.text.configure(state="disabled")
        self.text.see("end")
        errors, warnings = errorlog.count_problems()
        self.summary.configure(
            text=f"{errors} error{'s' if errors != 1 else ''} · "
                 f"{warnings} warning{'s' if warnings != 1 else ''}")

    def _copy(self):
        self.clipboard_clear()
        self.clipboard_append(errorlog.export_text())
        self.note.configure(text="Copied to clipboard", fg=COLORS["success"])

    def _save(self):
        path = save_log_dialog(self)
        if path:
            self.note.configure(text=f"Saved to {path}", fg=COLORS["success"])

    def _clear(self):
        if messagebox.askyesno("Clear log", "Delete all recorded log entries?", parent=self):
            errorlog.clear()
            self.refresh()
            self.note.configure(text="Log cleared", fg=COLORS["success"])
