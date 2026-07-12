"""chartwright absorb: UI height polish patches back into the spec: heights only,
exactly (fractional 40px units in 0.2 steps = Superset's 8px grid)."""

import io
import json
import zipfile

import yaml

from chartwright import ids
from chartwright.absorb import absorb_heights
from chartwright.compiler import _position, compile_bundle
from chartwright.spec import load_spec
from chartwright.testing import stub_resolution

DS = {"database": "examples", "table": "t"}


def _spec_data():
    return {
        "spec_version": "1",
        "dashboard": {"title": "T", "slug": "sdc-t"},
        "charts": [
            {"name": "a", "type": "big_number_total", "dataset": DS, "metric": "COUNT(*)", "height": 7},
            {"name": "b", "type": "big_number_total", "dataset": DS, "metric": "COUNT(*)", "height": 8},
        ],
        "layout": {"rows": [["a", "b"]]},
    }


def _live_position_with(spec, **height_overrides):
    """Live position = compiled position with some chart heights 'UI-polished'."""
    pos = json.loads(json.dumps(_position(spec)))
    for v in pos.values():
        if isinstance(v, dict) and v.get("type") == "CHART":
            name = v["meta"]["sliceName"]
            if name in height_overrides:
                v["meta"]["height"] = height_overrides[name]
    return pos


def test_absorb_writes_exact_fractional_heights():
    data = _spec_data()
    spec = load_spec(data)
    live = _live_position_with(spec, a=37)  # UI-dragged: 37 row units = 296px (not a 40px multiple)
    new_data, report = absorb_heights(spec, data, live)
    assert report.ok and len(report.absorbed) == 1
    entry = report.absorbed[0]
    assert entry == {"chart": "a", "from_px": 280, "to_px": 296, "height": 7.4}
    assert new_data["charts"][0]["height"] == 7.4
    # untouched chart keeps its int; original input not mutated
    assert new_data["charts"][1]["height"] == 8
    assert data["charts"][0]["height"] == 7

    # THE invariant: re-compiling the absorbed spec reproduces the live height exactly
    respec = load_spec(new_data)
    pos = _position(respec)
    a_meta = next(v["meta"] for v in pos.values()
                  if isinstance(v, dict) and v.get("type") == "CHART" and v["meta"]["sliceName"] == "a")
    assert a_meta["height"] == 37


def test_absorb_noop_when_live_matches():
    data = _spec_data()
    spec = load_spec(data)
    _, report = absorb_heights(spec, data, _live_position_with(spec))
    assert report.absorbed == [] and report.width_advice == []


def test_width_drift_is_advice_not_written():
    data = _spec_data()
    spec = load_spec(data)
    live = json.loads(json.dumps(_position(spec)))
    for v in live.values():
        if isinstance(v, dict) and v.get("type") == "CHART" and v["meta"]["sliceName"] == "b":
            v["meta"]["width"] = 4
    new_data, report = absorb_heights(spec, data, live)
    assert report.absorbed == []
    assert len(report.width_advice) == 1 and report.width_advice[0]["chart"] == "b"
    assert new_data == data  # nothing written for width-only drift


def test_unmatched_live_charts_reported():
    data = _spec_data()
    spec = load_spec(data)
    live = _live_position_with(spec)
    live["CHART-foreign"] = {"type": "CHART", "id": "CHART-foreign",
                             "meta": {"uuid": "not-ours", "sliceName": "someone else's", "width": 4, "height": 10}}
    _, report = absorb_heights(spec, data, live)
    assert report.unmatched_live == ["someone else's"]


def test_fractional_height_compiles_to_integer_row_units():
    data = _spec_data()
    data["charts"][0]["height"] = 7.4
    spec = load_spec(data)
    zf = zipfile.ZipFile(io.BytesIO(compile_bundle(spec, stub_resolution(spec))))
    dash = next(n for n in zf.namelist() if "/dashboards/" in n)
    pos = yaml.safe_load(zf.read(dash))["position"]
    a = next(v["meta"] for v in pos.values()
             if isinstance(v, dict) and v.get("type") == "CHART" and v["meta"]["sliceName"] == "a")
    assert a["height"] == 37 and isinstance(a["height"], int)


def test_sketch_mode_absorb():
    data = {
        "spec_version": "1",
        "dashboard": {"title": "T", "slug": "sdc-t"},
        "charts": [
            {"name": "a", "type": "big_number_total", "dataset": DS, "metric": "COUNT(*)"},
            {"name": "b", "type": "big_number_total", "dataset": DS, "metric": "COUNT(*)"},
        ],
        "layout": {"sketch": ["AB"], "legend": {"A": "a", "B": "b"}},
    }
    spec = load_spec(data)
    live = _live_position_with(spec, b=13)  # 104px
    new_data, report = absorb_heights(spec, data, live)
    assert report.absorbed[0]["height"] == 2.6
    assert new_data["charts"][1]["height"] == 2.6
    # explicit height override wins over the sketch line on re-compile
    respec = load_spec(new_data)
    b_meta = next(v["meta"] for v in _position(respec).values()
                  if isinstance(v, dict) and v.get("type") == "CHART" and v["meta"]["sliceName"] == "b")
    assert b_meta["height"] == 13


def test_chart_uuid_matching_is_by_slug_and_name():
    # sanity: the uuid absorb matches on is the same deterministic identity apply uses
    assert str(ids.chart_uuid("sdc-t", "a")) != str(ids.chart_uuid("sdc-t", "b"))
