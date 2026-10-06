"""Design brain 8: chart.axis-titles, layout.thin-subtabs, and value labels on grouped bars."""

from chartwright.design import advise
from chartwright.design.presets import Overlay
from chartwright.spec import load_spec

EMPTY = Overlay()  # tests never read ~/.config/chartwright

DS = {"database": "examples", "table": "t"}


def _spec(charts, layout):
    return load_spec({"spec_version": "1", "dashboard": {"title": "T", "slug": "t"},
                      "charts": charts, "layout": layout})


def _line(name, **kw):
    return {"type": "timeseries_line", "name": name, "dataset": DS, "metrics": ["COUNT(*)"],
            "time_column": "ts", "height": 8, "width": 12, **kw}


def _findings(spec, rule):
    return [f for f in advise(spec, overlay=EMPTY).findings if f.rule == rule]


def test_axis_titles_names_each_missing_axis():
    found = _findings(_spec([_line("A"), _line("B", x_axis_title="Month", y_axis_title="Orders"),
                             _line("C", y_axis_title="Orders")],
                            {"rows": [["A"], ["B"], ["C"]]}), "chart.axis-titles")
    got = sorted((f.chart, f.detail.split(":")[0]) for f in found)
    assert got == [("A", "no x_axis_title"), ("A", "no y_axis_title"), ("C", "no x_axis_title")]
    assert {f.severity for f in found} == {"info"}


def test_axis_titles_skips_a_heatmap():
    heat = {"type": "heatmap", "name": "H", "dataset": DS, "x_column": "a", "y_column": "b",
            "metric": "COUNT(*)", "height": 8, "width": 12}
    assert _findings(_spec([heat], {"rows": [["H"]]}), "chart.axis-titles") == []


def _kpi(name):
    return {"type": "big_number_total", "name": name, "dataset": DS, "metric": "COUNT(*)",
            "height": 4, "width": 4}


def test_thin_subtabs_counts_charts_besides_kpis():
    t = dict(x_axis_title="Month", y_axis_title="Orders")
    charts = [_kpi("K1"), _kpi("K2"), _kpi("K3"), _line("A", **t), _line("B", **t), _line("C", **t),
              _line("D", **t), _line("E", **t)]
    layout = {"tabs": [{"title": "Sales", "tabs": [
        {"title": "Thin", "rows": [["K1", "K2", "K3"], ["A"], ["B"]]},
        {"title": "Full", "rows": [["C"], ["D"], ["E"]]}]}]}
    found = _findings(_spec(charts, layout), "layout.thin-subtabs")
    assert [f.where for f in found] == ["tab 'Sales > Thin'"]
    assert "holds 2 chart(s) besides KPIs" in found[0].detail


def test_value_labels_fill_grouped_bars_that_fit():
    def bar(**kw):
        return {"type": "bar", "name": "G", "dataset": DS, "x_column": "region", "row_limit": 5,
                "metrics": ["SUM(a) AS A", "SUM(b) AS B"], "height": 8, "width": 12,
                "x_axis_title": "Region", "y_axis_title": "Sales", **kw}

    def filled(**kw):
        rep = advise(_spec([bar(**kw)], {"rows": [["G"]]}), overlay=EMPTY)
        return [f for f in rep.findings if f.rule == "default.value-labels"]

    assert filled()                       # 5 rows x 2 metrics = 10 bars, 12/12 wide
    assert not filled(row_limit=7)        # 14 bars, past the 12-bar limit
    assert not filled(stack=True)         # each stacked segment would carry a label
