"""
ui/pages/download_page.py
The home page. Layout (matches the agreed structure):

  +---------------------------------------------------+
  | SCROLLABLE: URL bar, preview, subtitles,           |  <- grows/shrinks,
  |             quality list (own internal scrollbar)  |     this part scrolls
  +---------------------------------------------------+
  | FIXED: Advanced options (collapsible) + Download   |  <- never scrolls,
  |        button (dim until a quality is selected)    |     always visible
  +---------------------------------------------------+
"""
import copy
import os
import re
import tkinter as tk
from tkinter import filedialog, messagebox

from theme import COLORS, FONTS
from core import downloader, storage
from core import output_options as oo
from core import errorlog
from ui.widgets import (BigCheckbutton, PlaceholderEntry, EntryField, RoundedButton,
                        IconButton, Separator, SegmentedControl)
from ui.scrollable import ScrollableFrame
from ui.threads import run_in_thread

# ── supported site URL pattern ──────────────────────────────────────────────
_VIDEO_URL_RE = re.compile(
    r'https?://'
    r'(?:'
    r'(?:www\.)?(?:youtube\.com|youtu\.be)|'
    r'(?:www\.)?facebook\.com|fb\.watch|'
    r'(?:www\.)?instagram\.com|'
    r'(?:www\.)?tiktok\.com|vm\.tiktok\.com|'
    r'(?:www\.)?(?:twitter|x)\.com|'
    r'(?:www\.)?vimeo\.com|'
    r'(?:www\.)?dailymotion\.com|'
    r'(?:www\.)?twitch\.tv|'
    r'(?:www\.)?reddit\.com/r/\S+/comments|'
    r'(?:www\.)?bilibili\.com|'
    r'(?:www\.)?soundcloud\.com|'
    r'(?:www\.)?nicovideo\.jp'
    r')',
    re.IGNORECASE,
)


PAD_X = 14          # horizontal padding of this window's sections


def _is_video_url(text):
    text = (text or "").strip()
    return bool(text.startswith("http") and _VIDEO_URL_RE.search(text))


class _TrimSlider(tk.Canvas):
    """
    Dual-handle range slider for picking a trim start/end within a
    video's total duration.

    This is a pure *view* onto seconds — it never owns the actual trim
    values. DownloadPage's trim_start_var/trim_end_var stay the single
    source of truth (same as before this existed, so _on_download_clicked
    and everything downstream needs zero changes): dragging a handle
    calls on_change(start_s, end_s), which DownloadPage uses to update
    those vars; typing directly into the Start/End entry fields instead
    calls set_range() to move the handles to match. set_range() never
    re-fires on_change, so the two directions can't feed back into an
    infinite loop through the vars' own trace.

    Shows a muted "fetch a video first" placeholder until set_duration()
    is called with a real (>0) duration.
    """
    _HANDLE_R = 7

    def __init__(self, parent, on_change, height=34, **kwargs):
        super().__init__(parent, height=height, bg=COLORS["bg_panel"],
                          bd=0, highlightthickness=0, **kwargs)
        self._on_change = on_change
        self._duration = 0       # 0 = unknown / nothing fetched yet
        self._start = 0
        self._end = 0
        self._drag = None        # "start" | "end" | None while dragging
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)

    def set_duration(self, seconds):
        """Called once a video's total duration is known (including on
        every fresh fetch) — resets the selection to the full range,
        same "nothing trimmed yet" starting point a fresh dual-handle
        slider is expected to show."""
        self._duration = max(0, int(seconds or 0))
        self._start = 0
        self._end = self._duration
        self._draw()

    def set_range(self, start_s, end_s):
        """External sync (Start/End entry edited by hand) — just moves
        the handles and redraws; never calls on_change itself."""
        if self._duration <= 0:
            return
        self._start = max(0, min(int(start_s), self._duration))
        self._end   = max(0, min(int(end_s), self._duration))
        self._draw()

    def _track_span(self):
        return self._HANDLE_R, max(self._HANDLE_R + 1, self.winfo_width() - self._HANDLE_R)

    def _x_to_seconds(self, x):
        x0, x1 = self._track_span()
        frac = max(0.0, min(1.0, (x - x0) / (x1 - x0)))
        return int(frac * self._duration)

    def _seconds_to_x(self, s):
        x0, x1 = self._track_span()
        frac = (s / self._duration) if self._duration else 0
        return x0 + frac * (x1 - x0)

    def _on_press(self, event):
        if self._duration <= 0:
            return
        xs, xe = self._seconds_to_x(self._start), self._seconds_to_x(self._end)
        self._drag = "start" if abs(event.x - xs) <= abs(event.x - xe) else "end"
        self._on_drag(event)

    def _on_drag(self, event):
        if self._duration <= 0 or not self._drag:
            return
        s = self._x_to_seconds(event.x)
        if self._drag == "start":
            self._start = min(s, self._end)
        else:
            self._end = max(s, self._start)
        self._draw()
        self._on_change(self._start, self._end)

    def _on_release(self, _event):
        self._drag = None

    def _draw(self):
        self.delete("all")
        W, H = self.winfo_width(), self.winfo_height()
        if W < 2 or H < 2:
            return
        mid = H // 2

        if self._duration <= 0:
            self.create_line(self._HANDLE_R, mid, W - self._HANDLE_R, mid,
                             width=4, fill=COLORS["bg_input"], capstyle="round")
            self.create_text(W // 2, mid, text="Fetch a video to enable the trim slider",
                             fill=COLORS["text_muted"], font=FONTS["tiny"])
            return

        x0, x1 = self._track_span()
        self.create_line(x0, mid, x1, mid, width=4,
                         fill=COLORS["bg_input"], capstyle="round")

        xs, xe = self._seconds_to_x(self._start), self._seconds_to_x(self._end)
        self.create_line(xs, mid, xe, mid, width=4,
                         fill=COLORS["accent"], capstyle="round")
        for x in (xs, xe):
            self.create_oval(x - self._HANDLE_R, mid - self._HANDLE_R,
                             x + self._HANDLE_R, mid + self._HANDLE_R,
                             fill=COLORS["text_primary"], outline=COLORS["accent"], width=2)


def _parented_messagebox(parent):
    """tkinter.messagebox with every dialog attached to `parent`."""
    import types
    return types.SimpleNamespace(
        showerror=lambda *a, **k: messagebox.showerror(*a, parent=parent, **k),
        showwarning=lambda *a, **k: messagebox.showwarning(*a, parent=parent, **k),
        showinfo=lambda *a, **k: messagebox.showinfo(*a, parent=parent, **k),
        askyesno=lambda *a, **k: messagebox.askyesno(*a, parent=parent, **k),
    )


class DownloadPage(tk.Frame):
    # The pencil-edit widgets exist only while a SINGLE video's preview card is
    # shown. The Download button asks _confirm_title_edit() about them first
    # thing, so for a playlist (which never builds that card) the lookup raised
    # AttributeError - swallowed by Tk, which looked like "Download does
    # nothing" for every playlist fetched before any single video.
    _title_entry = None
    _title_label = None
    _title_edit_btn = None

    def __init__(self, parent, app):
        super().__init__(parent, bg=COLORS["bg_dark"])
        self.app = app
        self.on_queued = None      # set by the Add Download dialog: close after queueing

        self.current_info = None
        self._info_url = None      # the URL current_info was fetched for (what Download must use)
        self.is_playlist = False
        self.edited_title = None   # None = use the fetched title as-is; set once the pencil-edit is used
        self.quality_options = []
        self.quality_var = tk.IntVar(value=-1)
        self.sub_vars = {}
        self._trim_slider = None   # set once _build_advanced_options() runs, below
        self._quality_panel_open = False
        self._qdrop_btn = None
        self._qdrop_panel = None
        # playlist state
        self._playlist_entries   = []
        self._playlist_vars      = []
        self._playlist_count_lbl = None
        self._select_all_var     = tk.BooleanVar(value=True)

        # ── prefetch state ───────────────────────────────────────────────────
        self._prefetch_url = None      # URL currently being prefetched / last completed
        self._prefetch_info = None     # result of a successful prefetch
        self._prefetch_loading = False # True while a background fetch is in progress
        self._fetch_on_complete = False# True when Fetch was pressed and we're waiting for result
        self._last_clipboard = ""      # last clipboard content we checked

        self.grid_rowconfigure(0, weight=0)  # fixed URL bar
        self.grid_rowconfigure(1, weight=1)  # scrollable preview
        self.grid_rowconfigure(2, weight=0)  # fixed quality section
        self.grid_rowconfigure(3, weight=0)  # fixed bottom
        self.grid_columnconfigure(0, weight=1)

        self._build_url_bar()
        self._build_scroll_area()
        self._build_quality_section()
        self._build_fixed_bottom()

    # ============================================================ FIXED URL BAR
    def _build_url_bar(self):
        url_card = self._card(self, "Video URL", rule=False)
        url_card.grid(row=0, column=0, sticky="ew", padx=PAD_X, pady=(16, 0))

        entry_row = tk.Frame(url_card, bg=COLORS["bg_dark"])
        entry_row.pack(fill="x", pady=(0, 8))

        self.url_var = tk.StringVar()
        field = EntryField(entry_row, self.url_var, "Type or paste a video URL here\u2026",
                           icon="link", bg=COLORS["bg_dark"], pady=8)
        field.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._url_entry = field.entry
        self._button(entry_row, "Paste", self._on_paste).pack(side="left", padx=(0, 6))
        self._button(entry_row, "Fetch", self._on_fetch, primary=True).pack(side="left", padx=(0, 6))
        self._button(entry_row, "Clear", self._on_clear, ghost=True).pack(side="left")

        self.url_var.trace_add("write", self._on_url_changed)
        self._debounce_id = None     # after() id for URL-change debounce

        self.fetch_status_var = tk.StringVar(value="")
        tk.Label(
            url_card, textvariable=self.fetch_status_var, font=FONTS["small"],
            bg=COLORS["bg_dark"], fg=COLORS["text_muted"], anchor="w",
            justify="left", wraplength=760,
        ).pack(fill="x", pady=(0, 10))

    # ============================================================ SCROLL AREA
    def _build_scroll_area(self):
        self.scroll = ScrollableFrame(self, bg=COLORS["bg_dark"])
        self.scroll.grid(row=1, column=0, sticky="nsew")
        body = self.scroll.body

        # preview card — built now but NOT packed until fetch succeeds
        self.preview_card = self._card(body, "Preview")
        self.preview_inner = tk.Frame(self.preview_card, bg=COLORS["bg_dark"])
        self.preview_inner.pack(fill="x", pady=(0, 10))

        # start clipboard monitor after 800 ms (UI is fully rendered by then)
        self.after(800, self._monitor_clipboard)

    # ========================================================= QUALITY SECTION
    def _build_quality_section(self):
        """Fixed quality row between scroll and bottom — never scrolls."""
        self.quality_card = self._card(self, "Quality")
        self.quality_card.grid(row=2, column=0, sticky="ew",
                               padx=PAD_X, pady=(4, 0))
        self.quality_card.grid_remove()   # hidden until fetch

        self.quality_frame = tk.Frame(self.quality_card, bg=COLORS["bg_dark"])
        self.quality_frame.pack(fill="x", pady=(0, 10))

    def _render_empty_preview(self):
        for w in self.preview_inner.winfo_children():
            w.destroy()

    def _render_empty_subtitles(self):
        for w in self.sub_inner.winfo_children():
            w.destroy()

    def _render_empty_quality(self):
        if self._quality_panel_open:
            self._close_quality_panel()
        for w in self.quality_frame.winfo_children():
            w.destroy()
        self._qdrop_btn   = None
        self._qdrop_panel = None

    # ============================================================ FIXED BOTTOM
    def _build_fixed_bottom(self):
        bottom = tk.Frame(self, bg=COLORS["bg_panel"])
        bottom.grid(row=3, column=0, sticky="ew")
        tk.Frame(bottom, bg=COLORS["border"], height=1).pack(fill="x")

        action_row = tk.Frame(bottom, bg=COLORS["bg_panel"])
        action_row.pack(fill="x", padx=PAD_X, pady=(8, 10))

        self.advanced_toggle_btn = RoundedButton(
            action_row, "Advanced options", icon="sliders", variant="ghost", padx=12, pady=8,
            bg=COLORS["bg_panel"], command=self._toggle_advanced_panel)
        self.advanced_toggle_btn.pack(side="left")

        self.download_btn = RoundedButton(
            action_row, "Download", icon="download", variant="primary", padx=26, pady=9,
            bg=COLORS["bg_panel"], font=FONTS["button_lg"], command=self._on_download_clicked)
        self.download_btn.configure(state="disabled")
        self.download_btn.pack(side="right")

        # ── pre-build the floating advanced panel (hidden until button clicked) ──
        self._adv_panel = tk.Frame(
            self, bg=COLORS["bg_card"],
            highlightbackground=COLORS["border"], highlightthickness=1,
        )
        self._adv_panel_visible = False
        self._adv_global_id     = None
        self._build_advanced_content(self._adv_panel)

    def _build_advanced_content(self, parent):
        """Build advanced options widgets into `parent` (called once)."""
        hdr = tk.Frame(parent, bg=COLORS["bg_card"])
        hdr.pack(fill="x", padx=16, pady=(10, 4))
        tk.Label(hdr, text="Advanced options", font=FONTS["section"],
                 bg=COLORS["bg_card"], fg=COLORS["text_primary"]).pack(side="left")
        IconButton(hdr, "x", command=self._close_advanced_panel, tooltip="Close", size=26,
                   bg=COLORS["bg_card"]).pack(side="right")
        Separator(parent).pack(fill="x")

        body = tk.Frame(parent, bg=COLORS["bg_card"])
        body.pack(fill="x", padx=16, pady=(8, 12))
        self._build_advanced_options(body)

    def _toggle_advanced_panel(self):
        if self._adv_panel_visible:
            self._close_advanced_panel()
        else:
            self._open_advanced_panel()

    def _open_advanced_panel(self):
        # Position the panel above the toggle button
        self.update_idletasks()
        self._adv_panel.update_idletasks()

        btn_x = self.advanced_toggle_btn.winfo_x()
        btn_y = self.advanced_toggle_btn.winfo_rooty() - self.winfo_rooty()
        pw    = self._adv_panel.winfo_reqwidth() or 560
        ph    = self._adv_panel.winfo_reqheight() or 220

        # anchor to left edge, sit above the button
        panel_y = btn_y - ph - 4
        self._adv_panel.place(
            x=PAD_X, y=max(panel_y, 4),
            width=self.winfo_width() - PAD_X * 2,
        )
        self._adv_panel.lift()
        self._adv_panel_visible = True
        self.advanced_toggle_btn.configure(icon="chevron-down")

        # bind outside click after 50 ms (avoid the opening click)
        self.after(50, self._bind_adv_outside)

    def _bind_adv_outside(self):
        def _check(event):
            if not self._adv_panel_visible:
                return
            p = self._adv_panel
            if not p.winfo_exists():
                return
            px, py = p.winfo_rootx(), p.winfo_rooty()
            pw, ph = p.winfo_width(), p.winfo_height()
            bx = self.advanced_toggle_btn.winfo_rootx()
            by = self.advanced_toggle_btn.winfo_rooty()
            bw = self.advanced_toggle_btn.winfo_width()
            bh = self.advanced_toggle_btn.winfo_height()
            in_panel = px <= event.x_root <= px+pw and py <= event.y_root <= py+ph
            on_btn   = bx <= event.x_root <= bx+bw and by <= event.y_root <= by+bh
            if not in_panel and not on_btn:
                self._close_advanced_panel()

        root = self.winfo_toplevel()
        self._adv_global_id = root.bind("<Button-1>", _check, add=True)

    def _close_advanced_panel(self):
        self._adv_panel.place_forget()
        self._adv_panel_visible = False
        self.advanced_toggle_btn.configure(icon="sliders")
        if self._adv_global_id:
            try:
                self.winfo_toplevel().unbind("<Button-1>", self._adv_global_id)
            except Exception:
                pass
            self._adv_global_id = None

    def _build_advanced_options(self, parent):
        """Advanced options rows — trim, output dir, subtitles. (Custom
        filename used to live here — it's now the pencil-edit on the
        preview card's title instead: same place you already see the
        video's info, and it naturally starts fresh for every new fetch
        instead of silently carrying over the last video's name.)"""
        # Trim
        row2 = tk.Frame(parent, bg=COLORS["bg_card"])
        row2.pack(fill="x", pady=4)
        self.trim_enabled = tk.BooleanVar(value=False)
        BigCheckbutton(
            row2, text="Trim", variable=self.trim_enabled, font=FONTS["small"],
            bg=COLORS["bg_card"], fg=COLORS["text_secondary"], label_width=16,
        ).pack(side="left")
        tk.Label(row2, text="From", font=FONTS["small"], bg=COLORS["bg_card"],
                 fg=COLORS["text_muted"]).pack(side="left", padx=(0, 4))
        self.trim_start_var = tk.StringVar(value="00:00:00")
        EntryField(row2, self.trim_start_var, "HH:MM:SS", font=FONTS["small"], pady=5,
                   bg=COLORS["bg_card"], width=96).pack(side="left")
        tk.Label(row2, text="To", font=FONTS["small"], bg=COLORS["bg_card"],
                 fg=COLORS["text_muted"]).pack(side="left", padx=(10, 4))
        self.trim_end_var = tk.StringVar(value="00:00:00")
        EntryField(row2, self.trim_end_var, "HH:MM:SS", font=FONTS["small"], pady=5,
                   bg=COLORS["bg_card"], width=96).pack(side="left")

        tk.Label(
            parent, text="Format: HH:MM:SS, MM:SS or just seconds (e.g. 00:01:30). An invalid time shows an error.",
            font=FONTS["tiny"], bg=COLORS["bg_card"], fg=COLORS["text_muted"],
            anchor="w", justify="left",
        ).pack(fill="x", pady=(0, 4))

        # Drag-to-trim slider — a visual companion to the two fields
        # above, not a replacement: it stays synced both ways (drag it →
        # updates the fields; edit a field → moves the handles), and the
        # fields remain the precise/source-of-truth way to enter an
        # exact time. Disabled-looking until a video's duration is
        # actually known (see set_duration() in _on_fetch_success).
        slider_row = tk.Frame(parent, bg=COLORS["bg_card"])
        slider_row.pack(fill="x", pady=(0, 4))
        self._trim_slider = _TrimSlider(slider_row, on_change=self._on_trim_slider_change)
        self._trim_slider.pack(fill="x")
        self.trim_start_var.trace_add("write", self._on_trim_text_changed)
        self.trim_end_var.trace_add("write", self._on_trim_text_changed)

        # Output directory
        row3 = tk.Frame(parent, bg=COLORS["bg_card"])
        row3.pack(fill="x", pady=4)
        tk.Label(
            row3, text="Output folder", font=FONTS["small"], bg=COLORS["bg_card"],
            fg=COLORS["text_secondary"], width=20, anchor="w",
        ).pack(side="left")
        self.output_dir_var = tk.StringVar(value=self.app.settings.get("output_dir", storage.DEFAULT_OUTPUT_DIR))
        EntryField(row3, self.output_dir_var, "Folder where this download is saved",
                   font=FONTS["small"], pady=5, bg=COLORS["bg_card"]).pack(
            side="left", fill="x", expand=True)
        RoundedButton(row3, "Browse", command=self._browse_output_dir, padx=12, pady=6,
                      bg=COLORS["bg_card"]).pack(side="left", padx=(6, 0))

        # Subtitles
        Separator(parent).pack(fill="x", pady=(10, 6))
        tk.Label(parent, text="Subtitles", font=FONTS["small_bold"], bg=COLORS["bg_card"],
                 fg=COLORS["text_secondary"], anchor="w").pack(fill="x")
        self.sub_inner = tk.Frame(parent, bg=COLORS["bg_card"])
        self.sub_inner.pack(fill="x", pady=(4, 0))

    # ============================================================== helpers
    def _card(self, parent, title, rule=True):
        """A plain titled section (no box): a hairline above it, a heading, then
        its content — packed by the caller directly into the returned frame."""
        card = tk.Frame(parent, bg=COLORS["bg_dark"])
        if rule:
            Separator(card).pack(fill="x", pady=(0, 10))
        tk.Label(card, text=title, font=FONTS["section"], bg=COLORS["bg_dark"],
                 fg=COLORS["text_primary"], anchor="w").pack(fill="x", pady=(0, 8))
        return card

    def _button(self, parent, text, command, primary=False, ghost=False):
        variant = "primary" if primary else "ghost" if ghost else "default"
        return RoundedButton(parent, text, command=command, variant=variant, padx=14, pady=8,
                             bg=parent.cget("bg"))

    @staticmethod
    def _format_duration(seconds):
        seconds = int(seconds)
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

    # ============================================================== fetch
    def _on_fetch(self):
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("URL Required", "Please enter a video URL first.")
            return

        # ── CASE 1: prefetch for this URL already finished → show instantly ──
        if (self._prefetch_info is not None
                and self._prefetch_url == url
                and not self._prefetch_loading):
            self.fetch_status_var.set("Fetched successfully. Now select a quality.")
            self.app.set_status("Fetched: " + (self._prefetch_info.get("title") or "video"))
            self._on_fetch_success(self._prefetch_info)
            return

        # ── CASE 2: still fetching in background → wait, show when done ──────
        if self._prefetch_loading and self._prefetch_url == url:
            self.fetch_status_var.set("Background fetch almost done, please wait...")
            self.app.set_status("Fetching...")
            self._fetch_on_complete = True
            return

        # ── CASE 3: new URL or no prefetch yet → start a fresh fetch ─────────
        if self.current_info is not None and url != self._info_url:
            self._drop_fetched_view()     # the old video's qualities must not stay on screen
        self._prefetch_url = url
        self._prefetch_info = None
        self._prefetch_loading = True
        self._fetch_on_complete = True
        self.fetch_status_var.set("Fetching video info...")
        self.app.set_status("Fetching video info...")
        self._run_prefetch(url)

    # ──────────────── clipboard & prefetch ──────────────────────────────────

    def _on_paste(self):
        """Paste button: puts the clipboard text in the URL bar; the trace starts the prefetch."""
        try:
            text = self.winfo_toplevel().clipboard_get().strip()
            if text:
                self.url_var.set(text)
        except Exception:
            pass

    def _on_url_changed(self, *_args):
        """url_var trace — fires on every keystroke; debounced to 500 ms."""
        # cancel any pending debounce timer
        if self._debounce_id is not None:
            self.after_cancel(self._debounce_id)
        self._debounce_id = self.after(500, self._debounced_prefetch)

    def _debounced_prefetch(self):
        self._debounce_id = None
        url = self.url_var.get().strip()
        if not _is_video_url(url):
            return
        if url == self._prefetch_url:
            return  # already fetching / fetched this URL
        self._start_prefetch(url)

    def _monitor_clipboard(self):
        """Poll clipboard every 1.5 s; auto-detect if a video URL appears."""
        try:
            top = self.winfo_toplevel()
            # Only react while the window is actually on screen: when VidDown
            # sits hidden in the background, a URL copied for some other purpose
            # must not be sent to yt-dlp (with the browser cookies) behind the
            # user's back.
            if top.winfo_viewable() and top.state() != "iconic":
                text = top.clipboard_get().strip()
                if text != self._last_clipboard and _is_video_url(text):
                    self._last_clipboard = text
                    # Fill the box only when it is empty. If it already holds
                    # something else, leave everything alone: the old silent
                    # prefetch of the clipboard URL could never be used (the
                    # box showed a different URL) and it overwrote the status
                    # line of the URL actually being worked on.
                    if not self.url_var.get().strip():
                        self.url_var.set(text)   # triggers the debounced prefetch
        except Exception:
            pass
        # reschedule itself — keeps running until the app closes
        self.after(1500, self._monitor_clipboard)

    def _start_prefetch(self, url):
        """Start a background prefetch; quality/preview are not shown yet."""
        if self._prefetch_loading and url == self._prefetch_url:
            return  # already fetching this URL
        if self.current_info is not None and url != self._info_url:
            self._drop_fetched_view()
        self._prefetch_url = url
        self._prefetch_info = None
        self._prefetch_loading = True
        self._fetch_on_complete = False
        self.fetch_status_var.set("Background fetch in progress...")
        self._run_prefetch(url)

    def _run_prefetch(self, url):
        """Fetch the video info on a background thread; the result comes back on
        the Tk thread (a worker must never touch Tk itself)."""
        settings = self.app.settings

        def _done(info, exc):
            if exc is not None:
                error_message = str(exc) or type(exc).__name__
                errorlog.warning("fetch", f"Could not fetch {url}: {error_message}")
                self._on_prefetch_done(url, None, error_message)
            else:
                self._on_prefetch_done(url, info, None)

        run_in_thread(self, lambda: downloader.fetch_info(url, settings), _done,
                      name="prefetch")

    def _on_prefetch_done(self, url, info, error):
        """Called on the main thread when a prefetch completes."""
        if url != self._prefetch_url:
            return  # URL changed while we were fetching — discard stale result
        self._prefetch_loading = False

        if error:
            self._prefetch_info = None
            if self._fetch_on_complete:
                # user pressed Fetch and was waiting → show the error
                self.fetch_status_var.set(f"Fetch failed: {error}")
                self.app.set_status("Fetch failed")
            else:
                # background attempt failed silently — user didn't press Fetch
                self.fetch_status_var.set("Background fetch failed. Click Try Download.")
            self._fetch_on_complete = False
        else:
            self._prefetch_info = info
            if self._fetch_on_complete:
                # user pressed Fetch and was waiting → show immediately
                self._fetch_on_complete = False
                self.fetch_status_var.set("Fetched successfully. Now select a quality.")
                self.app.set_status("Fetched: " + (info.get("title") or "video"))
                self._on_fetch_success(info)
            else:
                # finished in background; Fetch not yet pressed by user
                self.fetch_status_var.set("Ready — Click Try Download to see details.")

    # ────────────────────────────────────────────────────────────────────────

    def _on_fetch_success(self, info):
        self.current_info = info
        self._info_url = self._prefetch_url
        self.is_playlist = info.get("_type") == "playlist" or "entries" in info
        self.fetch_status_var.set("Fetched successfully. Now select a quality.")
        self.app.set_status("Fetched: " + (info.get("title") or "video"))

        # Fresh video, fresh advanced options — a Trim range or a one-off
        # Output Folder chosen for the PREVIOUS video must not silently
        # carry over and apply to this new one.
        self.trim_enabled.set(False)
        duration = info.get("duration") or 0
        if self._trim_slider is not None:
            self._trim_slider.set_duration(duration)  # resets handles to [0, duration]
        self.trim_start_var.set("00:00:00")
        self.trim_end_var.set(self._seconds_to_hms(duration))
        self.output_dir_var.set(self.app.settings.get("output_dir", storage.DEFAULT_OUTPUT_DIR))

        # Show preview card in scroll area
        if not self.preview_card.winfo_ismapped():
            self.preview_card.pack(fill="x", padx=PAD_X, pady=(14, 0))

        # Show fixed quality section (grid row 2)
        self.quality_card.grid()

        if self.is_playlist:
            self._render_playlist_preview(info)
        else:
            self._render_preview(info)
        self._render_subtitles(info)
        self._render_quality(info)

    def _render_playlist_preview(self, info):
        """Compact scrollable checklist — rows rendered in batches to keep UI responsive."""
        for w in self.preview_inner.winfo_children():
            w.destroy()
        self._title_entry = None        # the single-video card (and its edit box) is gone

        entries = list(info.get("entries") or [])
        self._playlist_entries = entries
        self._playlist_vars    = [tk.BooleanVar(value=True) for _ in entries]

        # ── header ──────────────────────────────────────────────────────────
        hdr = tk.Frame(self.preview_inner, bg=COLORS["bg_card"])
        hdr.pack(fill="x", pady=(0, 4))

        self._select_all_var = tk.BooleanVar(value=True)
        BigCheckbutton(
            hdr, text="Select All", variable=self._select_all_var,
            font=FONTS["small_bold"], bg=COLORS["bg_card"],
            fg=COLORS["text_primary"], command=self._on_select_all,
        ).pack(side="left")

        self._playlist_count_lbl = tk.Label(
            hdr, text=f"{len(entries)} / {len(entries)} selected",
            font=FONTS["small"], bg=COLORS["bg_card"], fg=COLORS["accent"],
        )
        self._playlist_count_lbl.pack(side="right")

        # The fetch reads at most a fixed number of entries; a longer playlist
        # used to be cut off without a word.
        if info.get("_truncated"):
            total = info.get("_playlist_total")
            if total and total > len(entries):
                note = f"Only the first {len(entries)} of {total} videos are listed."
            else:
                note = f"Only the first {len(entries)} videos are listed - the playlist may have more."
            tk.Label(
                self.preview_inner, text=note, font=FONTS["small"],
                bg=COLORS["bg_card"], fg=COLORS["warning"], anchor="w",
            ).pack(fill="x", pady=(0, 4))

        # ── scrollable entry list ────────────────────────────────────────────
        list_scroll = ScrollableFrame(
            self.preview_inner, bg=COLORS["bg_card"], height=190,
        )
        list_scroll.pack(fill="x")
        list_body = list_scroll.body

        # render 20 rows at a time → yields back to UI event loop between batches
        BATCH = 20

        def _render_batch(start: int):
            end = min(start + BATCH, len(entries))
            for idx in range(start, end):
                self._add_playlist_row(list_body, idx, entries[idx])
            if end < len(entries):
                self.after(5, lambda: _render_batch(end))

        _render_batch(0)
        self._update_playlist_count()

    def _add_playlist_row(self, parent, idx: int, entry: dict):
        """Render one playlist entry row with a clearly visible custom checkbox."""
        from core.downloader import yt_thumb_url
        var = self._playlist_vars[idx]

        row = tk.Frame(parent, bg=COLORS["bg_card"], cursor="hand2")
        row.pack(fill="x", pady=1)

        # ── Custom checkbox: dark box → accent + white ✓ when selected ──────
        check_box = tk.Label(
            row, text="", font=FONTS["small_bold"],
            bg=COLORS["bg_input"], fg="white",
            width=2, padx=2, pady=2,
            relief="flat", cursor="hand2",
        )
        check_box.pack(side="left", padx=(8, 6), pady=8)

        def _refresh_box(*_):
            if var.get():
                check_box.configure(text="✓", bg=COLORS["accent"])
                row.configure(bg=COLORS["bg_hover"])
                meta_frame.configure(bg=COLORS["bg_hover"])
            else:
                check_box.configure(text="", bg=COLORS["bg_input"])
                row.configure(bg=COLORS["bg_card"])
                meta_frame.configure(bg=COLORS["bg_card"])

        def _toggle(*_):
            var.set(not var.get())
            self._update_playlist_count()

        var.trace_add("write", _refresh_box)

        # Thumbnail container — Frame uses pixel dimensions
        thumb_c = tk.Frame(row, bg=COLORS["bg_input"], width=72, height=46)
        thumb_c.pack_propagate(False)
        thumb_c.pack(side="left", padx=(0, 8), pady=4)
        thumb_lbl = tk.Label(thumb_c, bg=COLORS["bg_input"])
        thumb_lbl.pack(fill="both", expand=True)
        thumb_url = entry.get("thumbnail") or yt_thumb_url(entry.get("id", ""))
        if thumb_url:
            self._load_thumbnail(thumb_url, thumb_lbl, size=(72, 46))

        # Title + duration
        meta_frame = tk.Frame(row, bg=COLORS["bg_card"])
        meta_frame.pack(side="left", fill="x", expand=True)
        title = entry.get("title") or f"Video {idx + 1}"
        tk.Label(
            meta_frame, text=title, font=FONTS["small"], bg=COLORS["bg_card"],
            fg=COLORS["text_primary"], anchor="w",
            wraplength=400, justify="left",
        ).pack(fill="x")
        dur = entry.get("duration")
        if dur:
            tk.Label(
                meta_frame, text=self._format_duration(dur), font=FONTS["tiny"],
                bg=COLORS["bg_card"], fg=COLORS["text_muted"], anchor="w",
            ).pack(anchor="w")

        # Click anywhere on row → toggle
        for w in (row, check_box, meta_frame):
            w.bind("<Button-1>", _toggle)

        # Apply initial state
        _refresh_box()

        if idx < len(self._playlist_entries) - 1:
            tk.Frame(parent, bg=COLORS["border"], height=1).pack(fill="x")

    def _show_toast(self, message: str, duration_ms: int = 2500):
        """Show a small floating notification that auto-dismisses."""
        toast = tk.Toplevel(self)
        toast.overrideredirect(True)       # no title bar / borders
        toast.attributes("-topmost", True)

        tk.Label(
            toast, text=message,
            font=FONTS["small_bold"],
            bg=COLORS["accent"], fg="white",
            padx=18, pady=10,
        ).pack()

        # Position: bottom-right of the main window
        self.update_idletasks()
        rx = self.winfo_rootx() + self.winfo_width()  - toast.winfo_reqwidth()  - 20
        ry = self.winfo_rooty() + self.winfo_height() - toast.winfo_reqheight() - 60
        toast.geometry(f"+{rx}+{ry}")

        # Auto-close after duration
        self.after(duration_ms, toast.destroy)

    def _on_select_all(self):
        state = self._select_all_var.get()
        for v in self._playlist_vars:
            v.set(state)
        self._update_playlist_count()

    def _update_playlist_count(self):
        n_sel   = sum(v.get() for v in self._playlist_vars)
        n_total = len(self._playlist_vars)

        if self._playlist_count_lbl and self._playlist_count_lbl.winfo_exists():
            self._playlist_count_lbl.configure(text=f"{n_sel} / {n_total} selected")

        # sync Select All checkbox state
        self._select_all_var.set(n_sel == n_total)

        # scale quality sizes by selection count
        self._update_quality_size_labels(n_sel)

        # en/disable download button
        self._set_download_enabled(n_sel > 0 and self.quality_var.get() != -1)

    def _update_quality_size_labels(self, n: int):
        """Recompute size labels for playlist (total = per_video × n selected)."""
        if not self.quality_options:
            return
        for opt in self.quality_options:
            raw = opt.get("size_bytes")
            if raw and n > 0:
                opt["_display_size"] = downloader.scaled_size_text(raw, n)
            else:
                opt["_display_size"] = opt["size_label"]
            # update the size widget inside the open panel
            sw = opt.get("_size_widget")
            if sw and sw.winfo_exists():
                sw.configure(text=opt["_display_size"])
        # refresh dropdown button text if quality already chosen
        idx = self.quality_var.get()
        if idx != -1 and self._qdrop_btn and self._qdrop_btn.winfo_exists():
            opt  = self.quality_options[idx]
            size = opt.get("_display_size") or opt["size_label"]
            txt  = f"  ({size})" if size else ""
            self._qdrop_btn.configure(text=f"{opt['label']}{txt}  ▾")
        # update preview quality badge
        if hasattr(self, "_quality_badge") and self._quality_badge.winfo_exists() and idx != -1:
            opt  = self.quality_options[idx]
            size = opt.get("_display_size") or opt["size_label"]
            txt  = f"  ({size})" if size else ""
            self._quality_badge.configure(text=f"{opt['label']}{txt}")

    def _render_preview(self, info):
        for w in self.preview_inner.winfo_children():
            w.destroy()

        # Frame enforces pixel size (Label uses char units without image)
        thumb_container = tk.Frame(
            self.preview_inner, bg=COLORS["bg_input"],
            width=112, height=70,
        )
        thumb_container.pack_propagate(False)
        thumb_container.pack(side="left", padx=(0, 10))
        thumb_label = tk.Label(thumb_container, bg=COLORS["bg_input"])
        thumb_label.pack(fill="both", expand=True)

        # thumbnail URL — fallback to thumbnails list if main field is empty
        thumb_url = info.get("thumbnail") or next(
            (t.get("url") for t in reversed(info.get("thumbnails") or []) if t.get("url")),
            None
        )
        self._load_thumbnail(thumb_url, thumb_label, size=(112, 70))

        info_frame = tk.Frame(self.preview_inner, bg=COLORS["bg_dark"])
        info_frame.pack(side="left", fill="x", expand=True)

        # Fresh every time this card is rebuilt (i.e. every new fetch) —
        # no leftover edit from whatever the previous video was renamed to.
        title = info.get("title") or "Unknown title"
        self.edited_title = title

        title_row = tk.Frame(info_frame, bg=COLORS["bg_dark"])
        title_row.pack(fill="x")
        self._title_label = tk.Label(
            title_row, text=title, font=FONTS["small_bold"], bg=COLORS["bg_dark"],
            fg=COLORS["text_primary"], anchor="w", wraplength=460, justify="left",
        )
        self._title_label.pack(side="left", fill="x", expand=True)
        self._title_edit_btn = tk.Label(
            title_row, text="Edit", font=FONTS["small_bold"], bg=COLORS["bg_dark"],
            fg=COLORS["accent"], cursor="hand2", padx=6, pady=2,
        )
        self._title_edit_btn.pack(side="left", padx=(8, 0), anchor="n")
        self._title_edit_btn.bind("<Button-1>", lambda e: self._start_title_edit())
        self._title_edit_btn.bind(
            "<Enter>", lambda e: self._title_edit_btn.configure(fg=COLORS["accent_hover"])
        )
        self._title_edit_btn.bind(
            "<Leave>", lambda e: self._title_edit_btn.configure(fg=COLORS["accent"])
        )
        self._title_entry = None  # created on demand in _start_title_edit

        self._quality_badge = tk.Label(
            info_frame, text="", font=FONTS["small"],
            bg=COLORS["bg_dark"], fg=COLORS["accent"], anchor="w",
        )
        self._quality_badge.pack(fill="x", pady=(2, 0))

        duration     = info.get("duration")
        dur_text     = self._format_duration(duration) if duration else "—"
        uploader     = info.get("uploader") or "—"
        view_count   = info.get("view_count")
        views        = f"{view_count:,} views" if view_count else ""
        meta = "  \u00b7  ".join(x for x in (dur_text, uploader, views) if x and x != "—")
        tk.Label(
            info_frame, text=meta, font=FONTS["tiny"], bg=COLORS["bg_dark"],
            fg=COLORS["text_muted"], anchor="w",
        ).pack(fill="x", pady=(3, 0))

    def _start_title_edit(self):
        if self._title_entry is not None:
            return  # already editing
        self._title_label.pack_forget()
        self._title_edit_btn.pack_forget()
        entry = PlaceholderEntry(
            self._title_label.master, tk.StringVar(), "Enter a title…", font=FONTS["small_bold"],
            bg=COLORS["bg_input"], fg=COLORS["text_primary"], relief="flat",
            insertbackground=COLORS["text_primary"],
        )
        entry.insert(0, self.edited_title or "")
        entry.pack(side="left", fill="x", expand=True, ipady=2)
        entry.focus_set()
        entry.select_range(0, "end")
        entry.icursor("end")
        entry.bind("<Return>", lambda e: self._confirm_title_edit())
        entry.bind("<Escape>", lambda e: self._cancel_title_edit())
        entry.bind("<FocusOut>", lambda e: self._confirm_title_edit())
        self._title_entry = entry

    def _confirm_title_edit(self):
        entry = self._title_entry
        if entry is None:
            return  # nothing pending — safe to call unconditionally
        try:
            alive = bool(entry.winfo_exists())
        except Exception:
            alive = False
        if not alive:   # the preview card was rebuilt (e.g. a playlist took its place)
            self._title_entry = None
            return
        new_title = self._title_entry.get().strip()
        self.edited_title = new_title or self.edited_title
        self._title_label.configure(text=self.edited_title)
        self._end_title_edit()

    def _cancel_title_edit(self):
        self._end_title_edit()

    def _end_title_edit(self):
        if self._title_entry is not None:
            self._title_entry.destroy()
            self._title_entry = None
        self._title_label.pack(side="left", fill="x", expand=True)
        self._title_edit_btn.pack(side="left", padx=(8, 0), anchor="n")

    def _load_thumbnail(self, url, label_widget, size=(112, 70)):
        """Load thumbnail in background thread — marshal result to main thread."""
        if not url:
            return

        def work():
            import urllib.request, io
            req = urllib.request.Request(url, headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read(8 * 1024 * 1024)
            from PIL import Image
            img = Image.open(io.BytesIO(data)).convert("RGB")
            img.thumbnail(size, Image.LANCZOS)
            return img

        def done(img, exc):
            if exc is not None:
                if not isinstance(exc, ImportError):
                    errorlog.warning("thumbnail", f"{type(exc).__name__}: {exc}")
                return
            try:
                if label_widget.winfo_exists():
                    self._set_thumbnail(label_widget, img)
            except tk.TclError:
                pass

        run_in_thread(self, work, done, name="thumb-loader")

    def _set_thumbnail(self, label_widget, pil_img):
        """Called on main thread — safe to create PhotoImage here."""
        try:
            if not label_widget.winfo_exists():
                return
            from PIL import ImageTk
            photo = ImageTk.PhotoImage(pil_img)
            # No width/height — let container Frame control the size
            label_widget.configure(image=photo, text="")
            label_widget.image = photo
        except Exception as e:
            import sys
            print(f"[thumbnail set] {type(e).__name__}: {e}", file=sys.stderr)

    def _render_subtitles(self, info):
        for w in self.sub_inner.winfo_children():
            w.destroy()
        self.sub_vars = {}

        langs = downloader.available_subtitles(info)
        if not langs:
            tk.Label(
                self.sub_inner, text="No subtitles available.",
                bg=COLORS["bg_panel"], fg=COLORS["text_muted"], font=FONTS["small"],
            ).pack(anchor="w")
            return

        wrap = tk.Frame(self.sub_inner, bg=COLORS["bg_panel"])
        wrap.pack(fill="x")
        for i, (code, label) in enumerate(langs):
            var = tk.BooleanVar(value=False)
            self.sub_vars[code] = var
            BigCheckbutton(
                wrap, text=label, variable=var, font=FONTS["small"],
                bg=COLORS["bg_panel"], fg=COLORS["text_secondary"],
                command=self._update_sub_count,
            ).grid(row=i // 5, column=i % 5, sticky="w", padx=6, pady=3)

        self.sub_count_label = tk.Label(
            self.sub_inner, text="Selected: None", font=FONTS["small"],
            bg=COLORS["bg_panel"], fg=COLORS["text_muted"],
        )
        self.sub_count_label.pack(anchor="w", pady=(6, 0))

    def _update_sub_count(self):
        selected = [c for c, v in self.sub_vars.items() if v.get()]
        text = "Selected: None" if not selected else f"Selected: {', '.join(selected)}"
        self.sub_count_label.configure(text=text)

    def _render_quality(self, info):
        for w in self.quality_frame.winfo_children():
            w.destroy()

        self.quality_options = downloader.build_quality_options(info)
        self.quality_var.set(-1)
        self._quality_panel_open = False
        self._set_download_enabled(False)

        # ── dropdown trigger button ───────────────────────────────────────
        self._qdrop_btn = RoundedButton(
            self.quality_frame, "Select quality  \u25be", variant="default", stretch=True,
            align="left", padx=12, pady=10, font=FONTS["body_bold"], bg=COLORS["bg_dark"],
            command=self._toggle_quality_panel)
        self._qdrop_btn.pack(fill="x")

        # codec picker: filled in and shown once a video quality is chosen
        self._codec_row = tk.Frame(self.quality_frame, bg=COLORS["bg_dark"])
        self._codec_var = tk.StringVar(value="Auto")

        # container / audio format + "embed thumbnail / metadata", below the codec
        # picker. The tick boxes start from Settings for every newly fetched video.
        st = self.app.settings
        self._output_row = tk.Frame(self.quality_frame, bg=COLORS["bg_dark"])
        self._container_var = tk.StringVar(value=oo.DEFAULT_CONTAINER)
        self._afmt_var = tk.StringVar(value=oo.DEFAULT_AUDIO_FORMAT)
        self._abitrate_var = tk.StringVar(value=str(oo.DEFAULT_AUDIO_BITRATE))
        self.embed_thumb_var = tk.BooleanVar(value=bool(st.get("embed_thumbnail", False)))
        self.embed_meta_var = tk.BooleanVar(value=bool(st.get("embed_metadata", False)))

        # ── panel (hidden until button clicked) ──────────────────────────
        self._qdrop_panel = tk.Frame(
            self.quality_frame, bg=COLORS["bg_card"],
            highlightbackground=COLORS["border"], highlightthickness=1,
        )
        self._quality_global_id = None

        # panel header: title + close button
        q_hdr = tk.Frame(self._qdrop_panel, bg=COLORS["bg_card"])
        q_hdr.pack(fill="x", padx=10, pady=(6, 2))
        tk.Label(q_hdr, text="Select Quality", font=FONTS["small_bold"],
                 bg=COLORS["bg_card"], fg=COLORS["text_secondary"]).pack(side="left")
        IconButton(q_hdr, "x", command=self._close_quality_panel, tooltip="Close", size=24,
                   bg=COLORS["bg_card"]).pack(side="right")
        tk.Frame(self._qdrop_panel, bg=COLORS["border"], height=1).pack(fill="x")

        for idx, opt in enumerate(self.quality_options):
            row = tk.Frame(self._qdrop_panel, bg=COLORS["bg_card"], cursor="hand2")
            row.pack(fill="x")

            dot = tk.Label(row, text="○", font=FONTS["small"],
                           bg=COLORS["bg_card"], fg=COLORS["text_muted"], width=2)
            dot.pack(side="left", padx=(10, 4), pady=8)

            name_lbl = tk.Label(row, text=opt["label"], font=FONTS["body"],
                                bg=COLORS["bg_card"], fg=COLORS["text_primary"])
            name_lbl.pack(side="left", pady=8)

            widgets = [row, dot, name_lbl]

            if opt.get("codecs"):
                codec_lbl = tk.Label(row, text="  ·  ".join(opt["codecs"]), font=FONTS["small"],
                                     bg=COLORS["bg_card"], fg=COLORS["text_muted"])
                codec_lbl.pack(side="left", padx=(10, 0), pady=8)
                widgets.append(codec_lbl)

            if opt["size_label"]:
                cur_size = opt.get("_display_size") or opt["size_label"]
                size_lbl = tk.Label(self._qdrop_panel, text=cur_size,
                                    font=FONTS["small"], bg=COLORS["bg_card"],
                                    fg=COLORS["text_muted"])
                size_lbl.pack(in_=row, side="left", padx=(8, 0), pady=8)
                opt["_size_widget"] = size_lbl
                widgets.append(size_lbl)

            for w in widgets:
                w.bind("<Button-1>", lambda _e, i=idx: self._select_quality(i))
            for w in widgets:
                w.bind("<Enter>", lambda _e, ws=widgets: self._row_hover(ws, True))
                w.bind("<Leave>", lambda _e, ws=widgets, i=idx: self._row_hover(
                    ws, False, selected=(self.quality_var.get() == i)))

            opt["_row_widgets"] = widgets
            opt["_dot"] = dot

            if idx < len(self.quality_options) - 1:
                tk.Frame(self._qdrop_panel, bg=COLORS["border"], height=1
                         ).pack(fill="x")

    def _toggle_quality_panel(self):
        if self._quality_panel_open:
            self._close_quality_panel()
        else:
            self._open_quality_panel()

    def _open_quality_panel(self):
        self._qdrop_panel.pack(fill="x", pady=(0, 1), before=self._qdrop_btn)
        self._quality_panel_open = True
        self._update_qdrop_btn_text("▾")
        self.after(50, self._bind_quality_outside)

    def _close_quality_panel(self):
        if self._qdrop_panel and self._qdrop_panel.winfo_exists() \
                and self._qdrop_panel.winfo_ismapped():
            self._qdrop_panel.pack_forget()
        self._quality_panel_open = False
        if self._qdrop_btn and self._qdrop_btn.winfo_exists():
            self._update_qdrop_btn_text("▴")
        if self._quality_global_id:
            try:
                self.winfo_toplevel().unbind("<Button-1>", self._quality_global_id)
            except Exception:
                pass
            self._quality_global_id = None

    def _update_qdrop_btn_text(self, arrow):
        idx = self.quality_var.get()
        if idx == -1:
            self._qdrop_btn.configure(text=f"Select quality  {arrow}")
        else:
            opt  = self.quality_options[idx]
            size = opt.get("_display_size") or opt["size_label"]
            txt  = f"  ({size})" if size else ""
            self._qdrop_btn.configure(text=f"{opt['label']}{txt}  {arrow}")

    def _bind_quality_outside(self):
        def _check(event):
            if not self._quality_panel_open:
                return
            p = self._qdrop_panel
            if not p.winfo_exists() or not p.winfo_ismapped():
                return
            px, py = p.winfo_rootx(), p.winfo_rooty()
            pw, ph = p.winfo_width(), p.winfo_height()
            bx = self._qdrop_btn.winfo_rootx()
            by = self._qdrop_btn.winfo_rooty()
            bw = self._qdrop_btn.winfo_width()
            bh = self._qdrop_btn.winfo_height()
            in_panel = px <= event.x_root <= px+pw and py <= event.y_root <= py+ph
            on_btn   = bx <= event.x_root <= bx+bw and by <= event.y_root <= by+bh
            if not in_panel and not on_btn:
                self._close_quality_panel()
        self._quality_global_id = self.winfo_toplevel().bind("<Button-1>", _check, add=True)

    def _select_quality(self, idx):
        prev = self.quality_var.get()
        self.quality_var.set(idx)

        if prev != -1 and prev < len(self.quality_options):
            self._apply_row_style(self.quality_options[prev], selected=False)
        self._apply_row_style(self.quality_options[idx], selected=True)

        # close the panel
        self._close_quality_panel()

        # show the selected quality on the button
        opt  = self.quality_options[idx]
        size = opt.get("_display_size") or opt["size_label"]
        txt  = f"  ({size})" if size else ""
        self._qdrop_btn.configure(text=f"{opt['label']}{txt}  ▾")
        self._show_codec_picker(opt)
        self._on_quality_selected()

    # ------------------------------------------------------------------ codec
    _CODEC_NOTES = {
        "Auto":  "Auto picks the smallest file available.",
        "AV1":   "Smallest files. Older devices may struggle to play it.",
        "VP9":   "Smaller than H.264. Plays on most modern devices.",
        "H.265": "Small files, but some browsers and players cannot play it.",
        "H.264": "Largest files, but plays on almost every device.",
    }

    def _show_codec_picker(self, opt):
        """Under the quality button: the codec (video only), then the container /
        audio format and the embed options. Called whenever a quality is chosen."""
        for w in self._codec_row.winfo_children():
            w.destroy()
        self._codec_row.pack_forget()
        codecs = opt.get("codecs") or []
        if not opt.get("audio_only") and codecs:
            self._build_codec_row(opt, codecs)
        self._apply_output_defaults_once(opt)
        self._show_output_picker(opt)
        self._refresh_quality_displays(opt)

    def _build_codec_row(self, opt, codecs):
        # the Settings default ("H.264 for compatibility") applies when available
        wanted = "H.264" if self.app.settings.get("default_codec") == "h264" and "H.264" in codecs else None
        if wanted and opt.get("codec") in (None, "Auto"):
            opt.update(downloader.apply_codec(opt, wanted))
        self._codec_var.set(opt.get("codec") or "Auto")

        bg = COLORS["bg_dark"]
        shown = [name for name, _ in downloader.CODEC_FAMILIES if name in codecs or name != "H.265"]
        choices = [("Auto", "Auto")] + [(n, n) for n in shown]
        top = tk.Frame(self._codec_row, bg=bg)
        top.pack(fill="x")
        tk.Label(top, text="Codec", font=FONTS["small_bold"], bg=bg, width=9, anchor="w",
                 fg=COLORS["text_secondary"]).pack(side="left")
        SegmentedControl(top, choices, self._codec_var, command=self._on_codec_changed, bg=bg,
                         disabled=[n for n in shown if n not in codecs]).pack(side="left")
        self._codec_note = tk.Label(self._codec_row, text=self._CODEC_NOTES.get(self._codec_var.get(), ""),
                                    font=FONTS["small"], bg=bg, fg=COLORS["text_muted"], anchor="w")
        self._codec_note.pack(fill="x", pady=(6, 0))
        self._codec_row.pack(fill="x", pady=(8, 0))

    def _on_codec_changed(self):
        idx = self.quality_var.get()
        if idx == -1:
            return
        opt = self.quality_options[idx]
        opt.update(downloader.apply_codec(opt, self._codec_var.get()))
        self._codec_note.configure(text=self._CODEC_NOTES.get(self._codec_var.get(), ""))
        self._refresh_quality_displays(opt)
        # the chosen codec decides which containers can hold it (and may have reset the container)
        self.after_idle(lambda o=opt: self._show_output_picker(o))

    # --------------------------------------------- container / audio / embed
    def _apply_output_defaults_once(self, opt):
        """Settings' default audio format / container, applied the first time a
        quality is chosen — after that the person's own picks stay."""
        if opt.get("_defaults_applied"):
            return
        opt["_defaults_applied"] = True
        st = self.app.settings
        if opt.get("audio_only"):
            opt.update(downloader.apply_audio_format(
                opt, st.get("default_audio_format"), st.get("default_audio_bitrate")))
        elif opt.get("codecs"):
            opt.update(downloader.apply_container(opt, st.get("default_container")))

    def _show_output_picker(self, opt):
        row = self._output_row
        if not row.winfo_exists():
            return
        for w in row.winfo_children():
            w.destroy()
        row.pack_forget()
        bg = COLORS["bg_dark"]

        def line(label):
            f = tk.Frame(row, bg=bg)
            f.pack(fill="x", pady=(0, 6))
            tk.Label(f, text=label, font=FONTS["small_bold"], bg=bg, width=9, anchor="w",
                     fg=COLORS["text_secondary"]).pack(side="left")
            return f

        def note(text):
            tk.Label(row, text=text, font=FONTS["small"], bg=bg, fg=COLORS["text_muted"],
                     anchor="w", justify="left", wraplength=720).pack(fill="x", pady=(0, 6))

        if opt.get("audio_only"):
            fmt = oo.normalize_audio_format(opt.get("audio_format"))
            self._afmt_var.set(fmt)
            SegmentedControl(line("Format"),
                             [(k, oo.AUDIO_FORMATS[k]["label"]) for k in oo.AUDIO_FORMAT_ORDER],
                             self._afmt_var, command=self._on_audio_format_changed, bg=bg).pack(side="left")
            if fmt == "mp3":
                self._abitrate_var.set(str(oo.normalize_audio_bitrate(opt.get("audio_bitrate"))))
                f = line("Bitrate")
                SegmentedControl(f, [(str(b), str(b)) for b in oo.MP3_BITRATES], self._abitrate_var,
                                 command=self._on_audio_format_changed, bg=bg).pack(side="left")
                tk.Label(f, text="kbps", font=FONTS["small"], bg=bg,
                         fg=COLORS["text_muted"]).pack(side="left", padx=(8, 0))
            note(oo.AUDIO_FORMATS[fmt]["note"])
        elif opt.get("codecs"):
            container = oo.normalize_container(opt.get("container"))
            self._container_var.set(container)
            choices = oo.container_choices(opt["codecs"], opt.get("codec") or "Auto")
            SegmentedControl(line("Container"), [(k, label) for k, label, _ok in choices],
                             self._container_var, command=self._on_container_changed, bg=bg,
                             disabled=[k for k, _l, ok in choices if not ok]).pack(side="left")
            note(oo.CONTAINERS[container]["note"])

        embed = line("Embed")
        BigCheckbutton(embed, text="Thumbnail", variable=self.embed_thumb_var, font=FONTS["small"],
                       bg=bg, fg=COLORS["text_secondary"]).pack(side="left", padx=(0, 18))
        BigCheckbutton(embed, text="Metadata", variable=self.embed_meta_var, font=FONTS["small"],
                       bg=bg, fg=COLORS["text_secondary"]).pack(side="left")
        why = oo.embed_note(opt)
        note(why or "Thumbnail becomes the file's cover picture; metadata adds title, "
                    "uploader, date, description and chapters.")
        row.pack(fill="x", pady=(8, 0))

    def _on_container_changed(self):
        idx = self.quality_var.get()
        if idx == -1:
            return
        opt = self.quality_options[idx]
        opt.update(downloader.apply_container(opt, self._container_var.get()))
        self._refresh_quality_displays(opt)
        self.after_idle(lambda o=opt: self._show_output_picker(o))

    def _on_audio_format_changed(self):
        idx = self.quality_var.get()
        if idx == -1:
            return
        opt = self.quality_options[idx]
        opt.update(downloader.apply_audio_format(opt, self._afmt_var.get(), self._abitrate_var.get()))
        self._refresh_quality_displays(opt)
        self.after_idle(lambda o=opt: self._show_output_picker(o))

    def _refresh_quality_displays(self, opt):
        """The chosen codec / container / audio format changed this option's label or
        size: update every place that shows it."""
        name = (opt.get("_row_widgets") or [None, None, None])[2]
        if name is not None and name.winfo_exists():
            name.configure(text=opt["label"])
        n = sum(v.get() for v in self._playlist_vars) if self._playlist_vars else 1
        self._update_quality_size_labels(max(n, 1) if self._playlist_vars else 0)
        if not self._playlist_vars:
            opt.pop("_display_size", None)
            sw = opt.get("_size_widget")
            if sw and sw.winfo_exists():
                sw.configure(text=opt["size_label"])
            size = opt["size_label"]
            self._qdrop_btn.configure(text=f"{opt['label']}{f'  ({size})' if size else ''}  ▾")
            if hasattr(self, "_quality_badge") and self._quality_badge.winfo_exists():
                self._quality_badge.configure(text=f"{opt['label']}{f'  ({size})' if size else ''}")

    @staticmethod
    def _apply_row_style(opt, selected: bool):
        bg  = COLORS["bg_input"] if selected else COLORS["bg_card"]
        dot = "●" if selected else "○"
        dot_fg = COLORS["accent"] if selected else COLORS["text_muted"]
        for w in opt.get("_row_widgets", []):
            try:
                w.configure(bg=bg)
            except Exception:
                pass
        d = opt.get("_dot")
        if d and d.winfo_exists():
            d.configure(text=dot, fg=dot_fg)

    @staticmethod
    def _row_hover(widgets, entering: bool, selected: bool = False):
        if selected:
            return
        bg = COLORS["bg_hover"] if entering else COLORS["bg_card"]
        for w in widgets:
            try:
                w.configure(bg=bg)
            except Exception:
                pass

    def _on_quality_selected(self):
        idx = self.quality_var.get()
        can_dl = idx != -1 and (
            not self.is_playlist or any(v.get() for v in self._playlist_vars)
        )
        self._set_download_enabled(can_dl)
        if idx == -1:
            return
        opt  = self.quality_options[idx]
        size = opt.get("_display_size") or opt["size_label"]
        txt  = f"  ({size})" if size else ""
        if hasattr(self, "_quality_badge") and self._quality_badge.winfo_exists():
            self._quality_badge.configure(text=f"{opt['label']}{txt}")

    def _set_download_enabled(self, enabled):
        if enabled:
            self.download_btn.configure(state="normal")
        else:
            self.download_btn.configure(state="disabled")

    # ============================================================== clear
    def _drop_fetched_view(self):
        """Forget the fetched video: hide its preview, qualities, subtitles and trim
        range and disable Download. Called when the URL changes (so the previous
        video's qualities can never be shown, or downloaded, for a different link)
        and by Clear."""
        if self._quality_panel_open:
            self._close_quality_panel()
        self.current_info = None
        self._info_url = None
        self.is_playlist = False
        self.edited_title = None
        self._title_entry = None

        # Hide the cards again
        self.preview_card.pack_forget()
        self.quality_card.grid_remove()
        self._render_empty_preview()
        self._render_empty_quality()
        self._quality_panel_open = False
        self.quality_options = []
        self.quality_var.set(-1)

        self._playlist_entries   = []
        self._playlist_vars      = []
        self._playlist_count_lbl = None

        # Clear subtitle section inside advanced options
        for w in self.sub_inner.winfo_children():
            w.destroy()
        self.sub_vars = {}
        self._set_download_enabled(False)

        # Trim slider would otherwise keep showing the just-cleared
        # video's duration/range — back to its "no video yet" placeholder.
        self.trim_enabled.set(False)
        if self._trim_slider is not None:
            self._trim_slider.set_duration(0)
        self.trim_start_var.set("00:00:00")
        self.trim_end_var.set("00:00:00")

    def _on_clear(self):
        # Close any open floating panels first
        if self._quality_panel_open:
            self._close_quality_panel()
        if self._adv_panel_visible:
            self._close_advanced_panel()

        # Cancel pending debounce
        if self._debounce_id is not None:
            self.after_cancel(self._debounce_id)
            self._debounce_id = None

        self.url_var.set("")
        self.fetch_status_var.set("")
        self._drop_fetched_view()

        # reset prefetch state
        self._prefetch_url      = None
        self._prefetch_info     = None
        self._prefetch_loading  = False
        self._fetch_on_complete = False

    # ============================================================== download
    def _on_download_clicked(self):
        """Download button. Tk swallows an exception raised inside a button
        callback (it only reaches the terminal), so a failure here used to look
        exactly like "the button does nothing". Report it instead."""
        try:
            self._start_download()
        except Exception as exc:                               # noqa: BLE001
            errorlog.error("download", f"Could not start the download: {exc!r}")
            try:
                messagebox.showerror("Could not start the download", str(exc) or repr(exc),
                                     parent=self.winfo_toplevel())
            except Exception:
                pass

    def _start_download(self):
        if not self.current_info or self.quality_var.get() == -1:
            return
        # Dialogs belong to the Add Download window. Without a parent Tk puts
        # them on the main window, which can sit BEHIND this dialog on Linux:
        # a hidden modal box then looks like a frozen, unresponsive app.
        # (This local name shadows the module-level one for this method only.)
        messagebox = _parented_messagebox(self.winfo_toplevel())
        # Clicking Download directly (without pressing Enter or clicking
        # away from the title field first) must still apply whatever's
        # currently typed there — don't rely on FocusOut/Return having
        # already fired by this point.
        self._confirm_title_edit()
        quality = downloader.job_quality(self.quality_options[self.quality_var.get()])
        title = (self.edited_title or self.current_info.get("title") or self.url_var.get()).strip()

        # This is a per-download override only — it must NOT touch
        # self.app.settings["output_dir"], which is the separate,
        # independent default set on the main Settings page. Writing it
        # back there (as this used to do) meant a one-off folder chosen
        # here silently became the new global default too. Only the
        # local job snapshot gets this folder; the main setting is left
        # exactly as the person configured it.
        out_dir = self.output_dir_var.get().strip() or self.app.settings.get("output_dir")
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("Output Folder Error", str(exc))
            return

        settings_snapshot = dict(self.app.settings)
        settings_snapshot["output_dir"] = out_dir

        trim = None
        if self.trim_enabled.get():
            start_s = self._parse_time_strict(self.trim_start_var.get())
            end_s   = self._parse_time_strict(self.trim_end_var.get())
            if start_s is None or end_s is None:
                # The lenient parser used for the slider turned "1;30" into 0
                # without a word, so a typo silently trimmed from the start.
                which = "Start" if start_s is None else "End"
                messagebox.showerror(
                    "Invalid Trim Time",
                    f"{which} time is not a valid time. Use HH:MM:SS, MM:SS or plain "
                    "seconds (for example 00:01:30)."
                )
                return
            duration = int((self.current_info or {}).get("duration") or 0)
            if duration and start_s >= duration:
                messagebox.showerror(
                    "Invalid Trim Time",
                    f"Start time ({self.trim_start_var.get()}) is past the end of the "
                    f"video ({self._seconds_to_hms(duration)})."
                )
                return
            if duration and end_s > duration:
                end_s = duration
                self.trim_end_var.set(self._seconds_to_hms(duration))
            if end_s == 0:
                messagebox.showerror(
                    "Invalid Trim Time",
                    "Trim end time must be greater than 00:00:00."
                )
                return
            if end_s <= start_s:
                messagebox.showerror(
                    "Invalid Trim Time",
                    f"End time ({self.trim_end_var.get()}) must be greater than start time ({self.trim_start_var.get()})."
                )
                return
            if duration and start_s == 0 and end_s >= duration:
                # Ticking Trim without changing the range selects the whole
                # video. Re-encoding all of it through ffmpeg (and dropping
                # aria2c) just to get the same video back is pure waste.
                self.app.set_status("Trim range covers the whole video — downloading it untrimmed.")
            else:
                trim = {
                    "enabled": True,
                    "start_seconds": start_s,
                    "end_seconds": end_s,
                }

        # Only a folder that differs from the global default counts as a
        # per-download override; otherwise the job keeps following Settings.
        default_dir = (self.app.settings.get("output_dir") or "").strip()
        is_override = os.path.abspath(os.path.expanduser(out_dir)) != \
            os.path.abspath(os.path.expanduser(default_dir)) if default_dir else bool(out_dir)

        options = {
            "subtitles":       [c for c, v in self.sub_vars.items() if v.get()],
            "custom_filename": "",
            "trim":            trim,
            "output_dir_override": out_dir if is_override else "",
            "embed_thumbnail": bool(self.embed_thumb_var.get()),
            "embed_metadata":  bool(self.embed_meta_var.get()),
        }

        if self.is_playlist:
            selected = [
                (entry, var) for entry, var in
                zip(self._playlist_entries, self._playlist_vars)
                if var.get()
            ]
            if not selected:
                messagebox.showwarning("No Selection", "Please select at least one video.")
                return
            if trim and not messagebox.askyesno(
                "Trim on a playlist",
                f"The same trim range ({self._seconds_to_hms(trim['start_seconds'])} – "
                f"{self._seconds_to_hms(trim['end_seconds'])}) will be applied to every "
                f"selected video ({len(selected)}), whatever its length.\n\nContinue?"
            ):
                return

            # Feedback right away: building and queueing a long playlist takes a
            # moment, and nothing else on screen changes until it is done.
            self.app.set_status(f"Adding {len(selected)} videos to the queue…")
            self.update_idletasks()

            jobs_to_queue = []
            duplicate_titles = []
            # Playlist entries always keep their own individual title —
            # the pencil-edit on the single-video preview card has no
            # equivalent here, and reusing one literal filename across
            # every selected video would make them all collide on the
            # same output file, silently overwriting each other as they
            # finish.
            playlist_options = dict(options)
            for entry, _ in selected:
                entry_url = (entry.get("url") or entry.get("webpage_url")
                             or f"https://www.youtube.com/watch?v={entry.get('id', '')}")
                entry_title = entry.get("title") or "Unknown"
                job = downloader.DownloadJob(
                    entry_url, entry_title, copy.deepcopy(quality), dict(settings_snapshot),
                    **copy.deepcopy(playlist_options)
                )
                if self._find_duplicate(job.title):
                    duplicate_titles.append(job.title)
                jobs_to_queue.append(job)

            if duplicate_titles:
                preview = "\n".join(f"• {t}" for t in duplicate_titles[:5])
                more = f"\n…and {len(duplicate_titles) - 5} more" if len(duplicate_titles) > 5 else ""
                proceed = messagebox.askyesno(
                    "Duplicate Downloads Found",
                    f"{len(duplicate_titles)} of {len(jobs_to_queue)} selected video(s) "
                    f"appear to already be queued or in your history:\n\n{preview}{more}\n\n"
                    f"Download all selected videos anyway?"
                )
                if not proceed:
                    return

            self.app.queue_downloads(jobs_to_queue)
            self._notify_queued()
            n = len(jobs_to_queue)
            self.app.set_status(f"{n} video{'s' if n > 1 else ''} added to the queue.")
        else:
            job = downloader.DownloadJob(
                self._info_url or self.url_var.get().strip(), title, quality, settings_snapshot, **options
            )
            original_title = (self.current_info.get("title") or "").strip()
            if title and title != original_title:
                # job.title already has the quality tag appended (see
                # DownloadJob.__init__) — reuse that rather than
                # recomputing it. This becomes a literal part of the
                # output filename rather than a %(title)s substitution,
                # so — unlike the untouched default — it bypasses yt-dlp's
                # own filesystem-safety sanitization; do the equivalent
                # cleanup ourselves.
                job.options["custom_filename"] = f"{self._sanitize_for_filename(job.title)}.%(ext)s"
            dup = self._find_duplicate(job.title)
            if dup:
                where = "already in your queue" if dup == "queue" else "already in your download history"
                proceed = messagebox.askyesno(
                    "Duplicate Download",
                    f"'{job.title}' is {where}.\n\nDownload it again anyway?"
                )
                if not proceed:
                    return
            self.app.queue_download(job)
            self._notify_queued()
            self.app.set_status(f"Added to queue: {title}")

    def _notify_queued(self):
        if self.on_queued:
            self.on_queued()

    def focus_url(self):
        """Put the cursor in the URL field (used when the dialog opens)."""
        self._url_entry.focus_set()
        self._url_entry.select_range(0, "end")

    def _find_duplicate(self, expected_title):
        """Delegates to the app-level check so extension-initiated
        downloads use the exact same duplicate logic."""
        return self.app.find_duplicate_title(expected_title)

    def _on_trim_slider_change(self, start_s, end_s):
        """Dragging a handle → push the new times into the Start/End
        entry fields (their own trace below pushes right back into the
        slider, but set_range() there is a no-op redraw, not a loop)."""
        self.trim_start_var.set(self._seconds_to_hms(start_s))
        self.trim_end_var.set(self._seconds_to_hms(end_s))

    def _on_trim_text_changed(self, *_args):
        """Start/End entry edited by hand → move the slider's handles
        to match. Guarded on the slider existing since this trace can
        in principle fire before _build_advanced_options finishes."""
        if self._trim_slider is None:
            return
        self._trim_slider.set_range(
            self._time_to_seconds(self.trim_start_var.get()),
            self._time_to_seconds(self.trim_end_var.get()),
        )

    @staticmethod
    def _time_to_seconds(text):
        parts = [p.strip() for p in text.strip().split(":") if p.strip() != ""]
        try:
            parts = [int(p) for p in parts]
        except ValueError:
            return 0
        while len(parts) < 3:
            parts.insert(0, 0)
        h, m, s = parts[-3:]
        return h * 3600 + m * 60 + s

    @staticmethod
    def _parse_time_strict(text):
        """'1:30:05' / '01:30' / '90' -> seconds, or None if it is not a valid time.
        (_time_to_seconds above is deliberately forgiving — it feeds the slider
        while typing — so it can't be used to validate what gets downloaded.)"""
        parts = [p.strip() for p in (text or "").strip().split(":")]
        if not parts or len(parts) > 3 or any(not p.isdigit() for p in parts):
            return None
        nums = [int(p) for p in parts]
        if len(nums) > 1 and any(n >= 60 for n in nums[1:]):
            return None            # minutes/seconds must be 0-59 when hours/minutes are given
        while len(nums) < 3:
            nums.insert(0, 0)
        h, m, s = nums
        return h * 3600 + m * 60 + s

    @staticmethod
    def _seconds_to_hms(seconds):
        seconds = max(0, int(seconds or 0))
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    @staticmethod
    def _sanitize_for_filename(name):
        """Only used for a pencil-edited title, which becomes a literal
        part of the output filename rather than a %(title)s substitution
        yt-dlp sanitizes on its own — do the equivalent cleanup by hand:
        swap out characters invalid on Windows or with special meaning in
        a path (a stray "/" would otherwise be read as a subdirectory)."""
        name = re.sub(r'[\\/:*?"<>|]', "-", name)
        name = name.strip().strip(".")
        # The result becomes part of a yt-dlp output TEMPLATE, where "%" starts
        # a field ("%(ext)s"). A title like "100% real" would raise an
        # "Error in output template", so escape it as "%%" (= literal %).
        name = name.replace("%", "%%")
        return name or "video"

    @staticmethod
    def _safe_int(text, default):
        try:
            return int(text)
        except (ValueError, TypeError):
            return default

    def _browse_output_dir(self):
        chosen = filedialog.askdirectory()
        if chosen:
            self.output_dir_var.set(chosen)
