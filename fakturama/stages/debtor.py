"""Stages 03/05: Debtor lookup from the Order, New Debtor creation, address roles, delivery binding checks."""

from __future__ import annotations

import re
import time


from fakturama.stages.common import GuardError, normalized, decimal_from_ui, midpoint, grab_region


class DebtorMixin:
    """Stages 03/05: Debtor lookup from the Order, New Debtor creation, address roles, delivery binding checks."""

    def _select_contact(self):
        # On a retry the selector may already be open; do not click the icon again.
        d = self._modal_if_open("Select the address")
        if d is None:
            self.editor("New Order")
            try:
                x, y = self.click_image_action(
                    "Addresses", ordinal=0, expected_count=2, x_distance=40
                )
            except Exception as exc:
                # click_input can time out after the click was delivered; check for the popup first.
                d = self._modal_if_open("Select the address")
                if d is None:
                    raise GuardError(
                        "Existing-contact upper-icon click failed "
                        f"at UIA-grounded location: "
                        f"{type(exc).__name__}: {exc!r}"
                    ) from exc
                self.note(
                    "address_selector_opened_after_click_warning",
                    warning=f"{type(exc).__name__}: {exc!r}",
                )
            if d is None:
                d = self.find_modal("Select the address")
            self.note("address_selector_opened", order_number=self.order_number)
        else:
            self.note("address_selector_reused", order_number=self.order_number)

        # One search by Company (PDF 2.2). The first word, because the Company
        # column truncates long names; the exact-match rule still checks every field.
        result = self.search_selector(
            "Select the address",
            [
                (
                    self.order.company.split()[0]
                    if len(self.order.company.split()[0]) >= 4
                    else self.order.company
                ),
            ],
            required_fragments=[
                self.order.company,
                self.order.first_name,
                self.order.last_name,
                self.order.billing.zip,
                self.order.billing.city,
            ],
        )
        if result.status == "EXACT":
            self._modal_confirm_row("Select the address", result)
            self.selected_customer = True
            self._assert_addresses(
                allow_delivery_company=not getattr(self, "strict_assessment", False)
            )
            return True
        self._modal_button(self.find_modal("Select the address"), "Cancel")
        return False

    @staticmethod
    def _address_text_matches(text, address):
        """Never accept a partial or different Order address as a valid match."""
        return all(
            normalized(piece) in normalized(text)
            for piece in (
                address.company,
                address.street,
                address.zip,
                address.city,
                address.country,
            )
        )

    def _order_address_control(self, scope):
        """Return the visible address text box, located relative to the Addresses
        label (Remarks and other multi-line fields sit elsewhere).
        """
        anchors = self.app.controls(kind="Text", name="Addresses", scope=scope)
        if len(anchors) != 1:
            raise GuardError(f"Cannot uniquely locate Order Addresses: {len(anchors)}")
        a = anchors[0].rectangle()
        items = self.app.controls(kind="Text", name="Items", scope=scope)
        if len(items) != 1:
            raise GuardError("Cannot uniquely locate address lower boundary (Items)")
        lower = items[0].rectangle().top
        candidates = []
        for ctrl in self.app.controls(kind="Edit", scope=scope):
            try:
                r = ctrl.rectangle()
                if (
                    r.left >= a.right - 12
                    and r.width() >= 225
                    and 45 <= r.height() <= 220
                    and a.top + 12 <= r.top <= a.top + 105
                    and r.bottom <= lower
                    and abs(r.left - a.right) <= 65
                ):
                    candidates.append(ctrl)
            except Exception:
                pass
        if len(candidates) != 1:
            raise GuardError(
                "Order address text box not uniquely visible; "
                f"candidate_count={len(candidates)}; "
                f"anchor={(a.left,a.top,a.right,a.bottom)}"
            )
        return candidates[0]

    def _order_address_snapshot(self, scope):
        box = self._order_address_control(scope)
        try:
            value = box.get_value().strip()
            if value:
                return value, box.rectangle()
        except Exception:
            pass
        # Read-only fallback; the clipboard is not touched.
        try:
            value = box.window_text().strip()
        except Exception:
            value = ""
        return value, box.rectangle()

    def _order_address_image_text(self, rect):
        """Fallback only for readback: never use OCR to choose another record."""
        from PIL import Image
        import pytesseract
        from fakturama.ocr import configure_tesseract

        try:
            configure_tesseract()
            picture = grab_region(
                self, (rect.left, rect.top, rect.right, rect.bottom), "Order address box"
            )
            picture = picture.resize(
                (picture.width * 2, picture.height * 3), Image.Resampling.LANCZOS
            )
            return pytesseract.image_to_string(picture, config="--psm 6").strip()
        except Exception as exc:
            self.note("order_address_ocr_unavailable", error=f"{type(exc).__name__}: {exc}")
            return ""

    def _initialize_delivery_binding(self, scope):
        """Work around the Fakturama 2.2.0 delivery-tab binding defect: re-select the
        same Debtor while the Delivery tab is active so the tab binds to its own
        address. Saves nothing.
        """
        if self.order_saved or self.verify_only or self._protected(self.order_number):
            raise GuardError(
                "Delivery binding initialization requires an editable unprotected draft"
            )
        self._source_identity(scope)
        self.click_image_action("Addresses", ordinal=0, expected_count=2, x_distance=40)
        self.find_modal("Select the address")
        result = self.search_selector(
            "Select the address",
            [self.order.company.split()[0], self.order.last_name, self.order.billing.zip],
            required_fragments=[
                self.order.company,
                self.order.first_name,
                self.order.last_name,
                self.order.billing.zip,
                self.order.billing.city,
            ],
        )
        if result.status != "EXACT":
            self._modal_button(self.find_modal("Select the address"), "Cancel")
            raise GuardError("Cannot initialize delivery binding without the exact existing Debtor")
        self._modal_confirm_row("Select the address", result)
        self._source_identity(scope)
        self._write_delivery(scope, self.order.delivery)
        self.note("delivery_binding_initialized_before_initial_save", order=self.order_number)

    def _assert_addresses(
        self,
        *,
        invoice=False,
        allow_delivery_company=False,
        repair_delivery=False,
        _allow_binding_repair=True,
    ):
        """Read both address tabs of the Order and compare them with the source.

        A tab click can report success while the previous tab is still showing, so
        the displayed text is what counts: accessibility click first, then a physical
        click inside the tab, and the content is re-read each time.
        """
        from pywinauto import mouse

        scope = self.editor("New Invoice" if invoice else "New Order")
        self._scroll_document(top=True)
        a = self.app.controls(kind="Text", name="Addresses", scope=scope)
        if len(a) != 1:
            raise GuardError("Order Addresses section is not uniquely visible")
        anchor = a[0].rectangle()
        tab_names = ("Invoice address", "Delivery address")
        order_tabs = {}
        for title in tab_names:
            matched = []
            for t in self.app.controls(kind="TabItem", name=title, scope=scope):
                r = t.rectangle()
                if (
                    r.left >= anchor.right - 15
                    and anchor.top - 20 <= r.top <= anchor.bottom + 45
                    and r.width() >= 60
                    and r.height() >= 12
                ):
                    matched.append(t)
            if len(matched) == 1:
                order_tabs[title] = matched[0]
            elif len(matched) > 1:
                raise GuardError(f"Order {title} tabs ambiguous: {len(matched)}")

        if "Invoice address" not in order_tabs:
            raise GuardError("Order Invoice address tab missing after Debtor selection")
        if self.order.delivery != self.order.billing and "Delivery address" not in order_tabs:
            raise GuardError(
                "Order Delivery address tab missing after Debtor selection; "
                "the customer was selected but its delivery role may not "
                "have been copied into the Order. No document saved."
            )

        to_check = [("Invoice address", self.order.billing)]
        if self.order.delivery != self.order.billing:
            to_check.append(("Delivery address", self.order.delivery))
        for title, address in to_check:
            tab = order_tabs[title]
            # A tab click does not always switch the content; verify before trusting it.
            attempts = []
            for how in ("uia_click", "physical_click"):
                if how == "uia_click":
                    try:
                        tab.click_input()
                    except Exception as exc:
                        attempts.append(f"uia_click:{type(exc).__name__}")
                else:
                    r = tab.rectangle()
                    x = r.left + min(24, max(12, r.width() // 4))
                    y = (r.top + r.bottom) // 2
                    mouse.click(button="left", coords=(x, y))
                time.sleep(0.50)
                # Still showing the previous tab? Click the tab physically before concluding anything.
                try:
                    # The tab switches before its Edit is replaced; re-fetch and poll.
                    deadline = time.monotonic() + 3.0
                    while True:
                        actual, box = self._order_address_snapshot(scope)
                        if self._address_text_matches(actual, address):
                            break
                        if time.monotonic() >= deadline:
                            break
                        time.sleep(0.25)
                    attempts.append(f"{how}:ui={actual[:190]!r}")
                except Exception as exc:
                    attempts.append(f"{how}:read_error={type(exc).__name__}:{exc}")
                    continue
                if self._address_text_matches(actual, address):
                    self.note("order_address_verified", tab=title, method=how, address_text=actual)
                    break
                if (
                    title == "Delivery address"
                    and not invoice
                    and self.strict_assessment
                    and not self.order_saved
                    and _allow_binding_repair
                ):
                    from dataclasses import replace

                    known = replace(address, company=self.order.billing.company)
                    if self._address_text_matches(actual, known):
                        self._initialize_delivery_binding(scope)
                        # Re-select, then verify both addresses once more; no further repair from here.
                        return self._assert_addresses(_allow_binding_repair=False)
                if title == "Delivery address" and (allow_delivery_company or repair_delivery):
                    # Only the known company-copy defect is repairable; anything else stops.
                    from dataclasses import replace

                    known = replace(address, company=self.order.billing.company)
                    if self._address_text_matches(actual, known):
                        if repair_delivery:
                            if not self.order_saved or self._protected(self.order_number):
                                raise GuardError(
                                    "Delivery repair requires an unprotected saved fixture Order"
                                )
                            self._write_delivery(scope, address)
                            self.delivery_repair_pending = True
                            self.note("delivery_company_repaired", order=self.order_number)
                        else:
                            self.delivery_repair_pending = True
                            self.note(
                                "delivery_company_repair_deferred_until_reopen",
                                order=self.order_number,
                            )
                        break
                # Cross-check the visible text with OCR in case the Value pattern is stale.
                if how == "physical_click":
                    screenshot_text = self._order_address_image_text(box)
                    attempts.append(f"{how}:ocr={screenshot_text[:190]!r}")
                    if self._address_text_matches(screenshot_text, address):
                        self.note(
                            "order_address_verified",
                            tab=title,
                            method="physical_click+image_readback",
                            address_text=screenshot_text,
                        )
                        break
            else:
                self.note(
                    "order_address_unverified",
                    tab=title,
                    expected_street=address.street,
                    attempts=attempts,
                )
                raise GuardError(
                    f"Order {title} did not display {address.street!r} "
                    "after verified tab-selection attempts. "
                    "The customer may have a saved delivery address "
                    "that was not copied into the Order; "
                    "no Order or Invoice saved. "
                    f"Diagnostics: {attempts}"
                )

        # Leave the billing tab selected.
        tab = order_tabs["Invoice address"]
        r = tab.rectangle()
        mouse.click(
            button="left",
            coords=(r.left + min(24, max(12, r.width() // 4)), (r.top + r.bottom) // 2),
        )
        time.sleep(0.2)

    def _write_delivery(self, scope, address):
        from pywinauto import keyboard

        box = self._order_address_control(scope)
        text = "\n".join(
            [
                address.company,
                f"{self.order.first_name} {self.order.last_name}",
                address.street,
                f"DE-{address.zip} {address.city}",
                address.country,
            ]
        )
        box.click_input()
        keyboard.send_keys("^a{BACKSPACE}")
        escaped = "".join("{" + ch + "}" if ch in "+^%(){}" else ch for ch in text)
        keyboard.send_keys(escaped, with_spaces=True, with_newlines=True, pause=0.015)
        # TAB would insert a tab character here; commit by clicking elsewhere.
        self.app.field("Cust.Ref.", "Edit", scope).click_input()
        time.sleep(0.35)
        actual, _ = self._order_address_snapshot(scope)
        if not self._address_text_matches(actual, address):
            raise GuardError(f"Delivery edit did not commit: {actual!r}")

    def _create_debtor(self):
        # Payment method first: the Debtor editor caches its payment list when it
        # opens (documented deviation from PDF 2.10).
        self.stage("04_conditional_payment_master", self.ensure_payment)
        # Reuse a single Debtor draft left by a stopped run; never someone else's.
        drafts = [
            c for c in self._upper_tabs() if normalized(c.window_text().lstrip("*")) == "new debtor"
        ]
        if len(drafts) > 1:
            raise GuardError(
                "Multiple New Debtor drafts are open; refusing to "
                "overwrite or create another. Nothing saved."
            )
        if drafts:
            tab = drafts[0]
            # As with the Order tab, a physical click may be needed to activate it.
            tab.click_input()
            time.sleep(0.20)
            scope = self.app.editor_bounds(tab)
            try:
                company_edit = self.app.field("Company", "Edit", scope)
            except Exception:
                r = tab.rectangle()
                from pywinauto import mouse

                self.window.set_focus()
                mouse.click(
                    button="left",
                    coords=(int(r.left + r.width() * 0.30), int((r.top + r.bottom) / 2)),
                )
                time.sleep(0.25)
                scope = self.app.editor_bounds(tab)
                company_edit = self.app.field("Company", "Edit", scope)
            current_company = normalized(company_edit.get_value())
            if current_company not in ("", normalized(self.order.company)):
                raise GuardError(
                    "Existing unsaved New Debtor belongs to a "
                    f"different company: {company_edit.get_value()!r}. "
                    "Nothing overwritten."
                )
            company_rect = company_edit.rectangle()
            existing_names = sorted(
                [
                    c
                    for c in self.app.controls(kind="Edit", scope=scope)
                    if company_rect.bottom + 10 < c.rectangle().top < company_rect.bottom + 67
                    and abs(c.rectangle().width() - company_rect.width() * 0.5) < 85
                ],
                key=lambda c: c.rectangle().left,
            )
            if len(existing_names) != 2:
                raise GuardError(
                    "Cannot uniquely verify first and last name " "in an existing New Debtor draft."
                )
            for field, expected_name in zip(
                existing_names, (self.order.first_name, self.order.last_name)
            ):
                old_name = normalized(field.get_value())
                if old_name not in ("", normalized(expected_name)):
                    raise GuardError(
                        f"Existing Debtor draft has another "
                        f"contact name {old_name!r}. Nothing overwritten."
                    )
            # Check the billing street too before reusing the draft.
            self._editor_inner_tab("Addresses")
            self._editor_inner_tab("Main address")
            scope = self.editor("New Debtor")
            old_street = normalized(self.app.field("Street", "Edit", scope).get_value())
            if old_street not in ("", normalized(self.order.billing.street)):
                raise GuardError(
                    f"Existing Debtor draft has another street: "
                    f"{old_street!r}. Nothing overwritten."
                )
            self.note("debtor_draft_reused", company=self.order.company)
        else:
            self._click_text_navigation("New Contact")
        scope = self.editor("New Debtor")
        self.fill("Company", self.order.company, scope)
        company_rect = self.app.field("Company", "Edit", scope).rectangle()
        first_last = sorted(
            [
                c
                for c in self.app.controls(kind="Edit", scope=scope)
                if company_rect.bottom + 10 < c.rectangle().top < company_rect.bottom + 67
                and abs(c.rectangle().width() - company_rect.width() * 0.5) < 85
            ],
            key=lambda c: c.rectangle().left,
        )
        if len(first_last) != 2:
            raise GuardError("Cannot locate Debtor first/last-name fields")
        self.edit_control(first_last[0], self.order.first_name)
        self.edit_control(first_last[1], self.order.last_name)
        self._debtor_address(
            "Main address", self.order.billing, roles=["Invoice address"], contact=True
        )
        if self.order.delivery == self.order.billing:
            self._set_address_roles(["Invoice address", "Delivery address"])
        else:
            self._add_delivery_address()
        self._editor_inner_tab("Miscellaneous")
        scope = self.editor("New Debtor")
        self.fill("Alias name", self.order.alias, scope)
        self.select_combo("Net or Gross", "Net", scope)
        if decimal_from_ui(self.app.field("Discount", "Edit", scope).get_value()) != 0:
            self.fill("Discount", "0%", scope)
        self._ensure_debtor_payment()
        self._verify_debtor_contact_before_save()
        self.save_once("New Debtor")

    def _native_payment_choices(self, combo):
        # 'Missing' must come from the full native list, not from UIA dropping items.
        from pywinauto.controls.win32_controls import ComboBoxWrapper

        try:
            values = ComboBoxWrapper(combo.handle).item_texts()
        except Exception as exc:
            raise GuardError("Cannot prove Payment dropdown contents") from exc
        if not values:
            raise GuardError("Payment dropdown contents unavailable; no creation allowed")
        return [str(value).strip() for value in values]

    def _ensure_debtor_payment(self):
        """Select the pre-created payment; preserve any stale existing draft."""
        self._editor_inner_tab("Miscellaneous")
        scope = self.editor("New Debtor")
        combo = self.app.field("Payment", "ComboBox", scope)
        target = self.order.payment_method
        choices = self._native_payment_choices(combo)
        count = choices.count(target)
        if count > 1:
            raise GuardError("Duplicate exact payment methods in Debtor dropdown")
        if count == 0:
            raise GuardError(
                "Saved payment is not available in the same open Debtor; stop before Debtor Save"
            )
        self.select_combo("Payment", target, scope)
        if self.app.field("Payment", "ComboBox", scope).selected_text().strip() != target:
            raise GuardError("Debtor payment did not persist after selection")
        self.note("debtor_payment_verified", method=target)

    def _verify_debtor_contact_before_save(self):
        # Re-read the contact fields after all the tab switching (PDF 2.7).
        self._editor_inner_tab("Addresses")
        self._editor_inner_tab("Main address")
        scope = self.editor("New Debtor")
        for label, expected in [
            ("Company", self.order.company),
            ("E-Mail", self.order.email),
            ("Telephone", self.order.phone),
            ("Street", self.order.billing.street),
        ]:
            actual = self.app.field(label, "Edit", scope).get_value().strip()
            if actual != str(expected).strip():
                raise GuardError(f"Debtor {label} changed before Save: {actual!r}")
        self.note("debtor_contact_verified_before_save")

    def _editor_inner_tab(self, name):
        # Adding an address rebuilds the tab tree asynchronously: look only inside
        # this editor, de-duplicate by bounds, and wait for the tab to appear.
        scope = self.editor("New Debtor")
        deadline = time.monotonic() + 4
        while True:
            unique = {}
            for tab in self.app.controls(kind="TabItem", name=name, scope=scope):
                r = tab.rectangle()
                unique[(r.left, r.top, r.right, r.bottom)] = tab
            if len(unique) > 1:
                raise GuardError(f"Debtor inner tab {name!r} ambiguous")
            if unique:
                next(iter(unique.values())).click_input()
                time.sleep(0.2)
                return
            if time.monotonic() >= deadline:
                raise GuardError(f"Debtor inner tab {name!r} missing after tab refresh")
            time.sleep(0.2)

    def _debtor_address(self, title, address, *, roles, contact=False):
        self._editor_inner_tab("Addresses")
        self._editor_inner_tab(title)
        scope = self.editor("New Debtor")
        for key, value in [("Street", address.street)]:
            self.fill(key, value, scope)
        self._fill_zip_city(scope, address.zip, address.city)
        self.select_combo("Country", address.country, scope)
        if title != "Main address":
            self.fill("additional name", address.company, scope)
        if contact:
            self.fill("E-Mail", self.order.email, scope)
            self.fill("Telephone", self.order.phone, scope)
        self._set_address_roles(roles)

    def _fill_zip_city(self, scope, zip_code, city):
        labels = [
            c
            for c in self.app.controls(kind="Text", scope=scope)
            if normalized(c.window_text()) == "zip - city"
        ]
        if len(labels) != 1:
            raise GuardError("ZIP - City label not uniquely visible")
        anchor = labels[0].rectangle()
        fields = sorted(
            [
                c
                for c in self.app.controls(kind="Edit", scope=scope)
                if abs(
                    (c.rectangle().top + c.rectangle().bottom) / 2
                    - (anchor.top + anchor.bottom) / 2
                )
                < 14
                and anchor.right - 5 < c.rectangle().left < anchor.right + 90
            ],
            key=lambda c: c.rectangle().left,
        )
        if len(fields) != 2:
            raise GuardError("ZIP and City inputs not uniquely grounded")
        self.edit_control(fields[0], zip_code)
        self.edit_control(fields[1], city)

    @staticmethod
    def _address_role_ink(image, local_x, local_y):
        """Tell a ticked checkbox from an empty one by its fill: Fakturama draws a
        checked box blue with a white tick; other themes draw a dark tick on white.
        """
        x, y = int(round(local_x)), int(round(local_y))
        if x - 4 < 0 or y - 3 < 0 or x + 4 >= image.width or y + 3 >= image.height:
            raise GuardError("Role checkbox interior lies outside captured popup")
        rgb = image.convert("RGB")
        blue_count = dark_count = 0
        for yy in range(y - 3, y + 4):
            for xx in range(x - 4, x + 5):
                red, green, blue = rgb.getpixel((xx, yy))
                # Windows accent blue, around (0, 95, 184).
                blue_fill = (
                    red < 110
                    and 45 < green < 210
                    and blue > 110
                    and blue - red > 55
                    and blue - green > 25
                )
                if blue_fill:
                    blue_count += 1
                if max(red, green, blue) < 135:
                    dark_count += 1
        # A grey border can add a few dark pixels; require a real tick cluster.
        return max(blue_count, dark_count if dark_count >= 10 else 0)

    @staticmethod
    def _parse_role_popup(
        image, *, anchor_right, anchor_bottom, crop_left, crop_top, control_height=26
    ):
        """Read the two role checkboxes in the open "address type" popup from pixels.

        Both checkbox frames must be found relative to the arrow before anything is
        read or clicked, so a popup that did not open cannot authorise a click.
        """
        scale = max(0.75, min(2.0, float(control_height) / 26.0))
        centers = {
            "Invoice address": (round(anchor_right + 9 * scale), round(anchor_bottom + 14 * scale)),
            "Delivery address": (
                round(anchor_right + 144 * scale),
                round(anchor_bottom + 14 * scale),
            ),
        }
        rgb = image.convert("RGB")

        def dark_near(x, y):
            """Account for one/two-pixel UI scaling and screenshot rounding."""
            for oy in (-1, 0, 1):
                for ox in (-1, 0, 1):
                    px, py = x + ox, y + oy
                    if 0 <= px < rgb.width and 0 <= py < rgb.height:
                        if max(rgb.getpixel((px, py))) < 185:
                            return True
            return False

        output = {}
        for role, (screen_x, screen_y) in centers.items():
            cx = screen_x - crop_left
            cy = screen_y - crop_top
            if not (12 <= cx < rgb.width - 12 and 11 <= cy < rgb.height - 11):
                raise GuardError(
                    f"Address-role popup checkbox {role!r} is outside the "
                    "safe screenshot area; no checkbox clicked."
                )
            # Checkboxes are ~16x16 px; four border checks tell an open popup from blank background.
            edges = (
                (round(cx - 8 * scale), round(cy)),
                (round(cx + 7 * scale), round(cy)),
                (round(cx), round(cy - 8 * scale)),
                (round(cx), round(cy + 7 * scale)),
            )
            border = sum(dark_near(x, y) for x, y in edges)
            if border < 3:
                raise GuardError(
                    f"Address-type arrow popup not open or its "
                    f"{role!r} checkbox frame is absent "
                    f"(border checks={border}/4). No checkbox clicked."
                )
            tick = DebtorMixin._address_role_ink(image, cx, cy)
            output[role] = {
                "x": screen_x,
                "y": screen_y,
                "checked": tick >= 5,
                "ink": tick,
                "border": border,
            }
        return output

    @staticmethod
    def _role_text_matches(value, roles):
        return all(
            (role in roles) == (normalized(role) in normalized(value))
            for role in ("Invoice address", "Delivery address")
        )

    def _role_popup_controls(self):
        """Discover the owned native popup by its two named UIA checkboxes."""
        import win32gui, win32process

        handles = set()

        def collect(hwnd, _):
            if (
                win32gui.IsWindowVisible(hwnd)
                and win32gui.GetClassName(hwnd) == "#32770"
                and win32process.GetWindowThreadProcessId(hwnd)[1] == self.pid
            ):
                handles.add(hwnd)

        win32gui.EnumWindows(collect, None)
        win32gui.EnumChildWindows(self.window.handle, collect, None)
        candidates = []
        for hwnd in handles:
            popup = self.desktop.window(handle=hwnd, top_level_only=False).wrapper_object()
            controls = {}
            for role in ("Invoice address", "Delivery address"):
                matches = [
                    c
                    for c in popup.descendants()
                    if c.element_info.control_type == "CheckBox" and c.window_text().strip() == role
                ]
                if len(matches) == 1:
                    controls[role] = matches[0]
            if len(controls) == 2:
                candidates.append(controls)
        if len(candidates) > 1:
            raise GuardError("Multiple address-role popups; no checkbox clicked")
        return candidates[0] if candidates else None

    def _set_address_roles(self, roles):
        allowed = {"Invoice address", "Delivery address"}
        if not roles or not set(roles).issubset(allowed):
            raise GuardError("Invalid address roles")
        scope = self.editor("New Debtor")
        field = self.app.field("address type", "Edit", scope)
        if self._role_text_matches(field.get_value(), roles):
            self.note("debtor_address_roles_verified", roles=list(roles), method="existing_field")
            return

        # The arrow is next to the role field; the rightmost button may be the scrollbar.
        def open_popup():
            popup = self._role_popup_controls()
            if popup:
                return popup
            box = field.rectangle()
            arrows = [
                c
                for c in field.parent().children()
                if c.element_info.control_type == "Button"
                and box.right - 5 <= c.rectangle().left <= box.right + 20
                and abs(midpoint(c.rectangle())[1] - midpoint(box)[1]) < 8
            ]
            if len(arrows) != 1:
                raise GuardError("Address-role arrow is not unique")
            arrows[0].click_input()
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                popup = self._role_popup_controls()
                if popup:
                    return popup
                time.sleep(0.15)
            raise GuardError("Address-role popup has no accessible named checkboxes")

        for role in ("Invoice address", "Delivery address"):
            controls = open_popup()
            check = controls[role]
            state = check.get_toggle_state()
            if state not in (0, 1):
                raise GuardError("Indeterminate address-role checkbox")
            if bool(state) != (role in roles):
                check.click_input()
                time.sleep(0.25)
        self.app.unique("Text", "address type", scope).click_input()
        deadline = time.monotonic() + 3
        while not self._role_text_matches(field.get_value(), roles):
            if time.monotonic() >= deadline:
                raise GuardError("Address-role field did not commit exact requested roles")
            time.sleep(0.15)
        self.note(
            "debtor_address_roles_verified", roles=list(roles), method="named_native_checkboxes"
        )

    def _add_delivery_address(self):
        scope = self.editor("New Debtor")
        # A stopped run may already have added the delivery tab; reuse it rather than press + again.
        existing = [
            c
            for c in self.app.controls(kind="TabItem", scope=scope)
            if re.fullmatch(r"additional address\s*#?\s*\d+", c.window_text().strip(), re.I)
        ]
        if existing:
            if len(existing) != 1 or normalized(existing[0].window_text()) != normalized(
                "additional address #1"
            ):
                raise GuardError(
                    "Existing additional Debtor address tabs are "
                    "ambiguous; refusing to add another."
                )
            self.note("delivery_address_draft_reused", tab=existing[0].window_text())
            self._debtor_address(
                "additional address #1",
                self.order.delivery,
                roles=["Delivery address"],
                contact=False,
            )
            return
        main = [c for c in self.app.controls(kind="TabItem", name="Main address", scope=scope)]
        if len(main) != 1:
            raise GuardError("Main address tab missing")
        r = main[0].rectangle()
        plus = [
            c
            for c in self.app.controls(kind="Button", scope=scope)
            if c.rectangle().left
            > self.window.rectangle().left + self.window.rectangle().width() * 0.8
            and abs(midpoint(c.rectangle())[1] - midpoint(r)[1]) < 35
        ]
        if len(plus) != 1:
            raise GuardError("Additional-address plus button ambiguous")
        plus[0].click_input()
        time.sleep(0.3)
        self._debtor_address(
            "additional address #1", self.order.delivery, roles=["Delivery address"], contact=False
        )

    def check_customer_first(self):
        """Required Order-side existence check, before creating any master record."""
        self.selected_customer = self._select_contact()
        self.note("customer_initial_lookup", exact_existing=self.selected_customer)

    def ensure_customer(self):
        if self.selected_customer:
            return
        self._create_debtor()
        if not self._select_contact():
            raise GuardError("Created Debtor not discoverable in original Order selector")
