"""Regression tests for layout changes between Tesseract installations."""

from pathlib import Path
import pytest
from fakturama import ocr
from fakturama.ocr import Line

IMAGE = Path(__file__).resolve().parents[1] / "examples" / "source_order.png"


def _base_lines():
    # Load the actual supplied source, then simulate known OCR segmentation
    # differences without changing the image or hard-coding the addresses.
    return ocr.extract_lines(IMAGE)


def test_billing_and_delivery_heading_unreadable(monkeypatch):
    lines = _base_lines()
    altered = []
    for line in lines:
        if "BILLING" in line.text and "DELIVERY" in line.text:
            words = [
                (x, text.replace("BILLING", "B1LL1NG").replace("DELIVERY", "DEL1VERY"))
                for x, text in line.words
            ]
            altered.append(Line(line.y, words))
        else:
            altered.append(line)
    monkeypatch.setattr(ocr, "extract_lines", lambda image_path: altered)
    result = ocr.parse_order_image(IMAGE)
    assert (result.billing.zip, result.billing.city) == ("10117", "Berlin")
    assert (result.delivery.zip, result.delivery.city) == ("10553", "Berlin")
    assert result.delivery.company == "Northstar Office Warehouse"


def test_heading_split_across_tesseract_lines(monkeypatch):
    lines = _base_lines()
    altered = []
    for line in lines:
        if "BILLING" in line.text and "DELIVERY" in line.text:
            altered += [
                Line(line.y, [(x, text) for x, text in line.words if x < 200]),
                Line(line.y + 3, [(x, text) for x, text in line.words if x >= 200]),
            ]
        else:
            altered.append(line)
    monkeypatch.setattr(ocr, "extract_lines", lambda image_path: altered)
    result = ocr.parse_order_image(IMAGE)
    assert result.billing.company == "Northstar Office GmbH"
    assert result.delivery.street == "Beusselstrasse 44"


def test_ambiguous_missing_zip_fails_with_ocr_evidence():
    lines = _base_lines()
    address_section = [line for line in lines if 340 < line.y < 480]
    ambiguous = [
        Line(line.y, [(x, "?????" if token == "10553" else token) for x, token in line.words])
        for line in address_section
    ]
    with pytest.raises(ValueError, match="postal-code columns") as error:
        ocr.parse_address_columns(ambiguous)
    assert "10117" in str(error.value)
