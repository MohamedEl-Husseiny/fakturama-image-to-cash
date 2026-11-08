"""--assume-fresh-workspace: skip the Documents-list OCR (stage 00) only when the
workspace was created by this run's launcher, and never for verification/recovery."""

from types import SimpleNamespace

import pytest

from fakturama_full_workflow import GuardError, Runner


class StopAfterDiscovery(Exception):
    """Raised by the fake stage 01 so run() ends right after stage 00."""


def make_runner(*, assume, isolated=True, verify_only=False, receipt_order="", receipt_invoice=""):
    r = Runner.__new__(Runner)
    r.order = SimpleNamespace(validate=lambda: None, reference="WEB-2026-0714-A17", items=[])
    r.workspace = "C:/evidence/assessment_isolated/x/business"
    r.isolated = isolated
    r.assume_fresh_workspace = assume
    r.verify_only = verify_only
    r.receipt_order = receipt_order
    r.receipt_invoice = receipt_invoice
    r.expected_order_number = ""
    r.receipt = SimpleNamespace(assert_save_outcome_resolved=lambda records: None)
    r.calls = []
    r.notes = []
    r.note = lambda stage, **data: r.notes.append((stage, data))
    r.stage = lambda name, fn: (r.calls.append(name), fn())[1]
    r._dismiss_customer_history = lambda: None
    r._dismiss_known_selector = lambda: None
    r._documents = lambda: (r.calls.append("OCR"), {"order": None, "invoice": None})[1]
    r._order_draft_tabs = lambda: []

    def stop():
        raise StopAfterDiscovery()

    r.launch_order = stop
    return r


def test_fresh_workspace_skips_documents_ocr():
    r = make_runner(assume=True)
    with pytest.raises(StopAfterDiscovery):
        r.run()
    assert "00_fresh_workspace_assumed" in r.calls
    assert "OCR" not in r.calls
    assert r.notes[0][0] == "documents_assumed_empty"
    assert r.calls[-1] == "01_order_open"


def test_default_still_runs_documents_ocr():
    r = make_runner(assume=False)
    with pytest.raises(StopAfterDiscovery):
        r.run()
    assert r.calls[:2] == ["00_discover_saved_documents", "OCR"]


def test_refused_with_verify_existing():
    with pytest.raises(GuardError, match="verify-existing"):
        make_runner(assume=True, verify_only=True).run()


def test_refused_with_recovery_receipt():
    with pytest.raises(GuardError, match="recovery receipt"):
        make_runner(assume=True, receipt_order="PO000001").run()


def test_refused_outside_isolated_workspace():
    with pytest.raises(GuardError, match="assessment_isolated"):
        make_runner(assume=True, isolated=False).run()
