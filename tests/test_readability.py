"""The readability.* rules (design brain 16; docs/DESIGN-BRAIN.md sec.17, "Type sizes"):
type floors for table and pivot text, axis labels, legends, chart titles and big
numbers, judged against what the dashboard's CSS and theme set, or Superset's defaults.

The model of what Superset draws is checked against what headless Chromium measured on
4.1.4, 5.0.0 and 6.1.0 (tests/fixtures/readability/measurements.json, written by
tools/record_readability_measurements.py), so a change to the reader or to the KPI
table that the browser disagrees with fails here without a browser."""

import json
import re
from pathlib import Path

import pytest

from chartwright.design import advise, advise_and_fix
from chartwright.design.brief import render_brief
from chartwright.design.model import RULES
from chartwright.design.presets import Overlay, params_for
from chartwright.design import readability as R
from chartwright.resolver import Resolution, ResolvedDataset, _theme_json
from chartwright.spec import load_spec

DS = {"database": "db", "table": "orders"}
EMPTY = Overlay()
MEASURED = json.loads((Path(__file__).parent / "fixtures" / "readability"
                       / "measurements.json").read_text(encoding="utf-8"))


def table(name="Orders", **kw):
    return {"type": "table", "name": name, "dataset": DS, "columns": ["id", "region"],
            "row_limit": 10, "height": 8, **kw}


def pivot(name="Pivot", **kw):
    return {"type": "pivot_table", "name": name, "dataset": DS, "rows": ["region"],
            "metrics": ["COUNT(*)"], "height": 8, **kw}


def line(name="Trend", **kw):
    return {"type": "timeseries_line", "name": name, "dataset": DS, "metrics": ["COUNT(*)"],
            "time_column": "ts", "number_format": ",.0f", "height": 8, **kw}


def total(name="Orders KPI", **kw):
    return {"type": "big_number_total", "name": name, "dataset": DS, "metric": "COUNT(*)",
            "number_format": ",.0f", **kw}


def trend(name="Revenue KPI", **kw):
    return {"type": "big_number_trend", "name": name, "dataset": DS, "metric": "SUM(amount)",
            "time_column": "ts", "time_grain": "P1M", "number_format": ",.0f", **kw}


def mk(charts, css=None, layout=None, theme=None):
    dash = {"title": "T", "slug": "t"}
    if css is not None:
        dash["css"] = css
    if theme is not None:
        dash["theme"] = theme
    return {"spec_version": "1", "dashboard": dash, "charts": charts,
            "layout": layout or {"rows": [[c["name"]] for c in charts]}}


def resolved(version=None, theme_json=None):
    res = Resolution(superset_version=version, theme_json=theme_json)
    res.datasets["db//orders"] = ResolvedDataset(
        id=1, uuid="u", table="orders", schema=None, database_name="db",
        columns=["id", "region", "ts", "amount"], metrics=[], main_dttm_col="ts",
        column_types={"id": 0, "region": 1, "ts": 2, "amount": 0}, temporal_columns=["ts"])
    return res


def found(data, rule=None, **kw):
    kw.setdefault("overlay", EMPTY)
    return [f for f in advise(load_spec(data), **kw).findings
            if f.rule == rule or (rule is None and f.rule.startswith("readability."))]


# -- the registry ----------------------------------------------------------------------


def test_three_rules_since_brain_13():
    rules = {r: RULES[r] for r in RULES if r.startswith("readability.")}
    assert sorted(rules) == ["readability.chart-text", "readability.kpi-text",
                             "readability.table-text"]
    assert all(r.since == "16" and r.severities == ("info", "warn") for r in rules.values())
    assert [r for r, x in rules.items() if x.fixable] == ["readability.kpi-text"]


def test_floors_are_audience_params_the_same_for_every_audience():
    for audience in ("executive", "analytical", "operational"):
        p = params_for(audience)
        assert (p.min_cell_text_px, p.min_label_text_px, p.min_title_text_px,
                p.min_kpi_value_px, p.min_row_px) == (14, 12, 14, 24, 24)


# -- readability.table-text ----------------------------------------------------------


def test_superset_default_cells_are_an_info_naming_the_selector():
    [f] = found(mk([table(), pivot()]))
    assert (f.rule, f.severity, f.chart, f.where) == (
        "readability.table-text", "info", None, "table cells")
    assert "tables (Orders) and pivots (Pivot) draw their cells at 12 px" in f.detail
    assert "`.superset-chart-table td, .pivot_table_v_2 td { font-size: 14px; }`" in f.detail
    assert "fontSizeSM" in f.detail and "check table heights" in f.detail


def test_cells_set_at_the_floor_pass():
    css = ".superset-chart-table td, .pivot_table_v_2 td { font-size: 14px; }"
    assert found(mk([table(), pivot()], css=css)) == []


def test_cells_the_dashboard_shrinks_are_a_warn():
    [f] = found(mk([table()], css=".superset-chart-table td { font-size: 11px; }"))
    assert f.severity == "warn" and "at 11 px (set by `.superset-chart-table td`)" in f.detail


def test_a_table_rule_reaches_the_cells_on_4_x_only():
    """`.superset-chart-table table` (0,1,1) beats `.table-condensed` (0,1,0) at 4.1.4 and
    5.0.0 and loses to 6.x's `.css-x table.table-condensed` (0,2,1): measured."""
    data = mk([table()], css=".superset-chart-table table { font-size: 15px; }")
    [f] = found(data)
    assert f.severity == "info" and "12 px on 6.0 and later" in f.detail
    assert "`.superset-chart-table table` loses to Superset's own" in f.detail
    assert found(data, resolution=resolved("4.1.4")) == []
    assert "12 px on 6.0 and later" not in found(data, resolution=resolved("6.1.0"))[0].detail


def test_the_chart_container_sets_nothing_and_markdown_rules_are_not_read():
    css = (".superset-chart-table { font-size: 16px; }\n"
           ".dashboard-markdown td { font-size: 20px; }")
    [f] = found(mk([table()], css=css))
    assert f.severity == "info" and "Not read" not in f.detail


def test_a_pivots_header_cells_keep_their_own_rule_unless_matched():
    loses = ".pivot_table_v_2 th { font-size: 10px; }"
    assert found(mk([pivot()], css=loses + ".pivot_table_v_2 td { font-size: 14px; }")) == []
    ties = (".pivot_table_v_2 table.pvtTable thead tr th { font-size: 10px; }\n"
            ".pivot_table_v_2 td { font-size: 14px; }")
    [f] = found(mk([pivot()], css=ties))
    assert (f.severity, f.where) == ("warn", "table headers")
    important = (".pivot_table_v_2 th { font-size: 10px !important; } "
                 ".pivot_table_v_2 td { font-size: 14px; }")
    assert found(mk([pivot()], css=important))[0].where == "table headers"


def test_squeezed_rows_are_a_warn():
    css = ".pivot_table_v_2 td, .pivot_table_v_2 th { font-size: 14px; line-height: 1; }"
    rows = [f for f in found(mk([pivot()], css=css)) if f.where == "table rows"]
    # 14 px at line-height 1, 4 px of padding above and below (Superset's own rule there
    # beats any the dashboard sets), a 1 px rule: 23 px.
    assert rows and rows[0].severity == "warn" and "23 px" in rows[0].detail


def test_rules_it_cannot_place_are_named_never_guessed():
    css = (".superset-chart-table tr:nth-child(odd) td { font-size: 9px; }\n"
           "@media (max-width: 900px) { .superset-chart-table td { font-size: 9px; } }\n"
           ".pivot_table_v2 td { font-size: 14px; }")
    [f] = found(mk([table()], css=css))
    assert f.severity == "info"
    assert "a pseudo-class" in f.detail and "@media (max-width: 900px)" in f.detail
    assert "`.pivot_table_v_2`" in f.detail    # the class Superset really renders


def test_no_table_no_finding():
    assert found(mk([line()]), rule="readability.table-text") == []


def test_theme_tokens_on_6_x():
    data = mk([table()], theme="Brand")
    # Offline the theme isn't read: the default stands, and the finding says so.
    [f] = found(data)
    assert "the theme 'Brand' was not read" in f.detail
    assert found(data, resolution=resolved("6.1.0", {"token": {"fontSizeSM": 14}})) == []
    # antd derives fontSizeSM from fontSize: 16 gives 14.
    assert found(data, resolution=resolved("6.1.0", {"token": {"fontSize": 16}})) == []
    got = {f.where: f for f in found(data, resolution=resolved("6.1.0", {"algorithm": "compact"}))}
    assert set(got) == {"table cells", "table headers"}
    f = got["table cells"]
    assert f.severity == "warn" and "10 px" in f.detail and "algorithm: compact" in f.detail


def test_antd_font_scale():
    assert R.tokens({}).sm == 12 and R.tokens({}).lg == 16
    t = R.tokens({"token": {"fontSize": 16}})
    assert (t.sm, t.lg, round(t.line, 4)) == (14, 18, 1.5)
    assert R.tokens({"token": {"fontSize": "14", "fontSizeSM": "13"}}).sm == 13
    assert R.tokens({"algorithm": ["dark", "compact"]}).sm == 10


# -- readability.chart-text ----------------------------------------------------------


def test_superset_defaults_pass():
    assert found(mk([line(), table(), total()]), rule="readability.chart-text") == []


def test_titles_the_dashboard_shrinks_are_a_warn():
    [f] = found(mk([line()], css=".header-title { font-size: 13px; }"))
    assert (f.severity, f.where) == ("warn", "chart titles")
    assert "`.header-title { font-size: 14px; }`" in f.detail


def test_a_themes_echarts_overrides_on_6_1():
    theme = {"echartsOptionsOverrides": {"textStyle": {"fontSize": 10}}}
    data = mk([line(), {"type": "pie", "name": "Share", "dataset": DS, "metric": "COUNT(*)",
                        "groupby": "region", "row_limit": 5}], theme="Brand")
    got = {f.where: f for f in found(data, resolution=resolved("6.1.0", theme))}
    assert set(got) == {"axis labels", "legends"}
    assert all(f.severity == "warn" for f in got.values())
    assert "echartsOptionsOverrides.textStyle.fontSize" in got["axis labels"].detail
    # 6.0.x accepts the keys and ignores them.
    assert found(data, resolution=resolved("6.0.0", theme)) == []


def test_a_component_override_beats_the_global_text_style():
    theme = {"echartsOptionsOverrides": {"textStyle": {"fontSize": 10},
                                         "xAxis": {"axisLabel": {"fontSize": 13}},
                                         "yAxis": {"axisLabel": {"fontSize": 13}}},
             "echartsOptionsOverridesByChartType": {
                 "echarts_timeseries_line": {"yAxis": [{"axisLabel": {"fontSize": 11}}]}}}
    assert R.echarts_size(theme, "echarts_timeseries_bar", "axis")[0] == 13
    assert R.echarts_size(theme, "echarts_timeseries_line", "axis") == (
        11, "echartsOptionsOverridesByChartType.echarts_timeseries_line.yAxis.axisLabel.fontSize")
    assert R.echarts_size(theme, "pie", "legend")[0] == 10
    assert R.echarts_size(None, "pie", "legend") is None


def test_a_raised_floor_reports_superset_defaults_as_info():
    ov = Overlay(params={"min_label_text_px": 13})
    got = {f.where: f for f in found(mk([line()]), overlay=ov,
                                     rule="readability.chart-text")}
    assert set(got) == {"axis labels", "legends"} and got["axis labels"].severity == "info"
    assert "a canvas" in got["axis labels"].detail


# -- readability.kpi-text ------------------------------------------------------------


def kpi_fixes(data, **kw):
    return {f.chart: f.fix["set"]["height"] for f in found(data, "readability.kpi-text", **kw)
            if f.fix}


def test_a_subtitle_needs_5_units_and_a_comparison_6():
    assert kpi_fixes(mk([total(subtitle="booked")])) == {"Orders KPI": 5}
    assert kpi_fixes(mk([total()])) == {}                    # value only: 31 px at 4
    assert kpi_fixes(mk([total(height=3)])) == {"Orders KPI": 4}   # 18 px at 3
    # A trendline KPI is 5 units unless written: its value reads (31 px), its comparison
    # doesn't (11 px).
    assert kpi_fixes(mk([trend()])) == {}
    assert kpi_fixes(mk([trend(compare_lag=1)])) == {"Revenue KPI": 6}
    assert kpi_fixes(mk([trend(height=4)])) == {"Revenue KPI": 5}   # 22 px value at 4


def test_a_trend_subtitle_counts_only_where_it_shows():
    data = mk([trend(subtitle="Booked revenue", height=5)])
    assert kpi_fixes(data) == {"Revenue KPI": 6}             # 6.x draws it at 11 px
    assert kpi_fixes(data, resolution=resolved("4.1.4")) == {}


def test_one_band_one_height_and_the_loop_converges():
    data = mk([total(), trend(compare_lag=1)],
              layout={"rows": [["Orders KPI", "Revenue KPI"]]})
    got = {f.chart: f for f in found(data, "readability.kpi-text")}
    assert {n: f.fix["set"]["height"] for n, f in got.items()} == {
        "Orders KPI": 6, "Revenue KPI": 6}
    assert "one band, one height" in got["Orders KPI"].detail
    fixed, rep = advise_and_fix(data, overlay=EMPTY)
    assert [c["height"] for c in fixed["charts"]] == [6, 6]
    assert not [f for f in rep.findings if f.rule in ("readability.kpi-text", "size.kpi-height")]


def test_a_polished_height_is_left_to_its_author():
    rep = advise(load_spec(mk([total(subtitle="booked", height=3.4)])), overlay=EMPTY)
    assert "readability.kpi-text@Orders KPI" in rep.polished


def test_a_narrow_card_gets_no_height_fix():
    long = "Orders booked across every region this quarter"
    [f] = found(mk([total(subtitle=long, width=2, height=4), line(width=10)],
                   layout={"rows": [["Orders KPI", "Trend"]]}), "readability.kpi-text")
    assert f.fix is None and "too narrow" in f.detail


def test_important_css_pins_a_size():
    css = ".subheader-line, .subtitle-line { font-size: 10px !important; }"
    got = found(mk([total(subtitle="booked", height=6)], css=css), "readability.kpi-text")
    assert [(f.severity, f.fix) for f in got] == [("warn", None)]
    assert found(mk([total(subtitle="booked", height=4)],
                    css=".subheader-line { font-size: 14px !important; }"),
                 "readability.kpi-text") == []
    # Without !important the inline style Superset writes wins: the rule reads nothing.
    assert kpi_fixes(mk([total(subtitle="booked")],
                        css=".subheader-line { font-size: 14px; }")) == {"Orders KPI": 5}


def test_the_brief_says_what_height_kpi_text_asks():
    text = render_brief("analytical", overlay=EMPTY)
    assert "a subtitle needs 5 units, a trendline KPI's comparison 6" in text


# -- the theme's JSON, as resolve reads it ----------------------------------------------


def test_resolve_parses_the_themes_json_data():
    assert _theme_json('{"token": {"fontSizeSM": 14}}') == {"token": {"fontSizeSM": 14}}
    assert _theme_json({"token": {}}) == {"token": {}}
    assert _theme_json("not json") is None and _theme_json("[1]") is None
    assert _theme_json(None) is None


# -- the model against what the browser measured -----------------------------------------


def releases_measured():
    return sorted(MEASURED["releases"])


def test_every_supported_release_was_measured():
    assert releases_measured() == ["4.1.4", "5.0.0", "6.1.0"]


@pytest.mark.parametrize("release", ["4.1.4", "5.0.0", "6.1.0"])
def test_the_kpi_table_is_the_smallest_any_release_drew(release):
    kpi = MEASURED["releases"][release]["kpi"]
    for units in range(2, 11):
        t, r = kpi[f"total {units}"], kpi[f"trend {units}"]
        assert R.KPI_TOTAL[units][0] <= t["value"] and R.KPI_TOTAL[units][1] <= t["label"]
        assert R.KPI_TREND[units][0] <= r["value"] and R.KPI_TREND[units][1] <= min(
            r["label"], r.get("subtitle", r["label"]))
    # ...and no smaller than the smallest: the table is exact.
    for units in range(2, 11):
        lows = [MEASURED["releases"][x]["kpi"] for x in releases_measured()]
        assert R.KPI_TOTAL[units] == (min(k[f"total {units}"]["value"] for k in lows),
                                      min(k[f"total {units}"]["label"] for k in lows))
        assert R.KPI_TREND[units] == (
            min(k[f"trend {units}"]["value"] for k in lows),
            min(min(k[f"trend {units}"]["label"], k[f"trend {units}"].get("subtitle", 99))
                for k in lows))


@pytest.mark.parametrize("release", ["4.1.4", "5.0.0", "6.1.0"])
def test_superset_defaults_are_what_the_browser_drew(release):
    m = MEASURED["releases"][release]["default"]
    assert m["Order lines"]["td"] == m["Order lines"]["th"] == R.DEFAULT_TEXT
    assert m["Country pivot"]["td"] == m["Country pivot"]["th"] == R.DEFAULT_TEXT
    assert {m[c]["title"] for c in m} == {R.DEFAULT_TITLE}
    assert all(re.match(r"(bold )?12px ", f) for f in m["Sales by line"]["canvas_fonts"])


@pytest.mark.parametrize("release", ["4.1.4", "5.0.0", "6.1.0"])
@pytest.mark.parametrize("variant", sorted(MEASURED["css"]))
def test_the_css_reader_predicts_what_the_browser_drew(release, variant):
    """Font sizes exactly, and row heights to a fifth of a pixel, for every stylesheet
    the recorder applied: each one tests a claim the reader makes about the cascade."""
    rel = R.MODERN if release.startswith("6") else R.LEGACY
    sheet = R.read_css(MEASURED["css"][variant])
    m = MEASURED["releases"][release][variant]
    tok = R.Tokens()
    for chart, kind in (("Order lines", "table"), ("Country pivot", "pivot_table")):
        got = m[chart]
        assert R._font(sheet, kind, "td", rel, tok, "body").px == got["td"], (chart, "td")
        assert R._font(sheet, kind, "th", rel, tok, "head").px == got["th"], (chart, "th")
        if got["row_label"] is not None:
            assert R._font(sheet, kind, "th", rel, tok, "body").px == got["row_label"]
        assert abs(R._row(sheet, kind, rel, tok).px - got["row"]) <= 0.2, (chart, "row")
    title = R._winner(sheet, "any", "title", "font-size")
    assert (float(title.value.rstrip("px")) if title else R.DEFAULT_TITLE) == m["Orders"]["title"]
    # Only !important beats the size BigNumberViz writes inline.
    label = R._winner(sheet, "big_number_total", "kpi-label", "font-size")
    pinned = float(label.value.rstrip("px")) if label is not None and label.important else None
    assert (m["Orders"]["label"] == pinned) == (pinned is not None)
