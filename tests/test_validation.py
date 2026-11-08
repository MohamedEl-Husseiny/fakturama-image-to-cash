import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import pytest
from fakturama.ocr import parse_order_image
from fakturama.models import read_order, money

IMAGE = Path(__file__).resolve().parents[1] / "examples" / "source_order.png"


def test_round_trip(tmp_path):
    source = parse_order_image(IMAGE)
    path = tmp_path / "out.json"
    source.save_json(path)
    assert read_order(path) == source


def test_detects_wrong_line_price():
    o = parse_order_image(IMAGE)
    item = replace(o.items[0], unit_net=Decimal("255.00"))
    invalid = replace(o, items=(item, *o.items[1:]))
    with pytest.raises(ValueError, match="OCR net"):
        invalid.validate()


def test_detects_wrong_document_total():
    o = parse_order_image(IMAGE)
    with pytest.raises(ValueError, match="total mismatch"):
        replace(o, source_gross=Decimal("678.31")).validate()


def test_persistence_requires_real_amounts(tmp_path):
    o = parse_order_image(IMAGE)
    p = tmp_path / "order.json"
    o.save_json(p)
    data = json.loads(p.read_text())
    data["items"][0]["source_line_net"] = "999.99"
    p.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        read_order(p)


def test_money_round_half_up():
    assert money("1.005") == Decimal("1.01")
