"""Sub-tabs (a TABS node inside a TAB): compile shape, decompile round trip, validators,
and layout-compare equality."""

import io
import zipfile

import pytest
import yaml
from pydantic import ValidationError

from chartwright.compiler import compile_bundle
from chartwright.dashdiff import _normalize
from chartwright.decompile import decompile_bundle
from chartwright.spec import load_spec
from chartwright.testing import stub_resolution

DS = {"database": "examples", "table": "t"}
CHARTS = [
    {"name": n, "type": "big_number_total", "dataset": DS, "metric": "COUNT(*)"}
    for n in ("Summary", "North", "South", "West")
]
LAYOUT = {"tabs": [
    {"title": "Summary", "rows": [["Summary"]]},
    {"title": "Regions", "tabs": [
        {"title": "North", "rows": [["North"]]},
        {"title": "South", "rows": [["South"]]},
        {"title": "West", "rows": [["West"]]},
    ]},
]}


def _spec(layout=LAYOUT, charts=CHARTS):
    return load_spec({
        "spec_version": "1",
        "dashboard": {"title": "T", "slug": "sdc-t"},
        "charts": charts,
        "layout": layout,
    })


def _position(spec):
    zf = zipfile.ZipFile(io.BytesIO(compile_bundle(spec, stub_resolution(spec))))
    dash = next(n for n in zf.namelist() if "/dashboards/" in n)
    return yaml.safe_load(zf.read(dash))["position"]


def test_compile_nests_a_tabs_node_inside_the_tab():
    pos = _position(_spec())
    assert pos["TABS-sdc-1"]["children"] == ["TAB-sdc-1", "TAB-sdc-2"]
    mech = pos["TAB-sdc-2"]
    assert mech["meta"]["text"] == "Regions"
    assert mech["children"] == ["TABS-sdc-t2"]
    sub = pos["TABS-sdc-t2"]
    assert sub["type"] == "TABS" and sub["parents"] == ["ROOT_ID", "GRID_ID", "TABS-sdc-1", "TAB-sdc-2"]
    assert sub["children"] == ["TAB-sdc-2-1", "TAB-sdc-2-2", "TAB-sdc-2-3"]
    north = pos["TAB-sdc-2-1"]
    assert north["meta"]["text"] == "North"
    assert north["parents"] == ["ROOT_ID", "GRID_ID", "TABS-sdc-1", "TAB-sdc-2", "TABS-sdc-t2"]
    (row_id,) = north["children"]
    (chart_id,) = pos[row_id]["children"]
    assert pos[chart_id]["meta"]["sliceName"] == "North"
    assert pos[chart_id]["parents"][-2:] == ["TAB-sdc-2-1", row_id]


def test_decompile_round_trips_sub_tabs():
    spec = _spec()
    bundle = compile_bundle(spec, stub_resolution(spec))
    ds = stub_resolution(spec).for_chart(spec.charts[0].dataset)
    result = decompile_bundle(bundle, lambda u: {"database": "examples", "schema": None, "table": "t"}
                              if u == ds.uuid else None)
    assert result.losses == [], result.losses_json()
    tabs = result.spec["layout"]["tabs"]
    assert [t["title"] for t in tabs] == ["Summary", "Regions"]
    assert [s["title"] for s in tabs[1]["tabs"]] == ["North", "South", "West"]
    assert [row for s in tabs[1]["tabs"] for row in s["rows"]] == [["North"], ["South"], ["West"]]


def test_sub_tab_charts_count_as_placed():
    spec = _spec()
    placed = {x for row in spec.layout.all_rows() for x in row if isinstance(x, str)}
    assert placed == {"Summary", "North", "South", "West"}
    assert [t.title for t in spec.layout.leaf_tabs()] == ["Summary", "North", "South", "West"]


def test_validators():
    with pytest.raises(ValidationError):  # rows AND tabs
        _spec({"tabs": [{"title": "M", "rows": [["Summary"]], "tabs": [{"title": "L", "rows": [["North"]]}]}]})
    with pytest.raises(ValidationError):  # nested two levels
        _spec({"tabs": [{"title": "M", "tabs": [{"title": "L", "tabs": [{"title": "X", "rows": [["North"]]}]}]}]})
    with pytest.raises(ValidationError):  # duplicate sub-tab title
        _spec({"tabs": [{"title": "M", "tabs": [{"title": "L", "rows": [["North"]]},
                                                {"title": "L", "rows": [["South"]]}]}]})


def test_layout_compare_sub_tabs_equal_to_themselves():
    spec = _spec()
    bundle = compile_bundle(spec, stub_resolution(spec))
    ds = stub_resolution(spec).for_chart(spec.charts[0].dataset)
    live = load_spec(decompile_bundle(bundle, lambda u: {"database": "examples", "schema": None, "table": "t"}
                                      if u == ds.uuid else None).spec)
    assert _normalize(spec)["layout"] == _normalize(live)["layout"]
