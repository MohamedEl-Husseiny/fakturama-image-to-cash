"""Offline regression for CDateTime segment entry.

Reproduces the 7 November 2026 live stop: with a one-digit day ('Nov 7, 2026') the
field text is shorter, the fixed 0.34-width click for the day lands on the year
segment and '14' became year 0014. The adapter must detect the selected segment by
probing (Arrow Up / Down) and steer with Arrow Left / Right instead of trusting the
click position. Runs on any OS: Windows-only modules are stubbed.
"""

import sys
import types
from datetime import date

import pytest


class FakeCDateTime:
    """Segmented date widget: text 'MMM d, yyyy', one selected field, digit entry
    that auto-advances when a field is complete, Up/Down adjust the selected field."""

    def __init__(self, value, width=140, height=24, auto_advance=True):
        self.value = value
        self.auto_advance = auto_advance
        self.selected = None
        self.typed = ""
        self.width, self.height = width, height
        self.clicks = []
        self.keys = []

    # --- what the adapter sees -------------------------------------------------
    def get_value(self):
        return self.value.strftime("%b %d, %Y").replace(" 0", " ")

    def rectangle(self):
        w, h = self.width, self.height
        return types.SimpleNamespace(
            left=0, top=0, right=w, bottom=h, width=lambda: w, height=lambda: h
        )

    def click_input(self, coords):
        x, _ = coords
        self.clicks.append(x)
        # Approximate proportional font layout of the current text: each glyph ~6%
        # of the field width after a 5% left padding, so a one-digit day shifts the
        # comma and year leftwards. This is what made 0.34 hit the year live.
        text = self.get_value()
        per_char = 0.055 * self.width
        idx = int((x - 0.05 * self.width) / per_char)
        idx = max(0, min(idx, len(text) - 1))
        month_end = 3
        day_end = text.index(",")
        if idx < month_end + 1:
            self.selected = "month"
        elif idx < day_end:
            self.selected = "day"
        else:
            self.selected = "year"
        self.typed = ""

    # --- keyboard --------------------------------------------------------------
    def send_keys(self, keys, pause=0):
        self.keys.append(keys)
        if keys in ("{UP}", "{DOWN}"):
            self._adjust(1 if keys == "{UP}" else -1)
        elif keys in ("{LEFT}", "{RIGHT}"):
            order = ["month", "day", "year"]
            i = order.index(self.selected) + (1 if keys == "{RIGHT}" else -1)
            self.selected = order[max(0, min(2, i))]
            self.typed = ""
        else:
            for ch in keys:
                assert ch.isdigit()
                self._type_digit(ch)

    def _adjust(self, delta):
        v = self.value
        if self.selected == "day":
            self.value = v.replace(day=v.day + delta)
        elif self.selected == "month":
            self.value = v.replace(month=v.month + delta)
        else:
            self.value = v.replace(year=v.year + delta)

    def _type_digit(self, ch):
        width = {"month": 2, "day": 2, "year": 4}[self.selected]
        self.typed = (self.typed + ch)[-width:]  # overtype keeps the last digits
        v = self.value
        n = int(self.typed)
        if n >= 1:  # a leading 0 is pending until the next digit, as in the real widget
            try:
                if self.selected == "day":
                    self.value = v.replace(day=n)
                elif self.selected == "month":
                    self.value = v.replace(month=n)
                else:
                    self.value = v.replace(year=n)
            except ValueError:
                pass  # the real widget ignores an impossible value
        if self.auto_advance and len(self.typed) == width:  # like the real widget
            order = ["month", "day", "year"]
            self.selected = order[min(2, order.index(self.selected) + 1)]
            self.typed = ""


@pytest.fixture
def adapter(monkeypatch):
    """FakturamaUI with the Windows-only imports stubbed so the test runs anywhere."""
    widget_holder = {}
    keyboard = types.SimpleNamespace(
        send_keys=lambda keys, pause=0: widget_holder["w"].send_keys(keys)
    )
    monkeypatch.setitem(sys.modules, "pywinauto", types.SimpleNamespace(keyboard=keyboard))
    monkeypatch.setitem(sys.modules, "pywinauto.keyboard", keyboard)
    monkeypatch.setitem(
        sys.modules,
        "win32gui",
        types.SimpleNamespace(GetForegroundWindow=lambda: 1, GetAncestor=lambda h, f: 1),
    )
    monkeypatch.setitem(sys.modules, "win32con", types.SimpleNamespace(GA_ROOT=2))
    monkeypatch.setattr("time.sleep", lambda s: None)

    from fakturama.ui import FakturamaUI

    app = FakturamaUI.__new__(FakturamaUI)
    app.window = types.SimpleNamespace(set_focus=lambda: None, handle=1)
    app.screenshot = lambda label: None
    focus = types.SimpleNamespace(click_input=lambda: None)

    def run(widget, expected):
        widget_holder["w"] = widget
        return app._set_order_date_by_uia(widget, expected, focus)

    return run


def test_fast_path_is_one_click_and_eight_keystrokes(adapter):
    widget = FakeCDateTime(date(2026, 11, 7))
    assert adapter(widget, date(2026, 7, 14)) == "Jul 14, 2026"
    assert widget.keys == ["07142026"]
    assert len(widget.clicks) == 1


def test_one_digit_day_is_steered_from_year_to_day_segment(adapter):
    # No auto-advance: the fast path leaves a wrong date, so the verified
    # per-segment path must take over and steer the day click off the year.
    widget = FakeCDateTime(date(2026, 11, 7), auto_advance=False)
    # Sanity: with this text the old fixed day click really lands on the year.
    widget.click_input((round(140 * 0.34), 12))
    assert widget.selected == "year"

    assert adapter(widget, date(2026, 7, 14)) == "Jul 14, 2026"
    assert widget.value == date(2026, 7, 14)
    assert "{LEFT}" in widget.keys  # steered back from year to day


def test_two_digit_day_layout_still_works(adapter):
    widget = FakeCDateTime(date(2026, 10, 17), auto_advance=False)
    assert adapter(widget, date(2026, 7, 14)) == "Jul 14, 2026"
    assert widget.value == date(2026, 7, 14)


def test_probe_always_restores_before_typing(adapter):
    widget = FakeCDateTime(date(2026, 11, 7), auto_advance=False)
    adapter(widget, date(2026, 7, 14))
    ups = widget.keys.count("{UP}")
    downs = widget.keys.count("{DOWN}")
    assert ups == downs >= 3


def test_unselectable_segment_stops(adapter):
    class Stuck(FakeCDateTime):
        def send_keys(self, keys, pause=0):
            if keys in ("{UP}", "{DOWN}"):
                return  # widget ignores arrows: nothing observable changes
            super().send_keys(keys, pause)

    from fakturama.ui import UIError

    with pytest.raises(UIError, match="no segment is selected"):
        adapter(Stuck(date(2026, 11, 7), auto_advance=False), date(2026, 7, 14))
