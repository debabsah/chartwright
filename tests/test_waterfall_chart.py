"""Waterfall (Superset's `waterfall` plugin): steps that add up to a running total.

Key names follow Waterfall/controlPanel.tsx at 4.1.4, 5.0.0 and 6.1.0 (the labels and
show_total are 6.1.0's). A bridge in its own order (steps + closing) is drawn by an x
axis the compiler writes, a CASE giving each step a key, with the step column as the
breakdown and show_total off (compiler.steps_order_sql): seen rendering on 6.1.0 in
order, its closing grey under its own name; 4.1.4 drew a running total after every
step, so the field is refused before 6.1.0."""

import io
import json
import sys
import zipfile
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from chartwright.compiler import compile_bundle, parse_steps_order_sql, steps_order_sql
from chartwright.dashdiff import _normalize
from chartwright.decompile import decompile_bundle
from chartwright.design import advise, advise_and_fix
from chartwright.design.presets import Overlay
from chartwright.resolver import Resolution, _check_chart_fields
from chartwright.smoke import _bridge_warning, _waterfall_query
from chartwright.spec import load_spec
from chartwright.testing import edit_bundle, stub_resolution
from chartwright.versions import check_spec_version

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
from params_drift import CONTRACT, check, emitted_keys_by_viz_type  # noqa: E402

DS = {"database": "examples", "table": "t"}
EMPTY = Overlay()
PLAIN = {"name": "Sales by Status", "type": "waterfall", "dataset": DS,
         "x_column": "status", "metric": "SUM(sales)"}
BRIDGE = {
    "name": "Energy bridge", "type": "waterfall", "dataset": DS,
    "x_column": "step", "metric": "SUM(mwh)",
    "steps": ["FY2025", "Solar", "Demand", "Wind", "Hydro"], "closing": "FY2026",
    "increase_color": "green", "decrease_color": "red", "total_color": "#5B6770",
    "show_value": True, "number_format": ",.0f", "y_axis_title": "MWh",
}


def _spec(*charts, **dashboard):
    return load_spec({
        "spec_version": "1",
        "dashboard": {"title": "T", "slug": "sdc-t", **dashboard},
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


def test_a_plain_waterfall_writes_the_query_and_nothing_unset():
    viz, p = _params(_spec(PLAIN))["Sales by Status"]
    assert viz == "waterfall"
    assert p["x_axis"] == "status" and p["groupby"] == [] and p["row_limit"] == 10000
    assert p["metric"]["label"] == "SUM(sales)"
    # Superset's own defaults draw an unset field: nothing else is written.
    assert set(p) == {"datasource", "viz_type", "time_range", "adhoc_filters", "x_axis",
                      "groupby", "metric", "row_limit"}


def test_every_display_field_has_its_control():
    chart = {**PLAIN, "increase_color": "green", "decrease_color": "#C0392B",
             "total_color": "amber", "increase_label": "Gain", "decrease_label": "Loss",
             "total_label": "All orders", "show_value": True, "show_legend": True,
             "number_format": ",.0f", "x_axis_title": "Status", "y_axis_title": "Sales",
             "x_label_rotation": 90, "x_label_format": "%Y", "row_limit": 20,
             "groupby": "deal_size", "time_grain": "P1Y"}
    _, p = _params(_spec(chart))["Sales by Status"]
    # The colour pickers store {r, g, b, a}; a name paints its text shade.
    assert p["increase_color"] == {"r": 0x1B, "g": 0x7F, "b": 0x3B, "a": 1}
    assert p["decrease_color"] == {"r": 0xC0, "g": 0x39, "b": 0x2B, "a": 1}
    assert p["total_color"] == {"r": 0x8A, "g": 0x61, "b": 0x00, "a": 1}
    assert (p["increase_label"], p["decrease_label"], p["total_label"]) == ("Gain", "Loss", "All orders")
    assert p["show_value"] is True and p["show_legend"] is True
    assert (p["y_axis_format"], p["x_axis_label"], p["y_axis_label"]) == (",.0f", "Status", "Sales")
    assert (p["x_ticks_layout"], p["x_axis_time_format"]) == ("90°", "%Y")
    assert p["groupby"] == ["deal_size"] and p["time_grain_sqla"] == "P1Y" and p["row_limit"] == 20


def test_a_bridge_orders_its_steps_by_a_written_x_axis():
    viz, p = _params(_spec(BRIDGE))["Energy bridge"]
    assert p["x_axis"] == {
        "expressionType": "SQL", "label": "Bridge order",
        "sqlExpression": "CASE step WHEN 'FY2025' THEN '0000' WHEN 'Solar' THEN '0001' "
                         "WHEN 'Demand' THEN '0002' WHEN 'Wind' THEN '0003' WHEN 'Hydro' THEN "
                         "'0004' WHEN 'FY2026' THEN 'FY2026' ELSE '0zzz' END"}
    # The step column labels every bar; the closing row is the total, under its own name.
    assert p["groupby"] == ["step"] and p["total_label"] == "FY2026" and p["show_total"] is False
    assert "time_grain_sqla" not in p


def test_the_order_sql_quotes_what_it_must_and_reads_back():
    sql = steps_order_sql("Step Name", ["O'Hare", "b"], "Z end")
    assert sql == ("CASE \"Step Name\" WHEN 'O''Hare' THEN '0000' WHEN 'b' THEN '0001' "
                   "WHEN 'Z end' THEN 'Z end' ELSE '0zzz' END")
    assert parse_steps_order_sql(sql) == ("Step Name", ["O'Hare", "b"], "Z end")
    for other in ("CASE step WHEN 'a' THEN 'x' ELSE '0zzz' END", "step", "",
                  "CASE step WHEN 'a' THEN '0001' WHEN 'Z' THEN 'Z' ELSE '0zzz' END"):
        assert parse_steps_order_sql(other) is None, other


def test_a_written_stock_colour_builds_like_an_omitted_one():
    stock = {**PLAIN, "increase_color": "#5ac189", "decrease_color": "#E04355",
             "total_color": "#666666"}
    assert _params(_spec(stock)) == _params(_spec(PLAIN))


def test_a_time_axis_takes_the_time_range_and_a_category_the_dataset_time():
    by_month = {**PLAIN, "name": "Monthly", "x_column": "ts", "time_grain": "P1M",
                "time_range": "Last year"}
    spec = _spec(PLAIN, by_month, {**BRIDGE, "name": "Bridge", "time_range": "Last year"})
    res = stub_resolution(spec)
    for ds in res.datasets.values():
        ds.main_dttm_col = "ts"
        ds.temporal_columns = ["ts"]
    params = _params(spec, res)
    month = params["Monthly"][1]
    assert month["adhoc_filters"][-1]["operator"] == "TEMPORAL_RANGE"
    assert month["adhoc_filters"][-1]["subject"] == "ts" and "granularity_sqla" not in month
    for name in ("Sales by Status", "Bridge"):
        assert params[name][1]["granularity_sqla"] == "ts", name


# -- the spec --------------------------------------------------------------------------


@pytest.mark.parametrize("change, message", [
    ({"closing": None}, "steps needs closing"),
    ({"steps": None}, "closing needs steps"),
    ({"closing": "Solar"}, "is also a step"),
    ({"steps": ["FY2025", "Solar", "Solar"]}, "more than once"),
    ({"steps": ["FY2025", ""]}, "non-empty values"),
    ({"closing": "0 balance"}, "must start with a letter or a digit 1-9"),
    ({"closing": "(FY2026)"}, "must start with a letter or a digit 1-9"),
    ({"total_label": "End"}, "total_label does not go with steps"),
    ({"time_grain": "P1M"}, "time_grain does not go with steps"),
    ({"groupby": "region"}, "groupby does not go with steps"),
    ({"steps": ["FY2025"]}, "at least 2"),
    ({"increase_color": "teal"}, "increase_color must be green, amber, red or #RRGGBB"),
    ({"x_label_rotation": 30}, "x_label_rotation"),
])
def test_a_bridge_is_validated(change, message):
    chart = {k: v for k, v in {**BRIDGE, **change}.items() if v is not None}
    with pytest.raises(ValidationError, match=message):
        _spec(chart)


def test_closing_names_in_any_script_and_years_are_fine():
    for closing in ("FY2026", "2026", "Été 2026", "This year"):
        assert _spec({**BRIDGE, "closing": closing}).charts[0].closing == closing


# -- decompile ---------------------------------------------------------------------------


@pytest.mark.parametrize("chart", [PLAIN, BRIDGE, {
    **PLAIN, "increase_color": "#2E7D32", "decrease_color": "amber", "total_color": "red",
    "increase_label": "Gain", "decrease_label": "Loss", "total_label": "All",
    "show_value": True, "show_legend": True, "number_format": ".3s", "x_axis_title": "X",
    "y_axis_title": "Y", "x_label_rotation": 0, "x_label_format": "%b", "row_limit": 9,
    "groupby": "deal_size", "time_grain": "P1M", "time_range": "Last year"}])
def test_decompile_round_trips_both_forms(chart):
    spec = _spec(chart)
    bundle = compile_bundle(spec, stub_resolution(spec))
    result = decompile_bundle(bundle, _lookup(spec))
    assert result.losses == [], result.losses_json()
    assert _normalize(load_spec(result.spec)) == _normalize(spec)


def _decompiled_after(edit_params, chart=PLAIN):
    spec = _spec(chart)

    def edit(path, doc):
        if "/charts/" in path:
            edit_params(doc["params"])

    bundle = edit_bundle(compile_bundle(spec, stub_resolution(spec)), edit)
    return decompile_bundle(bundle, _lookup(spec))


def test_a_chart_built_in_the_ui_reads_back_its_choices():
    def ui(p):
        p.update({"show_total": True, "x_ticks_layout": "staggered", "show_legend": False,
                  "increase_color": {"r": 90, "g": 193, "b": 137, "a": 1},
                  "currency_format": {}, "x_axis_time_format": "smart_date",
                  "y_axis_format": "SMART_NUMBER"})

    result = _decompiled_after(ui)
    assert result.losses == [], result.losses_json()
    chart = result.spec["charts"][0]
    # Stock values read as unset; "staggered" is drawn at 45 degrees like "45°".
    assert chart["x_label_rotation"] == 45
    assert not {"increase_color", "show_legend", "number_format", "x_label_format"} & set(chart)


def test_a_waterfall_saved_in_superset_reads_back_whole():
    """The featured-charts export (6.1.0) holds a waterfall made in Explore: every key it
    stores is a spec field or Superset's own default."""
    blob = (Path(__file__).parent / "fixtures" / "featured_charts_export.zip").read_bytes()
    result = decompile_bundle(blob, lambda u: {"database": "examples", "schema": None, "table": "t"})
    chart = next(c for c in result.spec["charts"] if c["type"] == "waterfall")
    assert not [loss for loss in result.losses if loss.where == chart["name"]]
    assert {k: chart[k] for k in ("x_column", "time_grain", "metric", "show_value")} == {
        "x_column": "order_date", "time_grain": "P3M", "metric": "SUM(sales)", "show_value": True}
    assert not {"increase_color", "number_format", "x_label_format", "x_label_rotation"} & set(chart)


def test_a_changed_setting_the_spec_cant_hold_is_named():
    result = _decompiled_after(lambda p: p.update({"show_total": False}))
    assert any("show_total=False" in loss.what for loss in result.losses), result.losses_json()


def test_a_bridge_edited_outside_the_spec_is_skipped_by_name():
    result = _decompiled_after(lambda p: p.update({"groupby": ["region"]}), BRIDGE)
    assert result.spec["charts"] == [] and result.skipped_charts == ["Energy bridge"]
    assert any("bridge order changed" in loss.what for loss in result.losses)
    result = _decompiled_after(lambda p: p.update(
        {"x_axis": {"expressionType": "SQL", "label": "x", "sqlExpression": "lower(step)"}}))
    assert any("SQL the spec can't express" in loss.what for loss in result.losses)


# -- releases ----------------------------------------------------------------------------


def test_a_bridge_is_refused_before_6_1_and_its_labels_warn():
    spec = _spec(BRIDGE, {**PLAIN, "total_label": "All", "increase_label": "Up",
                          "decrease_label": "Down"})
    for version in ("4.1.4", "5.0.0", "6.0.0"):
        out = check_spec_version(spec, version)
        assert [(e["code"], e["chart"], e["ref"]) for e in out.errors] == [
            ("superset_version_too_old", "Energy bridge", "steps")], version
        assert sorted(w["field"] for w in out.warnings) == [
            "decrease_label", "increase_label", "total_label"], version
    assert check_spec_version(spec, "6.1.0").errors == []
    assert check_spec_version(spec, "6.1.0").warnings == []
    assert check_spec_version(_spec(PLAIN), "4.1.4").errors == []


def test_the_contract_declares_every_key_at_its_release():
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    emitted = emitted_keys_by_viz_type()
    assert {"show_total", "total_label", "increase_label", "decrease_label"} <= emitted["waterfall"]
    for version in ("4.1.4", "5.0.0", "6.1.0"):
        assert check(version, contract, {"waterfall": emitted["waterfall"]}) == []
    assert "show_total" in contract["6.1.0"]["waterfall"]
    assert "show_total" not in contract["5.0.0"]["waterfall"]


# -- resolve, smoke ---------------------------------------------------------------------


def test_resolve_checks_the_metric_and_both_columns():
    spec = _spec({**PLAIN, "metric": "SUM(nope)", "groupby": "missing"})
    ds = stub_resolution(spec).for_chart(spec.charts[0].dataset)
    ds.columns = ["status", "sales"]
    res = Resolution()
    _check_chart_fields(spec.charts[0], ds, res)
    assert sorted(e.ref for e in res.errors) == ["missing", "nope"]


def test_smoke_queries_a_bridge_by_its_step_column():
    spec = _spec(BRIDGE)
    q = _waterfall_query(spec.charts[0], spec, stub_resolution(spec).for_chart(spec.charts[0].dataset))
    assert q["columns"] == ["step"] and [m["label"] for m in q["metrics"]] == ["SUM(mwh)"]


def _rows(**values):
    return [{"data": [{"step": k, "SUM(mwh)": v} for k, v in values.items()]}]


def test_smoke_says_when_a_bridge_doesnt_reconcile():
    chart = _spec(BRIDGE).charts[0]
    good = _rows(FY2025=1000, Solar=120, Demand=80, Wind=-150, Hydro=-30, FY2026=1020)
    assert _bridge_warning(chart, good) is None
    off = _bridge_warning(chart, _rows(FY2025=1000, Solar=120, Demand=80, Wind=-150, Hydro=-30,
                                       FY2026=1100))
    assert "the closing row holds 1,100 but the steps add to 1,020" in off
    gaps = _bridge_warning(chart, _rows(FY2025=1000, Solar=120, Coal=-5))
    assert "no rows for steps ['Demand', 'Wind', 'Hydro']" in gaps
    assert "no row for closing 'FY2026'" in gaps and "['Coal'] are not in steps" in gaps
    assert _bridge_warning(_spec(PLAIN).charts[0], good) is None


# -- design brain -----------------------------------------------------------------------


def _findings(chart, **kw):
    return [f for f in advise(_spec(chart), overlay=EMPTY, **kw).findings
            if f.rule.startswith("chart.waterfall")]


def test_steps_must_add_up():
    for metric in ("AVG(price)", "MIN(price)", "MAX(price)", "COUNT_DISTINCT(customer)"):
        found = [f for f in _findings({**BRIDGE, "metric": metric})
                 if f.rule == "chart.waterfall-additive"]
        assert [f.severity for f in found] == ["warn"], metric
    assert not [f for f in _findings(BRIDGE) if f.rule == "chart.waterfall-additive"]


def test_a_categorical_waterfall_without_steps_is_told_its_order():
    assert "chart.waterfall-order" in {f.rule for f in _findings(PLAIN)}
    for quiet in (BRIDGE, {**PLAIN, "x_column": "ts", "time_grain": "P1M"}):
        assert "chart.waterfall-order" not in {f.rule for f in _findings(quiet)}


def test_stock_and_faint_colours_are_named():
    stock = [f for f in _findings(PLAIN) if f.rule == "chart.waterfall-colors"]
    assert [f.severity for f in stock] == ["info"]
    assert "increase_color #5AC189 (2.2:1)" in stock[0].detail
    assert not [f for f in _findings(BRIDGE) if f.rule == "chart.waterfall-colors"]
    faint = [f for f in _findings({**BRIDGE, "increase_color": "#A6FF93"})
             if f.rule == "chart.waterfall-colors"]
    assert [f.severity for f in faint] == ["warn"] and "#A6FF93 is 1.2:1" in faint[0].detail


def test_axis_titles_where_superset_puts_them_over_the_labels():
    found = [f for f in _findings({**BRIDGE, "x_axis_title": "Driver", "x_label_rotation": 45})
             if f.rule == "chart.waterfall-axis-titles"]
    assert [f.severity for f in found] == ["info", "info"]
    assert "y_axis_title 'MWh'" in found[0].detail and "rotated 45" in found[1].detail
    quiet = {k: v for k, v in BRIDGE.items() if k != "y_axis_title"}
    assert not [f for f in _findings({**quiet, "x_axis_title": "Driver"})
                if f.rule == "chart.waterfall-axis-titles"]


def test_too_many_steps():
    steps = [f"S{i}" for i in range(13)]
    found = [f for f in _findings({**BRIDGE, "steps": steps}) if f.rule == "chart.waterfall-steps"]
    assert [f.severity for f in found] == ["warn"]


def test_the_brain_fills_value_labels_and_a_count_format():
    data = json.loads(json.dumps({
        "spec_version": "1", "dashboard": {"title": "T", "slug": "sdc-t"},
        "charts": [{**BRIDGE, "show_value": False, "number_format": None, "metric": "COUNT(*)",
                    "width": 12}],
        "layout": {"rows": [["Energy bridge"]]}}))
    data["charts"][0] = {k: v for k, v in data["charts"][0].items()
                         if k not in ("show_value", "number_format")}
    fixed, _ = advise_and_fix(data, overlay=EMPTY)
    chart = fixed["charts"][0]
    assert chart["show_value"] is True and chart["number_format"] == ",.0f"
    assert fixed["design"]["filled"]["Energy bridge"] == {"show_value": True, "number_format": ",.0f"}
