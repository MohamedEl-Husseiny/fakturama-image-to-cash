"""Generic editor interaction: buttons, fields, combos, numeric entry, single-Save guard, lower list tabs."""

from __future__ import annotations

from decimal import Decimal
import time

from fakturama.models import money

from fakturama.stages.common import (
    GuardError,
    LOG,
    normalized,
    decimal_from_ui,
    number_for_ui,
    within,
    midpoint,
    grab_region,
)


class ControlsMixin:
    """Generic editor interaction: buttons, fields, combos, numeric entry, single-Save guard, lower list tabs."""

    def click_image_action(self, anchor, *, ordinal=0, expected_count=2, x_distance=100):
        """Click exact UIA Image near label, not guessed screen coordinate."""
        labels = self.app.controls(kind="Text", name=anchor)
        if len(labels) != 1:
            raise GuardError(f"Cannot uniquely find action label {anchor!r}")
        ref = labels[0].rectangle()
        icons = []
        for c in self.app.controls(kind="Image"):
            r = c.rectangle()
            if (
                abs((r.left + r.right) / 2 - ref.right) < x_distance
                and r.top > ref.top + 8
                and r.top < ref.bottom + 135
                and r.width() < 35
                and r.height() < 35
            ):
                icons.append(c)
        # Addresses has two icons, Items three or four; the lowest + is never the selector.
        icons.sort(key=lambda c: c.rectangle().top)
        if len(icons) < expected_count:
            raise GuardError(f"Expected {expected_count}+ visible {anchor} icons; got {len(icons)}")
        selected = icons[ordinal]
        rect = selected.rectangle()
        x, y = midpoint(rect)
        self.window.set_focus()
        time.sleep(0.2)
        if not within(self.window.rectangle(), x, y):
            raise GuardError("Icon target outside Fakturama window")
        selected.click_input()
        time.sleep(0.55)
        return x, y

    def hit_button(self, name, *, in_scope=None):
        found = self.app.controls(kind="Button", name=name, scope=in_scope)
        if len(found) != 1:
            raise GuardError(f"Expected one Button {name!r}, got {len(found)}")
        found[0].click_input()
        time.sleep(0.35)

    def fill(self, label, value, scope):
        """Fill a text field and read it back after focus has left it.

        Reading immediately after typing can return a truncated prefix (SWT is still
        processing the keystrokes), so the value is polled and re-checked after a TAB.
        Numeric fields have their own path in edit_number().
        """
        plain_text_labels = {
            "e-mail",
            "telephone",
            "street",
            "company",
            "alias name",
            "additional name",
            "name",
            "description",
            "item number",
        }
        if normalized(label) in plain_text_labels:
            return self._fill_contact_field(label, str(value), scope)
        if normalized(label) in ("value", "price (gross)", "cost price (net)", "stock"):
            return self.edit_number(
                self.app.field(label, "Edit", scope), Decimal(str(value).replace("%", ""))
            )
        return self.app.fill(label, str(value), scope)

    def _focus_edit_for_input(self, control):
        """Restore the intended app and refuse keystrokes into another process."""
        import win32gui
        import win32process

        if not self.window.is_enabled():
            raise GuardError("Fakturama editor is blocked by a modal dialog; nothing typed")
        self.app.ensure_foreground()
        control.click_input()
        foreground = win32gui.GetForegroundWindow()
        if not foreground or win32process.GetWindowThreadProcessId(foreground)[1] != self.pid:
            raise GuardError("Fakturama lost foreground focus before text input; nothing typed")

    def edit_number(self, control, expected, *, display=None):
        from pywinauto import keyboard

        expected = money(expected)
        try:
            if decimal_from_ui(control.get_value()) == expected:
                return control
        except Exception:
            pass
        typed = number_for_ui(expected, control.get_value() if display is None else display)
        self._focus_edit_for_input(control)
        keyboard.send_keys("^a{BACKSPACE}")
        keyboard.send_keys(typed, pause=0.06)
        time.sleep(0.25)
        keyboard.send_keys("{TAB}")
        for _ in range(12):
            try:
                if decimal_from_ui(control.get_value()) == expected:
                    return control
            except Exception:
                pass
            time.sleep(0.2)
        raise GuardError(
            f"Numeric input failed after commit: wanted {expected}, got {control.get_value()!r}"
        )

    def _fill_contact_field(self, label, value, scope):
        """Write free text once and verify the final value; a field that already holds
        the value is left alone. Never presses Enter, never accepts a prefix.
        """
        from pywinauto import keyboard

        expected = str(value).strip()
        if not expected:
            raise GuardError(f"Refusing to clear required contact field {label!r}")

        def read(current=None):
            # A held wrapper can lag and still show the old Value, so a match on the
            # expected text is trusted but a mismatch is re-checked on a fresh lookup.
            if current is not None:
                try:
                    seen = current.get_value().strip()
                except Exception:
                    seen = None
                if seen == expected:
                    return current, seen
            fresh = self.app.field(label, "Edit", scope)
            return fresh, fresh.get_value().strip()

        control, old = read()
        if old == expected:
            self.note("debtor_contact_verified", field=label, source="existing_draft")
            return control

        # Real keystrokes, not SetValue, so SWT fires its Modify/Focus listeners.
        self._focus_edit_for_input(control)
        keyboard.send_keys("^a{BACKSPACE}")
        escaped = "".join(
            (
                "{%}"
                if ch == "%"
                else "{+}" if ch == "+" else "{{}" if ch == "{" else "{}}" if ch == "}" else ch
            )
            for ch in expected
        )
        keyboard.send_keys(escaped, with_spaces=True, pause=0.045)

        # The Value provider can lag behind send_keys; poll instead of failing on a prefix.
        actual = ""
        for _ in range(24):
            control, actual = read(control)
            if actual == expected:
                break
            time.sleep(0.20)
        if actual != expected:
            self.evidence_shot("contact_field_not_committed_" + label)
            raise GuardError(
                f"Contact field {label!r} did not finish typing: "
                f"expected {expected!r}, observed {actual!r}. "
                "No record was saved."
            )

        # Moving focus makes SWT commit the field; re-check the value afterwards.
        keyboard.send_keys("{TAB}")
        for _ in range(8):
            control, actual = read(control)
            if actual == expected:
                self.note(
                    "debtor_contact_verified", field=label, source="typed_and_focus_committed"
                )
                return control
            time.sleep(0.20)
        self.evidence_shot("contact_field_blur_mismatch_" + label)
        raise GuardError(
            f"Contact field {label!r} changed on focus loss: "
            f"expected {expected!r}, observed {actual!r}. "
            "No record was saved."
        )

    def select_combo(self, label, value, scope):
        # The country popup virtualises its items, so UIA enumeration can miss an
        # entry that is really there; this combo gets its own handling.
        if normalized(label) == "country":
            return self._select_country(value, scope)
        return self.app.choose(label, str(value), scope)

    def _select_country(self, value, scope):
        """Select a country from the read-only combo by keyboard and verify it after
        focus has moved. Only entries that exist in the list can be chosen.
        """
        from pywinauto import keyboard

        expected = normalized(value)
        combo = self.app.field("Country", "ComboBox", scope)

        def read():
            try:
                return normalized(combo.selected_text())
            except Exception as exc:
                raise GuardError(
                    "Country dropdown cannot report its current "
                    f"selection: {type(exc).__name__}: {exc!r}"
                ) from exc

        def commit_and_verify(method):
            # The selection commits once the list closes and focus leaves. TAB does both
            # without triggering a dialog's default button the way ENTER would.
            keyboard.send_keys("{TAB}")
            time.sleep(0.20)
            fresh = self.app.field("Country", "ComboBox", scope)
            try:
                actual = normalized(fresh.selected_text())
            except Exception as exc:
                raise GuardError(
                    "Country selection could not be verified after " f"commit ({method}): {exc!r}"
                ) from exc
            if actual != expected:
                raise GuardError(
                    f"Country selection did not persist ({method}): "
                    f"expected {value!r}, found {actual!r}. "
                    "Debtor has NOT been saved."
                )
            self.note("debtor_country_selected", value=str(value), method=method)
            return fresh

        if read() == expected:
            self.note("debtor_country_already_selected", value=str(value))
            return combo

        # Native select can work even when no ListItem wrappers are exposed.
        try:
            combo.select(str(value))
            time.sleep(0.20)
            if read() == expected:
                return commit_and_verify("native_combo_select")
        except Exception as exc:
            LOG.info("Native Country combo selection unavailable: %s: %r", type(exc).__name__, exc)

        # Type the first letter to jump close, then step with DOWN, reading the
        # selection each time rather than assuming positions.
        combo.click_input()
        keyboard.send_keys("{ESC}")  # close any popup left by native select
        combo.click_input()
        keyboard.send_keys(str(value)[0], pause=0.03)
        time.sleep(0.12)
        selected = read()
        if selected == expected:
            return commit_and_verify("keyboard_prefix")
        if selected.startswith(expected[:1]):
            for _ in range(35):
                keyboard.send_keys("{DOWN}")
                current = read()
                if current == expected:
                    return commit_and_verify("keyboard_prefix_down")
                # Past the entry's initial letter, further DOWN presses cannot reach it.
                if current and not current.startswith(expected[:1]):
                    break

        # Some builds have no prefix search: walk from HOME with a bounded step count.
        combo.click_input()
        keyboard.send_keys("{HOME}")
        current = read()
        if current == expected:
            return commit_and_verify("keyboard_home")
        unchanged = 0
        for _ in range(300):
            keyboard.send_keys("{DOWN}")
            nxt = read()
            if nxt == expected:
                return commit_and_verify("keyboard_full_list")
            unchanged = unchanged + 1 if nxt == current else 0
            if unchanged >= 4:
                break
            current = nxt
        self.evidence_shot("country_selection_not_confirmed")
        raise GuardError(
            f"Country {value!r} could not be selected using "
            "Fakturama keyboard navigation even though it may be "
            f"listed. Last selected={current!r}; no Debtor saved."
        )

    def edit_control(self, c, value):
        """Edit an unnamed text control (first/last name, ZIP, city) with the same
        poll-and-blur verification as labelled fields.
        """
        from pywinauto import keyboard

        expected = str(value).strip()
        old = c.get_value().strip()
        if old == expected:
            return c
        self._focus_edit_for_input(c)
        keyboard.send_keys("^a{BACKSPACE}")
        safe = "".join(
            (
                "{%}"
                if ch == "%"
                else "{+}" if ch == "+" else "{{}" if ch == "{" else "{}}" if ch == "}" else ch
            )
            for ch in expected
        )
        keyboard.send_keys(safe, with_spaces=True, pause=0.04)
        actual = ""
        for _ in range(24):
            actual = c.get_value().strip()
            if actual == expected:
                break
            time.sleep(0.20)
        if actual != expected:
            raise GuardError(
                f"Edit field did not finish entering {expected!r}; "
                f"last readback={actual!r}. No save attempted."
            )
        keyboard.send_keys("{TAB}")
        for _ in range(8):
            actual = c.get_value().strip()
            if actual == expected:
                return c
            time.sleep(0.20)
        raise GuardError(
            f"Edit field changed after focus: wanted {expected!r}; "
            f"last readback={actual!r}. No save attempted."
        )

    def money_field(self, label, expected, scope):
        actual = self.app.text(label, scope)
        if decimal_from_ui(actual) != money(expected):
            raise GuardError(f"{label} mismatch: expected {expected}, Fakturama={actual!r}")
        return actual

    def save_once(self, tab_title):
        if not self.allow_save:
            raise GuardError(f"Saving {tab_title} disabled. Re-run with --execute after preflight.")
        if self._protected(tab_title):
            raise GuardError(f"Protected completed document {tab_title} is verification-only")
        if tab_title in ("New Order", "New Invoice"):
            target = self.order_number if tab_title == "New Order" else self.invoice_number
            if self._protected(target):
                raise GuardError(f"Protected completed document {target} is verification-only")
            self.editor(tab_title)
            title = (
                (self.order_number if self.order_saved else "New Order")
                if tab_title == "New Order"
                else (self.invoice_number if self.invoice_saved else "New Invoice")
            )
            matches = [
                t
                for t in self._upper_tabs()
                if normalized(t.window_text().lstrip("*")) == normalized(title)
            ]
            if len(matches) != 1:
                raise GuardError("Save target tab ambiguous")
            tab = matches[0]
        else:
            tab = self.app.activate(tab_title)
        original = tab.window_text()
        if not original.startswith("*"):
            raise GuardError(f"{tab_title} lacks unsaved marker (*); refusing duplicate save")
        if tab_title in ("New Order", "New Invoice"):
            # Fakturama 2.2.0 throws an NPE in doSave when the Delivery tab is selected;
            # make sure the billing tab is active before saving.
            scope = self.app.editor_bounds(tab)
            billing = self.app.controls(kind="TabItem", name="Invoice address", scope=scope)
            if len(billing) != 1:
                raise GuardError("Cannot ground billing tab before document Save")
            from pywinauto import mouse

            mouse.click(button="left", coords=midpoint(billing[0].rectangle()))
            deadline = time.monotonic() + 3
            while True:
                text, _ = self._order_address_snapshot(scope)
                if self._address_text_matches(text, self.order.billing):
                    break
                if time.monotonic() >= deadline:
                    raise GuardError("Billing address did not settle before Save")
                time.sleep(0.25)
        self.evidence_shot("BEFORE_SAVE_" + tab_title)
        if tab_title in ("New Order", "New Invoice"):
            kind = "order" if tab_title == "New Order" else "invoice"
            number = self.order_number if kind == "order" else self.invoice_number
            self.receipt.mark_save_attempted(kind, number)
        self.hit_button("Save the current contents")
        time.sleep(0.85)
        if tab_title in ("New Order", "New Invoice"):
            expected = self.order_number if tab_title == "New Order" else self.invoice_number
            matches = [
                t for t in self._upper_tabs() if t.window_text().strip().lstrip("*") == expected
            ]
            if len(matches) != 1 or matches[0].window_text().startswith("*"):
                raise GuardError(f"{expected} did not become a saved tab; no retry attempted")
        candidates = [
            c
            for c in self.app.controls(kind="TabItem")
            if c.window_text().strip().lstrip("*") in (tab_title, original.strip().lstrip("*"))
        ]
        if candidates and any(c.window_text().startswith("*") for c in candidates):
            raise GuardError(f"{tab_title} still unsaved after Save; check validation dialogs")
        self.note("saved_" + tab_title)

    def _click_text_navigation(self, name):
        controls = self.app.controls(kind="Text", name=name)
        # The left navigation entries sit left of the editor; ignore same-named labels inside it.
        left = [
            c
            for c in controls
            if c.rectangle().right
            < self.window.rectangle().left + self.window.rectangle().width() * 0.23
        ]
        if len(left) != 1:
            raise GuardError(f"Left navigation {name!r} count={len(left)}")
        left[0].click_input()
        time.sleep(0.4)

    def _list_tab(self, name):
        candidates = [
            c
            for c in self.app.controls(kind="TabItem", name=name)
            if c.rectangle().top
            > self.window.rectangle().top + self.window.rectangle().height() * 0.45
        ]
        if len(candidates) != 1:
            self._click_text_navigation(name)
            candidates = [
                c
                for c in self.app.controls(kind="TabItem", name=name)
                if c.rectangle().top
                > self.window.rectangle().top + self.window.rectangle().height() * 0.45
            ]
        if len(candidates) != 1:
            raise GuardError(f"Cannot open lower {name} list")
        candidates[0].click_input()
        time.sleep(0.35)
        return candidates[0]

    @staticmethod
    def _parse_list_rows(image, left, top):
        """OCR the visible list at 2x scale (PSM 11), keeping native row coordinates.
        PSM 6 on the full-height table drops rows; the upscaled sparse mode does not.
        """
        import pytesseract
        from PIL import Image

        scale = 2
        enlarged = image.resize(
            (image.width * scale, image.height * scale), resample=Image.Resampling.LANCZOS
        )
        data = pytesseract.image_to_data(
            enlarged, output_type=pytesseract.Output.DICT, config="--psm 11"
        )
        rows = {}
        for i, raw in enumerate(data["text"]):
            if not str(raw).strip():
                continue
            try:
                confidence = float(data["conf"][i])
            except (ValueError, TypeError):
                continue
            if confidence < 30:
                continue
            y = top + (data["top"][i] + data["height"][i] / 2) / scale
            x = left + data["left"][i] / scale
            key = round((y - top) / 23)  # observed SWT table row pitch ~22-26 px
            rows.setdefault(key, []).append((x, str(raw).strip(), y))
        result = []
        for key, tokens in sorted(rows.items()):
            tokens.sort(key=lambda item: item[0])
            txt = " ".join(x[1] for x in tokens)
            result.append((txt, int(round(sum(x[2] for x in tokens) / len(tokens)))))
        return result

    def _lower_list_rows(self, tab):
        """OCR lower list rows from a screenshot, keeping row click positions."""
        from PIL import ImageGrab

        r = tab.rectangle()
        w = self.window.rectangle()
        # The tab moves horizontally as other tabs open; the table does not, so crop from the window.
        left = w.left + int(w.width() * 0.22)
        top = r.bottom + 30
        right = w.right - 25
        bottom = w.bottom - 30
        if bottom <= top or right <= left:
            raise GuardError("Data list grid is not visible")
        img = grab_region(self, (left, top, right, bottom), "data list grid")
        return self._parse_list_rows(img, left, top)

    def _find_lower_entry(self, tab, name):
        rows = self._lower_list_rows(tab)
        matching = [(text, y) for text, y in rows if normalized(name) in normalized(text)]
        if len(matching) > 1:
            raise GuardError(f"Multiple saved records called {name!r}")
        if len(matching) == 1:
            return matching[0]
        # A list known to be populated (VATs always has Tax-free) reading empty is an OCR failure, not absence.
        if not rows:
            raise GuardError(f"No readable rows in {tab.window_text()} list; cannot prove absent")
        return None

    def _open_lower_row(self, y, tab):
        from pywinauto import mouse

        w = self.window.rectangle()
        x = w.left + int(w.width() * 0.33)
        mouse.double_click(button="left", coords=(x, int(y)))
        time.sleep(0.45)
