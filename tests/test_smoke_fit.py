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
