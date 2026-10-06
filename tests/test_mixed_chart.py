"""Mixed chart (mixed_timeseries): bars and a line on two axes, on a time or a
categorical x axis. Key names follow MixedTimeseries/controlPanel.tsx at 4.1.4,
5.0.0 and 6.1.0: query B's query keys take '_b', its display keys 'B'."""

import io
import json
import sys
import zipfile
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from chartwright.compiler import compile_bundle
from chartwright.decompile import decompile_bundle
from chartwright.dashdiff import _normalize
from chartwright.resolver import Resolution, _check_chart_fields
from chartwright.smoke import _mixed_queries
from chartwright.spec import load_spec
from chartwright.testing import edit_bundle, stub_resolution

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
    # The chart's filter, then the time-range filter on the axis (test_chart_time_range.py).
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
        "xAxis": {"axisLabel": {"showMaxLabel": False}, "boundaryGap": ["3%", "3%"]}}
    assert pl["y_axis_bounds"] == [None, 1]  # the axis maximum on every release
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


# -- an area under a line ------------------------------------------------------------

SOLAR = {
    "name": "Solar and net load", "type": "mixed", "dataset": DS,
    "x_column": "month_start", "time_grain": "P1M",
    "a": {"metrics": ["SUM(solar)"], "kind": "area", "opacity": 1},
    "b": {"metrics": ["SUM(net_load)"], "kind": "line"},
}


def _decompile(spec, edit=None):
    bundle = compile_bundle(spec, stub_resolution(spec))
    if edit is not None:
        bundle = edit_bundle(bundle, edit)
    ds = stub_resolution(spec).for_chart(spec.charts[0].dataset)
    return decompile_bundle(bundle, lambda u: {"database": "examples", "schema": None, "table": "t"}
                            if u == ds.uuid else None)


def _edit(**changes):
    def edit(path, doc):
        if "/charts/" in path:
            doc["params"].update(changes)
    return edit


def test_an_area_is_a_line_series_with_its_area_box_ticked():
    """MixedTimeseries/controlPanel.tsx createCustomizeSection: seriesType, area and
    opacity per query (area / areaB, opacity / opacityB) at 4.1.4, 5.0.0 and 6.1.0."""
    _, p = _params(_spec(SOLAR))["Solar and net load"]
    assert (p["seriesType"], p["seriesTypeB"]) == ("line", "line")
    assert p["area"] is True and p["opacity"] == 1
    assert "areaB" not in p and "opacityB" not in p
    flipped = {**SOLAR, "a": SOLAR["b"], "b": {"metrics": ["SUM(solar)"], "kind": "area"}}
    _, q = _params(_spec(flipped))["Solar and net load"]
    assert q["seriesTypeB"] == "line" and q["areaB"] is True
    assert "opacityB" not in q and "area" not in q  # Superset's own 0.2 fill, unwritten
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    for version in contract:
        assert check(version, contract, {"mixed_timeseries": set(p) | set(q)}) == [], version


def test_specs_without_an_area_write_no_area_keys():
    for _, p in _params(_spec(BY_MONTH, BY_CAUSE)).values():
        assert not {"area", "areaB", "opacity", "opacityB"} & set(p)


def test_an_area_round_trips_and_plans_clean():
    spec = _spec(SOLAR)
    result = _decompile(spec)
    assert result.losses == [], result.losses_json()
    back = result.spec["charts"][0]
    assert back["a"] == {"metrics": ["SUM(solar)"], "kind": "area", "opacity": 1}
    assert back["b"] == {"metrics": ["SUM(net_load)"], "kind": "line"}
    assert _normalize(load_spec(result.spec)) == _normalize(spec)


def test_opacity_is_an_areas_fill():
    for kind in ("bar", "line"):
        with pytest.raises(ValidationError, match='opacity is an area\'s fill; it needs kind "area"'):
            _spec({**SOLAR, "b": {"metrics": ["SUM(x)"], "kind": kind, "opacity": 0.5}})
    with pytest.raises(ValidationError):
        _spec({**SOLAR, "a": {**SOLAR["a"], "opacity": 1.5}})


def test_a_written_default_opacity_builds_and_plans_as_omitted():
    """0.2 is Superset's own fill opacity: written, it is kept, builds the same bytes as
    an omitted one, and compares equal in plan (decompile reads both back as omitted)."""
    omitted = _spec({**SOLAR, "a": {"metrics": ["SUM(solar)"], "kind": "area"}})
    written = _spec({**SOLAR, "a": {"metrics": ["SUM(solar)"], "kind": "area", "opacity": 0.2}})
    assert written.charts[0].a.opacity == 0.2
    assert compile_bundle(written, stub_resolution(written)) == \
        compile_bundle(omitted, stub_resolution(omitted))
    assert _normalize(written) == _normalize(omitted)
    # and on a line, where it fills nothing, it is no error
    _spec({**SOLAR, "b": {"metrics": ["SUM(x)"], "kind": "line", "opacity": 0.2}})


def test_area_settings_that_draw_nothing_are_no_loss():
    """An area box on a bar series and an opacity without an area draw nothing new."""
    spec = _spec(BY_MONTH)  # a: bars, b: a line
    result = _decompile(spec, _edit(area=True, opacity=0.6, opacityB=0.9, areaB=False))
    assert result.losses == [], result.losses_json()
    back = result.spec["charts"][0]
    assert "kind" not in back["a"] and back["b"]["kind"] == "line"
    assert "opacity" not in back["a"] and "opacity" not in back["b"]


def test_a_ui_area_reads_back_with_its_opacity():
    result = _decompile(_spec(BY_MONTH), _edit(areaB=True, opacityB=0.5))
    assert result.spec["charts"][0]["b"]["kind"] == "area"
    assert result.spec["charts"][0]["b"]["opacity"] == 0.5
    result = _decompile(_spec(BY_MONTH), _edit(areaB=True, opacityB=0.2))
    assert "opacity" not in result.spec["charts"][0]["b"]


def test_a_series_type_reads_as_the_line_superset_draws():
    """transformSeries draws any seriesType but bar, scatter, smooth and the steps as a
    straight line, the old echarts_timeseries_* names included (Timeseries/
    transformers.ts:237-243 at 4.1.4, :306-312 at 6.1.0), so those are no loss."""
    result = _decompile(_spec(BY_MONTH), _edit(seriesType="echarts_timeseries_bar",
                                               seriesTypeB="echarts_timeseries_line"))
    assert result.losses == [], result.losses_json()
    assert result.spec["charts"][0]["a"]["kind"] == "line"
    result = _decompile(_spec(BY_MONTH), _edit(seriesTypeB="smooth", areaB=True))
    assert [loss.what for loss in result.losses] == [
        "query B series type 'smooth' not preserved (a straight line on re-apply)"]
    assert result.spec["charts"][0]["b"]["kind"] == "area"


def test_query_b_stores_the_same_defaults_as_query_a():
    """A mixed chart saved in Superset stores query B's defaults beside query A's; they
    were named as params the spec can't carry."""
    defaults = dict(markerSizeB=6, comparison_type_b="values", truncate_metric_b=True,
                    rolling_type_b="None", sort_series_typeB="sum")
    result = _decompile(_spec(BY_MONTH), _edit(**defaults))
    assert result.losses == [], result.losses_json()
    result = _decompile(_spec(BY_MONTH), _edit(markerSizeB=10, rolling_type_b="cumsum"))
    (loss,) = result.losses
    assert "markerSizeB=10" in loss.what and "rolling_type_b='cumsum'" in loss.what
