"""Names shared by the Runner and its stage mixins (no desktop access)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import logging
import re
from typing import Any

from fakturama.fields import NumericFieldError, format_ui_number, parse_ui_money

LOG = logging.getLogger("image_to_cash")
PAYMENT_CODES = {
    "Bank Transfer": "Credit transfer",
    "Credit Card": "Credit card",
    "SEPA Direct Debit": "SEPA direct debit",
}


class GuardError(RuntimeError):
    """Automation stopped safely because a required UI assertion was not proved."""


def normalized(s: str) -> str:
    return re.sub(r"\s+", " ", str(s)).strip().casefold()


def decimal_from_ui(s: str) -> Decimal:
    """Compatibility boundary: translate pure field errors into workflow guards."""
    try:
        return parse_ui_money(s)
    except NumericFieldError as exc:
        raise GuardError(str(exc)) from exc


def number_for_ui(value, displayed):
    try:
        return format_ui_number(value, displayed)
    except NumericFieldError as exc:
        raise GuardError(str(exc)) from exc


def within(rect, x, y):
    return rect.left <= x <= rect.right and rect.top <= y <= rect.bottom


def midpoint(rect):
    return (int((rect.left + rect.right) / 2), int((rect.top + rect.bottom) / 2))


def meaningful_ocr(s):
    return normalized(re.sub(r"[^a-zA-Z0-9 \-.]", " ", s))


@dataclass(frozen=True)
class SearchResult:
    status: str  # 'MISSING' or 'EXACT'; all ambiguity raises
    row: Any | None = None  # pixel y coordinate, relative to screen, or UIA wrapper


def assert_unobstructed(app, bbox, what):
    """Refuse to OCR a region another process's window covers (no-op for test fakes)."""
    check = getattr(app, "assert_unobstructed", None)
    if check is not None:
        check(bbox, what)


def grab_region(runner, bbox, what, hwnd=None):
    """OCR capture through the runner's UI adapter (overlay-proof); plain screen grab
    when there is no adapter or it is a test fake."""
    grab = getattr(getattr(runner, "app", None), "grab", None)
    if grab is not None:
        return grab(bbox, what, hwnd=hwnd)
    from PIL import ImageGrab

    return ImageGrab.grab(bbox=bbox)
