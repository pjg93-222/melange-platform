"""Writes melange-admin.svg and melange-admin.ico (16/24/32/48/64/128/256) from one set of shapes, so the two never drift.

    python ops/desktop/server/icons/make_melange_admin_icon.py

Flat vector from the dashboard tokens: plum rounded square, three cream bars, a short amber marker left of the top bar
(mockup 09 without its halo). Needs Pillow for the .ico only; the .svg is plain text. Small sizes use a bolder cut of the
same shapes so the bars stay visible at 16 px.
"""
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUM, CREAM, AMBER = '#342649', '#F7EDE4', '#ECA91C'
SIZES = (16, 24, 32, 48, 64, 128, 256)
# (x, y, width, height, corner radius, fill) in a 256 x 256 box, content centred on (128, 128).
TILE = (8, 8, 240, 240, 56, PLUM)
SHAPES = ((68, 80, 13, 30, 6.5, AMBER), (88, 85, 100, 20, 9, CREAM), (68, 120, 120, 20, 9, CREAM), (68, 155, 120, 20, 9, CREAM))


def bold(shape, k=1.3):
    x, y, w, h, r, fill = shape
    return (128 + (x - 128) * k, 128 + (y - 128) * k, w * k, h * k, r * k, fill)


def shapes(small):
    tile = (2, 2, 252, 252, 60, PLUM) if small else TILE
    return (tile,) + tuple(bold(s) if small else s for s in SHAPES)


def number(v):
    return ('%.2f' % v).rstrip('0').rstrip('.')


def svg():
    rects = ''.join(f'<rect x="{number(x)}" y="{number(y)}" width="{number(w)}" height="{number(h)}" rx="{number(r)}" fill="{fill}"/>'
                    for x, y, w, h, r, fill in shapes(False))
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="256" height="256" role="img" aria-label="Melange Admin">{rects}</svg>\n'


def render(size, small):
    from PIL import Image, ImageDraw
    scale = 8
    image = Image.new('RGBA', (256 * scale, 256 * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    for x, y, w, h, r, fill in shapes(small):
        draw.rounded_rectangle((x * scale, y * scale, (x + w) * scale - 1, (y + h) * scale - 1), radius=r * scale, fill=fill)
    return image.resize((size, size), Image.LANCZOS)


def main():
    (HERE / 'melange-admin.svg').write_text(svg(), encoding='utf-8', newline='\n')
    frames = [render(size, size <= 32) for size in SIZES]
    frames[-1].save(HERE / 'melange-admin.ico', format='ICO', sizes=[(s, s) for s in SIZES], append_images=frames[:-1])


if __name__ == '__main__':
    main()
