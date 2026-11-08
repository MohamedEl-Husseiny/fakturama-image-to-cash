"""Interrupted-save policies against persisted receipts; no accounting writes."""

from pathlib import Path

import pytest

from fakturama.execution import Receipt, RecoveryPhase, UnresolvedSaveError
from fakturama.models import read_order


@pytest.fixture
def receipt(tmp_path):
    order = read_order(Path(__file__).resolve().parents[1] / "examples/extracted_order.json")
    return Receipt(tmp_path / "receipt.json", order, workspace=tmp_path / "business")


@pytest.mark.parametrize("kind,number", [("order", "PO000001"), ("invoice", "INV000001")])
def test_attempt_survives_reload_and_never_authorizes_missing_record_retry(receipt, kind, number):
    receipt.update(order_number="PO000001", follow_up_order="PO000001")
    receipt.mark_save_attempted(kind, number)
    order = read_order(Path(__file__).resolve().parents[1] / "examples/extracted_order.json")
    restored = Receipt(receipt.path, order, workspace=receipt.data["workspace"])
    with pytest.raises(UnresolvedSaveError, match="no automatic Save retry"):
        restored.assert_save_outcome_resolved({"order": None, "invoice": None})
    records = {"order": {"number": "PO000001"}, "invoice": None}
    records[kind] = {"number": number}
    restored.assert_save_outcome_resolved(records)
    records[kind] = {"number": "PO999999" if kind == "order" else "INV999999"}
    with pytest.raises(UnresolvedSaveError):
        restored.assert_save_outcome_resolved(records)


def test_invoice_attempt_requires_linked_order(receipt):
    with pytest.raises(ValueError, match="exact linked pair"):
        receipt.mark_save_attempted("invoice", "INV000001")
    assert not receipt.path.exists()


def test_legacy_string_phase_is_compatible_and_invalid_phase_cannot_overwrite(receipt):
    receipt.update(order_number="PO000001", phase="order_saved")
    assert receipt.phase is RecoveryPhase.ORDER_SAVED
    before = receipt.path.read_bytes()
    with pytest.raises(ValueError):
        receipt.update(phase="unknown_phase")
    assert receipt.path.read_bytes() == before
    assert receipt.phase is RecoveryPhase.ORDER_SAVED


def test_recovery_document_identity_cannot_change(receipt):
    receipt.update(order_number="PO000001", phase=RecoveryPhase.ORDER_DRAFT)
    with pytest.raises(ValueError, match="cannot change"):
        receipt.update(order_number="PO000002")
    assert receipt.data["order_number"] == "PO000001"


def test_interrupted_receipt_replace_preserves_memory_and_last_persisted_phase(
    receipt, monkeypatch
):
    receipt.update(order_number="PO000001", phase=RecoveryPhase.ORDER_DRAFT)
    before = receipt.path.read_bytes()

    def fail(*args, **kwargs):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError):
        receipt.mark_save_attempted("order", "PO000001")
    assert receipt.path.read_bytes() == before
    assert receipt.phase is RecoveryPhase.ORDER_DRAFT


def test_unresolved_save_stops_workflow_before_replacement_creation(receipt):
    from fakturama_full_workflow import Runner

    runner = Runner.__new__(Runner)
    runner.order = read_order(Path(__file__).resolve().parents[1] / "examples/extracted_order.json")
    runner.receipt = receipt
    receipt.mark_save_attempted("order", "PO000001")
    runner._dismiss_customer_history = lambda: None
    runner._dismiss_known_selector = lambda: None
    runner._documents = lambda: {"order": None, "invoice": None}
    runner.stage = lambda name, fn: fn()
    runner.launch_order = lambda: pytest.fail("must not create another Order")
    with pytest.raises(UnresolvedSaveError):
        runner.run()


@pytest.mark.parametrize(
    "kind,title,number",
    [("order", "New Order", "PO000001"), ("invoice", "New Invoice", "INV000001")],
)
@pytest.mark.parametrize("failure", ["save_input", "receipt_write"])
def test_document_save_marker_is_durable_before_save_input(
    receipt, monkeypatch, kind, title, number, failure
):
    import json
    from types import SimpleNamespace
    from pywinauto import mouse
    from fakturama_full_workflow import Runner

    runner = Runner.__new__(Runner)
    runner.allow_save = True
    runner.order_number = "PO000001"
    runner.order_saved = False
    runner.invoice_number = "INV000001"
    runner.invoice_saved = False
    receipt.update(order_number="PO000001", follow_up_order="PO000001")
    runner.receipt = receipt
    runner.order = SimpleNamespace(billing=object())
    runner._protected = lambda number: False
    runner.editor = lambda title: None
    tab = SimpleNamespace(window_text=lambda: "*" + title)
    runner._upper_tabs = lambda: [tab]
    address_tab = SimpleNamespace(
        rectangle=lambda: SimpleNamespace(left=0, top=0, right=10, bottom=10)
    )
    runner.app = SimpleNamespace(
        editor_bounds=lambda tab: None, controls=lambda **kwargs: [address_tab]
    )
    runner._order_address_snapshot = lambda scope: ("billing", None)
    runner._address_text_matches = lambda *args: True
    runner.evidence_shot = lambda name: None
    monkeypatch.setattr(mouse, "click", lambda **kwargs: None)
    expected_phase = (
        RecoveryPhase.ORDER_SAVE_ATTEMPTED
        if kind == "order"
        else RecoveryPhase.INVOICE_SAVE_ATTEMPTED
    )
    if failure == "receipt_write":

        def fail_replace(*args, **kwargs):
            raise OSError("simulated receipt write failure")

        monkeypatch.setattr(Path, "replace", fail_replace)

    def save_input(name):
        if failure == "receipt_write":
            pytest.fail("Save input must not occur without a durable marker")
        assert receipt.phase is expected_phase
        assert json.loads(receipt.path.read_text(encoding="utf-8"))["phase"] == expected_phase.value
        raise OSError("simulated interruption at Save input")

    runner.hit_button = save_input
    with pytest.raises(
        OSError, match="at Save input" if failure == "save_input" else "receipt write failure"
    ):
        runner.save_once(title)
    assert receipt.phase is (expected_phase if failure == "save_input" else None)


@pytest.mark.parametrize(
    "phase", ["order_saved", "order_verified", "invoice_draft", "invoice_saved", "complete"]
)
def test_known_saved_records_cannot_disappear_and_authorize_replacement(receipt, phase):
    receipt.update(
        order_number="PO000001", invoice_number="INV000001", follow_up_order="PO000001", phase=phase
    )
    with pytest.raises(UnresolvedSaveError):
        receipt.assert_save_outcome_resolved({"order": None, "invoice": None})
    both = {"order": {"number": "PO000001"}, "invoice": {"number": "INV000001"}}
    receipt.assert_save_outcome_resolved(both)
