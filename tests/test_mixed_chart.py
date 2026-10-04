"""Mixed chart (mixed_timeseries): bars and a line on two axes, on a time or a
categorical x axis. Key names follow MixedTimeseries/controlPanel.tsx at 4.1.4,
5.0.0 and 6.1.0: query B's query keys take '_b', its display keys 'B'."""

import io
import json
import sys
import zipfile
from pathlib import Path

import yaml

from chartwright.compiler import compile_bundle
from chartwright.decompile import decompile_bundle
from chartwright.dashdiff import _normalize
from chartwright.resolver import Resolution, _check_chart_fields
from chartwright.smoke import _mixed_queries
from chartwright.spec import load_spec
from chartwright.testing import stub_resolution

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
from params_drift import CONTRACT, check  # noqa: E402

DS = {"database": "examples", "table": "t"}
BY_MONTH = {
    "name": "Revenue and revenue per order", "type": "mixed", "dataset": DS,
    "x_column": "month_start", "time_grain": "P1M",
    "a": {"metrics": ["SUM(revenue)"]},
    "b": {"metrics": ["MAX(revenue_per_order)"], "kind": "line", "axis": "secondary"},
    "number_format_secondary": ".3f",
    "filters": [{"column": "event_type", "op": "==", "value": "Online"}],
}
BY_CAUSE = {
    "name": "Categories: count and revenue", "type": "mixed", "dataset": DS,
    "x_column": "category",
    "a": {"metrics": ["COUNT(*)"]},
    "b": {"metrics": ["SUM(revenue)"], "kind": "line", "axis": "secondary"},
}


def _spec(*charts):
    return load_spec({
        "spec_version": "1",
        "dashboard": {"title": "T", "slug": "sdc-t"},
        "charts": list(charts),
        "layout": {"rows": [[c["name"]] for c in charts]},
    })


def _params(spec, res=None):
    zf = zipfile.ZipFile(io.BytesIO(compile_bundle(spec, res or stub_resolution(spec))))
    out = {}
    for n in zf.namelist():
        if "/charts/" in n:
            cy = yaml.safe_load(zf.read(n))
            out[cy["slice_name"]] = (cy["viz_type"], cy["params"])
    return out


def test_compile_time_axis_mixed():
    viz, p = _params(_spec(BY_MONTH))["Revenue and revenue per order"]
    assert viz == "mixed_timeseries"
    assert p["x_axis"] == "month_start" and p["time_grain_sqla"] == "P1M"
    assert [m["label"] for m in p["metrics"]] == ["SUM(revenue)"]
    assert [m["label"] for m in p["metrics_b"]] == ["MAX(revenue_per_order)"]
    assert (p["seriesType"], p["seriesTypeB"]) == ("bar", "line")
    assert (p["yAxisIndex"], p["yAxisIndexB"]) == (0, 1)
    assert p["y_axis_format"] == "SMART_NUMBER" and p["y_axis_format_secondary"] == ".3f"
    # The chart's own filter, then the time-range filter on its axis (0.2.1).
    assert p["adhoc_filters"] == p["adhoc_filters_b"] and len(p["adhoc_filters"]) == 2
    assert p["adhoc_filters"][1]["operator"] == "TEMPORAL_RANGE"
    assert p["groupby"] == [] and p["groupby_b"] == []


def test_categorical_mixed_has_no_grain_and_binds_the_dataset_time():
    spec = _spec(BY_CAUSE, BY_MONTH)
    res = stub_resolution(spec)
    for ds in res.datasets.values():
        ds.main_dttm_col = "event_date"
    params = _params(spec, res)
    _, cause = params["Categories: count and revenue"]
    assert "time_grain_sqla" not in cause
    assert cause["granularity_sqla"] == "event_date"  # dashboard time filters still reach it
    _, month = params["Revenue and revenue per order"]
    assert "granularity_sqla" not in month  # its own x axis is the time binding


def test_decompile_round_trips_both_forms():
    spec = _spec(BY_MONTH, BY_CAUSE)
    bundle = compile_bundle(spec, stub_resolution(spec))
    ds = stub_resolution(spec).for_chart(spec.charts[0].dataset)
    result = decompile_bundle(bundle, lambda u: {"database": "examples", "schema": None, "table": "t"}
                              if u == ds.uuid else None)
    assert result.losses == [], result.losses_json()
    assert _normalize(load_spec(result.spec)) == _normalize(spec)


def test_smoke_sends_both_queries():
    spec = _spec(BY_MONTH, BY_CAUSE)
    res = stub_resolution(spec)
    ds = res.for_chart(spec.charts[0].dataset)
    month = next(c for c in spec.charts if c.name.startswith("Revenue"))
    qa, qb = _mixed_queries(month, spec, ds)
    assert qa["columns"][0]["columnType"] == "BASE_AXIS"  # time axis, bucketed
    assert [m["label"] for m in qa["metrics"]] == ["SUM(revenue)"]
    assert [m["label"] for m in qb["metrics"]] == ["MAX(revenue_per_order)"]
    assert qa["filters"] == qb["filters"] and qa["filters"]
    cause = next(c for c in spec.charts if c.name.startswith("Categories"))
    qa, qb = _mixed_queries(cause, spec, ds)
    assert qa["columns"] == ["category"] and qb["columns"] == ["category"]


def test_resolver_checks_both_queries():
    spec = _spec({**BY_CAUSE, "b": {"metrics": ["SUM(nope)"], "groupby": "missing"}})
    ds = stub_resolution(spec).for_chart(spec.charts[0].dataset)
    ds.columns = ["category", "revenue"]
    res = Resolution()
    _check_chart_fields(spec.charts[0], ds, res)
    assert {e.code for e in res.errors} == {"column_not_found"}
    assert any("missing" in e.ref for e in res.errors)


X_LABEL_KEYS = {"x_axis_time_format", "xAxisLabelRotation", "force_max_interval", "xAxisLabelInterval"}


def test_x_labels_every_month_on_a_time_axis_every_category_on_a_category_axis():
    month = {**BY_MONTH, "x_label_format": "%b", "x_label_every": True}
    cause = {**BY_CAUSE, "x_label_every": True, "x_label_rotation": 45}
    params = _params(_spec(month, cause))
    _, pm = params["Revenue and revenue per order"]
    assert pm["x_axis_time_format"] == "%b"
    assert pm["force_max_interval"] is True  # the grain is the widest tick spacing
    assert "xAxisLabelInterval" not in pm  # ECharts reads interval on category axes only
    _, pc = params["Categories: count and revenue"]
    assert pc["xAxisLabelInterval"] == "0" and pc["xAxisLabelRotation"] == 45
    assert "force_max_interval" not in pc and "x_axis_time_format" not in pc


def test_x_labels_unset_emit_nothing():
    for _, p in _params(_spec(BY_MONTH, BY_CAUSE)).values():
        assert not X_LABEL_KEYS & set(p)


def test_x_labels_round_trip_and_stay_within_the_contract():
    spec = _spec({**BY_MONTH, "x_label_format": "%b", "x_label_every": True},
                 {**BY_CAUSE, "x_label_every": True, "x_label_rotation": 45})
    bundle = compile_bundle(spec, stub_resolution(spec))
    ds = stub_resolution(spec).for_chart(spec.charts[0].dataset)
    result = decompile_bundle(bundle, lambda u: {"database": "examples", "schema": None, "table": "t"}
                              if u == ds.uuid else None)
    assert result.losses == [], result.losses_json()
    assert _normalize(load_spec(result.spec)) == _normalize(spec)
    emitted = {"mixed_timeseries": set().union(*(set(p) for _, p in _params(spec).values()))}
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    for version in contract:  # 6.1.0-only keys are allowed, and ignored, before 6.1.0
        assert check(version, contract, emitted) == [], version


def test_markers_per_series_emit_only_when_set_and_round_trip():
    cause = {**BY_CAUSE, "b": {**BY_CAUSE["b"], "markers": True}}
    _, p = _params(_spec(cause))["Categories: count and revenue"]
    assert p["markerEnabledB"] is True and "markerEnabled" not in p
    for _, q in _params(_spec(BY_MONTH, BY_CAUSE)).values():
        assert not {"markerEnabled", "markerEnabledB"} & set(q)
    spec = _spec(cause)
    ds = stub_resolution(spec).for_chart(spec.charts[0].dataset)
    result = decompile_bundle(compile_bundle(spec, stub_resolution(spec)),
                              lambda u: {"database": "examples", "schema": None, "table": "t"} if u == ds.uuid else None)
    assert result.losses == [], result.losses_json()
    assert _normalize(load_spec(result.spec)) == _normalize(spec)
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    for version in contract:
        assert check(version, contract, {"mixed_timeseries": set(p)}) == [], version


def test_every_month_keeps_both_ends_on_lines_and_bars():
    """6.1.0 left the first and last month of a 13-month line unlabelled (a tick on the
    axis edge gets no label; the forced last label hid the last tick's)."""
    line = {"name": "Trend", "type": "timeseries_line", "dataset": DS, "metrics": ["MAX(r)"],
            "time_column": "month_start", "time_grain": "P1M", "x_label_every": True, "y_axis_max": 1}
    scatter = {**line, "name": "Dots", "type": "timeseries_scatter"}
    scatter.pop("y_axis_max")
    params = _params(_spec(line, scatter, {**BY_MONTH, "x_label_every": True}))
    _, pl = params["Trend"]
    assert json.loads(pl["echart_options"]) == {
        "xAxis": {"axisLabel": {"showMaxLabel": False}, "boundaryGap": ["3%", "3%"]},
        "yAxis": {"max": 1}}
    _, pm = params["Revenue and revenue per order"]  # bars are padded already
    assert json.loads(pm["echart_options"]) == {"xAxis": {"axisLabel": {"showMaxLabel": False}}}
    _, ps = params["Dots"]  # scatter's 6.1.0 panel has no echart_options
    assert ps["force_max_interval"] is True and "echart_options" not in ps
    spec = _spec(line)
    ds = stub_resolution(spec).for_chart(spec.charts[0].dataset)
    result = decompile_bundle(compile_bundle(spec, stub_resolution(spec)),
                              lambda u: {"database": "examples", "schema": None, "table": "t"} if u == ds.uuid else None)
    assert result.losses == [], result.losses_json()
    assert _normalize(load_spec(result.spec)) == _normalize(spec)
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    for version in contract:
        assert check(version, contract, {"echarts_timeseries_line": set(pl), "mixed_timeseries": set(pm)}) == [], version



def test_a_grain_beside_a_category_column_keeps_the_category_axis():
    """Superset stores time_grain_sqla on every UI-born mixed chart, so decompile hands
    back a time_grain even on a category x axis. A column the instance reports as not
    temporal stays a category axis: no grain, the dashboard time binding, and the
    category label control."""
    spec = _spec({**BY_CAUSE, "time_grain": "P1D", "x_label_every": True})
    res = stub_resolution(spec)
    for ds in res.datasets.values():
        ds.main_dttm_col = "event_date"
        ds.column_types["category"] = 1  # STRING
    _, p = _params(spec, res)["Categories: count and revenue"]
    assert "time_grain_sqla" not in p
    assert p["granularity_sqla"] == "event_date"
    assert p["xAxisLabelInterval"] == "0" and "force_max_interval" not in p


def test_a_reported_temporal_column_is_a_time_axis_without_a_grain():
    spec = _spec({**BY_MONTH, "time_grain": None})
    res = stub_resolution(spec)
    for ds in res.datasets.values():
        ds.column_types["month_start"] = 2  # TEMPORAL
    _, p = _params(spec, res)["Revenue and revenue per order"]
    assert p["time_grain_sqla"] == "P1D" and "granularity_sqla" not in p


def test_smoke_queries_the_axis_the_chart_draws():
    spec = _spec({**BY_CAUSE, "time_grain": "P1D"})
    res = stub_resolution(spec)
    ds = res.for_chart(spec.charts[0].dataset)
    ds.column_types["category"] = 1  # STRING
    qa, qb = _mixed_queries(spec.charts[0], spec, ds)
    assert qa["columns"] == ["category"] and qb["columns"] == ["category"]
