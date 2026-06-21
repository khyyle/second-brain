"""Generate the menu-bar template glyph from the app icon.

The app icon is a white node-graph on a dark rounded square. The menu bar
wants only that glyph as an alpha cutout -- a template image macOS tints to
match the bar -- trimmed to its bounds so it fills the bar height. Re-run
after changing AppIcon.png:

    uv run python gui/SecondBrainBar/Resources/generate_menubar_icon.py
"""

from pathlib import Path

from PIL import Image

SOURCE = Path(__file__).parent / "AppIcon.png"
OUTPUT = Path(__file__).parent / "MenuBarIcon.png"

# Luminance below LOW is background (fully transparent); above HIGH is glyph
# (fully opaque). The narrow ramp between keeps edges anti-aliased rather
# than jagged.
LOW, HIGH = 45, 120
PAD_RATIO = 0.03  # breathing room around the glyph, as a fraction of its size
TARGET_HEIGHT = 256  # output height in px; downscaled from the source


def _alpha_from_luminance(value: int) -> int:
    if value <= LOW:
        return 0
    if value >= HIGH:
        return 255
    return round((value - LOW) / (HIGH - LOW) * 255)


def main() -> None:
    luminance = Image.open(SOURCE).convert("L")
    alpha = luminance.point(_alpha_from_luminance)

    bounds = alpha.getbbox()
    if bounds is None:
        raise SystemExit("no glyph found in source image")

    left, top, right, bottom = bounds
    pad = round(max(right - left, bottom - top) * PAD_RATIO)
    alpha = alpha.crop((
        max(0, left - pad),
        max(0, top - pad),
        min(alpha.width, right + pad),
        min(alpha.height, bottom + pad),
    ))

    # Template images are keyed on alpha; the colour is ignored, so fill black.
    glyph = Image.new("RGBA", alpha.size, (0, 0, 0, 0))
    glyph.putalpha(alpha)

    scale = TARGET_HEIGHT / glyph.height
    glyph = glyph.resize((round(glyph.width * scale), TARGET_HEIGHT), Image.LANCZOS)
    glyph.save(OUTPUT)
    print(f"wrote {OUTPUT} ({glyph.width}x{glyph.height})")


if __name__ == "__main__":
    main()
