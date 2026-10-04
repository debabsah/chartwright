"""The grid model (chartwright/spec.py) against rendered Superset.

Measured on 2026-10-03 on Superset 4.1.4, 5.0.0 and 6.1.0 with the example data, at a
1600 px viewport (docs/DESIGN-BRAIN.md, "Calibration"). Each count is the full body
rows a panel showed without an inner scrollbar, at heights 6 to 20:
- a raw table of three short columns: plain, paged (page_length 50 of 100 rows), with
  a search box, and with both;
- a pivot of 19 countries: no column dimension or one (year), each with and without
  a totals row. With one, the count is the data rows clear of the pinned totals row.

The model takes the largest size of the three releases, so it may count a row fewer
than a release shows. It must never count one more: that row is the hidden-row defect
smoke, size.table-window and the page fill exist to prevent.
"""

import math

import pytest

from chartwright.spec import grid_header, grid_rows_visible, load_spec, table_header_units

HEIGHTS = range(6, 21)

TABLE_ROWS = {
    "plain": {"4.1.4": [5, 6, 8, 9, 10, 12, 13, 15, 16, 18, 19, 20, 22, 23, 25],
              "5.0.0": [5, 6, 8, 9, 10, 12, 13, 15, 16, 18, 19, 20, 22, 23, 25],
              "6.1.0": [4, 6, 7, 8, 10, 11, 12, 14, 15, 17, 18, 19, 21, 22, 23]},
    "pager": {"4.1.4": [2, 3, 5, 6, 7, 9, 10, 12, 13, 15, 16, 18, 19, 20, 22],
              "5.0.0": [2, 3, 5, 6, 7, 9, 10, 12, 13, 15, 16, 18, 19, 20, 22],
              "6.1.0": [1, 3, 4, 5, 7, 8, 9, 11, 12, 13, 15, 16, 18, 19, 20]},
    "search": {"4.1.4": [3, 5, 6, 8, 9, 10, 12, 13, 15, 16, 18, 19, 21, 22, 23],
               "5.0.0": [3, 5, 6, 8, 9, 10, 12, 13, 15, 16, 18, 19, 21, 22, 23],
               "6.1.0": [3, 4, 6, 7, 8, 10, 11, 12, 14, 15, 17, 18, 19, 21, 22]},
    "pager+search": {"4.1.4": [2, 3, 5, 6, 7, 9, 10, 12, 13, 15, 16, 18, 19, 20, 22],
                     "5.0.0": [2, 3, 5, 6, 7, 9, 10, 12, 13, 15, 16, 18, 19, 20, 22],
                     "6.1.0": [1, 2, 4, 5, 6, 8, 9, 10, 12, 13, 15, 16, 17, 19, 20]},
}
TABLES = {"plain": {"row_limit": 60}, "pager": {"row_limit": 100, "page_length": 50},
          "search": {"row_limit": 60, "search_box": True},
          "pager+search": {"row_limit": 100, "page_length": 50, "search_box": True}}

# The three releases drew the pivot identically.
PIVOT_ROWS = {
    "no column dimension": [3, 4, 6, 7, 9, 11, 12, 14, 15, 17, 18, 19, 19, 19, 19],
    "one column dimension": [2, 3, 5, 6, 8, 10, 11, 13, 14, 16, 17, 19, 19, 19, 19],
    "no column dimension, totals": [2, 3, 5, 6, 8, 10, 11, 13, 14, 16, 17, 19, 19, 19, 19],
    "one column dimension, totals": [1, 2, 4, 5, 7, 9, 10, 12, 13, 15, 16, 18, 19, 19, 19],
}
PIVOTS = {"no column dimension": {}, "one column dimension": {"columns": ["year"]},
          "no column dimension, totals": {"column_totals": True},
          "one column dimension, totals": {"columns": ["year"], "column_totals": True}}
PIVOT_LEAVES = 19

DS = {"database": "examples", "table": "cleaned_sales_data"}


def chart(c, height):
    spec = load_spec({"spec_version": "1", "dashboard": {"title": "T", "slug": "t"},
                      "charts": [{**c, "name": "C", "dataset": DS, "height": height}],
                      "layout": {"rows": [["C"]]}})
    return spec.charts[0]


def model_rows(c, height):
    header, row = grid_header(c, getattr(c, "row_limit", None))
    return math.floor(grid_rows_visible(height, header, row))


@pytest.mark.parametrize("variant", TABLE_ROWS)
def test_the_table_model_never_counts_a_row_any_release_hides(variant):
    for i, h in enumerate(HEIGHTS):
        c = chart({"type": "table", "columns": ["order_number", "country", "deal_size"],
                   **TABLES[variant]}, h)
        model = model_rows(c, h)
        shown = {release: rows[i] for release, rows in TABLE_ROWS[variant].items()}
        assert model <= min(shown.values()), (variant, h, model, shown)
        # ...and gives away at most one row on the release that draws the largest rows.
        assert shown["6.1.0"] - model <= 1, (variant, h, model, shown)


@pytest.mark.parametrize("variant", PIVOT_ROWS)
def test_the_pivot_model_never_counts_a_row_any_release_hides(variant):
    for i, h in enumerate(HEIGHTS):
        c = chart({"type": "pivot_table", "rows": ["country"],
                   "metrics": ["SUM(quantity_ordered)"], **PIVOTS[variant]}, h)
        model = min(model_rows(c, h), PIVOT_LEAVES)
        shown = PIVOT_ROWS[variant][i]
        assert model <= shown, (variant, h, model, shown)
        # The horizontal-scrollbar allowance (a column dimension) costs one more row.
        assert shown - model <= (2 if c.columns else 1), (variant, h, model, shown)


def test_a_filled_page_and_its_controls_fit_on_every_release():
    """default.page-length's page is the rows the paged-table measurements showed,
    or fewer, at every height a page is filled."""
    for i, h in enumerate(HEIGHTS):
        page = math.floor(grid_rows_visible(h, table_header_units(controls=True, pager=True)))
        for release, rows in TABLE_ROWS["pager+search"].items():
            assert page <= rows[i], (h, release, page, rows[i])


def test_a_page_size_alone_draws_the_bar_above_the_rows():
    """Measured: a table with page_length draws its page-size picker even when every
    row is on one page (33.1 px on 6.1.0, 39.1 px on 4.1.4), and the pager only when
    there is more than one page. A search box draws the same bar."""
    one_page = chart({"type": "table", "columns": ["a"], "row_limit": 10, "page_length": 50}, 10)
    assert grid_header(one_page, 10)[0] == table_header_units(controls=True)
    paged = chart({"type": "table", "columns": ["a"], "row_limit": 10, "page_length": 5}, 10)
    assert grid_header(paged, 10)[0] == table_header_units(controls=True, pager=True)
    search = chart({"type": "table", "columns": ["a"], "row_limit": 5, "search_box": True}, 10)
    assert grid_header(search, 5)[0] == table_header_units(controls=True)
