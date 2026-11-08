"""Layout-aware OCR for labelled purchase-order images.

Sections are found by their headings (several spellings each), values by pattern
classes rather than fixed literals: any reference or customer-id token, any ISO
currency code or symbol, 4-6 digit postal codes, common unit words, both
"1,234.56" and "1.234,56" amounts, and the usual date formats. Everything comes
from the image; there is no answer key. Order.validate() is the safety net, so a
looser read either reconciles arithmetically or stops.
"""

from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import os
import re
import shutil

import cv2
import pytesseract
from .models import Address, Item, Order, money


@dataclass
class Line:
    y: int
    words: list[tuple[int, str]]

    @property
    def text(self):
        return " ".join(t for _, t in self.words)

    def left_of(self, x):
        return " ".join(t for px, t in self.words if px < x).strip()

    def right_of(self, x):
        return " ".join(t for px, t in self.words if px >= x).strip()


def configure_tesseract(command: str | Path | None = None) -> str:
    """Locate Tesseract for CLI extraction AND direct OCR calls in pytest.

    Priority: --tesseract, the TESSERACT_CMD environment variable, an already
    configured executable, PATH, then the usual Windows install locations.
    """
    requested = command or os.environ.get("TESSERACT_CMD")
    if requested:
        candidate = Path(str(requested)).expanduser()
        executable = str(candidate) if candidate.is_file() else shutil.which(str(requested))
        if not executable:
            raise FileNotFoundError(
                f"Tesseract executable not found: {str(requested)!r}. "
                "Pass --tesseract with the complete path or set TESSERACT_CMD."
            )
        pytesseract.pytesseract.tesseract_cmd = executable
        return executable

    current = pytesseract.pytesseract.tesseract_cmd
    if current and current != "tesseract" and Path(str(current)).is_file():
        return str(current)

    candidates = [shutil.which("tesseract")]
    for env_name in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
        base = os.environ.get(env_name)
        if base:
            candidates.append(str(Path(base) / "Tesseract-OCR" / "tesseract.exe"))
    candidates.append(str(Path.home() / "AppData" / "Local" / "Tesseract-OCR" / "tesseract.exe"))

    for path in candidates:
        if path and Path(path).is_file():
            pytesseract.pytesseract.tesseract_cmd = str(path)
            return str(path)
    raise FileNotFoundError(
        "Tesseract executable not found. Windows: install Tesseract OCR and "
        "set TESSERACT_CMD to the path of tesseract.exe, or add its folder to PATH. "
        'Example: set "TESSERACT_CMD=%LOCALAPPDATA%\\Tesseract-OCR\\tesseract.exe"'
    )


def extract_lines(image_path: str | Path, scale: int = 3) -> list[Line]:
    # Keep an executable that --tesseract already selected.
    current = str(pytesseract.pytesseract.tesseract_cmd)
    if not (Path(current).is_file() or shutil.which(current)):
        configure_tesseract()
    image = cv2.imread(str(image_path))
    if image is None:
        raise FileNotFoundError(image_path)
    image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    data = pytesseract.image_to_data(
        image, config="--oem 3 --psm 6", output_type=pytesseract.Output.DICT
    )
    groups = defaultdict(list)
    for i, text in enumerate(data["text"]):
        if not text.strip():
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        groups[key].append((data["top"][i] // scale, data["left"][i] // scale, text.strip()))
    return sorted(
        (
            Line(
                min(y for y, _, _ in group),
                [(x, text) for _, x, text in sorted(group, key=lambda v: v[1])],
            )
            for group in groups.values()
        ),
        key=lambda line: line.y,
    )


def has(line: Line, phrase: str):
    return phrase.upper() in line.text.upper()


def after_heading(lines, heading, terminator=None):
    matches = [i for i, line in enumerate(lines) if has(line, heading)]
    if len(matches) != 1:
        raise ValueError(f"Expected one OCR heading {heading!r}, found {len(matches)}")
    result = lines[matches[0] + 1 :]
    if terminator:
        stops = [i for i, line in enumerate(result) if has(line, terminator)]
        if not stops:
            raise ValueError(f"Next OCR heading missing: {terminator}")
        result = result[: stops[0]]
    return result


def first_matching(lines, pattern, name):
    matches = [re.search(pattern, line.text, re.I) for line in lines]
    matches = [m for m in matches if m]
    if len(matches) != 1:
        raise ValueError(f"{name}: expected one match, found {len(matches)}")
    return matches[0]


# Alternative spellings for each section heading; the first one that appears
# exactly once wins.
HEADINGS = {
    "reference": ("EXTERNAL REFERENCE", "ORDER REFERENCE", "REFERENCE"),
    "customer": ("CUSTOMER AND CONTACT", "CUSTOMER & CONTACT", "CUSTOMER"),
    "addresses": ("ADDRESSES", "ADDRESS"),
    "payment": ("PAYMENT METHOD", "PAYMENT"),
    "items": ("ITEMS", "LINE ITEMS", "POSITIONS"),
    "totals": ("NET TOTAL", "SUBTOTAL", "TOTALS"),
}
CURRENCY_SYMBOLS = {"€": "EUR", "$": "USD", "£": "GBP"}
UNIT_WORDS = r"(?:pcs?\.?|pieces?|units?|ea\.?|stk\.?|st\.?|pc|x)"
DATE = r"\d{4}-\d{2}-\d{2}|\d{1,2}[./]\d{1,2}[./]\d{4}|[A-Z][a-z]{2,8}\.? \d{1,2},? \d{4}|\d{1,2} [A-Z][a-z]{2,8}\.? \d{4}"


def heading_variants(section):
    return HEADINGS[section]


def section(lines, name, terminator=None):
    """Lines between the heading of ``name`` and the next heading ``terminator``."""
    for heading in heading_variants(name):
        if len([line for line in lines if has(line, heading)]) == 1:
            break
    else:
        raise ValueError(f"No unique heading for {name!r} among {heading_variants(name)}")
    result = after_heading(lines, heading)
    if terminator:
        stops = [
            i
            for i, line in enumerate(result)
            if any(has(line, alt) for alt in heading_variants(terminator))
        ]
        if not stops:
            raise ValueError(f"Next OCR heading missing: {terminator}")
        result = result[: stops[0]]
    return result


def parse_date_text(text):
    """ISO, numeric day-first (14.07.2026, 14/07/2026) or English month names."""
    text = text.strip().rstrip(".")
    for fmt in (
        "%Y-%m-%d",
        "%d.%m.%Y",
        "%d/%m/%Y",
        "%b %d, %Y",
        "%b %d %Y",
        "%B %d, %Y",
        "%B %d %Y",
        "%d %b %Y",
        "%d %B %Y",
    ):
        try:
            return datetime.strptime(text.replace(".,", ","), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unrecognised date {text!r}")


def parse_amount(text):
    """Accept 1,234.56 and 1.234,56; the last separator is the decimal mark when both
    appear, a lone comma followed by two digits is a decimal mark."""
    text = text.strip()
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".") if re.fullmatch(r"\d+,\d{2}", text) else text.replace(",", "")
    return money(text)


def normalise_currency(token):
    token = token.strip()
    return CURRENCY_SYMBOLS.get(token, token.upper())


def fix_sku(value):
    value = value.strip().upper().replace(".", "-")
    segments = value.split("-")
    if len(segments) >= 3:
        # The last segment is a number; OCR confuses O/0 and I/1.
        segments[-1] = segments[-1].replace("O", "0").replace("I", "1").replace("L", "1")
    return "-".join(segments)


def parse_item(line: Line) -> Item | None:
    # Source row: #, SKU, description, qty, [unit], net unit price, [discount%], VAT%, line net.
    s = line.text
    pat = (
        r"^\s*\d+\s+(?P<sku>[A-Z0-9][A-Z0-9.\-/]{2,})\s+"
        r"(?P<description>.*?)\s+(?P<qty>\d+(?:[.,]\d+)?)\s+(?:" + UNIT_WORDS + r"\s+)?"
        r"(?P<unit_price>[\d,.]+)\s+(?:(?P<discount>\d+(?:[.,]\d+)?)\s*%\s+)?"
        r"(?P<vat>\d+(?:[.,]\d+)?)\s*%\s+(?P<line_net>[\d,.]+)\s*$"
    )
    match = re.search(pat, s, re.I)
    if not match:
        return None
    g = match.groupdict()
    return Item(
        sku=fix_sku(g["sku"]),
        description=g["description"].strip(),
        qty=Decimal(g["qty"].replace(",", ".")),
        unit_net=parse_amount(g["unit_price"]),
        discount_percent=Decimal((g["discount"] or "0").replace(",", ".")),
        vat_percent=Decimal(g["vat"].replace(",", ".")),
        source_line_net=parse_amount(g["line_net"]),
    )


def parse_address_columns(address_lines: list[Line]) -> tuple[Address, Address]:
    """Read two address columns using postal-code anchors, not heading OCR.

    The column headings OCR inconsistently across Tesseract builds; the postal
    codes (4-6 digits, optionally with a country prefix such as DE-) are stable anchors. A missing or ambiguous address fails closed
    with the raw OCR kept as evidence.
    """
    anchors = []
    for line in address_lines:
        for x, word in line.words:
            match = re.fullmatch(r"(?:[A-Z]{1,2}-)?(\d{4,6})[,:;.]?", word)
            if match:
                anchors.append((line.y, x, match.group(1)))

    # Postal codes should appear in the same visual row, once per column.
    pairs = [
        (left, right)
        for left in anchors
        for right in anchors
        if left[1] < right[1] and abs(left[0] - right[0]) <= 18
    ]
    if len(pairs) != 1:
        raise ValueError(
            "Unable to uniquely identify billing/delivery postal-code "
            f"columns ({len(pairs)} possible pairs). OCR lines: "
            + repr([(line.y, line.text) for line in address_lines])
        )

    left_zip, right_zip = pairs[0]
    split_x = (left_zip[1] + right_zip[1]) / 2

    def parse_column(left_side: bool, zip_anchor: tuple[int, int, str]) -> Address:
        zip_y, _, zip_expected = zip_anchor
        rows = []
        for line in address_lines:
            words = line.left_of(split_x) if left_side else line.right_of(split_x)
            if words:
                rows.append((line.y, words))

        # The source layout has company + street above the ZIP/City line,
        # and country directly below. Use geometry relative to the ZIP row.
        earlier = [(y, text) for y, text in rows if y < zip_y]
        zip_matches = [
            text
            for y, text in rows
            if y == zip_y and re.search(r"\b" + re.escape(zip_expected) + r"\b", text)
        ]
        later = [(y, text) for y, text in rows if y > zip_y]

        if len(earlier) < 2 or len(zip_matches) != 1 or not later:
            raise ValueError(
                "Incomplete address column relative to ZIP " f"{zip_expected}: {rows!r}"
            )
        company = earlier[-2][1]
        street = earlier[-1][1]
        zip_city = re.search(
            r"\b(" + re.escape(zip_expected) + r")\s+(.+?)\s*$",
            zip_matches[0],
        )
        if zip_city is None or not zip_city.group(2).strip():
            raise ValueError(f"ZIP/city OCR incomplete: {zip_matches!r}")
        country = later[0][1]
        return Address(
            company=company,
            street=street,
            zip=zip_city.group(1),
            city=zip_city.group(2).strip(),
            country=country,
        )

    return parse_column(True, left_zip), parse_column(False, right_zip)


def parse_order_image(image_path, tesseract_cmd=None) -> Order:
    if tesseract_cmd:
        configure_tesseract(tesseract_cmd)
    lines = extract_lines(image_path)
    order_header = section(lines, "reference", "customer")
    header = first_matching(
        order_header,
        r"(\S+)\s+(" + DATE + r")\s+(\S+)\s+([A-Z]{3}|[€$£])\b",
        "Order header",
    )

    customer_section = section(lines, "customer", "addresses")
    company_label = next(
        (x for x in customer_section if has(x, "COMPANY") and has(x, "CONTACT")), None
    )
    if company_label is None:
        raise ValueError("Company/contact label row missing")
    contact_xs = [x for x, t in company_label.words if t.upper() == "CONTACT"]
    if len(contact_xs) != 1:
        raise ValueError("Contact column missing")
    contact_x = contact_xs[0]
    company_row_candidates = [
        x
        for x in customer_section
        if x.y > company_label.y
        and len(x.right_of(contact_x)) >= 3
        and len(x.left_of(contact_x)) >= 3
    ]
    if not company_row_candidates:
        raise ValueError("Customer name row missing")
    customer_row = company_row_candidates[0]
    company = customer_row.left_of(contact_x)
    contact = customer_row.right_of(contact_x)
    if len(contact.split()) < 2:
        raise ValueError("First and last name must be present")
    first_name, last_name = contact.split(None, 1)
    # The alias is the left-column text on the first line below the ALIAS label;
    # the hyphenated-uppercase form of the sample is the fallback.
    alias = None
    alias_label = next((x for x in customer_section if has(x, "ALIAS")), None)
    if alias_label is not None:
        below = [x for x in customer_section if x.y > alias_label.y and x.left_of(contact_x)]
        if below and "@" not in below[0].left_of(contact_x):
            alias = below[0].left_of(contact_x)
    if not alias:
        alias_line = next(
            (
                x
                for x in customer_section
                if re.search(r"\b[A-Z]+(?:-[A-Z]+)+\b", x.left_of(contact_x))
            ),
            None,
        )
        if not alias_line:
            raise ValueError("Customer alias missing")
        alias = alias_line.left_of(contact_x)
    email = first_matching(customer_section, r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "Email").group(0)
    phone_matches = [
        x.right_of(contact_x)
        for x in customer_section
        if re.fullmatch(r"\+?\d[\d\s()/-]{6,}", x.right_of(contact_x))
    ]
    if len(phone_matches) != 1:
        raise ValueError("Phone OCR did not produce exactly one value")

    address_lines = section(lines, "addresses", "payment")
    billing, delivery = parse_address_columns(address_lines)

    payment_lines = section(lines, "payment", "items")
    payment_match = first_matching(
        payment_lines, r"(.+?)\s+(PAID|UNPAID|OPEN)(?:\s+(" + DATE + r"))?\s*$", "Payment"
    )
    payment_method, paid_str, payment_date = payment_match.groups()
    paid_str = paid_str.upper()

    item_lines = section(lines, "items", "totals")
    items = tuple(item for row in item_lines if (item := parse_item(row)) is not None)
    if not items:
        raise ValueError("No complete item rows detected; manual OCR review required")
    # SKU-looking lines that did not parse mean we are missing items.
    candidate_rows = [
        row
        for row in item_lines
        if re.match(r"^\s*\d+\s+[A-Z0-9][A-Z0-9.\-/]{2,}\s", row.text, re.I)
    ]
    if len(candidate_rows) != len(items):
        raise ValueError("At least one item row is ambiguous or incomplete")

    totals_lines = section(lines, "totals")
    amount = r"(?:[A-Z]{3}|[€$£])?\s*(\d[\d,.]*)"
    totals = first_matching(
        totals_lines, r"^\s*" + amount + r"\s+" + amount + r"\s+" + amount + r"\s*$", "Totals"
    )
    order = Order(
        reference=header.group(1),
        order_date=parse_date_text(header.group(2)),
        source_customer_id=header.group(3),
        currency=normalise_currency(header.group(4)),
        company=company,
        first_name=first_name,
        last_name=last_name,
        alias=alias,
        email=email,
        phone=phone_matches[0],
        billing=billing,
        delivery=delivery,
        payment_method=payment_method.strip(),
        paid=paid_str == "PAID",
        payment_date=parse_date_text(payment_date) if paid_str == "PAID" and payment_date else None,
        items=items,
        source_net=parse_amount(totals.group(1)),
        source_vat=parse_amount(totals.group(2)),
        source_gross=parse_amount(totals.group(3)),
    )
    order.validate()
    return order
