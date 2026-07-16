"""The design brain, Tier L: every offline rule fires on a violating spec and
stays silent on a clean one; fixes converge and are idempotent; toggles
(ignore, audience, overlay, fractional heights) behave."""

import json

import pytest
from pydantic import ValidationError

from chartwright.design import advise, advise_and_fix
from chartwright.design.brief import render_brief
from chartwright.design.model import RULES
from chartwright.design.presets import AUDIENCE_NAMES, Overlay, params_for
from chartwright.spec import DesignConfig, load_spec

DS = {"database": "db", "table": "orders"}
EMPTY = Overlay()  # tests never read ~/.config/chartwright


def kpi(name, **kw):
    return {"type": "big_number_total", "name": name, "dataset": DS,
            "metric": "COUNT(*)", "number_format": ",.0f", **kw}


def line(name, **kw):
    return {"type": "timeseries_line", "name": name, "dataset": DS,
            "metrics": ["COUNT(*)"], "time_column": "ts", "height": 8, **kw}


def hbar(name, **kw):
    return {"type": "bar", "name": name, "dataset": DS, "x_column": "product",
            "metrics": ["COUNT(*)"], "orientation": "horizontal",
            "row_limit": 10, "height": 8, **kw}


def mk(charts, layout=None, filters=None, design=None):
    data = {
        "spec_version": "1",
        "dashboard": {"title": "T", "slug": "t"},
        "charts": charts,
        "layout": layout or {"rows": [[c["name"]] for c in charts]},
    }
    if filters is not None:
        data["filters"] = filters
    if design is not None:
        data["design"] = design
    return data


def run(data, **kw):
    kw.setdefault("overlay", EMPTY)
    return advise(load_spec(data), **kw)


def rules_fired(data, **kw):
    return {f.rule for f in run(data, **kw).findings}


# -- a clean spec is silent ----------------------------------------------------


def clean_spec():
    return mk(
        [kpi("Total Orders"), kpi("Total Sales", metric="SUM(amount)"),
         kpi("Average Order Value", metric="AVG(amount)"),
         line("Orders Over Time"), hbar("Top Products")],
        layout={"rows": [
            ["Total Orders", "Total Sales", "Average Order Value"],
            ["Orders Over Time"],
            ["Top Products"],
        ]},
        filters=[{"type": "time_range", "name": "Window"}],
    )


def test_clean_spec_advises_clean():
    rep = run(clean_spec())
    assert rep.findings == []
    assert rep.ok and rep.counts == {"error": 0, "warn": 0, "info": 0}


# -- size rules ------------------------------------------------------------------


def test_axis_min_height_fires_and_fixes():
    rep = run(mk([kpi("A"), kpi("B"), line("L", height=3)]))
    f = next(f for f in rep.findings if f.rule == "size.axis-min-height")
    assert f.chart == "L" and f.fix["set"]["height"] == 6  # analytical minimum


def test_kpi_height_out_of_band():
    assert "size.kpi-height" in rules_fired(mk([kpi("A", height=10), kpi("B")]))
    assert "size.kpi-height" not in rules_fired(mk([kpi("A", height=4), kpi("B")]))


def test_pie_geometry_splits_width_and_height():
    pie = {"type": "pie", "name": "P", "dataset": DS, "metric": "COUNT(*)",
           "groupby": "region", "row_limit": 5, "width": 3, "height": 4}
    rep = run(mk([pie], layout={"rows": [["P"]]}))
    fs = [f for f in rep.findings if f.rule == "size.pie-geometry"]
    assert len(fs) == 2
    width_f = next(f for f in fs if "width 3/12" in f.detail)
    height_f = next(f for f in fs if "height 4" in f.detail)
    assert width_f.fix is None and height_f.fix["set"]["height"] == 8
    # a human-polished height silences ONLY the height complaint
    pie["height"] = 4.2
    fs = [f for f in run(mk([pie], layout={"rows": [["P"]]})).findings
          if f.rule == "size.pie-geometry"]
    assert len(fs) == 1 and "width 3/12" in fs[0].detail


def test_heatmap_geometry_and_table_window_and_hbar_window():
    heat = {"type": "heatmap", "name": "H", "dataset": DS, "metric": "COUNT(*)",
            "x_column": "a", "y_column": "b", "height": 4}
    table = {"type": "table", "name": "T", "dataset": DS, "groupby": ["a"],
             "metrics": ["COUNT(*)"], "row_limit": 500, "height": 6}
    bar = hbar("B", row_limit=30, height=8)
    fired = rules_fired(mk([heat, table, bar]))
    assert {"size.heatmap-geometry", "size.table-window", "size.hbar-window"} <= fired


def test_row_harmony_fires_and_excludes_kpis():
    data = mk([line("L", height=8), hbar("R", height=4)],
              layout={"rows": [["L", "R"]]})
    rep = run(data)
    f = next(f for f in rep.findings if f.rule == "size.row-harmony")
    assert f.chart == "R" and f.fix["set"]["height"] == 8
    # KPI beside a tall chart: mixed-band warning, but never row-harmony
    data = mk([kpi("K"), line("L")], layout={"rows": [["K", "L"]]})
    fired = rules_fired(data)
    assert "size.row-harmony" not in fired and "layout.kpi-band" in fired


def test_fractional_height_suppresses_size_rules():
    data = mk([kpi("A"), kpi("B"), line("L", height=3.4)])  # absorb's signature
    assert not any(f.rule.startswith("size.") for f in run(data).findings)


# -- layout rules -----------------------------------------------------------------


def test_kpi_first_and_lonely_kpi():
    data = mk([line("L"), kpi("K")], layout={"rows": [["L"], ["K"]]})
    fired = rules_fired(data)
    assert {"layout.kpi-first", "layout.kpi-band"} <= fired


def test_kpi_band_counts():
    many = [kpi(f"K{i}") for i in range(7)]
    data = mk(many, layout={"rows": [[c["name"] for c in many]]})
    assert "layout.kpi-band" in rules_fired(data)
    md_row = [{"markdown": "## Section"}, "K0"]
    data = mk([kpi("K0")], layout={"rows": [md_row]})
    assert "layout.kpi-band" not in rules_fired(data)  # markdown pairing is fine


def test_row_density_error_when_starved():
    charts = [line(f"L{i}", width=2, height=8) for i in range(6)]
    data = mk(charts, layout={"rows": [[c["name"] for c in charts]]})
    f = next(f for f in run(data).findings if f.rule == "layout.row-density")
    assert f.severity == "error"


def test_row_fill_and_orphan():
    data = mk([line("L", width=6)], layout={"rows": [["L"]]})
    fired = rules_fired(data)
    assert {"layout.row-fill", "layout.orphan-chart"} <= fired


def test_fold_budget_executive():
    charts = [line(f"L{i}", height=8) for i in range(4)]
    data = mk(charts)
    assert "layout.fold-budget" in rules_fired(data, audience="executive")
    assert "layout.fold-budget" not in rules_fired(data, audience="analytical")


def test_tab_balance_and_sketch_normalization():
    charts = [line(f"L{i}") for i in range(6)]
    charts[5].pop("height")  # geometry must come from the sketch drawing
    tabs = [
        {"title": "Deep", "rows": [[c["name"]] for c in charts[:5]]},
        {"title": "Thin", "sketch": ["XXXXXXXXXXXX", "XXXXXXXXXXXX"],
         "legend": {"X": "L5"}},
    ]
    data = mk(charts, layout={"tabs": tabs})
    rep = run(data)
    assert "layout.tab-balance" in {f.rule for f in rep.findings}
    # the sketch tab normalized: L5 got geometry (12 wide, 4 units tall)
    assert "size.axis-min-height" in {f.rule for f in rep.findings if f.chart == "L5"}


def test_section_headers():
    charts = [line(f"L{i}") for i in range(9)]
    assert "layout.section-headers" in rules_fired(mk(charts))


# -- chart / data / narrative rules ------------------------------------------------


def test_vbar_categories_fix_flips_orientation():
    bar = {"type": "bar", "name": "B", "dataset": DS, "x_column": "product",
           "metrics": ["COUNT(*)"], "row_limit": 12, "height": 8}
    rep = run(mk([bar]))
    f = next(f for f in rep.findings if f.rule == "chart.vbar-categories")
    assert f.fix["set"]["orientation"] == "horizontal"
    bar["row_limit"] = 40  # too many even horizontal: warn only
    rep = run(mk([bar]))
    f = next(f for f in rep.findings if f.rule == "chart.vbar-categories")
    assert f.fix is None


def test_pie_slices_metrics_bins_treemap_dupe_intent():
    pie = {"type": "pie", "name": "P", "dataset": DS, "metric": "COUNT(*)",
           "groupby": "region", "width": 6, "height": 8}
    multi = {"type": "bar", "name": "M", "dataset": DS, "x_column": "a", "height": 8,
             "metrics": ["SUM(a)", "SUM(b)", "SUM(c)", "SUM(d)"], "orientation": "horizontal",
             "row_limit": 10}
    hist = {"type": "histogram", "name": "H", "dataset": DS, "column": "x",
            "bins": 5, "height": 8}
    tree = {"type": "treemap", "name": "TM", "dataset": DS, "metric": "COUNT(*)",
            "groupby": ["a", "b", "c"], "height": 8}
    t1 = {"type": "table", "name": "T1", "dataset": DS, "columns": ["a"], "height": 8}
    t2 = {"type": "table", "name": "T2", "dataset": DS, "columns": ["a"], "height": 8}
    fired = rules_fired(mk([pie, multi, hist, tree, t1, t2]))
    assert {"chart.pie-slices", "chart.metrics-per-bar", "chart.histogram-bins",
            "chart.treemap-depth", "chart.dupe", "data.row-limit-intent"} <= fired


def test_grain_vs_range():
    few = line("Few", time_grain="P1M", time_range="Last week")
    many = line("Many", time_grain="P1D", time_range="last 5 years")
    iso = line("ISO", time_grain="P1D", time_range="2026-01-01 : 2026-01-02")
    findings = [f for f in run(mk([few, many, iso])).findings
                if f.rule == "data.grain-vs-range"]
    assert {f.chart for f in findings} == {"Few", "Many", "ISO"}


def test_narrative_and_filters_rules():
    a = line("Orders Over Time")
    b = line("orders by region")  # sentence case among Title Case
    b["name"] = "orders by big region"
    flt = hbar("Top Products", filters=[{"column": "region", "op": "==", "value": "EMEA"}])
    bare_kpi = {"type": "big_number_total", "name": "Total Sales", "dataset": DS,
                "metric": "SUM(x)"}
    fired = rules_fired(mk([a, b, flt, bare_kpi]))
    assert {"narrative.title-style", "narrative.filtered-title",
            "narrative.big-number-format", "filters.time-picker"} <= fired


# -- toggles, fixes, report shape ---------------------------------------------------


def test_ignore_rule_and_per_chart():
    data = mk([kpi("A", height=10), kpi("B", height=10)])
    rep = run(data, ignore=("size.kpi-height@A",))
    assert {f.chart for f in rep.findings if f.rule == "size.kpi-height"} == {"B"}
    assert "size.kpi-height@A" in rep.ignored
    rep = run(data, ignore=("size.kpi-height",))
    assert not any(f.rule == "size.kpi-height" for f in rep.findings)


def test_design_block_audience_and_ignore():
    data = mk([kpi("A"), kpi("B"), line("L", height=6)],
              design={"audience": "executive", "ignore": ["layout.fold-budget"]})
    rep = run(data)
    assert rep.audience == "executive"
    # executive min axis height is 8: the 6-unit line now fires
    assert any(f.rule == "size.axis-min-height" for f in rep.findings)
    # caller override beats the spec block
    assert run(data, audience="analytical").audience == "analytical"


def test_overlay_params_disable_and_recommended():
    ov = Overlay(params={"min_axis_height": 9}, disable=["filters.time-picker"],
                 recommended_heights={"timeseries_line": 11})
    data = mk([kpi("A"), kpi("B"), line("L", height=8)])
    rep = run(data, overlay=ov)
    f = next(f for f in rep.findings if f.rule == "size.axis-min-height")
    assert f.fix["set"]["height"] == 11  # calibrated height wins over the minimum
    assert not any(f.rule == "filters.time-picker" for f in rep.findings)
    with pytest.raises(ValueError, match="unknown parameters"):
        params_for("analytical", Overlay(params={"bogus": 1}))


def test_fix_loop_converges_and_is_idempotent():
    data = mk([kpi("A"), kpi("B"), line("L", height=3), hbar("R", height=5)],
              layout={"rows": [["A", "B"], ["L", "R"]]})
    fixed, rep = advise_and_fix(data, overlay=EMPTY)
    assert rep.fixed and not any(f.fix for f in rep.findings)
    again, rep2 = advise_and_fix(fixed, overlay=EMPTY)
    assert rep2.fixed == [] and again == fixed
    heights = {c["name"]: c.get("height") for c in fixed["charts"]}
    assert heights["L"] == heights["R"] >= 6
    load_spec(fixed)  # fixed specs always re-validate


def test_payload_shape_and_severity_order():
    charts = [line(f"L{i}", width=2, height=3) for i in range(6)]
    rep = run(mk(charts, layout={"rows": [[c["name"] for c in charts]]}))
    p = rep.payload()
    assert p["stage"] == "design" and p["design_brain"] == "1"
    sev = [f["severity"] for f in p["findings"]]
    assert sev == sorted(sev, key=["error", "warn", "info"].index)
    assert p["ok"] is False and rep.gate(strict=False)


# -- brief / presets -----------------------------------------------------------------


def test_brief_renders_within_budget_for_every_audience():
    for aud in AUDIENCE_NAMES:
        text = render_brief(aud, overlay=EMPTY)
        assert f"# Design brief: {aud}" in text
        assert len(text.splitlines()) <= 150
        assert "chartwright advise" in text
        assert text.isascii()


def test_brief_includes_house_guidance_and_calibrated_heights():
    ov = Overlay(brief_extra="Use fiscal weeks.", recommended_heights={"table": 11})
    text = render_brief("analytical", overlay=ov)
    assert "Use fiscal weeks." in text and "table: 11" in text


def test_spec_audience_literal_matches_presets():
    for aud in AUDIENCE_NAMES:
        DesignConfig(audience=aud)
    with pytest.raises(ValidationError):
        DesignConfig(audience="board")


def test_every_rule_registered_once_with_valid_severity():
    assert len(RULES) >= 25
    for r in RULES.values():
        assert r.severity in ("error", "warn", "info") and r.doc


# -- v2 roadmap regressions ----------------------------------------------------


def test_autofix_never_mints_fractional_heights():
    """Echo-chamber guard: tool-written fixes must be integers, or they would
    forge absorb's human-polish signature and silence the size rules."""
    ov = Overlay(recommended_heights={"timeseries_line": 8.6})
    data = mk([kpi("A"), kpi("B"), line("L", height=3)])
    fixed, rep = advise_and_fix(data, overlay=ov)
    heights = [c.get("height") for c in fixed["charts"] if c.get("height") is not None]
    assert all(float(h).is_integer() for h in heights), heights


def test_row_harmony_ceils_beside_polished_neighbor():
    data = mk([line("L", height=8.6), hbar("R", height=5)],
              layout={"rows": [["L", "R"]]})
    fixed, rep = advise_and_fix(data, overlay=EMPTY)
    by = {c["name"]: c.get("height") for c in fixed["charts"]}
    assert by["R"] == 9 and by["L"] == 8.6  # raised to ceil(max); polish untouched


def test_ignored_visible_even_on_polished_charts():
    data = mk([kpi("A"), kpi("B"), line("L", height=3.4)],
              design={"ignore": ["size.axis-min-height@L"]})
    rep = run(data)
    assert "size.axis-min-height@L" in rep.ignored


def test_overlay_value_validation():
    with pytest.raises(ValueError, match="must be a number"):
        params_for("analytical", Overlay(params={"min_axis_height": "tall"}))
    with pytest.raises(ValueError, match="1..100"):
        params_for("analytical", Overlay(recommended_heights={"table": 400}))


def test_overlay_file_validation(tmp_path, monkeypatch):
    import yaml
    monkeypatch.setenv("CHARTWRIGHT_DESIGN_DIR", str(tmp_path))
    from chartwright.design.presets import load_overlay
    (tmp_path / "design.yaml").write_text(yaml.safe_dump({"disable": "not-a-list"}))
    with pytest.raises(ValueError, match="list of rule-id strings"):
        load_overlay()
    (tmp_path / "design.yaml").write_text(yaml.safe_dump({"audiences": {"board": {}}}))
    with pytest.raises(ValueError, match="unknown audiences"):
        load_overlay()


def test_recommended_heights_merge_across_layers():
    ov = Overlay(recommended_heights={"table": 11},
                 audiences={"executive": {"recommended_heights": {"pie": 9}}})
    p = params_for("executive", ov)
    assert p.recommended_heights == {"table": 11, "pie": 9}  # merged, not replaced


def test_min_width_rule():
    charts = [line("L", width=2, height=8), hbar("T", width=3, height=8),
              line("W", width=7, height=8)]
    rep = run(mk(charts, layout={"rows": [["L", "T", "W"]]}))
    by = {f.chart: f for f in rep.findings if f.rule == "size.min-width"}
    assert by["L"].severity == "error" and by["T"].severity == "warn" and "W" not in by


def test_slot_counting_allows_kpi_sidebar_and_stacks():
    # The canonical sidebar: two KPIs stacked in a column beside a hero chart.
    charts = [line("T"), kpi("S"), kpi("P")]
    charts[0].pop("height")
    layout = {"sketch": ["TTTTTTTT SSSS", "TTTTTTTT PPPP"],
              "legend": {"T": "T", "S": "S", "P": "P"}, "line": 4}
    rep = run(mk(charts, layout=layout))
    assert not any(f.rule == "layout.kpi-band" for f in rep.findings)
    # A stack occupies ONE slot: 3 slots (one a 2-chart stack) is not 4 charts.
    charts = [line(f"L{i}") for i in range(4)]
    for c in charts:
        c.pop("height")
    layout = {"sketch": ["AABBCCCCDDDD", "AABBCCCCDDDD", "AAEECCCCDDDD", "AAEECCCCDDDD"],
              "legend": {"A": "L0", "B": "L1", "E": "extra", "C": "L2", "D": "L3"}}
    charts.append(line("extra"))
    charts[-1].pop("height")
    # 5 axis charts but only 4 horizontal slots (B stacks over E): under
    # analytical's max of 4 slots this is legal; flattened counting would fire.
    rep = run(mk(charts, layout=layout))
    assert not any(f.rule == "layout.row-density" for f in rep.findings)


def test_grain_default_applies():
    data = mk([line("Hist", time_range="last 5 years")])  # no time_grain
    fs = [f for f in run(data).findings if f.rule == "data.grain-vs-range"]
    assert len(fs) == 1 and "default when omitted" in fs[0].detail


def test_acronym_titles_not_misclassified():
    charts = [line("AOV by Region"), line("SLA Breaches Over Time"),
              hbar("Top Products by GMV")]
    assert not any(f.rule == "narrative.title-style" for f in run(mk(charts)).findings)


def test_boolean_filter_values_ignored_by_filtered_title():
    c = hbar("Active Products", filters=[{"column": "is_active", "op": "==", "value": True}])
    assert not any(f.rule == "narrative.filtered-title" for f in run(mk([c])).findings)


def test_axis_min_height_defers_to_owning_rules():
    heat = {"type": "heatmap", "name": "H", "dataset": DS, "metric": "COUNT(*)",
            "x_column": "a", "y_column": "b", "height": 4, "width": 6}
    bar = hbar("B", row_limit=30, height=8)
    rules = {f.rule for f in run(mk([heat, bar])).findings if f.chart in ("H", "B")}
    assert "size.axis-min-height" not in rules
    assert {"size.heatmap-geometry", "size.hbar-window"} <= rules


def test_duplicate_tab_titles_rejected():
    from pydantic import ValidationError as VE
    data = mk([line("A"), line("B")],
              layout={"tabs": [{"title": "T", "rows": [["A"]]},
                               {"title": "T", "rows": [["B"]]}]})
    with pytest.raises(VE, match="duplicate tab title"):
        load_spec(data)


def test_absorb_report_serializes():
    from chartwright.absorb import AbsorbReport
    assert '"stage": "absorb"' in AbsorbReport(ok=True).to_json()


def test_kpi_clamp_converges_without_oscillation():
    """Contract: size.kpi-height is the only rule that may LOWER a height;
    nothing else targets KPI heights, so the clamp converges in one round."""
    data = mk([kpi("A", height=10), kpi("B", height=10)])
    fixed, rep = advise_and_fix(data, overlay=EMPTY)
    heights = {c["name"]: c["height"] for c in fixed["charts"]}
    assert heights == {"A": 4, "B": 4}
    assert not any(f.rule == "design.fix-stalled" for f in rep.findings)
