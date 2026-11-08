"""Ground Order-header controls and the visually verified Addresses selector.

No screen-resolution constants. The one icon which Fakturama does not expose
through UIA is located RELATIVE to the labelled Addresses region and its editor.
This is a calibrated best-effort locator, not a universally proven UIA selector.
"""

from __future__ import annotations
from datetime import date, datetime
from typing import Iterable


class GroundingError(ValueError):
    pass


def mid_y(box):
    return (box[1] + box[3]) / 2


def exactly(rows, kind, name):
    found = [x for x in rows if x.get("type") == kind and x.get("name") == name]
    if len(found) != 1:
        raise GroundingError(f"Expected one {kind} {name!r}, found {len(found)}")
    return found[0]


def right_hand_edit(rows, label):
    anchor = exactly(rows, "Text", label)
    ar = anchor["rect"]
    found = [
        x
        for x in rows
        if x["type"] == "Edit"
        and x["rect"][0] >= ar[2] - 4
        and abs(mid_y(x["rect"]) - mid_y(ar)) <= 14
        and (x["rect"][0] - ar[2]) < 150
    ]
    found.sort(key=lambda x: x["rect"][0] - ar[2])
    if len(found) != 1:
        raise GroundingError(f"Cannot uniquely ground {label!r} Edit; found {len(found)}")
    return found[0]


def order_header_layout(rows):
    no = right_hand_edit(rows, "No.")
    when = right_hand_edit(rows, "Date")
    ref = exactly(rows, "Edit", "Cust.Ref.")
    vat = exactly(rows, "ComboBox", "VAT")
    price_combos = [
        x
        for x in rows
        if x["type"] == "ComboBox"
        and x.get("selected") in ("Net", "Gross")
        and abs(mid_y(x["rect"]) - mid_y(no["rect"])) <= 20
    ]
    if len(price_combos) != 1:
        raise GroundingError("Cannot uniquely locate Net/Gross order mode")
    if not (no["rect"][1] < ref["rect"][1] and when["rect"][1] < ref["rect"][1]):
        raise GroundingError("Unexpected Order header geometry")
    return {"number": no, "date": when, "reference": ref, "vat": vat, "price_mode": price_combos[0]}


def address_selector_location(rows):
    """Click point for the upper (existing contact) address icon, relative to the
    Addresses label. The lower green + creates a contact and is never targeted.
    """
    section = exactly(rows, "Text", "Addresses")["rect"]
    tab = exactly(rows, "TabItem", "Invoice address")["rect"]
    items = exactly(rows, "Text", "Items")["rect"]
    # UIA exposes the address editor but not the two icons beside it.
    editors = [
        x
        for x in rows
        if x["type"] == "Edit"
        and not x["name"]
        and abs(x["rect"][0] - tab[0]) < 15
        and 0 <= x["rect"][1] - tab[3] < 16
        and x["rect"][2] - x["rect"][0] >= 200
        and x["rect"][3] - x["rect"][1] >= 40
    ]
    if len(editors) != 1:
        raise GroundingError(f"Could not ground invoice-address box ({len(editors)} candidates)")
    edit = editors[0]["rect"]
    if not (section[0] < section[2] < tab[2] and section[1] < edit[1] < items[1]):
        raise GroundingError("Unexpected Addresses/Items geometry")
    # Icons are in the narrow strip between the label and the address textbox.
    x = round(section[2] - (tab[0] - section[2]) * 0.40)
    y = round(edit[1] + (edit[3] - edit[1]) * 0.40)
    if not (section[0] < x < edit[0] and section[3] < y < edit[3] - 8):
        raise GroundingError("Selector position does not satisfy safety bounds")
    if y >= items[1] - 25:
        raise GroundingError("Selector point is too close to Items row")
    return (x, y)


def interpret_date(text):
    text = text.strip()
    for fmt in (
        "%b %d, %Y",
        "%B %d, %Y",
        "%Y-%m-%d",
        "%d.%m.%Y",
        "%d/%m/%Y",
        "%m/%d/%Y",
        "%d %b %Y",
        "%d %B %Y",
    ):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise GroundingError(f"Unrecognized Fakturama date format {text!r}")


def header_values(rows):
    fields = order_header_layout(rows)
    return {
        "number": fields["number"].get("value"),
        "date": fields["date"].get("value"),
        "reference": fields["reference"].get("value"),
        "price_mode": fields["price_mode"].get("selected"),
        "vat": fields["vat"].get("selected"),
    }
