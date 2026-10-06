"""Box plot (Superset's `box_plot` plugin): a measure's distribution in each group.

Key names follow BoxPlot/controlPanel.ts at 4.1.4, 5.0.0 and 6.1.0 (row_limit is a
6.0.0 control). The observations are the rows of `columns` (distribute_across), one
box per `groupby` value, turned into quartiles by the boxplot post-processing step:
seen rendering on 4.1.4 and 6.1.0, the daily sales of each month."""

import io
import json
import sys
import zipfile
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from chartwright.compiler import compile_bundle
from chartwright.dashdiff import _normalize
from chartwright.decompile import decompile_bundle
from chartwright.design import advise
from chartwright.design.presets import Overlay
from chartwright.resolver import Resolution, _check_chart_fields
from chartwright.smoke import _box_plot_query, _observations_warning
from chartwright.spec import load_spec
from chartwright.testing import edit_bundle, stub_resolution
from chartwright.versions import check_spec_version

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
from params_drift import CONTRACT, check, emitted_keys_by_viz_type  # noqa: E402
from test_design_data import FakeProber, resolution  # noqa: E402

DS = {"database": "examples", "table": "t"}
EMPTY = Overlay()
DAILY = {"name": "Daily sales by month", "type": "box_plot", "dataset": DS,
         "metrics": ["SUM(sales)"], "distribute_across": ["order_date"], "time_grain": "P1D",
         "groupby": ["month"]}


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


def _lookup(spec):
    ds = stub_resolution(spec).for_chart(spec.charts[0].dataset)
    return lambda u: {"database": "examples", "schema": None, "table": "t"} if u == ds.uuid else None


# -- compile ---------------------------------------------------------------------------


def test_a_box_plot_writes_its_observations_and_groups():
    viz, p = _params(_spec(DAILY))["Daily sales by month"]
    assert viz == "box_plot"
    assert (p["columns"], p["groupby"]) == (["order_date"], ["month"])
    assert [m["label"] for m in p["metrics"]] == ["SUM(sales)"]
    # Written even at Superset's default: buildQuery adds no boxplot step without it.
    assert p["whiskerOptions"] == "Tukey"
    # The grain buckets a column only where temporal_columns_lookup marks it.
    assert p["time_grain_sqla"] == "P1D" and p["temporal_columns_lookup"] == {"order_date": True}
    assert set(p) == {"datasource", "viz_type", "time_range", "adhoc_filters", "columns", "groupby",
                      "metrics", "whiskerOptions", "time_grain_sqla", "temporal_columns_lookup"}


def test_only_the_time_columns_take_the_grain():
    spec = _spec({**DAILY, "distribute_across": ["order_date", "customer"]})
    res = stub_resolution(spec)
    res.for_chart(spec.charts[0].dataset).column_types = {"order_date": 2, "customer": 1}
    _, p = _params(spec, res)["Daily sales by month"]
    assert p["temporal_columns_lookup"] == {"order_date": True}
    _, p = _params(_spec({**DAILY, "time_grain": None}))["Daily sales by month"]
    assert "time_grain_sqla" not in p and "temporal_columns_lookup" not in p


@pytest.mark.parametrize("whiskers, option", [
    ("tukey", "Tukey"), ("min_max", "Min/max (no outliers)"), ([5, 95], "5/95 percentiles"),
    ([0, 100], "0/100 percentiles")])
def test_whiskers(whiskers, option):
    _, p = _params(_spec({**DAILY, "whiskers": whiskers}))["Daily sales by month"]
    assert p["whiskerOptions"] == option


def test_every_display_field_has_its_control():
    chart = {**DAILY, "number_format": "$,.0f", "x_label_format": "%b %Y",
             "x_axis_title": "Month", "y_axis_title": "Daily sales", "x_label_rotation": 90,
             "row_limit": 5000, "color_scheme": "wavesOfBlue"}
    _, p = _params(_spec(chart))["Daily sales by month"]
    assert (p["number_format"], p["date_format"], p["x_ticks_layout"]) == ("$,.0f", "%b %Y", "90°")
    assert (p["x_axis_title"], p["x_axis_title_margin"]) == ("Month", 50)
    assert (p["y_axis_title"], p["y_axis_title_margin"], p["y_axis_title_position"]) == (
        "Daily sales", 15, "Top")
    assert (p["row_limit"], p["color_scheme"]) == (5000, "wavesOfBlue")


def test_a_dashboard_time_filter_reaches_it_through_the_dataset_time():
    spec = _spec({**DAILY, "time_range": "Last year"})
    res = stub_resolution(spec)
    res.for_chart(spec.charts[0].dataset).main_dttm_col = "order_date"
    _, p = _params(spec, res)["Daily sales by month"]
    assert p["granularity_sqla"] == "order_date"
    assert not [f for f in p["adhoc_filters"] if f["operator"] == "TEMPORAL_RANGE"]


# -- the spec --------------------------------------------------------------------------


@pytest.mark.parametrize("change, message", [
    ({"groupby": ["order_date"]}, "in both distribute_across and groupby"),
    ({"groupby": ["month", "month"]}, "groupby lists a value twice"),
    ({"distribute_across": []}, "at least 1"),
    ({"metrics": []}, "at least 1"),
    ({"whiskers": [95, 5]}, "the lower first"),
    ({"whiskers": [5, 101]}, "from 0 to 100"),
    ({"whiskers": [5]}, "at least 2"),
    ({"whiskers": [1, 2, 3]}, "at most 2"),
    ({"whiskers": "iqr"}, "whiskers"),
    ({"x_label_rotation": 30}, "x_label_rotation"),
])
def test_a_box_plot_is_validated(change, message):
    with pytest.raises(ValidationError, match=message):
        _spec({**DAILY, **change})


# -- decompile ---------------------------------------------------------------------------


@pytest.mark.parametrize("chart", [DAILY, {**DAILY, "groupby": [], "time_grain": None}, {
    **DAILY, "whiskers": [2, 98], "number_format": ",.0f", "x_label_format": "%b",
    "x_axis_title": "M", "y_axis_title": "S", "x_label_rotation": 0, "row_limit": 9,
    "color_scheme": "bnbColors", "metrics": ["SUM(sales)", "COUNT(*)"], "time_range": "Last year",
    "filters": [{"column": "deal_size", "op": "==", "value": "Large"}]},
    {**DAILY, "whiskers": "min_max"}])
def test_decompile_round_trips(chart):
    spec = _spec(chart)
    result = decompile_bundle(compile_bundle(spec, stub_resolution(spec)), _lookup(spec))
    assert result.losses == [], result.losses_json()
    assert _normalize(load_spec(result.spec)) == _normalize(spec)


def test_a_box_plot_saved_in_superset_reads_back_whole():
    """The featured-charts export (6.1.0) holds a box plot made in Explore."""
    blob = (Path(__file__).parent / "fixtures" / "featured_charts_export.zip").read_bytes()
    result = decompile_bundle(blob, lambda u: {"database": "examples", "schema": None, "table": "t"})
    chart = next(c for c in result.spec["charts"] if c["type"] == "box_plot")
    assert not [loss for loss in result.losses if loss.where == chart["name"]]
    assert {k: chart.get(k) for k in ("metrics", "distribute_across", "groupby", "time_grain",
                                      "whiskers", "color_scheme")} == {
        "metrics": ["count"], "distribute_across": ["order_date"], "groupby": ["product_line"],
        "time_grain": "P1D", "whiskers": None, "color_scheme": "supersetColors"}


def _decompiled_after(**stored):
    spec = _spec(DAILY)

    def edit(path, doc):
        if "/charts/" in path:
            doc["params"].update(stored)

    return decompile_bundle(edit_bundle(compile_bundle(spec, stub_resolution(spec)), edit),
                            _lookup(spec))


def test_what_the_spec_cant_hold_is_named():
    out = _decompiled_after(whiskerOptions="Outer fences")
    assert any("whisker option 'Outer fences' not preserved" in loss.what for loss in out.losses)
    out = _decompiled_after(series_limit=5, zoomable=True)
    assert any("series_limit=5" in loss.what and "zoomable=True" in loss.what for loss in out.losses)
    out = _decompiled_after(zoomable=False, series_limit=None, x_ticks_layout="staggered")
    assert out.losses == [] and out.spec["charts"][0]["x_label_rotation"] == 45
    out = _decompiled_after(groupby=[{"expressionType": "SQL", "sqlExpression": "x", "label": "x"}])
    assert out.spec["charts"] == [] and any("SQL columns" in loss.what for loss in out.losses)


# -- releases ----------------------------------------------------------------------------


def test_a_row_limit_warns_before_6_0():
    spec = _spec({**DAILY, "row_limit": 5000})
    for version in ("4.1.4", "5.0.0"):
        out = check_spec_version(spec, version)
        assert out.errors == [] and [w["field"] for w in out.warnings] == ["row_limit"], version
        assert "the dashboard's query stops at it" in out.warnings[0]["detail"]
    assert check_spec_version(spec, "6.0.0").warnings == []
    assert check_spec_version(_spec(DAILY), "4.1.4").warnings == []


def test_the_contract_declares_every_key_at_its_release():
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    emitted = emitted_keys_by_viz_type()["box_plot"]
    assert {"columns", "whiskerOptions", "temporal_columns_lookup", "row_limit"} <= emitted
    for version in ("4.1.4", "5.0.0", "6.1.0"):
        assert check(version, contract, {"box_plot": emitted}) == []
    assert "row_limit" in contract["6.1.0"]["box_plot"]
    assert "row_limit" not in contract["5.0.0"]["box_plot"]


# -- resolve, smoke ---------------------------------------------------------------------


def test_resolve_checks_every_column_and_metric():
    spec = _spec({**DAILY, "metrics": ["SUM(nope)"], "groupby": ["missing"]})
    ds = stub_resolution(spec).for_chart(spec.charts[0].dataset)
    ds.columns = ["order_date", "sales"]
    res = Resolution()
    _check_chart_fields(spec.charts[0], ds, res)
    assert sorted(e.ref for e in res.errors) == ["missing", "nope"]


def test_smoke_queries_the_observations_as_the_plugin_does():
    spec = _spec(DAILY)
    ds = stub_resolution(spec).for_chart(spec.charts[0].dataset)
    q = _box_plot_query(spec.charts[0], spec, ds)
    assert q["columns"][0]["columnType"] == "BASE_AXIS" and q["columns"][0]["timeGrain"] == "P1D"
    assert q["columns"][1:] == ["month"] and q["row_limit"] == 10000


def test_smoke_says_when_the_observations_reach_the_row_limit():
    chart = _spec({**DAILY, "row_limit": 37}).charts[0]
    assert _observations_warning(chart, 36) is None
    assert "reached its row_limit (37 rows)" in _observations_warning(chart, 37)
    assert "10,000, where Superset 6.0.0 or later stops" in _observations_warning(
        _spec(DAILY).charts[0], 10000)


# -- design brain -----------------------------------------------------------------------


def _rules(chart, **kw):
    return [f for f in advise(_spec(chart), overlay=EMPTY, **kw).findings
            if f.rule.startswith("chart.box-plot") or f.rule == "chart.ordinal-order"]


def test_a_box_needs_many_observations():
    found = [f for f in _rules({**DAILY, "time_grain": "P1M"})
             if f.rule == "chart.box-plot-observations"]
    assert [f.severity for f in found] == ["warn"] and "one observation per month" in found[0].detail
    for quiet in (DAILY, {**DAILY, "groupby": ["day_of_week"], "time_grain": "P1D"},
                  {**DAILY, "time_grain": None}):
        assert not [f for f in _rules(quiet) if f.rule == "chart.box-plot-observations"]


def test_too_many_boxes_with_a_profile():
    res = resolution(order_date=2, month=0, sales=0)
    orders = {**DAILY, "dataset": {"database": "db", "table": "orders"}}
    found = [f for f in _rules(orders, resolution=res, prober=FakeProber({"month": 40}))
             if f.rule == "chart.box-plot-groups"]
    assert [f.severity for f in found] == ["warn"]
    assert not [f for f in _rules(orders, resolution=res, prober=FakeProber({"month": 12}))
                if f.rule == "chart.box-plot-groups"]
    assert not [f for f in _rules(orders) if f.rule == "chart.box-plot-groups"]  # offline


def test_ordinal_groups_sort_by_name_unless_the_dataset_says_numeric():
    named = {**DAILY, "dataset": {"database": "db", "table": "orders"}, "groupby": ["month_name"]}
    assert "chart.ordinal-order" in {f.rule for f in _rules(named)}
    numeric = {**named, "groupby": ["month"]}
    assert "chart.ordinal-order" not in {f.rule for f in _rules(
        numeric, resolution=resolution(order_date=2, month=0, sales=0))}
