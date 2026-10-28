# build_icons.py
# Regenerates every raster icon (app icon PNG, Windows .ico, macOS/Windows tray
# state PNGs, dashboard brand.svg) from the SVG sources in
# src/whisper_key/assets/logo/. Idempotent; run after editing an SVG:
#   .venv/bin/python tools/build_icons.py
# Needs `rsvg-convert` (brew install librsvg) or the `cairosvg` package, plus Pillow.

import io
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image

# --- Paths -------------------------------------------------------------------

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "src" / "whisper_key"
LOGO_DIR = PACKAGE_DIR / "assets" / "logo"
APP_ICON_PNG = PACKAGE_DIR / "assets" / "app_icon.png"
WINDOWS_ASSETS = PACKAGE_DIR / "platform" / "windows" / "assets"
MACOS_ASSETS = PACKAGE_DIR / "platform" / "macos" / "assets"
BRAND_SVG = PACKAGE_DIR / "dashboard" / "web" / "brand.svg"

# --- Sizes -------------------------------------------------------------------

APP_ICON_SIZE = 1024
# Frames at or below SMALL_ICO_MAX use the simplified mark so bars stay distinct.
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
SMALL_ICO_MAX = 32
# The 1024 canvas has a 100 px margin (macOS icon grid) around the 824 px tile;
# a Windows icon should fill its frame, so crop to the tile plus a sliver.
ICO_CROP_VIEWBOX = "92 92 840 840"
TRAY_SIZE_BY_PLATFORM = {MACOS_ASSETS: 44, WINDOWS_ASSETS: 32}
TRAY_STATES = ("idle", "recording", "processing", "sleeping")

# --- Tray palette ------------------------------------------------------------
# macOS pystray icons are NOT template images, and Windows taskbars are either
# light or dark, so the glyph carries its own graphite outline to stay readable
# on both.

MINT = "#2fd9b4"
RECORDING_RED = "#ff4a4a"
PROCESSING_AMBER = "#ffb020"
SLEEP_INDIGO = "#5856d6"
CURSOR_WHITE = "#ffffff"
OUTLINE = "#1d2230"
OUTLINE_WIDTH = 0.8          # 18-unit grid; half of it shows outside the fill
SLEEPING_OPACITY = 0.62


# --- SVG rasterising ---------------------------------------------------------

# Rasterise SVG text to an RGBA image with whichever renderer is installed.
def render_svg(svg_text: str, size: int) -> Image.Image:
    if shutil.which("rsvg-convert"):
        png_bytes = subprocess.run(
            ["rsvg-convert", "-w", str(size), "-h", str(size)],
            input=svg_text.encode(), capture_output=True, check=True).stdout
    else:
        import cairosvg  # fallback when librsvg is not installed
        png_bytes = cairosvg.svg2png(bytestring=svg_text.encode(),
                                     output_width=size, output_height=size)
    return Image.open(io.BytesIO(png_bytes)).convert("RGBA")


# --- App icon + .ico ---------------------------------------------------------

# Windows frames: simplified mark when tiny, tile-cropped full art otherwise.
def render_ico_frame(size: int, full_svg: str, small_svg: str) -> Image.Image:
    if size <= SMALL_ICO_MAX:
        return render_svg(small_svg, size)
    cropped = re.sub(r'viewBox="[^"]*"', f'viewBox="{ICO_CROP_VIEWBOX}"', full_svg, count=1)
    return render_svg(cropped, size)


def build_app_icons() -> None:
    full_svg = (LOGO_DIR / "app_icon.svg").read_text()
    small_svg = (LOGO_DIR / "app_icon_small.svg").read_text()

    render_svg(full_svg, APP_ICON_SIZE).save(APP_ICON_PNG)

    frames = [render_ico_frame(size, full_svg, small_svg) for size in ICO_SIZES]
    largest = frames[-1]
    largest.save(WINDOWS_ASSETS / "whisperkey-icon.ico", format="ICO",
                 sizes=[(f.width, f.height) for f in frames], append_images=frames[:-1])

    # Dashboard sidebar/About mark: the simplified tile, shipped as a plain SVG.
    BRAND_SVG.write_text(small_svg)


# --- Tray glyph variants -----------------------------------------------------

# Pull the bar/cursor rectangles out of tray_glyph.svg as (x, y, w, h, rx) tuples.
def read_glyph_rects() -> dict:
    root = ET.parse(LOGO_DIR / "tray_glyph.svg").getroot()
    rects_by_group = {}
    for group in root.iter("{http://www.w3.org/2000/svg}g"):
        rects_by_group[group.get("id")] = [
            tuple(float(rect.get(attr)) for attr in ("x", "y", "width", "height", "rx"))
            for rect in group.iter("{http://www.w3.org/2000/svg}rect")]
    return rects_by_group


# One shape drawn twice: a graphite outline underneath, the colour fill on top.
def outlined(shape_markup: str, fill: str) -> str:
    outline_paint = (f'fill="{OUTLINE}" stroke="{OUTLINE}" '
                     f'stroke-width="{OUTLINE_WIDTH}" stroke-linejoin="round"')
    return (shape_markup.format(paint=outline_paint)
            + shape_markup.format(paint=f'fill="{fill}"'))


def rect_markup(x, y, w, h, rx) -> str:
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" {{paint}}/>'


# Processing swaps the bars for three dots on the same centres ("thinking...").
def dot_markup(x, y, w, h, rx) -> str:
    return f'<circle cx="{x + w / 2}" cy="9" r="1.5" {{paint}}/>'


# A small indigo "z" badge, bottom-right, marking the unloaded/sleeping state.
def sleep_badge_markup() -> str:
    return (f'<circle cx="13.6" cy="13.6" r="4.4" fill="{SLEEP_INDIGO}" stroke="#fff" stroke-width="0.5"/>'
            '<path d="M11.7 11.9 H15.5 L11.7 15.3 H15.5" fill="none" stroke="#fff" '
            'stroke-width="1.1" stroke-linejoin="round" stroke-linecap="round"/>')


def tray_state_svg(state: str, glyph_rects: dict) -> str:
    bar_color = {"idle": MINT, "recording": RECORDING_RED,
                 "processing": PROCESSING_AMBER, "sleeping": MINT}[state]
    bar_shape = dot_markup if state == "processing" else rect_markup
    body = "".join(outlined(bar_shape(*r), bar_color) for r in glyph_rects["bars"])
    body += "".join(outlined(rect_markup(*r), CURSOR_WHITE) for r in glyph_rects["cursor"])

    # Slight inset so the outline is not clipped at the canvas edge.
    inset = f'<g transform="translate(9 9) scale(0.95) translate(-9 -9)">{body}</g>'
    if state == "sleeping":
        inset = f'<g opacity="{SLEEPING_OPACITY}">{inset}</g>{sleep_badge_markup()}'
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 18 18">{inset}</svg>'


def build_tray_icons() -> None:
    glyph_rects = read_glyph_rects()
    for assets_dir, size in TRAY_SIZE_BY_PLATFORM.items():
        for state in TRAY_STATES:
            render_svg(tray_state_svg(state, glyph_rects), size).save(assets_dir / f"tray_{state}.png")


if __name__ == "__main__":
    build_app_icons()
    build_tray_icons()
    print("Icons regenerated from", LOGO_DIR)
