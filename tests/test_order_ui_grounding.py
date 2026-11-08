from datetime import date
import copy
import pytest
from fakturama.order_ui import (
    GroundingError,
    address_selector_location,
    order_header_layout,
    header_values,
    interpret_date,
)


def dataset(dx=0, dy=0):
    # Synthetic UIA geometry (not an absolute click table). Layout derived
    # from the relative relationships in the uploaded Fakturama screenshot.
    base = [
        ("Text", "No.", (100, 40, 130, 60), None),
        ("Edit", "", (135, 38, 280, 62), "PO000222"),
        ("Text", "Date", (400, 40, 440, 60), None),
        ("Edit", "", (450, 39, 560, 61), "Oct 12, 2026"),
        ("ComboBox", "", (600, 37, 840, 64), "Net"),
        ("Text", "Cust.Ref.", (100, 110, 150, 130), None),
        ("Edit", "Cust.Ref.", (165, 108, 560, 135), ""),
        ("Text", "Addresses", (100, 147, 167, 167), None),
        ("TabItem", "Invoice address", (172, 147, 308, 171), None),
        ("Edit", "", (174, 174, 665, 225), ""),
        ("Text", "Items", (100, 246, 140, 266), None),
        ("ComboBox", "VAT", (580, 174, 825, 202), "With VAT"),
    ]
    rows = []
    for typ, name, rect, val in base:
        x1, y1, x2, y2 = rect
        row = {"type": typ, "name": name, "rect": [x1 + dx, y1 + dy, x2 + dx, y2 + dy]}
        if typ == "Edit":
            row["value"] = val
        if typ == "ComboBox":
            row["selected"] = val
        rows.append(row)
    return rows


def test_header_fields_found_without_automation_ids():
    actual = header_values(dataset())
    assert actual == {
        "number": "PO000222",
        "date": "Oct 12, 2026",
        "reference": "",
        "price_mode": "Net",
        "vat": "With VAT",
    }


def test_upper_selector_uses_relative_anchor_even_after_window_translation():
    a = address_selector_location(dataset())
    b = address_selector_location(dataset(dx=117, dy=83))
    assert a == (165, 194)
    assert b == (a[0] + 117, a[1] + 83)


def test_selector_fails_closed_on_missing_address_box():
    rows = [r for r in dataset() if not (r["type"] == "Edit" and r["rect"][1] == 174)]
    with pytest.raises(GroundingError):
        address_selector_location(rows)


def test_selector_fails_closed_on_ambiguous_address_fields():
    rows = dataset()
    extra = copy.deepcopy(next(r for r in rows if r["type"] == "Edit" and r["rect"][1] == 174))
    rows.append(extra)
    with pytest.raises(GroundingError):
        address_selector_location(rows)


def test_date_parser_rejects_unknown_text():
    assert interpret_date("Jul 14, 2026") == date(2026, 7, 14)
    assert interpret_date("14.07.2026") == date(2026, 7, 14)
    with pytest.raises(GroundingError):
        interpret_date("July days")


def test_header_ambiguity_fails_closed():
    rows = dataset()
    rows.append(copy.deepcopy(next(x for x in rows if x["type"] == "Text" and x["name"] == "Date")))
    with pytest.raises(GroundingError):
        order_header_layout(rows)
