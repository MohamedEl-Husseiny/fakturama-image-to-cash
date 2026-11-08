"""Stage 04: terms-of-payment lookup / creation with the E-Invoice payment code."""

from __future__ import annotations


from fakturama.stages.common import GuardError, PAYMENT_CODES, normalized, decimal_from_ui


class PaymentMixin:
    """Stage 04: terms-of-payment lookup / creation with the E-Invoice payment code."""

    def _payment_code_combo(self, scope):
        """Find the payment-code combo by position: the wide ComboBox between
        Description and Cash discount. Its label is untranslated
        ('!editorPaymentPaymentcode:') and not always exposed; the Account combo
        above Description must not be mistaken for it.
        """
        description = self.app.field("Description", "Edit", scope).rectangle()
        candidates = []
        for combo in self.app.controls(kind="ComboBox", scope=scope):
            try:
                r = combo.rectangle()
                if (
                    description.bottom - 1 <= r.top <= description.bottom + 50
                    and abs(r.left - description.left) <= 55
                    and r.width() >= description.width() * 0.6
                    and 16 <= r.height() <= 50
                ):
                    candidates.append(combo)
            except Exception:
                continue
        if len(candidates) != 1:
            positions = [
                tuple(
                    (
                        c.rectangle().left,
                        c.rectangle().top,
                        c.rectangle().right,
                        c.rectangle().bottom,
                    )
                )
                for c in candidates
            ]
            raise GuardError(
                "Payment Code ComboBox not uniquely located below "
                f"Description; candidates={positions}. No selection attempted."
            )
        combo = candidates[0]
        try:
            current = combo.selected_text().strip()
        except Exception as exc:
            raise GuardError(
                "Payment Code dropdown is present but its selection "
                f"cannot be read: {type(exc).__name__}: {exc!r}"
            ) from exc
        allowed = {
            "Mutually defined",
            "In cash",
            "Credit transfer",
            "Debit transfer",
            "Bank card",
            "Direct debit",
            "Credit card",
            "Debit card",
            "Standing agreement",
            "SEPA credit transfer",
            "SEPA direct debit",
            "Online payment service",
        }
        if current not in allowed:
            raise GuardError(
                "Candidate payment-code dropdown has unexpected "
                f"current selection {current!r}; refusing to guess."
            )
        return combo

    def _choose_payment_code(self, code, scope):
        combo = self._payment_code_combo(scope)
        self.app.combo_choose(combo, code)
        observed = combo.selected_text().strip()
        if observed != code:
            raise GuardError(f"Payment Code not committed: {observed!r}, expected {code!r}")
        self.note("payment_code_selected", value=observed)

    def _reuse_saved_payment_editor(self, name, code):
        """Treat an open, saved (unstarred) payment editor as proof the method exists,
        so a missed OCR row on a re-run cannot create a duplicate.
        """
        tabs = [
            c
            for c in self._upper_tabs()
            if normalized(c.window_text().strip().lstrip("*")) == normalized(name)
        ]
        if len(tabs) > 1:
            raise GuardError(f"Multiple open payment tabs named {name!r}; cannot disambiguate")
        if not tabs:
            return False
        tab = tabs[0]
        if tab.window_text().strip().startswith("*"):
            raise GuardError(
                f"Payment editor {name!r} has pending unsaved edits; "
                "refusing to create or overwrite payment records"
            )
        self._verify_payment_editor(name, code)
        self.note("payment_saved_editor_verified", name=name, code=code)
        return True

    def ensure_payment(self):
        mapping = PAYMENT_CODES.get(self.order.payment_method)
        if mapping is None:
            raise GuardError(f"Unsupported assessment payment method {self.order.payment_method}")
        # Check the saved editor before the list; list OCR can miss a row.
        if self._reuse_saved_payment_editor(self.order.payment_method, mapping):
            return
        tab = self._list_tab("terms of payment")
        existing = self._find_lower_entry(tab, self.order.payment_method)
        if existing:
            self.note("payment_existing", name=self.order.payment_method, row=existing[0])
            # Name and Description do not show the E-Invoice code; open the row to check it.
            self._open_lower_row(existing[1], tab)
            self._verify_payment_editor(self.order.payment_method, mapping)
            return
        # Reuse an unsaved payment draft left by a stopped run.
        drafts = self.app.tabs("New Term of Payment")
        if len(drafts) > 1:
            raise GuardError("Multiple New Term of Payment drafts; refusing to choose.")
        if not drafts:
            self.hit_button("Create a new term of payment")
        else:
            if not drafts[0].window_text().strip().startswith("*"):
                raise GuardError(
                    "Existing payment editor is not marked unsaved; " "refusing to overwrite it."
                )
            self.note("payment_draft_reused", tab=drafts[0].window_text())
        scope = self.editor("New Term of Payment")
        for label in ("Name", "Description"):
            ctrl = self.app.field(label, "Edit", scope)
            current = ctrl.get_value().strip()
            if current not in ("", self.order.payment_method):
                raise GuardError(f"Existing payment draft has different {label}: {current!r}")
            if current != self.order.payment_method:
                self.fill(label, self.order.payment_method, scope)
        self._choose_payment_code(mapping, scope)
        for label, zero in [("Cash discount", "0%"), ("Discount Days", "0"), ("Net Days", "0")]:
            c = self.app.field(label, "Edit", scope)
            if decimal_from_ui(c.get_value()) != 0:
                self.fill(label, zero, scope)
        self._verify_payment_editor(self.order.payment_method, mapping)
        self.save_once("New Term of Payment")
        # Re-fetch the tab after saving; the old wrapper can be stale.
        fresh_list_tab = self._list_tab("terms of payment")
        matching_row = self._find_lower_entry(fresh_list_tab, self.order.payment_method)
        if matching_row:
            self.note("payment_saved_in_list", row=matching_row[0])
        elif not self._reuse_saved_payment_editor(self.order.payment_method, mapping):
            raise GuardError(
                "Payment save is not verifiable in the list or a "
                "saved editor. Do not create a duplicate on restart."
            )
        else:
            self.note(
                "payment_list_ocr_missed_row_but_saved_editor_verified",
                name=self.order.payment_method,
            )

    def _verify_payment_editor(self, name, code):
        tablist = [
            c
            for c in self.app.controls(kind="TabItem")
            if normalized(c.window_text().strip().lstrip("*"))
            in (normalized("New Term of Payment"), normalized(name))
            and c.rectangle().top
            < self.window.rectangle().top + self.window.rectangle().height() * 0.30
        ]
        if len(tablist) != 1:
            raise GuardError(f"Payment editor ambiguous: {len(tablist)}")
        tablist[0].click_input()
        scope = self.app.editor_bounds(tablist[0])
        for label, val in [("Name", name), ("Description", name)]:
            if normalized(self.app.field(label, "Edit", scope).get_value()) != normalized(val):
                raise GuardError(f"Payment {label} differs from {val!r}")
        # Only the payment-code combo; the selected text can carry trailing spaces.
        selected = self._payment_code_combo(scope).selected_text()
        if normalized(selected) != normalized(code):
            raise GuardError(
                "Payment E-Invoice code differs: " f"expected {code!r}, found {selected!r}"
            )
