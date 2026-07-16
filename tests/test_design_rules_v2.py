"""Batch B of the v2 roadmap: the pivot pack, filter-bar pack, grain v2,
ranking sort, ordinal order, format consistency, color bands, and the
markdown-height fix."""

from chartwright.design import advise, advise_and_fix
from chartwright.design.presets import Overlay
from chartwright.spec import load_spec

from tests.test_design_data import FakeProber, resolution

DS = {"database": "db", "table": "orders"}
EMPTY = Overlay()


def mk(charts, layout=None, filters=None):
    data = {
        "spec_version": "1",
        "dashboard": {"title": "T", "slug": "t"},
        "charts": charts,
        "layout": layout or {"rows": [[c["name"]] for c in charts]},
    }
    if filters is not None:
        data["filters"] = filters
    return data


def run(data, **kw):
    kw.setdefault("overlay", EMPTY)
    return advise(load_spec(data), **kw)


def rules_fired(data, **kw):
    return {f.rule for f in run(data, **kw).findings}


def pivot(name, **kw):
    return {"type": "pivot_table", "name": name, "dataset": DS,
            "rows": ["region"], "metrics": ["COUNT(*)"], "height": 8, **kw}


# -- pivot pack -------------------------------------------------------------------


def test_pivot_window_and_row_limit_intent():
    fired = rules_fired(mk([pivot("P", row_limit=1000, height=6, columns=["month"])]))
    assert "size.pivot-window" in fired
    assert "data.row-limit-intent" in rules_fired(mk([pivot("Q")]))


def test_pivot_dims():
    p = pivot("P", rows=["a", "b"], columns=["c", "d"], row_limit=50, height=10)
    assert "chart.pivot-dims" in rules_fired(mk([p]))
    assert "chart.pivot-dims" not in rules_fired(
        mk([pivot("Q", rows=["a"], columns=["c"], row_limit=50, height=10)]))


def test_pivot_columns_data_aware():
    p = pivot("P", columns=["month", "channel"], row_limit=50, height=10,
              metrics=["COUNT(*)", "SUM(v)"])
    spec = load_spec(mk([p]))
    rep = advise(spec, resolution=resolution(ts=2),
                 prober=FakeProber({"month": 12, "channel": 4}), overlay=EMPTY)
    assert any(f.rule == "chart.pivot-columns" for f in rep.findings)  # 2*12*4 = 96
    rep = advise(spec, resolution=resolution(ts=2),
                 prober=FakeProber({"month": 3, "channel": 2}), overlay=EMPTY)
    assert not any(f.rule == "chart.pivot-columns" for f in rep.findings)  # 12


# -- filter-bar pack ---------------------------------------------------------------


def select(name, column="region"):
    return {"type": "select", "name": name, "dataset": DS, "column": column}


def test_filter_bar_pack():
    filters = ([select(f"S{i}", column=f"c{i}") for i in range(7)]
               + [select("Dup", column="c0"),
                  {"type": "time_range", "name": "W"},
                  {"type": "range", "name": "R", "dataset": DS, "column": "amount"}])
    charts = [pivot("P", row_limit=20, height=10)]
    fired = rules_fired(mk(charts, filters=filters))
    assert {"filters.count", "filters.duplicate-column", "filters.time-default",
            "filters.range-default"} <= fired


def test_select_cardinality_probe():
    filters = [select("Store", column="store")]
    spec = load_spec(mk([pivot("P", row_limit=20, height=10)], filters=filters))
    rep = advise(spec, resolution=resolution(ts=2, store=1),
                 prober=FakeProber({"store": 5000}), overlay=EMPTY)
    assert any(f.rule == "filters.select-cardinality" for f in rep.findings)


# -- grain v2 / trend tiles ----------------------------------------------------------


def line(name, **kw):
    return {"type": "timeseries_line", "name": name, "dataset": DS,
            "metrics": ["COUNT(*)"], "time_column": "ts", "height": 8, **kw}


def test_per_type_point_budgets():
    bar = dict(line("B", time_range="last 90 days"), type="timeseries_bar")
    ln = line("L", time_range="last 90 days")
    findings = run(mk([bar, ln]), audience="analytical").findings
    by = {f.chart for f in findings if f.rule == "data.grain-vs-range"}
    assert "B" in by and "L" not in by  # 90 daily points: over bar budget, under line's


def test_week_anchor_grain_and_zero_span():
    wk = line("W", time_range="last 2 years", time_grain="1969-12-28T00:00:00Z/P1W")
    zero = line("Z", time_range="2026-01-01 : 2026-01-01")
    findings = [f for f in run(mk([wk, zero])).findings if f.rule == "data.grain-vs-range"]
    assert {f.chart for f in findings} == {"Z"}
    assert "extend the time_range" in findings[0].detail


def test_trend_grain_rule():
    trend = {"type": "big_number_trend", "name": "K", "dataset": DS,
             "metric": "COUNT(*)", "time_column": "ts", "number_format": ",.0f"}
    assert "chart.trend-grain" in rules_fired(mk([trend]))
    # a defaulted dashboard window silences it
    fired = rules_fired(mk([trend], filters=[
        {"type": "time_range", "name": "W", "default": "Last month"}]))
    assert "chart.trend-grain" not in fired


# -- ranking, ordinal, formats, color ------------------------------------------------


def test_top_n_sort():
    t = {"type": "table", "name": "Top Stores", "dataset": DS, "groupby": ["store"],
         "metrics": ["SUM(v)"], "row_limit": 10, "height": 10}
    fired = rules_fired(mk([t]))
    assert "data.top-n-sort" in fired
    t["sort_by"] = "SUM(v)"
    assert "data.top-n-sort" not in rules_fired(mk([t]))


def test_ordinal_order():
    b = {"type": "bar", "name": "By Weekday", "dataset": DS, "x_column": "weekday",
         "metrics": ["COUNT(*)"], "row_limit": 7, "height": 8}
    assert "chart.ordinal-order" in rules_fired(mk([b]))


def test_format_consistency():
    k1 = {"type": "big_number_total", "name": "Sales", "dataset": DS,
          "metric": "SUM(v)", "number_format": ",.0f"}
    k2 = {"type": "big_number_total", "name": "Sales YTD", "dataset": DS,
          "metric": "SUM(v)", "number_format": ".3s"}
    assert "narrative.format-consistency" in rules_fired(mk([k1, k2]))


def test_format_bands_overlap_and_lone():
    p = pivot("P", row_limit=20, height=10, metrics=["SUM(v) AS Val"],
              conditional_formatting=[
                  {"metric": "Val", "operator": "<", "target": 50, "color": "red"},
                  {"metric": "Val", "operator": "between", "target_left": 40,
                   "target_right": 60, "color": "amber"}])
    findings = [f for f in run(mk([p])).findings if f.rule == "chart.format-bands"]
    assert any(f.severity == "warn" for f in findings)  # 40..50 overlaps <50
    lone = pivot("Q", row_limit=20, height=10, metrics=["SUM(v) AS Val"],
                 conditional_formatting=[
                     {"metric": "Val", "operator": ">", "target": 90, "color": "green"}])
    findings = [f for f in run(mk([lone])).findings if f.rule == "chart.format-bands"]
    assert findings and findings[0].severity == "info"


def test_treemap_vs_bar():
    tm = {"type": "treemap", "name": "TM", "dataset": DS, "metric": "COUNT(*)",
          "groupby": ["region"], "row_limit": 20, "height": 8}
    spec = load_spec(mk([tm]))
    rep = advise(spec, resolution=resolution(ts=2, region=1),
                 prober=FakeProber({"region": 5}), overlay=EMPTY)
    assert any(f.rule == "chart.treemap-vs-bar" for f in rep.findings)


def test_markdown_height_fix():
    data = mk([line("L")],
              layout={"rows": [[{"markdown": "## Section", "height": 6}], ["L"]]})
    fixed, rep = advise_and_fix(data, overlay=EMPTY)
    assert "layout.markdown-height" in " ".join(rep.fixed)
    assert fixed["layout"]["rows"][0][0]["height"] == 2
    load_spec(fixed)
