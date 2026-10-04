"""Design rules whose heuristics the chart display fields change: a vertical bar
sorted by its categories (hours, ranks) reads as a sequence, a paged table needs
room for one page, a series limit keeps a line chart to the top few, and a trend
with its own time range is bounded."""

from chartwright.design import advise
from chartwright.design.presets import Overlay
from chartwright.smoke import _fit_warning
from chartwright.spec import load_spec

from test_design_data import FakeProber, resolution

DS = {"database": "db", "table": "orders"}


def _spec(charts):
    return load_spec({"spec_version": "1", "dashboard": {"title": "T", "slug": "t"},
                      "charts": charts, "layout": {"rows": [[c["name"]] for c in charts]}})


def _rules(charts, **kw):
    return advise(_spec(charts), overlay=Overlay(), **kw).findings


def _fired(rule, charts, **kw):
    return [f for f in _rules(charts, **kw) if f.rule == rule]


HOURS = {"name": "By Hour", "type": "bar", "dataset": DS, "x_column": "hour",
         "metrics": ["COUNT(*)"], "row_limit": 24, "height": 8}


def test_vbar_categories_accepts_an_ordered_axis_that_labels_every_category():
    assert _fired("chart.vbar-categories", [HOURS])
    assert not _fired("chart.vbar-categories",
                      [{**HOURS, "category_sort": "asc", "x_label_every": True}])
    # Sorted by category but labels dropped: keep the sequence vertical, label every
    # category, rather than flip it to a ranked horizontal list.
    (f,) = _fired("chart.vbar-categories", [{**HOURS, "category_sort": "asc"}])
    assert f.fix == {"chart": "By Hour", "set": {"x_label_every": True}}
    assert "x_label_every" in f.detail
    # A ranking (sorted by its metric) is still pointed at a horizontal bar.
    (f,) = _fired("chart.vbar-categories", [{**HOURS, "row_limit": 12}])
    assert f.fix["set"] == {"orientation": "horizontal"}


def test_ordinal_bars_are_pointed_at_category_sort():
    bar = {**HOURS, "name": "By Weekday", "x_column": "weekday", "row_limit": 7}
    (f,) = _fired("chart.ordinal-order", [bar])
    assert "category_sort" in f.detail
    (f,) = _fired("chart.ordinal-order", [{**bar, "category_sort": "asc"}])
    assert "alphabetically" in f.detail and "category_sort" not in f.detail


def test_a_paged_table_needs_room_for_one_page_not_every_row():
    table = {"name": "T", "type": "table", "dataset": DS, "metrics": ["COUNT(*)"],
             "groupby": ["c"], "sort_by": "COUNT(*)", "row_limit": 500, "height": 12}
    assert _fired("size.table-window", [table])
    assert not _fired("size.table-window", [{**table, "page_length": 10}])
    (f,) = _fired("size.table-window", [{**table, "page_length": 50}])
    assert "50-row page" in f.detail and "page_length" in f.detail
    # 0 is every row on one page: judged like an unpaged table
    assert _fired("size.table-window", [{**table, "page_length": 0}])


def test_grid_fit_and_smoke_count_one_page_of_a_paged_table():
    table = {"name": "T", "type": "table", "dataset": DS, "metrics": ["COUNT(*)"],
             "groupby": ["c"], "sort_by": "COUNT(*)", "row_limit": 60, "height": 12}
    kw = {"resolution": resolution(c=1), "prober": FakeProber({"c": 60})}
    assert _fired("size.grid-fit", [table], **kw)
    assert not _fired("size.grid-fit", [{**table, "page_length": 10}], **kw)
    rows = [{"data": [{"c": i} for i in range(60)]}]
    spec = _spec([table])
    assert _fit_warning(spec.charts[0], spec, rows)
    spec = _spec([{**table, "page_length": 10}])
    assert _fit_warning(spec.charts[0], spec, rows) is None


def test_series_limit_quiets_the_spaghetti_warning():
    line = {"name": "L", "type": "timeseries_line", "dataset": DS, "metrics": ["COUNT(*)"],
            "time_column": "ts", "groupby": "region", "height": 8}
    kw = {"resolution": resolution(ts=2, region=1), "prober": FakeProber({"region": 40})}
    (f,) = _fired("chart.series-limit", [line], **kw)
    assert "series_limit" in f.detail
    assert not _fired("chart.series-limit", [{**line, "series_limit": 5}], **kw)
    assert _fired("chart.series-limit", [{**line, "series_limit": 20}], **kw)


def test_a_trend_with_its_own_time_range_is_bounded():
    trend = {"name": "K", "type": "big_number_trend", "dataset": DS, "metric": "COUNT(*)",
             "time_column": "ts", "time_grain": "P1D"}
    assert _fired("chart.trend-grain", [trend])
    assert not _fired("chart.trend-grain", [{**trend, "time_range": "Last 90 days"}])
