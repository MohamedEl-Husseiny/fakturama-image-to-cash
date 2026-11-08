"""Offline regressions; these do not verify the live SWT application."""

from types import SimpleNamespace

import pytest

from fakturama_full_workflow import GuardError, Runner
from fakturama.models import Address


def test_dropdown_restores_owner_before_opening(monkeypatch):
    from fakturama.ui import FakturamaUI, UIError

    app = FakturamaUI.__new__(FakturamaUI)
    calls = []
    app.ensure_foreground = lambda: calls.append("restore owner")

    def expand():
        assert calls == ["restore owner", "focus combo"]
        raise UIError("popup test barrier")

    combo = SimpleNamespace(
        selected_text=lambda: "Mutually defined",
        set_focus=lambda: calls.append("focus combo"),
        expand=expand,
    )
    with pytest.raises(UIError, match="popup test barrier"):
        app.combo_choose(combo, "Credit transfer")


def test_foreground_failure_prevents_dropdown_input(monkeypatch):
    import win32gui, win32process
    from fakturama.ui import FakturamaUI, UIError

    app = FakturamaUI.__new__(FakturamaUI)
    app.pid = 55
    calls = []
    app.window = SimpleNamespace(
        is_enabled=lambda: True,
        set_focus=lambda: calls.append("restore"),
        rectangle=lambda: Rect(0, 0, 1200, 900),
        click_input=lambda **kwargs: None,
    )
    monkeypatch.setattr(win32gui, "GetForegroundWindow", lambda: 123)
    monkeypatch.setattr(win32process, "GetWindowThreadProcessId", lambda handle: (1, 99))
    combo = SimpleNamespace(
        selected_text=lambda: "Mutually defined",
        set_focus=lambda: pytest.fail("must not touch combo"),
    )
    with pytest.raises(UIError, match="could not regain foreground"):
        app.combo_choose(combo, "Credit transfer")
    assert calls == ["restore"]


def _product_runner(opened, clicked, buttons, left_panel_works, monkeypatch):
    monkeypatch.setattr("fakturama.stages.product.time.sleep", lambda s: None)
    runner = Runner.__new__(Runner)
    runner.app = SimpleNamespace(
        tabs=lambda title: [object()] if opened else [],
        controls=lambda **kwargs: buttons,
    )
    runner._list_tab = lambda name: control(400, 650, 500, 675)
    runner.editor = lambda name: "new Product scope"
    runner._visible_app_dialogs = lambda: []
    runner.note = lambda *a, **k: None

    def left_panel(name):
        clicked.append("left:" + name)
        if left_panel_works:
            opened.append(True)

    runner._click_text_navigation = left_panel
    return runner


def test_new_product_prefers_left_panel_shortcut(monkeypatch):
    clicked, opened = [], []
    runner = _product_runner(opened, clicked, [], True, monkeypatch)
    assert runner._product_creation_scope(SimpleNamespace(sku="SKU")) == "new Product scope"
    assert clicked == ["left:New product"]


def test_new_product_falls_back_to_products_list_add(monkeypatch):
    clicked, opened = [], []

    def button(y, name):
        return SimpleNamespace(
            rectangle=lambda: Rect(100, y, 120, y + 20),
            click_input=lambda: (clicked.append(name), opened.append(True)),
        )

    buttons = [button(80, "global"), button(650, "Products Add")]
    runner = _product_runner(opened, clicked, buttons, False, monkeypatch)
    assert runner._product_creation_scope(SimpleNamespace(sku="SKU")) == "new Product scope"
    assert clicked == ["left:New product", "Products Add"]


def test_new_product_stops_when_shortcut_raises_a_dialog(monkeypatch):
    clicked, opened = [], []
    runner = _product_runner(opened, clicked, [], False, monkeypatch)
    runner._visible_app_dialogs = lambda: [SimpleNamespace(window_text=lambda: "Error")]
    with pytest.raises(GuardError, match="raised a dialog"):
        runner._product_creation_scope(SimpleNamespace(sku="SKU"))


def test_new_product_refuses_ambiguous_list_add_buttons(monkeypatch):
    clicked, opened = [], []
    buttons = [control(100, 650, 120, 670)] * 2
    runner = _product_runner(opened, clicked, buttons, False, monkeypatch)
    with pytest.raises(GuardError, match="Add button is not unique"):
        runner._product_creation_scope(SimpleNamespace(sku="SKU"))


@pytest.mark.parametrize(
    "display,expected",
    [
        ("29.750,00 EUR", "297,50"),
        ("0,00 €", "297,50"),
        ("0.00 USD", "297.50"),
        ("1,234.00 USD", "297.50"),
    ],
)
def test_numeric_entry_uses_observed_decimal_mark(display, expected):
    from fakturama_full_workflow import number_for_ui

    assert number_for_ui("297.50", display) == expected


@pytest.mark.parametrize("display", ["", "EUR", "0,00 / 1,00"])
def test_numeric_entry_refuses_ungrounded_or_multiple_values(display):
    from fakturama_full_workflow import number_for_ui

    with pytest.raises(GuardError):
        number_for_ui("297.50", display)


def test_mixed_locale_grid_inputs_follow_each_cell():
    from fakturama_full_workflow import number_for_ui

    assert number_for_ui(2, "| 200.00") == "2.00"
    assert number_for_ui(250, "250,00 EUR") == "250,00"
    assert number_for_ui(10, "| {000.00 %") == "10.00"


def test_product_reselection_activates_order_before_grid_read():
    runner = Runner.__new__(Runner)
    calls = []
    runner.editor = lambda title: calls.append(title)

    def grid():
        assert calls == ["New Order"]
        raise GuardError("grid barrier")

    runner._grid_sku_rows = grid
    with pytest.raises(GuardError, match="grid barrier"):
        runner._product_search(SimpleNamespace(sku="CHR-ERG-01"))


def test_fresh_product_price_uses_order_currency_despite_initial_default_zero():
    from decimal import Decimal

    runner = Runner.__new__(Runner)
    runner.editor = lambda title: "order"
    runner._source_identity = lambda scope: None
    runner._product_creation_scope = lambda item: "product"
    currency = SimpleNamespace(get_value=lambda: "450,00 EUR")
    initial_zero = SimpleNamespace(get_value=lambda: "0.00 EUR")
    runner.app = SimpleNamespace(
        field=lambda label, kind, scope: currency if label == "Total Net" else initial_zero
    )
    calls = []
    runner.edit_number = lambda ctrl, value, **kwargs: calls.append(kwargs.get("display"))
    runner.fill = lambda *args: None
    runner.select_combo = lambda *args: None
    runner.money_field = lambda *args: None
    runner.save_once = lambda *args: None
    runner._create_product(
        SimpleNamespace(
            sku="MAT-DESK-02",
            description="Mat",
            gross_master_price=Decimal("47.60"),
            vat_percent=Decimal(19),
        )
    )
    assert calls == ["450,00 EUR"]


class Rect:
    def __init__(self, left, top, right, bottom):
        self.left, self.top, self.right, self.bottom = left, top, right, bottom

    def width(self):
        return self.right - self.left

    def height(self):
        return self.bottom - self.top


def control(*bounds):
    return SimpleNamespace(rectangle=lambda: Rect(*bounds))


def address_runner(edits, dx=0, dy=0):
    runner = Runner.__new__(Runner)
    anchors = {
        ("Text", "Addresses"): [control(414 + dx, 276 + dy, 483 + dx, 296 + dy)],
        ("Text", "Items"): [control(410 + dx, 430 + dy, 450 + dx, 450 + dy)],
        ("Edit", None): edits,
    }
    runner.app = SimpleNamespace(
        controls=lambda kind, name=None, scope=None: anchors.get((kind, name), [])
    )
    return runner


@pytest.mark.parametrize("dx,dy", [(0, 0), (117, 83)])
def test_address_reader_accepts_historical_108_pixel_box(dx, dy):
    box = control(488 + dx, 301 + dy, 976 + dx, 409 + dy)
    short_stale = control(488 + dx, 301 + dy, 976 + dx, 326 + dy)
    remarks = control(474 + dx, 477 + dy, 1050 + dx, 627 + dy)
    runner = address_runner([box, short_stale, remarks], dx, dy)
    assert runner._order_address_control(None) is box


def test_address_reader_rejects_two_multiline_candidates():
    runner = address_runner([control(488, 301, 976, 409), control(488, 301, 976, 410)])
    with pytest.raises(GuardError, match="candidate_count=2"):
        runner._order_address_control(None)


def test_missing_preserved_draft_never_clicks_new_order():
    runner = Runner.__new__(Runner)
    runner.expected_order_number = "PO000008"
    runner._dismiss_known_selector = lambda: None
    runner._order_draft_tabs = lambda: []
    # No app adapter supplied: attempting a toolbar click would fail this test.
    with pytest.raises(GuardError, match="Refusing to create a replacement"):
        runner.launch_order()


def test_address_requires_warehouse_company_and_country_too():
    address = Address(
        "Northstar Office Warehouse", "Beusselstrasse 44", "10553", "Berlin", "Germany"
    )
    correct = (
        "Northstar Office Warehouse\r\nMarta Klein\r\n"
        "Beusselstrasse 44\r\nDE-10553 Berlin\r\nGermany"
    )
    assert Runner._address_text_matches(correct, address)
    assert not Runner._address_text_matches(
        correct.replace("Northstar Office Warehouse", "Northstar Office GmbH"), address
    )
    assert not Runner._address_text_matches(correct.replace("Germany", "France"), address)


def test_owned_product_selector_is_cancelled_even_when_top_level_uia_omits_it(monkeypatch):
    runner = Runner.__new__(Runner)
    clicked = []
    button = SimpleNamespace(
        element_info=SimpleNamespace(control_type="Button"),
        window_text=lambda: "Cancel",
        rectangle=lambda: Rect(1, 2, 3, 4),
        click_input=lambda: clicked.append(True),
    )
    popup = SimpleNamespace(
        handle=123, window_text=lambda: "Select a product", descendants=lambda: [button]
    )
    runner._visible_app_dialogs = lambda: []
    runner._modal_if_open = lambda name: popup if name == "Select a product" else None
    runner.note = lambda *args, **kwargs: None
    monkeypatch.setattr("fakturama_full_workflow.time.sleep", lambda seconds: None)
    assert runner._dismiss_known_selector()
    assert clicked == [True]


def test_existing_payment_is_selected_without_creating_master_in_debtor():
    runner = Runner.__new__(Runner)
    runner.order = SimpleNamespace(payment_method="Bank Transfer")
    combo = SimpleNamespace(selected_text=lambda: "Bank Transfer")
    runner.app = SimpleNamespace(field=lambda *a: combo)
    runner._editor_inner_tab = lambda name: None
    runner.editor = lambda name: "scope"
    runner._native_payment_choices = lambda c: ["Cash", "Bank Transfer"]
    runner.select_combo = lambda *args: None
    runner.note = lambda *args, **kwargs: None
    runner._ensure_debtor_payment()


def test_payment_master_is_resolved_before_any_debtor_draft_or_creation():
    runner = Runner.__new__(Runner)
    events = []

    class StopAfterOrdering(Exception):
        pass

    def tabs():
        events.append("debtor_drafts")
        raise StopAfterOrdering()

    runner._upper_tabs = tabs
    runner.ensure_payment = lambda: events.append("payment_master")
    runner.stage = lambda name, fn: fn()
    with pytest.raises(StopAfterOrdering):
        runner._create_debtor()
    assert events == ["payment_master", "debtor_drafts"]


def test_stale_payment_dropdown_stops_before_saving_or_replacing_debtor():
    runner = Runner.__new__(Runner)
    runner.order = SimpleNamespace(payment_method="Bank Transfer")
    runner.app = SimpleNamespace(field=lambda *a: object())
    runner._editor_inner_tab = lambda name: None
    runner.editor = lambda name: "scope"
    runner._native_payment_choices = lambda c: ["Cash"]
    runner.ensure_payment = lambda: None
    runner.stage = lambda name, fn: fn()
    with pytest.raises(GuardError, match="same open Debtor"):
        runner._ensure_debtor_payment()


def test_protected_number_cannot_be_saved_via_new_order_alias():
    runner = Runner.__new__(Runner)
    runner.allow_save = True
    runner.order_number = "PO000008"
    with pytest.raises(GuardError, match="verification-only"):
        runner.save_once("New Order")


def test_fresh_flat_documents_table_is_captured_twice_and_ocrd_once(monkeypatch):
    from PIL import Image
    from pywinauto.controls import win32_controls

    written = []
    monkeypatch.setattr(
        win32_controls, "EditWrapper", lambda handle: SimpleNamespace(set_edit_text=written.append)
    )
    monkeypatch.setattr("fakturama.stages.documents.time.sleep", lambda seconds: None)
    runner = Runner.__new__(Runner)
    runner.order = SimpleNamespace(reference="NEW-SOURCE")
    runner.window = SimpleNamespace(
        is_enabled=lambda: True, rectangle=lambda: Rect(0, 0, 1200, 900)
    )
    tab = SimpleNamespace(rectangle=lambda: Rect(200, 500, 300, 520))
    runner._list_tab = lambda name: tab
    runner.app = SimpleNamespace(
        field=lambda *a: SimpleNamespace(handle=42), controls=lambda **k: []
    )
    runner._focus_edit_for_input = lambda control: None
    runner._read_modal_search_value = lambda c: "NEW-SOURCE"
    captures = []
    runner._document_table_capture = lambda tab: captures.append(True) or Image.new("RGB", (4, 4))
    ocr = []
    runner._document_table_rows = lambda tab, image=None: ocr.append(image) or []
    runner.evidence_shot = lambda label: None
    runner.note = lambda *a, **k: None
    assert runner._documents() == {"order": None, "invoice": None}
    assert written == ["NEW-SOURCE"]
    assert len(captures) == 2 and len(ocr) == 1 and ocr[0] is not None


@pytest.mark.parametrize(
    "text,roles,expected",
    [
        ("Invoice address", ["Invoice address"], True),
        ("Invoice address Delivery address", ["Invoice address"], False),
        ("Delivery address", ["Delivery address"], True),
        ("Invoice address", ["Invoice address", "Delivery address"], False),
        ("Invoice address Delivery address", ["Invoice address", "Delivery address"], True),
        ("", ["Invoice address"], False),
    ],
)
def test_role_readback_requires_exact_set(text, roles, expected):
    assert Runner._role_text_matches(text, roles) == expected


def test_edit_focus_guard_rejects_another_foreground_process(monkeypatch):
    import win32gui, win32process

    runner = Runner.__new__(Runner)
    runner.pid = 123
    runner.window = SimpleNamespace(set_focus=lambda: None, is_enabled=lambda: True)
    runner.app = SimpleNamespace(ensure_foreground=lambda: None)
    monkeypatch.setattr(win32gui, "GetForegroundWindow", lambda: 456)
    monkeypatch.setattr(win32process, "GetWindowThreadProcessId", lambda hwnd: (1, 999))
    with pytest.raises(GuardError, match="nothing typed"):
        runner._focus_edit_for_input(SimpleNamespace(click_input=lambda: None))


@pytest.mark.parametrize(
    "sku,name,description,valid",
    [
        ("MAT-DESK-02", "Anti-Fatigue Desk Mat", "", True),
        ("MAT-DESK-02", "Anti-Fatigue Desk Mat", "Anti-Fatigue Desk Mat", True),
        ("OTHER", "Anti-Fatigue Desk Mat", "", False),
        ("", "Anti-Fatigue Desk Mat", "", False),
        ("MAT-DESK-02", "Unrelated product", "", False),
    ],
)
def test_product_resume_requires_exact_identity(sku, name, description, valid):
    runner = Runner.__new__(Runner)
    values = {"Item Number": sku, "Name": name, "Description": description}
    runner.app = SimpleNamespace(
        tabs=lambda title: [SimpleNamespace(window_text=lambda: "*New product")],
        field=lambda label, *a: SimpleNamespace(get_value=lambda: values[label]),
    )
    runner.editor = lambda title: "scope"
    runner.note = lambda *a, **k: None
    item = SimpleNamespace(sku="MAT-DESK-02", description="Anti-Fatigue Desk Mat")
    if valid:
        assert runner._product_creation_scope(item) == "scope"
    else:
        with pytest.raises(GuardError, match="nothing overwritten"):
            runner._product_creation_scope(item)


def test_edit_focus_guard_rejects_modal_blocked_editor():
    runner = Runner.__new__(Runner)
    runner.window = SimpleNamespace(is_enabled=lambda: False)
    with pytest.raises(GuardError, match="modal dialog; nothing typed"):
        runner._focus_edit_for_input(object())


def test_internal_error_is_not_reported_as_automatic_product_insertion():
    runner = Runner.__new__(Runner)
    message = SimpleNamespace(
        element_info=SimpleNamespace(control_type="Text"),
        window_text=lambda: "Currency mismatch: EUR/USD",
    )
    runner._modal_if_open = lambda title: SimpleNamespace(descendants=lambda: [message])
    with pytest.raises(GuardError, match="Fakturama Internal Error: Currency mismatch: EUR/USD"):
        runner._raise_if_application_error()


def test_no_internal_error_allows_normal_selector_close_verification():
    runner = Runner.__new__(Runner)
    runner._modal_if_open = lambda title: None
    runner._raise_if_application_error()


def test_lower_table_crop_is_independent_of_tab_horizontal_position(monkeypatch):
    from PIL import ImageGrab, Image

    runner = Runner.__new__(Runner)
    runner.window = SimpleNamespace(rectangle=lambda: Rect(0, 0, 1200, 900))
    captures = []

    def grab(*, bbox):
        captures.append(bbox)
        return Image.new("RGB", (bbox[2] - bbox[0], bbox[3] - bbox[1]))

    monkeypatch.setattr(ImageGrab, "grab", grab)
    runner._parse_list_rows = lambda *args: []
    for x in (280, 590):
        runner._lower_list_rows(SimpleNamespace(rectangle=lambda x=x: Rect(x, 550, x + 100, 575)))
    assert captures[0] == captures[1]
    assert captures[0][0] < 280


def test_debtor_inner_tab_waits_for_refresh_and_deduplicates_same_bounds(monkeypatch):
    monkeypatch.setattr("fakturama_full_workflow.time.sleep", lambda seconds: None)
    runner = Runner.__new__(Runner)
    scope = Rect(300, 100, 1200, 650)
    runner.editor = lambda name: scope
    clicked = []
    tab = SimpleNamespace(
        rectangle=lambda: Rect(400, 350, 500, 375), click_input=lambda: clicked.append(True)
    )
    answers = iter([[], [tab, tab]])

    def controls(**kwargs):
        assert kwargs["scope"] is scope
        return next(answers)

    runner.app = SimpleNamespace(controls=controls)
    runner._editor_inner_tab("Addresses")
    assert clicked == [True]


def test_debtor_inner_tab_rejects_distinct_targets():
    runner = Runner.__new__(Runner)
    runner.editor = lambda name: Rect(300, 100, 1200, 650)
    runner.app = SimpleNamespace(
        controls=lambda **kwargs: [control(400, 350, 500, 375), control(600, 350, 700, 375)]
    )
    with pytest.raises(GuardError, match="ambiguous"):
        runner._editor_inner_tab("Addresses")


@pytest.mark.parametrize(
    "saved,readonly,protected", [(True, False, False), (False, True, False), (False, False, True)]
)
def test_delivery_binding_repair_refuses_saved_readonly_or_protected(saved, readonly, protected):
    runner = Runner.__new__(Runner)
    runner.order_saved = saved
    runner.verify_only = readonly
    runner.order_number = "PO000001"
    runner._protected = lambda number: protected
    with pytest.raises(GuardError, match="editable unprotected draft"):
        runner._initialize_delivery_binding(None)


def test_delivery_binding_repair_never_writes_when_debtor_missing():
    runner = Runner.__new__(Runner)
    runner.order_saved = False
    runner.verify_only = False
    runner.order_number = "PO000001"
    runner._protected = lambda number: False
    runner.order = SimpleNamespace(
        company="Northstar Office GmbH",
        first_name="Marta",
        last_name="Klein",
        billing=SimpleNamespace(zip="10117", city="Berlin"),
    )
    runner._source_identity = lambda scope: None
    runner.click_image_action = lambda *args, **kwargs: None
    runner.find_modal = lambda title: object()
    runner.search_selector = lambda *args, **kwargs: SimpleNamespace(status="MISSING")
    cancelled = []
    runner._modal_button = lambda modal, label: cancelled.append(label)
    with pytest.raises(GuardError, match="exact existing Debtor"):
        runner._initialize_delivery_binding(None)
    assert cancelled == ["Cancel"]


@pytest.mark.parametrize(
    "foregrounds,should_capture", [([123, 123], True), ([456], False), ([123, 456], True)]
)
def test_document_capture_checks_exact_workspace_before_and_after(
    monkeypatch, foregrounds, should_capture
):
    import win32gui
    from PIL import ImageGrab
    from types import SimpleNamespace
    from fakturama_full_workflow import Runner, GuardError

    runner = Runner.__new__(Runner)
    restored = []
    captured = []
    runner.app = SimpleNamespace(ensure_foreground=lambda: restored.append(True))
    runner.window = SimpleNamespace(handle=123)
    values = iter(foregrounds)
    monkeypatch.setattr(win32gui, "GetForegroundWindow", lambda: next(values))
    picture = object()

    def grab(**kwargs):
        captured.append(kwargs)
        return picture

    monkeypatch.setattr(ImageGrab, "grab", grab)
    if foregrounds == [123, 123]:
        assert runner._capture_document_region((1, 2, 3, 4)) is picture
    else:
        with pytest.raises(GuardError, match="foreground|Foreground"):
            runner._capture_document_region((1, 2, 3, 4))
    assert bool(captured) is should_capture
    assert restored == [True]


@pytest.mark.parametrize(
    "counts,allow_absent,accepted",
    [
        ({"Orders": 1, "Invoices": 0}, True, True),
        ({"Orders": 1, "Invoices": 0}, False, False),
        ({"Orders": 0, "Invoices": 0}, True, False),
        ({"Orders": 2, "Invoices": 0}, True, False),
        ({"Orders": 1, "Invoices": 2}, True, False),
    ],
)
def test_empty_invoice_category_only_allowed_with_unique_order_category(
    counts, allow_absent, accepted
):
    from types import SimpleNamespace
    from fakturama_full_workflow import Runner, GuardError

    runner = Runner.__new__(Runner)
    runner.app = SimpleNamespace(
        controls=lambda **kw: [object() for _ in range(counts[kw["name"]])]
    )
    events = []
    runner.note = lambda *a, **kw: events.append((a, kw))
    if accepted:
        assert runner._document_category("Invoices", None, allow_absent=allow_absent) is None
        assert events[0][1]["category"] == "Invoices"
    else:
        with pytest.raises(GuardError):
            runner._document_category("Invoices", None, allow_absent=allow_absent)
