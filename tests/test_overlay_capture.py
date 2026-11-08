"""Overlay-proof OCR capture.

Reproduces the 7 Nov 2026 22:52 stop: a Google Meet "sharing your screen" banner
(a Chrome window, always on top, never focused) sat over the Documents table and its
text was OCR'd as a document row. Captures now use the window's own rendered content
first; if that is unavailable, a screen grab is allowed only when no other process's
window overlaps the region.
"""

from types import SimpleNamespace

import pytest

from fakturama.stages.common import grab_region
from fakturama.ui import FakturamaUI, WindowObstructionError, foreign_windows_over

FAKTURAMA = 0x1000
PID = 4242


def win(handle, pid, rect, *, visible=True, iconic=False, cloaked=False, title="", cls=""):
    return {
        "handle": handle,
        "pid": pid,
        "visible": visible,
        "iconic": iconic,
        "cloaked": cloaked,
        "rect": rect,
        "title": title,
        "cls": cls,
    }


TABLE = (380, 300, 1900, 1030)


def test_meet_banner_over_table_is_reported():
    banner = win(
        0x2000, 999, (700, 960, 1230, 1030), title="meet.google.com is sharing your screen"
    )
    zorder = [banner, win(FAKTURAMA, PID, (0, 0, 1938, 1038)), win(0x3000, 7, (0, 0, 1938, 1038))]
    assert foreign_windows_over(TABLE, FAKTURAMA, PID, zorder) == [banner]


def test_windows_behind_fakturama_are_ignored():
    behind = win(0x3000, 7, (0, 0, 1938, 1038), title="Explorer")
    zorder = [win(FAKTURAMA, PID, (0, 0, 1938, 1038)), behind]
    assert foreign_windows_over(TABLE, FAKTURAMA, PID, zorder) == []


def test_own_dialogs_minimised_cloaked_and_non_overlapping_windows_are_ignored():
    zorder = [
        win(0x2001, PID, (500, 400, 1200, 800), title="Select the address"),  # own dialog
        win(0x2002, 999, (500, 400, 1200, 800), iconic=True),
        win(0x2003, 999, (500, 400, 1200, 800), cloaked=True),
        win(0x2004, 999, (500, 400, 1200, 800), visible=False),
        win(0x2005, 999, (0, 0, 300, 200), title="Clock"),  # left of the table
        win(0x2006, 999, (0, 0, 0, 0)),  # empty rect
        win(FAKTURAMA, PID, (0, 0, 1938, 1038)),
    ]
    assert foreign_windows_over(TABLE, FAKTURAMA, PID, zorder) == []


def make_ui(monkeypatch, *, rendered, overlaps):
    ui = FakturamaUI.__new__(FakturamaUI)
    ui.window = SimpleNamespace(handle=FAKTURAMA)
    ui.pid = PID
    calls = []
    monkeypatch.setattr(
        "fakturama.ui.render_window_region",
        lambda hwnd, bbox: (calls.append(("render", hwnd, bbox)), rendered)[1],
    )
    monkeypatch.setattr("fakturama.ui._enumerate_top_level_windows", lambda: overlaps)
    monkeypatch.setattr(
        "PIL.ImageGrab.grab", lambda bbox=None, **k: (calls.append(("screen", bbox)), "SCREEN")[1]
    )
    return ui, calls


def test_rendered_window_content_is_preferred_even_with_banner_present(monkeypatch):
    banner = win(
        0x2000, 999, (700, 960, 1230, 1030), title="meet.google.com is sharing your screen"
    )
    ui, calls = make_ui(
        monkeypatch, rendered="RENDERED", overlaps=[banner, win(FAKTURAMA, PID, (0, 0, 1938, 1038))]
    )
    assert ui.grab(TABLE, "Documents table") == "RENDERED"
    assert calls == [("render", FAKTURAMA, TABLE)]  # no screen grab at all


def test_screen_grab_fallback_only_when_unobstructed(monkeypatch):
    ui, calls = make_ui(
        monkeypatch, rendered=None, overlaps=[win(FAKTURAMA, PID, (0, 0, 1938, 1038))]
    )
    assert ui.grab(TABLE, "Documents table") == "SCREEN"
    assert calls[-1] == ("screen", TABLE)


def test_screen_grab_fallback_refuses_under_banner(monkeypatch):
    banner = win(
        0x2000, 999, (700, 960, 1230, 1030), title="meet.google.com is sharing your screen"
    )
    ui, calls = make_ui(
        monkeypatch, rendered=None, overlaps=[banner, win(FAKTURAMA, PID, (0, 0, 1938, 1038))]
    )
    with pytest.raises(WindowObstructionError, match="meet.google.com"):
        ui.grab(TABLE, "Documents table")
    assert ("screen", TABLE) not in calls


def test_dialog_handle_is_passed_through(monkeypatch):
    ui, calls = make_ui(monkeypatch, rendered="RENDERED", overlaps=[])
    runner = SimpleNamespace(app=ui)
    assert grab_region(runner, (10, 10, 50, 50), "selector dialog", hwnd=0x2001) == "RENDERED"
    assert calls == [("render", 0x2001, (10, 10, 50, 50))]


def test_grab_region_without_adapter_uses_plain_screen_grab(monkeypatch):
    seen = []
    monkeypatch.setattr(
        "PIL.ImageGrab.grab", lambda bbox=None, **k: (seen.append(bbox), "SCREEN")[1]
    )
    assert grab_region(SimpleNamespace(), (1, 2, 3, 4), "x") == "SCREEN"
    assert seen == [(1, 2, 3, 4)]
