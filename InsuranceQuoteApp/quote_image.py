"""Render a calculated quote as the PNG that gets sent on WhatsApp.

The layout deliberately copies the Excel sheet these quotes used to be sent
from - same row order, same yellow input cells, same green final amount - so a
customer comparing an old quote with a new one sees the same document.

Everything is drawn at SCALE times the nominal size and downsampled at the end.
WhatsApp re-compresses images, and text drawn at 1x turns to mush; drawing at 3x
and shrinking gives the strokes enough weight to survive that.
"""

import io
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BASE_DIR = Path(__file__).resolve().parent

SCALE = 3

# The sheet's palette, sampled from the workbook.
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
YELLOW = (255, 255, 0)
BLUE = (0, 176, 240)
GREEN = (0, 176, 80)
GRID = (0, 0, 0)

# Nominal (1x) geometry. Three columns: label, the rate input, the amount.
LABEL_W, RATE_W, AMOUNT_W = 468, 128, 148
PAD = 6

ROW_H = {
        "spacer": 14,
        "band": 20,
        "value": 26,
        "input": 26,
        "subtotal": 26,
        "title": 40,
        "header": 26,
        "total": 40,
        "offer": 36,
        "final": 34,
        "contact": 26,
}

FONT_SIZE = {
        "value": 15,
        "input": 15,
        "subtotal": 15,
        "title": 26,
        "header": 15,
        "total": 26,
        "offer": 23,
        "final": 21,
        "contact": 15,
}

BOLD_STYLES = {"subtotal", "title", "header", "total", "offer", "final", "contact"}

# Fill colour for the whole row; None means white.
ROW_FILL = {
        "band": BLUE,
        "title": YELLOW,
        "offer": YELLOW,
        "final": GREEN,
        "contact": YELLOW,
}

# Fonts differ between the Windows dev machine and the cPanel host, so try a
# list rather than hard-coding one path. Bundling a font under fonts/ overrides
# both and is the only way to guarantee the two render identically.
FONT_CANDIDATES = {
        False: (
                "fonts/DejaVuSans.ttf",
                "C:/Windows/Fonts/arial.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/usr/share/fonts/dejavu/DejaVuSans.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        ),
        True: (
                "fonts/DejaVuSans-Bold.ttf",
                "C:/Windows/Fonts/arialbd.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ),
}


class FontsUnavailable(RuntimeError):
        """No scalable font was found.

        Pillow's built-in bitmap font is one fixed small size, so falling back
        to it would produce an unreadable quote rather than a slightly uglier
        one - better to fail loudly and let the caller send text instead."""


def _font_path(bold):
        for candidate in FONT_CANDIDATES[bold]:
                path = candidate if os.path.isabs(candidate) else str(BASE_DIR / candidate)
                if os.path.exists(path):
                        return path
        return None


def load_font(size, bold=False):
        path = _font_path(bold)
        if path is None:
                raise FontsUnavailable(
                        "No TrueType font found. Install DejaVu fonts on the server "
                        "or drop DejaVuSans.ttf and DejaVuSans-Bold.ttf into fonts/."
                )
        return ImageFont.truetype(path, size * SCALE)


def fonts_available():
        """Whether a PNG can be rendered here, for callers that need a fallback."""
        return _font_path(False) is not None and _font_path(True) is not None


def _amount(value):
        """Plain digits, as the sheet shows them - no separators, so there is
        no Indian/Western grouping ambiguity for the customer to misread."""
        return "" if value is None else str(value)


def _draw_row(draw, box, label, rate, amount, style):
        """Fill one row, rule its cell borders and lay out its three columns."""
        left, top, right, bottom = box
        fill = ROW_FILL.get(style, WHITE)
        draw.rectangle([left, top, right, bottom], fill=fill)

        if style in ("spacer", "band"):
                draw.rectangle([left, top, right, bottom], outline=GRID, width=SCALE)
                return

        font = load_font(FONT_SIZE[style], bold=style in BOLD_STYLES)
        rate_left = right - (RATE_W + AMOUNT_W) * SCALE
        amount_left = right - AMOUNT_W * SCALE

        # The rate column is an input box on the sheet, painted yellow unless
        # the row already has a fill of its own.
        if style == "input" and fill is WHITE:
                draw.rectangle([rate_left, top, amount_left, bottom], fill=YELLOW)

        for edge in (left, rate_left, amount_left):
                draw.line([edge, top, edge, bottom], fill=GRID, width=SCALE)
        draw.rectangle([left, top, right, bottom], outline=GRID, width=SCALE)

        middle = (top + bottom) // 2
        if label:
                draw.text((left + PAD * SCALE, middle), label,
                          font=font, fill=BLACK, anchor="lm")
        if rate:
                draw.text((amount_left - PAD * SCALE, middle), str(rate),
                          font=font, fill=BLACK, anchor="rm")
        if amount is not None:
                draw.text((right - PAD * SCALE, middle), _amount(amount),
                          font=font, fill=BLACK, anchor="rm")


def render(breakdown, contact=""):
        """Draw the quote and return a Pillow Image.

        `contact` is the agent line along the bottom, e.g.
        "CONTACT:S.KARTHIK -9176011369  9840856988"."""
        i = breakdown.inputs
        rows = breakdown.rows()

        # Header (vehicle number / date), insurer title, the quote, contact.
        plan = [("header", "", i.vehicle_number, i.quote_date)]
        plan.append(("title", i.insurer.upper(), None, None))
        plan.extend((row.style, row.label, row.rate, row.amount) for row in rows)
        if contact:
                plan.append(("contact", contact, None, None))

        width = (LABEL_W + RATE_W + AMOUNT_W) * SCALE
        height = sum(ROW_H[style] for style, *_ in plan) * SCALE

        image = Image.new("RGB", (width + SCALE, height + SCALE), WHITE)
        draw = ImageDraw.Draw(image)

        y = 0
        for style, label, rate, amount in plan:
                row_height = ROW_H[style] * SCALE
                if style == "title":
                        # The insurer name runs the full width, no cell rules.
                        draw.rectangle([0, y, width, y + row_height], fill=YELLOW, outline=GRID,
                                       width=SCALE)
                        draw.text((PAD * SCALE, y + row_height // 2), label,
                                  font=load_font(FONT_SIZE["title"], bold=True),
                                  fill=BLACK, anchor="lm")
                elif style == "contact":
                        draw.rectangle([0, y, width, y + row_height], fill=YELLOW, outline=GRID,
                                       width=SCALE)
                        draw.text((PAD * SCALE, y + row_height // 2), label,
                                  font=load_font(FONT_SIZE["contact"], bold=True),
                                  fill=BLACK, anchor="lm")
                else:
                        _draw_row(draw, (0, y, width, y + row_height), label, rate, amount, style)
                y += row_height

        return image.resize((width // SCALE, height // SCALE), Image.LANCZOS)


def render_png_bytes(breakdown, contact=""):
        """The quote as PNG bytes, ready to upload to the WhatsApp media API."""
        buffer = io.BytesIO()
        render(breakdown, contact).save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()


def save_png(breakdown, path, contact=""):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(render_png_bytes(breakdown, contact))
        return path
