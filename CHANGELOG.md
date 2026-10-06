# Changelog

Notable changes per release. Versions follow [semantic versioning](https://semver.org);
while the major version is 0, minor bumps may include breaking changes and say so here.

## Unreleased

### Upgrading

- The design brain is version 8. `advise` now flags a table whose height can't show every row its `row_limit` allows, as the smoke check after `apply` does when the data fills it; before, it passed a table that showed half. A `--design strict` or `advise --strict` gate can newly fail on such a table. To keep the old threshold for tables that rarely reach their `row_limit`, set `table_visible_ratio: 0.5` in `design.yaml` or a standards file.
- The design brain is version 11. `advise` now warns when a markdown block's text runs past the block's bottom edge (`size.markdown-fit`), so a `--design strict` or `advise --strict` gate can newly fail on such a block; `advise --fix` raises it. `layout.markdown-height` now fixes a one-line block to the height its line takes, 1.6 units for a line of text up to 2.4 for a `#` heading, instead of 2.
- A spec with `conditional_formatting` on a big number is refused by chartwright 0.5.0 and earlier (`extra_forbidden`); upgrade CI before committing one.
- The design brain is version 14. Its new warn rule, `chart.color-contrast`, can fail `advise --strict` and `--design strict` on a spec that passed before: a colour rule painting text or a trendline too pale to read.

### Added

- `advise` checks that each markdown block is tall enough for its text (`size.markdown-fit`). Superset cuts the rest off inside the block: macOS shows no scrollbar there, so a screenshot shows the text stopping mid-line. The check estimates the text's height from its headings, paragraphs, lists, tables, quotes, code and line breaks, the block's width and the dashboard's own CSS, in a window 1440 px wide, measured against what 4.1.4, 5.0.0 and 6.1.0 draw. A wider window wraps fewer lines, so a height that fits there fits wider too.
  - It warns when letters are cut off on every release and says by how much ("it needs ~16 units at 12/12 in a 1440 px wide window, has 15"); `--fix` raises the block to a height that shows every line on every release, Windows scrollbars included.
  - A block whose last line or padding only reaches the edge gets an info: it scrolls a few px, and Windows draws a scrollbar in it. That covers short strips: a line of text needs 1.6 units, a `##` heading 2.2.
  - It reads the text sizes and spacing a dashboard's CSS gives its markdown: font sizes, line heights, weights, letter and word spacing, margins, padding and borders set on `.dashboard-markdown` elements, `h4 + p` and `p:last-child` rules included, in cascade order over Superset's own. CSS that could change a block's height in a way it doesn't read (another font family, attribute selectors and pseudo-elements, `@media` rules) is named in the finding, as is a theme.
  - It checks blocks in layout rows, tabs and sub-tabs, and header and footer rows, and allows for the filter bar, which narrows the page on a dashboard with native filters. A header or footer row a standard owns is reported, never resized, and a block holding an image gets no fix, since its height is unknown.
- `conditional_formatting` on `big_number_total`: colour the number by its value, such as a balance-closure KPI in red outside ±2% and green inside (`[{"operator": "<", "target": -0.02, "color": "red"}, {"operator": ">", "target": 0.02, "color": "red"}]`). The rules take the operators, thresholds and colours of table and pivot rules; a named colour takes its darker text shade, since the number is text. Superset 4.1.4, 5.0.0 and 6.1.0 all colour it, so no release is refused or warned. Decompile reads the rules back, and `plan` reports a rule changed in Superset.
  - When several rules match, the last one's colour wins, as in Superset.
  - `apply_to`, `paint` and a `metric` other than the chart's own are refused on a big number's rule, and so is `conditional_formatting` on `big_number_trend`, which Superset can't colour (its `trend_color` colours the line).
  - Superset never colours a value of exactly 0; apply's data check warns when a coloured big number is 0 and a rule would have coloured it.
- Design brain 14:
  - `chart.color-contrast` (warn): a colour too pale to read on the white card, below 3:1 for a big number's rule or a trendline and below 4.5:1 for a table or pivot text rule.
  - `narrative.kpi-thresholds` (info): a coloured big number whose subtitle and description don't state its thresholds.
  - `chart.format-bands` checks a big number's rules for overlapping bands. A single band on a big number is fine: colouring only the exceptions is enough.
- Table cell bars per column. `cell_bars` takes a list of labels as well as true or false: `"cell_bars": ["Revenue", "Change"]` draws bars on those columns only. `color_by_sign` colours bars by sign, green above zero and red below (Superset 6.0+; 4.1.4 and 5.0.0 colour only the negative bars, red), for the whole table or the labels listed, so a change shows its sign while revenue bars stay neutral; `"color_by_sign": false` turns off the sign colours Superset draws by default. `absolute_bars` sizes bars by absolute value from the cell's left edge (Superset's "Align +/-"), so a rise and a fall of the same size draw the same bar. Each compiles to Superset's own table-wide and per-column settings; `decompile` reads any mix of them back as Superset draws it, and `plan` reports a change made in the UI. chartwright 0.5.0 and earlier refuse a spec that uses them.
- `check`, `apply` and `plan` warn when a table asks for cell bars beside colour rules on Superset before 6.1.0, which draws no cell bar on a table with any colour rule.
- Colour rules on tables, pivots and big numbers take `>=`, `<=`, `!=` and `between_inclusive` (a range with its bounds taken in), besides `<`, `>`, `=` and `between`. Every supported release has these comparators, so they draw the same everywhere. `decompile` reads them back from rules made in Superset, where before they were dropped as losses.
- `chartwright advise` checks type sizes against floors for reading a dashboard on a laptop (design brain 16): `readability.table-text` (table and pivot cells 14 px, their headers 12 px, rows 24 px tall), `readability.chart-text` (axis labels and legends 12 px, chart titles 14 px) and `readability.kpi-text` (a big number's value 24 px, its subtitle or comparison 12 px). Text Superset draws smaller by default, such as its 12 px table cells, is an info naming the CSS selector or theme token to set; text the dashboard's own CSS or theme sets smaller is a warn, so `--design strict` can newly block. Superset sizes a big number's text by the card's height, so `advise --fix` raises such a card, and the cards beside it, to the height where its text reads: a card with a subtitle to 5 units, a trendline card with a comparison to 6. The floors are design parameters (`min_cell_text_px` and four more) that `design.yaml` and a standard can tune or lock. `check` and `advise --profile` read a named theme's tokens and ECharts overrides.

### Fixed

- `advise` and the smoke check after `apply` now ask the same height question of tables and pivots, so they no longer disagree:
  - a table: advise names the height smoke names once the data fills `row_limit` (a 25-row table at 13 units now gets "raise height to ~21" from both);
  - a pivot: `size.pivot-window` no longer reads `row_limit` as the pivot's rows. `row_limit` caps the query's records, which are rows times columns, so a 5-row cause by month pivot was told it showed "~0 of 1000 rows" and to grow to 331 units. Offline, the rule now checks only what the spec fixes (its header rows, totals row and first row) and says so; `advise --profile` counts each row dimension's values and gives the height smoke gives;
  - messages name what takes the room: the card frame, each header row, the totals row and room for a horizontal scrollbar.
- Height estimates count what a pivot draws in every layout: a transposed pivot is sized by its column dimension, metrics laid out as rows multiply its rows, `row_subtotals` adds its subtotal rows, and a pivot with no row dimension is its totals row. A table's `show_totals` row now takes a row of height too, in the checks and in the page size `advise --fix` fills.
- The smoke check queries a table or pivot without a `row_limit` for as many rows as the chart does (10,000 for a pivot, 1,000 for a table), so a large pivot's height check counts all its rows.
- `apply`'s data check queries each chart the way the dashboard does when it loads: with the default of every native filter in the chart's scope. Before, it queried every chart unfiltered, so a table holding one set of rows per value of a required filter counted every set; the check then asked for more height on charts that fit, and could name a chart empty, or not, wrongly. As in Superset, a filter's default reaches a chart only when the chart is in the filter's scope and the chart's dataset has a column of that name.
  - Applied: a select's default values, or with `default_to_first` its first value, which the check reads with the query Superset's filter sends (its pre-filter and the defaults of the filters it depends on included); a range's bounds; a time range, on the chart's time axis or main time column; a time grain, on the chart's time axis.
  - Each chart's result names the defaults it ran with (`with the dashboard's filter defaults: Carrier = All carriers`), and lists them under `filter_defaults` in the apply report, present only when there are some. A default the check can't apply is named as not applied: a time column filter's, or a first value it could not read.
  - A chart the dashboard shows empty on load, such as one under a default time range the data doesn't reach, is now named empty.
- `decompile` (and so `adopt`) no longer reports the values an untouched table stores as settings it can't carry: HTML rendered, columns fixed in place, paging in the browser, no percentage metrics, and bars coloured by sign. A table saved in Superset reads back without a loss; each of these is still named when changed.
- `check`, `apply` and `plan` (and the MCP tools) warn when a table or pivot uses something its Superset release draws differently. Before, the dashboard came out different from the spec with no word. On 4.1.4, 5.0.0 and 6.0.0:
  - a table rule's `apply_to` paints the rule's own column instead;
  - `paint: "text"` fills the cell with the dark text shade instead, under the cell's own dark text, so the value is hard to read;
  - each band fades by the value's distance from its threshold instead of one solid colour (an `=` rule stays solid);
  - a dark `color` fill keeps the cell's dark text, where 6.1 turns it white.

  On 4.1.4 and 5.0.0, a table's `hidden` columns show. Each warning says what that release draws.
- `decompile` names a colour rule that Superset 6.1 fades by distance (its rule editor turns the gradient on by default). The spec paints a rule one solid colour, so the next apply changes how it looks.
- The design brain is version 15: `chart.format-bands` reads the bounds of the new operators, so `>= 80` and `<= 80` share 80 and `> 80` and `<= 80` share nothing.
### Added

- A sketch can hold section headers and notes. A legend letter stands for a chart's name as before, or for a header (`{"header": "Monthly sales", "size": "large"}`) or a markdown block (`{"markdown": "Source: the ledger", "height": 1.6}`), drawn like a chart. A header is one line: drawn across the whole sketch it titles the band below it, as a header row does in `rows`; drawn above or below charts it sits in their column, and alone in a narrower slot it gets a column of its own. A markdown block takes its drawn width and height, or its own `height` in fifths of a unit. `decompile` (and so `adopt` and `plan`) reads headers and text blocks in a section of stacked charts back into its sketch, where it used to flatten the columns. Specs without blocks compile to the same bytes. See [the layout guide](docs/LAYOUT-GUIDE.md#section-headers-and-notes).

### Fixed

- `decompile` (and so `adopt`) reads columns of stacked charts side by side, such as two columns of two charts each, back as one row of columns. Before, the sketch it drew for them split into one row per level of the stacks, so a copy laid them out as rows.
### Added

- A waterfall chart (`"type": "waterfall"`, Superset's Waterfall): steps that add up to a running total, such as a bridge from last year's revenue through each driver to this year's. Compiled, decompiled, compared by `plan` and checked by `apply` on 4.1.4, 5.0.0 and 6.1.0.
  - `x_column` holds the steps (or a time column, one bar per `time_grain` period), `metric` the one measure they add, and `groupby` an optional breakdown per x value.
  - `increase_color`, `decrease_color` and `total_color` take green, amber, red or any `#RRGGBB`, and replace Superset's stock green, red and grey on every release. `show_value`, `show_legend`, `number_format`, axis titles, `x_label_rotation` (0, 45 or 90) and `x_label_format` set the rest.
  - On Superset 6.1.0 or later, `steps` lists a bridge's values in its own order and `closing` names the row that closes it, drawn last as the running total in the total colour: `"steps": ["FY2025", "Price", "Volume"], "closing": "FY2026"`. The first step rises from zero; `"opening": "FY2025"` draws the opening row as a total too, on a dashboard whose theme keeps zero on the value axis (`{"echartsOptionsOverridesByChartType": {"waterfall": {"yAxis": {"scale": false}}}}` in its JSON), which `check` and `apply` verify (`waterfall_opening_axis`). Older releases draw a running total after every step, so `check`, `apply` and `plan` refuse `steps` there. `total_label`, `increase_label` and `decrease_label` name the bars on 6.1.0 and warn before it.
  - `apply`'s data check says when a bridge's opening, step or closing has no row, a value isn't in `steps`, or the closing row doesn't equal the steps added up (Superset draws the sum either way).
- A box plot (`"type": "box_plot"`, Superset's Box Plot): a measure's distribution in each group, its median, quartiles, whiskers and outliers, such as the daily sales of each month. Compiled, decompiled, compared by `plan` and checked by `apply` on 4.1.4, 5.0.0 and 6.1.0.
  - `distribute_across` holds the columns whose rows are the observations (a time column at `time_grain`, e.g. a day each), `groupby` the columns that get a box each, `metrics` the measure.
  - `whiskers` is `"tukey"` (Superset's default), `"min_max"` (no outliers) or two percentiles such as `[5, 95]`, which every release takes.
  - `number_format`, `x_label_format`, axis titles, `x_label_rotation` and `color_scheme` set the rest; `row_limit` is a Superset 6.0.0 control, and warns before it, where a dashboard's query still stops at it but Explore leaves it out.
  - `apply`'s data check says when the observations reach the row limit (10,000 unset on Superset 6.x), so the boxes miss some.
- The design brain is version 9. A waterfall gets `chart.waterfall-additive` (warn: `AVG`, `MIN`, `MAX` or `COUNT_DISTINCT` steps don't add up), `chart.waterfall-steps` (warn: past ~12 bars), `chart.waterfall-order` (info: a categorical waterfall without `steps` reads A to Z), `chart.waterfall-colors` (info for Superset's stock colours, whose green is 2.2:1 against the panel; warn for a colour under 3:1) and `chart.waterfall-axis-titles` (info: Superset puts the titles over wide or rotated tick labels). `advise --fix` fills a waterfall's `show_value` when it has at most 12 bars, and `number_format` `,.0f` on a count. A box plot gets `chart.box-plot-observations` (warn: grouped by a period at that period's grain, each box holds a few observations) and `chart.box-plot-groups` (warn, with `--profile`: past ~20 boxes). `chart.ordinal-order` reads a box plot's groups, and with `--profile` stands down for a column the dataset reports numeric or temporal. The brief's chart-choice guide says when a waterfall is the right form, and when a box plot beats a line of P50 and P90.
- `tools/extract_panel_contract.py` extracts the waterfall's and the box plot's options for the params contract from their control panels at each release; `tools/ci_live_waterfall.py` builds a bridge on a virtual dataset in CI.

### Fixed

- `decompile` reads Superset's stored defaults for data zoom and the series limit (`zoomable` false on time-axis and mixed charts, no `series_limit` on a pivot) as unset; before, `params not preserved` named them on charts saved in Explore. A zoomable chart or a series limit is still named.
- `plan` reads a `trend_color` written as the hex of a named shade (`#1B7F3B` for green) as equal to the dashboard, which decompile reads back as the name; before, the chart showed as changed after every apply. The waterfall's colours compare the same way.
- `plan` is clean straight after `apply` for a chart height in fifths of a unit and for an aggregate written as custom SQL. A height of 4.6 (as `absorb` writes after a resize in the UI) read back rounded to 5, and `SQL(MAX(col)) AS Label` read back as `MAX(col) AS Label`, so every such chart showed as changed after each apply. `decompile` now reads both back as written; custom SQL typed in Superset's metric popover still reads back as `MAX(col) AS Label`. An aggregate written with spaces (`SUM( qty )`, which is also the label Superset shows) reads back as written too, so a table's settings keyed by that label (number format, header, alignment, a rule painting it) are no longer dropped.

- `plan` reads alike the spellings `apply` stores alike, so none of them shows as a change after apply: Superset's own defaults written out (`"time_range": "No filter"`, `"number_format": "SMART_NUMBER"`, `"x_label_format": "smart_date"`, `"x_label_rotation": 0`, a table's `"date_format": "smart_date"`), `COUNT(*) AS N` and `SQL(COUNT(*)) AS N`, a named colour's hex (`"#1B7F3B"` for a green trendline, `"#ACE1C4"` for a green colour rule on a table or pivot), a height between fifths, an empty `metrics` or `groupby` list on a table, a filter's pre-filter time range of `"No filter"`, and blank text. `decompile` leaves a big number's `SMART_NUMBER` format out, as it does on every other chart.
- `plan` compares a table's `hidden` labels as a set. Decompile reads them back in the order Superset stores them, not the spec's, so a table hiding two or more columns showed as changed after every apply.

### Added

- `sort_by` on a categorical bar ranks it by something other than its first metric, largest first, on every supported release. `"total"` ranks stacked or grouped bars by their sum. Before, Superset drew several series in name order: two stacked metrics on 4.1.4 and 5.0.0, and a grouped bar on every release. A metric (`"sort_by": "SUM(revenue)"`) ranks by its value and orders the query too, so a `row_limit` keeps the top bars by it; it need not be drawn, so two charts can share one order. `advise` warns when a `row_limit` cuts a bar ranked by `"total"`, since the query is still ordered by the first metric.
- A threshold on a horizontal bar: `annotations` there now say what Superset draws, a line standing upright at its value (a 1.0x threshold, a 4-hour limit), seen on every supported release, and the live CI fixture builds one.
- Heatmap axes: `x_label_every` and `y_label_every` label every Nth column or row, counted from the first, so an hour axis with `"x_label_every": 6` reads 0, 6, 12, 18 instead of Superset's uneven automatic spacing. `left_margin` leaves room, in px, left of the row labels. Every supported release takes all three; `decompile` reads them back and `plan` reports a change made in the UI.

### Changed

- Documented: no supported release gives one line of a mixed chart its own width or dash. The plugin has no such control and its transform passes none, and 6.1's ECharts Options replace the series list whole. Set a ghost line apart by colour with `dashboard.label_colors`, or draw a fixed level as an annotation.
- Design brain 12: `advise --fix` fills `left_margin: 16` on every heatmap (`default.heatmap-label-room`). On Superset 6.1.0 a heatmap's longest row label lost its first letters at the card's edge, and a theme can't reach the heatmap there. Write `left_margin` yourself, or ignore the rule for a chart, to keep your own value.

### Fixed

- `decompile` names a bar ranked by its series' minimum, maximum or average, or ranked smallest first; before, apply quietly put the largest-first ranking back.
- `decompile` (and so `adopt`) names more chart settings it can't carry when they differ from Superset's defaults: big-number font sizes and a hidden trendline, a smooth or step line, horizontal bars on a time axis, a pie's radius and hidden labels, a funnel's labels, tooltip and percentage calculation, treemap labels, a heatmap's axis sort, legend, margins, label intervals and value bounds, a histogram's normalize setting, and any chart's currency format. Before, they were dropped without a note.
### Upgrading from 0.5

- A spec with a mixed chart's `kind: "area"`, a trendline's `rolling_type` or `y_axis_truncate`, a big number's `date_format` or a heatmap's `x_order` or `y_order` is refused by chartwright 0.5.0 and earlier (`literal_error` or `extra_forbidden`); upgrade CI before committing one.
- The design brain is version 10. `data.rolling-window-span` is a new warn-level rule, so `--design strict` can newly block a rolling trendline KPI whose time range can't hold its window. `advise --fix` fills `compare_suffix` as "vs prior 12 months" where a 12-step rolling window compares with the 12 steps before, and, with column types (`--profile`), `date_format` on a big number of a date column. It also removes a search box it filled on a table whose rows all show (below), so a spec it fixed before shows that change in its diff.

### Added

- Filled areas on a mixed chart: `"kind": "area"` on query `a` or `b`, with `opacity` for the fill (0 to 1; Superset's default is 0.2). A line draws over an area whichever query holds it, and at opacity 1 the area's edge disappears into its fill, so solar output under a net-load line reads as one solid shape; give it a light colour with `label_colors`. `decompile` reads Superset's "Area chart" box back as `kind: "area"`.
- A rolling window on a trendline KPI: `rolling_type` (`sum`, `mean`, `std`, or `cumsum` for a running total) with `rolling_periods`, the window in time-grain steps. At P1M, `"rolling_type": "sum", "rolling_periods": 12` shows a trailing-12-month total, and `compare_lag: 12` compares it with the 12 months before. Every point is a whole window; set `rolling_min_periods` to also draw the partial windows of the first steps.
    - `advise` warns when the chart's time range, or a defaulted dashboard time filter it loads with, is too short to hold the window and the comparison (`data.rolling-window-span`), and apply's data check warns from the time buckets the chart really has.
- A big number shown as a date: `"date_format": "%a %-d %b %Y"` on a `big_number_total` shows `MAX(updated_at)` as "Sat 3 Oct 2026", for a tile that says how fresh the data is. It works on a date or timestamp metric and on a number of epoch milliseconds, and replaces `number_format`. With column types (`advise --profile`), `advise --fix` fills it on a big number of a date column's `MIN` or `MAX`, which Superset shows by default as its day alone ("Tue 31").
- Heatmap axis order: `x_order` (left to right) and `y_order` (top to bottom), each `a_to_z`, `z_to_a`, `value_asc` or `value_desc`. `"y_order": "a_to_z"` puts the first label on top, such as a cohort triangle's oldest cohort; omitted, the labels keep running A to Z from the bottom up. On Superset 6.1.0 a value order ranks each label by its total; older releases order by the cells, so `check` warns there. `decompile` reads the order back.
- A trendline KPI fitted to its values: `"y_axis_truncate": true` draws the trendline from its lowest value to its highest instead of up from zero, so a trailing-12-month total that moves a few percent shows the movement rather than a flat line over a solid block. `decompile` reads Superset's unticked "Start y-axis at 0" back as it.
- Apply's data check names the values a chart's `IN` filter lists that return no rows, on a column the chart groups or draws by: a line chart that lists four zones where two have data now says which two it draws nothing for. A series limit that drops values on purpose, or a time column, is left alone.

### Changed

- The design brain adds no page-size picker, pager or search box to a table whose rows all show in its panel. `advise --fix` now fills a search box only on a raw table that pages, where the box sits in the bar the page-size picker already draws, and removes one it filled on a table whose rows all show. `advise` names a `page_length` or `search_box` you wrote on such a table (`size.table-chrome`, info), since Superset draws a page-size picker for any page size, even over one page; set `page_length` to 0 or leave it out to show every row with no picker.

### Fixed

- `decompile` (and so `adopt`) names more chart settings it can't carry when they differ from Superset's defaults: big-number font sizes and a hidden trendline, a smooth or step line, horizontal bars on a time axis, a pie's radius and hidden labels, a funnel's labels, tooltip and percentage calculation, treemap labels, a heatmap's legend, margins, label intervals and value bounds, a histogram's normalize setting, and any chart's currency format. Before, they were dropped without a note.

- `decompile` (and so `adopt` and `plan`) no longer lists a mixed chart's untouched query B settings (marker size, time comparison type, metric truncation, rolling window, series sort), which a chart saved in Superset stores beside query A's, as settings it can't carry, nor a trendline KPI's untouched Force date format. It reads the older `echarts_timeseries_line` and `echarts_timeseries_bar` series types, which Superset draws as a straight line, as `kind: "line"`, with no loss.

- `standards apply` no longer adds a header or footer row that the body already holds at its edge (the last rows for a footer, the first for a header). A dashboard built in the UI has no header or footer, so `decompile` and `adopt` read its legal line as a body row, and the standard's footer was then added below it, showing it twice. `--claim` moves such a row into the header or footer and records it; `explain` and the apply summary name it.

- `chart.format-bands` read an `=` rule as covering every value above its target, so a pivot with `= 1` in red and `> 5` in green was reported as overlapping bands.

- `decompile` (and so `adopt`) no longer reports colour rules kept on a trendline KPI that was switched from a Big Number in Superset as a loss: the trendline never draws them.

## 0.5.0 (2026-10-04)

Take over a dashboard built in the UI where it is, with everything the first apply resets listed up front. Save each chart's query so Superset's CSV reports work on the charts the tool builds. Decompile names the settings it can't carry and keeps stacked charts; backups record their instance, prune themselves and clean up after a restore.

### Upgrading from 0.4

- `apply` now keeps the newest 50 backups per profile and dashboard and deletes older ones after each new backup. Set `CHARTWRIGHT_BACKUP_KEEP` to keep more, or to 0 to keep them all, before upgrading if you rely on older backups.
- `restore` refuses a backup taken on another instance than the profile points at (`other_instance`); pass `--to-other-instance` to restore it there on purpose. Backups taken before 0.5.0 carry no record of their instance and restore as before, with a warning.
- A spec with `dashboard.adopted` is refused by chartwright 0.4.0 and earlier (`extra_forbidden`); upgrade CI before committing one.
- `apply` now keeps a chart's saved query (which CSV and text reports and the chart data API read) when it writes the same options the chart already stores; before, every in-place update cleared it. `restore` puts a backup's saved queries back.

### Added

- `chartwright save-queries <spec> --profile P`, `apply --save-queries` and MCP `build_dashboard(save_queries=true)`: save each chart's query, so Superset's CSV and text chart reports and its chart data endpoint work on the charts the tool builds. A headless browser opens each chart once in Explore and saves the query Superset's own frontend builds for it, exactly as Explore's Save does, on every chart type and release; each saved query is then run through the endpoint reports use. A later apply keeps it while the chart's options stay the same, and clears it when they change, so it never goes stale. Needs the optional extra `pip install 'chartwright[visual]'` and `playwright install chromium`.
- `chartwright adopt <dashboard> --profile P -o spec.json` takes over a dashboard built in the UI where it is. The spec names that dashboard and its charts by their own ids (`dashboard.adopted`), so `apply` updates the same dashboard: same id, same address, same chart ids, so links and embeds keep pointing at it. Adopt changes nothing in Superset.
  - It lists under `resets` what the first apply resets because a spec can't hold it, and refuses while there are any, unless you pass `--accept-reset`:
    - everything decompile can't carry;
    - each chart whose stored options the first apply rewrites, with the option keys (adopt compiles the spec against the instance to find them);
    - the saved queries of those charts;
    - tab-scoped filters, tab ids and native filter ids;
    - dashboard settings the spec can't express: per-chart and dashboard-wide cross-filter scopes, charts exempt from auto-refresh, expanded charts, staggered refresh and the colour namespace.
  - The spec keeps the dashboard's owners, so the first apply doesn't leave only the account that applies.
  - It refuses a dashboard with no usable URL name, with two charts of one title, or with charts that also sit on other dashboards (unless `--allow-shared`), each time saying what to do instead.
  - `plan` on an adopted spec compares what apply will write with what each chart stores, and lists the differing option keys under `chart_option_changes`; dashboard settings the first apply replaces are listed under `dashboard_settings_changed`.
  - Charts that leave an adopted spec come off that dashboard and are never deleted; charts the tool created after adoption are deleted as on any tool-built dashboard, unless another dashboard uses them.
  - `restore` also accepts the tool's own backups, which covers the first backup of an adopted dashboard, and finds charts renamed since the backup.
  - MCP: `adopt_dashboard` (fifteen tools).
- `chartwright decompile` has help text in `--help`.

### Fixed

- `decompile` (and so `adopt`, `plan` and `redesign`) reads charts stacked beside a taller one (a Superset COLUMN) back as a `sketch` layout, which holds them, instead of flattening them into one row. The decompiled spec compiles to the same layout, so adopting a dashboard built in the UI with stacked charts no longer rearranges them. A section that also holds text, headers or dividers stays rows, and the flattening is still named.
- The rule table and the rule registry mark `size.table-window` and `size.pivot-window` as fixable: both raise a chart's height under `advise --fix`, which they always did. A test now fails when any rule emits a fix while registered as not fixable.
- `restore` (and the automatic restore after a failed apply) deletes the charts the tool created after the backup that it leaves on no dashboard, such as the new chart of a failed apply; before, they stayed behind as orphans. Charts made in Superset are never deleted.
- A GET or PUT the server drops without answering (a pooled connection it had already closed, as a recycled gunicorn worker or an idle timeout does) is sent once more instead of failing the run. A chart query (`POST /api/v1/chart/data`) only reads, so it is sent once more too; other POSTs and DELETEs are never repeated.
- `decompile` (and so `redesign`, `plan` and `adopt`) names chart settings it can't carry when they differ from what an untouched Superset chart stores (a rolling sum, a forecast, a legend margin, a series sort, among others); before, it dropped them without a note. Superset's own defaults stay unreported, and so does any setting the spec writes back with the same value.
- `decompile` names a filter scoped to some tabs, which the spec scopes to the whole dashboard.
- `decompile` reads a big-number total's subtitle on Superset 6.0 and later, which stores it as `subtitle` rather than `subheader`.
- A chart added to a chartwright dashboard in Superset no longer makes the next `apply` fail on Superset 4.1.4 and 5.0.0. `apply` takes it off the dashboard on every release, leaves the chart itself under Charts, and names it in a warning; on 6.1.0 the chart came off before too, but without a word. To keep such a chart on the dashboard, add it to the spec.
- `restore` (and the automatic restore after a failed apply) now fails, naming the charts, when it can't put some charts' settings back; before, it reported success with the charts still on the newer settings.
- `restore` now leaves the dashboard linked to exactly the backup's charts on 4.1.4 and 5.0.0. Before, a chart linked since the backup, such as one a failed apply had just added, stayed linked to the restored dashboard.

## 0.4.0 (2026-10-04)

Standards gain recorded exceptions, a minimum Superset release per file, a lockable classification and a Superset theme, and a browser check that locked text is visible on the deployed dashboard. A fill you edit stays yours, and renaming a chart no longer breaks a spec that had fills. The design brain is version 7.

### Upgrading from 0.3

- A spec with `dashboard.theme` is refused by chartwright 0.3.0 and earlier (`extra_forbidden`); upgrade CI before committing one.
- `check`, `apply` and `plan` now hold back a standard's content that the instance's release can't take instead of refusing the spec, and list it under `held`. They read the standards folder for this even with `--design off`; `plan` takes `--standards`.
- `standards/waivers.yaml` is no longer read as a standards file. A repository that kept a standard in a file of that name at the top of the folder must rename it.
- Printed paths use forward slashes everywhere, including `compile`'s and `redesign`'s `output`, `advise --fix`'s `written` and `apply`'s `backup`.
- Table charts no longer carry `server_page_length: 10`, so every bundle with a table changes by that one line. Superset reads the key only with server pagination on, which chartwright never turns on; the page a viewer sees is still `page_length`. `plan` reports no drift for it; only a byte comparison of bundles shows the line going.
- The design brain is version 7. When you edit a value `advise --fix` filled, `--fix` now records it as null in `design.filled` instead of dropping the record, so the field stays yours even if you delete it later. Delete the null entry to let the brain fill the field again.

### Added

- Waivers: `standards/waivers.yaml` lists the dashboards that may deviate from a lock, each with an owner, a reason and an expiry (docs/DESIGN-BRAIN.md §18, "Waivers").
  - An entry names a dashboard by its spec path (preferred: a slug is the spec author's to edit, so a slug-only match is flagged, and a waiver naming both applies only while both match), a locked rule or content item (`layout.footer[org][0]`, `dashboard.classification`), and optionally the layer whose lock it covers.
  - The finding passes and is listed under `waived` with who, why and until when; `standards apply` leaves the item as the dashboard has it. `design.ignore` still can't silence a lock.
  - An expired waiver fails `standards check` and `advise` (new rule `standard.waiver-expired`) for the specs checked, and only warns in `standards apply`, `check`, `apply` and `plan`; `restore` never reads the file. `--as-of DATE` reads expiry as of a day.
  - `standards check --report` lists expired, expiring (`--expiring-within`, 30 days by default) and unmatched waivers; every `standards check` lists past-dated entries under `waiver_warnings`.
  - Malformed entries, a missing owner, reason or expiry, unknown rules, items or layers, and duplicates are errors naming the entry.
- `min_superset` in a standards file: the release its content is for. `check`, `apply` and `plan`, on the CLI and through MCP, hold back for each instance the standard's content its release can't take (a later `min_superset`, or a theme before 6.0) and report it under `held`; an author's own field is never held. `standards check --superset-version` shows the same offline.
- `dashboard.theme`: a Superset 6.0+ theme by name, resolved on each instance at the resolve stage (`theme_not_found`, `theme_ambiguous`, `theme_lookup_failed`), compiled as the target's `theme_id`, read back by decompile and compared by `plan` when the spec names one. 4.1.4 and 5.0.0 refuse it. Omitted, the theme chosen in Superset stays.
- Content slots `theme` and `classification`. A standard can assign a classification and lock it: changing or dropping it is a `standard.content-locked` error, and its classification rows follow the standard's value.
- `chartwright standards verify-visible <spec> --profile P`: opens the deployed dashboard in headless Chromium and checks every locked header and footer line is on the page and readable (rendered, sized, on the page and not indented off it, not clipped, transparent, blurred, tiny or covered, and with enough contrast); exits 1 naming each hidden line, and reports a timeout, an untrusted certificate or a browser failure as a typed error. A `ca_bundle` profile keeps certificate checks on, against the operating system's certificates. Needs the optional extra `pip install 'chartwright[visual]'` and `playwright install chromium`.
- MCP: `as_of` on `standards_check`, `standards_apply`, `advise_spec`, `fix_spec`, `check_spec` and `build_dashboard` (`fix_spec`, like `advise --fix`, fails on an expired waiver); `superset_version` on `standards_check`; `held` on `check_spec`, `build_dashboard` and `plan_dashboard`. The tool count is unchanged.
- `standards show` names each chain file's `min_superset`.
- Rule `default.stale-record` (info, fixable): reports a `design.filled` entry for a chart you renamed or removed, or a field the chart no longer has, and `advise --fix` drops it. To keep the brain updating a renamed chart's fills, rename its entry with the chart.

### Fixed

- Renaming or removing a chart that had brain fills no longer makes the spec fail validation; you can still build it, and `advise --fix` removes the stale entry.
- An edited fill that you later delete stays deleted; before, the next `advise --fix` filled it again.

## 0.3.0 (2026-10-04)

Most of what Superset lets you set on a dashboard or chart can now be written in a spec. The design brain fills sensible display defaults into the spec. Standards let an org layer its dashboard rules and content from org to team. The design brain is version 6.

### Upgrading from 0.2

- `plan` now compares more settings: dashboard CSS, colour scheme, description and certification, owners when a spec names them, and tags regardless of order. A dashboard whose CSS or settings were edited in Superset now shows those edits as drift, and the next `apply` replaces them with the spec's.
- Strict design gates (`advise --strict`, `check`/`apply --design strict` and the MCP strict modes) no longer take anything from a personal `design.yaml`. Put rule settings a team relies on in a standards file instead.
- `check`, `apply` and `plan` find out which Superset release they talk to, and refuse fields that release can't take (`tags` before 6.0, `show_chart_timestamps` before 6.1) before writing anything. State the release with `--superset-version` when it can't be detected.
- A spec that uses `design.standard`, `owners`, `layout.header` or any other new field is refused by chartwright 0.2.1 and earlier, so upgrade CI before committing such specs.
- MCP `build_dashboard` now returns the design advice block.

### Added

- Standards: design-review settings a repository shares across its dashboards, in a
  `standards/` folder of YAML files (docs/DESIGN-BRAIN.md §18).
  - Each file is one standard: `name`, an optional parent in `extends` (at most three
    files in a chain, org → unit → team, with the spec's own `design` block after them),
    and design.yaml's `params`, `audiences`, `severity` and `disable`, plus `locked` rules
    and parameters.
    `default: true` marks the standard for specs that name none.
  - Down a chain, `params` and `audiences` override per parameter, `severity` per rule;
    `disable` and `locked` add up. Below the layer that locks it, a locked rule can't be
    disabled or have its severity lowered, and a locked parameter can't be set. A locked
    parameter needs a value by the layer that locks it (in `params`, or under every
    audience), so a spec's `design.audience` can't move it.
  - Cycles, an unknown parent, a chain of four files, unknown rule ids and parameters,
    a lock on an unset parameter, and a lower file loosening a lock are errors that name
    the file.
  - The folder is `--standards DIR`, or the one `standards/` folder at or above the spec
    inside its git repository; the MCP server reads `$CHARTWRIGHT_STANDARDS_DIR`, or
    discovers the folder from its working directory without it. A
    `standards/` folder counts only when one of its YAML files has a `name` key, so a
    repository's unrelated standards folder changes nothing.
- `design.standard`: the standard a spec follows. **An older chartwright (0.2.1 and
  earlier) rejects a spec that sets it** (`extra_forbidden`), so run CI on a release that
  knows it. The field never reaches the bundle: a spec compiles to the same bytes with or
  without it.
- `advise`, `advise --fix`, `explain`, and the advice `check` and `apply` carry, apply the
  spec's standard between the audience preset and the spec's `design` block, in strict
  gates too. Without a standards folder nothing changes.
  - A rule the standard locks can't be silenced by `design.ignore`, `--ignore`,
    `design.yaml` or a fractional height; refused ignore entries are reported.
  - The advice gains a `standard` block (name, chain, how the spec got it, locks, refused
    ignores) and, on each finding, `layer` (what set its severity) and `locked`. Outside
    a strict gate, the `overlay` block lists what a lock set aside.
  - `explain` names the chain in its header and JSON.
- `chartwright standards check <specs>`: the review over many specs, each under its
  standard, with `design.yaml` set aside; exit 1 on an error finding, or a warn with
  `--strict`. `--report` prints the fleet report: per spec its standard, pass or fail,
  finding counts by rule and severity, and the locks it hit; then the totals. JSON
  without a top-level `spec_version` (a `package.json`) is listed under `skipped`, here
  and in `standards assign`.
- `chartwright standards show [NAME | --for SPEC] [--json]`: a standard after `extends`,
  each key with its value, the layer that set it and whether it is locked.
- `chartwright standards assign <specs> --standard NAME`: writes `design.standard` into
  each spec, leaving files that already name it untouched.
- MCP: `standards_check` and `standards_show`; `advise_spec`, `fix_spec`, `explain_spec`,
  `check_spec` and `build_dashboard` apply the spec's standard.
- Standard content (docs/DESIGN-BRAIN.md §18, "Content"): a standard's `content` holds
  header and footer rows, CSS, a colour scheme, label colours, certification and number
  formats per metric label, plus banner rows per lifecycle state and footer rows per
  classification. `locked.content` locks slots.
  - `chartwright standards apply <specs>` writes it into each spec that follows the
    standard: rows and label colours add up across layers, the colour scheme and
    certification are the innermost layer's, and `dashboard.css` gets one marked block
    per layer (`/* cw:std org <hash> */ ... /* cw:end org */`) with the author's CSS
    after them. It writes only files whose data changed and prints a summary grouped by
    standard and item; `--json` gives the full result.
  - `design.standard_written` records each item written, rows and CSS blocks by hash,
    the rest by value. Per item: the value written is refreshed when the standard
    changes; an unlocked item the author edits or deletes is theirs from then on; a
    locked one that differs is an error in `standards check` and is left as it is until
    `standards apply --locked` rewrites it, showing what was there.
  - `--check` writes nothing and exits 1 when a spec lacks locked content as the
    standard has it now; unlocked changes are listed and pass. `--check --strict`
    exits 1 on any change apply would make. `--claim` records content
    a decompiled or adopted spec already holds; apply never adds it twice.
    `--standard NAME` limits a run to one team's specs.
  - New rules, design brain 6: `standard.content-locked` (error),
    `standard.content-stale` (warn), `standard.content-released` (info),
    `standard.classification` (error) and `standard.css-hides` (warn, a heuristic for
    the common ways CSS hides an element; locked with the content).
  - A field has one owner: `design.filled` and `design.standard_written` never record
    the same field; apply takes a brain fill over, and the brain never fills a field a
    standard holds.
  - `explain` gains a dashboard content section; `standards show` lists content.
  - MCP `standards_apply` (`check`, `locked`, `claim`). Fourteen tools.
- `dashboard.lifecycle` (`state`: active, deprecated or sunset; optional `successor`
  slug and `sunset_date`) and `dashboard.classification`: spec-only, never in the
  bundle, and what a standard's banner and footer rows key off. **0.2.1 and earlier
  reject a spec that sets them, or `design.standard_written`.**
- `dashboard.owners`: the dashboard's owners, by Superset username or by the email
  address of each account, e.g. `["jdoe", "ana@example.com"]`. Superset's import makes
  the importing account an owner and its bundle has no owners field, so `apply` now
  sets them after the import, through the dashboard API.
  - Every owner is resolved to an account before anything is written. An unknown one
    is an `owner_not_found` error at resolve, with the closest accounts as
    `candidates`; one that names two accounts is `owner_ambiguous`.
  - Usernames need an instance whose API returns them: 6.1.0 by default, 4.1.4 and
    5.0.0 only with `FAB_ADD_SECURITY_API` on. Emails work on all three.
  - The account that applies stays an owner: Superset adds it on every import, and a
    non-admin account can't import over the dashboard again without it. Run `plan`
    with the same profile.
  - `decompile` reads the owners back from the live dashboard, and `plan` lists
    `owners` in `dashboard_settings_changed` when they differ.
  - Omitted, `apply` leaves the live owners alone and `plan` doesn't compare them;
    the bundle is the same either way. Chart owners are left as Superset sets them.
- `layout.header`: rows above the rows, tabs or sketch, outside any tab, so a tabbed
  dashboard shows them above every tab; the mirror of `layout.footer`.
  - Header rows compile under their own ids (`ROW-sdc-header-1`, ...), so adding,
    editing or removing a header leaves every body and footer node as it was, chart
    placeholders included.
  - Decompile reads rows above a dashboard's tabs as its header, including rows put
    there in Superset's UI, which used to flatten the tabs into rows with a loss.
    Without tabs, only rows compiled as a header read back as one.
  - `plan` compares it with the rest of the layout. The design review checks header
    rows like body rows and counts the header's height in every tab's fold budget.
  - A spec without a header builds the same bundle as before.
- `dashboard.css`: the dashboard's CSS, the same thing as Superset's own "Edit CSS",
  written into the import bundle's `css` field (which 4.1.4, 5.0.0 and 6.1.0 all import).
  Decompiled (blank CSS reads back as omitted) and compared by `plan` (`css_changed`).
  Spec-owned like the title: CSS edited in the UI shows up in `plan`, and `apply` replaces
  it, as it always did when the spec had none.
- Colour rules take any `#RRGGBB` besides `green` / `amber` / `red`, e.g.
  `"color": "#0057B8"`, painted as written for cells and text alike (stored upper case).
  The names keep their shades. Decompile reads a named shade for the rule's paint back as
  its name and any other hex as itself, where it used to drop the rule as a loss.
- Chart display options, each optional and written only when set, so an existing spec
  builds the same bundle. All decompile back and show up in `plan` when changed in the UI.
  - Axes on line, bar, area, scatter, categorical bar and mixed charts: `x_axis_title`,
    `y_axis_title`, `y_axis_min`, `y_axis_max`, `y_axis_truncate` (fit the axis to the
    data instead of including zero) and `y_axis_log`; a mixed chart's second axis takes
    `y_axis_title_secondary`, `y_axis_min_secondary`, `y_axis_max_secondary` and
    `y_axis_log_secondary`. A histogram takes the two titles.
  - `show_value` (values on the bars or points), `stack` (`true`, or `"stream"` and
    `"expand"` where the chart offers them), `only_total` and `contribution` (`"row"`
    with `stack` is a 100 % stacked chart). A mixed chart's queries take `show_value`,
    `stack` and `only_total` (6.0+) each.
  - `series_limit`, `series_limit_metric` and `series_limit_ascending`: the top N series
    of a `groupby`, on the timeseries charts, the categorical bar and each mixed query.
  - `show_legend`, `legend_position` (`bottom`, `left`, `right`) and `legend_type`
    (`plain`), on the timeseries, bar, mixed, pie and funnel charts; a heatmap takes
    `show_legend`.
  - `category_sort` (`"asc"` or `"desc"`) on a categorical bar: bars in the order of
    their categories, such as hours, instead of by the first metric.
  - `time_range` on every chart type, e.g. a "Last 30 days" KPI or table.
  - Trendline KPIs: `compare_lag` and `compare_suffix` ("+4 % vs last month"),
    `trend_color` (green, amber, red or `#RRGGBB`) and, on Superset 6.0+, `subtitle`.
  - Tables: `page_length`, `show_totals`, `search_box`, and per label `column_align`,
    `column_widths` and, on Superset 6.0+, `column_headers`.
  - Pivots: `aggregate_function` (Average, Median, Sum as Fraction of Total, ...),
    `row_order` and `column_order` (by label or by value), `row_subtotals`, `transpose`
    and `metrics_layout`.
  - Heatmaps: `show_values`, `color_scheme` (Superset's sequential schemes),
    `number_format`, `show_percentage` and `normalize_across`. Pie, funnel and treemap:
    `label_type` and `number_format`; pie also `show_total` and `labels_outside`.
  - Line: `markers`, `marker_size`, `area` and `opacity`; area: `markers`, `marker_size`
    and `opacity`; scatter: `marker_size`.
- The design review knows the new options: a vertical bar sorted by category that labels
  every category (hours, ranks) passes `chart.vbar-categories`, and the fix for one that
  drops labels is `x_label_every`, not a flip to horizontal; a paged table needs room for
  one page; `series_limit` quiets `chart.series-limit`; a trendline with its own
  `time_range` quiets `chart.trend-grain`.
- Dashboard settings in the spec: `color_scheme`, `description`, `certified_by`,
  `certification_details`, `published` (`false` for a draft), `refresh_frequency`
  (seconds), `filter_bar_orientation` (`"horizontal"` puts the bar above the charts; 4.1.4
  and 5.0.0 need Superset's `HORIZONTAL_FILTER_BAR` flag), `tags` (Superset 6.0 or
  later) and `show_chart_timestamps` (6.1 or later). `plan` names each one that differs
  in a new `dashboard_settings_changed` list. Omitted `tags` are left alone by `apply`
  and `plan` alike; `[]` clears them.
- Chart settings: `color_scheme` (on the chart types whose panel has one), `description`
  (viewers open it with "Show chart description"), `certified_by`,
  `certification_details`, `cache_timeout`, `display_name` (a shorter title shown on the
  dashboard card, Superset's `sliceNameOverride`) and `tags` (6.0 or later). A re-apply
  updates description, certification and cache timeout on existing charts too.
- `check`, `apply` and `plan` hold a spec to the instance's Superset release. When the
  spec uses a release-specific field, they read the version (`/version` on 6.1, the
  sign-in page on 4.1 and 5.0) and refuse `tags` before 6.0.0 and
  `show_chart_timestamps` before 6.1.0 with a resolve-stage error, before anything is
  written: `superset_version_too_old`, or `superset_version_unknown` when the instance
  doesn't say. Fields older releases ignore (`x_label_every`, a trendline's `subtitle`,
  `column_headers`, a stacked mixed query's totals-only labels) come back as
  `version_warnings`. `--superset-version` (the MCP tools' `superset_version`) states
  the release instead; `compile --superset-version` runs the same check offline.
- A colour scheme name Superset doesn't ship gets a `narrative.color-scheme` warning (with
  a did-you-mean for a case slip); a deployment's own registered schemes are accepted.
- `annotations` on line, bar, area, scatter and mixed charts: goal and trend lines, as
  Superset's FORMULA annotation layers, e.g. `{"name": "Goal", "value": 80, "style":
  "dashed"}`, with colour, width and opacity. Decompile keeps formula layers and names
  any other layer type as a loss, where it used to drop them silently.
- Custom SQL metrics, `SQL(100.0 * SUM(a) / NULLIF(SUM(b), 0)) AS Rate`, anywhere a
  metric goes, and custom SQL chart filters, `{"sql": "amount > 0 OR refunded"}`.
  `check` cannot see the columns inside SQL, so it lists them under `unchecked_sql`, and
  so do the `apply` report and the MCP `build_dashboard` payload: apply's data check runs
  them with the profile's rights. UI-built SQL metrics and SQL filters now decompile,
  where they used to be dropped as losses.
- Native filter controls: `description` on every filter; `dependencies` (cascading, by
  filter name); on value pickers `search_all_options`, `inverse_selection`,
  `sort_metric`; on value pickers and sliders a pre-filter (`pre_filter` conditions, and
  `time_range` with `time_column`); `charts` scoping on the time range picker; and two new
  filter types, `time_grain` and `time_column`.
- Layout: `{"header": "Revenue", "size": "large"}` and `{"divider": true}` entries between
  rows (Superset nests headers and dividers beside rows, never inside one), and
  `{"row": [...], "background": "white"}` for a row on a card.
- Design defaults: `advise --fix` (MCP `fix_spec`) fills sensible values into display
  fields the author left unset and writes them into the spec, where the diff shows them.
  Nothing changes at compile time: compile, `plan` and decompile are as before, and a
  spec that was never fixed builds the same bundle. Eight `default.*` rules, each an
  `info` finding with a fix that `check` and `apply` list too:
  - `x_label_format` from the time grain (`%b %Y` monthly, `%Y` yearly, `%d %b` daily
    or weekly over 365 days or less);
  - `compare_suffix` from the grain and `compare_lag` ("vs previous month");
  - `,.0f` for charts whose metrics are all COUNT or COUNT_DISTINCT;
  - on tables, `cell_bars: false` with id, code, year or zip columns, `page_length` set to
    the rows that fit beside the page controls when an explicit `row_limit` outgrows the
    panel, and `search_box` on raw tables of more than 20 rows when no row loses its
    place to the search bar;
  - `show_legend: false` on a single series the title names, `show_value` on a bar of 12
    bars or fewer: at least half the page wide, or on a horizontal bar, tall enough to
    space its labels.
  None writes Superset's own value; the thresholds are audience parameters
  (`search_min_rows`, `value_label_max_bars`, `value_label_min_width`, `page_min_rows`,
  `day_label_max_span_days`), tunable in `design.yaml`. The grid and value-label numbers
  were measured on rendered Superset 4.1.4, 5.0.0 and 6.1.0 (DESIGN-BRAIN §17).
- `design.filled` records, per chart, each field `--fix` filled and the value it wrote.
  While the chart still holds that value, `--fix` keeps it up to date as the chart changes
  and removes it when it stops applying. A field you write is never touched. Edit a filled
  value and it is yours; delete it and it stays deleted (`--fix` records null and fills it
  no more, until you delete that record).
- `chartwright explain <spec> [--chart NAME] [--json]`: per chart, each design-default
  field's value, whether it came from the spec, a fill or Superset, the value
  `design.filled` recorded, why, and how to change it. `advise --chart NAME` narrows a
  review, or a `--fix`, to one chart. MCP: `advise_spec` takes `chart`, and the new
  `explain_spec` tool returns the `explain --json` payload (eleven tools).

### Changed

- Each `fixed` entry from `advise --fix` and `fix_spec` gains `kind` (`"fill"`, `"repair"`,
  or `"release"` when a fill passes to the author) and a `why` line. `redesign` still
  applies repairs only and names `advise --fix` when fills are waiting. The brief and the
  skill tell an AI author to leave design defaults unset, run `--fix` before building, and
  edit the file it wrote.
- `narrative.big-number-format` no longer fires on a COUNT KPI, where
  `default.count-format` offers the same fix.
- `y_axis_max` now applies on Superset 4.1.4 and 5.0.0 as well as 6.1.0, and on every
  chart with a value axis, not only lines. It was written into the ECharts Options,
  which only 6.1.0 reads, and is now the axis bound every release reads
  (`y_axis_bounds`). A dashboard built with the old form still decompiles to the same
  `y_axis_max`; the next apply rewrites it.

### Fixed

- A `design.yaml` on one machine (`~/.config/chartwright/` or `$CHARTWRIGHT_DESIGN_DIR`)
  could loosen `advise --strict` and `check`/`apply --design strict` with no trace in the
  output: its `disable` list hid findings, its `severity` map could lower them, and its
  parameters could move thresholds. A strict gate now takes nothing from it, raised
  severities included, so the gate passes or fails the same on every machine.
  - Every advice block names the overlay in a new `overlay` entry: its path, what it
    set, each finding it changed in this run and, under a strict gate, what it set
    aside.
  - Without a strict gate the overlay applies as before.
  - MCP: `check_spec` and `build_dashboard` take `design` (`off`, `warn`, `strict`), the
    CLI's `--design`, and `build_dashboard` now carries the advice block `apply`
    carries; `advise_spec` and `fix_spec` take `strict`.
- Tables and pivots that hid their last rows passed `size.table-window`,
  `size.pivot-window`, `size.grid-fit` and the apply-time smoke warning. Their shared
  grid model was a guess. It is now measured on rendered Superset 4.1.4, 5.0.0 and 6.1.0
  (DESIGN-BRAIN §17), and counts what the old one missed:
  - a search box;
  - the page-size bar any `page_length` draws, and the pager's real height;
  - pivot header rows and the pinned totals row;
  - a horizontal scrollbar under a pivot with column dimensions.
  So these checks now warn where they used to pass. For example, a 5-row pivot by month
  at 8 units hid its last row, and a 10-row page at 12 units hides one on 6.1.0.
- A table's page size can now be set: `page_length`. The bundle carried
  `server_page_length: 10`, which Superset reads only with server pagination, so tables
  never paged by it; that key still ships, unchanged, and does nothing.
- The params contract's Mixed Chart entry listed only the keys the tool writes, so the
  drift check could not see a Mixed control missing from a release. It now lists every
  control the panel declares at 4.1.4, 5.0.0 and 6.1.0, and
  `tools/extract_mixed_contract.py` extracts it from the plugin source (`--check` says
  when the JSON no longer matches).

## 0.2.1 (2026-10-04)

### Fixed

- Charts over time (line, bar, area and scatter charts, trendline KPIs, and mixed charts
  with a time x axis) ignored both their own `time_range`, when set, and the dashboard's
  time filter. They showed every date while the filter bar counted them as filtered.
  - The next `apply` rewrites those charts, so both ranges work.
  - `plan` does not flag dashboards built with 0.2.0, so run `apply` on them even when
    `plan` is clean.
- `apply`'s data check queried every chart over all dates. It now uses each chart's own
  `time_range`, so a range that matches no rows shows up as an empty-chart warning.

## 0.2.0 (2026-10-03)

The design brain, plus the fixes found reviewing it.

### Added

- **A missing column names the real ones.** The error suggests up to three close matches
  (a case-only difference first, then by spelling) and lists the dataset's columns, up to
  50, where it used to give only their count. The JSON error carries the suggestions in a
  new `candidates` field, so an AI agent can correct the spec in one round; the MCP
  tools return the same.
- **`layout.footer`**: rows below the rows, tabs, or sketch, outside any tab,
  so a tabbed dashboard shows them under every tab. Round-trips through
  decompile and `plan`; rows below a dashboard's tabs, including ones placed
  in Superset's UI, decompile as its footer.
- **Markdown heights in fifths of a unit** (0.2 = 8 px, one Superset grid row),
  so a slim header or footer fits exactly (`"height": 1.6` is 64 px). Whole
  numbers are unchanged. Decompile reads text-block heights exactly instead of
  rounding them to whole units, so `plan` now reports a text block resized in
  the UI.
- Table colour rules. `conditional_formatting` on a table, with `apply_to`
  (another column's label, or `"row"`): the rule reads one column and paints
  another, which is how a scorecard colours each number by a status beside it
  when every row has its own goal (Superset 6.1's `columnFormatting`; a pivot
  can only colour a cell by its own value). Also on tables: `hidden` (queried,
  not displayed, e.g. that status), `number_formats` (d3, per label) and
  `sort_ascending` (a fixed row order; an ascending live sort now decompiles
  instead of being reported as a loss). New operator `=` on every colour rule.
- `"paint": "text"` on a colour rule: colour the text (e.g. an arrow) instead of the cell,
  with darker shades of green / amber / red (each >= 4.5:1 on white; Superset's picker
  colours are pastel backgrounds, unreadable as text).
- Select filters take `default_to_first` (select the first value on load),
  `sort_descending` and `required`: with year labels like "2026 (this year)", a
  Year filter always opens on the latest year, with no literal default to go stale.
- Pivot totals: `row_totals` (a total per row, as a column at the right, e.g. the
  year beside its months) and `column_totals` (a total row at the bottom).
  `measure_totals`: with several metrics, a total after each metric's block of columns
  (Superset's column subtotals, the metric being the outer column level); `row_totals`
  adds the metrics together, e.g. orders plus revenue.
- `dashboard.label_colors`: a fixed colour per series label on every chart (the
  dashboard's custom label colours, which 6.1.0 applies over the scheme and the per-view
  map). Superset otherwise assigns colours as the page loads, so a measure can change
  colour between charts and visits. Validated as `#RRGGBB`, decompiled, and compared by
  `plan` (`label_colors_changed`).
- X-axis labels on timeseries, bar and mixed charts: `x_label_format` (a d3 time
  format, e.g. `%b` for "Sep"), `x_label_every` (a label at every month, week or
  category) and `x_label_rotation`. Superset's default writes full month names and
  January as the year, at a spacing it picks from the width, then drops labels that
  collide, so 13 months read "September, November, 2026, March" with gaps that
  differ chart to chart. `x_label_every` uses two 6.1.0 controls (the grain as the
  widest tick spacing on a time axis, interval 0 on a category axis); older releases
  ignore them, and the drift check allows exactly those keys before 6.1.0 (`SINCE`).
  On a time axis it also keeps both ends labelled: 6.1.0 left the first and last month
  of a 13-month line blank (a tick on the axis edge gets no label, and Superset's forced
  last label hid the last tick's), so the panel's ECharts Options (`echart_options`,
  6.1.0) pad a line's axis like a bar's and label the ticks only. `y_axis_max` on a line
  sets the top of the value axis (e.g. 1 for a share, where the default ran to 120 %).
- Mixed chart (`"type": "mixed"`, Superset's Mixed Chart): two queries, `a` and
  `b`, each drawn as bars or a line on the primary or secondary value axis, over
  a time column (`time_grain`) or any column (a categorical axis, e.g. by cause).
  Compiled, decompiled, smoke-queried (both queries) and in the params contract
  for all three releases.
- Sub-tabs: a tab can hold `tabs` (one level deep, each with rows or a sketch)
  instead of rows, compiled as Superset's tabs-inside-a-tab and decompiled back.
- Select filters take a `default` (values selected on load, e.g. a relative
  "This year" that stays right after 1 January) and `charts` (scope the filter to
  named charts, resolved to slice ids at apply, as on range filters).
- `number_format` on timeseries and categorical bar charts (the value axis;
  it was hard-coded to Superset's smart number, so a rate read `0.9760`, not `97.6%`).
- Tables take `cell_bars` (Superset draws bars behind numeric cells by default, including
  ids and years) and `date_format` (e.g. `%Y-%m-%d`, not a midnight timestamp); pivots
  take `number_format` (cells and totals, e.g. `,.0f` for `2,570`, not `2.57k`); a mixed
  chart's series take `markers` (a line over a single category draws nothing without one).

### Fixed

- `schema/dashboard_spec.schema.json` had not changed since 0.1.0 and was missing fields
  the spec has gained. It is regenerated from `chartwright schema`, and a test fails if
  the two drift apart again.
- `chartwright.__version__` said 0.1.0. It now matches the package version, enforced by a
  test.
- Corrected text that disagreed with the code: the skill and a code docstring said 14
  chart types (there are 15) and the skill left the numeric range filter out of the
  filter bar; `LAYOUT-GUIDE.md` said the design review skips the footer (most rules
  include it); `DESIGN-BRAIN.md` §9 listed widths as auto-fixable (they are report-only).
- The login error and `docs/VERIFICATION.md` said Superset disables its password login
  API on SSO or OAuth instances. Superset's source doesn't support that: the login API
  stays registered whatever the sign-in method. Both now say what is true either way:
  Chartwright signs in with a Superset account's own password (database or LDAP) or
  Preset API tokens, never through SSO or OAuth.
- A failed build now puts the backup back by the same rule whatever the error. A chart
  update that Superset rejected after the import left the dashboard half-updated, while a
  connection drop at the same step restored it; both now restore. An error that wasn't a
  Superset error (a bug in Chartwright) skipped the restore and lost the backup path;
  it now follows the same rule and returns the normal report. Failures after the chart
  updates (linkage, filter scopes, chart queries) still leave the new version live.
- Two applies to the same dashboard within one second wrote the same backup name, and
  the second overwrote the first. Backup names now go to the microsecond
  (`20261003T141502.123456.zip`) and are never overwritten.
- When an automatic restore failed, the suggested `chartwright restore <zip>` command
  left out the required `--profile`, so pasting it gave a usage error. It now includes it.
- `docs/VERIFICATION.md` said CI runs on every push, and that the live check covers a
  range filter. CI runs on pull requests, pushes to main and manual runs, and the range
  filter is covered by the second-writer and fault-injection runs.
- `pip install "chartwright[mcp]"` installed mcp 2, which renamed `mcp.server.fastmcp`,
  so `chartwright-mcp` failed to start. It now runs on mcp 1 and mcp 2 (the extra
  allows `mcp>=1.0,<3`), and CI tests both.
- `advise` (and `advise_spec`, `plan_dashboard`) crashed with a `KeyError` on any chart in
  `layout.footer`: the design checks now see the footer, and `layout.markdown-height`
  checks and fixes text blocks in the footer and in sub-tabs too.
- Decompile wrote a spec that failed validation when a table kept settings for a column
  it no longer queries (Superset keeps `column_config` entries and colour rules after
  the column leaves the query). Those settings are now dropped and listed as losses, on
  tables and pivots alike.
- Decompile wrote both `default_to_first` and `default` for a select filter that had
  "select first value" on and a saved value, which the spec rejects. It keeps
  `default_to_first` alone: Superset picks the first value again on load.
- `restore` (and the automatic restore after a failed apply) now moves each chart back
  onto its backed-up dataset, as apply does, so the chart's dataset matches its params.
- A mixed chart on a category column became a time axis when it had a `time_grain`,
  which every chart built in Superset's UI does. The dashboard time range then stopped
  reaching it and `x_label_every` sent the time-axis control. The column's reported type
  now decides; `time_grain` decides only when the type is unknown.
- `docs/FEATURES.md` said every filter-bar control can be scoped to specific
  charts. Value pickers (`select`) and numeric sliders (`range`) can; the time
  range applies to the whole dashboard.
- A select filter with `default_to_first` showed its first value while every chart queried
  unfiltered until the viewer pressed Apply. Superset applies a value on load only for a
  filter marked `requiredFirst`, which its own form saves with "select first value"
  (`FilterBar/index.tsx`, `FiltersConfigModal/utils.ts`); the compiler now marks it too.
- Changing a chart's dataset in the spec didn't land on re-apply: the in-place
  update sent the new params only, so Superset kept the chart on its old dataset
  (`datasource_id`) while its params pointed at the new one, and `plan` reported the
  chart as changed forever. The update now moves the chart onto its spec dataset.
- A chart, dataset or dashboard name containing `&` (e.g. "Sales & Marketing")
  failed every lookup with HTTP 400 "Not a valid rison/json argument", which
  aborted `apply` at prepare. Flask-AppBuilder's JSON fallback for `q` re-parses
  the decoded value with `parse_qs`, splitting at `&` (and decoding `+` / `%`
  again); those three are now sent as JSON unicode escapes.
- Range colour rules never coloured anything: `between` compiled to the
  literal `"between"`, which is not a Superset comparator in any supported
  release (it is `'< x <'`), so the amber band of every RAG set was dead.
- Colour rules render as solid bands (`useGradient: false`). Superset's
  default fades a `<` / `>` / range colour by the value's distance from the
  threshold, so bands read as shades rather than green/amber/red.

- `"cross_filters": true|false` on the dashboard block. The tool had always
  written `cross_filters_enabled: false` into every dashboard it built, with
  no way to say otherwise; the flag is now spec-owned, decompiles back, and a
  UI toggle shows up in `plan` as `cross_filters_changed`. Default stays
  `false`, so existing specs and the golden bundle are byte-identical.
- **The design brain**, an optional layer that checks whether a dashboard
  reads well, not just whether it imports. Off with `--design off`, which
  restores byte-identical output.
  - `chartwright brief` prints design guidance to read before writing a spec,
    tuned to an audience preset (`executive`, `analytical`, `operational`).
  - `chartwright advise` reviews a finished spec against 49 rules with stable
    ids. `--fix` applies the safe geometry repairs; `--profile` adds checks
    that need live metadata, such as a time axis on a non-temporal column.
  - `chartwright redesign` decompiles a live dashboard, audits it, applies the
    safe fixes, and writes the repaired spec.
  - `chartwright calibrate` proposes recommended heights from the sizes you
    have polished by hand and absorbed.
  - `check` and `apply` gain `--design off|warn|strict`, defaulting to `warn`.
  - An optional `design` block in the spec sets the audience and suppresses
    individual rules per dashboard or per chart.
  - `~/.config/chartwright/design.yaml` tunes thresholds, disables rules, and
    appends house guidance for a whole deployment.
  - Four MCP tools covering the same ground, taking the server from six to ten.
- `data.unwindowed-history` warns when nothing bounds a timeseries dashboard's
  date range, so every load queries the dataset's full history.
- Apply-time warning when a table or pivot renders more rows than its
  configured height can show, which otherwise hides them behind an inner
  scrollbar with everything else looking healthy.

### Fixed

- `sort_by` on table charts had no effect. It compiled to a sort direction
  with no sort key, so a `row_limit` returned arbitrary rows rather than a
  ranking. It now compiles correctly in both aggregate and raw mode, is
  validated against the dataset like every other reference, and survives a
  decompile, so `chartwright plan` no longer reports a sorted table as
  permanently changed.
- Rows whose widths were left implicit could sum to more than the twelve
  column grid when a row held repeated markdown blocks.
- `chartwright absorb` now writes its report before touching the spec file, so
  a reporting failure cannot follow a silent mutation.
- `chartwright decompile` says so when its dataset index is truncated, instead
  of reporting a dataset it never looked at as unresolvable.
- Duplicate tab titles are rejected at validation rather than producing a
  dashboard whose tabs cannot be told apart.
- Layout sketches are parsed once per holder rather than once per lookup.

### Changed

- **The MCP tools return typed errors.** A tool that signs in (`check_spec`,
  `build_dashboard`, `plan_dashboard`, `advise_spec` with a profile,
  `decompile_dashboard`, `redesign_dashboard`) used to raise on a profile, password,
  sign-in or Superset error, which reached the agent as a bare tool failure. They now
  return the CLI's JSON: stage `profile` for profile and password problems, code `api`
  for sign-in and Superset errors, code `unexpected` for anything else.
- **`plan` reports missing references as data.** When the spec names something the
  instance doesn't have, `plan` is `blocked` and returns the same typed
  `resolution_errors` list as `check` and `apply`; `detail` is a short sentence. It
  used to put a Python-formatted list inside `detail`, which scripts couldn't parse.
- A design finding that is withheld because a chart carries a hand-polished
  height is now reported rather than passing silently.
- `--design strict` blocks when the advice cannot be evaluated at all, for
  example because `design.yaml` is malformed. It previously reported zero
  findings and allowed the run.
- Tables and pivots are expected to show at least half the rows their
  `row_limit` requests, up from a quarter. Specs with large explicit row
  limits will see more findings than before.

## 0.1.0

Initial release. Compile a spec into an Apache Superset dashboard, verify
every dataset, column, and metric before building, and check every chart
returns data afterwards. Decompile an existing dashboard back into a spec,
diff a spec against what is live with `chartwright plan`, and restore a
dashboard from an automatic backup.
