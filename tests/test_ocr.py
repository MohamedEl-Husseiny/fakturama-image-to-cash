from pathlib import Path
from decimal import Decimal
from fakturama.ocr import parse_order_image, fix_sku

IMAGE = Path(__file__).resolve().parents[1] / "examples" / "source_order.png"


def test_source_order_exact_values():
    x = parse_order_image(IMAGE)
    assert x.reference == "WEB-2026-0714-A17"
    assert x.company == "Northstar Office GmbH"
    assert (x.first_name, x.last_name) == ("Marta", "Klein")
    assert x.alias == "NORTHSTAR-BERLIN"
    assert x.billing.street == "Friedrichstrasse 88"
    assert (x.billing.zip, x.billing.city) == ("10117", "Berlin")
    assert x.delivery.company == "Northstar Office Warehouse"
    assert x.delivery.street == "Beusselstrasse 44"
    assert (x.delivery.zip, x.delivery.city) == ("10553", "Berlin")
    assert x.payment_method == "Bank Transfer"
    assert x.paid is True
    assert x.payment_date.isoformat() == "2026-07-18"
    assert [y.sku for y in x.items] == ["CHR-ERG-01", "MAT-DESK-02"]
    assert [y.description for y in x.items] == ["Ergonomic Desk Chair", "Anti-Fatigue Desk Mat"]
    assert [y.line_net for y in x.items] == [Decimal("450.00"), Decimal("120.00")]
    assert [y.gross_master_price for y in x.items] == [Decimal("297.50"), Decimal("47.60")]
    assert x.calculated_net == Decimal("570.00")
    assert x.calculated_vat == Decimal("108.30")
    assert x.calculated_gross == Decimal("678.30")


def test_sku_character_confusion():
    assert fix_sku("CHR-ERG.O1") == "CHR-ERG-01"
    assert fix_sku("MAT-DESK-02") == "MAT-DESK-02"
