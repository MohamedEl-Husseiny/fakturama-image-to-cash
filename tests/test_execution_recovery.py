from datetime import date
from decimal import Decimal
from pathlib import Path
from dataclasses import replace

import pytest

from fakturama.execution import Receipt, classify_documents, assert_document_snapshot
from fakturama.models import read_order
from fakturama.grid import assert_line_cells, grid_headers


@pytest.fixture
def order():
    return read_order(Path(__file__).parents[1] / "examples/extracted_order.json")


def test_receipt_rejects_changed_source(tmp_path, order):
    path = tmp_path / "receipt.json"
    receipt = Receipt(path, order)
    receipt.update(order_number="PO000009", phase="order_saved")
    assert Receipt(path, order).data["order_number"] == "PO000009"
    with pytest.raises(ValueError, match="different source"):
        Receipt(path, replace(order, reference="OTHER"))


def test_duplicate_or_malformed_document_rows_stop():
    with pytest.raises(ValueError, match="Multiple order"):
        classify_documents([("PO000009 open $678.30", 10), ("PO000010 open $678.30", 30)])
    with pytest.raises(ValueError, match="one document number"):
        classify_documents([("PO000009 INV000002 $678.30", 10)])
    assert classify_documents([]) == {"order": None, "invoice": None}


def snapshot(order):
    return dict(
        number="INV000002",
        reference=order.reference,
        order_date=order.order_date,
        price_mode="Net",
        vat_mode="With VAT",
        net=order.source_net,
        vat=order.source_vat,
        gross=order.source_gross,
        discount=Decimal("0"),
        shipping=Decimal("0"),
        paid=True,
        payment_method="Bank Transfer",
        payment_date=date(2026, 7, 18),
        payment_value=Decimal("678.30"),
    )


@pytest.mark.parametrize(
    "field,bad",
    [
        ("number", "INV000003"),
        ("reference", "OTHER"),
        ("paid", False),
        ("payment_method", "Cash"),
        ("payment_date", date(2026, 7, 19)),
        ("payment_value", Decimal("600")),
        ("gross", Decimal("600")),
        ("order_date", date(2026, 7, 15)),
    ],
)
def test_matching_totals_or_reference_do_not_hide_payment_identity_errors(order, field, bad):
    actual = snapshot(order)
    assert_document_snapshot(actual, order, "INV000002", invoice=True)
    actual[field] = bad
    with pytest.raises(ValueError, match="mismatch"):
        assert_document_snapshot(actual, order, "INV000002", invoice=True)


def cells():
    return dict(
        sku="CHR-ERG-01",
        qty="2.00",
        price="$250.00",
        vat="VAT 19% (19.0%)",
        discount="-10.00 %",
        total="$450.00",
    )


@pytest.mark.parametrize(
    "key,bad",
    [
        ("sku", "CHR-ERG-010"),
        ("qty", "1.00"),
        ("price", "$225.00"),
        ("vat", "VAT 7% (7.0%)"),
        ("discount", "0%"),
        ("total", "$500.00"),
    ],
)
def test_every_line_cell_is_independently_checked(order, key, bad):
    actual = cells()
    assert_line_cells(actual, order.items[0])
    actual[key] = bad
    with pytest.raises(ValueError):
        assert_line_cells(actual, order.items[0])


def test_grid_headers_distinguish_unit_price_from_line_price():
    words = [
        "Pos.",
        "Qty.",
        "Item",
        "Picture",
        "Name",
        "Description",
        "VAT",
        "U.Price",
        "Discount",
        "Price",
    ]
    tokens = [dict(t=t, x=100 + i * 90, y=200, w=45, h=12) for i, t in enumerate(words)]
    header = grid_headers(tokens)
    assert header["price"]["x"] < header["discount"]["x"] < header["total"]["x"]
    with pytest.raises(ValueError):
        grid_headers([t for t in tokens if t["t"] != "U.Price"])


@pytest.mark.parametrize(
    "image_name,expected",
    [
        ("document_order_list.png", "PO000008 open 678.30"),
        ("document_order_maximized.png", "PO000008 open 678.30"),
        ("document_invoice_maximized.png", "INV000001 paid 678.30"),
        ("document_linked_selected.png", ["INV000001 paid 678.30", "PO000008 open 678.30"]),
    ],
)
@pytest.mark.parametrize("omit_body_tokens", [False, True])
def test_real_saved_number_cell_regression(monkeypatch, image_name, expected, omit_body_tokens):
    """Recorded native table: broad OCR read POQ000008 or dropped it entirely."""
    from types import SimpleNamespace
    from PIL import Image, ImageGrab
    from fakturama_full_workflow import Runner
    from fakturama.ocr import configure_tesseract

    configure_tesseract()
    fixture = Image.open(Path(__file__).parent / "fixtures" / image_name)
    rect = SimpleNamespace(
        left=0,
        right=fixture.width + 12,
        bottom=fixture.height + 15,
        width=lambda: fixture.width + 12,
    )
    runner = Runner.__new__(Runner)
    runner.window = SimpleNamespace(rectangle=lambda: rect)
    tab = SimpleNamespace(rectangle=lambda: SimpleNamespace(bottom=-22))
    monkeypatch.setattr(ImageGrab, "grab", lambda bbox: fixture.copy())
    if omit_body_tokens:
        import pytesseract

        read_data = pytesseract.image_to_data

        def headers_only(*args, **kwargs):
            data = read_data(*args, **kwargs)
            for i, text in enumerate(data["text"]):
                if text not in ("Document", "Date", "State", "Total", "Printed"):
                    data["text"][i] = ""
            return data

        monkeypatch.setattr(pytesseract, "image_to_data", headers_only)
    rows = runner._document_table_rows(tab)
    assert [text for text, y in rows] == (expected if isinstance(expected, list) else [expected])


@pytest.mark.parametrize(
    "before,after",
    [
        ([], []),
        ([], ["OTHER"]),
        ([], ["MAT-DESK-02", "MAT-DESK-02"]),
        (["MAT-DESK-02"], ["MAT-DESK-02"]),
    ],
)
def test_auto_closed_product_selector_requires_one_exact_new_row(before, after):
    from fakturama.grid import assert_product_added

    assert_product_added(["CHR-ERG-01"], ["CHR-ERG-01", "MAT-DESK-02"], "MAT-DESK-02")
    with pytest.raises(ValueError):
        assert_product_added(before, after, "MAT-DESK-02")


@pytest.mark.parametrize("fixture", ["selected_order_grid.png", "two_row_order_grid.png"])
def test_selected_order_grid_keeps_qty_header_and_exact_sku(fixture):
    from PIL import Image
    from fakturama.grid import read_grid_tokens, grid_headers
    from fakturama.ocr import configure_tesseract

    configure_tesseract()
    tokens = read_grid_tokens(Image.open(Path(__file__).parent / "fixtures" / fixture))
    headers = grid_headers(tokens)
    assert headers["qty"]["x"] < headers["sku"]["x"]
    assert len([t for t in tokens if t["t"] == "CHR-ERG-01"]) == 1


def test_address_popup_shadow_is_not_a_second_customer(monkeypatch, tmp_path):
    from PIL import Image, ImageGrab
    from types import SimpleNamespace
    from fakturama_full_workflow import Runner

    image = Image.open(Path(__file__).parent / "fixtures/address_popup_shadow.png")
    monkeypatch.setattr(ImageGrab, "grab", lambda bbox: image.copy())
    rect = SimpleNamespace(
        left=0,
        top=0,
        right=image.width,
        bottom=image.height,
        width=lambda: image.width,
        height=lambda: image.height,
    )
    runner = Runner.__new__(Runner)
    runner.evidence = tmp_path
    runner.note = lambda *a, **k: None
    rows = runner._dialog_rows_ocr(SimpleNamespace(rectangle=lambda: rect))
    assert len(rows) == 1
    assert "Marta Klein" in rows[0][0]


def test_line_sku_ignores_only_outer_vertical_grid_rules(order):
    actual = cells()
    actual["sku"] = "| CHR-ERG-01 |"
    assert_line_cells(actual, order.items[0])
    actual["sku"] = "XCHR-ERG-01"
    with pytest.raises(ValueError):
        assert_line_cells(actual, order.items[0])


def test_receipts_and_protection_are_workspace_scoped(order, tmp_path):
    from fakturama.execution import receipt_name, is_isolated_workspace
    from fakturama_full_workflow import Runner

    other = tmp_path / "business"
    assert receipt_name(order) != receipt_name(order, other)
    receipt = Receipt(tmp_path / "receipt.json", order, workspace=other)
    receipt.update(order_number="PO000001")
    with pytest.raises(ValueError, match="different source"):
        Receipt(receipt.path, order, workspace=tmp_path / "another")
    assert not is_isolated_workspace(other)
    assert is_isolated_workspace(
        Path(__file__).resolve().parents[1] / "evidence/assessment_isolated/business"
    )
    runner = Runner.__new__(Runner)
    assert runner._protected("INV000001")
    runner.isolated = True
    assert not runner._protected("INV000001")


def test_euro_decimal_comma_line_cells(order):
    actual = dict(
        sku="CHR-ERG-01",
        qty="2,00",
        price="250,00 EUR",
        vat="VAT 19% (19,0%)",
        discount="-10,00 %",
        total="450,00 EUR",
    )
    assert_line_cells(actual, order.items[0])
    actual["price"] = "25000,00 EUR"
    with pytest.raises(ValueError, match="wrong price"):
        assert_line_cells(actual, order.items[0])


def test_euro_cell_rejects_two_amounts(order):
    actual = cells()
    actual["price"] = "250,00 225,00 EUR"
    with pytest.raises(ValueError, match="Ambiguous price"):
        assert_line_cells(actual, order.items[0])


@pytest.mark.parametrize(
    "fixture", ["euro_two_row_selected_grid.png", "euro_two_row_hover_free_grid.png"]
)
def test_recorded_euro_two_row_selected_grid_has_unique_headers(fixture):
    from PIL import Image
    from fakturama.grid import read_grid_tokens, grid_headers
    from fakturama.ocr import configure_tesseract

    configure_tesseract()
    image = Image.open(Path(__file__).parent / "fixtures" / fixture)
    header = grid_headers(read_grid_tokens(image))
    assert header["pos"]["x"] < header["qty"]["x"] < header["sku"]["x"]
    assert header["price"]["x"] < header["discount"]["x"] < header["total"]["x"]


def test_hover_obscured_header_is_rejected_without_guessing():
    from PIL import Image
    from fakturama.grid import read_grid_tokens, grid_headers
    from fakturama.ocr import configure_tesseract

    configure_tesseract()
    image = Image.open(Path(__file__).parent / "fixtures/euro_two_row_chair_selected_grid.png")
    with pytest.raises(ValueError, match="discount not unique"):
        grid_headers(read_grid_tokens(image))


def test_recorded_selected_euro_vat_cell_readback():
    from PIL import Image
    from fakturama.grid import read_selected_cell
    from fakturama.ocr import configure_tesseract

    configure_tesseract()
    text = read_selected_cell(
        Image.open(Path(__file__).parent / "fixtures/euro_selected_vat_cell.png")
    )
    assert "19%" in text and "(19.0%)" in text


def test_recorded_euro_document_table_reads_complete_right_aligned_total(monkeypatch, tmp_path):
    from PIL import Image, ImageGrab
    from types import SimpleNamespace
    from fakturama_full_workflow import Runner
    from fakturama.ocr import configure_tesseract

    configure_tesseract()
    image = Image.open(Path(__file__).parent / "fixtures/euro_saved_order_table.png")
    monkeypatch.setattr(ImageGrab, "grab", lambda **kwargs: image.copy())
    runner = Runner.__new__(Runner)
    runner.evidence = tmp_path
    runner.window = SimpleNamespace(
        rectangle=lambda: SimpleNamespace(left=0, right=1920, bottom=1080, width=lambda: 1920)
    )
    tab = SimpleNamespace(rectangle=lambda: SimpleNamespace(bottom=700))
    rows = runner._document_table_rows(tab)
    assert len(rows) == 1
    assert rows[0][0] == "PO000001 open 678.30"
