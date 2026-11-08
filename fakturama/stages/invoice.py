"""Stages 08-10: Order Save and reopen verification, follow-up Invoice, payment, linked-pair verification."""

from __future__ import annotations

import re
import time

from fakturama.order_ui import interpret_date
from fakturama.execution import (
    assert_document_snapshot,
)

from fakturama.stages.common import GuardError, normalized, decimal_from_ui, within, midpoint


class InvoiceMixin:
    """Stages 08-10: Order Save and reopen verification, follow-up Invoice, payment, linked-pair verification."""

    def save_order_and_verify(self):
        self.save_once("New Order")
        self.order_saved = True
        self.receipt.update(order_number=self.order_number, phase="order_saved")
        self.verify_saved_order()

    def _check_document_row(self, entry, number, state):
        """PDF 4.5 / 5.5: the Documents row shows the generated number, the expected
        state and the source Total."""
        if entry is None or entry["number"] != number:
            raise GuardError(f"Saved {number} not uniquely present for source reference")
        if not re.search(r"\b" + state + r"\b", entry["text"], re.I):
            raise GuardError(
                f'{number}: Documents state not verified as {state}: {entry["text"]!r}'
            )
        if f"{self.order.source_gross:.2f}" not in entry["text"].replace(",", ""):
            raise GuardError(f"{number}: Documents amount mismatch: {entry['text']!r}")

    def verify_saved_order(self, *, repair=False, reopen=False):
        """Fresh path (PDF 4.5): read the still-open saved editor's header and totals,
        then confirm the Order row in Data > Documents. ``reopen=True`` is the
        re-run/recovery path: the record is reopened from Documents and its
        addresses and lines are verified again."""
        if reopen:
            self._open_persisted(self.order_number, reopen=True)
            self.delivery_repair_pending = False
            self._assert_addresses(repair_delivery=repair)
            if self.delivery_repair_pending:
                if self.verify_only:
                    raise GuardError("Verification-only mode cannot repair saved delivery")
                self.save_once("New Order")
                self._open_persisted(self.order_number, reopen=True)
                self.delivery_repair_pending = False
                self._assert_addresses()
        actual = self._document_snapshot()
        try:
            assert_document_snapshot(actual, self.order, self.order_number)
        except ValueError as exc:
            raise GuardError(str(exc)) from exc
        if reopen:
            self._verify_all_lines()
        records = self._documents(category="Orders")
        self._check_document_row(records["order"], self.order_number, "open")
        self.receipt.update(order_number=self.order_number, phase="order_verified")
        self.note("order_persisted_verified", snapshot=actual, documents_row=records["order"])

    def _confirm_invoice_copied_from_order(self, scope):
        """PDF 5.1: before touching payment, confirm the proposed numbers/dates are
        untouched and that Cust.Ref., addresses, Order Date, VAT mode, lines and
        totals were copied from the Order."""
        for label, key in [("Date", "invoice_date"), ("Service date", "service_date")]:
            value = interpret_date(self.app.field(label, "Edit", scope).get_value()).isoformat()
            if self.receipt.data.get(key) and value != self.receipt.data[key]:
                raise GuardError(f"Proposed Invoice {label} changed")
        actual = self._document_snapshot(invoice=True)
        try:
            assert_document_snapshot(
                actual, self.order, self.invoice_number, invoice=True, before_payment=True
            )
        except ValueError as exc:
            raise GuardError(str(exc)) from exc
        self._assert_addresses(invoice=True)
        self._verify_all_lines()
        self.note("invoice_copied_from_order_verified", snapshot=actual)

    def verify_saved_invoice(self, *, reopen=False):
        """PDF 5.5: the Invoice and Order rows in Data > Documents show the expected
        states and the source Total, and the Order is still open. ``reopen=True`` is
        the re-run/recovery path, which reopens the Invoice and re-verifies it."""
        if reopen:
            self._open_persisted(self.invoice_number, reopen=True)
            scope = self.editor("New Invoice")
            self._confirm_invoice_copied_from_order(scope)
        actual = self._document_snapshot(invoice=True)
        try:
            assert_document_snapshot(actual, self.order, self.invoice_number, invoice=True)
        except ValueError as exc:
            raise GuardError(str(exc)) from exc
        # Read the Invoices and Orders categories; "This transaction" is not unique right after a Save.
        records = self._documents()
        self._check_document_row(records["order"], self.order_number, "open")
        self._check_document_row(
            records["invoice"], self.invoice_number, "paid" if self.order.paid else "open"
        )
        if not any(
            normalized(t.window_text().lstrip("*")) == normalized(self.order_number)
            for t in self._upper_tabs()
        ):
            raise GuardError(f"Source Order {self.order_number} is no longer open")
        self.receipt.update(invoice_number=self.invoice_number, phase="complete")
        self.note("linked_pair_persisted_verified", snapshot=actual, records=records)

    def create_linked_invoice(self):
        # The toolbar Invoice button would create an unlinked Invoice (PDF 4.6).
        self._activate_saved_order()
        if any(
            normalized(t.window_text().lstrip("*")) == "new invoice" for t in self._upper_tabs()
        ):
            raise GuardError("An untracked New Invoice is already open; refusing another follow-up")
        buttons = self.app.controls(kind="Button", name="Invoice")
        # The button has to be inside the "Create a follow-up document" group.
        groups = self.app.controls(kind="Group", name="Create a follow-up document")
        if len(groups) != 1:
            raise GuardError("Saved Order follow-up group not visible")
        r = groups[0].rectangle()
        choices = [b for b in buttons if within(r, *midpoint(b.rectangle()))]
        if len(choices) != 1 or not choices[0].is_enabled():
            raise GuardError("Linked follow-up Invoice button unavailable")
        choices[0].click_input()
        time.sleep(0.6)
        self.editor("New Invoice")
        scope = self.editor("New Invoice")
        ref = self.app.field("Cust.Ref.", "Edit", scope).get_value()
        if ref.strip() != self.order.reference:
            raise GuardError("New Invoice Cust.Ref. not carried over from Order")
        self._invoice_totals(scope)
        self.invoice_number = self.app.field("No.", "Edit", scope).get_value().strip()
        if self._protected(self.invoice_number):
            raise GuardError("Proposed Invoice number is protected")
        self.receipt.update(
            invoice_number=self.invoice_number,
            follow_up_order=self.order_number,
            invoice_date=interpret_date(
                self.app.field("Date", "Edit", scope).get_value()
            ).isoformat(),
            service_date=interpret_date(
                self.app.field("Service date", "Edit", scope).get_value()
            ).isoformat(),
            phase="invoice_draft",
        )
        self.note("linked_invoice_created", order=self.order_number, invoice=self.invoice_number)

    def _activate_saved_order(self):
        self._activate_document(self.order_number)
        self._scroll_document(top=True)

    def _invoice_totals(self, scope):
        for label, amount in [
            ("Total Net", self.order.source_net),
            ("VAT", self.order.source_vat),
            ("Total", self.order.source_gross),
        ]:
            self.money_field(label, amount, scope)

    def _invoice_payment_controls(self, scope):
        """Anchor invoice payment controls to its Paid row (Fig. 9-10)."""
        candidates = [
            c
            for c in self.app.controls(kind="CheckBox", scope=scope)
            if normalized(c.window_text()) in ("paid", "paid status", "paid in full")
        ]
        if len(candidates) != 1:
            labels = [
                c
                for c in self.app.controls(kind="Text", scope=scope)
                if normalized(c.window_text()) == "paid"
            ]
            if len(labels) == 1:
                lab = labels[0].rectangle()
                candidates = [
                    c
                    for c in self.app.controls(kind="CheckBox", scope=scope)
                    if abs(midpoint(c.rectangle())[1] - midpoint(lab)[1]) < 22
                    and abs(c.rectangle().left - lab.right) < 75
                ]
        if len(candidates) != 1:
            raise GuardError("Paid checkbox not uniquely grounded in linked Invoice")
        paid = candidates[0]
        py = midpoint(paid.rectangle())[1]
        combos = [
            c
            for c in self.app.controls(kind="ComboBox", scope=scope)
            if abs(midpoint(c.rectangle())[1] - py) < 27
            and c.rectangle().left > paid.rectangle().right
        ]
        if len(combos) != 1:
            raise GuardError("Invoice payment method dropdown ambiguous")
        if not paid.get_toggle_state():
            return paid, combos[0], None, None
        # Pay Date and Value may or may not carry UIA names.
        date = None
        for label in ("Payment Date", "Pay Date", "Payment date", "Pay date"):
            try:
                date = self.app.field(label, "Edit", scope)
                break
            except Exception:
                continue
        if date is None:
            # The date sits between the payment combo and the Value field on the same row.
            relevant = [
                c
                for c in self.app.controls(kind="Edit", scope=scope)
                if abs(midpoint(c.rectangle())[1] - py) < 27
                and c.rectangle().left > combos[0].rectangle().right
                and c.rectangle().width() >= 100
                and c.rectangle().width() <= 175
            ]
            if len(relevant) != 1:
                raise GuardError("Linked Invoice Pay Date field not identifiable")
            date = relevant[0]
        value = self.app.field("Value", "Edit", scope)
        return paid, combos[0], date, value

    def complete_invoice(self):
        scope = self.editor("New Invoice")
        self._invoice_totals(scope)
        self._confirm_invoice_copied_from_order(scope)  # PDF 5.1
        # The line check scrolled the editor; back to the top and re-take the bounds.
        self._scroll_document(top=True)
        scope = self.editor("New Invoice")
        paid, method, date, value = self._invoice_payment_controls(scope)
        self.app.combo_choose(method, self.order.payment_method)
        if self.order.paid:
            if not paid.get_toggle_state():
                paid.click_input()
            time.sleep(0.3)
            paid, method, date, value = self._invoice_payment_controls(scope)
            if not paid.get_toggle_state():
                raise GuardError("Invoice paid status did not commit")
            focus = self.app.field("Cust.Ref.", "Edit", scope)
            try:
                current_payment_date = interpret_date(date.get_value())
            except Exception:
                current_payment_date = None
            if current_payment_date != self.order.payment_date:
                self.app._set_order_date_by_uia(date, self.order.payment_date, focus)
            self.edit_number(value, self.order.source_gross)
            if decimal_from_ui(value.get_value()) != self.order.source_gross:
                raise GuardError("Invoice paid amount is not the full invoice total")
        elif paid.get_toggle_state():
            raise GuardError("Invoice unexpectedly marked PAID for unpaid source")
        self._invoice_totals(scope)
        self.save_once("New Invoice")
        self.invoice_saved = True
        self.receipt.update(invoice_number=self.invoice_number, phase="invoice_saved")
        self.verify_saved_invoice()
