from types import SimpleNamespace

import pytest
from PIL import Image

from fakturama_full_workflow import GuardError, Runner


def _runner(frames, monkeypatch):
    monkeypatch.setattr("fakturama.stages.documents.time.sleep", lambda seconds: None)
    runner = Runner.__new__(Runner)
    it = iter(frames)
    runner.captures = []
    runner.ocr_images = []

    def capture(tab):
        image = next(it)
        runner.captures.append(image)
        return image

    runner._document_table_capture = capture
    runner._document_table_rows = lambda tab, image=None: runner.ocr_images.append(image) or [
        ("PO000001 open 678.30", 10)
    ]
    return runner


def _frame(fill):
    return Image.new("RGB", (6, 3), fill)


def test_identical_captures_lead_to_a_single_ocr_pass(monkeypatch):
    runner = _runner([_frame("white"), _frame("white")], monkeypatch)
    rows = runner._stable_document_rows(SimpleNamespace())
    assert rows == [("PO000001 open 678.30", 10)]
    assert len(runner.captures) == 2
    assert runner.ocr_images == [runner.captures[1]]


def test_changing_table_waits_for_two_identical_captures(monkeypatch):
    runner = _runner([_frame("white"), _frame("grey"), _frame("grey")], monkeypatch)
    runner._stable_document_rows(SimpleNamespace())
    assert len(runner.captures) == 3
    assert len(runner.ocr_images) == 1


def test_table_that_never_settles_stops_without_ocr(monkeypatch):
    frames = [_frame((i, i, i)) for i in range(8)]
    runner = _runner(frames, monkeypatch)
    with pytest.raises(GuardError, match="did not stabilize"):
        runner._stable_document_rows(SimpleNamespace())
    assert runner.ocr_images == []


def test_journal_replace_retries_transient_permission_error(tmp_path, monkeypatch):
    from pathlib import Path

    from fakturama.evidence import ExecutionJournal

    journal = ExecutionJournal(tmp_path / "run")
    monkeypatch.setattr("fakturama.evidence.time.sleep", lambda s: None)
    calls = {"n": 0}
    real = Path.replace

    def flaky(self, target):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError(5, "Access is denied")
        return real(self, target)

    monkeypatch.setattr(Path, "replace", flaky)
    journal.record("01_order_open", status="PASS")
    assert calls["n"] == 3
    assert (tmp_path / "run" / "execution_report.json").exists()


def test_journal_replace_gives_up_after_persistent_lock(tmp_path, monkeypatch):
    from pathlib import Path

    from fakturama.evidence import ExecutionJournal

    journal = ExecutionJournal(tmp_path / "run")
    monkeypatch.setattr("fakturama.evidence.time.sleep", lambda s: None)
    monkeypatch.setattr(
        Path, "replace", lambda self, t: (_ for _ in ()).throw(PermissionError(5, "denied"))
    )
    with pytest.raises(PermissionError):
        journal.record("01_order_open", status="PASS")
