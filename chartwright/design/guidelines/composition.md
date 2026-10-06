# Composition

- Inverted pyramid, top to bottom: KPI band first (the answer), trends second
  (the why), breakdowns and tables last (the detail). Readers scan in a Z:
  the top-left cell is the most valuable slot on the page.
- Group by question, not by chart type: a trend and its breakdown belong side
  by side; two unrelated charts sharing a row invite false comparison.
- A row's charts share their time window and grain, or the title says why not.
- One message per chart: a chart needing a paragraph is two charts, or a table.
- Titles state the answer where possible ("Orders fell 12% WoW"), the
  question otherwise ("Orders by week"); never just a column name. Filtered
  charts name their scope in the title.
- Tabs split different questions; scrolling deepens one. Never a one-chart tab.
- Whitespace is markdown's job, sparingly: a one-line section header beats an
  empty band, at the 1.6 to 2.4 units `advise` fits to its line. In a sketch,
  draw headers and notes as legend blocks: `"T": {"header": "Revenue"}` drawn
  across the page titles the band below it, drawn above a chart it heads that
  chart's column, and `{"markdown": "...", "height": 1.6}` is a slim caption.
  Consistent number formats per measure across KPI cards and axes
  (`number_format` on KPIs, timeseries and bars; `number_formats` per column
  on tables; pivots use Superset's smart default).
- Color restraint: Superset's default palette, RAG only where a threshold has
  a real business meaning; never encode the same dimension with two palettes.
- RAG polarity is a convention, not a choice: red = adverse, green = good
  (IBCS). Bands on one metric must be disjoint and tell one story. A
  status-coloured KPI (`big_number_total` rules) may colour only its
  exceptions, and states its thresholds in its subtitle or description.
- A heatmap's default sequential scale, normalized over the whole map, suits
  magnitudes, not signed deltas: don't heatmap a metric that crosses zero.
- Table bars only where size is the point (`cell_bars: ["Revenue"]`), never on
  ids or years. Superset tints every bar by sign, green on 6.x even for revenue:
  keep that for a change, `color_by_sign: ["Change"]`, listed in cell_bars too.
