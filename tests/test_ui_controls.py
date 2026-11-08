"""Offline checks for FakturamaUI.controls(): the control-type filter must be pushed
into the UIA query, and the Python-side name/visibility/scope filters must still apply."""

from types import SimpleNamespace

from fakturama.ui import FakturamaUI


class Rect:
    def __init__(self, left, top, right, bottom):
        self.left, self.top, self.right, self.bottom = left, top, right, bottom


def element(kind, name, rect, visible=True):
    return SimpleNamespace(
        element_info=SimpleNamespace(control_type=kind),
        window_text=lambda: name,
        is_visible=lambda: visible,
        rectangle=lambda: rect,
    )


def make_app(elements, calls):
    def descendants(**kwargs):
        calls.append(kwargs)
        kind = kwargs.get("control_type")
        return [e for e in elements if kind is None or e.element_info.control_type == kind]

    app = FakturamaUI.__new__(FakturamaUI)
    app.window = SimpleNamespace(descendants=descendants)
    return app


def test_kind_filter_is_pushed_into_uia_query():
    calls = []
    edit = element("Edit", "Date", Rect(0, 0, 10, 10))
    app = make_app([element("Text", "Date", Rect(0, 0, 10, 10)), edit], calls)
    assert app.controls(kind="Edit") == [edit]
    assert calls == [{"control_type": "Edit"}]


def test_no_kind_walks_whole_tree():
    calls = []
    app = make_app([element("Text", "A", Rect(0, 0, 10, 10))], calls)
    assert len(app.controls()) == 1
    assert calls == [{}]


def test_name_visibility_and_scope_filters_still_apply():
    calls = []
    inside = element("Edit", " Date ", Rect(100, 100, 120, 120))
    hidden = element("Edit", "Date", Rect(100, 100, 120, 120), visible=False)
    outside = element("Edit", "Date", Rect(900, 900, 920, 920))
    other = element("Edit", "Other", Rect(100, 100, 120, 120))
    app = make_app([inside, hidden, outside, other], calls)
    scope = Rect(0, 0, 500, 500)
    assert app.controls(kind="Edit", name="Date", scope=scope) == [inside]


def test_activate_does_not_click_an_already_selected_tab():
    clicks = []
    tab = SimpleNamespace(is_selected=lambda: True, click_input=lambda: clicks.append(1))
    app = FakturamaUI.__new__(FakturamaUI)
    app.tabs = lambda title: [tab]
    assert app.activate("New Debtor") is tab
    assert clicks == []


def test_activate_clicks_when_not_selected_or_unknown(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    for selected in (lambda: False, lambda: (_ for _ in ()).throw(RuntimeError("no pattern"))):
        clicks = []
        tab = SimpleNamespace(is_selected=selected, click_input=lambda: clicks.append(1))
        app = FakturamaUI.__new__(FakturamaUI)
        app.tabs = lambda title: [tab]
        app.activate("New Debtor")
        assert clicks == [1]


def make_cached_app(monkeypatch, cached_elements, walked_elements, walked_calls):
    """Fake pywinauto so the FindAllBuildCache path runs offline. ``cached_elements``
    are (name, rect, offscreen) tuples; ``walked_elements`` is what descendants()
    would return if the slow path were used."""
    import sys
    import types

    class FakeElement:
        def __init__(self, name, rect, offscreen, kind="Edit"):
            self.CachedIsOffscreen = offscreen
            self.CachedBoundingRectangle = rect
            self.CachedControlType = {"Edit": 50004, "Text": 50020}[kind]
            self.name = name

    found = [FakeElement(*c) for c in cached_elements]
    array = SimpleNamespace(Length=len(found), GetElement=lambda i: found[i])
    queries = []

    def find_all(scope, condition, request):
        queries.append((scope, condition, request))
        return array

    iuia = SimpleNamespace(
        CreateCacheRequest=lambda: SimpleNamespace(AddProperty=lambda p: None),
        CreateOrCondition=lambda a, b: ("or", a, b),
    )
    fake_uia = SimpleNamespace(
        iuia=iuia,
        UIA_dll=SimpleNamespace(
            UIA_ControlTypePropertyId=0,
            UIA_IsOffscreenPropertyId=1,
            UIA_BoundingRectanglePropertyId=2,
        ),
        build_condition=lambda control_type=None: ("type", control_type),
        true_condition="true",
        known_control_types={"Edit": 50004, "Text": 50020},
        tree_scope={"descendants": "descendants"},
    )
    pkg = types.ModuleType("pywinauto")
    controls = types.ModuleType("pywinauto.controls")
    uiawrapper = types.ModuleType("pywinauto.controls.uiawrapper")
    uiawrapper.UIAWrapper = lambda info: SimpleNamespace(
        window_text=lambda: info.name,
        rectangle=lambda: info.CachedBoundingRectangle,
        element=info,
    )
    uia_defines = types.ModuleType("pywinauto.uia_defines")
    uia_defines.IUIA = lambda: fake_uia
    uia_element_info = types.ModuleType("pywinauto.uia_element_info")
    uia_element_info.UIAElementInfo = lambda e: e
    for name, module in {
        "pywinauto": pkg,
        "pywinauto.controls": controls,
        "pywinauto.controls.uiawrapper": uiawrapper,
        "pywinauto.uia_defines": uia_defines,
        "pywinauto.uia_element_info": uia_element_info,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    def descendants(**kwargs):
        walked_calls.append(kwargs)
        return walked_elements

    app = FakturamaUI.__new__(FakturamaUI)
    app.window = SimpleNamespace(
        descendants=descendants,
        element_info=SimpleNamespace(element=SimpleNamespace(FindAllBuildCache=find_all)),
    )
    return app, queries


def test_cached_query_filters_on_cached_visibility_and_scope(monkeypatch):
    walked = []
    app, queries = make_cached_app(
        monkeypatch,
        [
            (" Date ", Rect(100, 100, 120, 120), False),
            ("Date", Rect(100, 100, 120, 120), True),
            ("Date", Rect(900, 900, 920, 920), False),
            ("Other", Rect(100, 100, 120, 120), False),
        ],
        walked_elements=[],
        walked_calls=walked,
    )
    found = app.controls(kind="Edit", name="Date", scope=Rect(0, 0, 500, 500))
    assert [c.window_text() for c in found] == [" Date "]
    assert queries == [("descendants", ("type", "Edit"), queries[0][2])]
    assert walked == []  # the slow path was not needed


def test_cached_query_failure_falls_back_to_walking(monkeypatch):
    walked = []
    edit = element("Edit", "Date", Rect(0, 0, 10, 10))
    app, _ = make_cached_app(monkeypatch, [], walked_elements=[edit], walked_calls=walked)
    app.window.element_info.element.FindAllBuildCache = lambda *a: (_ for _ in ()).throw(
        OSError("COM error")
    )
    assert app.controls(kind="Edit") == [edit]
    assert walked == [{"control_type": "Edit"}]


def test_field_fetches_inputs_and_labels_with_one_query(monkeypatch):
    walked = []
    app, queries = make_cached_app(
        monkeypatch,
        [
            ("Date", Rect(100, 100, 140, 120), False, "Text"),
            ("", Rect(150, 100, 300, 120), False, "Edit"),
            ("", Rect(150, 200, 300, 220), False, "Edit"),
        ],
        walked_elements=[],
        walked_calls=walked,
    )
    found = app.field("Date", "Edit", Rect(0, 0, 500, 500))
    assert found.element.CachedBoundingRectangle.top == 100
    assert len(queries) == 1
    assert queries[0][1] == ("or", ("type", "Edit"), ("type", "Text"))


def test_fields_resolves_several_labels_from_one_query(monkeypatch):
    walked = []
    app, queries = make_cached_app(
        monkeypatch,
        [
            ("Date", Rect(100, 100, 140, 120), False, "Text"),
            ("", Rect(150, 100, 300, 120), False, "Edit"),
            ("Total", Rect(100, 200, 140, 220), False, "Text"),
            ("", Rect(150, 200, 300, 220), False, "Edit"),
        ],
        walked_elements=[],
        walked_calls=walked,
    )
    got, groups = app.fields([("Date", "Edit"), ("Total", "Edit")], Rect(0, 0, 500, 500))
    assert got[("Date", "Edit")].rectangle().top == 100
    assert got[("Total", "Edit")].rectangle().top == 200
    assert len(groups["Edit"]) == 2 and len(groups["Text"]) == 2
    assert len(queries) == 1
