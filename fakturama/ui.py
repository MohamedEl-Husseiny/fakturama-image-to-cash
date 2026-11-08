"""Fakturama desktop adapter: UI Automation first, OCR where SWT exposes nothing.

Windows only. When a control cannot be identified exactly, the adapter raises
rather than guessing; the caller decides whether that stops the run.
"""

from __future__ import annotations
from pathlib import Path
import json
from decimal import Decimal
from datetime import datetime
import logging
import os
import re
import time

LOG = logging.getLogger("image_to_cash")


class UIError(RuntimeError):
    pass


def rect_center(r):
    return (r.left + r.right) // 2, (r.top + r.bottom) // 2


def _text_or_none(control):
    try:
        return control.window_text().strip()
    except Exception:
        return None


def tab_is_selected(tab):
    """True when UIA reports the editor tab as the selected one (SelectionItem
    pattern). Unknown or unsupported reads as False, so the caller clicks."""
    try:
        return bool(tab.is_selected())
    except Exception:
        return False


class WindowObstructionError(UIError):
    """A window from another process covers part of a region about to be OCR'd."""


def foreign_windows_over(bbox, own_handle, own_pid, windows):
    """Return windows of *other* processes that are above ``own_handle`` in z-order and
    intersect ``bbox``.

    ``windows`` is an iterable of dicts in z-order, top-most first, each with keys
    ``handle, pid, visible, iconic, cloaked, rect(l,t,r,b), title, cls``. Enumeration
    stops at ``own_handle``: anything after it is behind the application and cannot
    cover the capture. Windows of the application's own process (dialogs, selectors,
    tooltips) are never obstructions.
    """
    left, top, right, bottom = bbox
    found = []
    for w in windows:
        if w["handle"] == own_handle:
            break
        if not w["visible"] or w["iconic"] or w["cloaked"] or w["pid"] == own_pid:
            continue
        l, t, r, b = w["rect"]
        if r <= l or b <= t:
            continue
        if l < right and r > left and t < bottom and b > top:
            found.append(w)
    return found


def _enumerate_top_level_windows():
    """Live z-ordered top-level windows (Windows only)."""
    import ctypes
    import win32gui
    import win32process

    out = []

    def cb(hwnd, _):
        cloaked = ctypes.c_int(0)
        try:
            ctypes.windll.dwmapi.DwmGetWindowAttribute(
                hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked)
            )  # DWMWA_CLOAKED
        except Exception:
            pass
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            out.append(
                {
                    "handle": hwnd,
                    "pid": pid,
                    "visible": bool(win32gui.IsWindowVisible(hwnd)),
                    "iconic": bool(win32gui.IsIconic(hwnd)),
                    "cloaked": bool(cloaked.value),
                    "rect": win32gui.GetWindowRect(hwnd),
                    "title": win32gui.GetWindowText(hwnd),
                    "cls": win32gui.GetClassName(hwnd),
                }
            )
        except Exception:
            pass
        return True

    win32gui.EnumWindows(cb, None)
    return out


def render_window_region(hwnd, bbox):
    """Render a window's own content with ``PrintWindow(PW_RENDERFULLCONTENT)`` and crop
    ``bbox`` (screen coordinates) from it.

    Unlike a screen grab, the rendered surface contains only this window: a
    screen-sharing banner, a toast or any other process's window on top of it is
    not in the image. Returns ``None`` when the region lies outside the window, the
    API reports failure, or the crop is a single flat colour (some GPU-composited
    windows render black), so the caller can fall back to a screen grab.
    """
    import ctypes
    import win32gui
    import win32ui
    from PIL import Image

    root = win32gui.GetAncestor(hwnd, 2)  # GA_ROOT
    l, t, r, b = win32gui.GetWindowRect(root)
    width, height = r - l, b - t
    if width <= 0 or height <= 0:
        return None
    x0, y0, x1, y1 = bbox
    if x0 < l or y0 < t or x1 > r or y1 > b or x1 <= x0 or y1 <= y0:
        return None
    hwnd_dc = win32gui.GetWindowDC(root)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    try:
        bitmap.CreateCompatibleBitmap(mfc_dc, width, height)
        save_dc.SelectObject(bitmap)
        ok = ctypes.windll.user32.PrintWindow(root, save_dc.GetSafeHdc(), 2)  # PW_RENDERFULLCONTENT
        info = bitmap.GetInfo()
        bits = bitmap.GetBitmapBits(True)
    finally:
        try:
            win32gui.DeleteObject(bitmap.GetHandle())
        except Exception:
            pass
        save_dc.DeleteDC()
        mfc_dc.DeleteDC()
        win32gui.ReleaseDC(root, hwnd_dc)
    if not ok:
        return None
    image = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]), bits, "raw", "BGRX", 0, 1)
    crop = image.crop((x0 - l, y0 - t, x1 - l, y1 - t))
    if all(lo == hi for lo, hi in crop.getextrema()):
        return None  # flat colour: nothing rendered
    return crop.copy()


class FakturamaUI:
    def __init__(self, evidence="evidence", workspace=None):
        from pywinauto import Desktop

        self.desktop = Desktop(backend="uia")
        windows = [
            w
            for w in self.desktop.windows(visible_only=True)
            if w.window_text().startswith("Fakturama - ")
        ]
        if workspace is not None:
            expected = os.path.normcase(os.path.normpath(str(Path(workspace).resolve())))
            windows = [
                w
                for w in windows
                if os.path.normcase(os.path.normpath(w.window_text().split(" - ", 1)[1]))
                == expected
            ]
        if len(windows) != 1:
            raise UIError(f"Expected one Fakturama window; found {len(windows)}")
        self.window = self.desktop.window(handle=windows[0].handle).wrapper_object()
        self.pid = self.window.process_id()
        self.evidence = Path(evidence)
        self.evidence.mkdir(parents=True, exist_ok=True)
        self.log = logging.getLogger("fakturama")

    def grab(self, bbox, what="capture region", hwnd=None):
        """Image of ``bbox`` for OCR that ignores other applications' windows.

        First choice is the window's own rendered content (``render_window_region``),
        which is immune to overlays such as a screen-sharing banner. If that is not
        available the screen is grabbed, but only after ``assert_unobstructed`` has
        confirmed no other process's window covers the region.
        """
        from PIL import ImageGrab

        target = hwnd if hwnd is not None else self.window.handle
        try:
            image = render_window_region(target, bbox)
        except Exception as exc:  # win32ui/PrintWindow failure: fall back, never guess
            LOG.debug("render_window_region failed for %s: %s", what, exc)
            image = None
        if image is not None:
            return image
        self.assert_unobstructed(bbox, what)
        return ImageGrab.grab(bbox=bbox)

    def assert_unobstructed(self, bbox, what="capture region"):
        """Stop before OCR if another application's window covers any of ``bbox``.

        The foreground check catches a stolen focus; this catches always-on-top
        overlays that do not take focus (screen-share banners, notification toasts,
        picture-in-picture players). Such pixels would be OCR'd as if they were
        Fakturama's, so the run refuses instead of guessing.
        """
        offenders = foreign_windows_over(
            bbox, self.window.handle, self.pid, _enumerate_top_level_windows()
        )
        if offenders:
            names = "; ".join(
                f"'{w['title'] or w['cls']}' (pid {w['pid']}, {w['rect']})" for w in offenders[:3]
            )
            raise WindowObstructionError(
                f"Another window overlaps the {what} {bbox}: {names}. "
                "Hide or move it (e.g. a screen-sharing banner) and start a new run."
            )

    def screenshot(self, label="checkpoint"):
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", label)
        dst = self.evidence / f"{datetime.now():%Y%m%d_%H%M%S}_{safe}.png"
        self.window.capture_as_image().save(dst)
        self.log.info("Screenshot: %s", dst)
        return dst

    def controls(self, scope=None, kind=None, name=None):
        """Visible descendants, optionally filtered by control type, exact name and region.

        The control-type filter is a server-side UIA condition; visibility and region
        come from one cached query (see _visible_descendants). Name matching uses the
        wrapper's own text so it reads exactly what the rest of the code reads.
        """
        result = []
        for c in self._visible_descendants(kind, scope):
            try:
                if name is not None and c.window_text().strip() != name:
                    continue
                result.append(c)
            except Exception:
                pass
        return result

    @staticmethod
    def _inside(scope, left, top, right, bottom):
        return (
            scope.left <= (left + right) / 2 <= scope.right
            and scope.top <= (top + bottom) / 2 <= scope.bottom
        )

    def _visible_descendants(self, kind, scope):
        return self._visible_by_kind((kind,), scope)[kind]

    def _visible_by_kind(self, kinds, scope):
        """Visible descendants of each control type in ``kinds`` whose centre lies in
        ``scope``, as a dict keyed by kind.

        Every UIA query walks the whole SWT tree server-side and costs about a third
        of a second whatever the filter, so one query serves all the kinds a caller
        needs. Fast path: a single FindAllBuildCache call returns every candidate
        with its control type, IsOffscreen flag and bounding rectangle cached, so
        the filters run on cached values with no further round trips. The wrappers
        it returns are built the same way pywinauto builds them for descendants().
        If the cached query is unavailable (fakes in tests, an old pywinauto, a COM
        error) the per-element walk is used; both paths select the same elements.
        """
        kinds = tuple(dict.fromkeys(kinds))
        try:
            return self._visible_by_kind_cached(kinds, scope)
        except Exception as exc:  # fall back to the per-element walk
            LOG.debug("Cached UIA query unavailable (%s); walking elements", exc)
        groups = {}
        for kind in kinds:
            found = []
            descendants = (
                self.window.descendants(control_type=kind) if kind else self.window.descendants()
            )
            for c in descendants:
                try:
                    if not c.is_visible():
                        continue
                    if scope is not None:
                        r = c.rectangle()
                        if not self._inside(scope, r.left, r.top, r.right, r.bottom):
                            continue
                    found.append(c)
                except Exception:
                    pass
            groups[kind] = found
        return groups

    def _visible_by_kind_cached(self, kinds, scope):
        from pywinauto.controls.uiawrapper import UIAWrapper
        from pywinauto.uia_defines import IUIA
        from pywinauto.uia_element_info import UIAElementInfo

        uia = IUIA()
        request = getattr(self, "_cache_request", None)
        if request is None:
            request = uia.iuia.CreateCacheRequest()
            request.AddProperty(uia.UIA_dll.UIA_ControlTypePropertyId)
            request.AddProperty(uia.UIA_dll.UIA_IsOffscreenPropertyId)
            request.AddProperty(uia.UIA_dll.UIA_BoundingRectanglePropertyId)
            self._cache_request = request
        if None in kinds:
            condition = uia.true_condition
        else:
            conditions = [uia.build_condition(control_type=k) for k in kinds]
            condition = conditions[0]
            for extra in conditions[1:]:
                condition = uia.iuia.CreateOrCondition(condition, extra)
        type_names = {v: k for k, v in uia.known_control_types.items()}
        elements = self.window.element_info.element.FindAllBuildCache(
            uia.tree_scope["descendants"], condition, request
        )
        groups = {kind: [] for kind in kinds}
        for i in range(elements.Length):
            e = elements.GetElement(i)
            if e.CachedIsOffscreen:
                continue
            if scope is not None:
                r = e.CachedBoundingRectangle
                if not self._inside(scope, r.left, r.top, r.right, r.bottom):
                    continue
            name = type_names.get(e.CachedControlType)
            for kind in kinds:
                if kind is None or kind == name:
                    groups[kind].append(UIAWrapper(UIAElementInfo(e)))
        return groups

    def unique(self, kind, name, scope=None):
        c = self.controls(scope=scope, kind=kind, name=name)
        if len(c) != 1:
            self.screenshot("missing_control")
            raise UIError(f"Expected one {kind} {name!r}; got {len(c)}")
        return c[0]

    def tabs(self, title):
        return [
            c
            for c in self.controls(kind="TabItem")
            if c.window_text().strip().lstrip("*").strip().casefold() == title.casefold()
        ]

    def activate(self, title, index=None):
        tabs = self.tabs(title)
        if index is None:
            if len(tabs) != 1:
                raise UIError(
                    f"{title!r} tab count={len(tabs)}; specify index only after inspection"
                )
            tab = tabs[0]
        else:
            if not 0 <= index < len(tabs):
                raise UIError("Invalid tab index")
            tab = tabs[index]
        if tab_is_selected(tab):
            return tab  # already the active editor: no click
        tab.click_input()
        time.sleep(0.25)
        return tab

    def editor_bounds(self, tab):
        """Use the tab boundary and lower data tab strip, not absolute pixels."""
        from pywinauto.win32structures import RECT

        start = tab.rectangle().bottom
        main = self.window.rectangle()
        # Only the lower data-view strip ends the editor; nested tabs such as
        # Invoice address or Miscellaneous are inside it.
        known_data_tabs = {
            "Documents",
            "Debtors",
            "Creditors",
            "Products",
            "VATs",
            "terms of payment",
            "Shippings",
        }
        # The strip's position only changes with the window geometry, so once it
        # has been seen it is remembered per window rectangle instead of being
        # queried again on every call.
        key = (main.left, main.top, main.right, main.bottom, start)
        cache = self.__dict__.setdefault("_lower_strip_top", {})
        end = cache.get(key)
        if end is None:
            lower = [
                c.rectangle().top
                for c in self.controls(kind="TabItem")
                if c.window_text().strip() in known_data_tabs and c.rectangle().top > start + 80
            ]
            if lower:
                end = cache[key] = min(lower)
            else:
                end = main.bottom - 15
        return RECT(main.left + min(290, int(main.width() * 0.18)), start, main.right - 10, end)

    def field(self, label, kind="Edit", scope=None):
        # One UIA query fetches the inputs and the labels together.
        groups = self._visible_by_kind((kind, "Text"), scope)
        return self._resolve_field(label, kind, groups)

    def fields(self, specs, scope=None, extra_kinds=()):
        """Resolve several (label, kind) pairs from a single UIA query.

        Returns (resolved, groups): ``resolved[(label, kind)]`` is the control each
        spec names, found exactly as field() would find it; ``groups`` holds the
        visible controls of every requested kind for callers that need the lists too.
        """
        kinds = tuple(dict.fromkeys([k for _, k in specs] + list(extra_kinds) + ["Text"]))
        groups = self._visible_by_kind(kinds, scope)
        resolved = {
            (label, kind): self._resolve_field(label, kind, groups) for label, kind in specs
        }
        return resolved, groups

    @staticmethod
    def _resolve_field(label, kind, groups):
        inputs = groups[kind]
        named = [c for c in inputs if _text_or_none(c) == label]
        if len(named) == 1:
            return named[0]
        labels = [
            c for c in groups["Text"] if (_text_or_none(c) or "").casefold() == label.casefold()
        ]
        if len(labels) != 1:
            # Callers poll this while an editor renders; a STOP captures the desktop anyway.
            raise UIError(f"Cannot locate {label!r} label / {kind} in active editor")
        lr = labels[0].rectangle()
        cy = (lr.top + lr.bottom) / 2
        candidates = []
        for c in inputs:
            try:
                r = c.rectangle()
            except Exception:
                continue
            if r.left >= lr.right - 8 and abs((r.top + r.bottom) / 2 - cy) < 18:
                candidates.append((r.left - lr.right, c))
        if not candidates:
            raise UIError(f"No control to right of {label!r}")
        candidates.sort(key=lambda pair: pair[0])
        if len(candidates) > 1 and candidates[1][0] - candidates[0][0] < 5:
            raise UIError(f"Ambiguous field near {label!r}")
        return candidates[0][1]

    def text(self, label, scope, kind="Edit"):
        return self.field(label, kind, scope).get_value()

    def fill(self, label, value, scope, kind="Edit"):
        c = self.field(label, kind, scope)
        c.click_input()
        c.type_keys("^a{BACKSPACE}")
        # type_keys treats + % ^ as modifiers; escape them.
        escaped = "".join(
            (
                "{%}"
                if ch == "%"
                else "{+}" if ch == "+" else "{{}" if ch == "{" else "{}}" if ch == "}" else ch
            )
            for ch in str(value)
        )
        c.type_keys(escaped, with_spaces=True, pause=0.005)
        observed = c.get_value().strip()
        expected = str(value).strip()
        if observed != expected:
            # Price fields show a currency symbol and group separators.
            price_like = any(k in label.casefold() for k in ("price", "cost", "stock"))
            if price_like:
                try:
                    actual_n = Decimal(
                        observed.replace("€", "")
                        .replace("$", "")
                        .replace("EUR", "")
                        .replace(",", "")
                        .strip()
                    )
                    expected_n = Decimal(expected.replace(",", ""))
                    if actual_n == expected_n:
                        return c
                except Exception:
                    pass
            raise UIError(f"Failed field {label!r}: wanted {value!r}, got {observed!r}")
        return c

    def all_pid_windows(self):
        for w in self.desktop.windows(visible_only=True):
            try:
                if w.process_id() == self.pid:
                    yield w
            except Exception:
                continue

    def combo_choose(self, combo, target):
        """Native UIA dropdown item discovery + physical input (commits SWT event)."""
        if combo.selected_text().strip() == target:
            return
        self.ensure_foreground()
        combo.set_focus()
        combo.expand()
        time.sleep(0.35)
        r = combo.rectangle()
        found = {}
        for root in self.all_pid_windows():
            try:
                descendants = [root] + root.descendants()
            except Exception:
                continue
            for c in descendants:
                try:
                    if c.element_info.control_type != "ListItem" or not c.is_visible():
                        continue
                    box = c.rectangle()
                    if box.right < r.left - 20 or box.left > r.right + 20:
                        continue
                    if box.top < r.top - 450 or box.bottom > r.bottom + 550:
                        continue
                    if c.window_text().strip() != target:
                        continue
                    found[(box.left, box.top, box.right, box.bottom)] = c
                except Exception:
                    continue
        if len(found) != 1:
            self.screenshot("dropdown_missing_" + target)
            raise UIError(f"Exact dropdown item {target!r} found {len(found)} times")
        list(found.values())[0].click_input()
        time.sleep(0.3)
        actual = combo.selected_text().strip()
        if actual != target:
            raise UIError(f"Dropdown commit failed: wanted {target!r}; got {actual!r}")

    def ensure_foreground(self):
        """Restore this instance before input, then verify the owning process."""
        import win32gui
        import win32process

        if not self.window.is_enabled():
            raise UIError("Fakturama is blocked by a modal; no input attempted")
        self.window.set_focus()
        foreground = win32gui.GetForegroundWindow()
        if not foreground or win32process.GetWindowThreadProcessId(foreground)[1] != self.pid:
            # A click on the caption dismisses a lingering taskbar preview.
            self.window.click_input(coords=(self.window.rectangle().width() // 2, 12))
            foreground = win32gui.GetForegroundWindow()
        if not foreground or win32process.GetWindowThreadProcessId(foreground)[1] != self.pid:
            raise UIError("Fakturama could not regain foreground; no input attempted")

    def choose(self, label, target, scope):
        combo = self.field(label, "ComboBox", scope)
        self.combo_choose(combo, target)
        # SWT commits the field when focus leaves it.
        return combo

    def save_current(self, expected_title):
        tab = self.activate(expected_title)
        if not tab.window_text().lstrip().startswith("*"):
            self.log.warning(
                "Tab %s does not show *; saving still requires explicit operator confirmation",
                expected_title,
            )
        confirm = input(
            f"Verify the {expected_title} editor, then type SAVE {expected_title}: "
        ).strip()
        if confirm != f"SAVE {expected_title}":
            raise UIError("Save denied. No toolbar Save action taken.")
        self.unique("Button", "Save the current contents").click_input()
        time.sleep(0.5)
        self.screenshot("save_clicked_" + expected_title)
        self.log.info(
            "Clicked Save once for %s; independently verify persisted row", expected_title
        )

    def checkpoint(self, instruction, keyword="DONE"):
        """Explicit assisted checkpoint for an action not yet verified on this install."""
        self.screenshot("before_manual_checkpoint")
        print("\n" + "=" * 72 + "\nHUMAN VERIFICATION REQUIRED\n" + instruction + "\n" + "=" * 72)
        if input(f"Type {keyword} only after completing and verifying: ").strip() != keyword:
            raise UIError("Stopped at manual review checkpoint")
        self.screenshot("after_manual_checkpoint")

    def guide_click(self, label, kind="Text"):
        c = self.unique(kind, label)
        c.click_input()
        time.sleep(0.25)
        return c

    def ui_snapshot(self):
        """Lightweight live UIA snapshot for exact control grounding."""
        rows = []
        for c in self.controls():
            try:
                r = c.rectangle()
                typ = c.element_info.control_type
                item = {
                    "type": typ,
                    "name": c.window_text().strip(),
                    "rect": [r.left, r.top, r.right, r.bottom],
                    "_wrapper": c,
                }
                if typ == "Edit":
                    try:
                        item["value"] = c.get_value()
                    except Exception:
                        pass
                elif typ == "ComboBox":
                    try:
                        item["selected"] = c.selected_text().strip()
                    except Exception:
                        pass
                rows.append(item)
            except Exception:
                continue
        return rows

    @staticmethod
    def _calendar_day_point(popup_rect, year, month, day):
        """Cell centre in the calendar popup's 7x6 grid, from the popup's own geometry."""
        import calendar
        from datetime import date

        date(year, month, day)  # Reject nonexistent source dates.
        weeks = calendar.Calendar(firstweekday=6).monthdayscalendar(year, month)
        matches = [(i, row.index(day)) for i, row in enumerate(weeks) if day in row]
        if len(matches) != 1:
            raise UIError("Cannot identify exactly one target day in calendar layout.")
        row, col = matches[0]
        left, top, right, bottom = popup_rect
        cell_width = (right - left) / 7.0
        x = round(left + (col + 0.5) * cell_width)
        y = round(top + 119.0 / 220.0 * (right - left) + row * cell_width)
        if not (left < x < right and top < y < bottom - 40):
            raise UIError("Date cell point fell outside the safely grounded calendar grid.")
        return (x, y), (row, col)

    def _set_order_date_by_uia(self, date_control, expected_date, focus_control):
        """Set a Nebula CDateTime date by typing into its segments.

        CDateTime is a segmented widget, not a text box: Ctrl+A, SetValue or pasting
        a whole date can leave a wrong year. Fast path first (click the month, type
        MMDDYYYY, read back); if the readback is off, each segment is selected,
        verified by probing and typed on its own. Clicks are relative to the Date
        control's own rectangle.
        """
        from .order_ui import interpret_date
        from pywinauto import keyboard
        from win32gui import GetForegroundWindow, GetAncestor
        from win32con import GA_ROOT

        # Make sure Fakturama, not the console, receives the keystrokes.
        self.window.set_focus()
        time.sleep(0.40)
        if GetAncestor(GetForegroundWindow(), GA_ROOT) != self.window.handle:
            raise UIError("Fakturama is not in foreground; no date keys were sent.")

        r = date_control.rectangle()
        if not (100 <= r.width() <= 175 and 18 <= r.height() <= 36):
            raise UIError(f"Unexpected Date Edit bounds {r}; refusing to type.")

        before = date_control.get_value().strip()
        if not re.fullmatch(r"[A-Za-z]{3,9}\s+\d{1,2},\s*\d{2,4}", before):
            raise UIError(f"Date does not look like MMM d, yyyy: {before!r}")
        print("Date text-entry starting from:", repr(before))
        print("Using year, month, day segments; no full-field Ctrl+A or paste.")

        def read():
            return date_control.get_value().strip()

        def probe_selected_segment():
            """Return which CDateTime field currently has the selection.

            Arrow Up increments only the selected field (the widget redraws its text
            immediately), Arrow Down restores it. This is the only observable that
            does not depend on where the click landed, so it is used instead of
            trusting the click position. The day is checked first because a month
            roll-over can also move the day (Jan 31 -> Feb 28)."""
            before_probe = interpret_date(read())
            keyboard.send_keys("{UP}", pause=0.05)
            time.sleep(0.15)
            after_up = interpret_date(read())
            keyboard.send_keys("{DOWN}", pause=0.05)
            time.sleep(0.15)
            restored = interpret_date(read())
            if after_up == before_probe:
                raise UIError("Arrow Up did not change the Date; no segment is selected.")
            if restored != before_probe:
                raise UIError(f"Date probe did not restore: {before_probe} -> {restored}")
            if after_up.day != before_probe.day and after_up.month == before_probe.month:
                return "day"
            if after_up.month != before_probe.month and after_up.day == before_probe.day:
                return "month"
            if after_up.year != before_probe.year and (after_up.month, after_up.day) == (
                before_probe.month,
                before_probe.day,
            ):
                return "year"
            # Day and month both moved (a roll-over); rare, so stop rather than guess.
            raise UIError(f"Ambiguous Date segment probe: {before_probe} -> {after_up}")

        segment_order = ("month", "day", "year")

        def select_and_type(name, proportion, value):
            # The click is a first guess (font, one- or two-digit day); the probe decides.
            x = round(r.width() * proportion)
            y = round(r.height() * 0.50)
            if not (8 < x < r.width() - 20 and 5 < y < r.height() - 5):
                raise UIError(f"{name} click position is outside safe Date region.")
            date_control.click_input(coords=(x, y))
            time.sleep(0.15)
            selected = probe_selected_segment()
            for _ in range(2):
                if selected == name:
                    break
                step = segment_order.index(name) - segment_order.index(selected)
                keyboard.send_keys("{RIGHT}" if step > 0 else "{LEFT}", pause=0.05)
                time.sleep(0.15)
                selected = probe_selected_segment()
            if selected != name:
                raise UIError(f"Could not select the {name} segment (landed on {selected}).")
            print(f"Selected {name} segment")
            keyboard.send_keys(str(value), pause=0.12)
            time.sleep(0.25)
            # Move focus to a harmless field so the widget commits.
            focus_control.click_input()
            time.sleep(0.30)
            observed = read()
            print(f"After {name}: {observed!r}")
            return observed

        def fast_path():
            """One click on the month segment, then MMDDYYYY typed straight through.

            CDateTime moves to the next field once a field is complete, so the
            normal case is a single click and eight keystrokes with no visible
            probing. The readback decides: anything but the exact date falls back
            to the verified per-segment path below, which repairs any state.
            """
            x = round(r.width() * 0.12)
            y = round(r.height() * 0.50)
            date_control.click_input(coords=(x, y))
            time.sleep(0.15)
            keyboard.send_keys(
                f"{expected_date.month:02d}{expected_date.day:02d}{expected_date.year:04d}",
                pause=0.08,
            )
            time.sleep(0.25)
            focus_control.click_input()
            time.sleep(0.30)
            observed = read()
            print(f"After fast path: {observed!r}")
            try:
                return interpret_date(observed) == expected_date, observed
            except Exception:
                return False, observed

        try:
            ok, observed = fast_path()
            if ok:
                print("PASS: Date committed by fast path:", observed)
                return observed
            print("Fast path readback differs; using verified per-segment entry.")
            # Year first: a two-digit year left by an earlier attempt must be repaired.
            after_year = select_and_type("year", 0.57, f"{expected_date.year:04d}")
            if not re.search(r",\s*" + re.escape(str(expected_date.year)) + r"$", after_year):
                raise UIError("Year segment was not committed as 4 digits; STOP.")

            after_month = select_and_type("month", 0.12, f"{expected_date.month:02d}")
            if not (
                after_month[:3].casefold() == expected_date.strftime("%b").casefold()
                and re.search(r",\s*" + re.escape(str(expected_date.year)) + r"$", after_month)
            ):
                raise UIError("Month segment failed or year changed; STOP.")

            after_day = select_and_type("day", 0.34, f"{expected_date.day:02d}")
            try:
                parsed = interpret_date(after_day)
            except Exception as exc:
                raise UIError(f"Date still has an invalid format: {after_day!r}") from exc
            if parsed != expected_date:
                raise UIError(f"Date mismatch: expected {expected_date}, got {after_day!r}")
            print("PASS: Date committed by segments:", after_day)
            return after_day
        except Exception:
            self.screenshot("date_segment_NEEDS_REVIEW_unsaved")
            raise

    def order_header_prepare(self, order):
        """Prepare header of the ORIGINAL unsaved Order only; NO Save clicks."""
        from .order_ui import order_header_layout, interpret_date, header_values

        self.activate("New Order")
        rows = self.ui_snapshot()
        fields = order_header_layout(rows)
        current = header_values(rows)
        print("Before:", current)
        if not current["number"] or not current["number"].strip():
            raise UIError("No autogenerated Order number: abort")
        if current["reference"] not in ("", order.reference):
            raise UIError(f'Existing Cust.Ref. differs: {current["reference"]!r}')
        if current["price_mode"] != "Net" or current["vat"] != "With VAT":
            raise UIError(
                "Order must already be Net / With VAT. Aborting instead of changing mode."
            )
        try:
            before_date = interpret_date(current["date"])
        except Exception:
            # A two-digit year is never accepted as committed; the segment entry repairs it.
            if not re.fullmatch(r"[A-Za-z]{3,9} \d{1,2}, \d{2,4}", current["date"].strip()):
                raise UIError("Current Date has an unexpected format. No fields were edited.")
            before_date = None
            print("Current Date has a malformed year; year-segment repair required.")
        print("Target:", order.order_date, order.reference)
        if before_date == order.order_date and current["reference"] == order.reference:
            print("Both target header fields already match; no changes needed.")
            return
        if input("Type HEADER to change only Date/Cust.Ref. (NO SAVE): ").strip() != "HEADER":
            print("Cancelled without editing.")
            return
        try:
            if before_date != order.order_date:
                date_ctrl = fields["date"]["_wrapper"]
                committed = self._set_order_date_by_uia(
                    date_ctrl, order.order_date, fields["reference"]["_wrapper"]
                )
                print("Date committed:", committed)
            if current["reference"] != order.reference:
                ref_ctrl = fields["reference"]["_wrapper"]
                ref_ctrl.click_input()
                ref_ctrl.type_keys("^a{BACKSPACE}")
                ref_ctrl.type_keys(order.reference, with_spaces=True, pause=0.04)
            # TAB commits the field without opening a selector.
            fields["reference"]["_wrapper"].type_keys("{TAB}")
            import time

            time.sleep(0.4)
            final = header_values(self.ui_snapshot())
            print("After: ", final)
            if final["number"] != current["number"]:
                raise UIError("Auto Order number changed; stop without saving.")
            if interpret_date(final["date"]) != order.order_date:
                raise UIError("Order Date failed readback. Do not save.")
            if final["reference"] != order.reference:
                raise UIError("Cust.Ref. failed readback. Do not save.")
            if final["price_mode"] != "Net" or final["vat"] != "With VAT":
                raise UIError("Net/With VAT changed unexpectedly. Do not save.")
            print("PASS: Order header matches source. Original Order remains UNSAVED.")
            self.screenshot("order_header_ready_unsaved")
        except Exception:
            self.screenshot("order_header_NEEDS_REVIEW")
            raise

    def probe_address_selector(self):
        """Open the upper (existing contact) address icon; never the lower + icon.

        The two icons are unnamed Image controls; their rectangles come from UIA,
        grounded between the Addresses label, the Invoice address tab and Items.
        """
        from pywinauto import Desktop
        from pywinauto.win32structures import RECT
        from datetime import datetime
        from pathlib import Path
        import json
        import time

        self.activate("New Order")
        rows = self.ui_snapshot()
        from .order_ui import header_values

        header = header_values(rows)
        print("Original Order:", header["number"])
        print("Date:", header["date"])
        print("Cust.Ref.:", header["reference"])

        if not header["number"] or not header["reference"]:
            raise UIError("Order number or Cust.Ref. missing. No click attempted.")

        address_label = self.unique("Text", "Addresses").rectangle()
        invoice_tab = self.unique("TabItem", "Invoice address").rectangle()
        items_label = self.unique("Text", "Items").rectangle()

        # Upper image: select existing contact. Lower image: create new contact.
        candidates = []
        for icon in self.controls(kind="Image"):
            try:
                r = icon.rectangle()
                in_strip = (
                    r.left >= address_label.left
                    and r.right <= invoice_tab.left + 2
                    and r.top >= invoice_tab.bottom
                    and r.bottom <= items_label.top - 10
                    and 12 <= r.width() <= 30
                    and 12 <= r.height() <= 30
                )
                if in_strip:
                    candidates.append(icon)
            except Exception:
                continue

        candidates.sort(key=lambda c: c.rectangle().top)
        if len(candidates) != 2:
            self.screenshot("address_icon_grounding_failed")
            raise UIError(
                f"Expected exactly 2 UIA Image controls beside Addresses; "
                f"found {len(candidates)}. No click attempted."
            )

        upper, lower = candidates
        ur, lr = upper.rectangle(), lower.rectangle()
        if ur.bottom > lr.top or lr.top - ur.top < 18:
            raise UIError("Cannot distinguish existing-contact icon from green +. No click.")
        x, y = rect_center(ur)
        print("Existing-contact icon rectangle:", (ur.left, ur.top, ur.right, ur.bottom))
        print("Safe click CENTER:", (x, y))
        print("Create-new (green +) icon is BELOW at:", rect_center(lr))
        self.screenshot("before_address_selector")

        if input("Type OPEN ADDRESS to open selector (NO SAVE): ").strip() != "OPEN ADDRESS":
            print("Cancelled without clicking.")
            return

        # The console may be in front; bring Fakturama forward before clicking.
        self.window.set_focus()
        time.sleep(0.4)
        from win32gui import GetForegroundWindow

        if GetForegroundWindow() != self.window.handle:
            raise UIError("Fakturama not foreground. No click attempted.")

        # Click the image's centre; the lower green + would create a contact.
        upper.click_input()
        time.sleep(0.8)

        # A window capture omits top-level SWT dialogs; grab the whole desktop.
        from PIL import ImageGrab

        popup_screenshot = (
            self.evidence / f"{datetime.now():%Y%m%d_%H%M%S}_address_selector_desktop.png"
        )
        ImageGrab.grab(all_screens=True).save(popup_screenshot)
        print("Desktop screenshot:", popup_screenshot)

        def is_address_dialog(label):
            label = label.strip().casefold()
            return "address" in label and ("select" in label or "choose" in label)

        # Dialogs may be top-level windows or nested UIA children.
        dialogs = []
        windows_inventory = []
        for w in Desktop(backend="uia").windows(visible_only=True):
            try:
                if w.process_id() != self.pid:
                    continue
                title = w.window_text().strip()
                r = w.rectangle()
                windows_inventory.append(
                    {"name": title, "rect": [r.left, r.top, r.right, r.bottom]}
                )
                if is_address_dialog(title):
                    dialogs.append((title, w))
            except Exception:
                continue

        if not dialogs:
            for c in self.controls():
                try:
                    title = c.window_text().strip()
                    if is_address_dialog(title):
                        dialogs.append((title, c))
                except Exception:
                    continue

        if len(dialogs) != 1:
            diagnostics = (
                self.evidence
                / f"{datetime.now():%Y%m%d_%H%M%S}_address_selector_window_diagnostics.json"
            )
            diagnostics.write_text(
                json.dumps(
                    {
                        "candidate_dialogs": [title for title, _ in dialogs],
                        "fakturama_windows": windows_inventory,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            print("Window diagnostics:", diagnostics)
            raise UIError(
                f"Expected one Select the address dialog, found {len(dialogs)}. "
                "No further click, create or save attempted. "
                "Please share the desktop screenshot and diagnostics JSON."
            )

        title, target = dialogs[0]
        print("SUCCESS: Address selector opened:", title)
        popup_inventory = []
        for c in target.descendants():
            try:
                if not c.is_visible():
                    continue
                r = c.rectangle()
                kind = c.element_info.control_type
                record = {
                    "type": kind,
                    "name": c.window_text().strip(),
                    "rect": [r.left, r.top, r.right, r.bottom],
                }
                if kind == "Edit":
                    try:
                        record["value"] = c.get_value()
                    except Exception:
                        pass
                popup_inventory.append(record)
            except Exception:
                continue
        inventory_path = (
            self.evidence / f"{datetime.now():%Y%m%d_%H%M%S}_address_selector_controls.json"
        )
        inventory_path.write_text(
            json.dumps(popup_inventory, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print("Selector UIA inventory:", inventory_path)
        print("No customer search or selection was performed. Original Order remains UNSAVED.")

    def print_inspection(self, title=None):
        scope = None
        if title:
            tab = self.activate(title)
            scope = self.editor_bounds(tab)
        for c in self.controls(scope=scope):
            typ = c.element_info.control_type
            if typ not in ("Text", "Edit", "ComboBox", "Button", "TabItem", "CheckBox"):
                continue
            txt = c.window_text().strip()
            if typ == "Edit":
                try:
                    txt += f" value={c.get_value()!r}"
                except Exception:
                    pass
            if typ == "ComboBox":
                try:
                    txt += f" selected={c.selected_text()!r}"
                except Exception:
                    pass
            if txt:
                print(f"{typ:12} {txt[:105]}")

    def deep_inspection(self, title=None):
        """Read-only UIA inventory of the whole window, for diagnostics."""
        if title:
            tab = self.activate(title)
            target_tab_rect = tab.rectangle()
            print(
                f"Activated tab: {tab.window_text()!r} "
                f"rect=({target_tab_rect.left},{target_tab_rect.top},"
                f"{target_tab_rect.right},{target_tab_rect.bottom})"
            )
        else:
            target_tab_rect = None

        active_window = self.window.rectangle()
        snapshot = []
        allowed = {
            "Text",
            "Edit",
            "ComboBox",
            "Button",
            "TabItem",
            "CheckBox",
            "RadioButton",
            "ListItem",
            "List",
            "Table",
            "DataGrid",
            "Tree",
            "TreeItem",
            "Custom",
            "Pane",
            "ToolBar",
            "MenuItem",
            "Document",
            "ScrollBar",
            "Group",
            "Hyperlink",
        }

        for c in self.window.descendants():
            try:
                if not c.is_visible():
                    continue
                typ = c.element_info.control_type
                if typ not in allowed:
                    continue
                r = c.rectangle()
                if r.width() <= 0 or r.height() <= 0:
                    continue
                # Skip off-window and virtual elements.
                if (
                    r.right < active_window.left
                    or r.left > active_window.right
                    or r.bottom < active_window.top
                    or r.top > active_window.bottom
                ):
                    continue
                row = {
                    "type": typ,
                    "name": c.window_text().strip(),
                    "automation_id": str(c.element_info.automation_id or ""),
                    "class_name": str(c.element_info.class_name or ""),
                    "rect": [r.left, r.top, r.right, r.bottom],
                    "enabled": bool(c.is_enabled()),
                }
                if typ == "Edit":
                    try:
                        row["value"] = c.get_value()
                    except Exception:
                        pass
                elif typ == "ComboBox":
                    try:
                        row["selected"] = c.selected_text().strip()
                    except Exception:
                        pass
                elif typ == "CheckBox":
                    try:
                        row["checked"] = bool(c.get_toggle_state())
                    except Exception:
                        pass
                snapshot.append(row)
            except Exception as exc:
                self.log.debug("Skipped unreadable UIA element: %s", exc)

        snapshot.sort(key=lambda row: (row["rect"][1], row["rect"][0], row["type"]))
        filename = self.evidence / f"{datetime.now():%Y%m%d_%H%M%S}_uia_full_inspection.json"
        filename.write_text(
            json.dumps(
                {
                    "window": self.window.window_text(),
                    "active_tab": title,
                    "window_rect": [
                        active_window.left,
                        active_window.top,
                        active_window.right,
                        active_window.bottom,
                    ],
                    "control_count": len(snapshot),
                    "controls": snapshot,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        # Short console summary; the JSON holds the full inventory.
        anchors = (
            "addresses",
            "items",
            "vat",
            "total",
            "discount",
            "cust.ref",
            "date",
            "shipping",
            "remark",
            "no.",
        )
        interesting = [row for row in snapshot if any(k in row["name"].casefold() for k in anchors)]
        print(f"\nRead-only whole-window inspection: {len(snapshot)} controls")
        print(f"UIA inventory JSON: {filename}")
        print("\nORDER ANCHORS (name, type, rectangle):")
        if not interesting:
            print("No anchors found; use the JSON to inspect all controls.")
        for row in interesting[:75]:
            print(f"  {row['type']:10} {row['name'][:55]:55} {row['rect']}")

        # Include unnamed controls near Addresses/Items: the selector icons have no names.
        print("\nCONTROLS NEAR ADDRESSES / ITEMS:")
        targets = [
            row
            for row in interesting
            if any(k in row["name"].casefold() for k in ("addresses", "items"))
        ]
        if not targets:
            print("No visible Addresses/Items anchors detected.")
        shown = set()
        for target in targets[:5]:
            tr = target["rect"]
            ty = (tr[1] + tr[3]) / 2
            neighbors = [
                row
                for row in snapshot
                if row["type"]
                in ("Button", "Text", "ComboBox", "Custom", "Table", "DataGrid", "Edit", "Pane")
                and abs((row["rect"][1] + row["rect"][3]) / 2 - ty) < 95
                and row["rect"][0] >= tr[0] - 60
                and row["rect"][0] <= tr[2] + 640
            ]
            for row in neighbors[:30]:
                key = (row["type"], row["name"], tuple(row["rect"]))
                if key in shown:
                    continue
                shown.add(key)
                print(f"  {row['type']:10} {row['name'][:45]:45} {row['rect']}")
        print("\nNo Fakturama fields were edited or saved.")
        return filename
