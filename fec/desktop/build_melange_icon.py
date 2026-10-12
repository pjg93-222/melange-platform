"""Export the existing Melange glyph as the Windows launcher icon."""
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parent
CANVAS = 1024
BACKGROUND = "#342649"
GLYPH = "#EECD5C"


def build():
    image = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((32, 32, 992, 992), radius=190, fill=BACKGROUND)

    # Exact path from the dashboard's 128 x 208 Melange SVG symbol.
    scale = 3.78
    x_offset, y_offset = 270, 120
    for points in (
        [(108, 20), (64, 64), (28, 104), (64, 144), (20, 188)],
        [(64, 64), (100, 104), (64, 144)],
    ):
        rendered = [(x_offset + x * scale, y_offset + y * scale) for x, y in points]
        draw.line(rendered, fill=GLYPH, width=72, joint="curve")

    preview = ROOT / "melange-icon.png"
    icon = ROOT / "melange.ico"
    image.resize((512, 512), Image.Resampling.LANCZOS).save(preview)
    image.save(icon, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return icon, preview


if __name__ == "__main__":
    for path in build():
        print(path)
