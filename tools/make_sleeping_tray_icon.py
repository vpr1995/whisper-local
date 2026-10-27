# make_sleeping_tray_icon.py
# Builds tray_sleeping.png (model unloaded after idle) for each platform from
# that platform's tray_idle.png: the mic faded out, plus an indigo "z" badge
# in the same corner as the processing icon's orange dot. Re-run after
# changing the idle art:  python tools/make_sleeping_tray_icon.py

from pathlib import Path

from PIL import Image, ImageDraw

ASSETS_ROOT = Path(__file__).resolve().parents[1] / "src" / "whisper_key" / "platform"
BADGE_COLOR = (88, 86, 214, 255)   # indigo, readable on light and dark bars
GLYPH_COLOR = (255, 255, 255, 255)
SUPERSAMPLE = 8                    # draw large, then downscale for smooth edges


# Fade the mic so "asleep" reads at a glance, even before the badge is noticed.
def faded(icon: Image.Image) -> Image.Image:
    grey = icon.convert("LA").convert("RGBA")
    alpha = grey.getchannel("A").point(lambda a: int(a * 0.45))
    grey.putalpha(alpha)
    return grey


# A filled circle with a white "z" in the bottom-right corner, drawn at
# SUPERSAMPLE x size and downscaled so the strokes stay crisp at 32/44 px.
def sleep_badge(size: int) -> Image.Image:
    big = size * SUPERSAMPLE
    badge = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(badge)
    diameter = int(big * 0.55)
    left = top = big - diameter
    draw.ellipse([left, top, big - 1, big - 1], fill=BADGE_COLOR)

    inset = diameter * 0.28
    x0, y0 = left + inset, top + inset
    x1, y1 = big - 1 - inset, big - 1 - inset
    stroke = max(1, int(diameter * 0.13))
    draw.line([(x0, y0), (x1, y0), (x0, y1), (x1, y1)], fill=GLYPH_COLOR,
              width=stroke, joint="curve")
    return badge.resize((size, size), Image.LANCZOS)


def build(platform_dir: Path):
    idle = Image.open(platform_dir / "assets" / "tray_idle.png").convert("RGBA")
    sleeping = faded(idle)
    sleeping.alpha_composite(sleep_badge(idle.width))
    out = platform_dir / "assets" / "tray_sleeping.png"
    sleeping.save(out)
    print(f"wrote {out.relative_to(ASSETS_ROOT.parents[2])} ({idle.width}x{idle.height})")


if __name__ == "__main__":
    for name in ("macos", "windows"):
        build(ASSETS_ROOT / name)
