"""Pure numeric field codecs shared by UI entry and OCR verification.

Pure functions: nothing here touches a window. Parsing keeps full precision;
formatting follows the two-decimal convention of Fakturama's fields.
"""

from decimal import Decimal
import re

from fakturama.models import money


class NumericFieldError(ValueError):
    """A numeric field cannot be interpreted without guessing."""


def _numeric_token(displayed: str) -> str:
    text = str(displayed)
    matches = list(re.finditer(r"[-+]?\d[\d.,]*", text))
    if len(matches) != 1:
        raise NumericFieldError(f"Expected exactly one numeric value: {displayed!r}")
    match = matches[0]
    token = match.group()
    prefix = text[: match.start()]
    suffix = text[match.end() :]
    signs = re.findall(r"[-+]", prefix)
    if (
        len(signs) > 1
        or (signs and token[0] in "+-")
        or re.search(r"[-+()]", suffix)
        or "(" in prefix
    ):
        raise NumericFieldError(f"Ambiguous numeric sign: {displayed!r}")
    return (signs[0] if signs else "") + token


def parse_ui_decimal(displayed: str) -> Decimal:
    """Parse one displayed amount/quantity without changing its precision."""
    token = _numeric_token(displayed)
    if "," in token and "." in token:
        token = (
            token.replace(",", "")
            if token.rfind(".") > token.rfind(",")
            else token.replace(".", "").replace(",", ".")
        )
    elif "," in token:
        token = (
            token.replace(",", ".") if len(token.rsplit(",", 1)[1]) <= 2 else token.replace(",", "")
        )
    if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", token):
        raise NumericFieldError(f"Invalid numeric value: {displayed!r}")
    return Decimal(token)


def parse_ui_money(displayed: str) -> Decimal:
    """Apply the domain's cent rounding only for monetary fields."""
    return money(parse_ui_decimal(displayed))


def format_ui_number(value: Decimal | int | str, displayed: str) -> str:
    """Use the decimal mark in the observed destination field; no conversion."""
    token = _numeric_token(displayed)
    separator = "," if "," in token and token.rfind(",") > token.rfind(".") else "."
    return f"{money(value):.2f}".replace(".", separator)
