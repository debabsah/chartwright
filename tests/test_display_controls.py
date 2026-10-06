"""Chart display controls: axis titles and bounds, values on the marks, stacking,
legends, category sort, series limits, chart time ranges, trend comparison,
table, pivot, heatmap and label options. Every field is optional and emitted only
when set; key names and values follow each chart's controlPanel.tsx at 4.1.4,
5.0.0 and 6.1.0 (tools/contracts/params-contract.json)."""

import functools
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

import chartwright.dashdiff as dashdiff
import chartwright.resolver as resolver
from chartwright import ids
from chartwright.compiler import compile_bundle
from chartwright.dashdiff import _normalize, plan
from chartwright.decompile import decompile_bundle
from chartwright.resolver import Resolution, ResolvedDataset, _check_chart_fields
from chartwright.spec import load_spec
from chartwright.testing import edit_bundle, stub_resolution

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
from params_drift import CONTRACT, check  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
DISPLAY = json.loads((FIXTURES / "display_controls.json").read_text(encoding="utf-8"))
DS = {"database": "examples", "table": "t"}

# Every params key a display field writes. None may appear unless a spec sets it.
NEW_KEYS = {
    "x_axis_title", "x_axis_title_margin", "y_axis_title", "y_axis_title_margin",
    "y_axis_title_position", "yAxisTitleSecondary", "y_axis_bounds", "y_axis_bounds_secondary",
    "truncateYAxis", "logAxis", "logAxisSecondary", "show_value", "show_valueB", "stack",
    "stackB", "only_total", "only_totalB", "contributionMode", "limit", "limit_b",
    "timeseries_limit_metric_b", "order_desc_b", "legendOrientation", "legendType",
    "compare_lag", "compare_suffix", "subtitle", "color_picker", "page_length", "show_totals",
    "include_search", "rowSubTotals", "transposePivot", "show_values", "show_percentage",
    "number_format", "show_total", "labels_outside", "markerEnabled", "area",
}


def _spec(*charts):
    return load_spec({
        "spec_version": "1",
        "dashboard": {"title": "T", "slug": "sdc-t"},
        "charts": list(charts),
        "layout": {"rows": [[c["name"]] for c in charts]},
    })


def _params(spec) -> dict[str, dict]:
    zf = zipfile.ZipFile(io.BytesIO(compile_bundle(spec, stub_resolution(spec))))
    out = {}
    for n in zf.namelist():
        if "/charts/" in n:
            cy = yaml.safe_load(zf.read(n))
            out[cy["slice_name"]] = cy["params"]
    return out


def _lookup(res):
    by_uuid = {ds.uuid: {"database": ds.database_name, "schema": ds.schema, "table": ds.table}
               for ds in res.datasets.values()}
    return lambda u: by_uuid.get(u)


def _decompile(spec, edit=None):
    res = stub_resolution(spec)
    bundle = compile_bundle(spec, res)
    if edit is not None:
        bundle = edit_bundle(bundle, edit)
    return decompile_bundle(bundle, _lookup(res))


def _chart(name: str) -> dict:
    return next(c for c in DISPLAY["charts"] if c["name"] == name)


@functools.cache
def _display_params() -> dict[str, dict]:
    return _params(load_spec(DISPLAY))


# -- compile -----------------------------------------------------------------------


@pytest.mark.parametrize("chart, expected", [
    ("Sales by Line", {
        "x_axis_title": "Month", "x_axis_title_margin": 50,  # rotated labels hang lower
        "y_axis_title": "Revenue (USD)", "y_axis_title_margin": 15, "y_axis_title_position": "Top",
        "y_axis_bounds": [0, 1], "truncateYAxis": True,
        "show_value": True, "stack": "Stream", "only_total": False, "contributionMode": "row",
        "limit": 5, "order_desc": False, "markerEnabled": True, "markerSize": 4,
        "area": True, "opacity": 0.5, "show_legend": True, "legendOrientation": "bottom",
        "legendType": "plain",
    }),
    ("Monthly Orders", {"stack": "Stack", "show_value": True, "logAxis": True,
                        "y_axis_bounds": [1, None]}),
    ("Quantity Area", {"stack": "Expand", "markerEnabled": True, "markerSize": 3,
                       "opacity": 0.7, "show_legend": False}),
    ("Price vs Time", {"markerSize": 10, "y_axis_bounds": [None, 200]}),
    ("Orders by Hour", {"x_axis_sort": "name", "x_axis_sort_asc": True,  # several series
                        "x_axis_sort_series": "name", "x_axis_sort_series_ascending": True,
                        "stack": "Stack",
                        "contributionMode": "column", "limit": 3, "legendOrientation": "right",
                        "time_range": "Last year", "xAxisLabelInterval": "0"}),
    ("Share by Line", {"label_type": "value_percent", "number_format": ",.0f",
                       "show_total": True, "labels_outside": False,
                       "legendOrientation": "left", "legendType": "plain"}),
    ("Order Funnel", {"label_type": 5, "number_format": ",d", "legendOrientation": "bottom"}),
    ("Sales Treemap", {"label_type": "value", "number_format": "$,.0f"}),
    ("Top Customers", {"page_length": 20, "show_totals": True, "include_search": True}),
    ("Sales Pivot", {"aggregateFunction": "Average", "rowOrder": "value_z_to_a",
                     "colOrder": "key_z_to_a", "rowSubTotals": True, "transposePivot": True,
                     "metricsLayout": "ROWS"}),
    ("Line by Deal Size", {"show_values": True, "linear_color_scheme": "schemeBlues",
                           "y_axis_format": ",.0f", "show_percentage": False,
                           "normalize_across": "y", "show_legend": False}),
    ("Price Spread", {"x_axis_title": "Unit price", "y_axis_title": "Orders"}),
    ("Revenue KPI", {"compare_lag": 1, "compare_suffix": "vs last month",
                     "subtitle": "Booked revenue", "time_range": "Last year",
                     "color_picker": {"r": 0, "g": 87, "b": 184, "a": 1}}),
    ("Orders This Quarter", {"time_range": "Last quarter"}),
    ("Average Price", {"y_axis_format": "$,.2f", "conditional_formatting": [
        {"column": "Avg Price", "colorScheme": "#B3261E", "operator": "<", "targetValue": 80.0,
         "useGradient": False},
        {"column": "Avg Price", "colorScheme": "#B26B00", "operator": "< x <",
         "targetValueLeft": 80.0, "targetValueRight": 90.0, "useGradient": False},
        {"column": "Avg Price", "colorScheme": "#1B7F3B", "operator": ">", "targetValue": 90.0,
         "useGradient": False},
    ]}),
    ("Revenue and Orders", {
        "show_value": True, "stack": True, "only_total": False, "limit": 2, "order_desc": False,
        "show_valueB": True, "limit_b": 3, "x_axis_title": "Month", "y_axis_title": "Revenue",
        "yAxisTitleSecondary": "Orders", "y_axis_bounds": [0, None],
        "y_axis_bounds_secondary": [None, 500], "logAxisSecondary": True, "truncateYAxis": True,
        "legendOrientation": "bottom", "time_range": "Last year",
    }),
])
def test_each_field_compiles_to_its_superset_param(chart, expected):
    p = _display_params()[chart]
    assert {k: p.get(k) for k in expected} == expected


def test_series_limit_metric_and_table_column_config():
    p = _display_params()["Sales by Line"]
    assert p["timeseries_limit_metric"]["label"] == "SUM(quantity_ordered)"
    assert _display_params()["Revenue and Orders"]["timeseries_limit_metric"]["label"] == "COUNT(*)"
    assert _display_params()["Top Customers"]["column_config"] == {
        "SUM(sales)": {"d3NumberFormat": "$,.0f", "horizontalAlign": "right",
                       "customColumnName": "Revenue"},
        "customer_name": {"horizontalAlign": "left", "columnWidth": 220,
                          "customColumnName": "Customer"},
    }


@pytest.mark.parametrize("fixture", ["kitchen_sink.json", "sales_overview.json"])
def test_specs_without_the_fields_emit_none_of_their_keys(fixture):
    spec = load_spec(json.loads((FIXTURES / fixture).read_text(encoding="utf-8")))
    for name, p in _params(spec).items():
        assert not NEW_KEYS & set(p), (name, NEW_KEYS & set(p))


def test_category_sort_reads_in_order_on_both_orientations():
    bar = {"name": "B", "type": "bar", "dataset": DS, "x_column": "hour", "metrics": ["COUNT(*)"]}
    cases = {("vertical", "asc"): True, ("vertical", "desc"): False,
             ("horizontal", "asc"): False, ("horizontal", "desc"): True}
    for (orientation, order), stored_asc in cases.items():
        p = _params(_spec({**bar, "orientation": orientation, "category_sort": order}))["B"]
        assert (p["x_axis_sort"], p["x_axis_sort_asc"]) == ("hour", stored_asc)
        assert "x_axis_sort_series" not in p  # one series: sortOperator sorts on the column
        # Several series: sortOperator.ts skips a groupby, so the plugin sorts by name,
        # read from x_axis_sort from 6.0.0 and x_axis_sort_series at 4.1.4 and 5.0.0.
        for extra in ({"groupby": "region"}, {"metrics": ["COUNT(*)", "SUM(x)"]}):
            p = _params(_spec({**bar, **extra, "orientation": orientation,
                               "category_sort": order}))["B"]
            assert (p["x_axis_sort"], p["x_axis_sort_asc"]) == ("name", stored_asc)
            assert (p["x_axis_sort_series"], p["x_axis_sort_series_ascending"]) == ("name", stored_asc)
    default = _params(_spec(bar))["B"]
    assert (default["x_axis_sort"], default["x_axis_sort_asc"]) == ("COUNT(*)", False)


def test_category_sort_orders_the_query_so_a_row_limit_keeps_the_first_categories():
    """The query's ORDER BY comes from normalizeOrderBy (Timeseries/buildQuery.ts:93 at
    4.1.4, 5.0.0 and 6.1.0): the "Sort query by" metric, else the first metric,
    descending. Ordered by value, a row limit kept the 5 busiest hours and the chart
    then drew them in hour order, with gaps. category_sort orders the query by the
    category itself: MIN(x) per x group is x, with or without a groupby."""
    bar = {"name": "B", "type": "bar", "dataset": DS, "x_column": "hour",
           "metrics": ["COUNT(*)"], "row_limit": 5}
    for extra in ({}, {"groupby": "region"}):
        for orientation in ("vertical", "horizontal"):
            for order, desc in (("asc", False), ("desc", True)):
                p = _params(_spec({**bar, **extra, "orientation": orientation,
                                   "category_sort": order}))["B"]
                sort_by = p["timeseries_limit_metric"]
                assert (sort_by["expressionType"], sort_by["aggregate"],
                        sort_by["column"]["column_name"]) == ("SIMPLE", "MIN", "hour")
                assert p["order_desc"] is desc
                # extractExtraMetrics adds the sort metric as a series only when its
                # label is x_axis_sort; it must stay out of the chart.
                assert sort_by["label"] != p["x_axis_sort"]
    default = _params(_spec(bar))["B"]
    assert "timeseries_limit_metric" not in default and "order_desc" not in default
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    for version in contract:
        assert check(version, contract, {"echarts_timeseries_bar": set(p)}) == [], version


def test_the_category_query_order_round_trips_without_a_loss():
    spec = _spec({"name": "B", "type": "bar", "dataset": DS, "x_column": "hour",
                  "metrics": ["COUNT(*)"], "row_limit": 5, "category_sort": "desc"})
    out = _decompile(spec)
    assert out.losses == []
    assert out.spec["charts"][0]["category_sort"] == "desc"
    assert "series_limit_metric" not in out.spec["charts"][0]


def test_category_sort_with_a_series_limit_keeps_the_series_ranking():
    """One "Sort query by" control (timeseries_limit_metric) ranks the series for the
    series limit and orders the query (buildQueryObject.ts, normalizeOrderBy.ts), so with
    series_limit it stays the series ranking (data.top-n-sort warns on a row_limit)."""
    bar = {"name": "B", "type": "bar", "dataset": DS, "x_column": "hour", "metrics": ["COUNT(*)"],
           "groupby": "region", "series_limit": 3, "series_limit_metric": "SUM(x)",
           "category_sort": "asc"}
    p = _params(_spec(bar))["B"]
    assert p["timeseries_limit_metric"]["column"]["column_name"] == "x" and "order_desc" not in p


def test_a_grouped_bar_sorted_by_name_on_4_1_decompiles_as_category_sort():
    """A 4.1.4 or 5.0.0 bar with several series stores its sort in x_axis_sort_series."""
    spec = _spec({"name": "B", "type": "bar", "dataset": DS, "x_column": "hour",
                  "metrics": ["COUNT(*)"], "groupby": "region"})
    edit = _edit_params("B", x_axis_sort=None, x_axis_sort_series="name",
                        x_axis_sort_series_ascending=False)
    assert _decompile(spec, edit).spec["charts"][0]["category_sort"] == "desc"
    edit = _edit_params("B", x_axis_sort=None, x_axis_sort_series="sum")
    assert "category_sort" not in _decompile(spec, edit).spec["charts"][0]


def test_y_axis_max_is_a_bound_on_every_release_not_echart_options():
    line = {"name": "L", "type": "timeseries_line", "dataset": DS, "metrics": ["MAX(share)"],
            "time_column": "ts", "y_axis_max": 1}
    p = _params(_spec(line))["L"]
    assert p["y_axis_bounds"] == [None, 1] and "echart_options" not in p
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    for version in contract:
        assert check(version, contract, {"echarts_timeseries_line": set(p)}) == [], version


def test_heatmap_scheme_and_defaults_are_unchanged_when_omitted():
    hm = {"name": "H", "type": "heatmap", "dataset": DS, "x_column": "a", "y_column": "b",
          "metric": "COUNT(*)"}
    p = _params(_spec(hm))["H"]
    assert p["linear_color_scheme"] == "superset_seq_1" and p["normalize_across"] == "heatmap"
    assert "show_percentage" not in p and "show_values" not in p


# -- version gating ------------------------------------------------------------------


def test_later_release_keys_are_excused_only_where_they_were_added():
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert check("4.1.4", contract, {"big_number": {"subtitle"}}) == []
    assert check("4.1.4", contract, {"mixed_timeseries": {"only_total", "only_totalB"}}) == []
    assert check("4.1.4", contract, {"big_number_total": {"subtitle"}}) != []
    assert check("4.1.4", contract, {"pie": {"only_totalB"}}) != []
    assert check("6.1.0", contract, {"mixed_timeseries": {"only_total", "only_totalB"}}) == []
    # and the reverse: the series sort keys 6.0.0 dropped, written for 4.1.4 and 5.0.0
    for version in contract:
        assert check(version, contract, {"echarts_timeseries_bar": {"x_axis_sort_series"}}) == []
    assert check("6.1.0", contract, {"echarts_timeseries_line": {"x_axis_sort_series"}}) != []


def test_the_mixed_contract_is_the_whole_panel():
    """Re-extracted from MixedTimeseries/controlPanel.tsx, it lists the controls the
    tool does not write as well, so a missing one shows up in the drift check."""
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    for version, viz in contract.items():
        keys = set(viz["mixed_timeseries"])
        assert {"y_axis_bounds_secondary", "logAxisSecondary", "yAxisTitleSecondary",
                "stackB", "show_valueB", "limit_b", "x_axis_title", "legendOrientation"} <= keys
        assert ("only_totalB" in keys) == (version == "6.1.0")


# -- decompile --------------------------------------------------------------------


def test_every_field_round_trips_losslessly():
    spec = load_spec(DISPLAY)
    out = _decompile(spec)
    assert out.losses == [], out.losses_json()
    assert _normalize(load_spec(out.spec)) == _normalize(spec)
    # not vacuous: the decompiled spec carries the fields themselves
    back = {c["name"]: c for c in out.spec["charts"]}
    for chart in DISPLAY["charts"]:
        for key, value in chart.items():
            if key in ("dataset", "x_label_every", "x_label_rotation", "a", "b"):
                continue
            if key == "show_legend" and value is True:
                continue  # the default reads back as omitted
            assert back[chart["name"]].get(key) == value, (chart["name"], key)
    for key in ("a", "b"):
        assert back["Revenue and Orders"][key] == _chart("Revenue and Orders")[key]


def _edit_params(name, **changes):
    def edit(path, doc):
        if "/charts/" in path and doc.get("slice_name") == name:
            doc["params"].update(changes)
    return edit


@pytest.mark.parametrize("chart, stored, spec_fields", [
    ("Sales by Line", {"stack": "Stack"}, {"stack": True}),
    ("Sales by Line", {"contributionMode": "column"}, {"contribution": "series"}),
    ("Sales by Line", {"legendOrientation": "top", "legendType": "scroll"},
     {"legend_position": None, "legend_type": None}),
    ("Sales by Line", {"y_axis_bounds": [None, None], "truncateYAxis": False},
     {"y_axis_min": None, "y_axis_max": None, "y_axis_truncate": None}),
    ("Sales by Line", {"markerSize": 6, "opacity": 0.2}, {"marker_size": None, "opacity": None}),
    ("Order Funnel", {"label_type": 0}, {"label_type": None}),
    ("Order Funnel", {"label_type": 6}, {"label_type": "value_percent"}),
    ("Sales Pivot", {"rowOrder": "key_a_to_z", "aggregateFunction": "Sum"},
     {"row_order": None, "aggregate_function": None}),
    ("Revenue KPI", {"color_picker": {"r": 0, "g": 122, "b": 135, "a": 1}}, {"trend_color": None}),
    ("Revenue KPI", {"color_picker": {"r": 0x1B, "g": 0x7F, "b": 0x3B, "a": 1}}, {"trend_color": "green"}),
    ("Revenue KPI", {"compare_lag": "2"}, {"compare_lag": 2}),
    ("Line by Deal Size", {"linear_color_scheme": "superset_seq_1"}, {"color_scheme": None}),
    ("Top Customers", {"page_length": None}, {"page_length": None}),
    ("Orders by Hour", {"x_axis_sort": "COUNT(*)"}, {"category_sort": None}),
    ("Orders This Quarter", {"time_range": "No filter"}, {"time_range": None}),
])
def test_superset_values_map_to_spec_values_and_defaults_read_as_omitted(chart, stored, spec_fields):
    out = _decompile(load_spec(DISPLAY), _edit_params(chart, **stored))
    back = next(c for c in out.spec["charts"] if c["name"] == chart)
    assert {k: back.get(k) for k in spec_fields} == spec_fields
    load_spec(out.spec)  # still a valid spec


def test_a_hidden_legend_drops_its_placement_and_an_inert_only_total_is_dropped():
    edit = _edit_params("Sales by Line", show_legend=False, show_value=False)
    back = next(c for c in _decompile(load_spec(DISPLAY), edit).spec["charts"]
                if c["name"] == "Sales by Line")
    assert back["show_legend"] is False
    assert "legend_position" not in back and "legend_type" not in back
    assert "only_total" not in back and "show_value" not in back


def test_the_old_echart_options_y_max_still_decompiles():
    """Bundles built before y_axis_max moved to y_axis_bounds carry it in echart_options."""
    spec = _spec({"name": "L", "type": "timeseries_line", "dataset": DS, "metrics": ["MAX(s)"],
                  "time_column": "ts"})
    out = _decompile(spec, _edit_params("L", echart_options=json.dumps({"yAxis": {"max": 1}})))
    assert out.losses == [] and out.spec["charts"][0]["y_axis_max"] == 1


def test_unrepresentable_values_are_named_losses():
    edit = _edit_params("Monthly Orders", stack="Stream")
    out = _decompile(load_spec(DISPLAY), edit)
    assert any("stack 'Stream'" in loss.what for loss in out.losses)
    edit = _edit_params("Share by Line", label_type="template")
    assert any("label_type 'template'" in loss.what
               for loss in _decompile(load_spec(DISPLAY), edit).losses)


# -- plan ----------------------------------------------------------------------------


def _plan(spec, edit, monkeypatch):
    live = _decompile(spec, edit)
    monkeypatch.setattr(resolver, "resolve", lambda s, c, *_: stub_resolution(s))
    monkeypatch.setattr(dashdiff, "decompile_live", lambda slug, c: live)

    class Client:
        def find_dashboard_by_slug(self, slug):
            return {"id": 3, "uuid": str(ids.dashboard_uuid(slug))}

    return json.loads(plan(spec, Client()).to_json())


def test_plan_is_clean_when_nothing_changed(monkeypatch):
    out = _plan(load_spec(DISPLAY), None, monkeypatch)
    assert out["clean"] is True, out


@pytest.mark.parametrize("chart, change", [
    ("Sales by Line", {"legendOrientation": "top"}),
    ("Sales by Line", {"y_axis_bounds": [0, 2]}),
    ("Sales by Line", {"y_axis_title": "Revenue"}),
    ("Monthly Orders", {"stack": None}),
    ("Orders by Hour", {"x_axis_sort_asc": False}),
    ("Share by Line", {"label_type": "key"}),
    ("Top Customers", {"page_length": 50}),
    ("Top Customers", {"column_config": {}}),
    ("Sales Pivot", {"transposePivot": False}),
    ("Line by Deal Size", {"show_percentage": True}),
    ("Revenue KPI", {"compare_lag": 3}),
    ("Orders This Quarter", {"time_range": "Last month"}),
    ("Average Price", {"conditional_formatting": []}),
    ("Average Price", {"conditional_formatting": [
        {"column": "Avg Price", "colorScheme": "#B3261E", "operator": "<", "targetValue": 75}]}),
    ("Revenue and Orders", {"limit_b": 9}),
])
def test_plan_reports_a_display_change_made_in_the_ui(chart, change, monkeypatch):
    out = _plan(load_spec(DISPLAY), _edit_params(chart, **change), monkeypatch)
    assert out["charts_changed"] == [chart], out


# -- validation ------------------------------------------------------------------------


LINE = {"name": "L", "type": "timeseries_line", "dataset": DS, "metrics": ["SUM(x)"],
        "time_column": "ts"}


@pytest.mark.parametrize("chart, message", [
    ({**LINE, "type": "timeseries_bar", "stack": "stream"}, 'stack "stream" is not available on timeseries_bar'),
    ({**LINE, "stack": "expand"}, 'stack "expand" is not available on timeseries_line'),
    ({"name": "B", "type": "bar", "dataset": DS, "x_column": "c", "metrics": ["SUM(x)"],
      "stack": "expand"}, 'stack "expand" is not available on bar'),
    ({**LINE, "only_total": False}, "only_total applies to show_value on a stacked chart"),
    ({**LINE, "series_limit": 5}, "series_limit needs groupby"),
    ({**LINE, "groupby": "g", "series_limit_metric": "SUM(y)"}, "need series_limit"),
    ({**LINE, "y_axis_min": 5, "y_axis_max": 1}, "y_axis_min (5) must be below y_axis_max (1)"),
    ({**LINE, "y_axis_log": True, "y_axis_min": 0}, "a logarithmic axis starts above zero"),
    ({**LINE, "show_legend": False, "legend_position": "left"}, "need show_legend"),
    ({**LINE, "marker_size": 4}, "marker_size needs markers"),
    ({**LINE, "opacity": 0.5}, "opacity is the area fill's; it needs area"),
    ({"name": "F", "type": "funnel", "dataset": DS, "metric": "COUNT(*)", "groupby": "s",
      "legend_type": "plain"}, "funnel has no legend_type"),
    ({"name": "K", "type": "big_number_trend", "dataset": DS, "metric": "COUNT(*)",
      "time_column": "ts", "compare_suffix": "vs last week"}, "compare_suffix needs compare_lag"),
    ({"name": "K", "type": "big_number_trend", "dataset": DS, "metric": "COUNT(*)",
      "time_column": "ts", "trend_color": "teal"}, "trend_color must be green, amber, red or #RRGGBB, got 'teal'"),
    ({"name": "T", "type": "table", "dataset": DS, "columns": ["a"], "show_totals": True},
     "show_totals needs aggregate mode"),
    ({"name": "T", "type": "table", "dataset": DS, "columns": ["a"], "column_headers": {"b": "B"}},
     "column_headers 'b' is not one of the table's labels"),
    ({"name": "H", "type": "heatmap", "dataset": DS, "x_column": "a", "y_column": "b",
      "metric": "COUNT(*)", "color_scheme": "rainbow"}, "'schemeBlues'"),
    ({"name": "P", "type": "pivot_table", "dataset": DS, "rows": ["a"], "metrics": ["COUNT(*)"],
      "aggregate_function": "Mode"}, "'Sum as Fraction of Total'"),
    ({"name": "M", "type": "mixed", "dataset": DS, "x_column": "ts",
      "a": {"metrics": ["SUM(x)"], "series_limit": 2}, "b": {"metrics": ["SUM(y)"]}},
     "series_limit needs groupby"),
    ({"name": "M", "type": "mixed", "dataset": DS, "x_column": "ts", "y_axis_min_secondary": 3,
      "y_axis_max_secondary": 2, "a": {"metrics": ["SUM(x)"]}, "b": {"metrics": ["SUM(y)"]}},
     "y_axis_min_secondary (3) must be below y_axis_max_secondary (2)"),
])
def test_wrong_combinations_are_named(chart, message):
    with pytest.raises(ValidationError) as err:
        _spec(chart)
    assert message in str(err.value)


def test_explicit_defaults_are_kept_as_written_and_normalize_to_omitted():
    charts = ({**LINE, "legend_position": "top", "legend_type": "scroll"},
              {"name": "P", "type": "pie", "dataset": DS, "metric": "COUNT(*)", "groupby": "g",
               "label_type": "key_percent"},
              {"name": "K", "type": "big_number_trend", "dataset": DS, "metric": "COUNT(*)",
               "time_column": "ts", "trend_color": "#007a87"})
    spec = _spec(*charts)
    line, pie, trend = spec.charts
    assert (line.legend_position, line.legend_type, pie.label_type, trend.trend_color) == (
        "top", "scroll", "key_percent", "#007A87")
    keys = ("legend_position", "legend_type", "label_type", "trend_color")
    bare = [{k: v for k, v in c.items() if k not in keys} for c in charts]
    assert _normalize(spec) == _normalize(_spec(*bare))


# -- resolution ----------------------------------------------------------------------


def test_series_limit_metric_is_resolved_like_any_metric():
    ds = ResolvedDataset(id=1, uuid="u", table="t", schema=None, database_name="examples",
                         columns=["ts", "g", "x"], metrics=["revenue"])
    for chart in (
        {**LINE, "groupby": "g", "series_limit": 3, "series_limit_metric": "no_such_metric"},
        {"name": "M", "type": "mixed", "dataset": DS, "x_column": "ts",
         "a": {"metrics": ["SUM(x)"], "groupby": "g", "series_limit": 2,
               "series_limit_metric": "no_such_metric"}, "b": {"metrics": ["SUM(x)"]}},
    ):
        res = Resolution()
        _check_chart_fields(_spec(chart).charts[0], ds, res)
        assert [(e.code, e.ref) for e in res.errors] == [("metric_not_found", "no_such_metric")]


# -- a written Superset default --------------------------------------------------------

PIE = {"name": "P", "type": "pie", "dataset": DS, "metric": "COUNT(*)", "groupby": "g"}
EXPLICIT_DEFAULTS = [
    (LINE, "legend_position", "top"),
    (LINE, "legend_type", "scroll"),
    ({**LINE, "markers": True}, "marker_size", 6),
    ({**LINE, "area": True}, "opacity", 0.2),
    ({**LINE, "type": "timeseries_area"}, "opacity", 0.2),
    ({**LINE, "type": "timeseries_scatter"}, "marker_size", 6),
    (PIE, "label_type", "key_percent"),
    ({**PIE, "type": "funnel"}, "label_type", "key"),
    ({**PIE, "type": "treemap", "groupby": ["g"]}, "label_type", "key_value"),
    ({"name": "H", "type": "heatmap", "dataset": DS, "x_column": "a", "y_column": "b",
      "metric": "COUNT(*)"}, "color_scheme", "superset_seq_1"),
    ({"name": "K", "type": "big_number_trend", "dataset": DS, "metric": "COUNT(*)",
      "time_column": "ts"}, "trend_color", "#007A87"),
]


@pytest.mark.parametrize("chart, field, value", EXPLICIT_DEFAULTS,
                         ids=[f"{c['type']}.{f}" for c, f, _ in EXPLICIT_DEFAULTS])
def test_a_written_superset_default_is_kept_compiles_as_omitted_and_plans_clean(
        chart, field, value, monkeypatch):
    """Writing Superset's own default (legend at the top, a pie's key_percent labels)
    used to validate to None, so the author's choice vanished from the model. It is
    kept as written; Superset draws it exactly as the omitted field, so the bundle is
    the same bytes, and plan reads the two as equal (decompile can't tell them apart)."""
    written, omitted = _spec({**chart, field: value}), _spec(chart)
    c = written.charts[0]
    assert getattr(c, field) == value and field in c.model_fields_set
    assert compile_bundle(written, stub_resolution(written)) == \
        compile_bundle(omitted, stub_resolution(omitted))
    assert _normalize(written) == _normalize(omitted)
    assert _plan(written, None, monkeypatch)["clean"] is True


@pytest.mark.parametrize("field, value", [("refresh_frequency", 0),
                                          ("filter_bar_orientation", "vertical")])
def test_a_written_dashboard_default_is_kept_compiles_as_omitted_and_plans_clean(
        field, value, monkeypatch):
    def spec(**dash):
        return load_spec({"spec_version": "1", "dashboard": {"title": "T", "slug": "sdc-t", **dash},
                          "charts": [LINE], "layout": {"rows": [["L"]]}})
    written, omitted = spec(**{field: value}), spec()
    assert getattr(written.dashboard, field) == value
    assert compile_bundle(written, stub_resolution(written)) == \
        compile_bundle(omitted, stub_resolution(omitted))
    assert _normalize(written) == _normalize(omitted)
    assert _plan(written, None, monkeypatch)["clean"] is True


def test_a_written_default_passes_the_checks_an_omitted_field_passes():
    """The checks read a written default as unset, so nothing that validated before
    is refused now."""
    _spec({**LINE, "show_legend": False, "legend_position": "top", "legend_type": "scroll"})
    _spec({**LINE, "marker_size": 6, "opacity": 0.2})
    _spec({**LINE, "type": "timeseries_area", "marker_size": 6})
    _spec({**PIE, "type": "funnel", "legend_type": "scroll"})
