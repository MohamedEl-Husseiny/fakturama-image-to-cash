"""Fakturama Image-to-Cash: continuous, Order-first, UIA/OCR desktop automation.

Windows only; drives the Fakturama desktop UI through UI Automation, real input and
OCR. Nothing touches the database or any API. Every stage is fail-closed: an
ambiguous selector row, an unrelated draft, a mismatched total or date stops the run
before the next Save.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import logging
import os
from pathlib import Path
import re
import sys
import time
import traceback

from fakturama.models import Order, read_order
from fakturama.execution import (
    Receipt,
    PROTECTED_NUMBERS,
    is_isolated_workspace,
    receipt_name,
)
from fakturama.evidence import ExecutionJournal
from fakturama.lease import DesktopLease, DriverBusyError, desktop_lock_path

from fakturama.ui import UIError, tab_is_selected
from fakturama.stages.common import (  # noqa: F401 - re-exported for compatibility
    GuardError,
    SearchResult,
    PAYMENT_CODES,
    LOG,
    normalized,
    decimal_from_ui,
    number_for_ui,
    within,
    midpoint,
    meaningful_ocr,
)
from fakturama.stages.documents import DocumentsMixin
from fakturama.stages.controls import ControlsMixin
from fakturama.stages.selectors import SelectorsMixin
from fakturama.stages.order_header import OrderHeaderMixin
from fakturama.stages.debtor import DebtorMixin
from fakturama.stages.payment import PaymentMixin
from fakturama.stages.product import ProductMixin
from fakturama.stages.invoice import InvoiceMixin


class Runner(
    DocumentsMixin,
    ControlsMixin,
    SelectorsMixin,
    OrderHeaderMixin,
    DebtorMixin,
    PaymentMixin,
    ProductMixin,
    InvoiceMixin,
):
    """Order-first stage sequence. Behaviour lives in the stage mixins; this class owns
    lifecycle (lease, UI attach, journal, receipt), the stage() guard and run().
    """

    def __init__(
        self,
        order: Order,
        evidence: Path,
        *,
        allow_save: bool = False,
        expected_order_number: str = "",
        allow_new: bool = False,
        verify_only: bool = False,
        receipt_path=None,
        workspace=None,
        strict_assessment=False,
        assume_fresh_workspace=False,
    ):
        self._lease = DesktopLease(desktop_lock_path()).acquire()
        try:
            self._initialize(
                order,
                evidence,
                allow_save=allow_save,
                expected_order_number=expected_order_number,
                allow_new=allow_new,
                verify_only=verify_only,
                receipt_path=receipt_path,
                workspace=workspace,
                strict_assessment=strict_assessment,
                assume_fresh_workspace=assume_fresh_workspace,
            )
        except BaseException:
            self.close()
            raise

    def close(self):
        lease = getattr(self, "_lease", None)
        if lease is not None:
            lease.release()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _initialize(
        self,
        order: Order,
        evidence: Path,
        *,
        allow_save: bool = False,
        expected_order_number: str = "",
        allow_new: bool = False,
        verify_only: bool = False,
        receipt_path=None,
        workspace=None,
        strict_assessment=False,
        assume_fresh_workspace=False,
    ):
        if sys.platform != "win32":
            raise RuntimeError("UIA execution requires Windows with Fakturama open")
        from fakturama.ocr import configure_tesseract

        configure_tesseract()
        from fakturama.ui import FakturamaUI

        self.workspace = Path(workspace).resolve() if workspace else None
        self.isolated = is_isolated_workspace(self.workspace)
        self.strict_assessment = strict_assessment
        self.assume_fresh_workspace = assume_fresh_workspace
        self.app = FakturamaUI(evidence, workspace=self.workspace)
        self.desktop = self.app.desktop
        self.window = self.app.window
        self.window.maximize()
        self.window.set_focus()
        time.sleep(0.4)
        self.pid = self.app.pid
        self.order = order
        self.evidence = Path(evidence)
        self.evidence.mkdir(parents=True, exist_ok=True)
        self.allow_save = allow_save
        self.expected_order_number = expected_order_number
        self.allow_new = allow_new
        self.verify_only = verify_only
        self.receipt = Receipt(
            receipt_path or Path("evidence/receipts") / receipt_name(order, self.workspace),
            order,
            workspace=self.workspace,
        )
        self.journal = ExecutionJournal(self.evidence, logger=LOG)
        self.events = self.journal.events
        self.order_number = ""
        self.order_tab_left = None
        self.invoice_number = ""
        self.selected_products = []
        self.selected_customer = False
        self.order_saved = False
        self.invoice_saved = False
        self.delivery_repair_pending = False
        self.receipt_order = self.receipt.data.get("order_number", "")
        self.receipt_invoice = self.receipt.data.get("invoice_number", "")

    def _protected(self, number):
        return number in PROTECTED_NUMBERS and not getattr(self, "isolated", False)

    def note(self, stage: str, **data):
        self.journal.record(stage, **data)

    def evidence_shot(self, label):
        return self.app.screenshot(label)

    def stage(self, name, fn):
        LOG.info("STAGE %s", name)
        try:
            value = fn()
            self.note(name, status="PASS")
            self.evidence_shot("PASS_" + name)
            return value
        except Exception as exc:
            # Some COM exceptions stringify to '', so keep the type, repr and traceback.
            detail = f"{type(exc).__name__}: {str(exc) or repr(exc)}"
            tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            self.note(name, status="STOP", reason=detail, traceback=tb)
            LOG.error("STOP at %s: %s\n%s", name, detail, tb)
            try:
                # Modal SWT popups are missing from a window capture; take the whole desktop.
                from PIL import ImageGrab

                target = self.evidence / f"{datetime.now():%Y%m%d_%H%M%S}_STOP_{name}_DESKTOP.png"
                ImageGrab.grab(all_screens=True).save(target)
                LOG.info("Desktop screenshot: %s", target)
            except Exception as shot_exc:
                LOG.warning("Desktop screenshot failed: %r", shot_exc)
            try:
                self.evidence_shot("STOP_" + name)
            except Exception as shot_exc:
                LOG.warning("Main screenshot failed: %r", shot_exc)
            raise GuardError(detail) from exc

    def refresh(self):
        return self.app.controls()

    def editor(self, title):
        # UIA can report the unsaved Order's tab as non-visible while the editor is
        # perfectly usable, so it gets a looser lookup than other editors.
        if title == "New Order" and self.order_saved:
            tab = self._activate_document(self.order_number)
        elif title == "New Invoice" and self.invoice_saved:
            tab = self._activate_document(self.invoice_number)
        elif title == "New Order":
            candidates = self._order_draft_tabs()
            if len(candidates) != 1:
                raise GuardError(
                    "Cannot uniquely activate unsaved New Order; "
                    f"found {len(candidates)}. Upper tabs: {self._upper_tab_names()}"
                )
            tab = candidates[0]
            if not tab_is_selected(tab):
                tab.click_input()
                time.sleep(0.28)
            # A successful tab click can still leave the previous editor showing;
            # verify the Order header before trusting the activation.
            self._verified_order_header(timeout=6.0)
        else:
            tab = self.app.activate(title)
        return self.app.editor_bounds(tab)

    def _activate_document(self, title):
        deadline = time.monotonic() + 7
        while True:
            self._dismiss_customer_history()
            matches = [
                t
                for t in self._upper_tabs()
                if normalized(t.window_text().lstrip("*")) == normalized(title)
            ]
            if matches or time.monotonic() >= deadline:
                break
            time.sleep(0.2)
        if len(matches) != 1:
            raise GuardError(f"Cannot uniquely activate document {title!r}")
        r = matches[0].rectangle()
        from pywinauto import mouse

        self.window.set_focus()
        if not tab_is_selected(matches[0]):
            mouse.click(coords=(r.left + min(30, r.width() // 3), midpoint(r)[1]))
            time.sleep(0.25)
            self._scroll_document(top=True)
        deadline = time.monotonic() + 5
        scrolled = False
        while True:
            try:
                scope = self.app.editor_bounds(matches[0])
                number = self.app.field("No.", "Edit", scope).get_value().strip()
                ref = self.app.field("Cust.Ref.", "Edit", scope).get_value().strip()
                if number == title and ref == self.order.reference:
                    return matches[0]
            except UIError:
                # An already-selected editor may be scrolled past its header
                # (after a line check, say); bring the header back once.
                if not scrolled:
                    scrolled = True
                    self._scroll_document(top=True)
                    continue
            except Exception:
                pass
            if time.monotonic() >= deadline:
                break
            time.sleep(0.2)
        raise GuardError(f"Document identity did not settle: {title!r}")

    def _dismiss_customer_history(self):
        import win32gui, win32process

        native = []

        def check(hwnd, _):
            if (
                win32gui.IsWindowVisible(hwnd)
                and win32gui.GetWindowText(hwnd) == "Information"
                and win32process.GetWindowThreadProcessId(hwnd)[1] == self.pid
            ):
                native.append(hwnd)

        win32gui.EnumWindows(check, None)
        if not native:
            return
        popup = self._modal_if_open("Information")
        if popup is None:
            return
        text = " ".join(c.window_text() for c in popup.descendants())
        if not re.search(
            r"has already ordered (?:\d+ times )?in the past", normalized(text)
        ) or normalized(self.order.company) not in normalized(text):
            raise GuardError(f"Unexpected Information dialog: {text!r}")
        self._modal_button(popup, "OK")
        self.note("known_customer_history_information_dismissed")

    def _scroll_document(self, *, top=False):
        self.app.ensure_foreground()
        from pywinauto import mouse

        w = self.window.rectangle()
        tabs = self._upper_tabs()
        if not tabs:
            raise GuardError("No document editor to scroll")
        scope = self.app.editor_bounds(tabs[0])
        point = (w.right - 65, scope.top + min(145, max(55, (scope.bottom - scope.top) // 3)))
        mouse.scroll(coords=point, wheel_dist=20 if top else -4)
        time.sleep(0.35)

    def _assume_empty_documents(self):
        self.note(
            "documents_assumed_empty",
            reference=self.order.reference,
            workspace=str(self.workspace),
        )
        return {"order": None, "invoice": None}

    def run(self):
        self.order.validate()
        self._dismiss_customer_history()
        self._dismiss_known_selector()
        if getattr(self, "assume_fresh_workspace", False):
            # The launcher created this workspace seconds ago, so there is nothing to
            # discover. Not allowed for verification or recovery runs.
            if self.verify_only or self.receipt_order or self.receipt_invoice:
                raise GuardError(
                    "--assume-fresh-workspace cannot be combined with --verify-existing "
                    "or a recovery receipt"
                )
            if not self.isolated:
                raise GuardError(
                    "--assume-fresh-workspace is only allowed for a workspace under "
                    "evidence/assessment_isolated/ created by scripts/launch_assessment_workspace.py"
                )
            records = self.stage("00_fresh_workspace_assumed", self._assume_empty_documents)
        else:
            records = self.stage("00_discover_saved_documents", self._documents)
        self.receipt.assert_save_outcome_resolved(records)
        if records["invoice"] and not records["order"]:
            raise GuardError("Invoice exists without one exact source Order; no creation allowed")
        if records["order"]:
            self.order_number = records["order"]["number"]
            self.order_saved = True
            if self.receipt_order and self.receipt_order != self.order_number:
                raise GuardError("Saved Order differs from recovery receipt")
            if self.expected_order_number and self.order_number != self.expected_order_number:
                raise GuardError("Saved Order differs from requested number")
            self.stage(
                "08_verify_existing_order",
                lambda: self.verify_saved_order(
                    repair=not records["invoice"]
                    and not self._protected(self.order_number)
                    and not self.verify_only
                    and not self.strict_assessment,
                    reopen=True,
                ),
            )
        else:
            if self.verify_only:
                raise GuardError("No saved source Order exists")
            if self.receipt_order and not self._order_draft_tabs():
                raise GuardError(
                    "Receipt has an Order identity but no saved row or draft; refusing replacement"
                )
            self.stage("01_order_open", self.launch_order)
            self.stage("02_header", self.prepare_header)
            self.stage("03_order_debtor_lookup", self.check_customer_first)
            self.stage("05_debtor_select_or_create", self.ensure_customer)
            # A row that is not one of ours means this is not the editor we think it is.
            rows = self._grid_sku_rows()
            if any(s not in [i.sku for i in self.order.items] for s, y in rows):
                raise GuardError(f"Unexpected existing item rows: {rows}")
            for number, item in enumerate(self.order.items, 1):
                self.stage(f"06_product_{number}_{item.sku}", lambda x=item: self.ensure_product(x))
            self.stage("07_verify_order", self.validate_order)
            self.stage("08_save_order", self.save_order_and_verify)
        if records["invoice"]:
            self.invoice_number = records["invoice"]["number"]
            self.invoice_saved = True
            if self.receipt_invoice and self.receipt_invoice != self.invoice_number:
                raise GuardError("Saved Invoice differs from recovery receipt")
            self.stage(
                "10_verify_existing_paid_invoice", lambda: self.verify_saved_invoice(reopen=True)
            )
        else:
            if self.verify_only or self._protected(self.order_number):
                raise GuardError(
                    "Existing protected/verification-only Order has no verified linked Invoice"
                )
            if self.receipt_invoice:
                drafts = [
                    t
                    for t in self._upper_tabs()
                    if normalized(t.window_text().lstrip("*")) == "new invoice"
                ]
                if (
                    len(drafts) != 1
                    or not self.receipt.data.get("follow_up_order") == self.order_number
                ):
                    raise GuardError(
                        "Known Invoice missing from saved list and linked draft; refusing duplicate"
                    )
                self.invoice_number = self.receipt_invoice
                scope = self.editor("New Invoice")
                self._source_identity(scope, invoice=True)
            else:
                self.stage("09_follow_up_invoice", self.create_linked_invoice)
            self.stage("10_invoice_paid_and_verified", self.complete_invoice)
        print(
            "SUCCESS: saved Order",
            self.order_number,
            "and linked",
            "paid" if self.order.paid else "open",
            "Invoice",
            self.invoice_number,
            "verified in Data > Documents. Evidence:",
            self.evidence,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--image", help="Source order image (uses the project OCR parser)")
    source.add_argument("--json", help="Previously OCR-extracted, validated order JSON")
    parser.add_argument(
        "--tesseract",
        default=os.environ.get("TESSERACT_CMD"),
        help="Tesseract executable path when not on PATH",
    )
    parser.add_argument(
        "--evidence",
        default=f"evidence/full_run/{datetime.now():%Y%m%d_%H%M%S}",
        help="Separate evidence directory for this attempt",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Explicitly authorize saves after every automatic guard passes",
    )
    parser.add_argument(
        "--allow-new", action="store_true", help="Allow one new Order for a separate source fixture"
    )
    parser.add_argument(
        "--verify-existing",
        action="store_true",
        help="Reopen and verify saved records without record edits",
    )
    parser.add_argument(
        "--expected-order-number", default="", help="Optional preserved Order identity"
    )
    parser.add_argument("--receipt", help="Source-specific recovery receipt; identity hints only")
    parser.add_argument(
        "--workspace", help="Exact business workspace path in Fakturama window title"
    )
    parser.add_argument(
        "--strict-assessment",
        action="store_true",
        help="Require exact addresses before initial Save; no post-save delivery repair",
    )
    parser.add_argument(
        "--assume-fresh-workspace",
        action="store_true",
        help="Skip the Documents-list discovery (stage 00) because the workspace was created "
        "by this run's launcher and is known to be empty; refused with --verify-existing or a receipt",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    if args.image:
        from fakturama.ocr import parse_order_image

        order = parse_order_image(args.image, args.tesseract)
        Path(args.evidence).mkdir(parents=True, exist_ok=True)
        order.save_json(Path(args.evidence) / "extracted_order.json")
    else:
        order = read_order(args.json)
    order.validate()
    print(
        "SOURCE",
        order.reference,
        order.company,
        "net",
        order.source_net,
        "VAT",
        order.source_vat,
        "total",
        order.source_gross,
        order.currency,
    )
    if (
        args.allow_new
        and order.reference == "WEB-2026-0714-A17"
        and not is_isolated_workspace(args.workspace)
    ):
        parser.error("Original completed source is protected; use a separate test reference")
    if not args.execute and not args.verify_existing:
        print("PRE-FLIGHT OK. No UI interaction occurred. Add --execute to run with guarded saves.")
        return
    try:
        with Runner(
            order,
            Path(args.evidence),
            allow_save=args.execute,
            expected_order_number=args.expected_order_number,
            allow_new=args.allow_new,
            verify_only=args.verify_existing,
            receipt_path=args.receipt,
            workspace=args.workspace,
            strict_assessment=args.strict_assessment,
            assume_fresh_workspace=args.assume_fresh_workspace,
        ) as runner:
            runner.run()
    except Exception as exc:
        print("\nSTOPPED SAFELY:", str(exc) or f"{type(exc).__name__}: {exc!r}")
        if isinstance(exc, DriverBusyError):
            print(
                "Driver lease denied before desktop initialization; no new execution report was created."
            )
        elif isinstance(exc, FileExistsError):
            print("Existing evidence was preserved; choose a new --evidence directory.")
        elif (Path(args.evidence) / "execution_report.json").exists():
            print(f"Check {args.evidence}/execution_report.json and STOP screenshot.")
        else:
            print("Initialization failed before an execution report was created.")
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
