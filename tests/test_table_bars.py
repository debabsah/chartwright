"""A table's cell bars per column: bars on the columns whose size is the point, a change
coloured by its sign, a mover's bar sized by its absolute value. Superset has each switch
table-wide (show_cell_bars, color_pn, align_pn) and per column in column_config
(showCellBars, colorPositiveNegative, alignPositiveNegative), the column's own value read
first (plugin-chart-table/src/TableChart.tsx, all three releases). A list in the spec
turns the table-wide switch off and each listed column's on."""

import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from chartwright.compiler import compile_bundle
from chartwright.dashdiff import _normalize
from chartwright.decompile import decompile_bundle
from chartwright.design import advise
from chartwright.design.presets import Overlay
from chartwright.spec import load_spec
from chartwright.testing import edit_bundle, stub_resolution
from chartwright.versions import check_spec_version

from test_display_controls import _plan
from test_table_sort import DS, _lookup, mk, params_for

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
from params_drift import CONTRACT, check  # noqa: E402

METRICS = ["SUM(sales) AS Revenue", "SUM(delta) AS Change", "COUNT(*) AS Orders"]
BAR_KEYS = {"show_cell_bars", "color_pn", "align_pn"}
CC_KEYS = {"showCellBars", "colorPositiveNegative", "alignPositiveNegative"}


def movers(**over):
    return {"name": "Movers", "type": "table", "dataset": DS, "metrics": METRICS,
            "groupby": ["country"], **over}


def records(**over):
    return {"name": "Records", "type": "table", "dataset": DS,
            "columns": ["order_id", "customer", "amount", "margin"], **over}


SHOWCASE = movers(cell_bars=["Revenue", "Change"], color_by_sign=["Change"],
                  absolute_bars=["Change"], number_formats={"Change": "+,.0f"})


def _decompile(spec, edit=None):
    bundle = compile_bundle(spec, stub_resolution(spec))
    if edit is not None:
        bundle = edit_bundle(bundle, lambda path, doc: edit(doc["params"])
                             if "/charts/" in path else None)
    return decompile_bundle(bundle, _lookup(spec))


# -- compile -----------------------------------------------------------------------


def test_a_list_turns_the_table_switch_off_and_each_listed_column_on():
    p = params_for(SHOWCASE)
    assert (p["show_cell_bars"], p["color_pn"]) == (False, False)
    assert "align_pn" not in p  # off unless written, so its list needs no table-wide key
    assert p["column_config"] == {
        "Revenue": {"showCellBars": True},
        "Change": {"showCellBars": True, "colorPositiveNegative": True,
                   "alignPositiveNegative": True, "d3NumberFormat": "+,.0f"},
    }


def test_a_boolean_sets_the_table_wide_switch_only():
    p = params_for(records(cell_bars=False))
    assert p["show_cell_bars"] is False and "column_config" not in p
    p = params_for(movers(color_by_sign=False, absolute_bars=True))
    assert (p["color_pn"], p["align_pn"]) == (False, True)
    assert "show_cell_bars" not in p and "column_config" not in p


def test_unset_switches_and_superset_defaults_emit_nothing():
    for chart in (movers(), records()):
        assert not BAR_KEYS & set(params_for(chart)) and "column_config" not in params_for(chart)
    # compile reads a written Superset default as unset
    written, omitted = mk(movers(color_by_sign=True, absolute_bars=False)), mk(movers())
    assert compile_bundle(written, stub_resolution(written)) == \
        compile_bundle(omitted, stub_resolution(omitted))
    assert _normalize(written) == _normalize(omitted)
    c = written.charts[0]
    assert (c.color_by_sign, c.absolute_bars) == (True, False)  # kept as written


def test_the_old_boolean_cell_bars_compile_as_before():
    assert params_for(records(cell_bars=True))["show_cell_bars"] is True
    assert params_for(records(cell_bars=False))["show_cell_bars"] is False
    assert "show_cell_bars" not in params_for(records())


def test_every_key_is_declared_by_the_table_plugin_on_every_release():
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    keys = set(params_for(SHOWCASE)) | set(params_for(movers(absolute_bars=True)))
    assert BAR_KEYS <= keys
    for version in contract:
        assert check(version, contract, {"table": keys}) == [], version


# -- validation --------------------------------------------------------------------


@pytest.mark.parametrize("chart, message", [
    (movers(cell_bars=["Profit"]), "cell_bars 'Profit' is not one of the table's labels"),
    (movers(color_by_sign=["Profit"]), "color_by_sign 'Profit' is not one of the table's labels"),
    (movers(cell_bars=["country"]), "cell_bars 'country' is a dimension"),
    (movers(cell_bars=["Revenue"], color_by_sign=["Change"]),
     "color_by_sign 'Change' has no cell bar to draw on; add it to cell_bars"),
    (movers(cell_bars=False, absolute_bars=True),
     "absolute_bars draws on cell bars, and cell_bars is false"),
    (movers(cell_bars=False, color_by_sign=["Change"]), "color_by_sign 'Change' has no cell bar"),
    (movers(absolute_bars=["Change", "Change"]), "absolute_bars lists 'Change' twice"),
    (movers(cell_bars=[]), "at least 1 item"),
])
def test_wrong_switches_are_named(chart, message):
    with pytest.raises(ValidationError) as err:
        mk(chart)
    assert message in str(err.value)


def test_a_raw_table_takes_any_column_and_a_written_default_asks_for_no_bar():
    mk(records(cell_bars=["amount", "margin"], color_by_sign=["margin"]))
    # color_by_sign true and absolute_bars false are Superset's own: no bar needed.
    mk(movers(cell_bars=False, color_by_sign=True, absolute_bars=False))
    mk(movers(cell_bars=["Revenue"], color_by_sign=True, absolute_bars=True))


# -- decompile -------------------------------------------------------------------------


@pytest.mark.parametrize("chart", [
    SHOWCASE,
    movers(cell_bars=False, color_by_sign=False),
    movers(cell_bars=True, absolute_bars=True),
    movers(color_by_sign=["Change", "Revenue"]),  # any order: plan reads it in label order
    records(cell_bars=["margin", "amount"], absolute_bars=["margin"]),
])
def test_round_trip_is_lossless(chart):
    spec = mk(chart)
    out = _decompile(spec)
    assert out.losses == [], out.losses_json()
    assert _normalize(load_spec(out.spec)) == _normalize(spec)


def test_decompile_lists_labels_in_the_table_order():
    back = _decompile(mk(movers(color_by_sign=["Orders", "Change"]))).spec["charts"][0]
    assert back["color_by_sign"] == ["Change", "Orders"]


NULL = object()  # a key stored as null, which Superset reads as a false


def _with(**params):
    def edit(p):
        for key, value in params.items():
            if key == "column_config":
                for label, cfg in value.items():
                    p.setdefault("column_config", {}).setdefault(label, {}).update(cfg)
            elif value is None:
                p.pop(key, None)
            else:
                p[key] = None if value is NULL else value
    return edit


@pytest.mark.parametrize("stored, want", [
    # What a chart saved in Explore stores untouched: Superset's own defaults.
    ({"show_cell_bars": True, "color_pn": True, "align_pn": False},
     {"cell_bars": True, "color_by_sign": None, "absolute_bars": None}),
    # Turned off for one column in the column's own settings.
    ({"column_config": {"Orders": {"showCellBars": False}}},
     {"cell_bars": ["Revenue", "Change"]}),
    ({"show_cell_bars": False, "column_config": {"Change": {"showCellBars": True}}},
     {"cell_bars": ["Change"]}),
    ({"color_pn": True, "column_config": {"Revenue": {"colorPositiveNegative": False}}},
     {"cell_bars": None, "color_by_sign": ["Change", "Orders"]}),
    ({"column_config": {"Change": {"alignPositiveNegative": True}}},
     {"absolute_bars": ["Change"]}),
    ({"color_pn": False}, {"color_by_sign": False}),
    # A column's value that changes nothing: the table's own, or on a column without a bar.
    ({"column_config": {"country": {"showCellBars": False}, "Revenue": {"showCellBars": True}}},
     {"cell_bars": None}),
    ({"show_cell_bars": False, "column_config": {"Change": {"colorPositiveNegative": True}},
      "align_pn": True}, {"cell_bars": False, "color_by_sign": None, "absolute_bars": None}),
    # Superset tests `=== undefined`: a stored null is a false.
    ({"column_config": {"Orders": {"showCellBars": None}}}, {"cell_bars": ["Revenue", "Change"]}),
    ({"show_cell_bars": NULL}, {"cell_bars": False}),
    ({"color_pn": NULL}, {"color_by_sign": False}),
])
def test_what_superset_stores_reads_back_as_it_draws(stored, want):
    out = _decompile(mk(movers()), _with(**stored))
    assert out.losses == [], out.losses_json()
    back = out.spec["charts"][0]
    assert {k: back.get(k) for k in want} == want
    load_spec(out.spec)  # still a valid spec


def test_a_raw_table_turned_off_for_its_id_keeps_bars_on_every_other_column():
    out = _decompile(mk(records()), _with(show_cell_bars=True,
                                          column_config={"order_id": {"showCellBars": False}}))
    assert out.spec["charts"][0]["cell_bars"] == ["customer", "amount", "margin"]


# -- plan ------------------------------------------------------------------------------


@pytest.mark.parametrize("stored", [
    {"column_config": {"Revenue": {"showCellBars": False}}},
    {"column_config": {"Change": {"colorPositiveNegative": False}}},
    {"column_config": {"Change": {"alignPositiveNegative": False}}},
    {"show_cell_bars": True, "column_config": {"Orders": {"showCellBars": True}}},
])
def test_plan_reports_a_switch_changed_in_the_ui(stored, monkeypatch):
    spec = mk(SHOWCASE)
    out = _plan(spec, lambda path, doc: _with(**stored)(doc["params"])
                if "/charts/" in path else None, monkeypatch)
    assert out["charts_changed"] == ["Movers"], out


def test_plan_reads_label_lists_in_any_order(monkeypatch):
    chart = movers(cell_bars=["Orders", "Change"], absolute_bars=["Orders", "Change"])
    assert _plan(mk(chart), None, monkeypatch)["clean"] is True


# -- release differences -----------------------------------------------------------------


RULE = {"metric": "Orders", "operator": ">", "target": 100, "color": "green"}


@pytest.mark.parametrize("chart, warned", [
    (movers(cell_bars=["Revenue"], conditional_formatting=[RULE]), True),
    (movers(cell_bars=True, conditional_formatting=[RULE]), True),
    (movers(color_by_sign=["Change"], conditional_formatting=[RULE]), True),
    (movers(conditional_formatting=[RULE]), False),  # Superset's bars: nobody asked
    (movers(cell_bars=False, color_by_sign=False, conditional_formatting=[RULE]), False),
    (movers(color_by_sign=True, conditional_formatting=[RULE]), False),  # the default
    (movers(cell_bars=["Revenue"]), False),
])
def test_bars_beside_colour_rules_warn_before_6_1(chart, warned):
    spec = load_spec({"spec_version": "1", "dashboard": {"title": "T", "slug": "t"},
                      "charts": [chart], "layout": {"rows": [["Movers"]]}})
    for version in ("4.1.4", "5.0.0", "6.0.0"):
        out = check_spec_version(spec, version)
        assert out.ok
        # Only the bar warning: a '>' rule also warns for its fade (test_format_rule_gating.py).
        bars = [w for w in out.warnings if w["field"] == "cell_bars"]
        got = [(w["field"], w["chart"], w["since"]) for w in bars]
        assert got == ([("cell_bars", "Movers", "6.1.0")] if warned else []), version
        if warned:
            assert "draws no cell bar on a table with any colour rule" in bars[0]["detail"]
    assert check_spec_version(spec, "6.1.0").warnings == []


# -- design brain --------------------------------------------------------------------------


def test_the_cell_bars_fill_leaves_bars_a_sign_switch_draws_on():
    def filled(**over):
        rep = advise(mk(records(**over)), overlay=Overlay())
        return [f.fix["set"] for f in rep.findings if f.rule == "default.cell-bars"]

    assert filled() == [{"cell_bars": False}]  # an id column: no bars at all
    assert filled(color_by_sign=["margin"]) == []
    assert filled(absolute_bars=True) == []
    assert filled(color_by_sign=False) == [{"cell_bars": False}]  # asks for no bar
