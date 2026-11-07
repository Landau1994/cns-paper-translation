#!/usr/bin/env python3
"""Rebuild the three-column, four-row neutral-template overview.

Cell, Nature and Science occupy the first row. One representative page from
each of twelve article and guideline templates fills one panel.
Guideline variants are not counted as new journals. No paper content or newly
generated illustrations are introduced.
"""
from pathlib import Path
import argparse
import json
from PIL import Image, ImageDraw, ImageFont


PANELS = [
    ('cell', 1, 'Cell', 'Opening page'),
    ('nature', 1, 'Nature', 'Opening page'),
    ('science', 1, 'Science', 'Research summary'),
    ('immunity', 1, 'Immunity', 'Opening page'),
    ('nature-reviews-cardiology', 1, 'Nature Reviews Cardiology', 'Abstract and contents'),
    ('nature-biotechnology', 1, 'Nature Biotechnology', 'Brief Communication opening'),
    ('nature-methods', 1, 'Nature Methods', 'Perspective / Analysis opening'),
    ('circulation', 1, 'Circulation', 'Original Research opening'),
    ('circulation-research', 1, 'Circulation Research', 'Review opening'),
    ('european-heart-journal', 1, 'European Heart Journal', 'Translational Research opening'),
    ('circulation-guideline', 1, 'Circulation guideline', 'Clinical Practice Guideline opening'),
    ('european-heart-journal-guideline', 1, 'EHJ / ESC guideline', 'Task-force guideline opening'),
]


def font(size, bold=False):
    names = ['arialbd.ttf', 'DejaVuSans-Bold.ttf'] if bold else ['arial.ttf', 'DejaVuSans.ttf']
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    raise RuntimeError('Install Arial or DejaVu Sans to build the overview.')


def build(root, output):
    previews = root / 'assets' / 'previews'
    margin, gap, card_w, card_h, top = 48, 30, 548, 740, 172
    width = 2 * margin + 3 * card_w + 2 * gap
    height = top + 4 * card_h + 3 * gap + 110
    canvas = Image.new('RGB', (width, height), '#edf0f3')
    draw = ImageDraw.Draw(canvas)
    draw.text((margin, 34), 'CNS Paper Translation', font=font(44, True), fill='#1b2733')
    draw.text((margin, 94), '12 neutral templates / 12 previews / 3 columns x 4 rows', font=font(25), fill='#526273')
    page_boxes = []
    for index, (key, page, name, kind) in enumerate(PANELS):
        x = margin + index % 3 * (card_w + gap)
        y = top + index // 3 * (card_h + gap)
        draw.rounded_rectangle((x, y, x + card_w, y + card_h), radius=12, fill='white', outline='#d2d9e1', width=2)
        name_font = font(29, True)
        while draw.textbbox((0, 0), name, font=name_font)[2] > card_w - 40:
            name_font = font(name_font.size - 1, True)
        draw.text((x + 20, y + 17), name, font=name_font, fill='#1b2733')
        draw.text((x + 20, y + 58), kind, font=font(20), fill='#5b6b7b')
        source = previews / f'{key}-{page}.png'
        with Image.open(source) as original:
            im = original.convert('RGB')
        im.thumbnail((card_w - 36, card_h - 116), Image.Resampling.LANCZOS)
        px, py = x + (card_w - im.width) // 2, y + 96 + (card_h - 116 - im.height) // 2
        canvas.paste(im, (px, py))
        draw.rectangle((px - 1, py - 1, px + im.width, py + im.height), outline='#d7dce3', width=1)
        assert x < px and y < py and px + im.width < x + card_w and py + im.height < y + card_h
        page_boxes.append({'row': index // 3 + 1, 'column': index % 3 + 1, 'template': key, 'page': page, 'label': name, 'kind': kind})
    draw.text((margin, height - 77), 'Cell / Nature / Science first; journal extensions follow.', font=font(23), fill='#33465a')
    draw.text((margin, height - 41), 'Original neutral examples; not official publisher templates.', font=font(21), fill='#667585')
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, optimize=True)
    return {'size': canvas.size, 'rows': 4, 'columns': 3, 'template_count': 12, 'panels': page_boxes}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = build(args.root, args.output or args.root / 'assets/previews/overview.png')
    print(json.dumps(result, indent=2))
