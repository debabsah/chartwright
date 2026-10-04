"""Design defaults: the default.* fills `advise --fix` writes into the spec
(docs/DESIGN-BRAIN.md sec.16). Each rule fires where its conditions hold and
nowhere else; never touches a field the author wrote; keeps its own fills
(design.filled) up to date; never writes Superset's own default. The fix loop
fills after repairs settle, is idempotent, and never ratchets size.table-window.
A spec's design block never reaches the bundle."""

import json
import math
from pathlib import Path

import pytest
from pydantic import ValidationError

from chartwright.compiler import compile_bundle
from chartwright.design import advise, advise_and_fix
from chartwright.design.defaults import FILLS
from chartwright.design.explain import explain, render_text
from chartwright.design.presets import Overlay
from chartwright.resolver import ResolvedDataset, Resolution
from chartwright.spec import BRAIN_FILLABLE_FIELDS, grid_rows_visible, load_spec
from chartwright.testing import stub_resolution

DS = {"database": "db", "table": "orders"}
EMPTY = Overlay()  # tests never read ~/.config/chartwright
FIXTURES = sorted((Path(__file__).parent / "fixtures").glob("*.json"))


def mk(charts, layout=None, design=None, filters=None):
    data = {"spec_version": "1", "dashboard": {"title": "T", "slug": "t"},
            "charts": charts, "layout": layout or {"rows": [[c["name"]] for c in charts]}}
    if design is not None:
        data["design"] = design
    if filters is not None:
        data["filters"] = filters
    return data


def line(name="L", **kw):
    return {"type": "timeseries_line", "name": name, "dataset": DS, "metrics": ["SUM(x)"],
            "time_column": "ts", "height": 8, **kw}


def trend(name="K", **kw):
    return {"type": "big_number_trend", "name": name, "dataset": DS, "metric": "SUM(x)",
            "time_column": "ts", "number_format": ",.0f", **kw}


def bar(name="B", **kw):
    return {"type": "bar", "name": name, "dataset": DS, "x_column": "region",
            "metrics": ["SUM(x)"], "height": 8, **kw}


def raw_table(name="T", **kw):
    return {"type": "table", "name": name, "dataset": DS,
            "columns": ["customer", "amount"], "height": 8, **kw}


def fills(data, rule=None, **kw):
    """{chart: finding} for one default.* rule (or every fill if rule is None)."""
    kw.setdefault("overlay", EMPTY)
    rep = advise(load_spec(data), **kw)
    return {f.chart: f for f in rep.findings
            if f.kind == "fill" and (rule is None or f.rule == rule)}


def filled_value(data, rule, chart="L"):
    f = fills(data, rule).get(chart)
    return None if f is None else f.fix.get("set", {}).get(FILLS[rule].field)


def fix(data, **kw):
    kw.setdefault("overlay", EMPTY)
    return advise_and_fix(data, **kw)


# -- the registry ------------------------------------------------------------------


def test_every_fill_is_info_fixable_and_governs_a_fillable_field():
    from chartwright.design.model import RULES

    assert {f.field for f in FILLS.values()} == set(BRAIN_FILLABLE_FIELDS)
    for rid, fill in FILLS.items():
        r = RULES[rid]
        assert rid.startswith("default.") and r.severity == "info" and r.fixable
        assert r.since == "5"


def test_no_fill_can_write_superset_own_default():
    for fill in FILLS.values():
        assert not fill.could_write(fill.superset), fill.rule


def test_a_decision_equal_to_superset_default_is_never_written():
    """The guard in the shared driver, not each rule's care: a rule that decided
    Superset's own value (show_value false) produces no finding at all."""
    from dataclasses import replace

    from chartwright.design.defaults import _findings
    from chartwright.design.model import RuleContext
    from chartwright.design.presets import params_for

    fill = replace(FILLS["default.value-labels"], decide=lambda ctx, c: (False, "Superset's own"))
    ctx = RuleContext(load_spec(mk([bar(row_limit=5)])), params_for("analytical"))
    assert list(_findings(ctx, fill)) == []


# -- default.x-label-format ---------------------------------------------------------


@pytest.mark.parametrize("grain,time_range,want", [
    ("P1M", None, "%b %Y"),
    ("P1Y", None, "%Y"),
    ("P1D", "Last 90 days", "%d %b"),
    ("1969-12-28T00:00:00Z/P1W", "Last 30 weeks", "%d %b"),
    ("P1D", None, None),               # no time_range: day labels would drop the year
    ("P1D", "Last 2 years", None),     # more than a year: same
    ("P1D", "No filter", None),        # not a span the brain can read
    ("P3M", None, None),               # no one format for a quarter
    (None, "Last 30 days", "%d %b"),   # an omitted grain is the P1D default
])
def test_x_label_format_follows_the_grain(grain, time_range, want):
    kw = {k: v for k, v in (("time_grain", grain), ("time_range", time_range)) if v}
    assert filled_value(mk([line(**kw)]), "default.x-label-format") == want


def test_x_label_format_only_on_time_axes():
    data = mk([bar(), {"type": "mixed", "name": "M", "dataset": DS,
               "x_column": "ts", "time_grain": "P1M", "a": {"metrics": ["SUM(x)"]},
               "b": {"metrics": ["SUM(y)"], "kind": "line"}}])
    assert fills(data, "default.x-label-format") == {}


def test_x_label_format_written_by_the_author_is_left_alone():
    assert fills(mk([line(time_grain="P1M", x_label_format="%B")]),
                 "default.x-label-format") == {}


def test_x_label_format_respects_the_overlay_span():
    data = mk([line(time_grain="P1D", time_range="Last 90 days")])
    ov = Overlay(params={"day_label_max_span_days": 60})
    assert fills(data, "default.x-label-format", overlay=ov) == {}


# -- default.compare-suffix ------------------------------------------------------------


@pytest.mark.parametrize("grain,lag,want", [
    ("P1M", 1, "vs previous month"),
    ("P1M", 12, "vs 12 months earlier"),
    ("P1W", 1, "vs previous week"),
    (None, 1, "vs previous day"),
    ("P3M", 4, "vs 4 quarters earlier"),
])
def test_compare_suffix_names_the_comparison(grain, lag, want):
    kw = {"compare_lag": lag, **({"time_grain": grain} if grain else {})}
    assert filled_value(mk([trend(**kw)]), "default.compare-suffix", "K") == want
    assert FILLS["default.compare-suffix"].could_write(want)


def test_compare_suffix_needs_compare_lag_and_respects_the_author():
    assert fills(mk([trend()]), "default.compare-suffix") == {}
    assert fills(mk([trend(compare_lag=1, compare_suffix="MoM")]),
                 "default.compare-suffix") == {}


# -- default.count-format ------------------------------------------------------------


@pytest.mark.parametrize("chart,want", [
    (line(metrics=["COUNT(*)"]), ",.0f"),
    (line(metrics=["COUNT_DISTINCT(user) AS Users"]), ",.0f"),
    (line(metrics=["COUNT(*)", "COUNT(id)"]), ",.0f"),
    (line(metrics=["COUNT(*)", "SUM(x)"]), None),           # one format, two units
    (line(metrics=["revenue"]), None),                      # a saved metric: unknown
    (line(metrics=["COUNT(*)"], contribution="row"), None),  # shares
    (line(metrics=["COUNT(*)"], number_format=".3s"), None),  # the author's
])
def test_count_format(chart, want):
    assert filled_value(mk([chart]), "default.count-format") == want


def test_count_format_on_single_format_charts_only():
    kpi = {"type": "big_number_total", "name": "K", "dataset": DS, "metric": "COUNT(*)"}
    pivot = {"type": "pivot_table", "name": "P", "dataset": DS, "rows": ["r"],
             "metrics": ["COUNT(*)"]}
    avg = {**pivot, "name": "A", "aggregate_function": "Average"}
    table = {"type": "table", "name": "T", "dataset": DS, "groupby": ["r"], "metrics": ["COUNT(*)"]}
    area = {"type": "timeseries_area", "name": "E", "dataset": DS, "metrics": ["COUNT(*)"],
            "time_column": "ts", "stack": "expand"}
    got = fills(mk([kpi, pivot, avg, table, area]), "default.count-format")
    assert set(got) == {"K", "P"}  # pivot Average and 100% stacks plot fractions; tables have number_formats


def test_count_format_replaces_the_big_number_nudge():
    kpi = {"type": "big_number_total", "name": "K", "dataset": DS, "metric": "COUNT(*)"}
    other = {**kpi, "name": "S", "metric": "SUM(x)"}
    rep = advise(load_spec(mk([kpi, other])), overlay=EMPTY)
    nudges = {f.chart for f in rep.findings if f.rule == "narrative.big-number-format"}
    assert nudges == {"S"}  # one remedy, one finding: K gets the fixable fill instead


# -- default.cell-bars -----------------------------------------------------------------


@pytest.mark.parametrize("columns,want", [
    (["order_id", "amount"], False),
    (["id", "amount"], False),
    (["country_code", "amount"], False),
    (["fiscal_year", "amount"], False),
    (["zip", "amount"], False),
    (["customer", "amount"], None),
    (["uuid", "yearly_total", "zip_count"], None),  # whole-token matches only
])
def test_cell_bars_by_column_name(columns, want):
    assert filled_value(mk([raw_table(columns=columns)]), "default.cell-bars", "T") == want


def test_cell_bars_skips_aggregate_tables_and_author_choice():
    agg = {"type": "table", "name": "A", "dataset": DS, "groupby": ["order_id"],
           "metrics": ["SUM(x)"]}
    kept = raw_table(name="K", columns=["order_id", "amount"], cell_bars=True)  # Superset's own
    assert fills(mk([agg, kept]), "default.cell-bars") == {}


def test_cell_bars_with_types_needs_a_numeric_identifier():
    def res(zip_type):
        return Resolution(datasets={"db//orders": ResolvedDataset(
            id=1, uuid="u", table="orders", schema=None, database_name="db",
            columns=["zip", "amount"], metrics=[], column_types={"zip": zip_type, "amount": 0})})

    data = mk([raw_table(columns=["zip", "amount"])])
    assert fills(data, "default.cell-bars", resolution=res(1)) == {}  # a string zip draws no bar
    assert "T" in fills(data, "default.cell-bars", resolution=res(0))


# -- default.page-length -----------------------------------------------------------------


@pytest.mark.parametrize("row_limit,height,want", [
    (400, 8, 5),     # 6.67 rows fit -> 6 whole rows, one for the pager
    (400, 11, 9),
    (20, 9, 7),
    (6, 8, None),    # every row fits
    (400, 5, None),  # 2 rows fit: too few to page
])
def test_page_length_pages_by_whole_rows(row_limit, height, want):
    data = mk([raw_table(row_limit=row_limit, height=height)])
    assert filled_value(data, "default.page-length", "T") == want


def test_page_length_needs_an_explicit_row_limit():
    assert fills(mk([raw_table()]), "default.page-length") == {}


@pytest.mark.parametrize("row_limit", [10, 20, 50, 400])
@pytest.mark.parametrize("height", range(6, 21))
def test_page_fill_never_ratchets_table_window(row_limit, height):
    """C#5 reproduced a ratchet: page = rows that fit, then size.table-window asked
    for one more row for the pager, then the page grew... One grid formula now:
    whatever the fix loop settles on advises clean, and a second run is a no-op."""
    data = mk([raw_table(row_limit=row_limit, height=height)])
    fixed, rep = fix(data)
    assert not any(f.rule in ("size.table-window", "design.fix-stalled") for f in rep.findings)
    assert not any(f.fix for f in rep.findings)
    again, rep2 = fix(fixed)
    assert again == fixed and rep2.fixed == []
    t = fixed["charts"][0]
    if "page_length" in t:
        assert t["page_length"] == math.floor(grid_rows_visible(t["height"])) - 1


def test_fills_wait_for_geometry_repairs():
    """A page computed from the pre-repair height would go stale the same run."""
    data = mk([raw_table(row_limit=20, height=5)])
    fixed, rep = fix(data)
    kinds = [e["kind"] for e in rep.fixed]
    assert kinds.index("fill") > kinds.index("repair")
    t = fixed["charts"][0]
    assert t["height"] > 5
    assert t["page_length"] == math.floor(grid_rows_visible(t["height"])) - 1


# -- default.search-box ----------------------------------------------------------------


def test_search_box_on_long_raw_tables():
    data = mk([raw_table(name="Big", row_limit=50), raw_table(name="Small", row_limit=20),
               raw_table(name="Unset"),
               {"type": "table", "name": "Agg", "dataset": DS, "groupby": ["r"],
                "metrics": ["SUM(x)"], "row_limit": 500}])
    assert set(fills(data, "default.search-box")) == {"Big"}


def test_search_threshold_comes_from_the_audience_params():
    data = mk([raw_table(row_limit=50)])
    assert fills(data, "default.search-box", overlay=Overlay(params={"search_min_rows": 100})) == {}


# -- default.single-series-legend -----------------------------------------------------


@pytest.mark.parametrize("chart,want", [
    (line(name="Daily Revenue", metrics=["SUM(fare) AS Revenue"]), False),
    (line(name="Daily", metrics=["SUM(fare) AS Revenue"], y_axis_title="Revenue ($)"), False),
    (line(name="x", display_name="Revenue", metrics=["SUM(fare) AS Revenue"]), False),
    (line(name="Daily trips", metrics=["SUM(fare) AS Revenue"]), None),  # title names another thing
    (line(name="Revenue", metrics=["SUM(fare) AS Revenue"], groupby="region"), None),
    (line(name="Revenue", metrics=["SUM(fare) AS Revenue", "SUM(tip) AS Tips"]), None),
    (line(name="Revenue", metrics=["SUM(fare) AS Revenue"],
          annotations=[{"name": "Goal", "value": 5}]), None),
    (line(name="Revenue", metrics=["SUM(fare) AS Revenue"], legend_position="bottom"), None),
    (line(name="Revenue", metrics=["SUM(fare) AS Revenue"], show_legend=True), None),  # written
])
def test_single_series_legend(chart, want):
    data = mk([chart])
    assert filled_value(data, "default.single-series-legend", chart["name"]) == want


def test_single_series_legend_never_on_a_heatmap():
    heat = {"type": "heatmap", "name": "Trips", "dataset": DS, "x_column": "a",
            "y_column": "b", "metric": "COUNT(*) AS Trips", "width": 12}
    assert fills(mk([heat]), "default.single-series-legend") == {}


# -- default.value-labels ------------------------------------------------------------------


@pytest.mark.parametrize("kw,want", [
    ({"row_limit": 10, "width": 12}, True),
    ({"row_limit": 12, "width": 6}, True),
    ({"row_limit": 13, "width": 12}, None),
    ({"row_limit": 10, "width": 5}, None),
    ({"width": 12}, None),                                   # bar count unknown
    ({"row_limit": 10, "width": 12, "groupby": "g"}, None),  # several series
    ({"row_limit": 10, "width": 12, "show_value": False}, None),  # the author's
])
def test_value_labels(kw, want):
    width = kw.pop("width")
    other = {"type": "big_number_total", "name": "K", "dataset": DS, "metric": "SUM(x)",
             "number_format": ",.0f"}
    if width == 12:
        data = mk([bar(**kw)])
    else:
        data = mk([bar(width=width, **kw), {**other, "width": 12 - width}],
                  layout={"rows": [["B", "K"]]})
    assert filled_value(data, "default.value-labels", "B") == want


# -- provenance: design.filled -------------------------------------------------------------


def test_fix_records_its_fills_and_a_second_run_is_a_no_op():
    data = mk([line(time_grain="P1M", metrics=["COUNT(*)"])])
    fixed, rep = fix(data)
    assert fixed["charts"][0]["x_label_format"] == "%b %Y"
    assert fixed["charts"][0]["number_format"] == ",.0f"
    assert fixed["design"] == {"filled": {"L": ["number_format", "x_label_format"]}}
    entry = next(e for e in rep.fixed if e["rule"] == "default.x-label-format")
    assert entry["kind"] == "fill" and entry["was"] == {"x_label_format": None}
    assert entry["set"] == {"x_label_format": "%b %Y"} and "P1M" in entry["why"]
    again, rep2 = fix(fixed)
    assert again == fixed and rep2.fixed == []
    assert not any(f.rule.startswith("default.") for f in rep2.findings)


def test_a_filled_field_follows_the_chart():
    """The brain refreshes its own fill when the inputs move (here the grain)."""
    data = mk([line(time_grain="P1Y", x_label_format="%b %Y")],
              design={"filled": {"L": ["x_label_format"]}})
    fixed, rep = fix(data)
    assert fixed["charts"][0]["x_label_format"] == "%Y"
    assert rep.fixed[0]["was"] == {"x_label_format": "%b %Y"}


def test_a_fill_that_no_longer_applies_is_removed():
    """C#10: a groupby added later must bring the legend back, not leave a
    brain-hidden legend that looks like the author's choice."""
    data = mk([line(name="Revenue", metrics=["SUM(x) AS Revenue"], groupby="region",
                    show_legend=False)], design={"filled": {"Revenue": ["show_legend"]}})
    fixed, rep = fix(data)
    assert "show_legend" not in fixed["charts"][0] and "design" not in fixed
    assert rep.fixed[0]["set"] == {"show_legend": None}
    assert rep.fixed[0]["was"] == {"show_legend": False}


def test_an_edit_the_brain_would_never_write_makes_the_field_the_authors():
    data = mk([raw_table(row_limit=400, page_length=0)],
              design={"filled": {"T": ["page_length"]}})
    fixed, rep = fix(data)
    assert fixed["charts"][0]["page_length"] == 0
    assert "page_length" not in fixed["design"]["filled"]["T"]  # search_box is filled, though
    entry = next(e for e in rep.fixed if e["rule"] == "default.page-length")
    assert entry["set"] == {} and entry["kind"] == "fill"
    # ...and from then on the brain leaves it alone.
    assert fills(fixed, "default.page-length") == {}


def test_a_listed_value_the_brain_could_write_stays_the_brains():
    """Documented limit: an edit to another value the rule itself produces is
    indistinguishable from a stale fill. Taking it over means delisting it."""
    data = mk([raw_table(row_limit=400, page_length=10, search_box=False)],
              design={"filled": {"T": ["page_length"]}})
    fixed, rep = fix(data)
    # The stale page follows the panel; the height does not grow to fit it.
    assert fixed["charts"][0]["page_length"] == 5 and fixed["charts"][0]["height"] == 8
    assert [e["kind"] for e in rep.fixed] == ["fill"]
    del data["design"]
    kept, rep = fix(data)
    assert kept["charts"][0]["page_length"] == 10  # the author's: table-window raises the height
    assert rep.fixed and all(e["kind"] == "repair" for e in rep.fixed)


def test_a_brain_page_never_silences_table_window():
    """The v2 echo chamber (roadmap item 2): brain output must not blind a rule. A
    stale filled page that no longer fits is still REPORTED; only the height fix is
    withheld, because the fill refits the page instead. With the fill ignored the
    warning stays, visible."""
    data = mk([raw_table(row_limit=400, page_length=10, search_box=False)],
              design={"filled": {"T": ["page_length"]}, "ignore": ["default.page-length@T"]})
    rep = advise(load_spec(data), overlay=EMPTY)
    tw = next(f for f in rep.findings if f.rule == "size.table-window")
    assert tw.fix is None and "design default" in tw.detail
    fixed, _ = fix(data)
    assert fixed["charts"][0] == data["charts"][0]  # nothing moves the height for it


def test_fills_keep_the_fix_loop_convergence_invariant():
    """v2 roadmap item 15: no two fixable rules may fight over one field. Fills never
    touch geometry (height, width, orientation), each fills a field no other rule
    writes, and the loop never stalls."""
    fill_fields, repair_fields = {}, {}
    specs = [json.loads(p.read_text(encoding="utf-8")) for p in FIXTURES]
    specs += [mk([raw_table(row_limit=rl, height=h, columns=["order_id", "amount"])])
              for rl in (5, 30, 400) for h in (3, 8, 14)]
    specs += [mk([line(name="Revenue", metrics=["COUNT(*) AS Revenue"], time_grain="P1M",
                       height=3), bar(row_limit=10, height=4)])]
    for data in specs:
        rep = advise(load_spec(data), overlay=EMPTY)
        for f in rep.findings:
            if f.fix and "chart" in f.fix:
                into = fill_fields if f.kind == "fill" else repair_fields
                for k in [*f.fix.get("set", {}), *f.fix.get("unset", ())]:
                    into.setdefault(k, set()).add(f.rule)
        _, done = fix(data)
        assert not any(f.rule == "design.fix-stalled" for f in done.findings)
    assert fill_fields and repair_fields
    assert not set(fill_fields) & set(repair_fields), (fill_fields, repair_fields)
    assert not set(fill_fields) & {"height", "width", "orientation"}
    assert all(len(rules) == 1 for rules in fill_fields.values()), fill_fields


def test_fills_apply_to_sketch_charts_and_leave_the_drawing_true():
    """v2 roadmap item 34 (sketch WYSIWYG): fills write no geometry, so a sketch-drawn
    chart takes them and the drawing still says what the dashboard shows."""
    data = mk([line(name="L", time_grain="P1M", metrics=["COUNT(*)"]),
               raw_table(name="T", row_limit=400, columns=["order_id", "amount"])],
              layout={"sketch": ["LLLLLLLLLLLL", "LLLLLLLLLLLL", "LLLLLLLLLLLL", "LLLLLLLLLLLL",
                                 "TTTTTTTTTTTT", "TTTTTTTTTTTT", "TTTTTTTTTTTT", "TTTTTTTTTTTT"],
                      "legend": {"L": "L", "T": "T"}})
    for c in data["charts"]:
        c.pop("height")
    fixed, rep = fix(data)
    assert fixed["layout"] == data["layout"]
    assert all("height" not in c and "width" not in c for c in fixed["charts"])
    assert {e["rule"] for e in rep.fixed} >= {"default.x-label-format", "default.page-length"}
    t = next(c for c in fixed["charts"] if c["name"] == "T")
    sketch_height = load_spec(fixed).resolved_height("T")
    assert t["page_length"] == math.floor(grid_rows_visible(sketch_height)) - 1


def test_an_unlisted_written_field_is_never_touched_even_at_superset_default():
    data = mk([line(name="Revenue", metrics=["SUM(x) AS Revenue"], show_legend=True,
                    time_grain="P1M", x_label_format="%B", number_format=".3s")])
    assert fills(data) == {}


def test_a_listed_field_the_chart_dropped_leaves_the_list():
    data = mk([line(metrics=["SUM(x)"])], design={"filled": {"L": ["number_format"]}})
    fixed, rep = fix(data)
    assert "design" not in fixed and rep.fixed[0]["set"] == {}


def test_ignore_keeps_a_field_unset():
    data = mk([line(time_grain="P1M")], design={"ignore": ["default.x-label-format@L"]})
    fixed, rep = fix(data)
    assert "x_label_format" not in fixed["charts"][0]
    assert "default.x-label-format@L" in rep.ignored


@pytest.mark.parametrize("filled,match", [
    ({"Nope": ["x_label_format"]}, "not in charts"),
    ({"L": ["height"]}, "not fields the design brain fills"),
    ({"L": ["cell_bars"]}, "has no"),
    ({"L": ["x_label_format", "x_label_format"]}, "twice"),
])
def test_design_filled_is_validated(filled, match):
    with pytest.raises(ValidationError, match=match):
        load_spec(mk([line()], design={"filled": filled}))


# -- compile never sees the design block ----------------------------------------------------


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_a_fixed_spec_compiles_like_the_same_fields_written_by_hand(path):
    """design.filled is provenance for the brain only: the bundle of a fixed spec is
    the bundle of the same fields with no design block at all."""
    fixed, _ = fix(json.loads(path.read_text(encoding="utf-8")))
    assert "filled" in (fixed.get("design") or {}), "every fixture has a fill to make"
    plain = {k: v for k, v in fixed.items() if k != "design"}
    a, b = load_spec(fixed), load_spec(plain)
    assert compile_bundle(a, stub_resolution(a)) == compile_bundle(b, stub_resolution(b))


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixing_a_fixture_converges_and_is_idempotent(path):
    fixed, rep = fix(json.loads(path.read_text(encoding="utf-8")))
    assert not any(f.fix for f in rep.findings)
    assert not any(f.rule in ("design.fix-stalled", "size.table-window") for f in rep.findings
                   if f.chart in (fixed.get("design") or {}).get("filled", {}))
    again, rep2 = fix(fixed)
    assert again == fixed and rep2.fixed == []
    load_spec(fixed)


# -- surfaces ----------------------------------------------------------------------------------


def test_repairs_say_so_too():
    data = mk([line(height=3, number_format=",.0f")])
    _, rep = fix(data)
    entry = next(e for e in rep.fixed if e["rule"] == "size.axis-min-height")
    assert entry["kind"] == "repair" and "flattened" in entry["why"]


def test_check_and_apply_advice_lists_available_fills(monkeypatch, tmp_path):
    monkeypatch.setenv("CHARTWRIGHT_DESIGN_DIR", str(tmp_path))
    from chartwright.cli import _advice_payload, _design_blocks

    advice = _advice_payload(load_spec(mk([line(time_grain="P1M", number_format=",.0f")])))
    f = next(f for f in advice["findings"] if f["rule"] == "default.x-label-format")
    assert f["severity"] == "info" and f["fixable"] is True
    assert _design_blocks(advice) is None  # an info fill never blocks --design strict


def test_redesign_writes_no_fills():
    from chartwright.design.redesign import redesign_spec

    data = mk([line(time_grain="P1M", height=3, number_format=",.0f")])
    new, payload = redesign_spec(data, [], owned=True, overlay=EMPTY)
    assert "x_label_format" not in new["charts"][0] and new["charts"][0]["height"] >= 6
    assert all(e["kind"] == "repair" for e in payload["advice"]["fixed"])
    assert "advise --fix" in payload["next"]


def _cli(argv, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CHARTWRIGHT_DESIGN_DIR", str(tmp_path))
    from chartwright.cli import main

    code = 0
    try:
        main(argv)
    except SystemExit as e:
        code = e.code or 0
    return code, capsys.readouterr().out


def test_advise_chart_filters_findings_and_fixes(monkeypatch, tmp_path, capsys):
    spec = tmp_path / "s.json"
    spec.write_text(json.dumps(mk([line("A", time_grain="P1M", number_format=",.0f"),
                                   line("B", time_grain="P1M", number_format=",.0f")])))
    code, out = _cli(["advise", str(spec), "--chart", "A"], monkeypatch, tmp_path, capsys)
    assert code == 0 and {f["chart"] for f in json.loads(out)["findings"]} == {"A"}
    code, out = _cli(["advise", str(spec), "--chart", "A", "--fix"], monkeypatch, tmp_path, capsys)
    written = json.loads(spec.read_text())
    assert written["charts"][0]["x_label_format"] == "%b %Y"
    assert "x_label_format" not in written["charts"][1]
    code, out = _cli(["advise", str(spec), "--chart", "Nope"], monkeypatch, tmp_path, capsys)
    assert code == 1 and json.loads(out)["errors"][0]["code"] == "unknown_chart"


def test_explain_shows_every_governed_field_and_its_source(monkeypatch, tmp_path, capsys):
    data = mk([line("A", time_grain="P1M", metrics=["COUNT(*)"], x_label_format="%b %Y",
                    show_legend=True)], design={"filled": {"A": ["x_label_format"]}})
    payload = explain(load_spec(data), overlay=EMPTY)
    rows = {r["field"]: r for r in payload["charts"][0]["fields"]}
    assert set(rows) == {"x_label_format", "number_format", "show_legend"}
    assert rows["x_label_format"]["source"] == "filled"
    assert rows["x_label_format"]["value"] == "%b %Y" and "P1M" in rows["x_label_format"]["reason"]
    assert rows["show_legend"]["source"] == "spec" and rows["show_legend"]["value"] is True
    assert rows["number_format"]["source"] == "superset default"
    assert rows["number_format"]["reason"].startswith("advise --fix fills ',.0f'")
    assert all(r["rule"].startswith("default.") and r["override"] for r in rows.values())
    text = render_text(payload)
    assert "A  (timeseries_line)" in text and "default.count-format" in text

    spec = tmp_path / "s.json"
    spec.write_text(json.dumps(data))
    code, out = _cli(["explain", str(spec), "--json", "--chart", "A"], monkeypatch, tmp_path, capsys)
    assert code == 0 and json.loads(out)["charts"][0]["chart"] == "A"
    code, out = _cli(["explain", str(spec)], monkeypatch, tmp_path, capsys)
    assert code == 0 and out.startswith("Design defaults (design brain 5")
    code, out = _cli(["explain", str(spec), "--chart", "Z"], monkeypatch, tmp_path, capsys)
    assert code == 1 and "unknown_chart" in out


def test_explain_is_deterministic_and_offline():
    data = json.loads(FIXTURES[0].read_text(encoding="utf-8"))
    assert explain(load_spec(data), overlay=EMPTY) == explain(load_spec(data), overlay=EMPTY)


def test_mcp_fix_spec_reports_fills(monkeypatch, tmp_path):
    pytest.importorskip("mcp")
    import asyncio

    from chartwright.mcp_server import mcp

    monkeypatch.setenv("CHARTWRIGHT_DESIGN_DIR", str(tmp_path))
    out = asyncio.run(mcp.call_tool("fix_spec", {"spec_json": json.dumps(
        mk([line(time_grain="P1M", number_format=",.0f")]))}))
    # mcp 2 returns a CallToolResult, mcp 1 a content list or a (content, structured) pair
    text = (out.content[0].text if hasattr(out, "content")
            else out[0][0].text if isinstance(out, tuple) else out[0].text)
    payload = json.loads(text)
    entry = payload["advice"]["fixed"][0]
    assert entry["kind"] == "fill" and entry["why"]
    assert payload["spec"]["design"]["filled"] == {"L": ["x_label_format"]}


def test_brief_tells_the_author_to_leave_design_defaults_unset():
    from chartwright.design.brief import render_brief

    text = render_brief("analytical", overlay=EMPTY)
    assert "leave them unset" in text and "design.filled" in text and "regenerate" in text and '"fill"' in text
