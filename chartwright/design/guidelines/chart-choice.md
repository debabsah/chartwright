# Chart choice

| The question | The chart |
|---|---|
| How much, right now | `big_number_total`; add `big_number_trend` when direction matters |
| How it moves over time | `timeseries_line`; `_area` for cumulative volume; `_bar` for discrete periods (<= ~30) |
| Ranking over a category | `bar` with `"orientation": "horizontal"`, `row_limit` ~10; long labels get a full line each. On tables, a `row_limit` needs a `sort_by` or it's a sample, not a ranking |
| Share of a whole | `pie`/donut only for <= 7 slices; else horizontal bar |
| Several measures per item | `table` or `pivot_table`, not grouped bars. Pivots: <= 3 total dimensions; column-dim values x metrics <= ~15 rendered columns |
| Two dimensions, one measure | `heatmap` (keep the grid under ~400 cells) |
| Distribution of one column | `histogram`, 20-30 bins; trim long tails with a WHERE filter and say so in the title |
| A distribution per group | `box_plot`: median, quartiles, whiskers and outliers of many observations each (daily sales by month) |
| Staged conversion | `funnel`, 3-8 ordered stages |
| Hierarchical share | `treemap`, at most 2 levels |
| A bridge between two totals | `waterfall`: the opening, 3-12 steps that add up, the closing; `steps` and `closing` keep its order |

- A `row_limit` on a pie redefines the whole: the shown slices read as 100%,
  so a truncated pie lies about share. Prefer a horizontal bar for top-N; a
  truncated pie's title must say "top N".
- Table and pivot height is a DATA-DEPENDENT property, not a one-time layout
  choice: size from expected rows (a table ~0.75 units per row + 2.5, more
  with a search box, pager or totals row; a pivot ~0.65 per distinct
  row-dimension value + 4 to 6 for its header rows and totals row, never from
  its row_limit, which counts records: rows x columns) and re-check whenever a
  row dimension is expected to gain members. Rows past the fold hide behind
  an inner scrollbar; smoke warns at apply time when they do.
- Sort order is per family: bars sort by their first metric (right for
  rankings, wrong for ordinals like weekday/month) unless `category_sort`
  orders them by label. Stacked or grouped bars need `sort_by: "total"` to
  rank (several series otherwise come in name order); `sort_by` a metric ranks
  by one the chart needn't draw, so two charts share an order. Heatmap and
  pivot categories sort alphabetically (by value: a pivot's `row_order`, a
  heatmap's `x_order`/`y_order`). For ordinal dimensions, chart an
  order-encoded label column ('1-Mon') if the dataset has one, and give a bar
  `category_sort: "asc"`. An ordered axis such as hours stays vertical: add
  `x_label_every`; on a heatmap it is a step (6 on hours reads 0, 6, 12, 18).
  A heatmap's `y_order: "a_to_z"` puts the first label on top (a cohort triangle).
- A rolling KPI (`rolling_type`): name its window ("Revenue, trailing 12 months"),
  give a range holding it plus `compare_lag`, and set `y_axis_truncate`.
- A table whose rows all show takes no `page_length` or `search_box`; a
  timeseries with many groups keeps the top few with `series_limit`. A mixed
  chart's lines share one width: give a reference series a pale `label_colors`
  colour; a fixed level is an annotation (`style`, `width`).
- A waterfall explains how one total became another (last year to this year,
  plan to actual) through steps that ADD UP: a SUM or COUNT of the change,
  never an average. Put the opening, each step and the closing in the dataset
  as rows of one step column; name the ends in `opening` and `closing`, the
  steps in `steps` (6.1.0+). An opening total needs a theme that keeps zero on
  the axis (see `opening` in the schema). Set the three colours and `show_value`.
- Show the distribution, not the average, when the spread is the story
  (delivery times, daily sales): a `box_plot` beats a line of P50 and P90
  across categories or a few periods; keep the line for long trends. Give each
  box many observations (`distribute_across` a time column at P1D, `groupby`
  the month) and pin one colour to its groups in `dashboard.label_colors`.
- One dominant category flattening its siblings is the data talking: note it
  or filter it, don't hide it.
- Every chart with a time axis should tolerate the dashboard time filter;
  add a `time_range` filter to the bar so viewers pick their window.
