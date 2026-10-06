"""The rows a table or pivot draws, and one height question for every consumer.

Found building the airline showcase on Superset 6.1.0:
1. advise passed a 25-row table at 13 units and smoke then asked for ~22: the offline
   rule passed half of row_limit while smoke wants every row;
2. size.pivot-window read row_limit as the pivot's rows ("~0 of 1000 rows", raise to
   331) for a 5-cause by month pivot. A pivot draws a row per distinct row key; its
   query's records are rows x columns;
3. a 5-row pivot by month with a totals row hid its last row at 8 units (fixed by the
   calibration; the arithmetic is pinned in test_smoke_fit.py).
"""

import math
import re

import pytest

from chartwright.design import advise
from chartwright.design.presets import Overlay
from chartwright.resolver import ResolvedDataset, Resolution
from chartwright.smoke import _fit_warning, _query_for
from chartwright.spec import (grid_fit, grid_header, grid_units_for_rows, load_spec, pivot_axes,
                              pivot_frame, pivot_rows, pivot_rows_from_counts)

DS = {"database": "db", "table": "orders"}
EMPTY = Overlay()


class FakeProber:
    """Canned cardinalities; None simulates a failed probe."""

    def __init__(self, counts):
        self.counts = counts

    def count_up_to(self, ds, column, cap):
        n = self.counts.get(column)
        return None if n is None else min(n, cap + 1)

    def more_than(self, ds, column, n):
        c = self.count_up_to(ds, column, n)
        return None if c is None else c > n


def resolution():
    ds = ResolvedDataset(id=1, uuid="u", table="orders", schema=None, database_name="db",
                         columns=[], metrics=[], main_dttm_col="ts", column_types={},
                         temporal_columns=[])
    res = Resolution()
    res.datasets["db//orders"] = ds
    return res


def spec_with(chart):
    return load_spec({"spec_version": "1", "dashboard": {"title": "T", "slug": "t"},
                      "charts": [chart], "layout": {"rows": [[chart["name"]]]}})


def findings(chart, rule, counts=None):
    kw = {"resolution": resolution(), "prober": FakeProber(counts)} if counts is not None else {}
    return [f for f in advise(spec_with(chart), overlay=EMPTY, **kw).findings if f.rule == rule]


def pivot(**kw):
    return {"type": "pivot_table", "name": "P", "dataset": DS, "rows": ["cause"],
            "columns": ["month"], "metrics": ["COUNT(*)"], "height": 8, **kw}


def records(causes=5, months=12, metrics=("count",)):
    return [{"cause": f"c{i}", "month": m, "store": f"s{i % 2}", **dict.fromkeys(metrics, 1)}
            for i in range(causes) for m in range(months)]


# -- what a pivot draws (6.1.0 PivotTableChart.tsx and react-pivottable) ---------------


def test_a_pivot_draws_a_row_per_row_key_never_per_record():
    c = spec_with(pivot()).charts[0]
    assert pivot_rows(c, records()) == (5, 0)  # 60 records, 5 rows


def test_transpose_puts_the_column_dimension_on_the_rows():
    c = spec_with(pivot(transpose=True)).charts[0]
    assert pivot_axes(c)[0] == ["month"]
    assert pivot_rows(c, records()) == (12, 0)


def test_metrics_laid_out_as_rows_multiply_the_rows_and_leave_a_header_row():
    c = spec_with(pivot(metrics=["COUNT(*)", "SUM(x)"], metrics_layout="rows")).charts[0]
    assert pivot_rows(c, records(metrics=("count", "SUM(x)"))) == (10, 0)
    assert pivot_frame(c)[0] == 2  # 'month', and the row names; no metric row above
    assert pivot_rows_from_counts(c, {"cause": 5}) == (10, 0, True)


def test_row_subtotals_add_a_row_per_key_prefix():
    c = spec_with(pivot(rows=["store", "cause"], row_subtotals=True)).charts[0]
    assert pivot_rows(c, records()) == (5, 2)  # 5 store x cause pairs, 2 stores
    # Per-column counts can't know the pairs: the largest count is a lower bound.
    assert pivot_rows_from_counts(c, {"store": 2, "cause": 5}) == (5, 0, False)
    one = spec_with(pivot(metrics=["COUNT(*)", "SUM(x)"], metrics_layout="rows",
                          row_subtotals=True)).charts[0]
    assert pivot_rows_from_counts(one, {"cause": 5}) == (10, 2, True)  # a subtotal per metric


def test_a_pivot_without_row_dimensions_draws_only_its_totals_row():
    c = spec_with(pivot(rows=[], height=4)).charts[0]
    assert pivot_rows(c, records()) == (0, 0)
    assert pivot_frame(c) == (2, True, True)  # metric and month rows; totals forced
    warning = _fit_warning(c, spec_with(pivot(rows=[], height=4)), [{"data": records()}])
    assert warning and warning.startswith("pivot renders only its totals row")


def test_the_header_names_its_parts_and_matches_the_measured_pivot():
    """The '5.7 header units' of the cause x month pivot: frame 2.55, three header rows
    0.66 each, the totals row 0.72 and the scrollbar allowance 0.45. Five rows of 0.65
    then need 8.95 units, 9 whole ones, which is what the rendered pivot needed."""
    c = spec_with(pivot(column_totals=True)).charts[0]
    header, row = grid_header(c)
    assert round(header, 2) == 5.7 and row == 0.65
    assert math.ceil(grid_units_for_rows(5, header, row)) == 9


def test_a_table_summary_row_takes_a_body_row():
    agg = {"type": "table", "name": "T", "dataset": DS, "groupby": ["cause"],
           "metrics": ["COUNT(*)"], "height": 6}
    plain = grid_header(spec_with(agg).charts[0], 5)[0]
    totals = grid_header(spec_with({**agg, "show_totals": True}).charts[0], 5)[0]
    assert totals - plain == pytest.approx(0.74)
    spec = spec_with({**agg, "show_totals": True})
    data = [{"cause": f"c{i}", "count": 1} for i in range(4)]
    assert "and its totals row" in _fit_warning(spec.charts[0], spec, [{"data": data}])


@pytest.mark.parametrize("chart,limit", [
    (pivot(), 10000),
    ({"type": "table", "name": "T", "dataset": DS, "columns": ["a"]}, 1000),
    (pivot(row_limit=50), 50),
])
def test_smoke_asks_for_the_rows_the_compiled_chart_asks_for(chart, limit):
    """At 1,000 records a pivot riding its 10,000 default lost leaf rows to the cap."""
    spec = spec_with(chart)
    assert _query_for(spec.charts[0], spec)["row_limit"] == limit


# -- size.pivot-window offline: never a row count from row_limit ---------------------


def test_pivot_window_never_reads_row_limit_as_rows():
    """Finding 2: 'pivot shows ~0 of 1000 rows at 6 units (5.7 header units); raise
    height to ~331 or lower row_limit' for a pivot that draws 5 rows."""
    chart = pivot(row_limit=1000, height=6, column_totals=True, row_totals=True)
    (f,) = findings(chart, "size.pivot-window")
    assert "1000" not in f.detail and "331" not in f.detail and "row_limit" not in f.detail
    assert f.detail.startswith("pivot at 6 units holds 0 full rows beside the card frame, "
                               "3 header rows, its totals row and room for a horizontal "
                               "scrollbar (5.7 units)")
    assert "~7 units for the first row" in f.detail and "advise --profile" in f.detail
    assert f.fix is None  # a floor: a height that still hides rows is no fix
    # Room for the first row, and offline nothing more is knowable: silent.
    assert not findings({**chart, "height": 7}, "size.pivot-window")
    assert not findings({**chart, "row_limit": 5, "height": 7}, "size.pivot-window")


def test_pivot_window_fixes_a_pivot_whose_rows_the_spec_fixes():
    (f,) = findings(pivot(rows=[], height=4), "size.pivot-window")
    assert f.fix == {"chart": "P", "set": {"height": 6}}


def test_a_probed_pivot_is_grid_fits_with_the_number_smoke_gives():
    chart = pivot(row_limit=1000, height=6, column_totals=True)
    assert not findings(chart, "size.pivot-window", {"cause": 5})
    (f,) = findings(chart, "size.grid-fit", {"cause": 5})
    assert "'cause' yields ~5 rendered rows and a totals row needing ~9 units" in f.detail
    assert f.fix == {"chart": "P", "set": {"height": 9}}
    spec = spec_with(chart)
    assert "raise height to ~9 units" in _fit_warning(spec.charts[0], spec, [{"data": records()}])


def test_a_multi_dimension_pivot_gets_a_lower_bound_without_a_fix():
    (f,) = findings(pivot(rows=["cause", "store"]), "size.grid-fit", {"cause": 30, "store": 2})
    assert "yields at least 30 rendered rows" in f.detail and f.fix is None


def test_a_saturated_probe_is_a_lower_bound():
    (f,) = findings(pivot(), "size.grid-fit", {"cause": 500})
    assert "at least 61 rendered rows" in f.detail and f.fix is None


def test_a_lower_bound_past_one_page_sizes_the_page_exactly():
    table = {"type": "table", "name": "T", "dataset": DS, "groupby": ["cause", "store"],
             "metrics": ["COUNT(*)"], "row_limit": 100, "page_length": 10, "height": 6}
    (f,) = findings(table, "size.grid-fit", {"cause": 40, "store": 2})
    assert "at least 40 rendered rows; its 10-row page needs ~13 units" in f.detail
    assert f.fix == {"chart": "T", "set": {"height": 13}}
    # ...and size.table-window leaves the probed table to size.grid-fit: one target.
    assert not findings(table, "size.table-window", {"cause": 40, "store": 2})
    assert findings(table, "size.table-window")


# -- advise and smoke can't disagree ------------------------------------------------


TABLES = {"raw": {"columns": ["a", "b"]},
          "paged": {"columns": ["a", "b"], "page_length": 8},
          "search": {"columns": ["a", "b"], "search_box": True}}


@pytest.mark.parametrize("variant", TABLES)
def test_table_window_and_smoke_agree_when_the_data_fills_row_limit(variant):
    """Finding 1. Offline the rule reasons about row_limit; smoke about the rows the
    query returned. When those are the same rows, they warn together and name one
    height."""
    for row_limit in (5, 10, 15, 25, 40):
        for height in range(4, 21):
            chart = {"type": "table", "name": "T", "dataset": DS, "row_limit": row_limit,
                     "height": height, **TABLES[variant]}
            spec = spec_with(chart)
            data = [{"a": i} for i in range(row_limit)]
            warning = _fit_warning(spec.charts[0], spec, [{"data": data}])
            found = [f for f in advise(spec, overlay=EMPTY).findings
                     if f.rule == "size.table-window"]
            assert bool(warning) == bool(found), (row_limit, height, warning, found)
            if warning:
                held, header, row = grid_fit(spec.charts[0], row_limit)
                target = math.ceil(grid_units_for_rows(held, header, row))
                assert f"raise height to ~{target} units" in warning
                assert f"raise height to ~{target} or" in found[0].detail


PIVOTS = {"plain": {}, "by month": {"columns": ["month"]},
          "by month, totals": {"columns": ["month"], "column_totals": True},
          "transposed": {"rows": ["month"], "columns": ["cause"], "transpose": True},
          "metrics as rows": {"metrics": ["COUNT(*)", "SUM(x)"], "metrics_layout": "rows"},
          "metrics as rows, subtotals": {"metrics": ["COUNT(*)", "SUM(x)"],
                                         "metrics_layout": "rows", "row_subtotals": True}}


@pytest.mark.parametrize("variant", PIVOTS)
def test_grid_fit_and_smoke_agree_on_a_pivot(variant):
    """Under --profile, size.grid-fit counts what smoke counts in the data: the same
    rows, so the same verdict and the same height."""
    for causes in (1, 3, 5, 12):
        data = records(causes=causes, metrics=("count", "SUM(x)"))
        for height in range(4, 16):
            chart = pivot(height=height, **PIVOTS[variant])
            spec = spec_with(chart)
            warning = _fit_warning(spec.charts[0], spec, [{"data": data}])
            found = findings(chart, "size.grid-fit", {"cause": causes, "month": 12})
            assert bool(warning) == bool(found), (causes, height, warning, found)
            if warning:
                target = re.search(r"raise height to ~(\d+) units", warning).group(1)
                assert f"needing ~{target} units" in found[0].detail, (warning, found[0].detail)
