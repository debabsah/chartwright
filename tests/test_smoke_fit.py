"""Issue #1: table/pivot heights are fixed while rendered rows grow with the
data; smoke must warn when rows would hide behind the inner scrollbar."""

from chartwright.smoke import _fit_warning, _window_warning
from chartwright.spec import load_spec

DS = {"database": "db", "table": "orders"}


def spec_with(chart):
    return load_spec({
        "spec_version": "1",
        "dashboard": {"title": "T", "slug": "t"},
        "charts": [chart],
        "layout": {"rows": [[chart["name"]]]},
    })


def result_rows(rows):
    return [{"data": rows}]


def test_pivot_overflow_warns_with_leaf_rows():
    # The issue's scenario: a pivot sized for 5 leaf rows; data grew to 9.
    chart = {"type": "pivot_table", "name": "P", "dataset": DS, "rows": ["region"],
             "columns": ["month"], "metrics": ["COUNT(*)"], "height": 9, "row_limit": 100}
    spec = spec_with(chart)
    # 9 regions x 2 months = 18 result rows but 9 leaf rows
    data = [{"region": f"r{i}", "month": m, "count": 1}
            for i in range(9) for m in ("Jan", "Feb")]
    warning = _fit_warning(spec.charts[0], spec, result_rows(data))
    assert warning and "~9 leaf rows" in warning and "inner scrollbar" in warning
    # 5 leaf rows fit at 9 units
    data = [{"region": f"r{i}", "month": "Jan", "count": 1} for i in range(5)]
    assert _fit_warning(spec.charts[0], spec, result_rows(data)) is None


def test_a_five_row_pivot_by_month_does_not_fit_at_eight_units():
    """The calibration's known finding: smoke called a 5-row cause x month pivot fine
    at height 8, and its last row was hidden. Measured on 4.1.4, 5.0.0 and 6.1.0: three
    header rows (26.3 px each) and a 101 px frame leave 5 rows 11.6 px to spare, which
    a horizontal scrollbar (11 px in Chromium, 17 px on Windows) or a totals row
    (28.8 px, pinned over the last row) takes. The grid model counts both."""
    chart = {"type": "pivot_table", "name": "P", "dataset": DS, "rows": ["cause"],
             "columns": ["month"], "metrics": ["COUNT(*)"], "height": 8}
    data = [{"cause": f"c{i}", "month": m, "count": 1} for i in range(5) for m in range(12)]
    for extra in ({}, {"column_totals": True}):
        spec = spec_with({**chart, **extra})
        assert _fit_warning(spec.charts[0], spec, result_rows(data)), extra
    spec = spec_with({**chart, "height": 9, "column_totals": True})
    assert _fit_warning(spec.charts[0], spec, result_rows(data)) is None
    warning = _fit_warning(spec_with({**chart, "column_totals": True}).charts[0],
                           spec_with({**chart, "column_totals": True}),
                           result_rows(data))
    assert "and its totals row" in warning


def test_table_overflow_and_fit():
    chart = {"type": "table", "name": "T", "dataset": DS, "columns": ["a"],
             "height": 6, "row_limit": 50}
    spec = spec_with(chart)
    assert _fit_warning(spec.charts[0], spec, result_rows([{"a": i} for i in range(20)]))
    assert _fit_warning(spec.charts[0], spec, result_rows([{"a": 1}, {"a": 2}])) is None


def test_smoke_and_the_design_critic_share_one_grid_model():
    """Three components once modelled 'how tall must a grid be' three ways
    (0.8 units/row + 1 header here, 0.75 + 3 there, 30px/row in smoke), so one
    table could be silent offline, warned data-aware, and warned again at
    apply. They now read the same numbers from chartwright.spec."""
    from chartwright.design import advise
    from chartwright.design.presets import Overlay
    from chartwright.spec import grid_rows_visible, grid_units_for_rows

    rows, height = 20, 8
    chart = {"type": "table", "name": "T", "dataset": DS, "columns": ["a"],
             "height": height, "row_limit": rows}
    spec = spec_with(chart)

    # smoke (post-apply, real rows) and the offline critic agree it is too short
    assert _fit_warning(spec.charts[0], spec, result_rows([{"a": i} for i in range(rows)]))
    findings = advise(spec, overlay=Overlay()).findings
    assert any(f.rule == "size.table-window" for f in findings), [f.rule for f in findings]

    # and both derive that from the same two functions
    assert grid_units_for_rows(rows) > height
    assert grid_rows_visible(height) < rows


def test_non_grid_charts_and_empty_results_skipped():
    chart = {"type": "timeseries_line", "name": "L", "dataset": DS,
             "metrics": ["COUNT(*)"], "time_column": "ts", "height": 2}
    spec = spec_with(chart)
    assert _fit_warning(spec.charts[0], spec, result_rows([{"ts": 1}] * 99)) is None
    pivot = {"type": "pivot_table", "name": "P", "dataset": DS, "rows": ["r"],
             "metrics": ["COUNT(*)"], "height": 4}
    spec = spec_with(pivot)
    assert _fit_warning(spec.charts[0], spec, result_rows([])) is None


def test_a_rolling_kpi_warns_when_its_data_holds_too_few_buckets():
    """data.rolling-window-span reads a time range it can parse; smoke counts the time
    buckets the chart really has, so "No filter" over 14 months is caught too."""
    ttm = {"type": "big_number_trend", "name": "TTM", "dataset": DS, "metric": "SUM(x)",
           "time_column": "ts", "time_grain": "P1M", "rolling_type": "sum",
           "rolling_periods": 12, "compare_lag": 12}
    chart = spec_with(ttm).charts[0]
    warning = _window_warning(chart, 14)
    assert warning == ("a 12-step rolling window with compare_lag 12 over 14 time buckets "
                       "draws 3 trendline point(s) and no change; widen the chart's time range")
    assert _window_warning(chart, 24) is None                     # 13 points: the change shows
    assert _window_warning(spec_with({**ttm, "rolling_min_periods": 0}).charts[0], 14) is None
    assert _window_warning(spec_with({**ttm, "compare_lag": None}).charts[0], 12)  # one point
    assert _window_warning(spec_with({**ttm, "compare_lag": None}).charts[0], 13) is None
    cum = {**ttm, "rolling_type": "cumsum", "rolling_periods": None}
    assert _window_warning(spec_with(cum).charts[0], 2) is None


# -- values an IN filter lists that return no rows ---------------------------------------

ZONES = ["North", "South", "East", "West"]


class _ChartData:
    """A client whose chart_data answers each query with rows(query)."""

    def __init__(self, rows):
        self.rows = rows

    def chart_data(self, ctx):
        result = [{"data": self.rows(q)} for q in ctx["queries"]]

        class Response:
            status_code = 200

            def json(self):
                return {"result": result}

        return Response()


def _smoke(chart, rows, temporal=()):
    from chartwright.smoke import smoke_chart
    from chartwright.testing import stub_resolution

    spec = spec_with(chart)
    res = stub_resolution(spec)
    for ds in res.datasets.values():
        ds.temporal_columns = set(temporal)
    return smoke_chart(spec.charts[0], spec, res, _ChartData(rows))


def test_a_listed_value_with_no_rows_is_named():
    """The analytics QA's line chart named four zones and drew two: its lines and legend
    said nothing of the other two."""
    line = {"type": "timeseries_line", "name": "By zone", "dataset": DS, "metrics": ["SUM(x)"],
            "time_column": "ts", "groupby": "zone",
            "filters": [{"column": "zone", "op": "IN", "value": ZONES}]}
    out = _smoke(line, lambda q: [{"ts": 1, "zone": z, "SUM(x)": 1} for z in ("North", "South")])
    assert out.ok and out.warning
    assert out.detail == ("2 rows; zone IN lists 'East', 'West', which return no rows, so the "
                          "chart draws nothing for them")
    every = _smoke(line, lambda q: [{"ts": 1, "zone": z} for z in ZONES])
    assert not every.warning and every.detail == "4 rows"


def test_listed_values_are_compared_as_text_and_named_one_at_a_time():
    bar = {"type": "bar", "name": "B", "dataset": DS, "x_column": "hour", "metrics": ["COUNT(*)"],
           "filters": [{"column": "hour", "op": "IN", "value": [1, 2, 3]}]}
    out = _smoke(bar, lambda q: [{"hour": "1"}, {"hour": 2.0}])
    assert out.detail == ("2 rows; hour IN lists 3, which returns no rows, so the chart draws "
                          "nothing for it")


def test_only_a_column_the_query_returns_is_checked():
    """A filter on a column the chart neither groups nor draws by: the rows don't say
    which values came back, so there is nothing to name; nor on a time column (it comes
    back as timestamps), nor values a series limit drops on purpose."""
    pie = {"type": "pie", "name": "P", "dataset": DS, "metric": "COUNT(*)", "groupby": "status",
           "filters": [{"column": "zone", "op": "IN", "value": ["North", "East"]}]}
    assert not _smoke(pie, lambda q: [{"status": "open"}]).warning
    table = {"type": "table", "name": "T", "dataset": DS, "columns": ["day", "x"],
             "filters": [{"column": "day", "op": "IN", "value": ["2026-01-01", "2026-01-02"]}]}
    assert not _smoke(table, lambda q: [{"day": 1767225600000, "x": 1}], temporal=["day"]).warning
    assert _smoke(table, lambda q: [{"day": "2026-01-01", "x": 1}]).warning
    top = {"type": "timeseries_line", "name": "L", "dataset": DS, "metrics": ["SUM(x)"],
           "time_column": "ts", "groupby": "zone", "series_limit": 2,
           "filters": [{"column": "zone", "op": "IN", "value": ZONES}]}
    assert not _smoke(top, lambda q: [{"ts": 1, "zone": "North"}]).warning
    other = {**top, "series_limit": None,
             "filters": [{"column": "zone", "op": "NOT IN", "value": ["North"]}]}
    assert not _smoke(other, lambda q: [{"ts": 1, "zone": "South"}]).warning


def test_a_mixed_chart_reads_both_queries_and_a_capped_query_says_so():
    mixed = {"type": "mixed", "name": "M", "dataset": DS, "x_column": "zone", "row_limit": 2,
             "filters": [{"column": "zone", "op": "IN", "value": ["North", "South", "East"]}],
             "a": {"metrics": ["SUM(x)"]}, "b": {"metrics": ["SUM(y)"], "kind": "line"}}
    out = _smoke(mixed, lambda q: [{"zone": "North"}, {"zone": "South"}])
    assert out.detail == ("4 rows; zone IN lists 'East', which returns no rows within the row "
                          "limit, so the chart draws nothing for it")
    answers = iter([[{"zone": "North"}], [{"zone": "South"}, {"zone": "East"}]])
    assert not _smoke({**mixed, "row_limit": 10}, lambda q: next(answers)).warning


def test_a_height_warning_and_empty_values_are_both_reported():
    table = {"type": "table", "name": "T", "dataset": DS, "columns": ["zone"], "height": 4,
             "row_limit": 50, "filters": [{"column": "zone", "op": "IN", "value": ["a", "z"]}]}
    out = _smoke(table, lambda q: [{"zone": "a"}] * 10)
    assert "inner scrollbar" in out.detail and "zone IN lists 'z'" in out.detail
