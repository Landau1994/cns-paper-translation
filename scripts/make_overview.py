#!/usr/bin/env python3
"""Rebuild the selected nine-journal, three-column, three-row overview.

Rows are Cell / Nature / Science; NEJM / The Lancet / JAMA; and
The BMJ / Circulation / European Heart Journal. The other eight journals,
article/review variants, guidelines and interior detail pages remain available
in the individual template previews. All seventeen journals are retained.
No paper content or newly generated illustrations are introduced.
"""
from pathlib import Path
import argparse
import json
from PIL import Image, ImageDraw, ImageFont


PANELS = [
    ('cell', 1, 'Cell', 'Opening page'),
    ('nature', 1, 'Nature', 'Opening page'),
    ('science', 1, 'Science', 'Research summary'),
    ('nejm', 1, 'NEJM', 'Original Article opening'),
    ('lancet', 1, 'The Lancet', 'Therapeutic Review opening'),
    ('jama', 1, 'JAMA', 'Original Investigation opening'),
    ('bmj', 1, 'The BMJ', 'Research opening'),
    ('circulation', 1, 'Circulation', 'Original Research opening'),
    ('european-heart-journal', 1, 'European Heart Journal', 'Translational Research opening'),
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
    rows = (len(PANELS) + 2) // 3
    selected_count = len({p[0] for p in PANELS})
    assert selected_count == len(PANELS), 'Show each selected journal once.'
    profiles = json.loads((root / 'assets/templates/profiles.json').read_text(encoding='utf-8'))['profiles']
    journal_count = len(profiles) - 2  # Two guideline variants share journal names.
    height = top + rows * card_h + (rows - 1) * gap + 110
    canvas = Image.new('RGB', (width, height), '#edf0f3')
    draw = ImageDraw.Draw(canvas)
    draw.text((margin, 34), 'CNS Paper Translation', font=font(44, True), fill='#1b2733')
    draw.text((margin, 94), f'{selected_count} selected journals / one opening page each / 3 columns x {rows} rows', font=font(25), fill='#526273')
    page_boxes = []
    for index, (key, page, name, kind) in enumerate(PANELS):
        row, column = divmod(index, 3)
        x = margin + column * (card_w + gap)
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
        page_boxes.append({'row': row + 1, 'column': column + 1, 'template': key, 'page': page, 'label': name, 'kind': kind})
    draw.text((margin, height - 77), f'All {journal_count} journals and {len(profiles)} templates: see individual previews for full coverage.', font=font(23), fill='#33465a')
    draw.text((margin, height - 41), 'Original neutral examples; not official publisher templates.', font=font(21), fill='#667585')
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, optimize=True)
    return {'size': canvas.size, 'rows': rows, 'columns': 3,
            'journal_count': journal_count, 'template_count': len(profiles),
            'represented_journal_count': selected_count,
            'represented_template_count': selected_count, 'detail_panels': 0,
            'guideline_variants': 2, 'panels': page_boxes}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = build(args.root, args.output or args.root / 'assets/previews/overview.png')
    print(json.dumps(result, indent=2))
