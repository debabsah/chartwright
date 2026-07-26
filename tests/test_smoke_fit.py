"""Issue #1: table/pivot heights are fixed while rendered rows grow with the
data; smoke must warn when rows would hide behind the inner scrollbar."""

from chartwright.smoke import _fit_warning
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
    # The issue's exact scenario: height 8 sized for 5 leaf rows; data grew to 9.
    chart = {"type": "pivot_table", "name": "P", "dataset": DS, "rows": ["region"],
             "columns": ["month"], "metrics": ["COUNT(*)"], "height": 8, "row_limit": 100}
    spec = spec_with(chart)
    # 9 regions x 2 months = 18 result rows but 9 leaf rows
    data = [{"region": f"r{i}", "month": m, "count": 1}
            for i in range(9) for m in ("Jan", "Feb")]
    warning = _fit_warning(spec.charts[0], spec, result_rows(data))
    assert warning and "~9 leaf rows" in warning and "inner scrollbar" in warning
    # 5 leaf rows fit exactly the way the author sized it
    data = [{"region": f"r{i}", "month": "Jan", "count": 1} for i in range(5)]
    assert _fit_warning(spec.charts[0], spec, result_rows(data)) is None


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
