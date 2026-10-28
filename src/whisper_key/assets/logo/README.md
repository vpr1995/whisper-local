# Logo sources

SVGs here are the source of truth for every icon in the app.

- `app_icon.svg`: full app icon (1024 canvas, 824 px tile). Becomes `assets/app_icon.png` and the large `.ico` frames.
- `app_icon_small.svg`: simplified mark for 16-32 px `.ico` frames; also copied verbatim to `dashboard/web/brand.svg`.
- `tray_glyph.svg`: monochrome glyph; `tools/build_icons.py` restyles it into the idle/recording/processing/sleeping tray PNGs for macOS (44 px) and Windows (32 px).

Regenerate all PNGs, the Windows `.ico` and `brand.svg` (needs `rsvg-convert` or `cairosvg`, plus Pillow):

    .venv/bin/python tools/build_icons.py
