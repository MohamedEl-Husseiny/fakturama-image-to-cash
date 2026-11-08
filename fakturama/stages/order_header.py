"""Stages 01-02: open New Order, verify its header, set Date / Cust.Ref. / Net / With VAT."""

from __future__ import annotations

import re
import time

from fakturama.order_ui import interpret_date, order_header_layout, header_values, GroundingError

from fakturama.stages.common import GuardError, LOG


class OrderHeaderMixin:
    """Stages 01-02: open New Order, verify its header, set Date / Cust.Ref. / Net / With VAT."""

    def _click_order_tab_by_screen_point(self, *, fraction=0.32):
        """Click inside the Order tab (left part, away from its close glyph).

        A UIA tab click can return normally while the previous editor is still
        showing; a real click on the live rectangle is the fallback. The point is
        checked to belong to Fakturama's window first.
        """
        from pywinauto import mouse
        import win32gui
        import win32process

        drafts = self._order_draft_tabs()
        if len(drafts) != 1:
            raise GuardError(
                "Unable to safely activate Order: expected exactly "
                f"one draft tab, found {len(drafts)}; "
                f"upper_tabs={self._upper_tab_names()}"
            )
        tab = drafts[0]
        r = tab.rectangle()
        if r.width() < 55 or r.height() < 14:
            raise GuardError(f"Unsafe Order tab rectangle: " f"{(r.left,r.top,r.right,r.bottom)}")
        # Aim at the left part of the tab; the close glyph is on the right.
        x = int(r.left + r.width() * fraction)
        y = int(r.top + r.height() * 0.50)
        if x >= r.right - 32 or x <= r.left + 8:
            raise GuardError(f"Cannot find safe clickable Order tab interior: {r}")
        try:
            self.window.set_focus()
        except Exception as exc:
            LOG.warning("Order tab focus request: %s: %r", type(exc).__name__, exc)
        time.sleep(0.22)
        underlying = win32gui.WindowFromPoint((x, y))
        _tid, hit_pid = win32process.GetWindowThreadProcessId(underlying) if underlying else (0, 0)
        if hit_pid != self.pid:
            foreground = win32gui.GetForegroundWindow()
            raise GuardError(
                "Order tab physical click blocked by another window; "
                f"target={(x,y)}, hit_pid={hit_pid}, expected_pid={self.pid}, "
                f"foreground={win32gui.GetWindowText(foreground)!r}. "
                "No click was performed."
            )
        mouse.click(button="left", coords=(x, y))
        time.sleep(0.5)
        self.note("order_tab_physical_activation", point=(x, y), tab=tab.window_text().strip())
        return tab

    def _select_order_tab_by_accessibility(self):
        """Fallback to native SelectionItem for CTabFolder tabs when supported."""
        tabs = self._order_draft_tabs()
        if len(tabs) != 1:
            raise GuardError("Order tab became ambiguous during selection")
        tab = tabs[0]
        try:
            item = tab.iface_selection_item
            item.Select()
            time.sleep(0.45)
            self.note("order_tab_selection_pattern_attempted", tab=tab.window_text().strip())
        except Exception as exc:
            LOG.warning("Order tab SelectionItem not available: %s: %r", type(exc).__name__, exc)

    def _verified_order_header(self, *, timeout=9.0):
        """Wait until the New Order editor is the one actually showing; the previous
        editor can linger in the UIA tree for a few repaints after a tab click.
        """
        deadline = time.monotonic() + timeout
        attempts = 0
        last_error = ""
        while True:
            rows = self.app.ui_snapshot()
            rows = self._recover_missing_number_label(rows)
            try:
                values = header_values(rows)
                if not values.get("number"):
                    raise GroundingError("Order No. Edit is blank")
                if (
                    not re.fullmatch(r"[A-Z]{1,4}\d{4,}", str(values["number"]))
                    or values.get("price_mode") not in ("Net", "Gross")
                    or values.get("vat") not in ("With VAT", "Without VAT")
                ):
                    raise GroundingError(f"Order header values not plausible: {values}")
                if attempts:
                    self.note(
                        "order_header_ui_ready",
                        after_attempts=attempts,
                        order_number=values["number"],
                    )
                return rows, values
            except (GroundingError, KeyError, TypeError, ValueError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            if time.monotonic() >= deadline:
                break
            # If the UIA click did not switch the editor, try a real click on the tab,
            # then the SelectionItem pattern.
            if attempts == 1:
                self._click_order_tab_by_screen_point(fraction=0.30)
            elif attempts == 5:
                self._select_order_tab_by_accessibility()
            elif attempts == 9:
                self._click_order_tab_by_screen_point(fraction=0.48)
            attempts += 1
            time.sleep(0.32)
        try:
            names = [
                f"{x['type']}:{x['name']}"
                for x in self.app.ui_snapshot()
                if x.get("type") in ("Pane", "Text")
                and (
                    x.get("name")
                    in ("New Order", "Payment", "No.", "Date", "Cust.Ref.", "Description", "VAT")
                )
            ]
        except Exception:
            names = []
        try:
            import win32gui

            active_hwnd = win32gui.GetForegroundWindow()
            active_name = win32gui.GetWindowText(active_hwnd)
        except Exception:
            active_name = "unavailable"
        raise GuardError(
            "New Order tab exists but its active header could not "
            f"be verified after {timeout:g}s; {last_error}; "
            f"upper_tabs={self._upper_tab_names()}; "
            f"visible_editor_landmarks={names}; "
            f"foreground_window={active_name!r}. "
            "No second Order created and nothing saved."
        )

    def launch_order(self):
        # An open selector would swallow the toolbar click; check for one first.
        self._dismiss_known_selector()
        candidates = self._order_draft_tabs()
        if len(candidates) > 1:
            raise GuardError(
                "Multiple New Order drafts; refusing to choose one. "
                f"Tabs: {self._upper_tab_names()}"
            )
        if len(candidates) == 0:
            if self.expected_order_number or not getattr(self, "allow_new", False):
                raise GuardError(
                    f"Existing draft {self.expected_order_number} is not visible; "
                    "restore its editor. Refusing to create a replacement Order."
                )
            buttons = self.app.controls(kind="Button", name="Create: New Order")
            if len(buttons) != 1:
                raise GuardError(
                    "Order toolbar button not uniquely available; "
                    f"found {len(buttons)}. Tabs: {self._upper_tab_names()}"
                )
            # click_input activates the window itself; SetForegroundWindow may be refused.
            try:
                self.window.set_focus()
            except Exception as exc:
                LOG.warning(
                    "Fakturama focus request was refused (%s); "
                    "trying the exact toolbar UIA click",
                    exc,
                )
            time.sleep(0.25)
            buttons[0].click_input()
            # The editor opens asynchronously; wait for its tab.
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                candidates = self._order_draft_tabs()
                if candidates:
                    break
                time.sleep(0.35)
            if not candidates:
                dialogs = [w.window_text().strip() for w in self._visible_app_dialogs()]
                raise GuardError(
                    "Order action produced no recognized New Order tab "
                    "after 8 seconds. Visible upper tabs: "
                    f"{self._upper_tab_names()}; dialogs: {dialogs}. "
                    "See STOP screenshot; no second Order clicked."
                )
        if len(candidates) != 1:
            raise GuardError(
                "Ambiguous New Order tabs: " f"{[c.window_text() for c in candidates]}"
            )
        self.editor("New Order")
        _rows, values = self._verified_order_header()
        if self.expected_order_number and values["number"] != self.expected_order_number:
            raise GuardError(
                f"Expected preserved Order {self.expected_order_number}, "
                f'found {values["number"]}; no fields edited.'
            )
        if values["reference"] not in ("", self.order.reference):
            raise GuardError("Existing New Order belongs to another reference; not touching it")
        self.order_number = values["number"]
        if self._protected(self.order_number):
            raise GuardError("Protected completed Order cannot be used as a draft")
        if self.receipt_order and self.receipt_order != self.order_number:
            raise GuardError(
                f"Recovery expects {self.receipt_order}; not adopting {self.order_number}"
            )
        self.receipt.update(order_number=self.order_number, phase="order_draft")
        self.order_tab_left = candidates[0].rectangle().left
        self.note(
            "order_opened",
            order_number=self.order_number,
            reused_existing_draft=bool(values["reference"]),
        )

    def prepare_header(self):
        self._scroll_document(top=True)
        self.editor("New Order")
        rows, original = self._verified_order_header()
        fields = order_header_layout(rows)
        date_ctrl = fields["date"]["_wrapper"]
        reference_ctrl = fields["reference"]["_wrapper"]
        try:
            current_date = interpret_date(original["date"])
        except Exception:
            current_date = None
        if current_date != self.order.order_date:
            if not hasattr(self.app, "_set_order_date_by_uia"):
                raise GuardError(
                    "Updated ui.py containing the verified segmented Date helper is needed"
                )
            self.app._set_order_date_by_uia(date_ctrl, self.order.order_date, reference_ctrl)
        if original["reference"] != self.order.reference:
            self.edit_control(reference_ctrl, self.order.reference)
        scope = self.editor("New Order")
        vat = self.app.controls(kind="ComboBox", name="VAT", scope=scope)
        if len(vat) != 1:
            raise GuardError("Document VAT mode is ambiguous")
        self.app.combo_choose(vat[0], "With VAT")
        prices = [
            c
            for c in self.app.controls(kind="ComboBox", scope=scope)
            if c.selected_text().strip() in ("Net", "Gross")
        ]
        if len(prices) != 1:
            raise GuardError("Document price mode is ambiguous")
        self.app.combo_choose(prices[0], "Net")
        _rows, after = self._verified_order_header()
        if (
            after["number"] != self.order_number
            or after["reference"] != self.order.reference
            or interpret_date(after["date"]) != self.order.order_date
            or after["price_mode"] != "Net"
            or after["vat"] != "With VAT"
        ):
            raise GuardError(f"Order header readback mismatch: {after}")
