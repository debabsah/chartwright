# Composition

- Inverted pyramid: KPI band first (the answer), trends next (the why), breakdowns
  and tables last. Readers scan in a Z: the top-left cell is the prime slot.
- Group by question, not by chart type: a trend and its breakdown belong side
  by side; two unrelated charts sharing a row invite false comparison.
- A row's charts share their time window and grain, or the title says why not.
- One message per chart: a chart needing a paragraph is two charts, or a table.
- Titles (recommended): the measure, unit and window ("Orders, last 12 weeks"),
  numbers only when the spec computes them from data; a typed takeaway ("Orders
  fell 12%") goes stale at the next refresh. Never just a column name.
- Tabs split different questions; scrolling deepens one. Never a one-chart tab;
  a one- or two-chart sub-tab belongs on its parent page, under a header row.
- Whitespace is markdown's job, sparingly: a one-line header (1.6-2.4 units) beats
  an empty band. In a sketch, legend blocks draw headers and notes: `"T": {"header":
  "Revenue"}` across the page titles the band below, above a chart its column;
  `{"markdown": "...", "height": 1.6}` is a slim caption.
- One number format per measure across KPIs and axes (`number_format`;
  `number_formats` per table column; pivots keep Superset's smart default).
- Colour by role (recommended): one accent for chrome, this period saturated and
  the prior period its tint, status green/amber/red beside an arrow or a word, row
  tints on status tables. One meaning per colour; never two palettes for one dimension.
- RAG polarity is a convention: red = adverse, green = good (IBCS). Bands on one
  metric are disjoint and tell one story; a status-coloured KPI colours only its
  exceptions and states its thresholds in its subtitle or description.
- Don't heatmap a metric that crosses zero: its sequential scale suits magnitudes.
- Table bars only where size is the point (`cell_bars: ["Revenue"]`), never on
  ids or years. Superset tints every bar by sign, green on 6.x even for revenue:
  keep that for a change, `color_by_sign: ["Change"]`, listed in cell_bars too.
