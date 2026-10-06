"""Colour rules on a big number: an enterprise KPI tile colours its number by status,
e.g. a balance-closure variance in red outside +-2%. Superset's Big Number declares
conditional_formatting at 4.1.4, 5.0.0 and 6.1.0 (BigNumberTotal/controlPanel.ts) and
paints the number itself, solid, from every rule whose column is set; the trendline
KPI has no colour rules in any release."""

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
from chartwright.smoke import _zero_colour_warning
from chartwright.spec import FORMAT_TEXT_HEX, hex_to_rgb, load_spec
from chartwright.testing import edit_bundle, stub_resolution
from chartwright.versions import check_spec_version
from chartwright.visible import contrast

from test_table_sort import _lookup, mk, params_for

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
from params_drift import CONTRACT, check  # noqa: E402

DS = {"database": "db", "table": "orders"}  # what test_table_sort._lookup reads back
CLOSURE = [
    {"operator": "<", "target": -0.02, "color": "red"},
    {"operator": ">", "target": 0.02, "color": "red"},
    {"operator": "between", "target_left": -0.02, "target_right": 0.02, "color": "green"},
]


def kpi(**over):
    return {"name": "Balance Closure", "type": "big_number_total", "dataset": DS,
            "metric": "SUM(variance) AS Variance", "number_format": ".1%", **over}


def coloured(rules=CLOSURE, **over):
    return kpi(conditional_formatting=rules, **over)


def roundtrip(spec, edit=None):
    bundle = compile_bundle(spec, stub_resolution(spec))
    if edit is not None:
        bundle = edit_bundle(bundle, edit)
    return decompile_bundle(bundle, _lookup(spec))


def edit_rules(rules):
    def edit(path, doc):
        if "/charts/" in path:
            doc["params"]["conditional_formatting"] = rules
    return edit


# -- compile -----------------------------------------------------------------------


def test_rules_compile_to_the_big_number_panel_shape():
    p = params_for(coloured(CLOSURE + [{"operator": "=", "target": 0.5, "color": "#0057b8"}]))
    assert p["conditional_formatting"] == [
        {"column": "Variance", "colorScheme": "#B3261E", "operator": "<", "targetValue": -0.02,
         "useGradient": False},
        {"column": "Variance", "colorScheme": "#B3261E", "operator": ">", "targetValue": 0.02,
         "useGradient": False},
        {"column": "Variance", "colorScheme": "#1B7F3B", "operator": "< x <",
         "targetValueLeft": -0.02, "targetValueRight": 0.02, "useGradient": False},
        {"column": "Variance", "colorScheme": "#0057B8", "operator": "=", "targetValue": 0.5,
         "useGradient": False},
    ]
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    for version in contract:
        assert check(version, contract, {"big_number_total": set(p)}) == [], version


def test_names_take_the_text_shades_since_the_number_is_text():
    rules = [{"operator": ">", "target": t, "color": c} for t, c in
             ((1, "green"), (2, "amber"), (3, "red"))]
    p = params_for(coloured(rules))
    assert [cf["colorScheme"] for cf in p["conditional_formatting"]] == [
        FORMAT_TEXT_HEX["green"], FORMAT_TEXT_HEX["amber"], FORMAT_TEXT_HEX["red"]]


def test_the_column_is_the_metric_label_superset_keys_the_value_by():
    for metric, label in (("SUM(variance)", "SUM(variance)"), ("COUNT(*)", "COUNT(*)"),
                          ("net_variance", "net_variance"),
                          ("SQL(SUM(a) / NULLIF(SUM(b), 0)) AS Closure", "Closure")):
        p = params_for(coloured(metric=metric))
        assert {cf["column"] for cf in p["conditional_formatting"]} == {label}, metric


def test_a_big_number_without_rules_compiles_as_before():
    assert "conditional_formatting" not in params_for(kpi())
    assert "conditional_formatting" not in params_for(kpi(conditional_formatting=[]))


# -- the spec ------------------------------------------------------------------------


def test_metric_may_name_the_charts_own_label_and_reads_as_omitted():
    named = [rule | {"metric": "Variance"} for rule in CLOSURE]
    spec = mk(coloured(named))
    assert [r.metric for r in spec.charts[0].conditional_formatting] == [None] * 3
    assert params_for(coloured(named)) == params_for(coloured())
    with pytest.raises(ValidationError, match="not the chart's metric label 'Variance'"):
        mk(coloured([CLOSURE[0] | {"metric": "SUM(variance)"}]))


@pytest.mark.parametrize("extra, message", [
    ({"apply_to": "row"}, "apply_to is table-only"),
    ({"paint": "text"}, "paint is for table and pivot cells"),
    ({"paint": "cell"}, "paint is for table and pivot cells"),
])
def test_cell_options_are_refused(extra, message):
    with pytest.raises(ValidationError, match=message):
        mk(coloured([CLOSURE[0] | extra]))


@pytest.mark.parametrize("bad, message", [
    ({"operator": "between", "target": 1}, "'between' needs target_left"),
    ({"operator": "<"}, "needs target"),
    ({"operator": "=>", "target": 1}, "operator"),
    ({"operator": "<", "target": 1, "color": "teal"}, "green, amber, red or #RRGGBB"),
])
def test_rule_shapes_are_checked_as_on_tables(bad, message):
    with pytest.raises(ValidationError, match=message):
        mk(coloured([{"color": "red"} | bad]))


def test_the_trendline_kpi_refuses_rules_and_says_where_they_go():
    trend = {"name": "T", "type": "big_number_trend", "dataset": DS, "metric": "SUM(v)",
             "time_column": "ts", "conditional_formatting": CLOSURE}
    with pytest.raises(ValidationError, match="only on big_number_total"):
        mk(trend)


def test_every_supported_release_takes_the_rules():
    spec = mk(coloured())
    for version in ("4.1.4", "5.0.0", "6.0.0", "6.1.0"):
        out = check_spec_version(spec, version)
        assert out.errors == [] and out.warnings == [], version


# -- decompile and plan ----------------------------------------------------------------


def test_rules_round_trip_with_no_loss_and_no_drift():
    spec = mk(coloured(CLOSURE + [{"operator": "=", "target": 0.5, "color": "#7a1fa2"}]))
    out = roundtrip(spec)
    assert out.losses == [], out.losses_json()
    rules = out.spec["charts"][0]["conditional_formatting"]
    assert rules[0] == {"operator": "<", "color": "red", "target": -0.02}
    assert [r["color"] for r in rules] == ["red", "red", "green", "#7A1FA2"]
    assert _normalize(load_spec(out.spec))["charts"] == _normalize(spec)["charts"]


@pytest.mark.parametrize("written, same_as_red", [("#B3261E", True), ("#EFA1AA", False)])
def test_plan_reads_a_rule_hex_as_the_shade_the_number_paints(written, same_as_red):
    """A name on a big number paints its text shade, so plan reads that shade's hex as the
    name, as decompile does; the cell shade of the same name is a different colour there."""
    spec = mk(coloured([{"operator": "<", "target": -0.02, "color": written}]))
    out = roundtrip(spec)
    assert _normalize(load_spec(out.spec))["charts"] == _normalize(spec)["charts"]
    red = mk(coloured([{"operator": "<", "target": -0.02, "color": "red"}]))
    assert (_normalize(red)["charts"] == _normalize(spec)["charts"]) is same_as_red


def test_decompile_reads_what_the_number_shows_and_names_the_rest():
    base = {"column": "Variance", "operator": ">", "targetValue": 1}
    rules = [
        base | {"colorScheme": "#1b7f3b", "useGradient": True},     # gradient: solid anyway
        base | {"colorScheme": "#ACE1C4"},                           # the cell pastel: a hex
        base | {"colorScheme": "#B3261E", "column": "Old label"},    # any column paints
        base | {"colorScheme": "colorSuccess"},                      # 6.1 theme token
        base | {"colorScheme": "#B3261E", "operator": "≤ x <",       # a half-open range:
                "targetValueLeft": 1, "targetValueRight": 2},        # outside the operators
        {"colorScheme": "#B3261E", "operator": ">", "targetValue": 1},  # no column: never paints
    ]
    out = roundtrip(mk(coloured()), edit_rules(rules))
    got = out.spec["charts"][0]["conditional_formatting"]
    assert [r["color"] for r in got] == ["green", "#ACE1C4", "red"]
    what = [loss.what for loss in out.losses]
    assert len(what) == 3, what
    assert sum("not representable" in w for w in what) == 2
    assert any("no column, which Superset never paints" in w for w in what)
    load_spec(out.spec)


def test_an_untouched_big_number_reads_back_without_rules_or_losses():
    out = roundtrip(mk(kpi()), edit_rules([]))
    assert out.losses == [] and "conditional_formatting" not in out.spec["charts"][0]


def test_rules_left_on_a_trendline_paint_nothing_and_are_no_loss():
    """A chart switched in Superset from Big Number to the trendline keeps its rules in
    the form data; the trendline plugin never reads them."""
    trend = {"name": "T", "type": "big_number_trend", "dataset": DS, "metric": "SUM(v)",
             "time_column": "ts"}

    def edit(path, doc):
        if "/charts/" in path:
            doc["params"]["conditional_formatting"] = [
                {"column": "SUM(v)", "colorScheme": "#B3261E", "operator": "<", "targetValue": 0}]

    out = roundtrip(mk(trend), edit)
    assert out.losses == [] and "conditional_formatting" not in out.spec["charts"][0]


# -- smoke ---------------------------------------------------------------------------


@pytest.mark.parametrize("value, rules, warns", [
    (0, CLOSURE, "not the green its rules give 0"),
    (0.0, [{"operator": "<", "target": 1, "color": "red"}], "not the red"),
    (0, [{"operator": "=", "target": 0, "color": "amber"}], "not the amber"),
    (0, [{"operator": ">", "target": 0, "color": "red"}], None),    # 0 earns no colour anyway
    (0.01, CLOSURE, None),
    (None, CLOSURE, None),                                           # "No data"
])
def test_smoke_warns_when_a_zero_number_loses_its_colour(value, rules, warns):
    spec = mk(coloured(rules))
    warning = _zero_colour_warning(spec.charts[0], [{"data": [{"Variance": value}]}])
    if warns is None:
        assert warning is None
    else:
        assert warning and "never colours" in warning and warns in warning
    assert _zero_colour_warning(mk(kpi()).charts[0], [{"data": [{"Variance": 0}]}]) is None
    assert _zero_colour_warning(spec.charts[0], [{"data": []}]) is None


INCLUSIVE = [{"operator": "<=", "target": -0.02, "color": "red"},
             {"operator": ">=", "target": 0.02, "color": "red"},
             {"operator": "!=", "target": 0, "color": "amber"},
             {"operator": "between_inclusive", "target_left": 0.5, "target_right": 0.6,
              "color": "green"}]


def test_a_big_number_takes_every_table_operator():
    """Superset builds a big number's rules with the table's getColorFormatters
    (BigNumberTotal/transformProps.ts 4.1.4 :99, 6.1.0 :121), so every comparator a
    table rule has colours the number too: compiled, read back, tested and described."""
    spec = mk(coloured(INCLUSIVE))
    p = params_for(coloured(INCLUSIVE))
    assert [cf["operator"] for cf in p["conditional_formatting"]] == ["≤", "≥", "≠", "≤ x ≤"]
    assert p["conditional_formatting"][3]["targetValueLeft"] == 0.5
    out = roundtrip(spec)
    assert out.losses == [], out.losses_json()
    assert _normalize(load_spec(out.spec))["charts"] == _normalize(spec)["charts"]
    rules = spec.charts[0].conditional_formatting
    assert [r.matches(0.02) for r in rules] == [False, True, True, False]
    assert [r.matches(0.5) for r in rules] == [False, True, True, True]
    zero = [{"data": [{"Variance": 0}]}]
    assert _zero_colour_warning(spec.charts[0], zero) is None  # no rule takes 0 ('!= 0' doesn't)
    at_most = mk(coloured([{"operator": "<=", "target": 0, "color": "red"}]))
    assert "the red its rules give 0" in _zero_colour_warning(at_most.charts[0], zero)
    [f] = findings(coloured(INCLUSIVE), rule="narrative.kpi-thresholds")
    assert ("red at or below -0.02; red at or above 0.02; amber except at 0; "
            "green from 0.5 to 0.6") in f.detail


def test_inclusive_kpi_bands_that_share_a_bound_overlap():
    touching = [{"operator": "<=", "target": 0.02, "color": "green"},
                {"operator": ">=", "target": 0.02, "color": "red"}]
    [f] = findings(coloured(touching, description="0.02"), rule="chart.format-bands")
    assert f.severity == "warn"
    apart = [{"operator": "<=", "target": 0.02, "color": "green"},
             {"operator": ">", "target": 0.02, "color": "red"}]
    assert findings(coloured(apart, description="0.02"), rule="chart.format-bands") == []


# -- the design brain ----------------------------------------------------------------


def findings(*charts, rule=None):
    data = {"spec_version": "1", "dashboard": {"title": "T", "slug": "t"},
            "charts": list(charts), "layout": {"rows": [[c["name"]] for c in charts]}}
    out = advise(load_spec(data), overlay=Overlay()).findings
    return [f for f in out if rule is None or f.rule == rule]


def test_overlapping_kpi_bands_warn_and_disjoint_or_lone_bands_do_not():
    overlap = [{"operator": "<", "target": 0.05, "color": "green"},
               {"operator": ">", "target": 0.02, "color": "red"}]
    [f] = findings(coloured(overlap, description="0.05 0.02"), rule="chart.format-bands")
    assert f.severity == "warn" and "later rule's colour" in f.detail
    assert findings(coloured(description="2%"), rule="chart.format-bands") == []
    # one red band outside a tolerance is exception highlighting, not decoration
    lone = [{"operator": "<", "target": 0, "color": "red"}]
    assert findings(coloured(lone, description="below 0"), rule="chart.format-bands") == []


def test_an_equals_band_is_one_point_not_everything_above_it():
    def pivot(rules):
        return {"type": "pivot_table", "name": "P", "dataset": DS, "rows": ["region"],
                "metrics": ["SUM(v) AS Val"], "height": 8, "row_limit": 20,
                "conditional_formatting": [{"metric": "Val"} | r for r in rules]}

    apart = [{"operator": "=", "target": 1, "color": "red"},
             {"operator": ">", "target": 5, "color": "green"}]
    assert not any(f.severity == "warn" for f in findings(pivot(apart), rule="chart.format-bands"))
    for clash in ([{"operator": "=", "target": 7, "color": "red"},
                   {"operator": ">", "target": 5, "color": "green"}],
                  [{"operator": "=", "target": 7, "color": "red"},
                   {"operator": "=", "target": 7, "color": "green"}]):
        assert any(f.severity == "warn" for f in findings(pivot(clash), rule="chart.format-bands"))


def test_named_colours_read_on_white():
    """The brain tells authors green, amber and red pass: they must, as text."""
    for name, hexed in FORMAT_TEXT_HEX.items():
        rgb = hex_to_rgb(hexed)
        assert contrast((rgb["r"], rgb["g"], rgb["b"]), (255, 255, 255)) >= 4.5, name


def test_pale_colours_on_numbers_lines_and_text_warn():
    pale = [{"operator": ">", "target": 0.02, "color": "#FDE380"},
            {"operator": "<", "target": -0.02, "color": "#FDE380"}]
    [f] = findings(coloured(pale, description="2%"), rule="chart.color-contrast")
    assert f.severity == "warn" and "#FDE380" in f.detail and "below 3:1" in f.detail
    assert findings(coloured(description="2%"), rule="chart.color-contrast") == []
    trend = {"name": "Tr", "type": "big_number_trend", "dataset": DS, "metric": "SUM(v)",
             "time_column": "ts", "trend_color": "#A8E6CF"}
    assert findings(trend, rule="chart.color-contrast")
    assert findings(trend | {"trend_color": "green"}, rule="chart.color-contrast") == []
    table = {"name": "Tb", "type": "table", "dataset": DS, "metrics": ["MAX(x) AS X"],
             "groupby": ["r"], "row_limit": 10,
             "conditional_formatting": [{"metric": "X", "operator": ">", "target": 1,
                                         "color": "#43A047", "paint": "text"}]}
    [f] = findings(table, rule="chart.color-contrast")
    assert "below 4.5:1" in f.detail     # 3.3:1 reads as a big number, not as cell text
    big = [{"operator": ">", "target": 0.02, "color": "#43A047"}]
    assert findings(coloured(big, description="2%"), rule="chart.color-contrast") == []
    cell = table | {"conditional_formatting": [table["conditional_formatting"][0] | {"paint": "cell"}]}
    assert findings(cell, rule="chart.color-contrast") == []


@pytest.mark.parametrize("over, missing", [
    ({}, "0.02"),
    ({"description": "Ledger minus bank, as a share of the ledger"}, "0.02"),
    ({"description": "Red outside the ±2% tolerance"}, None),
    ({"subtitle": "Tolerance 0.02"}, None),
])
def test_a_coloured_kpi_states_its_thresholds(over, missing):
    out = findings(coloured(**over), rule="narrative.kpi-thresholds")
    if missing is None:
        assert out == []
    else:
        [f] = out
        assert f.severity == "info" and missing in f.detail and "red below -0.02" in f.detail


def test_every_threshold_must_be_stated():
    rules = [{"operator": "<", "target": 90, "color": "red"},
             {"operator": "between", "target_left": 90, "target_right": 95, "color": "amber"},
             {"operator": ">", "target": 95, "color": "green"}]
    on_time = kpi(metric="AVG(on_time) AS On time", number_format=",.0f",
                  conditional_formatting=rules)
    [f] = findings(on_time | {"description": "Target 95"}, rule="narrative.kpi-thresholds")
    assert "states 90;" in f.detail
    assert findings(on_time | {"description": "Green from 95, red under 90"},
                    rule="narrative.kpi-thresholds") == []
    assert findings(on_time | {"subtitle": "95% target, 90% floor"},
                    rule="narrative.kpi-thresholds") == []
    assert findings(kpi(), rule="narrative.kpi-thresholds") == []


def test_large_thresholds_read_in_their_short_forms_and_print_in_full():
    sales = kpi(metric="SUM(sales) AS Sales", number_format="$.3s",
                conditional_formatting=[{"operator": ">", "target": 10_000_000, "color": "green"}])
    [f] = findings(sales, rule="narrative.kpi-thresholds")
    assert "green above 10,000,000" in f.detail and "e+" not in f.detail
    for stated in ("Green above $10M", "Green above 10,000,000", "Green above 10000k"):
        assert findings(sales | {"subtitle": stated}, rule="narrative.kpi-thresholds") == [], stated
