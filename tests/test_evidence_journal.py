"""Offline evidence durability checks; no desktop or accounting actions."""

import json
from pathlib import Path
import pytest

from fakturama.evidence import ExecutionJournal


def test_report_preserves_event_schema_and_unicode(tmp_path):
    journal = ExecutionJournal(tmp_path)
    journal.record("address", address="Straße", amount="678,30 €")
    journal.record("verify", status="PASS")
    persisted = json.loads(journal.path.read_text(encoding="utf-8"))
    assert persisted == journal.events
    assert persisted[0]["address"] == "Straße"
    assert persisted[1]["stage"] == "verify" and persisted[1]["status"] == "PASS"
    assert "timestamp" in persisted[0]
    assert not journal.path.with_suffix(".json.tmp").exists()


def test_failed_replace_preserves_last_report_and_in_memory_state(tmp_path, monkeypatch):
    journal = ExecutionJournal(tmp_path)
    journal.record("verified", status="PASS")
    before = journal.path.read_bytes()

    def fail_replace(*args, **kwargs):
        raise OSError("simulated interrupted replacement")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError):
        journal.record("next", status="PASS")
    assert journal.path.read_bytes() == before
    assert [e["stage"] for e in journal.events] == ["verified"]


def test_existing_report_cannot_be_overwritten(tmp_path):
    journal = ExecutionJournal(tmp_path)
    journal.record("original")
    before = journal.path.read_bytes()
    with pytest.raises(FileExistsError, match="new evidence directory"):
        ExecutionJournal(tmp_path)
    assert journal.path.read_bytes() == before
