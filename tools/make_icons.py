#!/usr/bin/env python3
"""Regenerate every VidDown icon from the two master PNGs in tools/icon/.

    python3 tools/make_icons.py

Writes (all relative to the repository root):
  resources/icon.png                          window / taskbar / About / toolbar logo (512)
  icons/hicolor/<N>x<N>/apps/viddown.png      Linux menu + launcher icons (16 ... 512)
  icons/pixmaps/viddown.png                   legacy fallback (64)
  windows/viddown.ico                         Windows installer + exe (16 ... 256)
  browser_extension/icons/icon{16,48,128}.png toolbar / extensions page icons

master_1024.png is the full icon; master_small.png is the same icon with a bolder,
larger logo, used for the tiny sizes where the fine detail would otherwise blur.
tools/icon/build_master_from_picture.py is how the masters were made from the
original picture (kept for reference; it needs that picture).
"""
import io
import os
import struct

import numpy as np
from PIL import Image, ImageFilter, ImageOps

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def with_rim(img, width=0.0075, strength=0.20):
    """A hairline inner rim in a warm grey. The tile is nearly white, so on a light
    desktop or a light browser toolbar it would otherwise have no visible edge."""
    size = img.width
    px = max(1, int(round(width * size)))
    alpha = img.getchannel("A")
    # shrink the shape by `px`; pad with transparent pixels first, otherwise the
    # filter treats the picture border as "opaque" and the straight edges get no rim
    pad = px + 2
    inner = ImageOps.expand(alpha, border=pad, fill=0).filter(ImageFilter.MinFilter(2 * px + 1))
    inner = inner.crop((pad, pad, pad + size, pad + size))
    ring = np.asarray(alpha).astype(np.float64) - np.asarray(inner).astype(np.float64)
    ring = np.clip(ring, 0, 255) / 255.0 * strength
    ring = np.asarray(Image.fromarray((ring * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(px / 3.0))) / 255.0
    a = np.asarray(img).astype(np.float64)
    rim_col = np.array([86.0, 74.0, 60.0])
    a[..., :3] = a[..., :3] * (1 - ring[..., None]) + rim_col * ring[..., None]
    return Image.fromarray(np.clip(np.rint(a), 0, 255).astype(np.uint8), "RGBA")


FULL = with_rim(Image.open(os.path.join(ROOT, "tools", "icon", "master_1024.png")).convert("RGBA"))
SMALL = with_rim(Image.open(os.path.join(ROOT, "tools", "icon", "master_small.png")).convert("RGBA"), width=0.0075, strength=0.28)


def render(size):
    """The icon at `size` px: premultiplied-alpha LANCZOS (no fringes on the rounded
    corners) plus a little sharpening that grows as the icon shrinks."""
    master = SMALL if size <= 32 else FULL
    sharpen = 60 if size <= 16 else 50 if size <= 24 else 40 if size <= 32 else 25 if size <= 48 else 15 if size <= 64 else 0
    a = np.asarray(master).astype(np.float64)
    alpha = a[..., 3:4] / 255.0
    pm = Image.fromarray(np.clip(a[..., :3] * alpha, 0, 255).astype(np.uint8), "RGB")
    pm = np.asarray(pm.resize((size, size), Image.LANCZOS)).astype(np.float64)
    al = master.getchannel("A").resize((size, size), Image.LANCZOS)
    af = np.asarray(al).astype(np.float64)[..., None] / 255.0
    rgb = np.where(af > 0, pm / np.maximum(af, 1e-6), 0)
    out = Image.fromarray(np.clip(np.rint(rgb), 0, 255).astype(np.uint8), "RGB")
    if sharpen:
        out = out.filter(ImageFilter.UnsharpMask(radius=0.6, percent=sharpen, threshold=1))
    out = out.convert("RGBA")
    out.putalpha(al)
    return out


def save_png(img, rel):
    path = os.path.join(ROOT, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path, optimize=True)
    print(f"  {rel:50s} {img.width}x{img.height}")


def save_ico(sizes, rel):
    """A multi-size .ico with one PNG-compressed frame per size, each rendered for
    that size (Pillow would instead shrink one picture for all of them)."""
    frames = []
    for s in sizes:
        buf = io.BytesIO()
        render(s).save(buf, "PNG", optimize=True)
        frames.append((s, buf.getvalue()))
    head = struct.pack("<HHH", 0, 1, len(frames))
    offset = 6 + 16 * len(frames)
    entries, blobs = b"", b""
    for s, data in frames:
        entries += struct.pack("<BBBBHHII", 0 if s >= 256 else s, 0 if s >= 256 else s, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    path = os.path.join(ROOT, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(head + entries + blobs)
    print(f"  {rel:50s} {','.join(map(str, sizes))}")


def main():
    print("Writing icons:")
    save_png(render(512), "resources/icon.png")
    for s in (16, 24, 32, 48, 64, 96, 128, 256, 512):
        save_png(render(s), f"icons/hicolor/{s}x{s}/apps/viddown.png")
    save_png(render(64), "icons/pixmaps/viddown.png")
    for s in (16, 48, 128):
        save_png(render(s), f"browser_extension/icons/icon{s}.png")
    save_ico((16, 24, 32, 48, 64, 128, 256), "windows/viddown.ico")


if __name__ == "__main__":
    main()
