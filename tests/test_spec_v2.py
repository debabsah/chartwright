"""Validation tests for the expanded surface: filters, new types, markdown, tabs."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from chartwright.spec import CHART_TYPES, load_spec

SINK = json.loads((Path(__file__).parent / "fixtures" / "kitchen_sink.json").read_text())


def _copy():
    return json.loads(json.dumps(SINK))


def test_kitchen_sink_valid():
    spec = load_spec(SINK)
    assert len(spec.charts) == 15
    assert {c.type for c in spec.charts} == set(CHART_TYPES)  # every type covered
    assert len(spec.filters) == 3
    assert spec.layout.tabs and len(spec.layout.tabs) == 2


def test_filter_value_shapes():
    d = _copy()
    d["charts"][2]["filters"] = [{"column": "deal_size", "op": "IN", "value": "Large"}]
    with pytest.raises(ValidationError, match="non-empty list"):
        load_spec(d)
    d["charts"][2]["filters"] = [{"column": "deal_size", "op": "IS NULL", "value": "x"}]
    with pytest.raises(ValidationError, match="takes no value"):
        load_spec(d)
    d["charts"][2]["filters"] = [{"column": "deal_size", "op": "=="}]
    with pytest.raises(ValidationError, match="needs a value"):
        load_spec(d)


def test_pivot_needs_dims():
    d = _copy()
    for c in d["charts"]:
        if c["type"] == "pivot_table":
            c["rows"] = []
            c["columns"] = []
    with pytest.raises(ValidationError, match="rows/columns"):
        load_spec(d)


def test_layout_rows_xor_tabs():
    d = _copy()
    d["layout"]["rows"] = [["Total Orders"]]
    with pytest.raises(ValidationError, match="exactly one"):
        load_spec(d)


def test_duplicate_filter_names():
    d = _copy()
    d["filters"][1]["name"] = "Country"
    with pytest.raises(ValidationError, match="duplicate filter name"):
        load_spec(d)


def test_markdown_takes_width_share():
    spec = load_spec(SINK)
    md = spec.layout.tabs[0].rows[0][0]
    assert spec.resolved_item_width(md) == 12


def test_charts_in_tabs_must_all_be_placed():
    d = _copy()
    d["layout"]["tabs"][1]["rows"] = d["layout"]["tabs"][1]["rows"][1:]
    with pytest.raises(ValidationError, match="not placed"):
        load_spec(d)
