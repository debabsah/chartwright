"""Native range filter (filter_range): emission shapes mirror Superset 4.1's
RangeFilterPlugin contract (SingleValueType is a bare TS enum -> NUMERIC values;
a default hydrates only when defaultDataMask carries BOTH extraFormData and
filterState), plus the post-import chart-scope fixup."""

import io
import json
import zipfile

import pytest
import yaml
from pydantic import ValidationError

from chartwright.apply import scoped_filter_fixup
from chartwright.compiler import compile_bundle
from chartwright.spec import load_spec
from chartwright.testing import stub_resolution

DS = {"database": "examples", "table": "t"}


def _spec(filters, charts=None):
    charts = charts or [
        {"name": "A", "type": "big_number_total", "dataset": DS, "metric": "COUNT(*)"},
        {"name": "B", "type": "big_number_total", "dataset": DS, "metric": "COUNT(*)"},
        {"name": "C", "type": "big_number_total", "dataset": DS, "metric": "COUNT(*)"},
    ]
    return load_spec({
        "spec_version": "1",
        "dashboard": {"title": "T", "slug": "sdc-t"},
        "charts": charts,
        "filters": filters,
        "layout": {"rows": [[c["name"] for c in charts]]},
    })


def _native_filter_config(spec):
    zf = zipfile.ZipFile(io.BytesIO(compile_bundle(spec, stub_resolution(spec))))
    dash = next(n for n in zf.namelist() if "/dashboards/" in n)
    meta = yaml.safe_load(zf.read(dash))["metadata"]
    return json.loads(json.dumps(meta["native_filter_configuration"]))


def test_le_only_single_max_mode():
    (nf,) = _native_filter_config(_spec([
        {"type": "range", "name": "Weeks", "dataset": DS, "column": "week_rank", "le": 8},
    ]))
    assert nf["filterType"] == "filter_range"
    assert nf["targets"][0]["column"] == {"name": "week_rank"}
    assert "datasetUuid" in nf["targets"][0]
    # numeric enum: Maximum = 2 (a string here never hydrates)
    assert nf["controlValues"] == {"enableEmptyFilter": False, "enableSingleValue": 2}
    assert nf["defaultDataMask"] == {
        "extraFormData": {"filters": [{"col": "week_rank", "op": "<=", "val": 8}]},
        "filterState": {"value": [None, 8], "label": "x ≤ 8"},
        "ownState": {},
    }


def test_ge_only_single_min_mode():
    (nf,) = _native_filter_config(_spec([
        {"type": "range", "name": "Floor", "dataset": DS, "column": "n", "ge": 2.5},
    ]))
    assert nf["controlValues"]["enableSingleValue"] == 0
    assert nf["defaultDataMask"]["extraFormData"] == {"filters": [{"col": "n", "op": ">=", "val": 2.5}]}
    assert nf["defaultDataMask"]["filterState"] == {"value": [2.5, None], "label": "x ≥ 2.5"}


def test_both_bounds_range_mode():
    (nf,) = _native_filter_config(_spec([
        {"type": "range", "name": "Band", "dataset": DS, "column": "n", "ge": 1, "le": 9},
    ]))
    assert "enableSingleValue" not in nf["controlValues"]
    assert nf["defaultDataMask"]["extraFormData"] == {"filters": [
        {"col": "n", "op": ">=", "val": 1}, {"col": "n", "op": "<=", "val": 9},
    ]}
    assert nf["defaultDataMask"]["filterState"] == {"value": [1, 9], "label": "1 ≤ x ≤ 9"}


def test_equal_bounds_exact_mode():
    (nf,) = _native_filter_config(_spec([
        {"type": "range", "name": "Pin", "dataset": DS, "column": "n", "ge": 4, "le": 4},
    ]))
    assert nf["controlValues"]["enableSingleValue"] == 1
    assert nf["defaultDataMask"]["extraFormData"] == {"filters": [{"col": "n", "op": "==", "val": 4}]}
    assert nf["defaultDataMask"]["filterState"] == {"value": [4, 4], "label": "x = 4"}


def test_no_default_empty_mask():
    (nf,) = _native_filter_config(_spec([
        {"type": "range", "name": "Free", "dataset": DS, "column": "n"},
    ]))
    assert nf["defaultDataMask"] == {"extraFormData": {}, "filterState": {}, "ownState": {}}
    assert nf["controlValues"] == {"enableEmptyFilter": False}


def test_validators():
    with pytest.raises(ValidationError, match="ge .* > le"):
        _spec([{"type": "range", "name": "X", "dataset": DS, "column": "n", "ge": 9, "le": 1}])
    with pytest.raises(ValidationError, match="scopes unknown chart"):
        _spec([{"type": "range", "name": "X", "dataset": DS, "column": "n", "le": 8,
                "charts": ["Nope"]}])
    with pytest.raises(ValidationError, match="omitted or non-empty"):
        _spec([{"type": "range", "name": "X", "dataset": DS, "column": "n", "le": 8, "charts": []}])


def test_scope_fixup():
    spec = _spec([
        {"type": "range", "name": "Weeks", "dataset": DS, "column": "week_rank",
         "le": 8, "charts": ["A", "B"]},
    ])
    meta = {"native_filter_configuration": _native_filter_config(spec)}
    name_to_id = {"A": 101, "B": 102, "C": 103}
    new_meta, errors = scoped_filter_fixup(meta, spec, name_to_id)
    assert errors == []
    (nf,) = new_meta["native_filter_configuration"]
    assert nf["scope"] == {"rootPath": ["ROOT_ID"], "excluded": [103]}
    assert nf["chartsInScope"] == [101, 102]
    # pure: input untouched
    assert meta["native_filter_configuration"][0]["scope"]["excluded"] == []


def test_scope_fixup_names_problems():
    spec = _spec([
        {"type": "range", "name": "Weeks", "dataset": DS, "column": "week_rank",
         "le": 8, "charts": ["A"]},
    ])
    meta = {"native_filter_configuration": _native_filter_config(spec)}
    _, errors = scoped_filter_fixup(meta, spec, {"B": 2, "C": 3})
    assert errors and "not on the dashboard" in errors[0]
    _, errors = scoped_filter_fixup({"native_filter_configuration": []}, spec, {"A": 1})
    assert errors and "not found in imported" in errors[0]


def test_unscoped_spec_is_noop():
    spec = _spec([{"type": "range", "name": "Weeks", "dataset": DS, "column": "week_rank", "le": 8}])
    meta = {"native_filter_configuration": _native_filter_config(spec)}
    new_meta, errors = scoped_filter_fixup(meta, spec, {"A": 1, "B": 2, "C": 3})
    assert new_meta is meta and errors == []


def test_decompile_range_filter():
    from chartwright.decompile import decompile_bundle

    spec = _spec([
        {"type": "range", "name": "Weeks", "dataset": DS, "column": "week_rank", "le": 8},
    ])
    bundle = compile_bundle(spec, stub_resolution(spec))
    res = stub_resolution(spec)
    ds = res.for_chart(spec.charts[0].dataset)
    lookup = {ds.uuid: {"database": "examples", "schema": None, "table": "t"}}
    result = decompile_bundle(bundle, lambda u: lookup.get(u))
    assert result.losses == [], result.losses_json()
    (f,) = result.spec["filters"]
    assert f == {"type": "range", "name": "Weeks",
                 "dataset": {"database": "examples", "schema": None, "table": "t"},
                 "column": "week_rank", "le": 8}


def test_scope_fixup_normalizes_unscoped_filters():
    spec = _spec([
        {"type": "select", "name": "Class", "dataset": DS, "column": "k"},
        {"type": "range", "name": "Weeks", "dataset": DS, "column": "week_rank",
         "le": 8, "charts": ["A"]},
    ])
    meta = {"native_filter_configuration": _native_filter_config(spec)}
    new_meta, errors = scoped_filter_fixup(meta, spec, {"A": 1, "B": 2, "C": 3})
    assert errors == []
    select, rng = new_meta["native_filter_configuration"]
    assert select["chartsInScope"] == [1, 2, 3]
    assert select["scope"]["excluded"] == []
    assert rng["chartsInScope"] == [1]
    assert rng["scope"]["excluded"] == [2, 3]


def test_chart_payloads_from_bundle():
    from chartwright.apply import chart_payloads_from_bundle
    from chartwright import ids as _ids

    spec = _spec([{"type": "range", "name": "W", "dataset": DS, "column": "week_rank", "le": 8}])
    bundle = compile_bundle(spec, stub_resolution(spec))
    payloads = chart_payloads_from_bundle(bundle)
    assert len(payloads) == 3
    u = str(_ids.chart_uuid("sdc-t", "A"))
    assert payloads[u]["slice_name"] == "A"
    assert payloads[u]["viz_type"] == "big_number_total"
    assert payloads[u]["query_context"] is None
    metric = json.loads(payloads[u]["params"])["metric"]
    assert metric["sqlExpression"] == "COUNT(*)" and metric["label"] == "COUNT(*)"
