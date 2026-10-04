"""A chart's time range, its own `time_range` or a dashboard time filter's,
reaches its query only through a time binding (compiler.time_binding).

A chart without a time axis binds the dataset's main time column as
granularity_sqla. A chart drawn on a time axis (timeseries_*, big_number_trend,
mixed on a time column) gets an adhoc TEMPORAL_RANGE filter on that axis. Before
that filter, Superset 4.1.4, 5.0.0 and 6.1.0 applied neither the chart's
time_range nor the dashboard's time filter to those charts (seen live: a line
chart with time_range "2004-03-01 : 2005-03-02" still drew 2003 to 2005)."""

import io
import json
import zipfile

import pytest
import yaml

from chartwright.compiler import compile_bundle
from chartwright.dashdiff import _normalize
from chartwright.decompile import decompile_bundle
from chartwright.smoke import _mixed_queries, _query_for, _with_time_range
from chartwright.spec import load_spec
from chartwright.testing import stub_resolution

DS = {"database": "examples", "table": "t"}
RANGE = "2004-03-01 : 2005-03-02"
TIME_AXIS = [
    {"name": "Line", "type": "timeseries_line", "metrics": ["COUNT(*)"], "time_column": "ts"},
    {"name": "Bars", "type": "timeseries_bar", "metrics": ["COUNT(*)"], "time_column": "ts"},
    {"name": "Area", "type": "timeseries_area", "metrics": ["COUNT(*)"], "time_column": "ts"},
    {"name": "Dots", "type": "timeseries_scatter", "metrics": ["COUNT(*)"], "time_column": "ts"},
    {"name": "Trend", "type": "big_number_trend", "metric": "COUNT(*)", "time_column": "ts"},
]
MIXED_TIME = {"name": "Mixed by month", "type": "mixed", "x_column": "ts", "time_grain": "P1M",
              "a": {"metrics": ["COUNT(*)"]},
              "b": {"metrics": ["SUM(amount)"], "kind": "line", "axis": "secondary"}}
MIXED_CATEGORY = {"name": "Mixed by region", "type": "mixed", "x_column": "region",
                  "a": {"metrics": ["COUNT(*)"]},
                  "b": {"metrics": ["SUM(amount)"], "kind": "line", "axis": "secondary"}}
AXIS_NAMES = {c["name"] for c in TIME_AXIS} | {MIXED_TIME["name"]}
NO_AXIS = [
    {"name": "Total", "type": "big_number_total", "metric": "COUNT(*)"},
    {"name": "Table", "type": "table", "groupby": ["region"], "metrics": ["COUNT(*)"]},
    {"name": "Pie", "type": "pie", "metric": "COUNT(*)", "groupby": "region"},
    {"name": "Pivot", "type": "pivot_table", "rows": ["region"], "columns": [], "metrics": ["COUNT(*)"]},
    {"name": "Bar", "type": "bar", "x_column": "region", "metrics": ["COUNT(*)"]},
    MIXED_CATEGORY,
]


def _spec(charts, time_range=None):
    charts = [{**c, "dataset": DS, **({"time_range": time_range} if time_range else {})}
              for c in charts]
    return load_spec({
        "spec_version": "1",
        "dashboard": {"title": "T", "slug": "sdc-t"},
        "charts": charts,
        "layout": {"rows": [[c["name"]] for c in charts]},
    })


def _resolution(spec, main_dttm_col="ts"):
    res = stub_resolution(spec)
    for ds in res.datasets.values():
        ds.main_dttm_col = main_dttm_col
        ds.column_types.update({"ts": 2, "region": 1})
    return res


def _bundle(spec, res=None):
    return compile_bundle(spec, res or _resolution(spec))


def _params(spec, res=None):
    zf = zipfile.ZipFile(io.BytesIO(_bundle(spec, res)))
    return {cy["slice_name"]: cy["params"] for cy in
            (yaml.safe_load(zf.read(n)) for n in zf.namelist() if "/charts/" in n)}


def _temporal(params, key="adhoc_filters"):
    return [f for f in params.get(key) or [] if f.get("operator") == "TEMPORAL_RANGE"]


def _lookup(spec, res):
    by_uuid = {res.for_chart(c.dataset).uuid: dict(DS, schema=None) for c in spec.charts}
    return lambda u: by_uuid.get(u)


@pytest.mark.parametrize("time_range", [RANGE, None])
def test_a_time_axis_chart_filters_its_axis_with_its_range(time_range):
    params = _params(_spec(TIME_AXIS + [MIXED_TIME], time_range))
    for name, p in params.items():
        expected = time_range or "No filter"
        assert p["time_range"] == expected, name
        assert _temporal(p) == [{"clause": "WHERE", "expressionType": "SIMPLE", "subject": "ts",
                                 "operator": "TEMPORAL_RANGE", "comparator": expected}], name
        assert "granularity_sqla" not in p, name  # it would replace the axis column


def test_a_mixed_time_chart_filters_both_queries():
    p = _params(_spec([MIXED_TIME], RANGE))["Mixed by month"]
    assert p["adhoc_filters_b"] == p["adhoc_filters"] and _temporal(p, "adhoc_filters_b")


def test_the_range_filter_follows_the_charts_own_filters():
    chart = {**TIME_AXIS[0], "filters": [{"column": "region", "op": "==", "value": "West"}]}
    p = _params(_spec([chart], RANGE))["Line"]
    assert [f["operator"] for f in p["adhoc_filters"]] == ["==", "TEMPORAL_RANGE"]


@pytest.mark.parametrize("time_range", [RANGE, None])
def test_a_chart_without_a_time_axis_keeps_the_granularity_binding(time_range):
    for name, p in _params(_spec(NO_AXIS, time_range)).items():
        assert p["time_range"] == (time_range or "No filter"), name
        assert p["granularity_sqla"] == "ts", name
        assert not _temporal(p) and not _temporal(p, "adhoc_filters_b"), name


def test_a_dataset_without_a_time_column_binds_nothing_to_a_chart_without_an_axis():
    spec = _spec(NO_AXIS[:1], RANGE)
    p = _params(spec, _resolution(spec, main_dttm_col=None))["Total"]
    assert "granularity_sqla" not in p and not _temporal(p)


def test_compiles_are_deterministic():
    spec = _spec(TIME_AXIS + [MIXED_TIME] + NO_AXIS, RANGE)
    assert _bundle(spec) == _bundle(spec)


@pytest.mark.parametrize("time_range", [RANGE, None])
def test_decompile_reads_the_range_back_without_a_chart_filter(time_range):
    spec = _spec(TIME_AXIS + [MIXED_TIME] + NO_AXIS, time_range)
    res = _resolution(spec)
    result = decompile_bundle(_bundle(spec, res), _lookup(spec, res))
    assert result.losses == [], [loss.as_dict() for loss in result.losses]
    for chart in result.spec["charts"]:
        assert chart.get("time_range") == time_range, chart["name"]
        assert "filters" not in chart, chart["name"]
    # plan compares exactly this: no drift on an unchanged spec.
    assert _normalize(load_spec(result.spec)) == _normalize(spec)


def test_smoke_queries_with_the_charts_range():
    spec = _spec(TIME_AXIS + [MIXED_TIME] + NO_AXIS, RANGE)
    res = _resolution(spec)
    for chart in spec.charts:
        ds = res.for_chart(chart.dataset)
        queries = _mixed_queries(chart, spec, ds) if chart.type == "mixed" else [_query_for(chart, spec)]
        for q in (_with_time_range(q, chart, ds) for q in queries):
            assert q["time_range"] == RANGE, chart.name
            temporal = [f for f in q["filters"] if f["op"] == "TEMPORAL_RANGE"]
            if chart.name in AXIS_NAMES:
                assert temporal == [{"col": "ts", "op": "TEMPORAL_RANGE", "val": RANGE}], chart.name
                assert "granularity" not in q, chart.name
            else:
                assert q["granularity"] == "ts" and not temporal, chart.name


def test_smoke_without_a_range_stays_unfiltered():
    spec = _spec(TIME_AXIS[:1] + NO_AXIS[:1])
    res = _resolution(spec)
    for chart in spec.charts:
        q = _with_time_range(_query_for(chart, spec), chart, res.for_chart(chart.dataset))
        assert q["time_range"] == "No filter" and "granularity" not in q
        assert not [f for f in q["filters"] if f["op"] == "TEMPORAL_RANGE"]


def test_the_live_fixture_ranges_land_on_the_axis():
    """The live display-controls fixture: its trend KPI and its mixed chart set a range."""
    from pathlib import Path
    data = json.loads((Path(__file__).parent / "fixtures" / "live_display_controls.json").read_text())
    spec = load_spec(data)
    params = _params(spec, _resolution(spec, main_dttm_col="order_date"))
    assert _temporal(params["Revenue KPI"])[0]["comparator"] == "Last year"
    assert _temporal(params["Revenue and Orders"], "adhoc_filters_b")[0]["comparator"] == "Last year"
