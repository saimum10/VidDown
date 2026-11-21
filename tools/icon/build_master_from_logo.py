#!/usr/bin/env python3
"""Make the two master icons (tools/icon/master_1024.png, master_small.png) from the
cut-out logo tools/icon/logo_source.png (transparent background, no tile).

    python3 tools/icon/build_master_from_logo.py

The logo is centred on a square, transparent 1024x1024 canvas. master_small.png has
the logo a little larger, for the tiny sizes (16-32 px) where margins waste pixels.
Then run  python3 tools/make_icons.py  to write every size.
"""
import os

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
S = 1024
FRAC_FULL = 0.92        # logo height / canvas height
FRAC_SMALL = 0.98


def premultiplied_resize(img, size):
    """LANCZOS resize without dark/light fringes at the transparent edge."""
    a = np.asarray(img.convert("RGBA")).astype(np.float64)
    alpha = a[..., 3:4] / 255.0
    pm = Image.fromarray(np.clip(a[..., :3] * alpha, 0, 255).astype(np.uint8), "RGB").resize(size, Image.LANCZOS)
    al = img.getchannel("A").resize(size, Image.LANCZOS)
    pm = np.asarray(pm).astype(np.float64)
    af = np.asarray(al).astype(np.float64)[..., None] / 255.0
    rgb = np.where(af > 0, pm / np.maximum(af, 1e-6), 0)
    out = Image.fromarray(np.clip(np.rint(rgb), 0, 255).astype(np.uint8), "RGB").convert("RGBA")
    out.putalpha(al)
    return out


def build(frac, out_name):
    logo = Image.open(os.path.join(HERE, "logo_source.png")).convert("RGBA")
    box = logo.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
    logo = logo.crop(box)                                    # tight to the artwork
    scale = min(frac * S / logo.height, 0.94 * S / logo.width)
    size = (round(logo.width * scale), round(logo.height * scale))
    art = premultiplied_resize(logo, size)
    canvas = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    canvas.alpha_composite(art, ((S - size[0]) // 2, (S - size[1]) // 2))
    canvas.save(os.path.join(HERE, out_name), optimize=True)
    print(f"{out_name}: logo {size[0]}x{size[1]} on {S}x{S}")


if __name__ == "__main__":
    build(FRAC_FULL, "master_1024.png")
    build(FRAC_SMALL, "master_small.png")
