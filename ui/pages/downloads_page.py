"""
ui/pages/downloads_page.py
The main view: one list for everything — active, queued, paused and failed
downloads (live DownloadJob objects) plus completed ones (history entries).
A category filter (set from the navigation) and a search box narrow it down.

The page keeps the same entry points the old Queue/History pages had, so the
rest of the app is unchanged:
    add_job_row(job)    update_job_row(job)    _remove_row(job_id)    refresh()
"""
import os
import platform
import re
import subprocess
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox

from theme import COLORS, FONTS
from core.downloader import DownloadJob
from ui import icons
from ui.widgets import IconButton, RoundedButton, SlimProgress, Tooltip, Separator

CATEGORIES = [
    ("all", "All Downloads"),
    ("downloading", "Downloading"),
    ("queued", "Queued"),
    ("paused", "Paused"),
    ("completed", "Completed"),
    ("failed", "Failed"),
]
_JOB_CATEGORY = {"downloading": "downloading", "queued": "queued",
                 "paused": "paused", "error": "failed"}
HISTORY_PAGE = 60           # completed entries drawn at once ("Show more" adds more)
ROW_HEIGHT = 54

_STATUS_STYLE = {   # status -> (icon, colour token, label)
    "downloading": ("download", "accent", "Downloading"),
    "queued": ("clock", "text_muted", "Queued"),
    "paused": ("pause", "warning", "Paused"),
    "error": ("alert", "danger", "Failed"),
    "completed": ("check-circle", "success", "Completed"),
}


# ------------------------------------------------------------------ helpers --
def friendly_error(raw):
    """A plain-language reason for a failure (the raw text stays available in
    the tooltip and in the error log)."""
    text = (raw or "").strip()
    if not text or text == "None":
        return "The download failed."
    low = text.lower()
    rules = [
        ("http error 403", "The site refused the download (HTTP 403). Browser cookies may help."),
        ("http error 404", "The video was not found (HTTP 404). It may have been removed."),
        ("http error 429", "Too many requests (HTTP 429). Try again in a while."),
        ("unsupported url", "This link is not supported."),
        ("private video", "This video is private."),
        ("confirm your age", "This video is age-restricted. Try enabling browser cookies."),
        ("not a bot", "The site asks for a human check. Try enabling browser cookies."),
        ("sign in", "Sign-in is required. Try enabling browser cookies."),
        ("login required", "Sign-in is required. Try enabling browser cookies."),
        ("not available in your country", "This video is not available in your region."),
        ("requested format is not available",
         "No download format matched the chosen quality/codec for this video. Try another "
         "quality or set the codec to Auto. For YouTube also update yt-dlp in About, or use "
         "the build that includes the JavaScript runtime."),
        ("video unavailable", "This video is unavailable. It may have been removed or made private."),
        ("members-only", "This video is for channel members only."),
        ("timed out", "The connection timed out. Check your internet connection and try again."),
        ("connection reset", "The connection was interrupted. Try again."),
        ("ffmpeg is not installed", "ffmpeg is needed for this download but could not run."),
        ("ffprobe", "ffmpeg is needed for this download but could not run."),
        ("postprocessing", "The download finished but converting it failed. See the error log for details."),
        ("ffmpeg", "ffmpeg is needed for this download but could not run."),
        ("name resolution", "Could not reach the site. Check your internet connection."),
        ("failed to resolve", "Could not reach the site. Check your internet connection."),
        ("name or service not known", "Could not reach the site. Check your internet connection."),
        ("urlopen error", "Could not reach the site. Check your internet connection."),
        ("unable to download webpage", "Could not load the page. Check your connection or the link."),
        ("no space left", "The disk is full."),
        ("permission denied", "VidDown cannot write to the download folder."),
        ("read-only file system", "The download folder is read-only."),
        ("file name too long", "The file name is too long for this disk. Shorten the title."),
    ]
    for needle, message in rules:
        if needle in low:
            return message
    text = re.sub(r"^ERROR:\s*", "", text.splitlines()[0])
    return text if len(text) <= 140 else text[:137] + "…"


def human_size(n):
    if not n:
        return ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0


def open_path(path):
    if platform.system() == "Darwin":
        subprocess.Popen(["open", path])
    elif platform.system() == "Windows":
        os.startfile(path)   # noqa
    else:
        subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class _Item:
    """One list entry: a live job or a completed (history) entry."""

    def __init__(self, job=None, entry=None):
        self.job, self.entry = job, entry
        if job is not None:
            self.key = ("job", job.id)
        else:
            self.key = ("hist", f"{entry.get('finished_at', '')}|{entry.get('filepath', '')}|{entry.get('title', '')}")

    @property
    def status(self):
        return self.job.status if self.job is not None else "completed"

    @property
    def category(self):
        return "completed" if self.job is None else _JOB_CATEGORY.get(self.job.status)

    @property
    def name(self):
        if self.job is not None:
            return self.job.title or self.job.url
        fp = self.entry.get("filepath")
        return os.path.basename(fp) if fp else (self.entry.get("title") or "Untitled")

    @property
    def url(self):
        return self.job.url if self.job is not None else self.entry.get("url")

    @property
    def path(self):
        return (self.job.filepath if self.job is not None else self.entry.get("filepath")) or None


# --------------------------------------------------------------------- row --
class _Row(tk.Frame):
    def __init__(self, page, item):
        super().__init__(page.list_body, bg=COLORS["bg_dark"], height=ROW_HEIGHT)
        self.page, self.item = page, item
        self.selected = self.hover = False
        self._full_name = ""
        self._font_name = tkfont.Font(font=FONTS["body_bold"])
        self.grid_propagate(False)
        self.grid_columnconfigure(1, weight=1, minsize=120)
        self.grid_rowconfigure(0, weight=1)

        self.icon_lbl = tk.Label(self, bd=0)
        self.icon_lbl.grid(row=0, column=0, padx=(16, 10))

        text = tk.Frame(self)
        text.grid(row=0, column=1, sticky="ew")
        self.name_lbl = tk.Label(text, font=FONTS["body_bold"], anchor="w", bd=0)
        self.name_lbl.pack(fill="x", pady=(9, 0))
        self.meta_lbl = tk.Label(text, font=FONTS["small"], anchor="w", bd=0, compound="left")
        self.meta_lbl.pack(fill="x", pady=(1, 0))
        self._font_meta = tkfont.Font(font=FONTS["small"])
        self._full_meta = ""
        self.meta_lbl.bind("<Configure>", lambda _e: self._fit_meta())
        self._name_tip = Tooltip(self.name_lbl, "")
        self._meta_tip = Tooltip(self.meta_lbl, "")
        self.name_lbl.bind("<Configure>", lambda _e: self._fit_name())

        prog = tk.Frame(self, width=150)
        prog.grid(row=0, column=2, padx=(8, 8))
        prog.grid_propagate(False)
        prog.configure(height=ROW_HEIGHT - 10)
        self.pb = SlimProgress(prog)
        self.pb.place(x=0, rely=0.5, anchor="w", width=106)
        self.pct_lbl = tk.Label(prog, font=FONTS["small"], anchor="e", bd=0)
        self.pct_lbl.place(x=110, rely=0.5, anchor="w", width=38)

        self.size_lbl = tk.Label(self, font=FONTS["small"], anchor="e", width=10, bd=0)
        self.speed_lbl = tk.Label(self, font=FONTS["small"], anchor="e", width=10, bd=0)
        self.eta_lbl = tk.Label(self, font=FONTS["small"], anchor="e", width=8, bd=0)
        self.size_lbl.grid(row=0, column=3, padx=(0, 6))
        self.speed_lbl.grid(row=0, column=4, padx=(0, 6))
        self.eta_lbl.grid(row=0, column=5, padx=(0, 6))

        self.actions = tk.Frame(self)
        self.actions.grid(row=0, column=6, padx=(4, 12))
        self.btn1 = IconButton(self.actions, "pause", size=30)
        self.btn2 = IconButton(self.actions, "x", size=30)
        self.btn3 = IconButton(self.actions, "more", size=30, command=self._show_menu,
                               tooltip="More actions")
        for b in (self.btn1, self.btn2, self.btn3):
            b.pack(side="left", padx=1)

        self.sep = Separator(self)
        self.sep.place(relx=0, rely=1.0, relwidth=1.0, height=1, anchor="sw")
        self._plain = [self, text, prog, self.actions, self.icon_lbl, self.name_lbl, self.meta_lbl,
                       self.pct_lbl, self.size_lbl, self.speed_lbl, self.eta_lbl]
        self._optional = [self.size_lbl, self.speed_lbl, self.eta_lbl]
        self._bind_interaction(self)
        self.set_compact(page.compact)
        self.refresh_content()
        self._paint()

    # ----------------------------------------------------------- appearance
    def _bind_interaction(self, widget):
        if isinstance(widget, (IconButton, RoundedButton)):
            return
        widget.bind("<Button-1>", self._on_click, add="+")
        widget.bind("<Double-Button-1>", self._on_double, add="+")
        widget.bind("<Button-3>", self._on_right_click, add="+")
        widget.bind("<Enter>", lambda _e: self._set_hover(True), add="+")
        widget.bind("<Leave>", lambda _e: self._set_hover(False), add="+")
        for child in widget.winfo_children():
            self._bind_interaction(child)

    def _bg(self):
        if self.selected:
            return COLORS["bg_selected"]
        return COLORS["bg_hover"] if self.hover else COLORS["bg_dark"]

    def _paint(self):
        bg = self._bg()
        for w in self._plain:
            w.configure(bg=bg)
        self.pb.configure(bg=bg)
        for b in (self.btn1, self.btn2, self.btn3):
            b.configure(bg=bg)
        self.name_lbl.configure(fg=COLORS["text_primary"])
        for w in (self.pct_lbl, self.size_lbl, self.speed_lbl, self.eta_lbl):
            w.configure(fg=COLORS["text_secondary"])

    def _set_hover(self, value):
        # <Leave> fires when moving onto a child; only treat it as leaving
        # when the pointer really is outside the row.
        if not value:
            x, y = self.winfo_pointerxy()
            if self.winfo_containing(x, y) is not None and str(self.winfo_containing(x, y)).startswith(str(self)):
                return
        self.hover = value
        self._paint()

    def set_selected(self, value):
        self.selected = value
        self._paint()

    def set_compact(self, compact):
        for w in self._optional:
            (w.grid_remove if compact else w.grid)()

    def _fit_name(self):
        width = self.name_lbl.winfo_width()
        if width < 20:
            return
        text = self._full_name
        if self._font_name.measure(text) > width - 6:
            lo, hi = 1, len(text)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if self._font_name.measure(text[:mid] + "…") <= width - 6:
                    lo = mid
                else:
                    hi = mid - 1
            self.name_lbl.configure(text=text[:lo] + "…")
            self._name_tip.text = text
        else:
            self.name_lbl.configure(text=text)
            self._name_tip.text = ""

    def _fit_meta(self):
        width = self.meta_lbl.winfo_width() - 18          # room for the status icon
        if width < 20:
            return
        text = self._full_meta
        if self._font_meta.measure(text) > width:
            lo, hi = 1, len(text)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if self._font_meta.measure(text[:mid] + "…") <= width:
                    lo = mid
                else:
                    hi = mid - 1
            self.meta_lbl.configure(text=" " + text[:lo] + "…")
            if not self._meta_tip.text:
                self._meta_tip.text = text
        else:
            self.meta_lbl.configure(text=" " + text)

    # -------------------------------------------------------------- content
    def refresh_content(self):
        item = self.item
        status = item.status
        icon_name, color_key, label = _STATUS_STYLE[status]
        color = COLORS[color_key]

        self._full_name = item.name
        self._fit_name()
        if item.job is not None:
            kind = "music" if (item.job.quality or {}).get("audio_only") else "video"
        else:
            kind = icons.file_icon_name(item.name)
        self.icon_lbl.image = icons.icon(kind, 22, COLORS["text_muted"])
        self.icon_lbl.configure(image=self.icon_lbl.image)

        job, entry = item.job, item.entry
        meta_icon = icons.icon(icon_name, 12, color)
        self.meta_lbl.configure(image=meta_icon, fg=color, padx=0)
        self.meta_lbl.image = meta_icon
        quality = ""
        if job is not None and job.quality:
            quality = job.quality.get("label") or ""
        meta, tip = label, ""
        pct, size, speed, eta = "", "", "", ""
        indeterminate, value = False, 0.0

        if status == "downloading":
            value = job.progress
            merging = job.eta == "Merging…"
            if merging:
                meta = "Merging streams"
                value = 92
            elif job.progress <= 0 and not job.speed:
                meta, indeterminate = "Connecting", True
            else:
                pct = f"{job.progress:.0f}%"
                speed = job.speed or ""
                eta = job.eta or ""
            if quality:
                meta += f"  ·  {quality}"
        elif status == "queued":
            meta = "Waiting in queue" + (f"  ·  {quality}" if quality else "")
        elif status == "paused":
            value, pct = job.progress, f"{job.progress:.0f}%"
            meta = f"Paused at {job.progress:.0f}%" + (f"  ·  {quality}" if quality else "")
        elif status == "error":
            tip = (job.error or "").strip()
            meta = friendly_error(job.error)
        else:                                            # completed
            when = (entry or {}).get("finished_at", "")
            meta = "Completed" + (f"  ·  {when}" if when else "")
            if job is not None and getattr(job, "notes", None):
                tip = "\n".join(job.notes)         # e.g. "WAV files cannot hold a cover picture."
                meta += "  ·  note"
            size = human_size((entry or {}).get("filesize"))

        if job is not None and status != "error":
            size = size or (job.quality.get("size_label") if job.quality else "") or ""
            if size and status != "completed":
                size = f"≈ {size}" if not size.startswith("≈") else size
        self._full_meta = meta
        self._meta_tip.text = tip
        self._fit_meta()
        self.pct_lbl.configure(text=pct)
        self.size_lbl.configure(text=size or ("" if status == "error" else "—"))
        self.speed_lbl.configure(text=speed or ("—" if status == "downloading" else ""))
        self.eta_lbl.configure(text=eta or ("—" if status == "downloading" else ""))

        if status in ("completed", "error"):
            self.pb.place_forget()          # nothing to measure
            self.pct_lbl.place_forget()
        else:
            self.pb.place(x=0, rely=0.5, anchor="w", width=106)
            self.pct_lbl.place(x=110, rely=0.5, anchor="w", width=38)
            self.pb.set(value, status if status != "queued" else "queued", indeterminate)

        self._update_actions()
        self._paint()

    def _update_actions(self):
        status, page = self.item.status, self.page
        if status in ("downloading", "queued"):
            self.btn1.configure(icon="pause", command=lambda: page.pause_item(self.item), tooltip="Pause", state="normal")
            self.btn2.configure(icon="x", command=lambda: page.cancel_item(self.item), tooltip="Cancel download", state="normal")
        elif status == "paused":
            self.btn1.configure(icon="play", command=lambda: page.resume_item(self.item), tooltip="Resume", state="normal")
            self.btn2.configure(icon="x", command=lambda: page.cancel_item(self.item), tooltip="Cancel download", state="normal")
        elif status == "error":
            self.btn1.configure(icon="retry", command=lambda: page.retry_item(self.item), tooltip="Retry", state="normal")
            self.btn2.configure(icon="x", command=lambda: page.cancel_item(self.item), tooltip="Remove from list", state="normal")
        else:
            exists = bool(self.item.path and os.path.exists(self.item.path))
            self.btn1.configure(icon="file", command=lambda: page.open_file(self.item), tooltip="Open file",
                                state="normal" if exists else "disabled")
            self.btn2.configure(icon="folder", command=lambda: page.open_folder(self.item),
                                tooltip="Show in folder", state="normal")

    # ----------------------------------------------------------- interaction
    def _on_click(self, event):
        self.page.click_row(self, shift=bool(event.state & 0x1), ctrl=bool(event.state & 0x4))
        self.page.focus_list()

    def _on_double(self, _event):
        if self.item.status == "completed":
            self.page.open_file(self.item)
        elif self.item.status in ("downloading", "queued"):
            self.page.pause_item(self.item)
        elif self.item.status == "paused":
            self.page.resume_item(self.item)
        elif self.item.status == "error":
            self.page.retry_item(self.item)

    def _on_right_click(self, event):
        if not self.selected:
            self.page.click_row(self)
        self.page.show_menu(self.item, event.x_root, event.y_root)

    def _show_menu(self):
        self.page.click_row(self)
        self.page.show_menu(self.item, self.btn3.winfo_rootx(), self.btn3.winfo_rooty() + 32)


# -------------------------------------------------------------------- page --
class DownloadsPage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=COLORS["bg_dark"])
        self.app = app
        self.jobs = []              # _Item for live jobs, newest first
        self.hist_items = []        # _Item for history entries
        self.rows = {}              # key -> _Row
        self.category = "all"
        self.search = ""
        self.selected = []          # keys, in click order
        self._anchor = None
        self.compact = False
        self._shown_history = HISTORY_PAGE
        self._visible = []
        self._menu_icons = []

        # ---- header: title / count on the left, contextual action right
        self.header = tk.Frame(self, bg=COLORS["bg_dark"])
        self.header.pack(fill="x", padx=20, pady=(14, 6))
        self.title_lbl = tk.Label(self.header, font=FONTS["page_title"], bg=COLORS["bg_dark"],
                                  fg=COLORS["text_primary"])
        self.title_lbl.pack(side="left")
        self.count_lbl = tk.Label(self.header, font=FONTS["small"], bg=COLORS["bg_dark"],
                                  fg=COLORS["text_muted"])
        self.count_lbl.pack(side="left", padx=(10, 0), pady=(4, 0))
        self.header_action = RoundedButton(self.header, "", variant="ghost", padx=10, pady=5)

        # ---- column captions
        self.captions = tk.Frame(self, bg=COLORS["bg_dark"], height=24)
        self.captions.pack(fill="x", padx=0)
        self.captions.grid_columnconfigure(1, weight=1)
        self.captions.grid_propagate(False)
        cap = dict(font=FONTS["small"], bg=COLORS["bg_dark"], fg=COLORS["text_muted"], bd=0)
        tk.Label(self.captions, text="Name", anchor="w", **cap).grid(row=0, column=1, sticky="w", padx=(48, 0))
        cap_prog = tk.Frame(self.captions, width=150, height=22, bg=COLORS["bg_dark"])
        cap_prog.grid(row=0, column=2, padx=(8, 8))
        cap_prog.grid_propagate(False)
        tk.Label(cap_prog, text="Progress", anchor="w", **cap).place(x=0, rely=0.5, anchor="w")
        self._cap_optional = [
            tk.Label(self.captions, text="Size", anchor="e", width=10, **cap),
            tk.Label(self.captions, text="Speed", anchor="e", width=10, **cap),
            tk.Label(self.captions, text="Time left", anchor="e", width=8, **cap),
        ]
        for i, w in enumerate(self._cap_optional):
            w.grid(row=0, column=3 + i, padx=(0, 6))
        tk.Frame(self.captions, width=112, bg=COLORS["bg_dark"]).grid(row=0, column=6, padx=(4, 12))
        Separator(self).pack(fill="x")

        # ---- scrolling list (own wheel handling: reliable over child widgets)
        holder = tk.Frame(self, bg=COLORS["bg_dark"])
        holder.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(holder, bg=COLORS["bg_dark"], highlightthickness=0, bd=0, takefocus=1)
        self.scrollbar = tk.Scrollbar(holder, orient="vertical", command=self.canvas.yview,
                                      bg=COLORS["bg_hover"], troughcolor=COLORS["bg_dark"],
                                      activebackground=COLORS["text_muted"], relief="flat", bd=0, width=8)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.list_body = tk.Frame(self.canvas, bg=COLORS["bg_dark"])
        self._win = self.canvas.create_window((0, 0), window=self.list_body, anchor="nw")
        self.list_body.bind("<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.canvas.bind("<Button-1>", lambda _e: (self.clear_selection(), self.canvas.focus_set()))
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.bind_all(seq, self._on_wheel, add="+")

        self.empty = tk.Frame(self.list_body, bg=COLORS["bg_dark"])
        self.empty_icon = tk.Label(self.empty, bg=COLORS["bg_dark"], bd=0)
        self.empty_icon.pack(pady=(70, 10))
        self.empty_title = tk.Label(self.empty, font=FONTS["section"], bg=COLORS["bg_dark"],
                                    fg=COLORS["text_primary"])
        self.empty_title.pack()
        self.empty_text = tk.Label(self.empty, font=FONTS["body"], bg=COLORS["bg_dark"],
                                   fg=COLORS["text_muted"], justify="center")
        self.empty_text.pack(pady=(4, 14))
        self.empty_btn = RoundedButton(self.empty, "Add Download", variant="primary", icon="plus",
                                       command=lambda: self.app.show_page("download"))

        self.more_btn = RoundedButton(self.list_body, "", variant="ghost", command=self._show_more)

        self.menu = tk.Menu(self, tearoff=0, bg=COLORS["bg_card"], fg=COLORS["text_primary"],
                            activebackground=COLORS["accent"], activeforeground=COLORS["accent_text"],
                            disabledforeground=COLORS["text_muted"], bd=0, relief="flat",
                            font=FONTS["body"], activeborderwidth=0)
        # Draw the first screen immediately; the (possibly long) history list is
        # filled in a moment later so the window never waits for it.
        self._relayout()
        self.after(30, self.refresh)

    # ================================================================ layout
    def _on_canvas_resize(self, event):
        self.canvas.itemconfigure(self._win, width=event.width)
        compact = event.width < 960
        if compact != self.compact:
            self.compact = compact
            for w in self._cap_optional:
                (w.grid_remove if compact else w.grid)()
            for row in self.rows.values():
                row.set_compact(compact)

    def _on_wheel(self, event):
        try:
            widget = self.winfo_containing(event.x_root, event.y_root)
        except (KeyError, tk.TclError):
            return
        if widget is None or not str(widget).startswith(str(self)):
            return
        if not self.winfo_ismapped():
            return
        if event.num == 4:
            self.canvas.yview_scroll(-2, "units")
        elif event.num == 5:
            self.canvas.yview_scroll(2, "units")
        else:
            self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def _matches(self, item):
        if self.category != "all" and item.category != self.category:
            return False
        return not self.search or self.search in item.name.lower()

    def _relayout(self):
        rank = {"downloading": 0, "queued": 1, "paused": 2, "error": 3}
        ordered = sorted(reversed(self.jobs), key=lambda i: rank.get(i.status, 4))   # stable: oldest first
        visible = [i for i in ordered if self._matches(i)]
        hist = [i for i in self.hist_items if self._matches(i)]
        hidden_hist = max(0, len(hist) - self._shown_history)
        visible += hist[:self._shown_history]
        self._visible = visible

        wanted = {i.key for i in visible}
        for key in [k for k in self.rows if k not in wanted]:
            self.rows.pop(key).destroy()
        self.selected = [k for k in self.selected if k in wanted]

        self.empty.pack_forget()
        self.more_btn.pack_forget()
        for item in visible:
            row = self.rows.get(item.key)
            if row is None:
                row = self.rows[item.key] = _Row(self, item)
            else:
                row.item = item
            row.pack_forget()
        for item in visible:
            self.rows[item.key].pack(fill="x")
            self.rows[item.key].set_selected(item.key in self.selected)
        if hidden_hist:
            self.more_btn.configure(text=f"Show {min(hidden_hist, HISTORY_PAGE)} more of {hidden_hist} older")
            self.more_btn.pack(pady=12)
        if not visible:
            self._show_empty()
            self.empty.pack(fill="both", expand=True)
        self._update_header()
        self.app.on_downloads_changed()

    def _show_more(self):
        self._shown_history += HISTORY_PAGE
        self._relayout()

    def _show_empty(self):
        name = dict(CATEGORIES)[self.category]
        if self.search:
            icon, title, text = "search", "No matches", f"Nothing in {name} matches \u201c{self.search}\u201d."
        elif self.category == "all":
            icon, title = "download", "No downloads yet"
            text = "Paste a video link to start your first download.\nYou can also send videos from the browser extension."
        else:
            icon, title = "list", f"No {name.lower()} downloads"
            text = {"downloading": "Nothing is downloading right now.", "queued": "Nothing is waiting in the queue.",
                    "paused": "No downloads are paused.", "completed": "Finished downloads will appear here.",
                    "failed": "No downloads have failed."}.get(self.category, "")
        self._empty_img = icons.icon(icon, 32, COLORS["text_muted"], 1.5)
        self.empty_icon.configure(image=self._empty_img)
        self.empty_title.configure(text=title)
        self.empty_text.configure(text=text)
        if self.category == "all" and not self.search:
            self.empty_btn.pack()
        else:
            self.empty_btn.pack_forget()

    def _update_header(self):
        self.title_lbl.configure(text=dict(CATEGORIES)[self.category])
        total = len([i for i in self.jobs + self.hist_items if self._matches(i)])
        self.count_lbl.configure(text=f"{total} item{'s' if total != 1 else ''}")
        self.header_action.pack_forget()
        if self.category == "failed" and any(i.category == "failed" for i in self.jobs):
            self.header_action.configure(text="Clear failed", icon="trash", command=self.clear_failed)
            self.header_action.pack(side="right")
        elif self.category == "completed" and self.hist_items:
            self.header_action.configure(text="Clear history", icon="trash", command=self.clear_history)
            self.header_action.pack(side="right")

    # =========================================================== app hooks
    def set_category(self, key):
        self.category = key
        self._shown_history = HISTORY_PAGE
        self.canvas.yview_moveto(0)
        self._relayout()

    def set_search(self, text):
        self.search = (text or "").strip().lower()
        self._shown_history = HISTORY_PAGE
        self._relayout()

    def counts(self):
        c = {k: 0 for k, _ in CATEGORIES}
        for item in self.jobs:
            if item.category:
                c[item.category] += 1
        c["completed"] = len(self.hist_items)
        c["all"] = len(self.jobs) + len(self.hist_items)
        return c

    def add_job_row(self, job):
        self.jobs.insert(0, _Item(job=job))
        self._relayout()

    def add_job_rows(self, jobs):
        """Several jobs at once (a playlist): one re-layout instead of one per
        job - each re-layout re-packs every row, so adding N jobs singly was
        quadratic."""
        added = False
        for job in jobs:
            self.jobs.insert(0, _Item(job=job))
            added = True
        if added:
            self._relayout()

    def update_job_row(self, job):
        item = next((i for i in self.jobs if i.job is job), None)
        if item is None:
            if job.status in ("cancelled", "completed"):
                return
            self.add_job_row(job)
            return
        if job.status in ("cancelled", "completed"):
            # a completed job reappears as a history entry (app.add_history_entry
            # runs right after this), so the row hands over without flicker
            self._remove_row(job.id)
            return
        row = self.rows.get(item.key)
        previous = getattr(item, "_last_status", None)
        item._last_status = job.status
        if row is None:
            if self._matches(item):
                self._relayout()
            else:
                self.app.on_downloads_changed()
            return
        if previous is not None and previous != job.status:
            self._relayout()          # category (and maybe visibility) changed
            row = self.rows.get(item.key)
        if row is not None:
            row.refresh_content()
        self.app.schedule_summary_update()

    def _remove_row(self, job_id):
        self.jobs = [i for i in self.jobs if not (i.job is not None and i.job.id == job_id)]
        self._relayout()

    def refresh(self):
        """History changed: rebuild the completed entries."""
        self.hist_items = [_Item(entry=e) for e in self.app.history]
        self._relayout()

    def on_show(self):
        self.refresh()

    # ============================================================ selection
    def click_row(self, row, shift=False, ctrl=False):
        key = row.item.key
        keys = [i.key for i in self._visible]
        if shift and self._anchor in keys:
            a, b = sorted((keys.index(self._anchor), keys.index(key)))
            self.selected = keys[a:b + 1]
        elif ctrl:
            if key in self.selected:
                self.selected.remove(key)
            else:
                self.selected.append(key)
            self._anchor = key
        else:
            self.selected = [key]
            self._anchor = key
        for k, r in self.rows.items():
            r.set_selected(k in self.selected)

    def clear_selection(self):
        self.selected = []
        for r in self.rows.values():
            r.set_selected(False)

    def select_all(self):
        self.selected = [i.key for i in self._visible]
        for k, r in self.rows.items():
            r.set_selected(k in self.selected)

    def focus_list(self):
        self.canvas.focus_set()

    def selected_items(self):
        by_key = {i.key: i for i in self._visible}
        return [by_key[k] for k in self.selected if k in by_key]

    # ============================================================== actions
    def pause_item(self, item):
        job = item.job
        if job is not None and self.app.download_manager.pause(job):
            self.update_job_row(job)
            self.app._save_queue_state()

    def resume_item(self, item):
        if item.job is not None:
            self.app.download_manager.resume(item.job)

    def cancel_item(self, item):
        job = item.job
        if job is None:
            return
        self.app.download_manager.cancel(job)
        self.app.all_jobs.pop(job.id, None)
        self._remove_row(job.id)
        self.app._save_queue_state()

    def retry_item(self, item):
        job = item.job
        if job is None:
            return
        new_job = DownloadJob(job.url, job.base_title, job.quality, dict(job.settings), **dict(job.options))
        self._remove_row(job.id)
        self.app.queue_download(new_job)

    def pause_selection_or_all(self):
        items = self.selected_items() or list(self.jobs)
        n = 0
        for it in items:
            if it.job is not None and it.status in ("queued", "downloading"):
                self.pause_item(it)
                n += 1
        self.app.set_status(f"Paused {n} download{'s' if n != 1 else ''}" if n else "Nothing to pause")

    def resume_selection_or_all(self):
        items = self.selected_items() or list(self.jobs)
        n = 0
        for it in items:
            if it.job is not None and it.status == "paused":
                self.resume_item(it)
                n += 1
        self.app.set_status(f"Resumed {n} download{'s' if n != 1 else ''}" if n else "Nothing to resume")

    def remove_selection(self):
        """Delete key: takes entries off the list. Never deletes files."""
        items = self.selected_items()
        if not items:
            return
        jobs = [i for i in items if i.job is not None]
        entries = [i.entry for i in items if i.entry is not None]
        if any(i.status in ("downloading", "queued", "paused") for i in jobs):
            if not messagebox.askyesno("Cancel downloads",
                                       f"Cancel {len(jobs)} unfinished download(s) and remove them from the list?"):
                return
        for i in jobs:
            self.cancel_item(i)
        if entries:
            self.app.remove_history_entries(entries)
        self.clear_selection()

    def clear_failed(self):
        for i in [i for i in self.jobs if i.category == "failed"]:
            self.cancel_item(i)

    def clear_history(self):
        if messagebox.askyesno("Clear history",
                               "Remove all completed downloads from this list?\n\nThe files themselves are not deleted."):
            self.app.clear_history()

    def open_file(self, item):
        path = item.path
        if not path or not os.path.exists(path):
            messagebox.showinfo("File not found", "This file no longer exists at its original location.")
            return
        open_path(path)

    def open_folder(self, item):
        path = item.path
        if not path:
            messagebox.showinfo("Folder not found", "No file location is recorded for this download.")
            return
        folder = path if os.path.isdir(path) else os.path.dirname(path)
        if not os.path.isdir(folder):
            messagebox.showwarning("Folder not found", "This folder no longer exists.")
            return
        open_path(folder)

    def copy_url(self, item):
        if item.url:
            self.clipboard_clear()
            self.clipboard_append(item.url)
            self.app.set_status("Download URL copied")

    def delete_file(self, item):
        path = item.path
        if not path or not os.path.exists(path):
            messagebox.showinfo("File not found", "This file no longer exists. You can remove the entry from the list instead.")
            return
        if not messagebox.askyesno(
                "Delete file from disk",
                f"Permanently delete this file?\n\n{path}\n\nThis cannot be undone.",
                icon="warning", default="no"):
            return
        try:
            os.remove(path)
        except OSError as exc:
            messagebox.showerror("Could not delete", str(exc))
            return
        if item.entry is not None:
            self.app.remove_history_entries([item.entry])
        self.app.set_status("File deleted")

    # ============================================================ context menu
    def show_menu(self, item, x, y):
        m = self.menu
        m.delete(0, "end")
        self._menu_icons = []

        def add(label, command, icon=None, state="normal"):
            kw = {}
            if icon:
                img = icons.icon(icon, 14, COLORS["text_primary"] if state == "normal" else COLORS["text_muted"])
                self._menu_icons.append(img)
                kw = {"image": img, "compound": "left"}
            m.add_command(label="  " + label, command=command, state=state, **kw)

        status = item.status
        if status in ("downloading", "queued"):
            add("Pause", lambda: self.pause_item(item), "pause")
            add("Cancel download", lambda: self.cancel_item(item), "x")
        elif status == "paused":
            add("Resume", lambda: self.resume_item(item), "play")
            add("Cancel download", lambda: self.cancel_item(item), "x")
        elif status == "error":
            add("Retry", lambda: self.retry_item(item), "retry")
        if status == "completed":
            exists = bool(item.path and os.path.exists(item.path))
            add("Open file", lambda: self.open_file(item), "file", "normal" if exists else "disabled")
            add("Show in folder", lambda: self.open_folder(item), "folder")
        m.add_separator()
        add("Copy download URL", lambda: self.copy_url(item), "copy", "normal" if item.url else "disabled")
        m.add_separator()
        if status == "completed":
            add("Remove from list", lambda: self.app.remove_history_entries([item.entry]), "x")
            add("Delete file from disk\u2026", lambda: self.delete_file(item), "trash")
        elif status == "error":
            add("Remove from list", lambda: self.cancel_item(item), "x")
        try:
            m.tk_popup(x, y)
        finally:
            m.grab_release()
