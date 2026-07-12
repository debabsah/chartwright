"""Regression tests for plan normalization/comparison (found by independent
review: tabs layouts crashed the layout comparison)."""

import json
from pathlib import Path

from chartwright.dashdiff import _normalize
from chartwright.spec import load_spec

FIXTURES = Path(__file__).parent / "fixtures"


def _sink():
    return load_spec(json.loads((FIXTURES / "kitchen_sink.json").read_text()))


def test_normalize_tabs_layout_has_no_rows_key():
    t = _normalize(_sink())
    assert "rows" not in t["layout"] and t["layout"]["tabs"]


def test_layout_compare_tabs_vs_tabs_no_keyerror():
    a, b = _normalize(_sink()), _normalize(_sink())
    assert (a["layout"] != b["layout"]) is False  # the exact expression plan() uses


def test_layout_compare_detects_tab_change():
    spec_a = _sink()
    data = json.loads((FIXTURES / "kitchen_sink.json").read_text())
    data["layout"]["tabs"][0]["title"] = "Renamed Tab"
    spec_b = load_spec(data)
    assert _normalize(spec_a)["layout"] != _normalize(spec_b)["layout"]


def test_layout_compare_rows_vs_tabs_differs():
    rows_spec = load_spec(json.loads((FIXTURES / "sales_overview.json").read_text()))
    assert _normalize(rows_spec)["layout"] != _normalize(_sink())["layout"]


def _spec_pair_sketch_and_rows():
    """The same two-chart dashboard drawn as a sketch and written as rows."""
    charts = [
        {"name": "KPI", "type": "big_number_total",
         "dataset": {"database": "examples", "table": "t"}, "metric": "COUNT(*)"},
        {"name": "Trend", "type": "timeseries_line",
         "dataset": {"database": "examples", "table": "t"},
         "metrics": ["COUNT(*)"], "time_column": "ts"},
    ]
    base = {"spec_version": "1", "dashboard": {"title": "T", "slug": "sdc-t"}, "charts": charts}
    sketch = load_spec({**base, "layout": {
        "sketch": ["KKKK TTTTTTTT", "KKKK TTTTTTTT"],
        "legend": {"K": "KPI", "T": "Trend"},
        "line": 2,
    }})
    rows = load_spec({**base, "layout": {"rows": [["KPI", "Trend"]]}})
    return sketch, rows


def test_normalize_sketch_layout_no_crash_and_rows_form():
    """plan() crashed with KeyError on any sketch spec (found by the NYC demo,
    2026-07-12): resolved_item_width only walked rows layouts."""
    sketch, _ = _spec_pair_sketch_and_rows()
    n = _normalize(sketch)
    assert n["layout"] == {"rows": [["KPI", "Trend"]]}
    kpi = next(c for c in n["charts"] if c["name"] == "KPI")
    assert kpi["width"] == 4        # 4 of 13 cells -> largest-remainder 4/12
    assert kpi["height"] == 4       # 2 lines x line=2


def test_sketch_and_rows_layouts_compare_equal():
    sketch, rows = _spec_pair_sketch_and_rows()
    a, b = _normalize(sketch), _normalize(rows)
    assert a["layout"] == b["layout"]  # the exact expression plan() uses
