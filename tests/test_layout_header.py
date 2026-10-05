"""layout.header: rows above the rows / tabs / sketch, outside any tab (offline).

The mirror of layout.footer. Before it, a banner above every tab could not be
written: a layout is exactly one of rows, tabs or sketch, and putting the banner in
`rows[0]` shifted every positional id (ROW-sdc-<i>, MARKDOWN-sdc-<i>-<j>) and every
positional markdown fix behind it. Header rows compile under their own id prefix,
ROW-sdc-header-<i>, after the body in emission order, so a header changes no body or
footer node at all.
"""

import copy
import json
from pathlib import Path

import pytest

from chartwright.compiler import _position, compile_bundle
from chartwright.dashdiff import _normalize
from chartwright.decompile import decompile_bundle
from chartwright.design import advise, advise_and_fix
from chartwright.design.presets import Overlay
from chartwright.spec import load_spec
from chartwright.testing import stub_resolution

from test_decompile import _stub_lookup_for
from test_layout_footer import FOOTER, _renamed

FIXTURES = Path(__file__).parent / "fixtures"
HEADER = [[{"markdown": "**Finance view** · figures are provisional until the 5th", "width": 12,
            "height": 1}]]
EMPTY = Overlay()


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _with_header(fixture: str, header=HEADER, footer=None) -> dict:
    data = _fixture(fixture)
    data["layout"]["header"] = header
    if footer:
        data["layout"]["footer"] = footer
    return data


def _round_trip(data: dict):
    spec = load_spec(data)
    return spec, decompile_bundle(compile_bundle(spec, stub_resolution(spec)), _stub_lookup_for(spec))


def test_header_sits_above_the_tabs_at_grid_level():
    pos = _position(load_spec(_with_header("kitchen_sink.json")))
    assert pos["GRID_ID"]["children"] == ["ROW-sdc-header-1", "TABS-sdc-1"]
    assert pos["ROW-sdc-header-1"]["parents"] == ["ROOT_ID", "GRID_ID"]
    md = pos["MARKDOWN-sdc-header-1-1"]
    assert md["parents"] == ["ROOT_ID", "GRID_ID", "ROW-sdc-header-1"]
    assert md["meta"]["code"] == HEADER[0][0]["markdown"]


def test_header_precedes_flat_rows_and_the_footer_follows():
    spec = load_spec(_with_header("sales_overview.json", footer=FOOTER))
    kids = _position(spec)["GRID_ID"]["children"]
    n = len(spec.layout.rows)
    assert kids == ["ROW-sdc-header-1", *[f"ROW-sdc-{i + 1}" for i in range(n)], "ROW-sdc-footer-1"]


@pytest.mark.parametrize("fixture", ["kitchen_sink.json", "sales_overview.json"])
def test_a_header_changes_no_body_or_footer_node(fixture):
    """Every node a spec compiles to without a header, chart placeholders included,
    is byte-for-byte the same with one; only the header's nodes and GRID's children
    differ."""
    plain = _fixture(fixture)
    plain["layout"]["footer"] = FOOTER
    headed = copy.deepcopy(plain)
    headed["layout"]["header"] = [[{"markdown": "Banner", "height": 1}],
                                  {"header": "Read me", "size": "small"}, {"divider": True}]
    before, after = _position(load_spec(plain)), _position(load_spec(headed))
    added = {k for k in after if k not in before}
    assert added == {"ROW-sdc-header-1", "MARKDOWN-sdc-header-1-1", "HEADER-sdc-header-2",
                     "DIVIDER-sdc-header-3"}
    for key, node in before.items():
        if key != "GRID_ID":
            assert after[key] == node, key
    assert after["GRID_ID"]["children"] == [
        "ROW-sdc-header-1", "HEADER-sdc-header-2", "DIVIDER-sdc-header-3",
        *before["GRID_ID"]["children"]]


def test_a_chart_in_the_header_leaves_body_chart_placeholders_alone():
    """Header rows are emitted after the body and footer, so a chart added to the
    header takes the next import placeholder id instead of shifting the body's."""
    plain = _fixture("sales_overview.json")
    plain["layout"]["footer"] = FOOTER
    headed = copy.deepcopy(plain)
    headed["charts"].append({"name": "Banner KPI", "type": "big_number_total",
                             "metric": "COUNT(*)", "dataset": plain["charts"][0]["dataset"]})
    headed["layout"]["header"] = [["Banner KPI"]]
    before, after = _position(load_spec(plain)), _position(load_spec(headed))
    for key, node in before.items():
        if key != "GRID_ID":
            assert after[key] == node, key
    [kpi] = after["ROW-sdc-header-1"]["children"]
    placeholders = [n["meta"]["chartId"] for n in before.values()
                    if isinstance(n, dict) and n.get("type") == "CHART"]
    assert after[kpi]["meta"]["chartId"] == max(placeholders) + 1


@pytest.mark.parametrize("fixture", ["kitchen_sink.json", "sales_overview.json"])
def test_header_round_trips_losslessly(fixture):
    spec, result = _round_trip(_with_header(fixture, footer=FOOTER))
    assert result.losses == [], [loss.as_dict() for loss in result.losses]
    assert result.spec["layout"]["header"] == HEADER
    assert result.spec["layout"]["footer"] == FOOTER
    assert list(result.spec["layout"])[0] == "header"
    assert _normalize(load_spec(result.spec)) == _normalize(spec)


def test_header_recompiles_to_the_same_bundle():
    spec, result = _round_trip(_with_header("kitchen_sink.json", footer=FOOTER))
    again = load_spec(result.spec)
    assert compile_bundle(again, stub_resolution(again)) == compile_bundle(spec, stub_resolution(spec))


def test_any_rows_before_the_tabs_read_as_the_header():
    """A dashboard assembled in Superset's UI (no chartwright ids): rows dragged
    above the tabs are a header, not a reason to flatten the tabs."""
    spec = load_spec(_with_header("kitchen_sink.json"))
    bundle = _renamed(compile_bundle(spec, stub_resolution(spec)), "sdc-header-", "ui-built-")
    result = decompile_bundle(bundle, _stub_lookup_for(spec))
    assert result.spec["layout"]["header"] == HEADER
    assert "tabs" in result.spec["layout"]
    assert not [loss for loss in result.losses if "flattened" in loss.what]


def test_untabbed_rows_without_the_marker_stay_in_the_body():
    spec = load_spec(_with_header("sales_overview.json"))
    bundle = _renamed(compile_bundle(spec, stub_resolution(spec)), "sdc-header-", "ui-built-")
    layout = decompile_bundle(bundle, _stub_lookup_for(spec)).spec["layout"]
    assert "header" not in layout
    assert layout["rows"][0] == HEADER[0]


def test_header_with_a_sketch_layout_round_trips_and_keeps_the_sketch_ids():
    data = _fixture("sales_overview.json")
    names = [c["name"] for c in data["charts"]]
    data["layout"] = {"sketch": ["AAAABBBBCCCC", "DDDDEEEEFFFF"],
                      "legend": dict(zip("ABCDEF", names)), "footer": FOOTER}
    plain = _position(load_spec(data))
    data["layout"]["header"] = HEADER
    spec, result = _round_trip(data)
    assert result.spec["layout"]["header"] == HEADER
    assert _normalize(load_spec(result.spec)) == _normalize(spec)
    headed = _position(spec)
    assert all(headed[k] == v for k, v in plain.items() if k != "GRID_ID")


def test_a_header_chart_counts_as_placed_and_only_once():
    data = _fixture("sales_overview.json")
    first = data["layout"]["rows"].pop(0)
    data["layout"]["header"] = [first]
    spec = load_spec(data)
    assert _position(spec)["GRID_ID"]["children"][0] == "ROW-sdc-header-1"
    data["layout"]["rows"].append([first[0]])
    with pytest.raises(ValueError, match="more than once"):
        load_spec(data)


def test_header_rows_obey_the_grid_width():
    wide = [[{"markdown": "a", "width": 8}, {"markdown": "b", "width": 8}]]
    with pytest.raises(ValueError, match="widths sum to 16"):
        load_spec(_with_header("sales_overview.json", wide))


def test_a_header_alone_is_not_a_layout():
    data = _fixture("sales_overview.json")
    data["layout"] = {"header": HEADER}
    with pytest.raises(ValueError, match="exactly one of rows / tabs / sketch"):
        load_spec(data)


# -- the design brain ---------------------------------------------------------------

DS = {"database": "db", "table": "orders"}


def line(name, **kw):
    return {"type": "timeseries_line", "name": name, "dataset": DS,
            "metrics": ["COUNT(*)"], "time_column": "ts", "height": 8, **kw}


def mk(charts, layout) -> dict:
    return {"spec_version": "1", "dashboard": {"title": "T", "slug": "t"},
            "charts": charts, "layout": layout}


def test_a_header_chart_is_advised_where_it_is():
    data = mk([line("L"), line("H")], {"header": [["H"]], "rows": [["L"]]})
    rep = advise(load_spec(data), overlay=EMPTY)
    assert {f.where for f in rep.findings if f.chart == "H"} <= {"header row 0"}
    # A header is not a tab: it never makes a flat dashboard look tabbed.
    assert "layout.tab-balance" not in {f.rule for f in rep.findings}


def test_the_fold_budget_spends_the_header_in_every_tab():
    tabs = [{"title": "A", "rows": [["A1"], ["A2"]]}, {"title": "B", "rows": [["B1"]]}]
    charts = [line("A1"), line("A2"), line("B1")]
    exec_ = {"audience": "executive"}  # 22 units per tab
    clean = mk(charts, {"tabs": tabs})
    clean["design"] = exec_
    assert "layout.fold-budget" not in {f.rule for f in advise(load_spec(clean), overlay=EMPTY).findings}
    headed = mk(charts, {"tabs": tabs, "header": [[{"markdown": "Banner", "height": 8}]]})
    headed["design"] = exec_
    found = [f for f in advise(load_spec(headed), overlay=EMPTY).findings
             if f.rule == "layout.fold-budget"]
    assert [f.where for f in found] == ["tab 'A' row 1"]
    assert "with the header's 8" in found[0].detail


def test_a_header_that_overflows_alone_is_reported_once():
    charts = [line("A1"), line("B1")]
    tabs = [{"title": "A", "rows": [["A1"]]}, {"title": "B", "rows": [["B1"]]}]
    data = mk(charts, {"tabs": tabs, "header": [[{"markdown": "x", "height": 20}],
                                                [{"markdown": "y", "height": 6}]]})
    data["design"] = {"audience": "executive"}
    found = [f for f in advise(load_spec(data), overlay=EMPTY).findings
             if f.rule == "layout.fold-budget"]
    assert [f.where for f in found] == ["header row 1"]


def test_a_header_markdown_fix_is_addressed_to_the_header():
    md = {"markdown": "## Section", "height": 6}
    data = mk([line("L")], {"header": [[dict(md)]], "rows": [[dict(md)], ["L"]]})
    fixed, rep = advise_and_fix(data, overlay=EMPTY)
    addresses = sorted(str(e["md"][0]) for e in rep.fixed if e["rule"] == "layout.markdown-height")
    assert addresses == ["None", "header"]
    assert fixed["layout"]["header"][0][0]["height"] == 2
    assert fixed["layout"]["rows"][0][0]["height"] == 2
    load_spec(fixed)


def test_a_spec_without_a_header_builds_the_same_bundle_as_one_with_none():
    data = _fixture("kitchen_sink.json")
    spec = load_spec(data)
    data["layout"]["header"] = None
    same = load_spec(data)
    assert compile_bundle(spec, stub_resolution(spec)) == compile_bundle(same, stub_resolution(same))
