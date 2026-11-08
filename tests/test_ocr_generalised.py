"""The parser anchors on headings and pattern classes, not on the sample's literals."""

from datetime import date
from decimal import Decimal

import pytest

from fakturama import ocr
from fakturama.ocr import Line, parse_amount, parse_date_text, parse_item, section


def line(y, text, start=10, step=60):
    return Line(y, [(start + i * step, word) for i, word in enumerate(text.split())])


def test_parse_amount_accepts_both_separator_conventions():
    assert parse_amount("1,234.56") == Decimal("1234.56")
    assert parse_amount("1.234,56") == Decimal("1234.56")
    assert parse_amount("250,00") == Decimal("250.00")
    assert parse_amount("250.00") == Decimal("250.00")
    assert parse_amount("570") == Decimal("570.00")


@pytest.mark.parametrize(
    "text",
    ["2026-07-14", "14.07.2026", "14/07/2026", "Jul 14, 2026", "14 Jul 2026", "July 14 2026"],
)
def test_parse_date_text_formats(text):
    assert parse_date_text(text) == date(2026, 7, 14)


def test_item_row_without_unit_word_or_discount():
    item = parse_item(line(1, "3 ABC-1 Widget 2 40,00 19% 80,00"))
    assert item.sku == "ABC-1"
    assert item.qty == Decimal("2")
    assert item.discount_percent == Decimal("0")
    assert item.vat_percent == Decimal("19")
    assert item.unit_net == Decimal("40.00")
    assert item.source_line_net == Decimal("80.00")


def test_item_row_with_unit_word_and_discount_still_parses():
    item = parse_item(line(1, "1 CHR-ERG-01 Ergonomic Desk Chair 2 pcs 250.00 10% 19% 450.00"))
    assert item.sku == "CHR-ERG-01"
    assert item.description == "Ergonomic Desk Chair"
    assert item.discount_percent == Decimal("10")


def test_section_accepts_alternative_headings():
    lines = [
        line(1, "LINE ITEMS"),
        line(2, "1 A-1 Thing 1 10.00 19% 10.00"),
        line(3, "SUBTOTAL VAT TOTAL"),
    ]
    body = section(lines, "items", "totals")
    assert [l.text for l in body] == ["1 A-1 Thing 1 10.00 19% 10.00"]


def test_section_requires_a_unique_heading():
    lines = [line(1, "ITEMS"), line(2, "ITEMS"), line(3, "NET TOTAL")]
    with pytest.raises(ValueError, match="No unique heading"):
        section(lines, "items", "totals")


def test_alternative_layout_parses_end_to_end(monkeypatch):
    """Different reference and customer-id formats, day-first dates, European
    amounts, a four-digit postal code and no unit words: still one valid Order."""
    rows = [
        (10, "ORDER REFERENCE ORDER DATE CUSTOMER ID CURRENCY"),
        (20, "PO/2026/0099 14.07.2026 K-77 EUR"),
        (30, "CUSTOMER AND CONTACT"),
        (40, "COMPANY CONTACT NAME"),
        (50, "Beispiel AG Anna Muster"),
        (60, "CUSTOMER ALIAS"),
        (70, "BEISPIEL anna@example.test"),
        (80, "PHONE"),
        (90, "x 030 1234567"),
        (100, "ADDRESSES"),
        (110, "BILLING ADDRESS DELIVERY ADDRESS"),
        (120, "Beispiel AG Beispiel Lager"),
        (130, "Hauptstrasse 1 Lagerweg 2"),
        (140, "1010 Wien 1020 Wien"),
        (150, "Austria Austria"),
        (160, "PAYMENT METHOD PAID STATUS PAYMENT DATE"),
        (170, "Credit Card UNPAID"),
        (180, "ITEMS"),
        (190, "1 ART-0001 Stuhl 2 100,00 20% 200,00"),
        (200, "TOTALS"),
        (210, "€ 200,00 € 40,00 € 240,00"),
    ]

    def fake_lines(image_path, scale=3):
        out = []
        for y, text in rows:
            words = text.split()
            if y in (50, 120, 130, 140, 150):
                # two columns: split the words half/half across the page
                half = len(words) // 2
                left = [(10 + i * 60, w) for i, w in enumerate(words[:half])]
                right = [(400 + i * 60, w) for i, w in enumerate(words[half:])]
                out.append(Line(y, left + right))
            elif y == 70:
                out.append(Line(y, [(10, "BEISPIEL"), (400, "anna@example.test")]))
            elif y == 90:
                out.append(Line(y, [(400, "030"), (460, "1234567")]))
            elif y == 40:
                out.append(Line(y, [(10, "COMPANY"), (400, "CONTACT"), (460, "NAME")]))
            else:
                out.append(line(y, text))
        return out

    monkeypatch.setattr(ocr, "extract_lines", fake_lines)
    order = ocr.parse_order_image("ignored.png")
    assert order.reference == "PO/2026/0099"
    assert order.source_customer_id == "K-77"
    assert order.order_date == date(2026, 7, 14)
    assert order.company == "Beispiel AG"
    assert (order.first_name, order.last_name) == ("Anna", "Muster")
    assert order.alias == "BEISPIEL"
    assert order.billing.zip == "1010" and order.delivery.zip == "1020"
    assert order.paid is False and order.payment_date is None
    assert order.items[0].unit_net == Decimal("100.00")
    assert order.source_gross == Decimal("240.00")
