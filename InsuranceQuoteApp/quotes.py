"""Motor insurance premium calculation.

Reproduces the Excel sheet the quotes were previously worked out in by hand.
The order of operations matters and is not obvious, so it is spelled out here:

        base premium   = IDV x base rate %
        gross OD       = base premium + carrying capacity load
        net OD         = gross OD - (gross OD x OD discount %)
        gross OD (2)   = net OD + IMT-23 loading
        total OD       = gross OD (2) - NCB
        liability      = third party + PA owner + paid driver + PA unnamed
        gross premium  = total OD + liability
        total          = gross premium + GST
        final amount   = total - cash back

The sheet carries full precision between cells and rounds only for display -
95% of 61030 shows as 57979 but the 3051.5 remainder is what IMT-23 is applied
to. Rounding each step instead drifts the total by a few rupees, so every
amount is kept as an exact Decimal and rounded once, at render time.
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


def money(value):
        """Round an amount the way the sheet displays it: half up, no paise.

        Excel rounds halves away from zero; Python's round() rounds to even,
        which would show 57978 where the sheet shows 57979."""
        return int(Decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def dec(value):
        """Accept ints, floats and strings from form input as exact Decimals."""
        if isinstance(value, Decimal):
                return value
        if value in ("", None):
                return Decimal(0)
        return Decimal(str(value))


def percent_of(amount, rate):
        return amount * dec(rate) / Decimal(100)


GST_PERCENT = Decimal("18")


@dataclass
class QuoteInputs:
        """Everything the sheet needs. Rates are percentages, not fractions:
        an OD discount of 95 means 95%, not 0.95."""

        vehicle_number: str = ""
        insurer: str = ""
        quote_date: str = ""

        idv: Decimal = Decimal(0)
        base_premium_rate: Decimal = Decimal(0)
        carrying_capacity: Decimal = Decimal(0)
        od_discount_percent: Decimal = Decimal(0)
        imt23_percent: Decimal = Decimal(0)
        ncb_percent: Decimal = Decimal(0)

        third_party: Decimal = Decimal(0)
        pa_owner_driver: Decimal = Decimal(0)
        paid_driver: Decimal = Decimal(0)

        # The sheet shows PA to unnamed passengers as a unit count beside a
        # lump sum, so either form is accepted: give the amount outright, or
        # give the count and let the per-unit rate multiply it out.
        pa_unnamed_units: Decimal = Decimal(0)
        pa_unnamed_rate: Decimal = Decimal(0)
        pa_unnamed_amount: Decimal = None

        gst_percent: Decimal = GST_PERCENT

        # Cash back is a negotiated giveaway, not a tariff, so it is either an
        # outright amount or a percentage of the GST-inclusive total.
        cash_back: Decimal = None
        cash_back_percent: Decimal = None

        # Fields that mean "not supplied" when None keep that distinction; the
        # rest are coerced so callers can pass strings straight from a form.
        OPTIONAL = ("pa_unnamed_amount", "cash_back", "cash_back_percent")
        REQUIRED = (
                "idv", "base_premium_rate", "carrying_capacity",
                "od_discount_percent", "imt23_percent", "ncb_percent",
                "third_party", "pa_owner_driver", "paid_driver",
                "pa_unnamed_units", "pa_unnamed_rate", "gst_percent",
        )

        def __post_init__(self):
                for name in self.REQUIRED:
                        setattr(self, name, dec(getattr(self, name)))
                for name in self.OPTIONAL:
                        value = getattr(self, name)
                        if value is not None and value != "":
                                setattr(self, name, dec(value))
                        else:
                                setattr(self, name, None)


@dataclass
class QuoteRow:
        """One line of the rendered quote.

        `style` names the sheet's formatting for that line so the renderer does
        not have to recognise rows by their label text:

                value    plain row
                input    the rate cell is one of the yellow input boxes
                spacer   blank separator row
                band     the blue full-width rule
                subtotal bold row
                total    the large bold Total Premium row
                offer    the yellow Cash Back row
                final    the green Final Amount row
        """

        label: str
        rate: str
        amount: int
        style: str = "value"


@dataclass
class QuoteBreakdown:
        """Computed amounts, in sheet order. Every field is an exact Decimal;
        call rows() for the rounded values to display or send."""

        inputs: QuoteInputs
        base_premium: Decimal
        gross_od_before_discount: Decimal
        od_discount: Decimal
        net_od: Decimal
        imt23: Decimal
        gross_od_after_imt23: Decimal
        ncb: Decimal
        total_od: Decimal
        pa_unnamed: Decimal
        total_liability: Decimal
        gross_premium: Decimal
        gst: Decimal
        total_premium: Decimal
        cash_back: Decimal
        final_amount: Decimal

        def rows(self):
                """The quote as QuoteRow lines, in the sheet's own order -
                including its blank separators and the blue rule, so a render
                lines up with the spreadsheet the customers already know."""
                i = self.inputs
                return [
                        QuoteRow("Insured Declared Value (IDV)", None, money(i.idv)),
                        QuoteRow("", None, None, "spacer"),
                        QuoteRow("Base Premium", f"{i.base_premium_rate:.3f}",
                                 money(self.base_premium), "input"),
                        QuoteRow("Carrying Capacity", None, money(i.carrying_capacity)),
                        QuoteRow("Gross OD", None, money(self.gross_od_before_discount)),
                        QuoteRow("Percentage on OD", f"{i.od_discount_percent:g}",
                                 money(self.od_discount), "input"),
                        QuoteRow("Net OD", None, money(self.net_od)),
                        QuoteRow("", None, None, "spacer"),
                        QuoteRow("Add: IMT 23", f"{i.imt23_percent:g}", money(self.imt23), "input"),
                        QuoteRow("Gross OD", None, money(self.gross_od_after_imt23)),
                        QuoteRow("Less: NCB", f"{i.ncb_percent:g}", money(self.ncb), "input"),
                        QuoteRow("Total OD", None, money(self.total_od)),
                        QuoteRow("", None, None, "band"),
                        QuoteRow("Net Own Damage Premium", None, money(self.total_od), "subtotal"),
                        QuoteRow("Add: Third Party", None, money(i.third_party)),
                        QuoteRow("Add: PA to Owner Driver", None, money(i.pa_owner_driver)),
                        QuoteRow("Add: Paid Driver", None, money(i.paid_driver), "subtotal"),
                        QuoteRow("Add: PA to Unnamed Passenger",
                                 f"{i.pa_unnamed_units:g}" if i.pa_unnamed_units else None,
                                 money(self.pa_unnamed)),
                        QuoteRow("Total Liability Premium", None,
                                 money(self.total_liability), "subtotal"),
                        QuoteRow("Gross Premium (Own Damage+Third Party)", None,
                                 money(self.gross_premium), "subtotal"),
                        QuoteRow("GST", f"{i.gst_percent:.2f}%", money(self.gst)),
                        QuoteRow("Total Premuim", None, money(self.total_premium), "total"),
                        QuoteRow("Cash Back Offer", None, money(self.cash_back), "offer"),
                        QuoteRow("Final Amount", None, money(self.final_amount), "final"),
                ]


def calculate(inputs):
        """Work the sheet top to bottom. Returns a QuoteBreakdown."""
        base_premium = percent_of(inputs.idv, inputs.base_premium_rate)
        gross_od_before_discount = base_premium + inputs.carrying_capacity

        od_discount = percent_of(gross_od_before_discount, inputs.od_discount_percent)
        net_od = gross_od_before_discount - od_discount

        imt23 = percent_of(net_od, inputs.imt23_percent)
        gross_od_after_imt23 = net_od + imt23

        ncb = percent_of(gross_od_after_imt23, inputs.ncb_percent)
        total_od = gross_od_after_imt23 - ncb

        if inputs.pa_unnamed_amount is not None:
                pa_unnamed = inputs.pa_unnamed_amount
        else:
                pa_unnamed = inputs.pa_unnamed_units * inputs.pa_unnamed_rate

        total_liability = (
                inputs.third_party + inputs.pa_owner_driver
                + inputs.paid_driver + pa_unnamed
        )

        gross_premium = total_od + total_liability
        gst = percent_of(gross_premium, inputs.gst_percent)
        total_premium = gross_premium + gst

        if inputs.cash_back is not None:
                cash_back = inputs.cash_back
        elif inputs.cash_back_percent is not None:
                cash_back = percent_of(total_premium, inputs.cash_back_percent)
        else:
                cash_back = Decimal(0)

        return QuoteBreakdown(
                inputs=inputs,
                base_premium=base_premium,
                gross_od_before_discount=gross_od_before_discount,
                od_discount=od_discount,
                net_od=net_od,
                imt23=imt23,
                gross_od_after_imt23=gross_od_after_imt23,
                ncb=ncb,
                total_od=total_od,
                pa_unnamed=pa_unnamed,
                total_liability=total_liability,
                gross_premium=gross_premium,
                gst=gst,
                total_premium=total_premium,
                cash_back=cash_back,
                final_amount=total_premium - cash_back,
        )


def format_inr(amount):
        """Group digits the Indian way: 32494 -> 32,494, 3600000 -> 36,00,000.

        Western grouping would render that second one as 3,600,000, which reads
        as a different number to a customer used to lakhs."""
        digits = str(abs(money(amount)))
        if len(digits) > 3:
                head, tail = digits[:-3], digits[-3:]
                groups = []
                while len(head) > 2:
                        head, group = head[:-2], head[-2:]
                        groups.insert(0, group)
                if head:
                        groups.insert(0, head)
                digits = ",".join(groups + [tail])
        return ("-" if money(amount) < 0 else "") + digits


def format_text(breakdown, label_width=40):
        """Plain-text rendering, for checking a quote in a terminal."""
        i = breakdown.inputs
        lines = []
        header = "   ".join(part for part in (i.insurer, i.vehicle_number, i.quote_date) if part)
        if header:
                lines.append(header)
                lines.append("-" * (label_width + 18))
        for row in breakdown.rows():
                if row.style == "spacer":
                        lines.append("")
                        continue
                if row.style == "band":
                        lines.append("-" * (label_width + 18))
                        continue
                rate_cell = f"{row.rate:>8}" if row.rate else " " * 8
                lines.append(f"{row.label:<{label_width}}{rate_cell}{row.amount:>10,}")
        return "\n".join(lines)
