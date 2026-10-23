"""
ui/widgets.py
VidDown's shared widget kit. Everything here draws from the theme tokens
(theme.COLORS / FONTS / RADIUS) so components stay consistent: one corner
radius, one accent, one set of states (hover, pressed, focus, disabled).

Rounded shapes are rendered with Pillow at 4x and scaled down so their edges
are anti-aliased (Tk's canvas polygons are not). Images are cached per
size/colour combination.
"""
import tkinter as tk
import tkinter.font as tkfont

from PIL import Image, ImageDraw

from theme import COLORS, FONTS, RADIUS
from ui import icons

_SS = 4
_shape_cache = {}


def _rgba(color, alpha=255):
    c = color.lstrip("#")
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), alpha)


def shape(w, h, radius, fill=None, outline=None, border=1):
    """Anti-aliased rounded rectangle as a PhotoImage (transparent corners)."""
    w, h = max(2, int(w)), max(2, int(h))
    key = (w, h, radius, fill, outline, border)
    cached = _shape_cache.get(key)
    if cached is not None:
        return cached
    img = Image.new("RGBA", (w * _SS, h * _SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    box = [0, 0, w * _SS - 1, h * _SS - 1]
    r = min(radius, h / 2, w / 2) * _SS
    d.rounded_rectangle(box, radius=r, fill=_rgba(fill) if fill else None,
                        outline=_rgba(outline) if outline else None,
                        width=border * _SS if outline else 0)
    photo = icons.photo_from(img.resize((w, h), Image.LANCZOS))
    _shape_cache[key] = photo
    return photo


def clear_caches():
    _shape_cache.clear()
    icons.clear_cache()


def watch(widget, variable, callback):
    """variable.trace_add("write", ...) that is removed when the widget is
    destroyed. A bare trace outlives its widget and then fails ("invalid command
    name") the next time the variable changes — which happens whenever a screen
    rebuilds its controls around a shared variable."""
    trace_id = variable.trace_add("write", lambda *_: callback())

    def _cleanup(event):
        if event.widget is widget:
            try:
                variable.trace_remove("write", trace_id)
            except Exception:
                pass
    widget.bind("<Destroy>", _cleanup, add="+")


# ----------------------------------------------------------------- tooltip --
class Tooltip:
    """Small hover label for icon-only controls (also their accessible name)."""
    DELAY_MS = 450

    def __init__(self, widget, text):
        self.widget, self.text = widget, text
        self._job = None
        self._tip = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._hide()
        self._job = self.widget.after(self.DELAY_MS, self._show)

    def _show(self):
        if self._tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 4
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self._tip = tk.Toplevel(self.widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self._tip, text=self.text, font=FONTS["small"], bg=COLORS["bg_hover"],
                 fg=COLORS["text_primary"], padx=8, pady=4, bd=0,
                 highlightthickness=1, highlightbackground=COLORS["border"]).pack()

    def _hide(self, _e=None):
        if self._job:
            try:
                self.widget.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
        if self._tip:
            self._tip.destroy()
            self._tip = None


# ------------------------------------------------------------------ button --
class RoundedButton(tk.Canvas):
    """Button with text and/or icon. variant: "primary" | "default" | "ghost" |
    "danger" (ghost in the danger colour). Supports configure(text=, state=,
    command=, icon=). Space/Enter activate it when focused."""

    def __init__(self, parent, text="", command=None, variant="default",
                 font=None, padx=14, pady=7, radius=None, bg=None, icon=None, tooltip=None,
                 stretch=False, align="center"):
        super().__init__(parent, bg=bg or parent.cget("bg"), highlightthickness=0,
                         bd=0, takefocus=1)
        self._text, self._command, self._variant, self._icon = text, command, variant, icon
        self._stretch, self._align = stretch, align
        self._padx, self._pady = padx, pady
        self._radius = RADIUS if radius is None else min(radius, RADIUS + 2)
        self._font = font or FONTS["small_bold"]
        self._state, self._hover, self._pressed, self._focus = "normal", False, False, False
        self._photo = self._icon_photo = None
        self.bind("<Enter>", lambda _e: self._set(hover=True))
        self.bind("<Leave>", lambda _e: self._set(hover=False, pressed=False))
        self.bind("<ButtonPress-1>", lambda _e: self._set(pressed=True))
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<FocusIn>", lambda _e: self._set(focus=True))
        self.bind("<FocusOut>", lambda _e: self._set(focus=False))
        self.bind("<space>", self._activate)
        self.bind("<Return>", self._activate)
        if stretch:
            self.bind("<Configure>", self._on_stretch)
        self._relayout()
        if tooltip:
            Tooltip(self, tooltip)

    def _on_stretch(self, event):
        if event.width != self._size[0]:
            self._size = (event.width, self._size[1])
            self._draw()

    def configure(self, cnf=None, **kw):
        changed = False
        for key in ("text", "state", "command", "icon"):
            if key in kw:
                setattr(self, "_" + key, kw.pop(key))
                changed = True
        if cnf or kw:
            super().configure(cnf, **kw)
        if changed:
            self._relayout()
    config = configure

    def _colors(self):
        v, enabled = self._variant, self._state == "normal"
        if not enabled:
            if v in ("ghost", "danger"):
                return None, None, COLORS["text_muted"]
            return COLORS["bg_input"], COLORS["border"], COLORS["text_muted"]
        if v == "primary":
            fill = COLORS["accent_hover"] if (self._hover or self._pressed) else COLORS["accent"]
            return fill, None, COLORS["accent_text"]
        if v == "danger":
            return (COLORS["bg_hover"] if self._hover else None), None, COLORS["danger"]
        if v == "ghost":
            return ((COLORS["bg_hover"] if self._hover else None), None,
                    COLORS["text_primary"] if self._hover else COLORS["text_secondary"])
        return (COLORS["bg_hover"] if self._hover else COLORS["bg_input"]), COLORS["border"], COLORS["text_primary"]

    def _relayout(self):
        f = tkfont.Font(font=self._font)
        text_w = f.measure(self._text) if self._text else 0
        icon_w = 16 if self._icon else 0
        gap = 7 if (self._text and self._icon) else 0
        self._content_w = icon_w + gap + text_w
        w = self._content_w + 2 * self._padx
        h = f.metrics("linespace") + 2 * self._pady
        super().configure(height=h, cursor="hand2" if self._state == "normal" else "arrow",
                          **({} if self._stretch else {"width": w}))
        self._size = (self.winfo_width() if self._stretch and self.winfo_width() > 1 else w, h)
        self._draw()

    def _set(self, **kw):
        for k, v in kw.items():
            setattr(self, "_" + k, v)
        if self._state != "normal":
            self._hover = self._pressed = False
        self._draw()

    def _draw(self):
        self.delete("all")
        w, h = self._size
        fill, outline, fg = self._colors()
        if self._focus and self._state == "normal":
            outline = COLORS["accent"]
        if fill or outline:
            self._photo = shape(w, h, self._radius, fill, outline, 2 if self._focus else 1)
            self.create_image(0, 0, anchor="nw", image=self._photo)
        x = self._padx + 2 if self._align == "left" else (w - self._content_w) / 2
        if self._icon:
            self._icon_photo = icons.icon(self._icon, 16, fg)
            self.create_image(x, h / 2, anchor="w", image=self._icon_photo)
            x += 16 + (7 if self._text else 0)
        if self._text:
            self.create_text(x, h / 2, anchor="w", text=self._text, fill=fg, font=self._font)

    def _on_release(self, event):
        was_pressed = self._pressed
        self._set(pressed=False)
        if was_pressed and 0 <= event.x <= self._size[0] and 0 <= event.y <= self._size[1]:
            self._activate()

    def _activate(self, _e=None):
        if self._state == "normal" and self._command:
            self._command()


class IconButton(tk.Canvas):
    """Square icon-only button with a hover plate and a tooltip."""

    def __init__(self, parent, icon, command=None, tooltip=None, size=30, bg=None,
                 variant="ghost"):
        super().__init__(parent, width=size, height=size, bg=bg or parent.cget("bg"),
                         highlightthickness=0, bd=0, takefocus=1, cursor="hand2")
        self._icon, self._command, self._size, self._variant = icon, command, size, variant
        self._state, self._hover, self._focus = "normal", False, False
        self._photo = self._plate = None
        self.bind("<Enter>", lambda _e: self._set(hover=True))
        self.bind("<Leave>", lambda _e: self._set(hover=False))
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<FocusIn>", lambda _e: self._set(focus=True))
        self.bind("<FocusOut>", lambda _e: self._set(focus=False))
        self.bind("<space>", self._activate)
        self.bind("<Return>", self._activate)
        self._tooltip = Tooltip(self, tooltip) if tooltip else None
        self._draw()

    def configure(self, cnf=None, **kw):
        changed = False
        for key in ("icon", "state", "command"):
            if key in kw:
                setattr(self, "_" + key, kw.pop(key))
                changed = True
        if "tooltip" in kw:
            text = kw.pop("tooltip")
            if self._tooltip:
                self._tooltip.text = text
        if cnf or kw:
            super().configure(cnf, **kw)
        if changed:
            super().configure(cursor="hand2" if self._state == "normal" else "arrow")
            self._draw()
    config = configure

    def _set(self, **kw):
        for k, v in kw.items():
            setattr(self, "_" + k, v)
        self._draw()

    def _draw(self):
        self.delete("all")
        s, enabled = self._size, self._state == "normal"
        if enabled and self._variant == "primary":
            fill, fg = (COLORS["accent_hover"] if self._hover else COLORS["accent"]), COLORS["accent_text"]
        else:
            fill = COLORS["bg_hover"] if (enabled and self._hover) else None
            if not enabled:
                fg = COLORS["text_muted"]
            elif self._variant == "danger":
                fg = COLORS["danger"]
            else:
                fg = COLORS["text_primary"] if self._hover else COLORS["text_secondary"]
        outline = COLORS["accent"] if (self._focus and enabled) else None
        if fill or outline:
            self._plate = shape(s, s, RADIUS, fill, outline, 2)
            self.create_image(0, 0, anchor="nw", image=self._plate)
        self._photo = icons.icon(self._icon, 16, fg)
        self.create_image(s / 2, s / 2, image=self._photo)

    def _on_release(self, event):
        if 0 <= event.x <= self._size and 0 <= event.y <= self._size:
            self._activate()

    def _activate(self, _e=None):
        if self._state == "normal" and self._command:
            self._command()


# ---------------------------------------------------------------- progress --
class SlimProgress(tk.Canvas):
    """Thin pill progress bar. Colour follows the job status; supports an
    indeterminate mode (moving segment) for when no reliable percentage
    exists — it never shows an invented value."""
    HEIGHT = 6

    def __init__(self, parent, bg=None, height=None):
        self._h = height or self.HEIGHT
        super().__init__(parent, height=self._h, bg=bg or parent.cget("bg"),
                         highlightthickness=0, bd=0)
        self._value, self._status = 0.0, "queued"
        self._indeterminate, self._phase, self._job = False, 0.0, None
        self._imgs = []
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Destroy>", lambda _e: self._stop_anim())

    @staticmethod
    def _fill(status):
        return {"downloading": COLORS["accent"], "paused": COLORS["warning"],
                "error": COLORS["danger"], "completed": COLORS["success"],
                "cancelled": COLORS["text_muted"]}.get(status, COLORS["accent"])

    def set(self, value, status="downloading", indeterminate=False):
        self._value = max(0.0, min(100.0, value))
        self._status = status
        if indeterminate != self._indeterminate:
            self._indeterminate = indeterminate
            (self._start_anim if indeterminate else self._stop_anim)()
        self._draw()

    def _start_anim(self):
        if self._job is None:
            self._tick()

    def _stop_anim(self):
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
            self._job = None

    def _tick(self):
        self._phase = (self._phase + 0.025) % 2.0
        self._draw()
        self._job = self.after(40, self._tick)

    def _draw(self):
        W = self.winfo_width()
        if W < 4:
            return
        H = self._h
        self.delete("all")
        self._imgs = [shape(W, H, H / 2, COLORS["bg_input"])]
        self.create_image(0, 0, anchor="nw", image=self._imgs[0])
        color = self._fill(self._status)
        if self._indeterminate:
            seg = max(H * 2, int(W * 0.28))
            t = self._phase if self._phase <= 1 else 2 - self._phase    # bounce
            x = int((W - seg) * t)
            self._imgs.append(shape(seg, H, H / 2, color))
            self.create_image(x, 0, anchor="nw", image=self._imgs[-1])
        elif self._value > 0:
            fw = max(H, int(W * self._value / 100))
            self._imgs.append(shape(fw, H, H / 2, color))
            self.create_image(0, 0, anchor="nw", image=self._imgs[-1])


# ---------------------------------------------------------- toggle / check --
_toggle_cache = {}


def _toggle_image(on, track):
    key = (on, track)
    if key not in _toggle_cache:
        w, h = 40, 22
        img = Image.new("RGBA", (w * _SS, h * _SS), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([0, 0, w * _SS - 1, h * _SS - 1], radius=h * _SS / 2, fill=_rgba(track))
        k = (h - 6) * _SS
        x0 = (w - 3) * _SS - k if on else 3 * _SS
        d.ellipse([x0, 3 * _SS, x0 + k, 3 * _SS + k], fill=(255, 255, 255, 255))
        _toggle_cache[key] = icons.photo_from(img.resize((w, h), Image.LANCZOS))
    return _toggle_cache[key]


class ToggleSwitch(tk.Canvas):
    """Pill switch (accent when on). Bound to a tk.BooleanVar; the whole pill
    is clickable and Space toggles it when focused."""
    W, H = 40, 22

    def __init__(self, parent, variable=None, command=None, bg=None):
        super().__init__(parent, width=self.W, height=self.H, bg=bg or parent.cget("bg"),
                         highlightthickness=0, bd=0, cursor="hand2", takefocus=1)
        self.variable = variable if variable is not None else tk.BooleanVar(value=False)
        self._command = command
        self._photo = None
        self.bind("<Button-1>", self._toggle)
        self.bind("<space>", self._toggle)
        watch(self, self.variable, self._draw)
        self._draw()

    def get(self):
        return bool(self.variable.get())

    def _toggle(self, _event=None):
        self.variable.set(not self.get())
        if self._command:
            self._command()

    def _draw(self):
        self.delete("all")
        track = COLORS["accent"] if self.get() else COLORS["text_muted"]
        self._photo = _toggle_image(self.get(), track)
        self.create_image(0, 0, anchor="nw", image=self._photo)


class BigCheckbutton(tk.Frame):
    """22px rounded tick box with an optional label — drop-in for tk.Checkbutton.
    The command runs after the variable has been flipped."""

    def __init__(self, parent, text="", variable=None, command=None, font=None,
                 bg=None, fg=None, box=20, label_width=None):
        bg = bg or parent.cget("bg")
        super().__init__(parent, bg=bg)
        self.variable = variable if variable is not None else tk.BooleanVar(value=False)
        self._command, self._box = command, box
        self._canvas = tk.Canvas(self, width=box, height=box, bg=bg, highlightthickness=0,
                                 bd=0, cursor="hand2", takefocus=1)
        self._canvas.pack(side="left")
        self._imgs = []
        if text:
            kw = {"width": label_width} if label_width else {}
            lab = tk.Label(self, text=text, font=font or FONTS["body"], bg=bg,
                           fg=fg or COLORS["text_primary"], anchor="w", cursor="hand2", **kw)
            lab.pack(side="left", padx=(8, 0))
            lab.bind("<Button-1>", self._toggle)
        self._canvas.bind("<Button-1>", self._toggle)
        self._canvas.bind("<space>", self._toggle)
        watch(self, self.variable, self._draw)
        self._draw()

    def _toggle(self, _event=None):
        self.variable.set(not self.variable.get())
        if self._command:
            self._command()

    def _draw(self):
        n = self._box
        self._canvas.delete("all")
        if self.variable.get():
            self._imgs = [shape(n, n, 5, COLORS["accent"]), icons.icon("check", n - 6, COLORS["accent_text"], 2.6)]
            self._canvas.create_image(0, 0, anchor="nw", image=self._imgs[0])
            self._canvas.create_image(n / 2, n / 2, image=self._imgs[1])
        else:
            self._imgs = [shape(n, n, 5, COLORS["bg_input"], COLORS["text_muted"], 2)]
            self._canvas.create_image(0, 0, anchor="nw", image=self._imgs[0])


# ----------------------------------------------------------------- entries --
class PlaceholderEntry(tk.Entry):
    """Entry that shows a dimmed hint while empty (like a browser address bar).
    The hint is an overlay, so the bound variable only holds typed text."""

    def __init__(self, parent, textvariable, placeholder, **kw):
        super().__init__(parent, textvariable=textvariable, **kw)
        self._var = textvariable
        self._hint = tk.Label(self, text=placeholder, font=kw.get("font"),
                              bg=kw.get("bg", COLORS["bg_input"]), fg=COLORS["text_muted"],
                              anchor="w", cursor="xterm")
        self._hint.bind("<Button-1>", lambda _e: self.focus_set())
        watch(self, textvariable, self._sync)
        self._sync()

    def _sync(self):
        if self._var.get():
            self._hint.place_forget()
        else:
            self._hint.place(x=2, rely=0.5, anchor="w", relwidth=1.0, width=-4)


class EntryField(tk.Canvas):
    """Rounded text field: outline turns accent while focused. Optional leading
    icon and, with clearable=True, an x button while there is text. Use
    `.entry` for focus/selection; pack the field itself."""

    def __init__(self, parent, variable, placeholder="", font=None, icon=None,
                 clearable=False, pady=7, bg=None, width=None):
        self._font = font or FONTS["body"]
        lh = tkfont.Font(font=self._font).metrics("linespace")
        self._h = lh + 2 * pady + 2
        super().__init__(parent, height=self._h, bg=bg or parent.cget("bg"),
                         highlightthickness=0, bd=0, **({"width": width} if width else {}))
        self.variable = variable
        self._icon, self._clearable, self._focus = icon, clearable, False
        self._imgs = []
        left = 34 if icon else 12
        self.entry = PlaceholderEntry(self, variable, placeholder, font=self._font, bd=0,
                                      relief="flat", highlightthickness=0, bg=COLORS["bg_input"],
                                      fg=COLORS["text_primary"], insertbackground=COLORS["text_primary"])
        self._win = self.create_window(left, self._h / 2, window=self.entry, anchor="w")
        self._left = left
        self._clear = None
        if clearable:
            self._clear = IconButton(self, "x", command=self._on_clear, tooltip="Clear", size=22,
                                     bg=COLORS["bg_input"])
            self._clear_win = self.create_window(0, self._h / 2, window=self._clear, anchor="e")
            watch(self, variable, self._sync_clear)
        self.entry.bind("<FocusIn>", lambda _e: self._set_focus(True), add="+")
        self.entry.bind("<FocusOut>", lambda _e: self._set_focus(False), add="+")
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Button-1>", lambda _e: self.entry.focus_set())

    def _on_clear(self):
        self.variable.set("")
        self.entry.focus_set()

    def _sync_clear(self):
        if self._clear is None:
            return
        self.itemconfigure(self._clear_win, state="normal" if self.variable.get() else "hidden")

    def _set_focus(self, value):
        self._focus = value
        self._draw()

    def _draw(self):
        W = self.winfo_width()
        if W < 8:
            return
        self.delete("bg")
        outline = COLORS["accent"] if self._focus else COLORS["border"]
        self._imgs = [shape(W, self._h, RADIUS, COLORS["bg_input"], outline, 2 if self._focus else 1)]
        self.create_image(0, 0, anchor="nw", image=self._imgs[0], tags="bg")
        if self._icon:
            self._imgs.append(icons.icon(self._icon, 16, COLORS["text_muted"]))
            self.create_image(12, self._h / 2, anchor="w", image=self._imgs[-1], tags="bg")
        self.tag_lower("bg")
        right = 30 if self._clearable else 10
        self.itemconfigure(self._win, width=max(20, W - self._left - right))
        if self._clear is not None:
            self.coords(self._clear_win, W - 5, self._h / 2)
            self._sync_clear()


class SegmentedControl(tk.Canvas):
    """A few mutually exclusive choices in one rounded strip (replaces radio
    buttons). options = [(value, label), ...]; bound to a tk variable."""

    def __init__(self, parent, options, variable, command=None, bg=None, font=None, pad=14,
                 disabled=()):
        self._font = font or FONTS["small_bold"]
        f = tkfont.Font(font=self._font)
        self._choices = options
        self._widths = [f.measure(label) + 2 * pad for _, label in options]
        self._h = f.metrics("linespace") + 14
        super().__init__(parent, width=sum(self._widths) + 6, height=self._h + 6,
                         bg=bg or parent.cget("bg"), highlightthickness=0, bd=0, cursor="hand2",
                         takefocus=1)
        self.variable, self._command = variable, command
        self._disabled = set(disabled)
        self._imgs = []
        self.bind("<Button-1>", self._on_click)
        watch(self, self.variable, self._draw)
        self._draw()

    def set_disabled(self, values):
        """Choices that cannot be picked right now (shown greyed)."""
        self._disabled = set(values)
        self._draw()

    def _on_click(self, event):
        x = 3
        for (value, _), w in zip(self._choices, self._widths):
            if x <= event.x < x + w:
                if value in self._disabled:
                    return
                if self.variable.get() != value:
                    self.variable.set(value)
                    if self._command:
                        self._command()
                return
            x += w

    def _draw(self):
        self.delete("all")
        W = sum(self._widths) + 6
        self._imgs = [shape(W, self._h + 6, RADIUS + 2, COLORS["bg_input"], COLORS["border"])]
        self.create_image(0, 0, anchor="nw", image=self._imgs[0])
        x = 3
        for (value, label), w in zip(self._choices, self._widths):
            on = self.variable.get() == value
            if on:
                self._imgs.append(shape(w, self._h, RADIUS, COLORS["accent"]))
                self.create_image(x, 3, anchor="nw", image=self._imgs[-1])
            dim = value in self._disabled
            self.create_text(x + w / 2, 3 + self._h / 2, text=label, font=self._font,
                             fill=COLORS["accent_text"] if on else
                             (COLORS["text_muted"] if dim else COLORS["text_secondary"]))
            x += w


class Stepper(tk.Frame):
    """Compact - [ n ] + control for a small integer range."""

    def __init__(self, parent, variable, minimum=1, maximum=10, command=None, bg=None):
        bg = bg or parent.cget("bg")
        super().__init__(parent, bg=bg)
        self.variable, self._min, self._max, self._command = variable, minimum, maximum, command
        self._dec = IconButton(self, "minus", command=lambda: self._step(-1), tooltip="Decrease", size=28, bg=bg)
        self._dec.pack(side="left")
        self._box = tk.Canvas(self, width=46, height=28, bg=bg, highlightthickness=0, bd=0)
        self._box.pack(side="left", padx=2)
        self._inc = IconButton(self, "plus", command=lambda: self._step(1), tooltip="Increase", size=28, bg=bg)
        self._inc.pack(side="left")
        self._img = None
        watch(self, variable, self._draw)
        self._draw()

    def _step(self, delta):
        value = max(self._min, min(self._max, int(self.variable.get()) + delta))
        if value != self.variable.get():
            self.variable.set(value)
            if self._command:
                self._command()

    def _draw(self):
        self._box.delete("all")
        self._img = shape(46, 28, RADIUS, COLORS["bg_input"], COLORS["border"])
        self._box.create_image(0, 0, anchor="nw", image=self._img)
        self._box.create_text(23, 14, text=str(self.variable.get()), font=FONTS["body_bold"],
                              fill=COLORS["text_primary"])
        v = int(self.variable.get())
        self._dec.configure(state="normal" if v > self._min else "disabled")
        self._inc.configure(state="normal" if v < self._max else "disabled")


class Dropdown(tk.Canvas):
    """Select box: shows the current choice and a chevron; clicking opens a
    menu below it. `values` is a list of labels; the chosen label is stored in
    `variable` (a StringVar) and passed to `command`."""

    def __init__(self, parent, variable, values, command=None, bg=None, font=None, pady=7):
        self._font = font or FONTS["body"]
        lh = tkfont.Font(font=self._font).metrics("linespace")
        self._h = lh + 2 * pady + 2
        super().__init__(parent, height=self._h, bg=bg or parent.cget("bg"),
                         highlightthickness=0, bd=0, cursor="hand2", takefocus=1)
        self.variable, self._values, self._command = variable, list(values), command
        self._hover = self._focus = False
        self._imgs = []
        self._menu = tk.Menu(self, tearoff=0, bg=COLORS["bg_card"], fg=COLORS["text_primary"],
                             activebackground=COLORS["accent"], activeforeground=COLORS["accent_text"],
                             bd=0, relief="flat", font=FONTS["body"], activeborderwidth=0)
        for value in self._values:
            self._menu.add_command(label="  " + value, command=lambda v=value: self._choose(v))
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Button-1>", lambda _e: self._open())
        self.bind("<space>", lambda _e: self._open())
        self.bind("<Return>", lambda _e: self._open())
        self.bind("<Enter>", lambda _e: self._set(hover=True))
        self.bind("<Leave>", lambda _e: self._set(hover=False))
        self.bind("<FocusIn>", lambda _e: self._set(focus=True))
        self.bind("<FocusOut>", lambda _e: self._set(focus=False))
        watch(self, variable, self._draw)

    def _set(self, **kw):
        for k, v in kw.items():
            setattr(self, "_" + k, v)
        self._draw()

    def _open(self):
        self._menu.tk_popup(self.winfo_rootx(), self.winfo_rooty() + self._h + 2)
        self._menu.grab_release()

    def _choose(self, value):
        self.variable.set(value)
        if self._command:
            self._command(value)

    def _draw(self):
        W = self.winfo_width()
        if W < 8:
            return
        self.delete("all")
        outline = COLORS["accent"] if self._focus else COLORS["border"]
        fill = COLORS["bg_hover"] if self._hover else COLORS["bg_input"]
        self._imgs = [shape(W, self._h, RADIUS, fill, outline, 2 if self._focus else 1),
                      icons.icon("chevron-down", 16, COLORS["text_muted"])]
        self.create_image(0, 0, anchor="nw", image=self._imgs[0])
        self.create_text(12, self._h / 2, anchor="w", text=self.variable.get(), font=self._font,
                         fill=COLORS["text_primary"])
        self.create_image(W - 10, self._h / 2, anchor="e", image=self._imgs[1])


# -------------------------------------------------------------------- misc --
class Separator(tk.Frame):
    def __init__(self, parent, orient="horizontal", bg=None):
        kw = {"height": 1} if orient == "horizontal" else {"width": 1}
        super().__init__(parent, bg=bg or COLORS["border"], **kw)


def _round_rect(canvas, x1, y1, x2, y2, r, **kw):
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, **kw)


class RoundedCard(tk.Frame):
    """A rounded, bordered container (children go in `.inner`). Used sparingly —
    for dialogs and the odd grouped block, not as the default surface."""

    def __init__(self, parent, radius=10, pad=16, fill=None, outline=None):
        self._parent_bg = parent.cget("bg")
        super().__init__(parent, bg=self._parent_bg)
        px, py = pad if isinstance(pad, tuple) else (pad, pad)
        self._radius, self._px, self._py = radius, px, py
        self._fill = fill or COLORS["bg_card"]
        self._outline = outline or COLORS["border"]
        self._canvas = tk.Canvas(self, bg=self._parent_bg, highlightthickness=0, bd=0, height=2 * py)
        self._canvas.pack(fill="x")
        self.inner = tk.Frame(self._canvas, bg=self._fill)
        self._win = self._canvas.create_window(px, py, window=self.inner, anchor="nw")
        self._canvas.bind("<Configure>", self._on_canvas_resize)
        self.inner.bind("<Configure>", self._on_inner_resize)
        self._img = None

    def _on_canvas_resize(self, event):
        self._canvas.itemconfigure(self._win, width=max(1, event.width - 2 * self._px))
        self._canvas.delete("card")
        self._img = shape(event.width, event.height, self._radius, self._fill, self._outline)
        self._canvas.create_image(0, 0, anchor="nw", image=self._img, tags="card")
        self._canvas.tag_lower("card")

    def _on_inner_resize(self, event):
        wanted = event.height + 2 * self._py
        if int(float(self._canvas.cget("height"))) != wanted:
            self._canvas.configure(height=wanted)


class Section(tk.Frame):
    """A flat group: a hairline above (except the first), then its content in
    `.inner`. This is the default way to group things — no box around them."""

    def __init__(self, parent, first=False):
        bg = parent.cget("bg")
        super().__init__(parent, bg=bg)
        if not first:
            Separator(self).pack(fill="x", pady=(0, 14))
        self.inner = tk.Frame(self, bg=bg)
        self.inner.pack(fill="x", pady=(0, 6))


class SettingsGroup(tk.Frame):
    """Phone-style settings block: a small caption, then one rounded card whose
    rows are separated by hairlines. Every setting is its own row — title and
    explanation on the left, its control (switch, segmented control, stepper,
    button, dropdown) on the right; wide controls such as text fields sit below
    the title instead."""

    def __init__(self, parent, title=None, footer=None):
        bg = parent.cget("bg")
        super().__init__(parent, bg=bg)
        if title:
            tk.Label(self, text=title, font=FONTS["small_bold"], bg=bg, fg=COLORS["text_muted"],
                     anchor="w").pack(fill="x", padx=6, pady=(0, 6))
        self.card = RoundedCard(self, radius=10, pad=(1, 8))
        self.card.pack(fill="x")
        self.bg = COLORS["bg_card"]
        self._rows = 0
        if footer:
            tk.Label(self, text=footer, font=FONTS["small"], bg=bg, fg=COLORS["text_muted"],
                     anchor="w", justify="left", wraplength=540).pack(fill="x", padx=6, pady=(6, 0))

    def _new_row(self):
        inner = self.card.inner
        if self._rows:
            Separator(inner).pack(fill="x", padx=16)
        self._rows += 1
        row = tk.Frame(inner, bg=self.bg)
        row.pack(fill="x", padx=16, pady=11)
        return row

    def row(self, title, hint=None, control=None, stacked=False):
        """One setting. `control` is a callable taking the parent and returning
        the widget (it is placed for you): to the right of the text, or — with
        stacked=True — full width underneath it. Returns (row, control)."""
        row = self._new_row()
        head = tk.Frame(row, bg=self.bg)
        head.pack(fill="x")
        text = tk.Frame(head, bg=self.bg)
        text.pack(side="left", fill="x", expand=True)
        tk.Label(text, text=title, font=FONTS["body"], bg=self.bg, fg=COLORS["text_primary"],
                 anchor="w", justify="left").pack(fill="x")
        if hint:
            tk.Label(text, text=hint, font=FONTS["small"], bg=self.bg, fg=COLORS["text_muted"],
                     anchor="w", justify="left",
                     wraplength=540 if stacked else 430).pack(fill="x", pady=(2, 0))
        widget = None
        if control is not None:
            if stacked:
                widget = control(row)
                widget.pack(fill="x", pady=(9, 0))
            else:
                widget = control(head)
                widget.pack(side="right", padx=(16, 0))
        return row, widget

    def custom(self):
        """A free-form row (status text, a block of buttons ...). Returns its frame."""
        return self._new_row()


def toggle_row(parent, title, variable, hint=None, command=None, bg=None):
    """Settings row: title (+ small hint) on the left, switch on the right."""
    card_bg = bg or parent.cget("bg")
    row = tk.Frame(parent, bg=card_bg)
    row.pack(fill="x", pady=(8, 2))
    text_col = tk.Frame(row, bg=card_bg)
    text_col.pack(side="left", fill="x", expand=True)
    tk.Label(text_col, text=title, font=FONTS["body"], bg=card_bg, fg=COLORS["text_primary"],
             anchor="w", justify="left").pack(fill="x")
    if hint:
        tk.Label(text_col, text=hint, font=FONTS["small"], bg=card_bg, fg=COLORS["text_muted"],
                 anchor="w", justify="left", wraplength=520).pack(fill="x", pady=(1, 0))
    ToggleSwitch(row, variable=variable, command=command, bg=card_bg).pack(side="right", padx=(12, 0))
    return row
