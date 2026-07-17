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
| Staged conversion | `funnel`, 3-8 ordered stages |
| Hierarchical share | `treemap`, at most 2 levels |

- A `row_limit` on a pie redefines the whole: the shown slices read as 100%,
  so a truncated pie lies about share. Prefer a horizontal bar for top-N; a
  truncated pie's title must say "top N".
- Table and pivot height is a DATA-DEPENDENT property, not a one-time layout
  choice: size from expected rows (~0.75 units per row + 3 for title/header,
  +1 for pivot column headers) and re-check whenever a row dimension is
  expected to gain members. Rows past the fold hide behind an inner
  scrollbar; smoke warns at apply time when they do.
- Sort order is per family: bars sort by their first metric (right for
  rankings, wrong for ordinals like weekday/month); heatmap and pivot
  categories sort alphabetically. For ordinal dimensions, chart an
  order-encoded label column (labels prefixed with a sort index: '1-Mon')
  if the dataset has one.
- One dominant category flattening its siblings is the data talking: note it
  or filter it, don't hide it.
- Every chart with a time axis should tolerate the dashboard time filter;
  add a `time_range` filter to the bar so viewers pick their window.
