#!/usr/bin/env python3
"""Generate desktop app icons for Windows and macOS builds."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
BUILD_DIR = ROOT / "build"


def _font(size: int) -> ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def _rounded_gradient(size: int) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    radius = int(size * 0.22)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size, size), radius=radius, fill=255)

    gradient = Image.new("RGBA", (size, size))
    pixels = gradient.load()
    for y in range(size):
        for x in range(size):
            t = (x * 0.55 + y * 0.45) / size
            r = int(14 + 12 * t)
            g = int(103 + 74 * t)
            b = int(143 + 62 * t)
            pixels[x, y] = (r, g, b, 255)
    image.alpha_composite(gradient)
    image.putalpha(mask)

    inset = int(size * 0.035)
    draw.rounded_rectangle(
        (inset, inset, size - inset, size - inset),
        radius=radius,
        outline=(255, 255, 255, 72),
        width=max(4, size // 80),
    )
    return image


def _draw_icon(size: int = 1024) -> Image.Image:
    image = _rounded_gradient(size)
    draw = ImageDraw.Draw(image)
    white = (255, 255, 255, 245)
    pale = (222, 247, 255, 210)
    accent = (61, 220, 151, 255)
    dark = (8, 55, 82, 210)

    # Barcode panel.
    panel = (
        int(size * 0.18),
        int(size * 0.20),
        int(size * 0.82),
        int(size * 0.58),
    )
    draw.rounded_rectangle(panel, radius=int(size * 0.07), fill=(255, 255, 255, 34), outline=pale, width=max(6, size // 80))
    x = panel[0] + int(size * 0.07)
    widths = [12, 7, 18, 8, 23, 7, 14, 20, 8, 15, 24, 7, 12]
    gap = int(size * 0.022)
    for index, width in enumerate(widths):
        bar_width = max(4, int(size * width / 1024))
        y1 = panel[1] + int(size * (0.08 + (index % 3) * 0.015))
        y2 = panel[3] - int(size * (0.06 + (index % 2) * 0.02))
        draw.rounded_rectangle((x, y1, x + bar_width, y2), radius=max(2, size // 160), fill=(9, 81, 112, 235))
        x += bar_width + gap

    # Scanner line and check mark.
    line_y = int(size * 0.64)
    draw.rounded_rectangle((int(size * 0.20), line_y, int(size * 0.80), line_y + int(size * 0.035)), radius=int(size * 0.018), fill=accent)
    draw.line(
        (
            int(size * 0.63),
            int(size * 0.69),
            int(size * 0.70),
            int(size * 0.76),
            int(size * 0.82),
            int(size * 0.61),
        ),
        fill=white,
        width=max(16, size // 42),
        joint="curve",
    )

    # CRM text.
    font = _font(int(size * 0.18))
    label = "CRM"
    bbox = draw.textbbox((0, 0), label, font=font)
    text_w = bbox[2] - bbox[0]
    text_x = (size - text_w) // 2
    text_y = int(size * 0.72)
    draw.text((text_x + int(size * 0.012), text_y + int(size * 0.012)), label, font=font, fill=dark)
    draw.text((text_x, text_y), label, font=font, fill=white)
    return image


def _save_pngs(base: Image.Image, iconset: Path) -> None:
    iconset.mkdir(parents=True, exist_ok=True)
    specs = [
        (16, "icon_16x16.png"),
        (32, "icon_16x16@2x.png"),
        (32, "icon_32x32.png"),
        (64, "icon_32x32@2x.png"),
        (128, "icon_128x128.png"),
        (256, "icon_128x128@2x.png"),
        (256, "icon_256x256.png"),
        (512, "icon_256x256@2x.png"),
        (512, "icon_512x512.png"),
        (1024, "icon_512x512@2x.png"),
    ]
    for px, filename in specs:
        base.resize((px, px), Image.Resampling.LANCZOS).save(iconset / filename)


def main() -> int:
    BUILD_DIR.mkdir(exist_ok=True)
    base = _draw_icon()
    base.save(BUILD_DIR / "app_icon.png")
    base.save(
        BUILD_DIR / "app_icon.ico",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )

    iconset = BUILD_DIR / "app_icon.iconset"
    if iconset.exists():
        shutil.rmtree(iconset)
    _save_pngs(base, iconset)
    icns_path = BUILD_DIR / "app_icon.icns"
    if sys.platform == "darwin" and shutil.which("iconutil"):
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(icns_path)], check=True)
    else:
        try:
            base.save(icns_path, format="ICNS")
        except Exception:
            pass
    print(f"Generated icons in {BUILD_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
