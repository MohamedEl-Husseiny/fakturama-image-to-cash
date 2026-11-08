"""Saved-document discovery, Documents list reading, persisted snapshots, modal/dialog discovery."""

from __future__ import annotations

import re
import time

from fakturama.order_ui import interpret_date
from fakturama.execution import (
    classify_documents,
)

from fakturama.stages.common import GuardError, normalized, decimal_from_ui, midpoint, grab_region


class DocumentsMixin:
    """Saved-document discovery, Documents list reading, persisted snapshots, modal/dialog discovery."""

    def _document_category(self, name, scope, *, allow_absent=False):
        choices = self.app.controls(kind="TreeItem", name=name, scope=scope)
        if not choices and allow_absent:
            other = "Invoices" if name == "Orders" else "Orders"
            if len(self.app.controls(kind="TreeItem", name=other, scope=scope)) == 1:
                self.note("documents_category_absent", category=name)
                return None
        if len(choices) != 1:
            raise GuardError(f"Documents category {name!r} not unique")
        return choices[0]

    def _documents(self, category=None):
        """Read the live, reference-filtered Documents table before any creation."""
        from pywinauto.win32structures import RECT

        if not self.window.is_enabled():
            raise GuardError("A modal blocks Documents; refusing Search input")
        tab = self._list_tab("Documents")
        w = self.window.rectangle()
        scope = RECT(w.left, tab.rectangle().bottom, w.right, w.bottom)
        search = self.app.field("Search:", "Edit", scope)
        # The Search box's Value goes blank when it loses focus; verify while focused.
        from pywinauto.controls.win32_controls import EditWrapper

        self._focus_edit_for_input(search)
        EditWrapper(search.handle).set_edit_text(self.order.reference)
        deadline = time.monotonic() + 4
        while self._read_modal_search_value(search) != self.order.reference:
            if time.monotonic() >= deadline:
                raise GuardError("Documents search text not verified")
            time.sleep(0.15)
        # A fresh database has no category tree yet; read the flat table instead.
        if category is None and not self.app.controls(kind="TreeItem", scope=scope):
            rows = self._stable_document_rows(tab, attempts=2)
            self.evidence_shot("Documents_flat")
            result = classify_documents(rows)
            self.note(
                "documents_checked", reference=self.order.reference, category="flat", records=result
            )
            return result
        found_all = []
        for name in ([category] if category else ["Orders", "Invoices"]):
            choice = self._document_category(name, scope, allow_absent=category is None)
            if choice is None:
                continue
            choice.click_input()
            time.sleep(0.4)
            rows = self._stable_document_rows(tab)
            for text, y in rows:
                if re.search(r"\b(?:PO|INV)\d{6,}\b", text, re.I):
                    found_all.append((text, y))
                elif re.search(r"\b(?:paid|open)\b", text, re.I) and re.search(
                    r"\d+[.,]\d{2}", text
                ):
                    raise GuardError(f"Document row number unreadable: {text!r}")
            self.evidence_shot("Documents_" + name.replace(" ", "_"))
        result = classify_documents(found_all)
        self.note(
            "documents_checked", reference=self.order.reference, category=category, records=result
        )
        return result

    def _document_table_bbox(self, tab):
        w = self.window.rectangle()
        r = tab.rectangle()
        return (w.left + int(w.width() * 0.20), r.bottom + 22, w.right - 12, w.bottom - 15)

    def _document_table_capture(self, tab):
        if hasattr(self.window, "handle"):
            import win32gui

            self.window.set_focus()
            if win32gui.GetForegroundWindow() != self.window.handle:
                raise GuardError("Fakturama is obscured; refusing to read an empty document list")
        original = grab_region(self, self._document_table_bbox(tab), "Documents table")
        if hasattr(self, "evidence"):
            original.save(self.evidence / "document_table_last.png")
        return original

    def _stable_document_rows(self, tab, attempts=8):
        """OCR the table once its pixels stop changing. Two identical captures are a
        stricter test than two identical OCR results, and cost one OCR pass instead of two."""
        previous = None
        for _ in range(attempts):
            image = self._document_table_capture(tab)
            if previous is not None and image.tobytes() == previous.tobytes():
                return self._document_table_rows(tab, image=image)
            previous = image
            time.sleep(0.35)
        raise GuardError("Documents table did not stabilize")

    def _document_table_rows(self, tab, image=None):
        """Column OCR prevents PSM 11 from dropping the document-number cell."""
        from PIL import Image
        import pytesseract

        scale = 3
        bbox = self._document_table_bbox(tab)
        original = image if image is not None else self._document_table_capture(tab)
        raw_image = original
        # Strip the grey grid lines first; they make OCR merge rows.
        from fakturama.grid import clean_table_image

        original = clean_table_image(original)
        large = original.resize(
            (original.width * scale, original.height * scale), Image.Resampling.LANCZOS
        )
        data = pytesseract.image_to_data(
            large, config="--psm 11", output_type=pytesseract.Output.DICT
        )
        tokens = []
        for i, t in enumerate(data["text"]):
            if t.strip():
                tokens.append(
                    dict(
                        t=t,
                        x=data["left"][i] / scale,
                        y=data["top"][i] / scale,
                        w=data["width"][i] / scale,
                        h=data["height"][i] / scale,
                    )
                )
        headers = {}
        for key in ("Document", "Date", "State", "Total", "Printed"):
            matches = [t for t in tokens if normalized(t["t"]) == normalized(key)]
            if len(matches) != 1:
                raise GuardError(f"Documents {key} header not unique")
            headers[key] = matches[0]
        hy = headers["Document"]["y"]
        self.document_number_x = int(bbox[0] + headers["Document"]["x"] + 20)
        if any(abs(t["y"] - hy) > 12 for t in headers.values()):
            raise GuardError("Documents headers are not aligned")
        starts = [headers[k]["x"] for k in ("Document", "Date", "State", "Total", "Printed")]
        if starts != sorted(starts):
            raise GuardError("Documents columns out of order")
        top = int(hy + headers["Document"]["h"] + 5)
        left = max(0, int(headers["Document"]["x"] - 8))
        right = int(headers["Date"]["x"] - 8)
        # Find text bands from pixels, so an unreadable row is not mistaken for no row.
        left = int(headers["Document"]["x"] - 2)
        right = int(headers["Date"]["x"] - 15)
        bands = []
        for y in range(top + 1, original.height):
            if sum(original.getpixel((x, y)) == 0 for x in range(left + 2, right)) < 3:
                continue
            if not bands or y - bands[-1][-1] > 3:
                bands.append([y])
            else:
                bands[-1].append(y)
        rows = []
        from PIL import ImageOps

        for band in bands:
            if band[-1] - band[0] < 4:
                continue
            cy = (band[0] + band[-1]) / 2
            sample = raw_image.getpixel((left + 2, int(cy)))
            source = original if min(sample[:3]) < 180 else raw_image

            def cell(x1, x2):
                im = raw_image.crop((int(x1) + 3, band[0] - 2, int(x2) - 3, band[-1] + 5))
                if source is original:
                    im = ImageOps.autocontrast(ImageOps.invert(im.convert("L")))
                im = ImageOps.expand(im, border=12, fill=255 if im.mode == "L" else "white")
                im = im.resize((im.width * scale, im.height * scale), Image.Resampling.LANCZOS)
                return pytesseract.image_to_string(im, config="--psm 7").strip()

            number_crop = original.crop((left + 2, band[0] - 1, right, band[-1] + 2))
            number_crop = ImageOps.expand(number_crop, border=12, fill=255)
            readings = []
            candidates = set()
            for factor in (3, 4):
                im = number_crop.resize(
                    (number_crop.width * factor, number_crop.height * factor),
                    Image.Resampling.BICUBIC,
                )
                raw = pytesseract.image_to_string(
                    im, config="--psm 7 -c tessedit_char_whitelist=POINV0123456789"
                ).strip()
                readings.append(raw)
                compact = re.sub(r"[^A-Z0-9]", "", raw.upper())
                match = re.fullmatch(r"(PO|INV)([0-9OI]{6})", compact)
                if match:
                    candidates.add(match[1] + match[2].replace("O", "0").replace("I", "1"))
            # Document numbers are six digits; anything else needs a human look.
            if len(candidates) != 1:
                raise GuardError(f"Document number OCR ambiguous: {readings}")
            number = candidates.pop()
            state = cell(headers["State"]["x"] - 8, headers["Total"]["x"] - 8)
            # Total is right-aligned; bound the cell by the next header.
            amount = cell(headers["Total"]["x"] - 8, headers["Printed"]["x"] - 8)
            word = next((s for s in ("paid", "open") if s in normalized(state)), None)
            if word is None:
                raise GuardError(f"{number}: unreadable Documents state {state!r}")
            value = decimal_from_ui(amount)
            rows.append((f"{number} {word} {value:.2f}", int(bbox[1] + cy)))
        if not rows:
            body = " ".join(t["t"] for t in tokens if t["y"] > top)
            if re.search(r"\b(?:paid|open)\b", body, re.I):
                raise GuardError("Visible document body but number-column OCR failed")
        return rows

    def _open_persisted(self, number, *, reopen=False):
        tabs = [
            t
            for t in self._upper_tabs()
            if normalized(t.window_text().lstrip("*")) == normalized(number)
        ]
        if len(tabs) > 1:
            raise GuardError(f"Multiple editors for {number}")
        if tabs and reopen:
            self._activate_document(number)
            if tabs[0].window_text().startswith("*"):
                raise GuardError(f"Refusing to close unsaved {number}")
            # The selected tab widens to show its close glyph; Ctrl+W is safer than clicking it.
            scope = self.app.editor_bounds(self._activate_document(number))
            self.app.field("Cust.Ref.", "Edit", scope).click_input()
            from pywinauto import keyboard

            keyboard.send_keys("^w")
            deadline = time.monotonic() + 5
            while any(
                normalized(t.window_text().lstrip("*")) == normalized(number)
                for t in self._upper_tabs()
            ):
                if time.monotonic() >= deadline:
                    raise GuardError(f"Saved editor {number} did not close")
                time.sleep(0.2)
            tabs = []
        if not tabs:
            records = self._documents(category="Invoices" if number.startswith("INV") else "Orders")
            entry = records["invoice" if number.startswith("INV") else "order"]
            if entry is None or entry["number"] != number:
                raise GuardError(f"Saved {number} not uniquely present for source reference")
            from pywinauto import mouse

            mouse.double_click(coords=(self.document_number_x, entry["y"]))
            time.sleep(0.4)
        self._activate_document(number)
        self._scroll_document(top=True)
        return self.app.editor_bounds(self._activate_document(number))

    def _source_identity(self, scope, *, invoice=False):
        number = self.app.field("No.", "Edit", scope).get_value().strip()
        ref = self.app.field("Cust.Ref.", "Edit", scope).get_value().strip()
        return self._check_source_identity(number, ref, invoice=invoice)

    def _check_source_identity(self, number, ref, *, invoice=False):
        expected = self.invoice_number if invoice else self.order_number
        if number != expected or ref != self.order.reference:
            raise GuardError(f"Active document is not source {expected}: {number!r}, {ref!r}")
        return number

    def _shipping_value(self, scope, combo=None, edits=None):
        if combo is None:
            combo = self.app.field("Shipping", "ComboBox", scope)
        if edits is None:
            edits = self.app.controls(kind="Edit", scope=scope)
        r = combo.rectangle()
        edits = [
            c
            for c in edits
            if c.rectangle().left >= r.right - 3
            and abs(midpoint(c.rectangle())[1] - midpoint(r)[1]) < 15
        ]
        if len(edits) != 1:
            raise GuardError("Shipping amount field ambiguous")
        if normalized(combo.selected_text()) != "free of shipping costs":
            raise GuardError("Shipping method is not free of shipping costs")
        return decimal_from_ui(edits[0].get_value())

    def _document_snapshot(self, *, invoice=False):
        scope = self.editor("New Invoice" if invoice else "New Order")
        self._scroll_document(top=True)
        # Every header value comes from one UIA query of the editor.
        date_label = "Order Date" if invoice else "Date"
        specs = [
            ("No.", "Edit"),
            ("Cust.Ref.", "Edit"),
            (date_label, "Edit"),
            ("VAT", "ComboBox"),
            ("Total Net", "Edit"),
            ("VAT", "Edit"),
            ("Total", "Edit"),
            ("Discount", "Edit"),
            ("Shipping", "ComboBox"),
        ]
        got, groups = self.app.fields(specs, scope)
        number = self._check_source_identity(
            got[("No.", "Edit")].get_value().strip(),
            got[("Cust.Ref.", "Edit")].get_value().strip(),
            invoice=invoice,
        )
        prices = [c for c in groups["ComboBox"] if c.selected_text().strip() in ("Net", "Gross")]
        if len(prices) != 1:
            raise GuardError("Document Net/Gross control ambiguous")
        result = dict(
            number=number,
            reference=got[("Cust.Ref.", "Edit")].get_value().strip(),
            order_date=interpret_date(got[(date_label, "Edit")].get_value()),
            price_mode=prices[0].selected_text().strip(),
            vat_mode=got[("VAT", "ComboBox")].selected_text().strip(),
            net=decimal_from_ui(got[("Total Net", "Edit")].get_value()),
            vat=decimal_from_ui(got[("VAT", "Edit")].get_value()),
            gross=decimal_from_ui(got[("Total", "Edit")].get_value()),
            discount=decimal_from_ui(got[("Discount", "Edit")].get_value()),
            shipping=self._shipping_value(
                scope, combo=got[("Shipping", "ComboBox")], edits=groups["Edit"]
            ),
        )
        if invoice:
            paid, method, when, value = self._invoice_payment_controls(scope)
            result.update(
                paid=bool(paid.get_toggle_state()), payment_method=method.selected_text().strip()
            )
            if result["paid"]:
                result.update(
                    payment_date=interpret_date(when.get_value()),
                    payment_value=decimal_from_ui(value.get_value()),
                )
        return result

    def _upper_tabs(self):
        """Read native SWT editor tabs without the adapter's is_visible filter."""
        bound = self.window.rectangle()
        found = {}
        for c in self.window.descendants(control_type="TabItem"):
            try:
                r = c.rectangle()
                if (
                    not c.window_text().strip()
                    or r.width() < 35
                    or r.height() < 10
                    or not (bound.left <= midpoint(r)[0] <= bound.right)
                    or not (bound.top <= r.top <= bound.top + max(260, int(bound.height() * 0.28)))
                ):
                    continue
                # Same native element can appear under several wrappers.
                found[(c.window_text().strip(), r.left, r.top, r.right, r.bottom)] = c
            except Exception:
                continue
        return list(found.values())

    def _upper_tab_names(self):
        return [c.window_text().strip() for c in self._upper_tabs()]

    def _order_draft_tabs(self):
        # '*' marks an unsaved tab.
        return [
            c
            for c in self._upper_tabs()
            if re.fullmatch(
                r"\*?New Order(?:\s+\(\d+\))?", c.window_text().strip(), flags=re.IGNORECASE
            )
        ]

    def _visible_app_dialogs(self):
        result = []
        for w in self.all_windows():
            try:
                if w.handle != self.window.handle and w.window_text().strip():
                    result.append(w)
            except Exception:
                pass
        return result

    def _dismiss_known_selector(self):
        """Cancel only leftover selection dialogs; never close unknown popups."""
        known = {"select the address", "select a product"}
        dialogs = self._visible_app_dialogs()
        # Owned selector popups can be missing from Desktop.windows().
        for title in ("Select the address", "Select a product"):
            popup = self._modal_if_open(title)
            if popup is not None and not any(w.handle == popup.handle for w in dialogs):
                dialogs.append(popup)
        unknown = [w.window_text() for w in dialogs if normalized(w.window_text()) not in known]
        if unknown:
            raise GuardError(
                "A modal window is blocking Fakturama: " f"{unknown}. No action taken."
            )
        for popup in dialogs:
            name = popup.window_text().strip()
            buttons = []
            try:
                for c in popup.descendants():
                    if (
                        c.element_info.control_type == "Button"
                        and normalized(c.window_text()) == "cancel"
                    ):
                        buttons.append(c)
            except Exception:
                pass
            unique = {
                tuple(
                    (
                        c.rectangle().left,
                        c.rectangle().top,
                        c.rectangle().right,
                        c.rectangle().bottom,
                    )
                ): c
                for c in buttons
            }
            if len(unique) != 1:
                raise GuardError(
                    f"Existing selector {name!r} could not be safely "
                    "cancelled; no Order toolbar click attempted."
                )
            list(unique.values())[0].click_input()
            time.sleep(0.5)
            self.note("dismissed_leftover_selector", dialog=name)
        return bool(dialogs)

    def all_windows(self):
        roots = []
        for w in self.desktop.windows(visible_only=True):
            try:
                if w.process_id() == self.pid:
                    roots.append(w)
            except Exception:
                pass
        return roots

    def _modal_if_open(self, title):
        """Find an open selector dialog by native window handle first; owned SWT
        popups are missing from the UIA desktop enumeration.
        """
        import win32gui
        import win32process

        target = normalized(title)
        if not target:
            raise GuardError("Refusing unnamed modal lookup")
        main_rect = self.window.rectangle()
        hits = {}

        def candidate(hwnd, _=None):
            try:
                if not win32gui.IsWindowVisible(hwnd):
                    return
                name = normalized(win32gui.GetWindowText(hwnd))
                if name != target:
                    return
                _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
                if pid != self.pid:
                    return
                left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                if (
                    (right - left) < 280
                    or (bottom - top) < 170
                    or right < main_rect.left
                    or left > main_rect.right
                    or bottom < main_rect.top
                    or top > main_rect.bottom
                ):
                    return
                hits[hwnd] = (left, top, right, bottom)
            except Exception:
                return

        win32gui.EnumWindows(candidate, None)
        win32gui.EnumChildWindows(self.window.handle, candidate, None)
        if len(hits) > 1:
            raise GuardError(
                f"Multiple visible {title!r} native dialogs: {hits}. "
                "No duplicate selector click attempted."
            )
        if hits:
            hwnd = next(iter(hits))
            try:
                popup = self.desktop.window(handle=hwnd, top_level_only=False).wrapper_object()
                return popup
            except Exception as exc:
                raise GuardError(
                    f"{title!r} is open (HWND={hwnd}), but its UIA "
                    f"wrapper is inaccessible: {type(exc).__name__}: {exc!r}"
                ) from exc

        # Some dialogs are panes, not windows; fall back to UIA.
        visible = {}
        for root in [self.window] + self.all_windows():
            try:
                items = [root] + root.descendants()
            except Exception:
                continue
            for c in items:
                try:
                    if normalized(c.window_text()) != target or c.element_info.control_type not in (
                        "Window",
                        "Pane",
                        "Dialog",
                    ):
                        continue
                    r = c.rectangle()
                    if r.width() < 280 or r.height() < 170:
                        continue
                    if r.right < main_rect.left or r.left > main_rect.right:
                        continue
                    key = (r.left, r.top, r.right, r.bottom)
                    visible[key] = c
                except Exception:
                    continue
        if len(visible) > 1:
            raise GuardError(f"Ambiguous {title!r} UIA dialog rectangles: {list(visible)}")
        return next(iter(visible.values())) if visible else None

    def find_modal(self, title, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            modal = self._modal_if_open(title)
            if modal is not None:
                return modal
            time.sleep(0.25)
        raise GuardError(
            f"{title!r} not found after {timeout} seconds, including "
            "native SWT windows and UIA panes. Selector may not have opened."
        )

    @staticmethod
    def _recover_missing_number_label(rows):
        """Locate the number Edit when UIA drops the 'No.' label: it is the Edit on
        the header row left of Date, next to Cust.Ref. The number itself is never
        invented.
        """
        if any(x.get("type") == "Text" and x.get("name") == "No." for x in rows):
            return rows
        if not any(x.get("type") == "Pane" and x.get("name") == "New Order" for x in rows):
            return rows
        dates = [x for x in rows if x.get("type") == "Text" and x.get("name") == "Date"]
        references = [x for x in rows if x.get("type") == "Edit" and x.get("name") == "Cust.Ref."]
        vat = [x for x in rows if x.get("type") == "ComboBox" and x.get("name") == "VAT"]
        if len(dates) != 1 or len(references) != 1 or len(vat) != 1:
            return rows
        date_rect = dates[0]["rect"]
        candidates = [
            x
            for x in rows
            if x.get("type") == "Edit"
            and x.get("name", "") == ""
            and re.fullmatch(r"[A-Z]{1,4}\d{4,}", str(x.get("value") or ""))
            and x["rect"][2] < date_rect[0]
            and abs((x["rect"][1] + x["rect"][3]) - (date_rect[1] + date_rect[3])) < 28
            and x["rect"][3] < references[0]["rect"][1]
        ]
        if len(candidates) != 1:
            return rows
        r = candidates[0]["rect"]
        # Anchor the synthetic label to the Edit's left edge; geometry is verified later.
        label = {"type": "Text", "name": "No.", "rect": [r[0] - 42, r[1], r[0] - 8, r[3]]}
        return [*rows, label]

    def _capture_document_region(self, bbox):
        """Capture only while the exact workspace window owns the foreground."""
        import win32gui

        self.app.ensure_foreground()
        expected = self.window.handle
        if win32gui.GetForegroundWindow() != expected:
            raise GuardError("Document capture requires the exact workspace foreground")
        picture = grab_region(self, bbox, "document region")
        if win32gui.GetForegroundWindow() != expected:
            raise GuardError("Foreground changed during document capture; image discarded")
        return picture
