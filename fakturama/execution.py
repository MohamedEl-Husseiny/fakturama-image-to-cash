"""Recovery identity and strict persisted-document assertions (no desktop imports)."""

from dataclasses import asdict
from datetime import date
from decimal import Decimal
from enum import Enum
import hashlib
import json
from pathlib import Path
import re

PROTECTED_NUMBERS = frozenset({"PO000008", "INV000001"})


class RecoveryPhase(str, Enum):
    ORDER_DRAFT = "order_draft"
    ORDER_SAVE_ATTEMPTED = "order_save_attempted"
    ORDER_SAVED = "order_saved"
    ORDER_VERIFIED = "order_verified"
    INVOICE_DRAFT = "invoice_draft"
    INVOICE_SAVE_ATTEMPTED = "invoice_save_attempted"
    INVOICE_SAVED = "invoice_saved"
    COMPLETE = "complete"


class UnresolvedSaveError(RuntimeError):
    """A prior Save may have happened; no automatic retry is permitted."""


def source_digest(order):
    payload = json.dumps(asdict(order), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf8")).hexdigest()


class Receipt:
    """An identity hint, never proof that a desktop record is persisted."""

    def __init__(self, path, order, workspace=None):
        self.path = Path(path)
        self.data = {"source_digest": source_digest(order), "reference": order.reference}
        if workspace is not None:
            self.data["workspace"] = str(Path(workspace).resolve()).casefold()
        if self.path.exists():
            loaded = json.loads(self.path.read_text(encoding="utf8"))
            if any(loaded.get(k) != self.data[k] for k in self.data):
                raise ValueError("Recovery receipt belongs to different source data")
            self.data = loaded
        self._validate(self.data)

    @staticmethod
    def _validate(data):
        phase = RecoveryPhase(data["phase"]) if data.get("phase") else None
        for key, prefix in [("order_number", "PO"), ("invoice_number", "INV")]:
            if data.get(key) and not re.fullmatch(prefix + r"\d{6,}", data[key]):
                raise ValueError(f"Invalid recovery {key}")
        if phase is RecoveryPhase.ORDER_SAVE_ATTEMPTED and not data.get("order_number"):
            raise ValueError("Order Save attempt requires its document number")
        if phase is RecoveryPhase.INVOICE_SAVE_ATTEMPTED and (
            not data.get("invoice_number")
            or not data.get("order_number")
            or data.get("follow_up_order") != data["order_number"]
        ):
            raise ValueError("Invoice Save attempt requires the exact linked pair")

    @property
    def phase(self):
        return RecoveryPhase(self.data["phase"]) if self.data.get("phase") else None

    def update(self, **values):
        for key in (
            "source_digest",
            "reference",
            "workspace",
            "order_number",
            "invoice_number",
            "follow_up_order",
        ):
            if key in values and self.data.get(key) and values[key] != self.data[key]:
                raise ValueError(f"Recovery identity {key} cannot change")
        if "phase" in values:
            values["phase"] = RecoveryPhase(values["phase"]).value
        updated = {**self.data, **values}
        self._validate(updated)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(updated, indent=2), encoding="utf8")
        temp.replace(self.path)
        self.data = updated

    def mark_save_attempted(self, kind, number):
        if kind == "order":
            self.update(order_number=number, phase=RecoveryPhase.ORDER_SAVE_ATTEMPTED)
        elif kind == "invoice":
            self.update(invoice_number=number, phase=RecoveryPhase.INVOICE_SAVE_ATTEMPTED)
        else:
            raise ValueError("Unknown document kind")

    def assert_save_outcome_resolved(self, records):
        required = {
            RecoveryPhase.ORDER_SAVE_ATTEMPTED: ("order",),
            RecoveryPhase.ORDER_SAVED: ("order",),
            RecoveryPhase.ORDER_VERIFIED: ("order",),
            RecoveryPhase.INVOICE_DRAFT: ("order",),
            RecoveryPhase.INVOICE_SAVE_ATTEMPTED: ("order", "invoice"),
            RecoveryPhase.INVOICE_SAVED: ("order", "invoice"),
            RecoveryPhase.COMPLETE: ("order", "invoice"),
        }.get(self.phase, ())
        for kind in required:
            found = records.get(kind)
            if not found or found["number"] != self.data.get(kind + "_number"):
                raise UnresolvedSaveError(
                    f"Prior {kind} Save outcome is unresolved; no automatic Save retry or replacement"
                )


def document_number(text):
    matches = re.findall(r"(?<![A-Z0-9])(?:PO|INV)\d{6,}(?!\d)", text.upper())
    if len(matches) != 1:
        raise ValueError(f"Expected one document number: {text!r}")
    return matches[0]


def assert_document_snapshot(actual, order, number, *, invoice=False, before_payment=False):
    """Compare an editor snapshot with the source. ``before_payment`` checks only the
    fields a follow-up Invoice copies from the Order (PDF 5.1), not the payment
    fields that are entered afterwards (5.2-5.3)."""
    expected = {
        "number": number,
        "reference": order.reference,
        "order_date": order.order_date,
        "price_mode": "Net",
        "vat_mode": "With VAT",
        "net": order.source_net,
        "vat": order.source_vat,
        "gross": order.source_gross,
        "discount": Decimal("0.00"),
        "shipping": Decimal("0.00"),
    }
    if invoice and not before_payment:
        expected.update(paid=order.paid, payment_method=order.payment_method)
        if order.paid:
            expected.update(payment_date=order.payment_date, payment_value=order.source_gross)
    mismatches = {k: (expected[k], actual.get(k)) for k in expected if actual.get(k) != expected[k]}
    if mismatches:
        raise ValueError(f"Persisted document mismatch: {mismatches}")


def classify_documents(rows):
    """Reject duplicates/unknown records before an action can create a document."""
    result = {"order": None, "invoice": None}
    for text, y in rows:
        number = document_number(text)
        key = "invoice" if number.startswith("INV") else "order"
        if result[key] is not None:
            raise ValueError(f"Multiple {key} records for this reference")
        result[key] = {"number": number, "text": text, "y": y}
    return result


def is_isolated_workspace(workspace):
    if not workspace:
        return False
    root = Path(__file__).resolve().parents[1] / "evidence" / "assessment_isolated"
    target = Path(workspace).resolve()
    return root.resolve() in target.parents


def receipt_name(order, workspace=None):
    prefix = (
        ""
        if workspace is None
        else hashlib.sha256(str(Path(workspace).resolve()).casefold().encode()).hexdigest()[:12]
        + "_"
    )
    return prefix + source_digest(order)[:20] + ".json"
