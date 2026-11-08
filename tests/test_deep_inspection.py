"""Deep inspection is read-only and does not truncate at nested tabs."""

import json
import logging
from types import SimpleNamespace

from fakturama.ui import FakturamaUI


class Rect:
    def __init__(self, left, top, right, bottom):
        self.left, self.top, self.right, self.bottom = left, top, right, bottom

    def width(self):
        return self.right - self.left

    def height(self):
        return self.bottom - self.top


class Element:
    def __init__(self, kind, name, rect, *, value=None):
        self.kind, self.name, self.rect, self.value = kind, name, rect, value
        self.element_info = SimpleNamespace(control_type=kind, automation_id="", class_name="")

    def window_text(self):
        return self.name

    def rectangle(self):
        return self.rect

    def is_visible(self):
        return True

    def is_enabled(self):
        return True

    def get_value(self):
        return self.value


class Window(Element):
    def __init__(self, children):
        super().__init__("Window", "Fakturama - demo", Rect(0, 0, 1800, 1000))
        self.children = children

    def descendants(self):
        return self.children


def test_whole_window_inspection_survives_nested_tabs(tmp_path):
    app = FakturamaUI.__new__(FakturamaUI)
    app.window = Window(
        [
            Element("Text", "Date", Rect(100, 100, 150, 124)),
            Element("Edit", "", Rect(160, 100, 400, 124), value="Oct 12, 2026"),
            Element("TabItem", "Remarks", Rect(100, 200, 300, 230)),
            Element("Text", "Addresses", Rect(100, 420, 250, 450)),
            Element("Button", "", Rect(300, 425, 320, 445)),
            Element("Text", "Items", Rect(100, 700, 250, 730)),
        ]
    )
    app.evidence = tmp_path
    app.log = logging.getLogger("test-inspection")

    path = app.deep_inspection()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["control_count"] == 6
    assert [x["name"] for x in data["controls"] if x["name"] in ("Addresses", "Items")] == [
        "Addresses",
        "Items",
    ]
    assert any(x["value"] == "Oct 12, 2026" for x in data["controls"] if x["type"] == "Edit")
