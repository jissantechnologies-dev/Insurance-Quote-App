"""Checks on the rendered quote PNG.

Pixel-perfect comparison against a stored image would break on every font
difference between the dev machine and the server, so these tests assert the
things that actually matter: the file is a valid PNG, the signature colours are
where they belong, the totals are legible in it, and it is small enough to send.

Run with:  py test_quote_image.py
"""

import io
import unittest

from PIL import Image

import quote_image
import quotes
from test_quotes import tn28bm0972

CONTACT = "CONTACT:S.KARTHIK -9176011369   9840856988"


@unittest.skipUnless(quote_image.fonts_available(), "no TrueType font on this machine")
class Render(unittest.TestCase):
        @classmethod
        def setUpClass(cls):
                cls.breakdown = quotes.calculate(tn28bm0972())
                cls.image = quote_image.render(cls.breakdown, contact=CONTACT)

        def test_is_a_png_of_the_nominal_width(self):
                data = quote_image.render_png_bytes(self.breakdown, contact=CONTACT)
                self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
                reopened = Image.open(io.BytesIO(data))
                self.assertEqual(reopened.format, "PNG")
                expected = quote_image.LABEL_W + quote_image.RATE_W + quote_image.AMOUNT_W
                self.assertEqual(reopened.width, expected)

        def test_small_enough_for_whatsapp(self):
                """The media endpoint caps images at 5 MB."""
                data = quote_image.render_png_bytes(self.breakdown, contact=CONTACT)
                self.assertLess(len(data), 5 * 1024 * 1024)

        def test_signature_colours_are_present(self):
                colours = {colour for _, colour in self.image.convert("RGB").getcolors(1 << 16)}
                for expected in (quote_image.YELLOW, quote_image.BLUE, quote_image.GREEN):
                        self.assertIn(expected, colours, f"{expected} missing from the render")

        def test_final_amount_row_is_green(self):
                """The bottom row above the contact line is the green Final
                Amount band. Sampled in the empty rate column, which no glyph
                reaches, so the test catches a layout shift and not a font."""
                contact_h = quote_image.ROW_H["contact"]
                final_h = quote_image.ROW_H["final"]
                y = self.image.height - contact_h - final_h // 2
                x = self.image.width - quote_image.AMOUNT_W - quote_image.RATE_W // 2
                self.assertEqual(self.image.convert("RGB").getpixel((x, y)), quote_image.GREEN)

        def test_height_tracks_the_row_plan(self):
                rows = self.breakdown.rows()
                expected = (
                        quote_image.ROW_H["header"]
                        + quote_image.ROW_H["title"]
                        + sum(quote_image.ROW_H[row.style] for row in rows)
                        + quote_image.ROW_H["contact"]
                )
                self.assertEqual(self.image.height, expected)

        def test_contact_line_is_optional(self):
                without = quote_image.render(self.breakdown)
                self.assertEqual(
                        self.image.height - without.height, quote_image.ROW_H["contact"]
                )


class Fonts(unittest.TestCase):
        def test_missing_fonts_raise_rather_than_render_unreadably(self):
                original = quote_image.FONT_CANDIDATES
                quote_image.FONT_CANDIDATES = {False: (), True: ()}
                try:
                        self.assertFalse(quote_image.fonts_available())
                        with self.assertRaises(quote_image.FontsUnavailable):
                                quote_image.load_font(15)
                finally:
                        quote_image.FONT_CANDIDATES = original


if __name__ == "__main__":
        unittest.main()
