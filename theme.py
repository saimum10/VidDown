"""
theme.py
Centralized color palette, fonts, and small style constants for VidDown.
Keeping all of this in one place means the dark/blue look stays consistent
across every page and is easy to retouch later.

Theme switching: every page does `from theme import COLORS` and reads it
at widget-construction time — Tkinter widgets don't re-read a dict on
their own, so an already-built widget can't be re-colored just by
changing values here. load_theme() therefore mutates the *same* COLORS
dict object in place (.clear()/.update(), never `COLORS = ...`) so it
must run before the very first widget is built — see main_window.py,
where it's called as the first thing in __init__, before super().__init__().
Switching afterwards (e.g. from Settings) takes effect on next launch,
same as this app's other restart-required settings.
"""
import sys
import tkinter.font as tkfont

# ---------------------------------------------------------------- colors --
# Design tokens. Surfaces go back -> front:
#   bg_dark (content) < bg_panel (toolbar / navigation) < bg_card (list rows,
#   dialogs) < bg_input (fields) < bg_hover.
# The two palettes are designed separately rather than inverted: the dark one
# is a cool graphite, the light one a warm off-white. Both share one indigo
# accent, and the same restrained status colours.
DARK_THEME = {
    "bg_dark": "#15171c",      # main content background
    "bg_panel": "#1a1d23",     # toolbar, navigation, status bar
    "bg_card": "#1d2027",      # list rows, dialogs, grouped surfaces
    "bg_input": "#23272f",     # fields, dropdowns, progress tracks
    "bg_hover": "#2a2f38",     # hover feedback
    "bg_selected": "#232945",  # selected list row / active navigation item
    "border": "#2b3039",       # separators and field outlines

    "accent": "#5a67ee",       # the one accent colour
    "accent_hover": "#6b77f2",
    "accent_dim": "#262b44",   # tinted fill (badges, disabled accent)
    "accent_text": "#ffffff",  # text/icons drawn on top of accent

    "success": "#46b985",
    "warning": "#e0a83a",
    "danger": "#ec6a62",

    "text_primary": "#e9ebf1",
    "text_secondary": "#a0a7b6",
    "text_muted": "#7d8494",
}

LIGHT_THEME = {
    "bg_dark": "#f4f4f2",
    "bg_panel": "#fbfbfa",
    "bg_card": "#ffffff",
    "bg_input": "#efeeeb",
    "bg_hover": "#e7e6e2",
    "bg_selected": "#e8ebfd",
    "border": "#dcdbd6",

    "accent": "#4a56d8",
    "accent_hover": "#3f4ac4",
    "accent_dim": "#dfe2fb",
    "accent_text": "#ffffff",

    "success": "#1f8a5b",
    "warning": "#9a6212",
    "danger": "#c53a3a",

    "text_primary": "#1c1e24",
    "text_secondary": "#4d5260",
    "text_muted": "#6f7384",
}

THEMES = {"dark": DARK_THEME, "light": LIGHT_THEME}

# Populated by load_theme() before any widget is built — starts as the
# dark palette so importing this module alone (e.g. in a quick script)
# still gives sane values.
COLORS = dict(DARK_THEME)


def load_theme(name):
    """Repopulate COLORS in place with the requested palette ('dark' or
    'light', defaulting to dark for anything else/unset). Must run
    before the first widget reads COLORS."""
    COLORS.clear()
    COLORS.update(THEMES.get(name, DARK_THEME))

# ---------------------------------------------------------------- fonts ---
FONTS = {}

_FONT_PREFERENCE = ["Inter", "Cantarell", "Noto Sans", "Ubuntu", "DejaVu Sans", "Segoe UI", "Helvetica"]
_MONO_PREFERENCE = ["Ubuntu Mono", "DejaVu Sans Mono", "Consolas", "Courier New"]

# On Windows prefer the native UI fonts (Linux order is unchanged).
if sys.platform == "win32":
    _FONT_PREFERENCE = ["Segoe UI", "Inter"] + [f for f in _FONT_PREFERENCE if f not in ("Segoe UI", "Inter")]
    _MONO_PREFERENCE = ["Consolas", "Courier New"]


def _pick_family(preferred, fallback):
    try:
        available = set(tkfont.families())
    except Exception:
        return fallback
    for name in preferred:
        if name in available:
            return name
    return fallback


def init_fonts(root):
    """
    Must be called once, right after the Tk() root window is created
    (tkfont.families() needs a live Tk instance to query the system).
    """
    family = _pick_family(_FONT_PREFERENCE, "TkDefaultFont")
    mono = _pick_family(_MONO_PREFERENCE, "TkFixedFont")

    FONTS.update({
        "logo": (family, 14, "bold"),
        "logo_sub": (family, 10, "bold"),
        "page_title": (family, 14, "bold"),
        "section": (family, 11, "bold"),
        "body": (family, 10),
        "body_bold": (family, 10, "bold"),
        "small": (family, 9),
        "small_bold": (family, 9, "bold"),
        "tiny": (family, 8),
        "button_lg": (family, 11, "bold"),
        "nav": (family, 10),
        "mono": (mono, 9),
    })


# --------------------------------------------------------------- spacing --
PAD_X = 18
PAD_Y_TOP = (14, 0)
PAD_Y_LAST = (14, 20)

# One spacing scale and one corner radius, used everywhere instead of
# one-off numbers.
SP = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24}
RADIUS = 6
