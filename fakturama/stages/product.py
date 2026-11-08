"""Stages 06-07: VAT and Product select-or-create, item grid OCR, line completion and Order validation."""

from __future__ import annotations

import re
import time

from fakturama.models import money
from fakturama.order_ui import interpret_date, header_values

from fakturama.stages.common import (
    GuardError,
    normalized,
    decimal_from_ui,
    number_for_ui,
    midpoint,
    grab_region,
)


class ProductMixin:
    """Stages 06-07: VAT and Product select-or-create, item grid OCR, line completion and Order validation."""

    def ensure_vat(self, vat):
        v = int(vat)
        tab = self._list_tab("VATs")
        key = f"VAT {v}%"
        existing = self._find_lower_entry(tab, key)
        if existing:
            self._open_lower_row(existing[1], tab)
            scope = self.editor(key)
            name = normalized(self.app.field("Name", "Edit", scope).get_value())
            if name != normalized(key):
                raise GuardError("VAT Name conflict")
            value = self.app.field("Value", "Edit", scope).get_value()
            if decimal_from_ui(value) != money(v):
                raise GuardError("VAT percentage conflict")
            code = self.app.field("VAT code (E-Invoice)", "ComboBox", scope).selected_text()
            if normalized(code) != normalized("S (Standard rate)"):
                raise GuardError(f"VAT E-Invoice code conflict: {code!r}")
            self.note("vat_reused", name=key)
            return
        self.hit_button("Create a new tax rate")
        scope = self.editor("New TAX Rate")
        self.fill("Name", key, scope)
        self.fill("Description", key, scope)
        self.select_combo("VAT code (E-Invoice)", "S (Standard rate)", scope)
        # Numeric fields parse on blur; SetValue with a % suffix saves 0.
        self.fill("Value", str(v), scope)
        self.money_field("Value", v, scope)
        self.save_once("New TAX Rate")
        tab = self._list_tab("VATs")
        if not self._find_lower_entry(tab, key):
            raise GuardError("New VAT not visible after Save")

    def _open_product_selector(self):
        existing = self._modal_if_open("Select a product")
        if existing is not None:
            return existing
        self.editor("New Order")
        self.click_image_action("Items", ordinal=0, expected_count=2, x_distance=35)
        return self.find_modal("Select a product")

    def _product_search(self, item, before=None):
        # Creating a master leaves its editor active; go back to the Order first.
        self.editor("New Order")
        if before is None:
            before = [sku for sku, y in self._grid_sku_rows()]
        d = self._open_product_selector()
        result = self.search_selector("Select a product", [item.sku], required_fragments=[item.sku])
        if result.status == "AUTO_ADDED":
            from fakturama.grid import assert_product_added

            after = [sku for sku, y in self._grid_sku_rows()]
            try:
                assert_product_added(before, after, item.sku)
            except ValueError as exc:
                raise GuardError(str(exc)) from exc
            self.note("product_auto_added_verified", sku=item.sku, before=before, after=after)
            return True
        if result.status == "EXACT":
            self._modal_confirm_row("Select a product", result)
            return True
        self._modal_button(d, "Cancel")
        return False

    def _product_creation_scope(self, item):
        drafts = self.app.tabs("New product")
        if len(drafts) > 1:
            raise GuardError("Multiple unsaved Product drafts; no creation allowed")
        if drafts:
            if not drafts[0].window_text().strip().startswith("*"):
                raise GuardError("Product draft is not marked unsaved")
            scope = self.editor("New product")
            if self.app.field("Item Number", "Edit", scope).get_value().strip() != item.sku:
                raise GuardError(
                    "Existing Product draft has another or unknown SKU; nothing overwritten"
                )
            for label in ("Name", "Description"):
                value = self.app.field(label, "Edit", scope).get_value().strip()
                if value not in ("", item.description):
                    raise GuardError(
                        f"Existing Product draft has another {label}; nothing overwritten"
                    )
            self.note("product_draft_reused", sku=item.sku)
            return scope
        # PDF 3.7: "New product" in the left New panel. Fakturama 2.2.0 can throw a
        # ClassCastException there right after an address selection, so if no editor
        # appears (and no error dialog did either) the Products list's Add button,
        # which opens the same editor, is used instead.
        self._click_text_navigation("New product")
        if self._wait_for_new_product_tab(4):
            self.note("product_editor_opened", via="left_panel")
            return self.editor("New product")
        dialogs = [w.window_text().strip() for w in self._visible_app_dialogs()]
        if dialogs:
            raise GuardError(f"New product shortcut raised a dialog: {dialogs}")
        tab = self._list_tab("Products")
        anchor = tab.rectangle()
        buttons = [
            b
            for b in self.app.controls(kind="Button", name="Create a new product")
            if anchor.top - 8 <= b.rectangle().top and b.rectangle().bottom <= anchor.bottom + 45
        ]
        if len(buttons) != 1:
            raise GuardError(f"Products-list Add button is not unique: {len(buttons)}")
        buttons[0].click_input()
        if not self._wait_for_new_product_tab(5):
            raise GuardError(
                "Products-list Add did not open a new Product; no duplicate click attempted"
            )
        self.note("product_editor_opened", via="products_list_add")
        return self.editor("New product")

    def _wait_for_new_product_tab(self, seconds):
        deadline = time.monotonic() + seconds
        while not self.app.tabs("New product"):
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.2)
        return True

    def _create_product(self, item):
        order_scope = self.editor("New Order")
        self._source_identity(order_scope)
        currency_display = self.app.field("Total Net", "Edit", order_scope).get_value()
        scope = self._product_creation_scope(item)
        for label, val in [
            ("Item Number", item.sku),
            ("Name", item.description),
            ("Description", item.description),
            ("Price (gross)", f"{item.gross_master_price:.2f}"),
        ]:
            if label == "Price (gross)":
                # A new price field shows a default-locale zero before it is edited.
                self.edit_number(
                    self.app.field(label, "Edit", scope),
                    item.gross_master_price,
                    display=currency_display,
                )
            else:
                self.fill(label, val, scope)
        self.select_combo("VAT", f"VAT {int(item.vat_percent)}%", scope)
        for field in ("Stock", "cost price (net)"):
            value = self.app.field(field, "Edit", scope).get_value()
            if decimal_from_ui(value) != 0:
                self.fill(field, "0.00", scope)
        self.money_field("Price (gross)", item.gross_master_price, scope)
        self.save_once("New product")

    def ensure_product(self, item):
        rows = self._grid_sku_rows()
        actual = [s for s, y in rows]
        expected = [i.sku for i in self.order.items]
        if actual != expected[: len(actual)]:
            raise GuardError(f"Existing draft rows are not a source prefix: {actual}")
        if item.sku in actual:
            self.selected_products.append(item.sku)
            self._edit_added_line(item)
            return
        # The grid was just read above; reuse it as the "before" snapshot.
        if not self._product_search(item, before=actual):
            self.ensure_vat(item.vat_percent)
            self._create_product(item)
            if not self._product_search(item):
                raise GuardError(f"Saved {item.sku} product not selectable from Order")
        self.selected_products.append(item.sku)
        self._edit_added_line(item)

    def _order_grid(self):
        """OCR actual column starts, row centers and bounds; no assumed row pitch."""
        from fakturama.ocr import configure_tesseract
        from fakturama.grid import grid_headers

        configure_tesseract()
        self._scroll_document()
        labels = self.app.controls(kind="Text", name="Items")
        remarks = self.app.controls(kind="Text", name="Remarks")
        if len(labels) != 1 or len(remarks) != 1:
            raise GuardError("Items/Remarks anchors ambiguous")
        a = labels[0].rectangle()
        w = self.window.rectangle()
        # Park the mouse on the Items label: hover glow on a cell ruins the OCR.
        from pywinauto import mouse

        mouse.move(coords=midpoint(a))
        time.sleep(0.35)
        bbox = (a.right + 8, a.top - 7, w.right - 25, remarks[0].rectangle().top - 3)
        if bbox[3] - bbox[1] < 40:
            raise GuardError("Items body is clipped; enlarge document editor")
        picture = self._capture_document_region(bbox)
        picture.save(self.evidence / "current_grid.png")
        from fakturama.grid import read_grid_tokens

        tokens = read_grid_tokens(picture, bbox[0], bbox[1])
        try:
            header = grid_headers(tokens)
        except ValueError as exc:
            raise GuardError(str(exc) + ": " + repr([t["t"] for t in tokens])) from exc
        ordered = sorted(header, key=lambda k: header[k]["x"])
        self.grid_bounds = {
            k: (
                header[k]["x"] - 9,
                header[ordered[i + 1]]["x"] - 9 if i + 1 < len(ordered) else bbox[2],
            )
            for i, k in enumerate(ordered)
        }
        self.grid_header_y = header["qty"]["y"] + header["qty"]["h"] / 2
        self.grid_body_bottom = bbox[3]
        self.grid_tokens = tokens
        self.grid_columns = {k: int(header[k]["x"] + header[k]["w"] / 2) for k in header}
        return self.grid_columns, self.grid_header_y + 23, 0

    def _grid_sku_rows(self):
        self._order_grid()
        hits = []
        left, right = self.grid_bounds["sku"]
        for token in self.grid_tokens:
            if (
                left <= token["x"] <= right
                and token["y"] > self.grid_header_y + 8
                and re.fullmatch(r"[A-Z0-9]+-[A-Z0-9]+-[A-Z0-9]+", token["t"], re.I)
            ):
                hits.append((token["t"].upper(), token["y"] + token["h"] / 2))
        if len({s for s, y in hits}) != len(hits):
            raise GuardError("Duplicate grid SKU rows")
        return sorted(hits, key=lambda row: row[1])

    def _line_cells(self, y, keys=("qty", "sku", "vat", "price", "discount", "total")):
        import pytesseract
        from PIL import Image
        from pywinauto import mouse

        labels = self.app.controls(kind="Text", name="Items")
        if len(labels) != 1:
            raise GuardError("Items anchor ambiguous before cell readback")
        mouse.move(coords=midpoint(labels[0].rectangle()))
        time.sleep(0.35)
        cells = {}
        for key in keys:
            left, right = self.grid_bounds[key]
            crop = self._capture_document_region((int(left), int(y - 10), int(right), int(y + 11)))
            large = crop.resize((crop.width * 3, crop.height * 3), Image.Resampling.LANCZOS)
            cells[key] = pytesseract.image_to_string(large, config="--psm 7").strip()
            if key == "vat" and not re.search(r"\d+(?:[.,]\d+)?\s*%", cells[key]):
                from fakturama.grid import read_selected_cell

                cells[key] = read_selected_cell(crop)
        return cells

    def _verify_all_lines(self):
        from fakturama.grid import assert_line_cells

        rows = self._grid_sku_rows()
        if [s for s, y in rows] != [i.sku for i in self.order.items]:
            raise GuardError(f"Actual item sequence differs: {rows}")
        for item, (_, y) in zip(self.order.items, rows):
            cells = self._line_cells(y)
            try:
                assert_line_cells(cells, item)
            except ValueError as exc:
                raise GuardError(str(exc) + ": " + repr(cells)) from exc
            self.note("line_independently_verified", sku=item.sku, cells=cells)

    def _edit_added_line(self, item):
        from pywinauto import mouse, keyboard

        scope = self.editor("New Order")
        rows = self._grid_sku_rows()
        matches = [y for sku, y in rows if sku == item.sku]
        if len(matches) != 1:
            raise GuardError(f"Cannot ground exact item row {item.sku}")
        cols = self.grid_columns
        y = matches[0]
        # Quantity and percentage cells use their own number format, not the currency
        # one; only the three cells about to be edited are read here.
        displays = self._line_cells(y, keys=("qty", "price", "discount"))
        for key, value in [
            ("qty", item.qty),
            ("price", item.unit_net),
            ("discount", item.discount_percent),
        ]:
            target = number_for_ui(value, displays[key])
            x = int(cols[key])
            mouse.double_click(button="left", coords=(x, int(y)))
            time.sleep(0.17)
            keyboard.send_keys("^a{BACKSPACE}")
            keyboard.send_keys(target, pause=0.06)
            keyboard.send_keys("{ENTER}")
            time.sleep(0.25)
        # Re-read the row; typing alone proves nothing.
        self._verify_line_ocr(item, y)

    def _verify_line_ocr(self, item, y):
        from fakturama.grid import assert_line_cells

        cells = self._line_cells(y)
        try:
            assert_line_cells(cells, item)
        except ValueError as exc:
            raise GuardError(str(exc) + ": " + repr(cells)) from exc
        self.note("grid_line", sku=item.sku, cells=cells)

    def _delete_only_demo_line(self):
        """Refuse to delete any unrelated line; old demonstration row is explicit."""
        self.editor("New Order")
        import pytesseract

        cols, y, _ = self._order_grid()
        w = self.window.rectangle()
        bbox = (int(cols["sku"] - 115), int(y - 9), int(cols["sku"] + 145), int(y + 12))
        text = pytesseract.image_to_string(
            grab_region(self, bbox, "demo line SKU cell"), config="--psm 7"
        )
        if "TEST-SKU-001" not in text.upper():
            if normalized(text) and not any(
                normalized(i.sku) in normalized(text) for i in self.order.items
            ):
                raise GuardError(f"Unexpected existing Order item {text!r}; will not delete")
            return
        from pywinauto import mouse

        mouse.click(button="left", coords=(int(cols["sku"]), int(y)))
        icon = self.app.controls(kind="Image")
        items = self.app.controls(kind="Text", name="Items")
        ar = items[0].rectangle()
        nearby = sorted(
            [
                c
                for c in icon
                if abs(midpoint(c.rectangle())[0] - ar.right) < 40
                and ar.top + 55 < c.rectangle().top < ar.top + 132
            ],
            key=lambda x: x.rectangle().top,
        )
        if len(nearby) < 2:
            raise GuardError("Cannot identify Items delete button")
        # Icon order: Select, Add, Delete, Other.
        all_items = sorted(
            [
                c
                for c in icon
                if abs(midpoint(c.rectangle())[0] - ar.right) < 40
                and ar.top + 20 < c.rectangle().top < ar.top + 135
            ],
            key=lambda c: c.rectangle().top,
        )
        if len(all_items) < 3:
            raise GuardError("Items delete (third icon) not available")
        all_items[2].click_input()
        time.sleep(0.4)
        self.note("removed_known_demo_line", sku="TEST-SKU-001")

    def validate_order(self):
        scope = self.editor("New Order")
        current = header_values(self.app.ui_snapshot())
        if (
            current["number"] != self.order_number
            or current["reference"] != self.order.reference
            or interpret_date(current["date"]) != self.order.order_date
        ):
            raise GuardError("Order header changed after adding master records")
        self._assert_addresses(
            allow_delivery_company=not self.order_saved and not self.strict_assessment
        )
        self._verify_all_lines()
        if self.selected_products != [item.sku for item in self.order.items]:
            raise GuardError("Selected product sequence differs from source")
        self.money_field("Total Net", self.order.source_net, scope)
        self.money_field("VAT", self.order.source_vat, scope)
        self.money_field("Total", self.order.source_gross, scope)
        shipping_amount = [
            c
            for c in self.app.controls(kind="Edit", scope=scope)
            if c.rectangle().left
            > self.window.rectangle().left + self.window.rectangle().width() * 0.70
            and abs(
                midpoint(c.rectangle())[1]
                - midpoint(self.app.field("Shipping", "ComboBox", scope).rectangle())[1]
            )
            < 18
        ]
        if len(shipping_amount) != 1 or decimal_from_ui(shipping_amount[0].get_value()) != 0:
            raise GuardError("Order shipping amount is not exactly zero")
        if decimal_from_ui(self.app.field("Discount", "Edit", scope).get_value()) != 0:
            raise GuardError("Nonzero overall Order discount")
        # Compare the three numeric totals; the currency was validated at extraction.
        self.note(
            "order_totals",
            net=str(self.order.source_net),
            vat=str(self.order.source_vat),
            gross=str(self.order.source_gross),
        )
