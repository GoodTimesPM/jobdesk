"""Draw the JobDesk icon and cut it to .ico and .png.

Icon 02 out of the 24-candidate gallery: a results table with one row lit.
Chosen because it is the actual app -- a list of postings where one is the
match -- rather than a metaphor for it, and because three bars survive 16px
where a radar sweep turns to mush.

The drawing lives here rather than in an image file so the icon can be
recut at any size without hunting for a source. Same reason the tray app
renders its strip from code: a screenshot is a dead end.

    py scripts/make_icon.py

Geometry is written in the gallery's 64x64 coordinate space and scaled, so
these numbers can be compared against the SVG in the artifact line for line.
Everything is drawn at 8x and downsampled, because Pillow has no antialiased
rounded rectangle and a 1.75px bar at 16px needs one.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]

BG = (0xED, 0xF1, 0xF0, 0xFF)      # paper
FG = (0x5A, 0x6E, 0x6B)            # the unmatched rows, at 45%
ACCENT = (0x0B, 0x6E, 0x6E, 0xFF)  # the one that matched

SS = 8                             # supersample factor
ICO_SIZES = (16, 32, 48, 256)


def draw(size: int) -> Image.Image:
    s = size * SS
    k = s / 64                     # gallery units -> pixels
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def box(x, y, w, h, r, fill):
        d.rounded_rectangle([x * k, y * k, (x + w) * k, (y + h) * k],
                            radius=r * k, fill=fill)

    box(0, 0, 64, 64, 14, BG)
    box(12, 17, 40, 7, 2, FG + (115,))     # .45 of 255
    box(12, 28, 40, 8, 2, ACCENT)
    box(12, 40, 40, 7, 2, FG + (115,))
    return img.resize((size, size), Image.LANCZOS)


def main() -> int:
    ico = ROOT / "jobdesk.ico"
    draw(256).save(ico, sizes=[(n, n) for n in ICO_SIZES])

    png = ROOT / "docs" / "jobdesk-icon.png"
    draw(256).save(png)

    print(f"{ico.relative_to(ROOT)}  {', '.join(str(n) for n in ICO_SIZES)}")
    print(f"{png.relative_to(ROOT)}  256")
    return 0


if __name__ == "__main__":
    sys.exit(main())
