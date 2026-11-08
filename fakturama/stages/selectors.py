""" "Select the address" / "Select a product" modal selectors: native search, row OCR, exact-match rules."""

from __future__ import annotations

from datetime import datetime
import re
import time


from fakturama.stages.common import (
    GuardError,
    SearchResult,
    normalized,
    meaningful_ocr,
    grab_region,
)


class SelectorsMixin:
    """ "Select the address" / "Select a product" modal selectors: native search, row OCR, exact-match rules."""

    @staticmethod
    def _read_modal_search_value(field):
        """Try accessible text without requiring SWT's unsupported ValuePattern."""
        try:
            value = field.get_value()
            if isinstance(value, str):
                return value
        except Exception:
            # A normal-looking Edit can still lack the Value pattern.
            pass
        try:
            props = field.legacy_properties()
            value = props.get("Value")
            if isinstance(value, str):
                return value
        except Exception:
            pass
        try:
            import win32gui

            hwnd = getattr(field, "handle", None)
            if hwnd:
                value = win32gui.GetWindowText(hwnd)
                if value:
                    return value
        except Exception:
            pass
        return None

    def _modal_search_ocr(self, field, query):
        """OCR the Search box when it exposes no Value pattern. Queries are kept short
        so the box does not scroll and hide part of the text.
        """
        from PIL import Image, ImageOps, ImageEnhance
        import pytesseract
        from fakturama.ocr import configure_tesseract

        try:
            configure_tesseract()
        except Exception as exc:
            raise GuardError(f"Tesseract required to verify SWT search text: {exc}") from exc
        r = field.rectangle()
        # Skip the magnifier glyph at the left edge.
        left = r.left + min(27, max(16, round(r.width() * 0.18)))
        box = (left, r.top + 1, r.right - 2, r.bottom - 1)
        if box[2] - box[0] < 35 or box[3] - box[1] < 12:
            raise GuardError(f"Modal Search field geometry is unusable: {box}")
        raw = grab_region(self, box, "modal Search field", hwnd=getattr(field, "handle", None))
        scale = 4
        enlarged = raw.resize((raw.width * scale, raw.height * scale), Image.Resampling.LANCZOS)
        gray = ImageOps.grayscale(enlarged)
        processed = ImageEnhance.Contrast(gray).enhance(2.2)
        candidates = []
        for im in (gray, processed):
            for psm in (7, 13):
                try:
                    found = pytesseract.image_to_string(im, config=f"--psm {psm}").strip()
                except Exception as exc:
                    raise GuardError(f"Modal Search OCR failed: {exc}") from exc
                candidates.append(found)

        # OCR may drop glyphs and spaces.
        def plain(v):
            return re.sub(r"[^a-z0-9]", "", normalized(v))

        expected = plain(query)
        # A prefix may be an OCR fragment of our query, never a different query.
        if any(plain(v) == expected for v in candidates):
            return True
        folder = self.evidence
        name = folder / f"modal_search_unverified_{datetime.now():%H%M%S_%f}.png"
        raw.save(name)
        raise GuardError(
            "SWT search text could not be verified by UIA or OCR: "
            f"expected {query!r}, OCR={candidates!r}; crop={name}. "
            "No missing-customer decision or master-data creation attempted."
        )

    def _raise_if_application_error(self):
        popup = self._modal_if_open("Internal Error")
        if popup is None:
            return
        messages = []
        for control in popup.descendants():
            try:
                if control.element_info.control_type in ("Text", "Edit"):
                    value = control.window_text().strip()
                    if value:
                        messages.append(value)
            except Exception:
                continue
        raise GuardError(
            "Fakturama Internal Error: " + " ".join(messages or ["see captured dialog"])
        )

    def _dialog_search(self, dialog, query):
        """Set the Search text and confirm it took. No ENTER: in these dialogs it
        fires the default OK button and closes the selector.
        """
        from pywinauto import keyboard

        edits = [
            c
            for c in dialog.descendants()
            if c.element_info.control_type == "Edit" and c.is_visible()
        ]
        # The Search box is the upper-right Edit, not a grid cell editor.
        r = dialog.rectangle()
        edits = [
            c
            for c in edits
            if c.rectangle().top >= r.top
            and c.rectangle().top < r.top + r.height() * 0.24
            and c.rectangle().left >= r.left + r.width() * 0.55
        ]
        if len(edits) != 1:
            raise GuardError(f"Modal upper-right Search control ambiguous: {len(edits)}")
        field = edits[0]
        title = dialog.window_text().strip()
        query = str(query).strip()
        if not query:
            raise GuardError("Refusing blank selector search")
        field.click_input()
        keyboard.send_keys("^a{BACKSPACE}")
        if title == "Select a product":
            # Set the whole query at once: a unique prefix typed character by character can auto-close the dialog.
            field.set_edit_text(query)
        else:
            keyboard.send_keys(query, with_spaces=True, pause=0.04)
        # Filtering happens on Modify; no ENTER.
        time.sleep(0.8)
        if self._modal_if_open(title) is None:
            self._raise_if_application_error()
            if title == "Select a product":
                return False
            raise GuardError("Selector closed while typing into Search; no row selected")
        value = self._read_modal_search_value(field)
        if value is not None:
            if normalized(value) != normalized(query):
                raise GuardError(f"Modal Search value mismatch: expected {query!r}, got {value!r}")
        else:
            self._modal_search_ocr(field, query)
        self.note(
            "modal_search_verified",
            selector=title,
            query=query,
            method="accessibility" if value is not None else "ocr",
        )
        return True

    def _dialog_rows_ocr(self, dialog):
        """OCR the result rows of a selector dialog. The header/body boundary comes
        from the OCR'd heading words, not from fixed pixels.
        """
        import pytesseract
        from fakturama.ocr import configure_tesseract

        try:
            configure_tesseract()
        except Exception as exc:
            raise GuardError(f"Tesseract is needed for the selector grid: {exc}") from exc
        r = dialog.rectangle()
        screenshot = grab_region(
            self,
            (r.left, r.top, r.right, r.bottom),
            "selector dialog",
            hwnd=getattr(dialog, "handle", None),
        )
        image_file = self.evidence / f"popup_{datetime.now():%H%M%S_%f}.png"
        screenshot.save(image_file)
        try:
            records = pytesseract.image_to_data(
                screenshot, output_type=pytesseract.Output.DICT, config="--psm 11"
            )
        except Exception as exc:
            raise GuardError(f"Unable to OCR selector grid: {exc}") from exc
        tokens = []
        for i, t in enumerate(records["text"]):
            txt = t.strip()
            if not txt:
                continue
            try:
                conf = float(records["conf"][i])
            except (TypeError, ValueError):
                conf = -1
            if conf < 25:
                continue
            tokens.append(
                {
                    "t": txt,
                    "x": records["left"][i],
                    "y": records["top"][i],
                    "w": records["width"][i],
                    "h": records["height"][i],
                }
            )
        # Find the header row by its labels (No., Company, Name, Item No., SKU).
        header_tokens = [
            t
            for t in tokens
            if normalized(t["t"])
            in ("no.", "first", "name", "company", "zip", "city", "item", "stock", "price", "vat")
            and 0.08 * r.height() < t["y"] < 0.38 * r.height()
        ]
        if not header_tokens:
            raise GuardError(
                "Cannot locate modal result-grid header by OCR; unsafe to assume empty"
            )
        header_y = min(x["y"] for x in header_tokens)
        low = header_y + max(18, int(0.020 * r.height()))
        high = int(0.83 * r.height())
        body = [
            t
            for t in tokens
            if low < t["y"] < high and max(16, r.width() * 0.02) < t["x"] < r.width() - 20
        ]
        groups = []
        for token in sorted(body, key=lambda a: (a["y"], a["x"])):
            line = next((x for x in groups if abs(x[0]["y"] - token["y"]) < 10), None)
            if line is None:
                groups.append([token])
            else:
                line.append(token)
        rows = []
        for g in groups:
            g.sort(key=lambda x: x["x"])
            txt = " ".join(x["t"] for x in g)
            y = r.top + round(sum(x["y"] + x["h"] / 2 for x in g) / len(g))
            if meaningful_ocr(txt):
                rows.append((txt, y))
        self.note("selector_ocr", body_rows=[t for t, _ in rows], image=str(image_file))
        return rows

    @staticmethod
    def _address_cell_ocr(popup_image, local_row_y):
        """Read one address row cell by cell, using the six column headings as
        anchors. A truncated Company value is never completed by guessing.
        """
        import pytesseract
        from PIL import Image

        width, height = popup_image.size
        if width < 550 or height < 300:
            raise GuardError("Address dialog image too small for verified table geometry")
        if not (0.12 * height <= local_row_y <= 0.72 * height):
            raise GuardError("Address result row is outside the visible table")
        # Check the headings before trusting the layout.
        h_top = max(0, round(height * 0.135))
        h_bottom = min(height, round(height * 0.203))
        header = popup_image.crop((round(width * 0.045), h_top, round(width * 0.993), h_bottom))
        heading_text = normalized(
            pytesseract.image_to_string(
                header.resize((header.width * 2, header.height * 2), Image.Resampling.LANCZOS),
                config="--psm 11",
            )
        )
        if any(
            word not in heading_text for word in ("no", "first", "name", "company", "zip", "city")
        ):
            raise GuardError(
                "Cannot verify address selector column layout: " f"headings={heading_text!r}"
            )
        boundaries = (0.063, 0.224, 0.383, 0.542, 0.702, 0.861, 0.991)
        names = ("number", "first_name", "last_name", "company", "zip", "city")
        cells = {}
        row_y = int(round(local_row_y))
        for i, name in enumerate(names):
            # ZIP and City can touch the column divider; keep a small left margin.
            x1 = int(round(width * boundaries[i])) + (-5 if name in ("zip", "city") else 3)
            x2 = int(round(width * boundaries[i + 1])) - 3
            if x2 <= x1:
                raise GuardError("Address column bounds invalid")
            patch = popup_image.crop((x1, max(0, row_y - 12), x2, min(height, row_y + 12)))
            large = patch.resize((patch.width * 3, patch.height * 3), Image.Resampling.LANCZOS)
            cells[name] = pytesseract.image_to_string(large, config="--psm 7").strip()
        return cells

    @staticmethod
    def _address_cells_match(cells, order, raw_row_ocr=""):
        """Accept a row only if customer ID, first and last name, company prefix
        and ZIP/City all agree. Column boundaries can clip one leading character,
        in which case the full-row reading must still end with the exact ZIP and City.
        """
        number = normalized(cells.get("number", ""))
        if not re.fullmatch(r"cust[0-9o]{5,}", number):
            return False
        if normalized(cells.get("first_name", "")) != normalized(order.first_name):
            return False
        if normalized(cells.get("last_name", "")) != normalized(order.last_name):
            return False
        visible = normalized(cells.get("company", ""))
        visible = re.sub(r"\s*[.…]{2,}.*$", "", visible).strip(" .…")
        expected_company = normalized(order.company)
        if len(visible) < 8 or not expected_company.startswith(visible):
            return False

        expected_zip = re.sub(r"\D", "", str(order.billing.zip))
        seen_zip = re.sub(r"\D", "", str(cells.get("zip", "")))
        expected_city = normalized(order.billing.city)
        seen_city = normalized(cells.get("city", "")).strip(" .|[]")
        if seen_zip == expected_zip and seen_city == expected_city:
            return True

        # Allow one clipped leading character, nothing more; the full row must still end with ZIP and City.
        zip_is_clipped = (
            bool(seen_zip)
            and len(seen_zip) == len(expected_zip) - 1
            and expected_zip.endswith(seen_zip)
        )
        city_is_clipped = (
            bool(seen_city)
            and len(seen_city) == len(expected_city) - 1
            and expected_city.endswith(seen_city)
        )
        if not (
            (seen_zip == expected_zip or zip_is_clipped)
            and (seen_city == expected_city or city_is_clipped)
        ):
            return False
        row = normalized(raw_row_ocr)
        suffix_pattern = (
            r"(?<![0-9])" + re.escape(expected_zip) + r"\s+" + re.escape(expected_city) + r"\s*$"
        )
        return re.search(suffix_pattern, row) is not None

    def _address_match_one_row(self, dialog, rows, query):
        """Match a single filtered row cell by cell; the loose full-row text alone is
        not enough to select a Debtor.
        """
        if len(rows) != 1:
            raise GuardError(
                f"Address selector search {query!r} yielded "
                f"{len(rows)} visible lines, not one unambiguous row"
            )
        row_text, row_y = rows[0]

        r = dialog.rectangle()
        image = grab_region(
            self,
            (r.left, r.top, r.right, r.bottom),
            "selector dialog",
            hwnd=getattr(dialog, "handle", None),
        )
        cells = self._address_cell_ocr(image, row_y - r.top)
        self.note(
            "address_selector_columns", search=query, cells=cells, row_y=row_y, raw_row_ocr=row_text
        )
        if not self._address_cells_match(cells, self.order, row_text):
            raise GuardError(
                "Address selector visible identity mismatch; "
                f"search={query!r}, cells={cells}. No row selected."
            )
        self.note(
            "address_selector_verified_unique",
            number=cells["number"],
            first=cells["first_name"],
            last=cells["last_name"],
            company_visible=cells["company"],
            zip=cells["zip"],
            city=cells["city"],
        )
        return SearchResult("EXACT", row_y)

    def search_selector(self, title, queries, *, required_fragments):
        dialog = self.find_modal(title)
        for query in queries:
            if self._dialog_search(dialog, query) is False:
                return SearchResult("AUTO_ADDED")
            rows = self._dialog_rows_ocr(dialog)
            if not rows:
                continue
            # Company is truncated in this column; match the other cells exactly instead.
            if title == "Select the address":
                return self._address_match_one_row(dialog, rows, query)
            matches = [
                (text, y)
                for text, y in rows
                if all(normalized(fragment) in normalized(text) for fragment in required_fragments)
            ]
            if len(matches) == 1 and len(rows) == 1:
                return SearchResult("EXACT", matches[0][1])
            # Fragmented rows are ambiguous; stop rather than create.
            raise GuardError(
                f"Search {query!r} produced visible rows but not one provable "
                f"exact match: {[txt for txt,_ in rows]}"
            )
        return SearchResult("MISSING")

    def _modal_confirm_row(self, title, result):
        if result.status != "EXACT" or result.row is None:
            raise GuardError("No exact selection")
        dialog = self.find_modal(title)
        r = dialog.rectangle()
        from pywinauto import mouse

        x = r.left + round(r.width() * 0.30)
        mouse.click(button="left", coords=(x, result.row))
        self._modal_button(dialog, "OK")
        time.sleep(0.5)

    def _modal_button(self, dialog, label):
        buttons = [
            c
            for c in dialog.descendants()
            if c.element_info.control_type == "Button" and c.window_text().strip() == label
        ]
        if len(buttons) != 1:
            # Buttons may only exist as native child windows; look them up through the window.
            raise GuardError(f"Modal {label} button count={len(buttons)}")
        buttons[0].click_input()
        time.sleep(0.4)
