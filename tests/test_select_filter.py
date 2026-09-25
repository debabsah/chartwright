"""Native select filter (filter_select): a default value (both halves of the data mask,
as the range and time defaults) and name-based chart scope, round-tripped."""

import pytest
from pydantic import ValidationError

from chartwright.apply import scoped_filter_fixup
from chartwright.compiler import compile_bundle
from chartwright.decompile import decompile_bundle
from chartwright.testing import stub_resolution

from test_range_filter import DS, _native_filter_config, _spec


def test_select_default_fills_both_halves_and_scope_marker():
    (nf,) = _native_filter_config(_spec([
        {"type": "select", "name": "Year", "dataset": DS, "column": "year_label",
         "multi": False, "default": ["This year (2026)"], "charts": ["A", "B"]},
    ]))
    assert nf["filterType"] == "filter_select"
    assert nf["controlValues"]["multiSelect"] is False
    assert nf["defaultDataMask"] == {
        "extraFormData": {"filters": [{"col": "year_label", "op": "IN", "val": ["This year (2026)"]}]},
        "filterState": {"value": ["This year (2026)"], "label": "This year (2026)"},
        "ownState": {},
    }
    assert nf["sdc_scope_charts"] == ["A", "B"]


def test_select_without_default_keeps_empty_mask():
    (nf,) = _native_filter_config(_spec([
        {"type": "select", "name": "Class", "dataset": DS, "column": "event_class"},
    ]))
    assert nf["defaultDataMask"] == {"extraFormData": {}, "filterState": {}, "ownState": {}}
    assert "sdc_scope_charts" not in nf


def test_select_validators():
    with pytest.raises(ValidationError):
        _spec([{"type": "select", "name": "Y", "dataset": DS, "column": "c", "default": []}])
    with pytest.raises(ValidationError):
        _spec([{"type": "select", "name": "Y", "dataset": DS, "column": "c", "multi": False,
                "default": ["a", "b"]}])
    with pytest.raises(ValidationError):
        _spec([{"type": "select", "name": "Y", "dataset": DS, "column": "c", "charts": []}])


def test_select_scope_fixup():
    spec = _spec([{"type": "select", "name": "Year", "dataset": DS, "column": "y", "charts": ["A"]}])
    nf = _native_filter_config(spec)
    meta, errors = scoped_filter_fixup({"native_filter_configuration": nf}, spec, {"A": 1, "B": 2, "C": 3})
    assert errors == []
    (f,) = meta["native_filter_configuration"]
    assert f["scope"] == {"rootPath": ["ROOT_ID"], "excluded": [2, 3]}
    assert f["chartsInScope"] == [1]


def test_decompile_select_default_and_scope():
    spec = _spec([
        {"type": "select", "name": "Year", "dataset": DS, "column": "year_label",
         "multi": False, "default": ["This year (2026)"], "charts": ["A"]},
    ])
    bundle = compile_bundle(spec, stub_resolution(spec))
    ds = stub_resolution(spec).for_chart(spec.charts[0].dataset)
    lookup = {ds.uuid: {"database": "examples", "schema": None, "table": "t"}}
    result = decompile_bundle(bundle, lambda u: lookup.get(u))
    assert result.losses == [], result.losses_json()
    (f,) = result.spec["filters"]
    assert f == {"type": "select", "name": "Year",
                 "dataset": {"database": "examples", "schema": None, "table": "t"},
                 "column": "year_label", "multi": False,
                 "default": ["This year (2026)"], "charts": ["A"]}
