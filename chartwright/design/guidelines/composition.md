# Composition

- Inverted pyramid, top to bottom: KPI band first (the answer), trends second
  (the why), breakdowns and tables last (the detail). Readers scan in a Z:
  the top-left cell is the most valuable slot on the page.
- Group by question, not by chart type: a trend and its breakdown belong side
  by side; two unrelated charts sharing a row invite false comparison.
- Matched granularity per row: charts in one row should share their time
  window and grain, or say in the title why not.
- One message per chart. A chart needing a paragraph to explain wants to be
  two charts, or a table.
- Titles state the answer where possible ("Orders fell 12% WoW"), the
  question otherwise ("Orders by week"); never just a column name. Filtered
  charts name their scope in the title.
- Tabs when sections answer different questions; scrolling when one question
  deepens. Never a tab with a single lonely chart.
- Whitespace is markdown's job, sparingly: a one-line section header beats an
  empty band (2 units is plenty for a header). Consistent number formats per
  measure across KPI cards (tables and pivots use Superset's smart default;
  the spec cannot set per-column formats yet).
- Color restraint: Superset's default palette, RAG only where a threshold has
  a real business meaning; never encode the same dimension with two palettes.
- RAG polarity is a convention, not a choice: red = adverse, green = good
  (IBCS). Bands on one metric must be disjoint and tell one story.
- Heatmaps ship with a sequential scale normalized over the whole map: right
  for magnitudes, wrong for signed deltas -- don't heatmap a metric that
  crosses zero.
