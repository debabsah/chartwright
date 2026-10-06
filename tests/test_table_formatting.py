"""Table colour rules that read one column and paint another (Superset 6.1's
columnFormatting), hidden columns and per-column number formats: the shape a
scorecard needs when each row's goal differs, so the number is coloured by a
status column beside it rather than by its own value."""

import pytest
from pydantic import ValidationError

from chartwright.compiler import compile_bundle
from chartwright.dashdiff import _normalize
from chartwright.decompile import _format_to_spec, decompile_bundle
from chartwright.spec import load_spec
from chartwright.testing import edit_bundle, stub_resolution

from test_table_sort import _lookup, aggregate, mk, params_for, raw

STATUS = [
    {"metric": "status", "operator": "=", "target": 1, "color": "green", "apply_to": "Rate"},
    {"metric": "status", "operator": "=", "target": 3, "color": "red", "apply_to": "Rate"},
    {"metric": "status", "operator": "=", "target": 3, "color": "red", "apply_to": "row"},
]


def board(**over):
    return aggregate(metrics=["MAX(rate) AS Rate", "MAX(st) AS status", "MIN(ord)"],
                     sort_by="MIN(ord)", sort_ascending=True, conditional_formatting=STATUS,
                     hidden=["status"], number_formats={"Rate": ".3f"}, **over)


def test_emission():
    p = params_for(board())
    assert p["conditional_formatting"] == [
        {"column": "status", "colorScheme": "#ACE1C4", "operator": "=", "targetValue": 1.0,
         "useGradient": False, "columnFormatting": "Rate"},
        {"column": "status", "colorScheme": "#EFA1AA", "operator": "=", "targetValue": 3.0,
         "useGradient": False, "columnFormatting": "Rate"},
        {"column": "status", "colorScheme": "#EFA1AA", "operator": "=", "targetValue": 3.0,
         "useGradient": False, "columnFormatting": "ENTIRE_ROW"},
    ]
    assert p["column_config"] == {"status": {"visible": False}, "Rate": {"d3NumberFormat": ".3f"}}
    assert p["order_desc"] is False


def test_plain_table_emits_no_new_keys():
    p = params_for(aggregate())
    assert "conditional_formatting" not in p and "column_config" not in p


def test_labels_must_exist():
    for bad in (
        {"conditional_formatting": [{**STATUS[0], "apply_to": "Nope"}]},
        {"conditional_formatting": [{**STATUS[0], "metric": "Nope"}]},
        {"hidden": ["Nope"]},
        {"number_formats": {"Nope": ".1f"}},
    ):
        with pytest.raises(ValidationError, match="not one of the table's labels"):
            mk(aggregate(metrics=["MAX(rate) AS Rate", "MAX(st) AS status"], **bad))
    with pytest.raises(ValidationError, match="sort_ascending needs sort_by"):
        mk(aggregate(sort_ascending=True))


def test_apply_to_is_table_only():
    with pytest.raises(ValidationError, match="table-only"):
        load_spec({
            "spec_version": "1", "dashboard": {"title": "T", "slug": "t"},
            "charts": [{"name": "P", "type": "pivot_table", "dataset": {"database": "db", "table": "t"},
                        "rows": ["r"], "metrics": ["MAX(st) AS status"],
                        "conditional_formatting": [STATUS[0] | {"apply_to": "status"}]}],
            "layout": {"rows": [["P"]]},
        })


def test_roundtrip_has_no_drift():
    spec = mk(board())
    result = decompile_bundle(compile_bundle(spec, stub_resolution(spec)), _lookup(spec))
    assert result.losses == [], result.losses_json()
    live = load_spec(result.spec)
    assert _normalize(live)["charts"] == _normalize(spec)["charts"]


def test_hidden_labels_in_any_order_have_no_drift():
    """Superset keeps column_config by label, so decompile reads hidden labels back in the
    bundle's key order; plan compared the lists in order and reported the table changed
    after a clean apply."""
    spec = mk(aggregate(metrics=["MAX(rate) AS Rate", "MAX(st) AS status"],
                        hidden=["status", "Rate"]))
    result = decompile_bundle(compile_bundle(spec, stub_resolution(spec)), _lookup(spec))
    assert result.spec["charts"][0]["hidden"] == ["Rate", "status"]
    assert _normalize(load_spec(result.spec)) == _normalize(spec)


def test_decompile_outside_surface():
    base = {"column": "status", "colorScheme": "#ACE1C4", "operator": "=", "targetValue": 1}
    assert _format_to_spec({**base, "toAllRow": True})["apply_to"] == "row"  # 5.x-era flag
    assert _format_to_spec({**base, "toTextColor": True}) is None
    assert _format_to_spec({**base, "objectFormatting": "CELL_BAR"}) is None


def test_text_paint_uses_the_dark_palette_and_round_trips():
    rule = {"metric": "dir", "operator": "=", "target": -1, "color": "green", "apply_to": "Trend",
            "paint": "text"}
    chart = aggregate(metrics=["MAX(txt) AS Trend", "MAX(d) AS dir"], hidden=["dir"],
                      conditional_formatting=[rule])
    p = params_for(chart)
    assert p["conditional_formatting"] == [{
        "column": "dir", "colorScheme": "#1B7F3B", "operator": "=", "targetValue": -1.0,
        "useGradient": False, "objectFormatting": "TEXT_COLOR", "columnFormatting": "Trend"}]
    spec = mk(chart)
    result = decompile_bundle(compile_bundle(spec, stub_resolution(spec)), _lookup(spec))
    assert result.losses == [], result.losses_json()
    assert _normalize(load_spec(result.spec))["charts"] == _normalize(spec)["charts"]


def test_axis_number_format_round_trips():
    from test_table_sort import DS
    for chart in (
        {"name": "L", "type": "timeseries_line", "dataset": DS, "metrics": ["MAX(r)"],
         "time_column": "m", "time_grain": "P1M", "number_format": ".1%"},
        {"name": "B", "type": "bar", "dataset": DS, "metrics": ["SUM(v)"], "x_column": "k",
         "number_format": ",.0f"},
    ):
        p = params_for(chart)
        assert p["y_axis_format"] == chart["number_format"]
        spec = mk(chart)
        result = decompile_bundle(compile_bundle(spec, stub_resolution(spec)), _lookup(spec))
        assert result.losses == [], result.losses_json()
        assert _normalize(load_spec(result.spec))["charts"] == _normalize(spec)["charts"]
    assert params_for({"name": "B", "type": "bar", "dataset": DS, "metrics": ["SUM(v)"],
                       "x_column": "k"})["y_axis_format"] == "SMART_NUMBER"


def test_cell_bars_and_date_format_emit_only_when_set_and_round_trip():
    p = params_for(raw(cell_bars=False, date_format="%Y-%m-%d"))
    assert p["show_cell_bars"] is False and p["table_timestamp_format"] == "%Y-%m-%d"
    assert not {"show_cell_bars", "table_timestamp_format"} & set(params_for(raw()))
    spec = mk(raw(cell_bars=False, date_format="%Y-%m-%d"))
    result = decompile_bundle(compile_bundle(spec, stub_resolution(spec)), _lookup(spec))
    assert result.losses == [], result.losses_json()
    assert _normalize(load_spec(result.spec))["charts"] == _normalize(spec)["charts"]


def _edit_params(bundle: bytes, edit) -> bytes:
    """The same bundle with every chart's params passed through `edit`."""
    return edit_bundle(bundle, lambda path, doc: edit(doc["params"]) if "/charts/" in path else None)


def test_settings_for_a_dropped_column_are_dropped_with_a_loss():
    def stale(p):
        p["column_config"]["gone"] = {"visible": False, "d3NumberFormat": ".1f"}
        p["conditional_formatting"].append(
            {"column": "gone", "colorScheme": "#ACE1C4", "operator": "=", "targetValue": 1})
        p["conditional_formatting"].append(
            {"column": "status", "colorScheme": "#ACE1C4", "operator": "=", "targetValue": 1,
             "columnFormatting": "gone"})

    spec = mk(board())
    bundle = _edit_params(compile_bundle(spec, stub_resolution(spec)), stale)
    result = decompile_bundle(bundle, _lookup(spec))
    live = load_spec(result.spec)  # a valid spec, not a validation error
    assert _normalize(live)["charts"] == _normalize(spec)["charts"]
    assert sum("'gone'" in l.what for l in result.losses) == 3, result.losses_json()


def test_pivot_rule_on_a_dropped_metric_is_dropped_with_a_loss():
    spec = load_spec({
        "spec_version": "1", "dashboard": {"title": "T", "slug": "t"},
        "charts": [{"name": "P", "type": "pivot_table", "dataset": {"database": "db", "table": "t"},
                    "rows": ["r"], "metrics": ["MAX(st) AS status"],
                    "conditional_formatting": [{k: v for k, v in STATUS[0].items() if k != "apply_to"}]}],
        "layout": {"rows": [["P"]]},
    })

    def stale(p):
        p["conditional_formatting"].append(
            {"column": "gone", "colorScheme": "#ACE1C4", "operator": "=", "targetValue": 1})

    bundle = _edit_params(compile_bundle(spec, stub_resolution(spec)), stale)
    result = decompile_bundle(bundle, _lookup(spec))
    assert len(load_spec(result.spec).charts[0].conditional_formatting) == 1
    assert any("'gone'" in l.what for l in result.losses), result.losses_json()
