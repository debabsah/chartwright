"""Headers, dividers and row backgrounds in the layout. Superset nests HEADER
and DIVIDER in a grid, tab or column, never in a row, so each is an entry of
its own in `rows` / `footer`; a row with a background is
{"row": [...], "background": "white"}. All compile into position_json,
decompile back, and show as layout changes in `plan`."""

import pytest
from pydantic import ValidationError

from chartwright.design import advise, advise_and_fix
from chartwright.design.presets import load_overlay
from chartwright.spec import load_spec

from test_dashboard_settings import (
    DS, assert_lossless, dashboard_doc, edit_dashboard, plan_against, roundtrip, spec_data,
)

CHARTS = [
    {"name": "Revenue", "type": "big_number_total", "metric": "SUM(amount)", "dataset": DS},
    {"name": "Orders", "type": "big_number_total", "metric": "COUNT(*)", "dataset": DS},
    {"name": "By Region", "type": "bar", "x_column": "region", "metrics": ["SUM(amount)"], "dataset": DS},
]


def with_rows(rows, footer=None):
    data = spec_data(charts=[dict(c) for c in CHARTS])
    data["layout"] = {"rows": rows, **({"footer": footer} if footer else {})}
    return data


ROWS = [
    {"header": "Headline numbers", "size": "large", "background": "white"},
    {"row": ["Revenue", "Orders"], "background": "white"},
    {"divider": True},
    {"header": "Detail"},
    ["By Region"],
]


def position(data) -> dict:
    return dashboard_doc(load_spec(data))["position"]


def test_headers_dividers_and_row_backgrounds_compile():
    pos = position(with_rows(ROWS))
    assert pos["GRID_ID"]["children"] == [
        "HEADER-sdc-1", "ROW-sdc-2", "DIVIDER-sdc-3", "HEADER-sdc-4", "ROW-sdc-5"]
    assert pos["HEADER-sdc-1"] == {
        "type": "HEADER", "id": "HEADER-sdc-1", "children": [], "parents": ["ROOT_ID", "GRID_ID"],
        "meta": {"text": "Headline numbers", "headerSize": "LARGE_HEADER", "background": "BACKGROUND_WHITE"}}
    assert pos["HEADER-sdc-4"]["meta"] == {
        "text": "Detail", "headerSize": "MEDIUM_HEADER", "background": "BACKGROUND_TRANSPARENT"}
    assert pos["DIVIDER-sdc-3"] == {
        "type": "DIVIDER", "id": "DIVIDER-sdc-3", "children": [], "parents": ["ROOT_ID", "GRID_ID"], "meta": {}}
    assert pos["ROW-sdc-2"]["meta"] == {"background": "BACKGROUND_WHITE"}
    assert pos["ROW-sdc-5"]["meta"] == {"background": "BACKGROUND_TRANSPARENT"}
    assert len(pos["ROW-sdc-2"]["children"]) == 2


def test_row_ids_stay_put_without_headers():
    pos = position(with_rows([["Revenue", "Orders"], ["By Region"]]))
    assert pos["GRID_ID"]["children"] == ["ROW-sdc-1", "ROW-sdc-2"]


def test_headers_in_tabs_sub_tabs_and_footer():
    data = spec_data(charts=[dict(c) for c in CHARTS])
    data["layout"] = {
        "tabs": [
            {"title": "One", "rows": [{"header": "KPIs"}, ["Revenue", "Orders"]]},
            {"title": "Two", "tabs": [{"title": "A", "rows": [{"divider": True}, ["By Region"]]}]},
        ],
        "footer": [{"divider": True}, [{"markdown": "Source: ERP"}]],
    }
    pos = position(data)
    assert pos["TAB-sdc-1"]["children"] == ["HEADER-sdc-t1-1", "ROW-sdc-t1-2"]
    assert pos["HEADER-sdc-t1-1"]["parents"] == ["ROOT_ID", "GRID_ID", "TABS-sdc-1", "TAB-sdc-1"]
    assert pos["TAB-sdc-2-1"]["children"] == ["DIVIDER-sdc-t2-1-1", "ROW-sdc-t2-1-2"]
    assert pos["GRID_ID"]["children"][-2:] == ["DIVIDER-sdc-footer-1", "ROW-sdc-footer-2"]
    assert_lossless(load_spec(data))


@pytest.mark.parametrize("entry, needle", [
    ({"header": ""}, "at least 1 character"),
    ({"header": "x", "size": "huge"}, "small"),
    ({"header": "x", "background": "blue"}, "transparent"),
    ({"divider": False}, "True"),
    ({"row": ["By Region"], "background": "transparent"}, "white"),
    ({"row": [], "background": "white"}, "at least 1 item"),
    ({"row": ["By Region"]}, "background"),
])
def test_bad_layout_entries_are_named(entry, needle):
    with pytest.raises(ValidationError) as e:
        load_spec(with_rows([["Revenue", "Orders"], entry, ["By Region"]]))
    assert needle in str(e.value)


def test_charts_in_a_styled_row_are_placed_and_sized():
    spec = load_spec(with_rows([{"row": ["Revenue", "Orders", "By Region"], "background": "white"}]))
    assert [spec.resolved_item_width(n) for n in ("Revenue", "Orders", "By Region")] == [4, 4, 4]
    with pytest.raises(ValidationError, match="not placed"):
        load_spec(with_rows([{"header": "only a header"}, ["Revenue", "Orders"]]))


def test_headers_and_backgrounds_round_trip():
    out = assert_lossless(load_spec(with_rows(ROWS)))
    assert out.spec["layout"]["rows"] == [
        {"header": "Headline numbers", "size": "large", "background": "white"},
        {"row": ["Revenue", "Orders"], "background": "white"},
        {"divider": True},
        {"header": "Detail"},
        ["By Region"],
    ]


def test_an_untabbed_footer_with_a_divider_round_trips():
    assert_lossless(load_spec(with_rows([["Revenue", "Orders", "By Region"]],
                                        footer=[{"divider": True}, [{"markdown": "Note"}]])))


def test_a_ui_header_with_no_size_reads_as_small():
    """Superset draws a header with no stored headerSize as small (Header.tsx)."""
    def fn(doc):
        doc["position"]["HEADER-sdc-4"]["meta"] = {"text": "Detail"}
    out = roundtrip(load_spec(with_rows(ROWS)), edit_dashboard(fn))
    assert out.spec["layout"]["rows"][3] == {"header": "Detail", "size": "small"}


def test_a_header_inside_a_column_is_a_named_loss():
    def fn(doc):
        pos = doc["position"]
        pos["COLUMN-x"] = {"type": "COLUMN", "id": "COLUMN-x", "children": ["HEADER-x"], "meta": {}}
        pos["HEADER-x"] = {"type": "HEADER", "id": "HEADER-x", "children": [], "meta": {"text": "Nested"}}
        pos["ROW-sdc-5"]["children"].append("COLUMN-x")
    out = roundtrip(load_spec(with_rows(ROWS)), edit_dashboard(fn))
    assert any("HEADER element dropped" in loss.what for loss in out.losses)


@pytest.mark.parametrize("change", [
    lambda pos: pos["HEADER-sdc-1"]["meta"].update(text="Renamed"),
    lambda pos: pos["HEADER-sdc-4"]["meta"].update(headerSize="LARGE_HEADER"),
    lambda pos: pos["ROW-sdc-2"]["meta"].update(background="BACKGROUND_TRANSPARENT"),
    lambda pos: pos["GRID_ID"]["children"].remove("DIVIDER-sdc-3"),
])
def test_plan_reports_a_layout_change(change, monkeypatch):
    spec = load_spec(with_rows(ROWS))
    assert plan_against(spec, None, monkeypatch)["clean"] is True
    out = plan_against(spec, edit_dashboard(lambda doc: change(doc["position"])), monkeypatch)
    assert out["layout_changed"] is True and out["charts_changed"] == []


# -- the design brain reads around headers ---------------------------------------------


def test_header_rows_count_as_section_signposts():
    charts = [{"name": f"C{i}", "type": "bar", "x_column": "r", "metrics": ["SUM(a)"], "dataset": DS}
              for i in range(9)]
    rows = [[f"C{i}", f"C{i + 1}", f"C{i + 2}"] for i in range(0, 9, 3)]
    data = spec_data(charts=charts)
    data["layout"] = {"rows": rows}
    rule = "layout.section-headers"
    assert any(f.rule == rule for f in advise(load_spec(data), overlay=load_overlay(None)).findings)
    data["layout"] = {"rows": [{"header": "Regions"}, *rows]}
    assert not any(f.rule == rule for f in advise(load_spec(data), overlay=load_overlay(None)).findings)


def test_the_markdown_height_fix_addresses_the_right_block_past_headers():
    data = with_rows([{"header": "Top"}, {"row": [{"markdown": "One line", "height": 4}, "Revenue"],
                                          "background": "white"}, ["Orders", "By Region"]])
    fixed, report = advise_and_fix(data, overlay=load_overlay(None))
    assert fixed["layout"]["rows"][1]["row"][0]["height"] == 1.6
    assert fixed["layout"]["rows"][0] == {"header": "Top"}
