"""One-off: turn a high-res TCL logo PNG into HA brand assets.

Produces square icons (1:1) and wide logos (plus white dark_* variants) in
custom_components/tcl_lyon/brand/, per the home-assistant/brands size rules.
Not part of the integration runtime; needs Pillow (`pip install Pillow`).

Source: the TCL network logo "Tcl-2024.svg" from Wikimedia Commons, released
under CC0 (public domain). Re-render and regenerate with:

    curl -L "https://commons.wikimedia.org/wiki/Special:FilePath/Tcl-2024.svg?width=1024" \\
        -o scripts/_tcl-hires.png
    python scripts/make_brand.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

SRC = Path(sys.argv[1] if len(sys.argv) > 1 else "scripts/_tcl-hires.png")
OUT = Path("custom_components/tcl_lyon/brand")
ICON_PAD = 0.94  # fraction of the square the artwork fills (small breathing room)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    img = Image.open(SRC).convert("RGBA")

    # Report background: fully-transparent corner => transparent logo.
    corner = img.getpixel((0, 0))
    transparent = corner[3] == 0
    print(f"source {img.size}, corner={corner}, transparent_bg={transparent}")

    bbox = img.getbbox()  # tight crop around non-transparent (or non-zero) pixels
    art = img.crop(bbox) if bbox else img
    w, h = art.size
    print(f"trimmed to {art.size}")

    def icon(size: int) -> Image.Image:
        scale = (size * ICON_PAD) / max(w, h)
        nw, nh = round(w * scale), round(h * scale)
        resized = art.resize((nw, nh), Image.LANCZOS)
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        canvas.paste(resized, ((size - nw) // 2, (size - nh) // 2), resized)
        return canvas

    def logo(short_side: int) -> Image.Image:
        scale = short_side / h
        return art.resize((round(w * scale), short_side), Image.LANCZOS)

    def whiten(im: Image.Image) -> Image.Image:
        # Recolor the monochrome mark to white for dark-theme variants, keeping
        # the antialiased alpha so edges stay smooth.
        out = Image.new("RGBA", im.size, (255, 255, 255, 255))
        out.putalpha(im.getchannel("A"))
        return out

    targets = {
        "icon.png": icon(256),
        "icon@2x.png": icon(512),
        "logo.png": logo(256),
        "logo@2x.png": logo(512),
    }
    targets.update({f"dark_{name}": whiten(im) for name, im in dict(targets).items()})
    for name, im in targets.items():
        path = OUT / name
        im.save(path, "PNG", optimize=True)
        print(f"wrote {path} {im.size}")


if __name__ == "__main__":
    main()
