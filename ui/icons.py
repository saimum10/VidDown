"""
ui/icons.py
One consistent icon set: 24x24 outline icons (round caps and joins, same
stroke weight) drawn with Pillow at 8x and scaled down, so edges are
anti-aliased — Tk's own canvas lines are not. Icons are tinted with whatever
colour the caller passes (always a theme token) and cached per size/colour.

    img = icon("download", 16, COLORS["text_secondary"])   # -> PhotoImage

Keep a reference to the returned PhotoImage (or let IconLabel/IconButton do it).
"""
import base64
import io
import math
import tkinter as tk

from PIL import Image, ImageDraw

_SS = 8                      # supersampling factor
_cache = {}


def photo_from(img):
    """PIL image -> Tk PhotoImage via PNG data. Tk 8.6 reads PNG (with alpha)
    natively, so this avoids PIL.ImageTk, which Debian ships as a separate
    package (python3-pil.imagetk)."""
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return tk.PhotoImage(data=base64.b64encode(buf.getvalue()))


class _Pen:
    def __init__(self, size, color, stroke):
        self.s = size * _SS / 24.0
        self.img = Image.new("RGBA", (size * _SS, size * _SS), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.img)
        self.c = color
        self.w = max(1, round(stroke * self.s))
        self.r = self.w / 2.0

    def _p(self, pt):
        return (pt[0] * self.s, pt[1] * self.s)

    def dot(self, x, y, radius=None):
        x, y = self._p((x, y))
        r = self.r if radius is None else radius * self.s
        self.d.ellipse([x - r, y - r, x + r, y + r], fill=self.c)

    def line(self, *pts, closed=False):
        pts = [self._p(p) for p in pts]
        if closed:
            pts = pts + [pts[0]]
        for a, b in zip(pts, pts[1:]):
            self.d.line([a, b], fill=self.c, width=self.w)
        for p in pts:                                   # round joins / caps
            self.d.ellipse([p[0] - self.r, p[1] - self.r, p[0] + self.r, p[1] + self.r], fill=self.c)

    def circle(self, cx, cy, r, fill=False):
        cx, cy = self._p((cx, cy))
        r *= self.s
        if fill:
            self.d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=self.c)
        else:
            self.d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=self.c, width=self.w)

    def arc(self, cx, cy, r, start, end):
        cx, cy = self._p((cx, cy))
        r *= self.s
        self.d.arc([cx - r, cy - r, cx + r, cy + r], start, end, fill=self.c, width=self.w)
        for ang in (start, end):
            x = cx + r * math.cos(math.radians(ang))
            y = cy + r * math.sin(math.radians(ang))
            self.d.ellipse([x - self.r, y - self.r, x + self.r, y + self.r], fill=self.c)

    def rrect(self, x0, y0, x1, y1, radius, fill=False):
        a, b = self._p((x0, y0)), self._p((x1, y1))
        if fill:
            self.d.rounded_rectangle([a, b], radius=radius * self.s, fill=self.c)
        else:
            self.d.rounded_rectangle([a, b], radius=radius * self.s, outline=self.c, width=self.w)

    def poly(self, *pts):
        self.d.polygon([self._p(p) for p in pts], fill=self.c)


def _arrow_head(p, tip, direction, size=4.6, spread=32):
    """Filled arrow head with its point at `tip`, facing `direction`."""
    dx, dy = direction
    n = math.hypot(dx, dy) or 1
    dx, dy = dx / n, dy / n
    pts = [tip]
    for sign in (1, -1):
        a = math.radians(180 + sign * spread)
        rx = dx * math.cos(a) - dy * math.sin(a)
        ry = dx * math.sin(a) + dy * math.cos(a)
        pts.append((tip[0] + rx * size, tip[1] + ry * size))
    p.poly(*pts)
    p.line(*pts, closed=True)


# ----------------------------------------------------------------- icons ---
def _plus(p):        p.line((12, 5), (12, 19)); p.line((5, 12), (19, 12))
def _minus(p):       p.line((5, 12), (19, 12))
def _x(p):           p.line((6, 6), (18, 18)); p.line((18, 6), (6, 18))
def _check(p):       p.line((5, 12.5), (10, 17.5), (19, 7))
def _play(p):        p.poly((8, 5), (19, 12), (8, 19)); p.line((8, 5), (19, 12), (8, 19), closed=True)
def _pause(p):       p.rrect(6, 5, 10, 19, 1.2, fill=True); p.rrect(14, 5, 18, 19, 1.2, fill=True)
def _list(p):        [p.line((4, y), (20, y)) for y in (7, 12, 17)]
def _clock(p):       p.circle(12, 12, 8.5); p.line((12, 7), (12, 12), (15.5, 14))
def _more(p):        [p.dot(x, 12, 1.5) for x in (5.5, 12, 18.5)]
def _chev_down(p):   p.line((6, 9.5), (12, 15.5), (18, 9.5))
def _chev_right(p):  p.line((9.5, 6), (15.5, 12), (9.5, 18))
def _search(p):      p.circle(10.5, 10.5, 6); p.line((15, 15), (20, 20))
def _info(p):        p.circle(12, 12, 8.5); p.line((12, 11), (12, 16.5)); p.dot(12, 7.8, 1.1)
def _alert(p):       p.circle(12, 12, 8.5); p.line((12, 7.5), (12, 13)); p.dot(12, 16.4, 1.1)
def _check_circle(p): p.circle(12, 12, 8.5); p.line((8, 12.3), (11, 15.3), (16.2, 9.2))
def _power(p):       p.arc(12, 13, 7.5, -50, 230); p.line((12, 4.5), (12, 12))
def _copy(p):        p.rrect(9, 9, 20, 20, 2.2); p.line((15, 9), (15, 6), (6, 6), (4, 8), (4, 15), (6, 15), (9, 15))
def _link(p):
    p.line((10, 14), (14, 10))
    p.line((11.5, 7.5), (13, 6), (17, 6), (19, 8), (19, 12), (17.5, 13.5))
    p.line((12.5, 16.5), (11, 18), (7, 18), (5, 16), (5, 12), (6.5, 10.5))
def _trash(p):
    p.line((4, 7), (20, 7)); p.line((9, 7), (9, 4.5), (15, 4.5), (15, 7))
    p.line((6, 7), (7, 19.5), (8, 20), (16, 20), (17, 19.5), (18, 7))
    p.line((10.2, 11), (10.2, 16)); p.line((13.8, 11), (13.8, 16))
def _folder(p):      p.line((3, 7), (3, 19), (21, 19), (21, 9.5), (11.5, 9.5), (9.5, 6.5), (3, 6.5), closed=True)
def _file(p):
    p.line((6, 3.5), (13.5, 3.5), (19, 9), (19, 20.5), (6, 20.5), closed=True)
    p.line((13.5, 3.5), (13.5, 9), (19, 9))
def _file_text(p):
    _file(p); p.line((9, 13), (16, 13)); p.line((9, 16.5), (16, 16.5))
def _video(p):
    p.rrect(3.5, 5.5, 20.5, 18.5, 2.5); p.line((10, 9.3), (15, 12), (10, 14.7), closed=True)
def _music(p):
    p.circle(8, 17.5, 2.8); p.line((10.8, 17.5), (10.8, 5.5), (19, 4)); p.line((19, 4), (19, 14))
    p.circle(16.2, 15, 2.8)
def _archive(p):
    p.rrect(4, 4.5, 20, 9.5, 1.5); p.line((5.5, 9.5), (5.5, 19.5), (18.5, 19.5), (18.5, 9.5)); p.line((10, 13), (14, 13))
def _download(p):
    p.line((12, 4), (12, 15)); p.line((7.5, 10.8), (12, 15.3), (16.5, 10.8))
    p.line((4.5, 16.5), (4.5, 20), (19.5, 20), (19.5, 16.5))
def _retry(p):
    p.arc(12, 12, 8, -50, 250)
    cx, cy = 12 + 8 * math.cos(math.radians(250)), 12 + 8 * math.sin(math.radians(250))
    _arrow_head(p, (cx, cy), (-math.sin(math.radians(250)), math.cos(math.radians(250))))
def _gear(p):
    p.circle(12, 12, 3)
    p.circle(12, 12, 7.2)
    for k in range(8):
        a = math.radians(k * 45)
        p.line((12 + 7.2 * math.cos(a), 12 + 7.2 * math.sin(a)), (12 + 10 * math.cos(a), 12 + 10 * math.sin(a)))
def _sliders(p):
    for y, x in ((7, 9), (12, 15), (17, 8)):
        p.line((4, y), (20, y)); p.circle(x, y, 2.3, fill=True)
def _globe(p):
    p.circle(12, 12, 8.5); p.line((3.5, 12), (20.5, 12))
    p.arc(12, 12, 8.5, 270, 90)  # right half outline reinforcement
    p.line((12, 3.5), (9.5, 7.5), (8.8, 12), (9.5, 16.5), (12, 20.5))
    p.line((12, 3.5), (14.5, 7.5), (15.2, 12), (14.5, 16.5), (12, 20.5))
def _cookie(p):
    p.circle(12, 12, 8.5); p.dot(9, 10, 1.1); p.dot(14.5, 9, 1.1); p.dot(15, 14.5, 1.1); p.dot(10, 15.5, 1.1)
def _puzzle(p):
    p.line((5, 8), (5, 19), (16, 19), (16, 16)); p.line((5, 8), (9, 8))
    p.line((9, 8), (9, 6), (10.5, 4.5), (12.5, 4.5), (14, 6), (14, 8), (19, 8), (19, 13), (21, 13.5))


_DRAW = {
    "minus": _minus, "plus": _plus, "x": _x, "check": _check, "play": _play, "pause": _pause, "list": _list,
    "clock": _clock, "more": _more, "chevron-down": _chev_down, "chevron-right": _chev_right,
    "search": _search, "info": _info, "alert": _alert, "check-circle": _check_circle,
    "power": _power, "copy": _copy, "link": _link, "trash": _trash, "folder": _folder,
    "file": _file, "file-text": _file_text, "video": _video, "music": _music, "archive": _archive,
    "download": _download, "retry": _retry, "settings": _gear, "sliders": _sliders,
    "globe": _globe, "cookie": _cookie, "puzzle": _puzzle,
}


def icon(name, size=16, color="#ffffff", stroke=1.9):
    """Anti-aliased icon as a Tk PhotoImage (cached)."""
    key = (name, size, color, stroke)
    cached = _cache.get(key)
    if cached is not None:
        return cached
    pen = _Pen(size, color, stroke)
    _DRAW[name](pen)
    img = pen.img.resize((size, size), Image.LANCZOS)
    photo = photo_from(img)
    _cache[key] = photo
    return photo


def file_icon_name(filename):
    """Which icon suits a file, judged by its extension."""
    ext = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    if ext in ("mp4", "mkv", "webm", "mov", "avi", "flv", "m4v", "ts"):
        return "video"
    if ext in ("mp3", "m4a", "opus", "ogg", "flac", "wav", "aac"):
        return "music"
    if ext in ("zip", "rar", "7z", "gz", "xz", "tar"):
        return "archive"
    return "file"


def clear_cache():
    """Drop cached images (call when the theme changes)."""
    _cache.clear()
