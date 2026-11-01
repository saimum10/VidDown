"""Build the VidDown master icon from the uploaded picture.

The picture shows the icon as a slightly TALL tile (449x479 px incl. a 3-D rim)
photographed on a grey backdrop, so it cannot be cut out as it is. Instead the
logo is lifted off the tile and put on a fresh, perfectly square, rounded tile
of the same off-white gradient (sides blended with a feathered mask, so no seam).
"""
from PIL import Image, ImageDraw, ImageFilter
import numpy as np

SRC = "/mnt/user-data/uploads/1000167548.jpg"
import os, sys
S = 1024                       # master size
FRAC = float(os.environ.get('LOGO_FRAC', '0.64'))      # logo height / tile height
OUT = os.environ.get('OUT', 'master_1024.png')
RADIUS = 0.2237                # corner radius / size (the usual app-icon squircle-ish ratio)

src = Image.open(SRC).convert("RGB")
a = np.asarray(src).astype(float)

# ---- 1. logo patch: logo bbox + margins, taken from inside the tile
LX0, LX1, LY0, LY1 = 555, 851, 159, 582          # measured logo box (absolute)
ML, MT, MB = 34, 10, 26                           # left/right, top, bottom margins
px0, px1, py0, py1 = LX0 - ML, LX1 + ML + 1, LY0 - MT, LY1 + MB + 1
# the patch must stay inside the tile interior (479..927 x 142..620 minus the rim)
assert px0 >= 482 and px1 <= 924 and py0 >= 145 and py1 <= 612, (px0, px1, py0, py1)
patch = a[py0:py1, px0:px1]                       # float RGB
ph, pw, _ = patch.shape

# the tile's own background (it has a soft top glow and a vertical fall-off): fit a smooth
# 2-D polynomial to the pixels that are NOT logo and subtract it
sat = patch.max(axis=2) - patch.min(axis=2)
raw = (sat > 9)
yy0, xx0 = np.mgrid[0:ph, 0:pw]
raw &= (xx0 >= 14) & (xx0 < pw - 14) & (yy0 < ph - 14)        # the logo never touches these bands
logo = Image.fromarray((raw * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(15))
is_bg = np.asarray(logo) == 0
yy, xx = np.mgrid[0:ph, 0:pw]
u, v = xx / pw * 2 - 1, yy / ph * 2 - 1
def feats(u, v):
    return np.stack([u**i * v**j for i in range(4) for j in range(4) if i + j <= 3], axis=-1)
F = feats(u, v)
bg_fit = np.zeros_like(patch)
for c in range(3):
    coef, *_ = np.linalg.lstsq(F[is_bg], patch[:, :, c][is_bg], rcond=None)
    bg_fit[:, :, c] = F @ coef
resid = (patch - bg_fit)[is_bg]
print("bg fit residual (rms per channel):", np.sqrt((resid ** 2).mean(axis=0)).round(2))

# what the logo adds on top of that background (anti-aliased edges included); JPEG
# noise under ~3 levels is cored away so the new tile stays perfectly clean, and the
# layer fades to nothing at the patch border
dev = patch - bg_fit
dev = np.sign(dev) * np.maximum(np.abs(dev) - 3.0, 0.0)
fade = np.minimum(np.minimum(yy, ph - 1 - yy), np.minimum(xx, pw - 1 - xx)) / 6.0
dev *= np.clip(fade, 0, 1)[:, :, None]
# keep only what is near the logo (JPEG specks elsewhere would otherwise survive)
keep = np.asarray(logo.filter(ImageFilter.GaussianBlur(5))).astype(float) / 255.0
dev *= keep[:, :, None]

# ---- 2. the new tile: vertical off-white gradient measured from the original
#         (253 at the top -> 237 at the bottom, slightly warm)
top, bot = np.array([253, 253, 252.0]), np.array([236, 237, 231.0])
t = np.linspace(0, 1, S)[:, None, None] ** 1.15
tile = np.repeat(top + (bot - top) * t, S, axis=1)

# ---- 3. scale the deviation layer so the logo is ~64 % of the tile height; centre the LOGO
logo_h = LY1 - LY0 + 1
scale = (FRAC * S) / logo_h
nw, nh = int(round(pw * scale)), int(round(ph * scale))
big = np.stack([np.asarray(Image.fromarray(dev[:, :, c].astype(np.float32), "F")
                           .resize((nw, nh), Image.LANCZOS)) for c in range(3)], axis=2)
ox = int(round(S / 2 - ((LX0 + LX1 + 1) / 2 - px0) * scale))
oy = int(round(S / 2 - ((LY0 + LY1 + 1) / 2 - py0) * scale))
tile[oy:oy + nh, ox:ox + nw] += big
# a whisper of noise dithers the gradient so 8-bit banding cannot show on big screens
tile += np.random.default_rng(7).uniform(-0.5, 0.5, tile.shape)
base = Image.fromarray(np.clip(np.rint(tile), 0, 255).astype(np.uint8), "RGB")

# ---- 4. rounded-square alpha (supersampled for a clean edge)
SS = 4
m = Image.new("L", (S * SS, S * SS), 0)
ImageDraw.Draw(m).rounded_rectangle([0, 0, S * SS - 1, S * SS - 1], radius=int(RADIUS * S * SS), fill=255)
m = m.resize((S, S), Image.LANCZOS)
icon = base.convert("RGBA")
icon.putalpha(m)
icon.save(OUT)
print("master:", icon.size, "layer at", (ox, oy), "size", (nw, nh), "scale %.3f" % scale)
