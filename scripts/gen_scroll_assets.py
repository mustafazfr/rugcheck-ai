"""Generate the theme-B scroll assets (ADR-050 v2) — run: uv run python scripts/gen_scroll_assets.py

Renders, at native CSS resolution (no upscaled reference photos):
  web/static/roll.png       1200x116 RGBA — a parchment roll tube: lambertian cylinder shading,
                            paper-fiber grain, wavy organic silhouette, rolled end caps with a
                            dark core. Used for BOTH the top and the (flipped) bottom roll via
                            border-image 3-slice (caps = 78px, geometry exact by construction).
  web/static/parchment.png  512x512 RGB — seamless aged-paper tile (mirror-tiled quadrant =
                            mathematically seamless) for the scroll body.

Pillow is a local dev tool only (not a runtime dependency) — the app just serves the PNGs.
"""

from __future__ import annotations

import math
import random
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parent.parent / "src" / "solscout" / "web" / "static"
random.seed(7)

PAPER = (0xF0, 0xE3, 0xC4)


def _noise(size: tuple[int, int], sigma: float, blur: float = 0.0) -> Image.Image:
    n = Image.effect_noise(size, sigma)
    if blur:
        n = n.filter(ImageFilter.GaussianBlur(blur))
    return n


def _grain(img: Image.Image, strength: float, scale: int = 1, blur: float = 0.6) -> Image.Image:
    """Multiply paper-fiber noise into an RGB image. scale>1 stretches the noise horizontally
    (long fibers); strength 0..1 is how far from neutral gray the noise may push."""
    w, h = img.size
    n = _noise((max(2, w // scale), h), 26, blur)
    if scale > 1:
        n = n.resize((w, h), Image.BILINEAR)
    # remap noise around 128 with limited amplitude, then multiply (128 = neutral)
    n = n.point(lambda v: int(128 + (v - 128) * strength))
    n3 = Image.merge("RGB", (n, n, n))
    return ImageChops.multiply(img, n3.point(lambda v: min(255, v + 127)))


def make_parchment(side: int = 512) -> Image.Image:
    q = side // 2
    base = Image.new("RGB", (q, q), PAPER)
    base = _grain(base, 0.5, scale=1, blur=0.7)               # fine tooth
    low = _noise((q // 4, q // 4), 34, 1.5).resize((q, q), Image.BILINEAR)
    low = low.point(lambda v: int(128 + (v - 128) * 0.35))
    base = ImageChops.multiply(base, Image.merge("RGB", (low, low, low)).point(lambda v: min(255, v + 127)))
    # faint aged blotches
    bl = Image.new("L", (q, q), 0)
    d = ImageDraw.Draw(bl)
    for _ in range(7):
        x, y = random.randint(-30, q), random.randint(-30, q)
        r = random.randint(26, 70)
        d.ellipse([x, y, x + r, y + int(r * random.uniform(0.5, 1.0))], fill=random.randint(28, 52))
    bl = bl.filter(ImageFilter.GaussianBlur(26))
    tint = Image.new("RGB", (q, q), (0xCB, 0xB4, 0x86))
    base = Image.composite(ImageChops.multiply(base, tint.point(lambda v: min(255, v + 96))), base, bl)
    # mirror-tile quadrant → perfectly seamless full tile
    full = Image.new("RGB", (side, side))
    full.paste(base, (0, 0))
    full.paste(base.transpose(Image.FLIP_LEFT_RIGHT), (q, 0))
    full.paste(base.transpose(Image.FLIP_TOP_BOTTOM), (0, q))
    full.paste(base.transpose(Image.ROTATE_180), (q, q))
    return full


def _shade_row(t: float) -> float:
    """Cylinder lighting profile (t: 0 top → 1 bottom). Bright band above center, falloff to a
    deep under-curve shadow — the thing flat 2D bars don't have."""
    lam = math.sin(math.pi * min(max(t, 0.0), 1.0)) ** 1.25      # lambertian-ish
    hl = math.exp(-((t - 0.36) ** 2) / 0.03) * 0.22               # soft paper sheen (matte, not chrome)
    bottom = max(0.0, t - 0.72) * 1.35                            # rolled-under darkness
    top = max(0.0, 0.10 - t) * 1.8                                # top edge turns away
    return max(0.22, min(1.12, 0.40 + 0.62 * lam + hl - bottom - top))


def make_roll(w: int = 1200, h: int = 116, cap_w: int = 78) -> Image.Image:
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    # — shaded tube strip —
    tube_h = h - 18
    strip = Image.new("RGB", (1, tube_h))
    for r in range(tube_h):
        L = _shade_row(r / (tube_h - 1))
        strip.putpixel((0, r), tuple(min(255, int(c * L)) for c in PAPER))
    tube = strip.resize((w, tube_h), Image.BILINEAR)
    tube = _grain(tube, 0.42, scale=14, blur=0.4)                 # long horizontal fibers
    tube = _grain(tube, 0.22, scale=1, blur=0.5)                  # fine tooth
    # a few subtle wrap creases (vertical darker streaks, blurred)
    cd = ImageDraw.Draw(tube, "RGBA")
    for _ in range(26):
        x = random.randint(cap_w, w - cap_w)
        a = random.randint(8, 22)
        cd.line([(x, 0), (x + random.randint(-6, 6), tube_h)], fill=(96, 78, 52, a), width=random.randint(2, 5))
    tube = tube.filter(ImageFilter.GaussianBlur(0.4))
    # — organic wavy silhouette mask —
    mask = Image.new("L", (w, tube_h), 0)
    md = ImageDraw.Draw(mask)
    p1, p2 = random.uniform(0, 6.3), random.uniform(0, 6.3)
    top_pts = [(x, 3.5 + 1.9 * math.sin(x / 90 + p1) + 1.2 * math.sin(x / 23 + p2)) for x in range(0, w + 4, 4)]
    bot_pts = [(x, tube_h - 4 + 1.7 * math.sin(x / 76 + p2) + 1.1 * math.sin(x / 19 + p1)) for x in range(w, -4, -4)]
    md.polygon(top_pts + bot_pts, fill=255)
    img.paste(tube, (0, 9), mask)
    d = ImageDraw.Draw(img, "RGBA")
    # — end caps: the rolled core seen end-on —
    for cx0 in (4, w - cap_w + 4):
        bb = [cx0, 6, cx0 + cap_w - 8, h - 6]
        d.ellipse(bb, fill=(0xD9, 0xC7, 0x9E, 255), outline=(0x6E, 0x5A, 0x40, 255), width=2)
        rings = [(0xCD, 0xBA, 0x92), (0xC2, 0xAE, 0x85), (0xB2, 0x9D, 0x74), (0x8F, 0x7A, 0x58)]
        for i, col in enumerate(rings):
            k = 6 + i * 6
            d.ellipse([bb[0] + k, bb[1] + k * 1.35, bb[2] - k, bb[3] - k * 1.35], fill=col + (255,))
        k = 6 + len(rings) * 6
        d.ellipse([bb[0] + k, bb[1] + k * 1.35, bb[2] - k, bb[3] - k * 1.35], fill=(0x41, 0x34, 0x23, 255))
        # cap throws a soft shadow onto the tube beside it
        sh = Image.new("RGBA", (26, h), (0, 0, 0, 0))
        sd = ImageDraw.Draw(sh)
        inner = cx0 == 4
        for i in range(26):
            a = int(44 * (1 - i / 26))
            x = i if inner else 25 - i
            sd.line([(x, 10), (x, h - 12)], fill=(70, 56, 38, a))
        img.alpha_composite(sh, (cx0 + cap_w - 8 if inner else cx0 - 18, 0))
    return img


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    make_parchment().save(OUT / "parchment.png")
    make_roll().save(OUT / "roll.png")
    print("wrote", OUT / "parchment.png", "and", OUT / "roll.png")
