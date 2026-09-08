"""Check the calculation against a real quote worked out in the Excel sheet.

The fixture is the TATA AIG quote for TN28BM0972 dated 01.09.2026. Every
expected number below is read straight off that sheet, so a failure here means
the engine has drifted from what was being sent to customers by hand.

Run with:  py test_quotes.py
"""

import unittest
from decimal import Decimal

import quotes


def tn28bm0972():
        return quotes.QuoteInputs(
                vehicle_number="TN28BM0972",
                insurer="TATA AIG GENERAL INSURANCE CO LTD",
                quote_date="01.09.2026",
                idv=3600000,
                base_premium_rate="1.680",
                carrying_capacity=550,
                od_discount_percent=95,
                imt23_percent=15,
                ncb_percent=20,
                third_party=12192,
                pa_owner_driver=0,
                paid_driver=50,
                pa_unnamed_units=55,
                pa_unnamed_amount=40975,
                cash_back=33615,
        )


class SheetFixture(unittest.TestCase):
        def setUp(self):
                self.result = quotes.calculate(tn28bm0972())

        def assertAmount(self, actual, expected):
                self.assertEqual(quotes.money(actual), expected)

        def test_own_damage_chain(self):
                r = self.result
                self.assertAmount(r.base_premium, 60480)
                self.assertAmount(r.gross_od_before_discount, 61030)
                self.assertAmount(r.od_discount, 57979)
                self.assertAmount(r.net_od, 3052)
                self.assertAmount(r.imt23, 458)
                self.assertAmount(r.gross_od_after_imt23, 3509)
                self.assertAmount(r.ncb, 702)
                self.assertAmount(r.total_od, 2807)

        def test_liability_and_totals(self):
                r = self.result
                self.assertAmount(r.total_liability, 53217)
                self.assertAmount(r.gross_premium, 56024)
                self.assertAmount(r.gst, 10084)
                self.assertAmount(r.total_premium, 66109)
                self.assertAmount(r.final_amount, 32494)

        def test_precision_is_carried_between_steps(self):
                """Rounding each step instead would give 3051 here, not 3051.5,
                and the total would come out a rupee light."""
                self.assertEqual(self.result.net_od, Decimal("3051.5"))
                self.assertEqual(self.result.total_premium, Decimal("66108.7684"))

        def test_rows_match_the_sheet_layout(self):
                rows = self.result.rows()
                self.assertEqual(
                        (rows[0].label, rows[0].rate, rows[0].amount, rows[0].style),
                        ("Insured Declared Value (IDV)", None, 3600000, "value"),
                )
                self.assertEqual(
                        (rows[2].label, rows[2].rate, rows[2].amount, rows[2].style),
                        ("Base Premium", "1.680", 60480, "input"),
                )
                self.assertEqual(
                        (rows[-1].label, rows[-1].amount, rows[-1].style),
                        ("Final Amount", 32494, "final"),
                )
                self.assertEqual(
                        (rows[-2].label, rows[-2].amount, rows[-2].style),
                        ("Cash Back Offer", 33615, "offer"),
                )

        def test_every_row_has_a_style_the_renderer_knows(self):
                import quote_image

                for row in self.result.rows():
                        self.assertIn(row.style, quote_image.ROW_H)
                        self.assertIn(row.style, quote_image.FONT_SIZE | {"spacer": 0, "band": 0})


class InputForms(unittest.TestCase):
        def test_pa_unnamed_from_units_and_rate(self):
                """The sheet shows 55 units against 40975, i.e. 745 apiece."""
                data = tn28bm0972()
                data.pa_unnamed_amount = None
                data.pa_unnamed_rate = 745
                self.assertEqual(quotes.calculate(data).pa_unnamed, Decimal(40975))

        def test_cash_back_as_a_percentage(self):
                data = tn28bm0972()
                data.cash_back = None
                data.cash_back_percent = 50
                result = quotes.calculate(data)
                self.assertEqual(quotes.money(result.cash_back), 33054)
                self.assertEqual(quotes.money(result.final_amount), 33054)

        def test_no_cash_back_leaves_the_total_intact(self):
                data = tn28bm0972()
                data.cash_back = None
                result = quotes.calculate(data)
                self.assertEqual(result.cash_back, Decimal(0))
                self.assertEqual(result.final_amount, result.total_premium)

        def test_blank_form_values_are_treated_as_zero(self):
                data = quotes.QuoteInputs(idv="", base_premium_rate=None, third_party="")
                result = quotes.calculate(data)
                self.assertEqual(result.total_premium, Decimal(0))

        def test_strings_from_a_form_are_exact(self):
                """0.1 + 0.2 as floats would not land on a clean rupee."""
                data = quotes.QuoteInputs(idv="100000", base_premium_rate="2.5")
                self.assertEqual(quotes.calculate(data).base_premium, Decimal(2500))

        def test_money_rounds_halves_up_like_excel(self):
                self.assertEqual(quotes.money(Decimal("57978.5")), 57979)
                self.assertEqual(quotes.money(Decimal("0.5")), 1)
                self.assertEqual(quotes.money(Decimal("1.5")), 2)


class IndianNumberFormat(unittest.TestCase):
        def test_groups_in_lakhs_not_millions(self):
                self.assertEqual(quotes.format_inr(3600000), "36,00,000")
                self.assertEqual(quotes.format_inr(66109), "66,109")
                self.assertEqual(quotes.format_inr(32494), "32,494")

        def test_short_numbers_are_left_alone(self):
                self.assertEqual(quotes.format_inr(0), "0")
                self.assertEqual(quotes.format_inr(550), "550")
                self.assertEqual(quotes.format_inr(1000), "1,000")

        def test_crore_and_above(self):
                self.assertEqual(quotes.format_inr(12345678), "1,23,45,678")

        def test_rounds_before_grouping(self):
                self.assertEqual(quotes.format_inr(Decimal("66108.7684")), "66,109")

        def test_negative_amounts_keep_their_sign(self):
                """A cash back larger than the premium would make this negative."""
                self.assertEqual(quotes.format_inr(-150000), "-1,50,000")


if __name__ == "__main__":
        print(quotes.format_text(quotes.calculate(tn28bm0972())))
        print()
        unittest.main()
