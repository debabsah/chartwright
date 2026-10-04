# The Design Brain

> **Status: SHIPPED, design brain 6** (4 added `narrative.color-scheme`; 5 the
> design defaults of §16; 6 the `standard.*` rules of §18's content). This page is both the design and the
> reference for the implementation in `chartwright/design/`. The decision log
> at the bottom records every judgment call made without a review gate; §15
> records where the implementation deliberately deviates from the design
> text. A verified multi-lens review of the first implementation produced the
> ranked roadmap in [DESIGN-BRAIN-V2.md](DESIGN-BRAIN-V2.md) (36 of 37 items
> in the v2 batch, the last closed afterwards (see that page's status note);
> a later full review of core + brain produced the version-3 changes recorded
> in §15.11 onward. §7's rule table is now genuinely generated
> (`tools/gen_rule_table.py`, checked by `tests/test_docs.py`). The v2 claim
> that it was pointed at a placeholder snippet, and the table had drifted.
>
> **Rendering verification: partial.** §17 records what was measured on
> rendered Superset 4.1.4, 5.0.0 and 6.1.0: the table and pivot grid model
> (row, header, search bar and pager heights) and the value-label and day-label
> thresholds of the design defaults. The other thresholds still come from the
> skill's field notes and BI literature, not from measured pixels: axis
> heights, pie and heatmap geometry, the horizontal-bar height per bar,
> vertical-bar category counts, KPI heights and the audience budgets. Treat
> those as informed judgement.

## 1. Problem

Chartwright guarantees a dashboard **imports correctly**. It does not
guarantee the dashboard **reads well**. Nothing stops a spec from shipping a
pie chart squeezed into 2 of 12 columns, a vertical bar with 40 category
labels (Superset silently drops most of them), a KPI buried under three rows
of tables, or a 4,000-pixel scroll for an executive audience. Today the only
design knowledge in the system is a prose section in `skill/SKILL.md`,
frozen inside one prompt, invisible to the CLI, not versioned as a surface,
not toggleable, and not testable.

The design brain is that missing layer: a codified, continuously-improvable
body of BI/UX design knowledge (Few, Tufte, IBCS, and Superset-specific
rendering facts) that the toolchain can consult, enforce, and explain, and
that the user can switch off.

## 2. Principles

1. **Same trust model as the rest of the tool.** The LLM stays the untrusted
   parser at the edge. Design judgment that requires intelligence is
   delivered *to* the LLM as versioned knowledge; design judgment that can be
   mechanically checked is enforced *after* the LLM in tested code. Nothing
   about the brain loosens the bright line (the spec remains the only
   LLM-authored artifact).
2. **Advice, not authority.** The brain never silently changes what data a
   chart shows. Autofixes are presentation-only: repairs (geometry,
   orientation) and design defaults it fills into fields the author left
   unset (§16). Both happen only when asked (`advise --fix`, MCP
   `fix_spec`), land in the spec file where the diff shows them, and never
   at compile time. Anything that would change the data shown (row limits,
   filters, chart type, series limits, sort order) is a finding with a
   suggested edit, never an automatic one.
3. **Off is really off.** `--design off` (or omitting `advise`) yields
   byte-identical behavior to today. The feature is additive; `spec_version`
   stays `"1"`.
4. **Human polish outranks the brain.** Heights written back by
   `chartwright absorb` are fractional by construction; sizing rules treat a
   fractional height as a human decision and stay silent on that chart.
5. **Improvable without architecture.** Adding or refining a guideline is a
   markdown edit; adding an enforceable rule is one small function, one
   guideline card, and one test. Rule ids are a stable public surface.

## 3. Architecture

One knowledge base, two delivery mechanisms, split by who can apply the rule:

```
              chartwright/design/guidelines/*.md   (rule cards: the knowledge)
                        │
        ┌───────────────┴────────────────┐
        │ Tier G (generative)            │ Tier L (lintable)
        │ needs intelligence to apply    │ mechanically checkable
        │                                │
        ▼                                ▼
  `chartwright brief`             `chartwright advise`
  a design brief the LLM          a deterministic critic over the
  reads BEFORE authoring          finished spec, with autofix
  (skill step 1.5)                (skill step 4.5; also in apply/check)
```

- **Tier G, the brief.** Composition, grouping, narrative flow, when to use
  tabs, title-as-insight, color restraint: judgments only an intelligence can
  make. Delivered as a compact markdown brief the skill loads before writing
  a spec. Continuously improvable by editing guideline files; no code change.
- **Tier L, the critic.** Predicates over the spec (optionally enriched with
  live metadata): minimum readable geometry per chart type, category limits,
  layout composition, fold budgets. Implemented as a rule registry in Python,
  each rule tested, each finding actionable, safe subset auto-fixable.

Both tiers share the rule cards, so the brief can tell the LLM what the
critic will later enforce (the LLM pre-complies instead of iterating).

### Module layout

```
chartwright/design/
  __init__.py      advise() / advise_and_fix() entry points
  model.py         Finding, AdviceReport, RuleContext, @rule registry, taxonomy
  rules.py         all Tier L rule implementations (split when it outgrows one file)
  defaults.py      the default.* fills (§16): one decision per field, one shared driver
  explain.py       `chartwright explain`: each design-default field's value and source
  presets.py       audience parameter tables + design.yaml overlay
  standards.py     repository standards (§18): load, extends, locks, check, report, apply
  content.py       standard content (§18): slots, the per-item record, CSS blocks, apply
  standard_rules.py  the standard.* rules: content checked against the spec
  fix.py           apply_fixes(spec_data, findings) -> (new_data, applied)
  brief.py         render_brief(audience) -> markdown
  probe.py         bounded cardinality probes (data-aware rules)
  calibrate.py     the absorb-log learning loop
  redesign.py      decompile -> audit -> fix, in one shot
  guidelines/      Tier G knowledge (packaged data, shipped in the wheel)
```

## 4. CLI surface

```
chartwright advise <spec> [--audience A] [--profile P] [--fix] [--strict]
                          [--ignore rule1,rule2] [--no-probe] [--chart NAME]
                          [--standards DIR]
chartwright explain <spec> [--chart NAME] [--audience A] [--json] [--standards DIR]
chartwright brief [--audience A]
chartwright redesign <slug-or-id> --profile P [-o spec.json] [--audience A] [--no-probe]
chartwright calibrate [--write] [--min-samples N] [--since 90d]
chartwright standards check <specs...> [--strict] [--report] [--standards DIR]
chartwright standards show [NAME | --for SPEC] [--json] [--standards DIR]
chartwright standards assign <specs...> --standard NAME [--standards DIR]
chartwright standards apply <specs...> [--check [--strict]] [--locked] [--claim] [--standard NAME]
                                       [--json] [--standards DIR]
```

- `advise` (offline by default): evaluates the spec, prints an
  `AdviceReport` JSON (shape in §10). Exit 0 unless a finding of severity
  `error` exists, or `--strict` and any `warn` exists.
- `advise --profile P`: adds **data-aware** rules: column type checks and
  bounded cardinality probes against the live instance (§8). `--no-probe`
  keeps it to metadata already fetched by resolution (no queries).
- `advise --fix`: applies the safe-fix subset in place (same file-rewrite
  mechanics as `absorb`; formatting normalizes), re-validates, and reports
  each change as `{rule, kind, chart, set: {field: new}, was: {field: old},
  why}` plus the `written` path. `kind` is `repair`, `fill` (a design
  default, §16) or `release` (a fill the author edited or deleted). Idempotent: a second `--fix` run is a no-op.
- `advise --chart NAME`: only that chart's findings; with `--fix`, only its
  fixes. An unknown name is an `unknown_chart` error.
- `explain`: offline, per chart, one row per field a design default
  governs: value, source (`spec`, `filled`, `superset default`), rule, a
  one-line reason and how to take it over. Text, or `--json` for agents.
- `brief`: prints the Tier G design brief for the audience, the document the
  skill reads before authoring. Compact by contract (a test caps the line
  count), because it lands in an LLM context window.
- `check`/`apply` gain `--design off|warn|strict` (default `warn`):
  - `warn`: advice rides along in the payload under `"advice"`, never blocks.
  - `strict`: `error`/`warn` findings block (a `design_gate` entry lands in
    `errors` and the exit code is 1); apply blocks BEFORE anything on the
    instance is touched. Like `advise --strict`, it takes nothing from the
    per-machine `design.yaml` (§6).
  - `off`: byte-identical to the pre-brain behavior, advice machinery never
    runs.
- `calibrate`: the learning loop (§13 phase 3). Mines absorb history into
  per-audience recommended heights; `--since` is the decay knob.
- `standards check | show | assign | apply`: the repository's standards (§18);
  `apply` writes a standard's content into its specs.
  `advise`, `explain`, `check` and `apply` apply the spec's standard when a
  standards folder is found; `--standards DIR` names the folder.
- MCP server: `design_brief`, `advise_spec` (with `chart` and `strict`),
  `fix_spec` (with `strict`), `explain_spec` (the `explain --json` payload),
  `standards_check`, `standards_show`, `standards_apply` (with `check`,
  `locked` and `claim`) and `redesign_dashboard` mirror the CLI
  verbs; `check_spec` and `build_dashboard` carry the advice block and take
  `design` (`off`/`warn`/`strict`, the CLI's `--design`). The tools apply the
  standards in `$CHARTWRIGHT_STANDARDS_DIR`, or those discovered from the
  server's working directory (§18).

## 5. Spec surface

One additive optional block (models stay `extra="forbid"`):

```json
"design": {
  "audience": "executive",
  "ignore": ["size.pie-geometry", "layout.fold-budget@Ops Detail"],
  "standard": "finance",
  "filled": {"Orders": {"page_length": 8, "search_box": true}},
  "standard_written": {"layout.footer[org][0]": {"layer": "org", "hash": "0b7cd41bcb55"}}
}
```

- `audience`: this dashboard's preset; CLI `--audience` overrides it, the
  built-in default (`analytical`) applies when both are absent.
- `ignore`: rule ids to suppress, dashboard-wide (`rule.id`), per chart
  (`rule.id@Chart Name`), or per band for findings that name no chart
  (`rule.id@tab-Ops-row-1`, the finding's `where`, slugified). Suppressions
  are reported in every AdviceReport (`"ignored"`), so silence is always
  visible; entries whose rule id doesn't exist come back as
  `unmatched_ignores` instead of silently suppressing nothing. An entry for a
  rule the spec's standard locks is refused and reported (§18).
- `standard`: the name of the repository standard this dashboard follows
  (§18); omitted, the standard marked `default: true` applies, if any.
  Compile, plan and decompile ignore it.
- `filled`: written by `advise --fix`, not by hand: per chart, each field the
  brain filled with a design default and the value it wrote, or null for a
  fill the author deleted (§16). Validated against the charts (a renamed
  chart carries its entry along) and each value against its field's type.
  Compile, plan and decompile ignore it.
- `standard_written`: written by `standards apply`, not by hand: each item of
  content a standard wrote and its value or hash (§18, "Content"). A field
  recorded here is never in `filled` too. An entry for a chart that was renamed
  or removed is accepted: `standards check` warns and `standards apply` drops it
  (`filled` still refuses one, as §16 says). Compile, plan and decompile ignore
  it.

Precedence everywhere: CLI flag > spec `design` block > built-in default,
except that nothing silences a rule the spec's standard locks (§18).

## 6. Audiences

A professional designer designs for a reader. Rules read their thresholds
from an audience preset, so one rulebook serves three very different
dashboards. Heights are in spec units (1 unit = 40 px; a laptop viewport
minus Superset chrome is ≈ 22 units).

| Parameter | `executive` | `analytical` (default) | `operational` |
|---|---|---|---|
| Intent | one screen, few numbers, big | scrolling analysis, depth | dense wall/monitor view |
| `fold_units` (height budget, per tab) | 22 | 66 | 22 |
| `max_row_charts` (axis slots per row) | 3 | 4 | 4 |
| `kpi_per_row` (min–max) | 2–5 | 2–6 | 2–8 |
| `kpi_height` | 5 | 4 | 3 |
| `min_axis_height` | 8 | 6 | 5 |
| `max_filter_selects` | 5 | 6 | 7 |
| `table_visible_ratio` (min visible/row_limit) | 0.5 | 0.5 | 0.5 |
| `vbar_max_categories` | 6 | 8 | 8 |
| `pie_max_slices` | 5 | 7 | 7 |
| `series_max` (lines per timeseries) | 5 | 10 | 8 |
| `search_min_rows` (§16, judgement) | 20 | 20 | 20 |
| `value_label_max_bars` (§16, §17) | 12 | 12 | 12 |
| `value_label_min_width` (§16, §17) | 6 | 6 | 6 |
| `page_min_rows` (§16, judgement) | 3 | 3 | 3 |
| `day_label_max_span_days` (§16) | 365 | 365 | 365 |

Presets are data (`presets.py`), not branches: rules never test the audience
name, only parameters. Adding an audience is adding a row.

### House style: design.yaml

`~/.config/chartwright/design.yaml` (or `$CHARTWRIGHT_DESIGN_DIR/design.yaml`)
overlays the presets for a whole deployment, without forking the rulebook. Five
keys, all optional, all validated at load with typed errors:

```yaml
params:                      # every audience
  min_axis_height: 7
audiences:                   # one audience
  executive: {fold_units: 20, recommended_heights: {table: 12}}
disable: [narrative.title-style]        # rule ids (aliases accepted)
severity: {filters.time-default: warn}  # per-deployment level overrides
recommended_heights: {table: 11}        # calibrate --write maintains these
brief_extra: |
  House style: fiscal weeks start Sunday; money is always '$,.0f'.
```

`recommended_heights` merges per key across preset -> overlay -> per-audience
layers; the brief prints the merged values and height autofixes target them.

The file lives on one machine, so it must not decide a gate (§14.14). Under
a strict gate (`advise --strict`, `advise --fix --strict`, `check`/`apply
--design strict`, and the MCP tools' `strict` and `design: "strict"`) the
overlay counts for **nothing**: its `disable` list, every `severity` entry
(raises included) and its parameters are set aside, so the gate passes or
fails the same on every machine. Without a strict gate the overlay applies as
before. Either way, every advice payload carries an `overlay` block naming the
file (§10): what it changed in a run it applied to, and what was set aside in
a gate, so a user sees why their local file had no effect there. Team-wide
settings, reviewable in a pull request and the same on every machine, belong
in the repository's standards (§18), which apply in strict gates too and can
lock what the overlay may not change anywhere.

## 7. The rulebook

Stable ids (`category.slug`) are the public API: `ignore`/`disable` lists
and severity overrides key on them, and renames keep working through the
alias table. Severity: **error** = unreadable for any audience; **warn** =
below professional quality; **info** = polish nudge. The overlay can override
it per deployment. A `sev` of `warn/error` means the rule's DEFAULT is `warn`
but it escalates per finding. Worth reading closely, because `ok` is
error-driven, so those rules can fail a run while advertising `warn`.
"fix" marks the safe-autofix subset (presentation-only, §9).
"data" marks rules that only run with a live resolution (`--profile`);
several offline rules additionally sharpen or stand down when probes are
available (noted in their text). `since` is the design-brain version that
introduced the rule: "2" the post-review batch
(docs/DESIGN-BRAIN-V2.md), "3" the review burn-down (§15.11 onward), "4"
the colour-scheme check, "5" the design defaults (§16).

The table below is GENERATED from the registry by
`tools/gen_rule_table.py --write`; do not hand-edit it. `tests/test_docs.py`
fails when it drifts.

<!-- BEGIN rule-table: generated by tools/gen_rule_table.py; do not hand-edit -->

| id | sev | fix | data | since | rule |
|---|---|---|---|---|---|
| `chart.dupe` | info | - | - | 1 | two charts answering the identical question is redundancy |
| `chart.format-bands` | warn/info | - | - | 2 | conditional-formatting bands must tell one coherent story per metric |
| `chart.funnel-stages` | warn | - | ⚡ | 1 | funnels need 3-8 ordered stages |
| `chart.heatmap-grid` | warn | - | ⚡ | 1 | a heatmap past ~400 cells is unreadable at any size |
| `chart.histogram-bins` | info | - | - | 1 | histograms read best at 10-50 bins |
| `chart.metrics-per-bar` | warn | - | - | 1 | many metrics per category read better as a table |
| `chart.ordinal-order` | info | - | - | 2 | ordinal dimensions (weekday, month) sort alphabetically unless order-encoded |
| `chart.pie-slices` | warn | - | - | 1 | pies stop working past ~7 slices |
| `chart.pivot-columns` | warn | - | ⚡ | 2 | column-dim values x metrics = rendered columns; past ~15 the pivot scrolls sideways |
| `chart.pivot-dims` | warn | - | - | 2 | a pivot past three total dimensions is unreadable nesting |
| `chart.series-limit` | warn | - | ⚡ | 1 | a timeseries with too many grouped series turns to spaghetti |
| `chart.temporal-type` | error | - | ⚡ | 1 | a time axis must point at a temporal column |
| `chart.treemap-depth` | warn | - | - | 1 | treemaps past two grouping levels become unreadable nesting |
| `chart.treemap-vs-bar` | info | - | ⚡ | 2 | a one-level treemap of few categories is a worse bar chart |
| `chart.trend-grain` | info | - | - | 2 | trend tiles at a fine grain over full history draw thousands of points in a small card |
| `chart.vbar-categories` | warn | ✔ | - | 1 | vertical bars drop labels past ~8 categories; rank with horizontal bars |
| `data.grain-vs-range` | warn | - | - | 1 | the time grain should yield a sane number of points for the range |
| `data.row-limit-intent` | info | - | - | 1 | row limits doing design work should be deliberate, not defaults |
| `data.top-n-sort` | warn | - | - | 2 | a limit without an order is a sample, not a ranking |
| `data.unwindowed-history` | warn | - | - | 3 | timeseries charts with no way to bound the window draw ALL history at their grain |
| `default.cell-bars` | info | ✔ | - | 5 | a raw table with id, code, year or zip columns draws no cell bars (a bar behind an identifier reads as an amount) |
| `default.compare-suffix` | info | ✔ | - | 5 | a trendline KPI's change says what it compares against ('vs previous month') |
| `default.count-format` | info | ✔ | - | 5 | counts read as whole numbers with thousands separators (',.0f') |
| `default.page-length` | info | ✔ | - | 5 | a table whose row_limit outgrows its panel pages by the rows that fit beside its page controls |
| `default.search-box` | info | ✔ | - | 5 | a raw table of more than ~20 rows gets a search box, when its rows still fit beside it (the 20 is judgement) |
| `default.single-series-legend` | info | ✔ | - | 5 | a single series named by the chart or y-axis title needs no legend |
| `default.value-labels` | info | ✔ | - | 5 | few bars carry their values: <= 12 bars, on a panel >= 6/12 wide (vertical) or tall enough to space the labels (horizontal) |
| `default.x-label-format` | info | ✔ | - | 5 | a time axis labels its points in its grain's own format ('Sep 2026' by month); day and week labels only over a year or less |
| `filters.count` | warn | - | - | 2 | past ~6 select pickers a filter bar stops being navigable (and each costs a query on load) |
| `filters.duplicate-column` | info | - | - | 2 | two filters on the same column fight each other |
| `filters.range-default` | info | - | - | 2 | a range slider with no default bounds spans the whole domain |
| `filters.select-cardinality` | warn | - | ⚡ | 2 | a select over hundreds of distinct values is an unusable picker |
| `filters.time-default` | info | - | - | 2 | an undefaulted time picker loads the dashboard over ALL history |
| `filters.time-picker` | info | - | - | 1 | time-based dashboards want a time range picker in the filter bar |
| `layout.fold-budget` | warn | - | - | 1 | the dashboard should fit its audience's scroll budget |
| `layout.kpi-band` | warn | - | - | 1 | KPIs get their own band, in readable numbers |
| `layout.kpi-first` | warn | - | - | 1 | summary KPIs belong above detail charts (inverted pyramid) |
| `layout.markdown-height` | info | ✔ | - | 2 | a one-line markdown header doesn't need a chart-sized block |
| `layout.orphan-chart` | info | - | - | 1 | a lone narrow chart in its own row looks unfinished |
| `layout.row-density` | warn/error | - | - | 1 | too many axis charts side by side starves each of width |
| `layout.row-fill` | warn/info | - | - | 1 | a row should fill the 12-column grid |
| `layout.section-headers` | info | - | - | 1 | large flat dashboards need section headers (header rows or markdown) |
| `layout.tab-balance` | info | - | - | 1 | tabs should carry comparable weight |
| `narrative.big-number-format` | info | - | - | 1 | hero numbers deserve a number format |
| `narrative.color-scheme` | warn | - | - | 4 | a colour scheme Superset doesn't ship draws the default palette unless your deployment registers it |
| `narrative.filtered-title` | info | - | - | 1 | a filtered chart's title should say what it shows |
| `narrative.format-consistency` | info | - | - | 2 | one measure, one number format |
| `narrative.title-style` | info | - | - | 1 | chart titles should share one casing style |
| `size.axis-min-height` | warn | ✔ | - | 1 | axis charts below the audience minimum height flatten and drop labels |
| `size.grid-fit` | warn | ✔ | ⚡ | 2 | table/pivot heights must fit their data-driven row counts (they grow after authoring) |
| `size.hbar-window` | warn | ✔ | - | 1 | horizontal bars need ~0.5 units of height per bar |
| `size.heatmap-geometry` | warn | ✔ | - | 1 | heatmaps need >= 5/12 width (7/12 with many columns) and 6 height |
| `size.kpi-height` | warn | ✔ | - | 1 | big numbers read best at 2-6 units |
| `size.min-width` | warn/error | - | - | 2 | below 3/12 width a chart is unreadable; KPIs need 2/12 |
| `size.pie-geometry` | warn | ✔ | - | 1 | pies need >= 5/12 width and 8 height or the ring shrinks and the legend crowds |
| `size.pivot-window` | warn | - | - | 2 | a pivot's height should show a meaningful share of its row_limit |
| `size.row-harmony` | warn | ✔ | - | 1 | charts sharing a row should share a height (Superset sizes the row to its tallest child) |
| `size.table-window` | warn | - | - | 1 | a table's height should show a meaningful share of its row_limit |
| `standard.classification` | error | - | - | 6 | the dashboard's classification is one the standard lists |
| `standard.content-locked` | error | - | - | 6 | content a standard locks is in the spec as the standard has it (standards apply writes it; a change goes through the standard's file) |
| `standard.content-released` | info | - | - | 6 | content a standard has that the author took over (edited or removed): the author's now, and standards apply leaves it alone |
| `standard.content-stale` | warn | - | - | 6 | content a standard wrote is current: standards apply would change nothing |
| `standard.css-hides` | warn | - | - | 6 | CSS outside the locking layers' own blocks has no declaration known to hide elements while a standard locks header or footer rows (a heuristic: other ways to hide one pass) |
| `standard.waiver-expired` | error | - | - | 6 | no waiver naming this dashboard in standards/waivers.yaml has expired (checked by standards check and advise; a deploy warns instead) |

<!-- END rule-table -->

Anything fuzzier than this (reading order beyond KPI-first, grouping
related metrics, matched granularity across a row, insight-stating titles)
is Tier G: it goes in the brief, not the linter.

## 8. Data-aware mode

Resolution already fetches full column metadata (`dataset_detail`) and
discards everything but names. Phase 2 extends `ResolvedDataset` with
`column_types` (Superset's `type_generic`) and the `is_dttm` set, at zero extra
API cost, which powers `chart.temporal-type`.

Cardinality (`chart.pie-slices` ⚡, `chart.series-limit`, `chart.funnel-stages`,
`chart.heatmap-grid`) uses one bounded probe per distinct (dataset, column):
a `COUNT` grouped query through `/api/v1/chart/data` (the smoke module's
existing machinery) with `row_limit = threshold + 1`, because the rule only needs
"more than N", never the true count. Probes are cached per run, skipped
entirely under `--no-probe`, and never run for `advise` without `--profile`.

## 9. Autofix semantics

- **Safe set only:** repairs (heights, bar orientation, `x_label_every` on
  a category-sorted bar, markdown header heights) and the design defaults
  of §16, which fill unset display fields. All presentation; a fixed spec
  queries identically to the unfixed one. Widths are report-only (§15.1).
- Mechanics mirror `absorb`: findings carry a patch
  (`{"chart": "Top Products", "set": {"height": 8}}`), `fix.py` applies them
  to the raw spec JSON, the result is re-validated before writing, and the
  report lists every applied fix with its `set`/`was` diff, its `kind`
  (`repair`, `fill`, or `release` when a fill passes to the author) and a
  `why` line (a repair's why is its finding).
- **Two phases:** repairs run until none is left; fills run only then,
  because some read the geometry repairs settle (a page size reads the
  height). A fill never writes geometry or a field a repair writes, so the
  convergence invariant of `advise_and_fix` is unchanged (tested), and a
  moved height refreshes the brain's own fills on the next pass.
- **Idempotent by test:** fixed specs re-advise with zero fixable findings.
- Sketch layouts: heights are fixable (an explicit `chart.height` overrides
  sketch height by existing precedence in `spec.resolved_height`); widths are
  not (the drawing is authoritative). Width findings under a sketch include
  a suggested redrawn sketch line for the LLM or human to adopt.
- Fractional heights (absorb's signature) suppress sizing rules on that
  chart: human polish wins (§2.4).

## 10. Output contract

`advise` speaks the same JSON dialect as every other stage:

```json
{
  "stage": "design",
  "ok": true,
  "design_brain": "6",
  "audience": "analytical",
  "counts": {"error": 0, "warn": 2, "info": 1},
  "findings": [
    {
      "rule": "size.pie-geometry",
      "severity": "warn",
      "chart": "Sales by Region",
      "where": "layout row 2",
      "detail": "pie squeezed: height 4 < 8; ring shrinks and legend crowds",
      "fixable": true
    }
  ],
  "fixed": [
    {"finding": "size.pie-geometry@Sales by Region", "rule": "size.pie-geometry",
     "kind": "repair", "chart": "Sales by Region", "set": {"height": 8},
     "was": {"height": 4},
     "why": "pie squeezed: height 4 < 8; ring shrinks and legend crowds"},
    {"finding": "default.x-label-format@Weekly Orders", "rule": "default.x-label-format",
     "kind": "fill", "chart": "Weekly Orders", "set": {"x_label_format": "%b %Y"},
     "was": {"x_label_format": null}, "why": "grain P1M reads as 'Sep 2026'"}
  ],
  "ignored": ["layout.fold-budget"],
  "unmatched_ignores": ["size.pie-geometri"],
  "polished": ["size.axis-min-height@Weekly Orders"],
  "overlay": {
    "path": "/home/me/.config/chartwright/design.yaml", "strict": false,
    "params": ["fold_units"], "disable": [],
    "severity": {"filters.time-default": "warn"},
    "changed": [{"finding": "filters.time-default@filters", "severity": ["info", "warn"]}]
  }
}
```

- `ok` is false iff a finding of severity `error` exists. The `--strict` gate
  (and `--design strict` on check/apply) rides the exit code and appends a
  `design_gate` entry to `errors`; it does not redefine `ok`'s meaning.
- `fixed` entries disclose the full diff (`set` new values, `was` old; a
  field a fill removes shows `null` in `set`), the `kind` (`repair` or
  `fill`) and the `why`; `advise --fix` additionally reports the `written`
  file path. A fill waiting for `--fix` is an `info` finding with
  `fixable: true`, in `advise` and in the advice `check` and `apply` carry.
- `unmatched_ignores` lists ignore/disable entries whose rule id doesn't
  exist: a typo'd suppression is surfaced, never a silent no-op.
- `polished` lists sizing findings withheld because the chart carries a
  human-polished (fractional) height, which is §2.4's deference made visible.
  `ignored` is the user's *explicit* intent; `polished` is the brain's own
  *inference*, and an inference that silences a rule invisibly reads exactly
  like the rule having passed. Present only when non-empty.
- `overlay` is present whenever a `design.yaml` is in play (§6): its `path`
  and whether the run was a `strict` gate. Outside a strict gate it lists the
  `params` it set, its `disable` list, its `severity` entries and `changed`,
  each finding it changed in this run (`severity: [from, to]`, or
  `disabled: true`). Under a strict gate it holds only `set_aside`: every
  `params`, `disable` and `severity` entry the gate ignored (omitted when the
  file sets none). An overlay typo in `disable` is an `unmatched_ignores`
  entry only where the list applies, outside a strict gate. Outside a strict
  gate, entries for what the spec's standard locks move from `params`,
  `disable` and `severity` to `set_aside` (§18).
- `standard` is present only when the spec follows a standard (§18): its
  `name`, `chain`, `via`, the `locked` rules and parameters, and any
  `refused_ignores`. Each finding then also carries `layer` (the standard,
  `overlay` or `rulebook` that set its severity) and `locked`.
- Under `apply --design warn`, this object is embedded in the apply report
  as `"advice"` and never affects `apply`'s own `ok`. Under
  `--design strict` the gate fails CLOSED: if advice could not be evaluated
  at all (a broken `design.yaml`, or a standard that can't be resolved), that
  blocks too, rather than reporting counts of zero and passing.

## 11. Interactions with the existing system

- **`absorb`:** fractional heights silence sizing rules (§9). The two flows
  compose: brain roughs in professional geometry, human drags to taste,
  absorb records it, brain respects it forever after.
- **`decompile`:** running `advise` on a decompiled spec is a **design audit
  of any legacy UI-built dashboard**, an emergent feature worth documenting:
  `chartwright decompile old-dash -o spec.json && chartwright advise spec.json`.
- **`redesign`:** the one-shot form of the above: decompile → data-aware
  audit → safe geometry fixes → redesigned spec + losses + remaining
  structural findings. It writes no design defaults (only `advise --fix`
  and `fix_spec` do, §16); its `next` line names the command when fills
  are waiting. Ownership decides where it lands: tool-born
  dashboards redesign in place; UI-born ones come back under a `-redesign`
  slug (title suffixed too) so apply builds the redesign **side by side**
  and the original is never overwritten. Structural findings stay findings:
  the spec author (usually the skill-driven LLM) acts on them before apply.
- **`smoke` (issue #1):** table/pivot heights are fixed layout properties
  while rendered rows are data-driven, and data that grows after authoring hides
  new rows behind the chart's inner scrollbar with everything looking green.
  Smoke now compares the rows its query already fetched against the
  configured height and warns on every apply (`~9 leaf rows (~433px) but
  height=8 (320px)`); the data-aware `size.grid-fit` rule catches the same
  class pre-apply for single-dimension grids. All of it, meaning smoke,
  `size.grid-fit`, `size.table-window`, `size.pivot-window`, and the
  page-length and search-box fills, reads ONE grid model (`grid_header`,
  `grid_units_for_rows` and `grid_rows_visible` in `chartwright/spec.py`,
  measured in §17), so the offline critic, the data-aware critic, the fills and
  the apply-time warning cannot give one chart different verdicts.
- **Chart identity:** no rule may ever autofix a chart `name`: names seed
  uuid5 identity; a rename is a delete+create on the live instance.
- **`plan`/golden tests:** advise is pure spec-side analysis; compiled bytes
  are untouched, golden tests unaffected. A design default is an ordinary
  field once `--fix` writes it, so `plan` and decompile treat it like one the
  author typed; `design.filled` never reaches the bundle, and decompile
  cannot recover it (a decompiled spec's fields read as the author's).
- **Skill (`skill/SKILL.md`):** the static "Design rules" section is replaced
  by two procedure steps:
  - *Step 1.5*, brain on (default): run `CW brief --audience <inferred>`
    and follow it while authoring. Infer audience from the request
    ("executive scorecard" / "ops monitor" / default analytical). Brain off
    (user said "no design opinions" / "exactly as I specify"): skip the
    brief, pass `--design off`.
  - *Step 4.5*, after `check` passes: `CW advise <spec> --profile <p>`;
    apply or consciously `ignore` findings (with the user, in the spec's
    `design.ignore`); at most 2 design iterations, then surface remaining
    findings verbatim. `--fix` runs before any hand edit, and the author
    edits the file it wrote, never a regenerated copy (§16).
  - Anti-evasion row: *"Advice finding seems wrong → record it in
    `design.ignore` and tell the user, or report a rule bug; never hand-tune
    output to dodge the critic."*

## 12. Testing

What actually runs (tests/test_design*.py, test_calibrate.py, test_redesign.py):

- **Per rule, table-driven:** a clean spec stays silent; a violating spec
  fires exactly the expected finding; the autofixed spec re-advises clean.
- **Fix-loop invariants:** convergence and idempotence; autofixes never mint
  fractional (human-signature) heights; the KPI clamp cannot oscillate.
- **Seeded fuzz:** advise never raises across a grid of height/width/layout
  mutations of a mixed-type spec.
- **Golden dogfood:** `examples/nyc_taxi_operations.json` advises clean at
  `analytical` (its two deliberate exceptions recorded in `design.ignore`)
  except for the design defaults `--fix` would fill: the file is the spec
  behind the README screenshot, kept as written, and the test also holds it
  to zero findings once `--fix` writes them.
- **Design defaults** (`test_design_defaults.py`): see §16.
- **Contract tests:** the chart-type taxonomy exactly covers `CHART_TYPES`
  (a 15th type fails CI until classified); one sketch parse per holder
  (the geometry-cache bound); brief line budget per audience.
- **Trust-boundary tests:** every design.yaml value class rejects with a
  typed error; typo'd ignores surface as `unmatched_ignores`.

## 13. Phases

| Phase | Scope |
|---|---|
| **1** (next minor) | design/ package, offline rulebook (all non-⚡ rules), presets, `advise`/`brief` CLI, `--fix`, spec `design` block, `apply`/`check --design`, skill rewrite, MCP tools, tests |
| **2** | data-aware rules (resolver type enrichment, bounded cardinality probes, `--no-probe`), house-style overlay: `~/.config/chartwright/design.yaml` (org-level parameter overrides, disabled rules, extra Tier G guidance appended to the brief) |
| **3** | calibration loop: mine absorb history and backups for systematic human corrections (e.g. tables consistently dragged from 8 to 11 units) and propose preset updates, so the brain learns from every human polish it was told to respect |

## 14. Decision log

Judgment calls made without a review gate, per instruction; each is
reversible and none is load-bearing enough to block on:

1. **Default `--design warn` on apply/check** (brain visible by default,
   never blocking). Rationale: an invisible-by-default brain never gets used;
   a blocking-by-default one breaks existing pipelines.
2. **Audience presets named `executive`/`analytical`/`operational`**, with
   `analytical` default; "audience" chosen over "profile" (collides with
   connection profiles) and "preset".
3. **Autofix = presentation only.** Anything data-affecting (row_limit,
   chart type, filters) is suggestion-only, however confident the rule.
4. **`chart.temporal-type` lives in advise (error), not `check`.** It is
   type-correctness, but moving it into check changes a shipped gate's
   behavior on specs that currently pass; advise-as-error gets the same
   protection without a silent contract change.
5. **Rules are Python functions in a registry, not a YAML/DSL engine.**
   Stable ids + parameter tables give the evolvability a DSL would; the DSL
   is speculative machinery (YAGNI).
6. **Fractional height = human polish → sizing rules skip the chart.**
   Zero-config way to honor absorb without a new spec field.
7. **No design score in v1.** A 0–100 number invites gaming and argues with
   itself; counts by severity carry the same information honestly.
8. **Guidelines ship as package data** so `brief` works from a pip install,
   not only a repo checkout.
9. **`spec_version` stays `"1"`;** the `design` block is additive and
   optional. No migration.
10. **Names never autofixed** (uuid identity churn), even for the
    title-style rule.
11. **Chart-name casing, cross-dataset filter applicability, and
    reading-order-beyond-KPI-first stay Tier G** (or info-severity):
    heuristics too fuzzy to enforce mechanically without false positives.
12. **Learning/calibration deferred to phase 3**; the architecture point is
    that rules-as-data + stable ids make it a parameter update, not a
    rewrite.
13. **Design defaults are fills written into the spec, never compile-time
    defaults** (2026-10-03). The brain chooses a value only through
    `advise --fix` and MCP `fix_spec`, which write it into the spec;
    compile, plan and decompile never
    invent one, so a bundle depends on the spec alone, `plan` stays clean
    after `apply`, and upgrading chartwright restyles nothing. Provenance
    is `design.filled`, and there is no per-spec brain version pin: a
    newer brain changes a spec only when someone runs `--fix`, and then
    only fields left unset or still holding the value `design.filled` records. The catalogue, the
    ownership rule and the conditions each fill honours are §16.
14. **Strict gates take nothing from the per-machine overlay** (2026-10-03,
    the first groundwork item of the project's fleet-standards decision,
    kept with its research notes outside the repository, which makes a strict
    gate immune to it). `design.yaml` lives in a home directory or
    `$CHARTWRIGHT_DESIGN_DIR`, so its `disable` list and a lowered `severity`
    could pass `advise --strict` or `--design strict` on one machine while
    CI failed, with nothing in the payload to say why; a verifier reproduced
    both. A gate's result must not depend on the machine it runs on, in
    either direction, so under a strict gate the whole overlay is set aside:
    disable list, severities (a raise too: it would fail a gate locally that
    passes in CI) and parameters. Outside a strict gate nothing changes, and
    every advice payload names the overlay and what it changed or what was
    set aside (§6, §10). Team-wide strictness comes from repository
    standards (§14.15, §18).
15. **Standards: rule settings in the repository, layered and lockable**
    (2026-10-03, the second phase of the project's fleet-standards decision
    record, approved after five reviews and two cross-checks and kept with
    its research notes outside the repository; it settles decisions 1, 2, 7
    and 10 there). The rules-only phase of standards is §18:
    - **Advice only, never compile.** A standard sets the brain's parameters,
      severities and disabled rules. Compile, `plan`, decompile and MCP's
      build and plan read only the spec, so the bundle never depends on a
      standards file, as §14.13 already decided for design defaults.
    - **Explicit, single-parent `extends`, at most three files** (org, unit,
      team; the dashboard's design block comes after them, §15.23), with no
      folder cascade:
      ESLint's maintainers wrote that they "would have removed the
      configuration cascade", and diamonds and cycles have no defined
      meaning.
    - **Assignment lives in the spec** (`design.standard`), because MCP sees
      specs and never paths; a folder layout only seeds the field through
      `standards assign`. Older releases reject the field; that cost was
      accepted.
    - **Locks are recomputed from the standards files on every run**, never
      read from the spec, and nothing in a spec, on the command line or in
      `design.yaml` silences a locked rule. Exceptions to a lock come with
      the decision's exceptions file, a later phase; until then a locked rule
      has none.
    - **One gate concept.** `standards check` is `advise` over a folder with
      each spec's standard applied and `design.yaml` set aside, and the same
      standard applies in every advice run, so `advise --strict` and
      `standards check --strict` give a spec the same verdict.
    - **The name "standards"** avoids the words already taken: connection
      profiles, audiences, Superset themes, and "house style", which names
      `design.yaml`.
16. **Standard content is written into specs, with an ownership record per
    item** (2026-10-04, the third phase of the same decision record, which
    settles its decisions 3, 6, 7 and 8 for content). §18, "Content":
    - **Written, never merged at compile.** `standards apply` writes a
      standard's content into each spec that follows it; compile, `plan`,
      decompile and MCP's build and plan still read the spec alone, and a
      spec without standard content builds the same bytes as before.
    - **A closed list of slots:** header and footer rows, a CSS block per
      layer, the colour scheme, label colours, certification, and a chart's
      number format. Rows, CSS blocks and label keys add up across layers;
      the colour scheme and certification are the innermost layer's.
    - **Ownership per item, not per field.** A team that edits one footer
      row or one CSS block takes that item over and keeps receiving every
      other one; a per-field record would cut the dashboard off from all
      later changes to the field at the first edit, with nothing reported.
      The record reuses §16's "value written" rule.
    - **A lock is a check, and apply rewrites only when told.** A locked item
      an author changed is an error in `standards check` naming the layer;
      `standards apply` leaves it and reports it, and `apply --locked`
      rewrites it as a change that shows what was there. An unlocked item an
      author changed stays theirs.
    - **`apply --check` fails on locked content only** (decision #7: "stale
      on locked content"), so an unlocked change can reach each team in its
      own pull request (decision #8) while CI stays green;
      `standards check --strict` fails on stale unlocked content, a warn,
      for a team that wants it current.
    - **CSS is the weakest lock** (decision #6): a lower layer's CSS can hide
      a locked row. `standard.css-hides` is a heuristic: it warns on the
      hiding declarations it knows outside the locking layers' blocks, and any
      other way to hide an element passes it. The real lock on what readers
      see is a check of the rendered dashboard after deploy, which is not
      built yet (phase 4). The rule is locked with the content, so nothing
      below the lock silences it.
    - **Lifecycle and classification are spec-only fields** that standards
      key content off; Superset has nothing they could map to safely (§18).
17. **Exceptions, mixed releases, a theme, and a check of what readers see**
    (2026-10-04, the fourth phase of the same decision record, which settles
    its decisions 4, 5, 6 and 9). §18, "Waivers" onwards:
    - **Exceptions live in `standards/waivers.yaml`, under CODEOWNERS**
      (decision #4): dashboard, rule or content item, owner, reason, expiry,
      optional layer. A waiver is the only way past a lock; the spec's
      `design.ignore` still can't silence one, because an exception written
      in the spec is self-granted in the same pull request.
    - **Expiry fails only the author-side gates, for the specs checked**
      (decision #5): `standards check` and `advise` fail on an expired waiver;
      `standards apply`, `check`, `apply` and `plan` keep applying it with a
      warning; `restore` never reads it. CI checks the changed specs, so an
      expiry fails only pull requests touching that dashboard, and a
      scheduled `standards check --report` lists every expired and expiring
      waiver. `--as-of` makes any run repeatable.
    - **A standards file declares the release its content is for**
      (decision #9): `min_superset`, one release, in versions.py's form
      ("6.0" read as 6.0.0), never a range. Below it, and below a gated
      field's own release, the deploying commands hold the standard's
      content back per instance and list it as held, so a newer-only item
      never fails a fleet deploy.
    - **A theme by name, resolved per instance** (decision #6):
      `dashboard.theme`, gated at 6.0.0, resolved through the theme API and
      written as the target's `theme_id`; a standard may set it. Superset
      owns the theme's tokens; chartwright names the theme.
    - **A lockable classification is a scalar slot.** A standard assigns one
      classification to the dashboards that follow it; locked, a spec can't
      change or drop it, and its classification rows follow the standard's
      value. Per-dashboard exceptions are waivers.
    - **The real lock on visible text is a rendered check** (decision #6):
      `standards verify-visible` opens the deployed dashboard in a headless
      browser and fails when a locked line is hidden. The browser is an
      optional extra, never a dependency.

## 15. Implementation deviations (recorded, not silent)

Where the shipped code deliberately departs from the design text above:

1. **Width autofixes are report-only everywhere**, not just under sketches.
   A width change can overflow a row's 12-column sum and invalidate the
   spec; the finding carries the suggested width (or redrawn sketch run)
   instead. Heights remain fully autofixable.
2. **`apply --design` advises offline** (no resolution, no probes) so
   applies stay fast; `check --design` attaches type-aware advice for free
   (it already resolved); full data-aware advice is `advise --profile`.
3. **`size.axis-min-height` fixes to the audience minimum** (8/6/5 by
   audience), not a blanket 8, because a dense operational board shouldn't be
   inflated to analytical proportions.
4. **Rule cards are the registry docstrings** (one line per rule, printed in
   the brief) plus two Tier G guideline files; 27 separate markdown cards
   would have been boilerplate.
5. **Phase 3 concretely:** `absorb` appends to
   `~/.config/chartwright/absorb-log.jsonl` (one vote per profile/slug/chart,
   latest wins) and `chartwright calibrate [--write]` proposes/records
   `recommended_heights` in the overlay, which the brief prints and height
   autofixes target. `$CHARTWRIGHT_DESIGN_DIR` relocates both files.
6. **`chart.dupe` proved itself in testing**: it flagged the test suite's own
   lazily-copied KPIs. Working as intended.

Recorded during the v2 roadmap burn-down:

7. **Row references stay 0-indexed** everywhere (`layout row 0`), matching
   the spec validator's long-shipped messages; the review's 1-indexing
   suggestion was declined for consistency.
8. **`filters.time-default` ships as info for every audience**; deployments
   that want it blocking for executives raise it via the overlay's
   `severity` map rather than a boolean param (outside strict gates, which
   take nothing from the overlay since §14.14).
9. **Sketch WYSIWYG resolved as disclosure, not withholding**: height fixes
   still apply to sketch-drawn charts (explicit heights legitimately override
   the drawing, absorb's precedent), and the finding says the drawing goes
   stale and how to redraw it.
10. **A per-metric d3 format on table/pivot/timeseries is a spec v-next
    candidate** (the compiler pins SMART_NUMBER today); the guideline was
    softened to what the spec can express rather than promising the
    inexpressible.

Recorded during the post-merge review burn-down:

11. **One grid model, three consumers.** `size.table-window` (0.8 units/row,
    1 header unit), `size.grid-fit` (0.75 + 3) and smoke (30px/row + 3×40px)
    each modelled a rendered grid row differently, so a 20-row table at
    height 8 passed offline while the data-aware rule and the apply warning
    both said it hid ten rows. The constants now live once in
    `chartwright/spec.py`; the rules, the fills and smoke import them. §17
    replaced the shared guesses with measured sizes: the search bar, the
    page-size bar, the pager, pivot header rows and the pinned totals row are
    now part of the model, which had none of them.
12. **`table_visible_ratio` is 0.5 for every audience**, up from 0.25 on
    `analytical`/`operational`. Below half, the MAJORITY of the rows the
    author deliberately asked for sit behind the inner scrollbar: the exact
    defect §11's issue-#1 work exists to catch, so the offline rule must not
    bless it. It remains a per-deployment knob; it is no longer a lenient
    default. `size.table-window` and `size.pivot-window` also became
    autofixable (a height raise, guarded to sane heights) now that they
    compute a real target rather than a ratio verdict.
13. **The polish skip is disclosed, not silent** (`polished` in §10). §2.4's
    deference to a human-dragged height is an INFERRED signal; v2 theme 1
    named it alongside `unmatched_ignores` and the strict-gate `ok`, and only
    the other two landed. Known limits of the inference, unchanged: a drag to
    an exact 40px boundary absorbs as an integer and is not recognized, and a
    hand-written fractional height silences sizing rules without the author
    intending it. Explicit provenance (a spec field written by `absorb`) is
    the real fix and stays a v-next candidate; reporting it is the floor.
14. **`--design strict` fails closed.** Advice still degrades to an error
    note instead of crashing check/apply, but under `strict` an unevaluable
    overlay blocks: previously one typo in an org-wide `design.yaml`
    silently disarmed the gate everywhere it was used.
15. **`advise --strict` names its gate.** `ok` stays error-driven by §10's
    contract, so the exit code was previously the only signal that `--strict`
    blocked. It now appends a `design_gate` entry to `errors`, matching
    check/apply. (The review first read the `ok` difference between the two
    verbs as the defect; it is not. They are different payloads with
    different `ok` meanings. The missing cause was the real gap.)
16. **Severities a rule can actually emit are declared** (`severities=` on
    `@rule`) and printed as `warn/error`. Four rules vary severity per
    finding; the table showed all four as their default, and since `ok` is
    error-driven, the two that escalate to `error` understated exactly the
    case a reader most needs. A test compares each declaration against the
    severity literals in the rule's own source, so a new escalation fails CI
    until it is declared.
17. **§7's table is generated for real.** The doc claimed "GENERATED from the
    registry" while pointing at a placeholder snippet, so it was
    hand-maintained and had drifted. `tools/gen_rule_table.py --write`
    produces it; `--check` runs in the test suite.
18. **`data.unwindowed-history` (new, warn).** The commonest real-world
    Superset failure was uncovered: no chart `time_range`, no time_range
    filter at all, daily-or-finer grain, so every load queries the dataset's
    full history. Reported ONCE per dashboard, not per chart, and deliberately
    silent when a time_range filter exists without a default;
    `filters.time-default` already names that one-line fix, and double-
    reporting one remedy at two severities is noise. Deployments that want
    that case to bite raise it via the overlay `severity` map (§15.8; not
    in a strict gate, §14.14).
19. **`decompile` says when its dataset index is truncated.** The lookup
    stops at a page cap; past it, a real dataset became "uuid not resolvable"
    and its chart was dropped: a wrong answer wearing the costume of an
    honest loss, which is the one failure this decompiler must never produce.

Recorded during the fleet-standards groundwork:

20. **The MCP tools gained the CLI's gates.** §4 said the MCP tools mirror
    the CLI, but `build_dashboard` ran no advice at all and no MCP tool had a
    strict mode, so an agent could not run the gate CI runs. `check_spec`
    and `build_dashboard` now take `design` (`off`, `warn` by default,
    `strict`) exactly as `--design` does, `build_dashboard` carries the
    advice block `apply` carries, and `advise_spec` and `fix_spec` take
    `strict`. Both surfaces build the block through one function
    (`design.advice_payload`), so they cannot report an overlay differently.
21. **A header counts against every tab's fold budget; a footer does not.**
    `layout.header` mirrors `layout.footer` everywhere else: its rows are a
    section of their own (`header row N`) that the sizing and band rules
    review like the body, and tab balance and the section-header check leave
    it out. The fold budget is the exception, because the two sit on
    opposite sides of the fold: every tab opens below the header, so its
    height is spent before the tab's first row, while a footer comes after
    the content the budget protects. `layout.fold-budget` therefore starts
    each tab's count at the header's height and says so ("with the header's
    N"); a header that alone exceeds the budget is reported once, on the
    header row where it runs out, not again on every tab.

Recorded during the rules-only standards (§18):

22. **A locked rule doesn't yield to human polish.** §2.4 has sizing rules
    stand down on a chart with a fractional height, the mark `absorb` leaves.
    That signal is inferred, and an author can write `7.5` by hand, so on a
    rule a standard locks it would be a way around the lock that no review
    sees. A locked rule therefore reports on polished charts too; the platform
    team that locked it decides whether the absorbed height or the rule wins.
23. **A standard chain holds at most three files: org → unit → team.** The
    decision's table says "at most 3 files: org → unit or team → dashboard"
    and, as its cost, "a team that needs more nests through its unit". The
    first reading counts the dashboard as the third layer and stops the files
    at two; the author chose the second on 2026-10-04, reading "nests through
    its unit" as org → unit → team, so three files with the spec's `design`
    block after them. The cap is one constant (`MAX_FILES` in
    `design/standards.py`).
24. **No mapping file for assignment.** The decision let folder or mapping
    rules seed `design.standard`; `standards assign` takes folders and globs
    as its arguments, which seeds it the same way without a third file whose
    overlapping patterns would need their own precedence rule.
25. **A standard reaches the spec through `--fix`.** A standard writes no field
    itself, but `advise --fix` and `fix_spec` read its parameters: a height
    repair targets the standard's `min_axis_height` or `recommended_heights`,
    and the fills read its thresholds. Those values land in the spec as
    ordinary repairs and fills, with no record that a standard chose them.
    The content standards of the next phase, whose provenance tells a
    standard's writes from an author's edits, must account for these too, or
    a later change to the standard will read the old repair as the author's.
26. **`design.standard` stays a name.** The decision's next phase records
    which standard wrote each item of spec content. That per-item provenance
    goes in a sibling field of the `design` block, not in `design.standard`,
    which stays the standard's name as a string, so specs written now stay
    valid and an assignment remains a one-line diff.

Recorded during the content standards (§18, "Content"):

27. **The record is `design.standard_written`.** §15.26 put per-item
    provenance in a sibling of `design.standard`; the name parallels
    `design.filled` (what the brain filled) and avoids "owned" (Superset's
    owners, `dashboard.owners`) and "applied" (`chartwright apply`). The
    decision record's phase list calls it "the `design.standard` record per
    item"; §15.26 already set that aside, so the sibling field stands.
28. **CSS block markers reach Superset.** A block is a CSS comment pair,
    `/* cw:std org <hash> */ ... /* cw:end org */`, inside `dashboard.css`.
    Compile emits the CSS verbatim, as always, so the markers are part of
    the dashboard's CSS: Superset stored them and decompile read them back
    unchanged on 4.1.4, 5.0.0 and 6.1.0 (`plan` clean after apply, slugs
    `cw-std3-*`). Stripping them at compile would make `plan` report the
    CSS as drift on every governed dashboard. The ownership record never
    reaches the bundle.
29. **A row has no identity beyond its content.** A row a standard wrote is
    found by the hash recorded for it, so authors can insert rows anywhere
    without moving it. The hash is taken over the row with its defaults
    written out, since decompile writes a markdown block's width and height
    back explicitly. When no row holds that hash, the row that is it, edited,
    is the one unclaimed row of the same shape (kind, background, widths and
    heights) where it stood that still reads like it: anywhere between two
    present neighbours, and on the author's side of the managed rows only
    the one row next to them; and at least 0.6 of difflib's similarity ratio
    with the standard's text. A corrected date, contact or typo keeps far
    more ("Confidential. Acme Corp. 2026." scores 0.89 against the original);
    a row the author wrote themselves keeps far less ("My disclaimer, keep
    me" scores 0.26). A same-shape row below the threshold is the author's
    own: `--locked` adds the standard's row beside it and names it (`kept`),
    and the locked finding says so. Where two layers wrote identical rows and
    one copy is gone, a locked item takes the copy left.
    That row is released, and it is the row `apply --locked` rewrites in
    place. With none or several, the row counts as removed. Either way the
    record keeps the hash written, marked `"released": true`, rather than
    becoming null: an edited row no longer carries the standard's identity,
    so without a record apply would add the standard's row beside the
    author's, and with the hash the record follows its row when the standard
    adds, drops or reorders rows (a record keyed by place alone would hand
    the release to the row that moved into that place). A row's record
    moves with its hash, or stays at its place when the standard edited that
    row. Every other item (a setting, a label colour, a number format, a CSS
    block) keeps a released record the same way, so a later deletion of the
    author's value stands, as the approved "an author's edit or deletion
    wins" says. §16's fills drop their record on an edit instead, so there a
    deleted edit is filled again; phase 4 revisits fills.
30. **§15.25's repairs and fills, accounted for.** A height repair that read
    a standard's parameter is geometry, which standards never write, and
    stays recordless and the author's: a later standard asking for more
    height fires the rule again, and a lower minimum leaves a taller chart
    alone. Fills keep their own record in `design.filled` and refresh
    themselves when a threshold moves. The one field both can write,
    `number_format`, has one owner: validation refuses it in both records,
    `standards apply` takes a brain fill and drops its record, and the brain
    never fills a field the standard's record names, released included.
    The markdown-height repair leaves a standard's rows alone.
31. **A chart's number format is keyed by metric label.** A standard
    covers dashboards whose charts it can't know, so it names formats per
    metric label (`Revenue: "$,.0f"`), and a chart takes one when every
    metric it shows maps to the same format. A chart that plots shares (a
    contribution chart, a 100 % stack) or a pivot whose aggregation changes
    the unit (`Count`, a fraction of a total) takes none.
32. **A spec that follows no standard is left alone by `standards apply`,**
    even when it carries a record from an earlier standard: removing a
    standard's `default: true` must not strip every dashboard that
    followed it.
33. **Left for phase 4**, with where each plugs in (what phase 4 did with
    each is in 34-40; the last two items stay open, see 40):
    - the version floor filters the content a spec expects per instance
      release (`expected_items` in `design/content.py`). No content slot is
      version-gated today: header and footer rows, CSS, colours,
      certification and number formats import on all three releases;
    - the exceptions file is consulted where a locked mismatch is decided
      (`Decision.violation`);
    - the check that locked text is visible on the rendered dashboard after
      deploy, the real lock that `standard.css-hides` only approximates;
    - a lockable classification: today the lock is on the rows of the
      classification a spec has, and the classification is the author's
      field, so reclassifying a dashboard swaps its locked rows with only a
      warning (§18, "Lifecycle and classification");
    - design.filled's behaviour after an edit: a fill drops its record, so
      deleting an edited fill lets `--fix` fill it again, where a standard's
      released record keeps the deletion (§15.29). Both follow "an author's
      edit or deletion wins" once fills keep a released record too;
    - design.filled entries for a renamed or removed chart, which
      validation still refuses, where design.standard_written accepts them
      and `standards apply` drops them.

Recorded during exceptions, mixed releases and the theme (§18, "Waivers"
onwards):

34. **Waivers are their own file, read with the standards.** The waivers file
    sits at the top of the standards folder, the place CODEOWNERS already
    guards, and is parsed when the folder loads, so a malformed entry fails
    every run as a broken standards file does. It is skipped by standards
    discovery: the folder still counts only when a YAML file declares a
    standard. A waiver names a dashboard by slug or by spec path; the MCP
    server sees no paths, so it matches slugs only, and slugs are the form to
    prefer.
35. **An expired waiver fails its dashboard even when nothing is left to
    cover.** The decision record says expiry is an error on pull requests
    touching the dashboard; read literally, that holds for a dashboard that
    conforms again, so the stale entry leaves the file at its next pull
    request. A softer reading (fail only while the waiver still covers a
    finding) was weighed and not taken: it would let expired entries pile
    up, the sprawl the adversarial review warned of.
36. **A waived item is left as the dashboard has it, everywhere.**
    `standards apply` adds, refreshes and rewrites nothing a waiver covers,
    even before the first apply, and even after expiry (with a warning):
    expiry is reported where the decision puts it, in `standards check` on the
    dashboard's next pull request and in the scheduled report, and never
    changes a spec by itself. Taking the waiver out of the file puts the
    standard's content back on the next apply.
37. **`min_superset`, one release per file, not a range.** The decision says
    "minimum release"; versions.py states releases as `since` strings, so the
    key takes one release in the same form. It covers the content its own
    file contributes, not the files that extend it, so an org can mark a
    6.x-only block without holding back a team's rows. Holding happens where
    the instance is known (`check`, `apply`, `plan` and their MCP tools) and
    removes only content the spec holds as the standard has it; an author's
    value is never held, so it can't be used to dodge the version check. The
    spec on disk is untouched: compile still reads one spec, and the instance
    picks what it can take.
38. **A theme travels as `theme_id`, not `theme_uuid`.** Superset's importer
    maps a `theme_uuid` only through a `themes/` file in the same bundle and
    otherwise clears the theme (docs/CONTRACTS.md, "Dashboard theme");
    shipping the target's theme file would recreate it if it were deleted
    mid-apply. The id is resolved by name at the resolve stage, as datasets
    are, so the bundle stays deterministic for a given resolution. An omitted
    theme stays unmanaged, as omitted tags and owners do, so a theme chosen in
    the UI survives every apply.
39. **A classification lock keys rows off the standard's value.** Without
    that, `--locked` would restore the classification and, in the same run,
    drop the confidential row it keys, because the rows were computed from the
    spec's changed value. An unlocked assignment writes the classification and
    its rows in one run, so a first apply converges.
40. **Still open from §15.33:** fills keep dropping their record after an
    edit (so a deleted edited fill is filled again), and design.filled still
    refuses entries for a renamed chart. Both are design-defaults work, not
    standards work, and stay as they were.

## 16. Design defaults (fills)

Some display fields have one sensible value the spec can work out on its
own: a monthly axis labelled `Sep 2026`, a count shown as `12,345`, a
table that pages by what fits its panel. The brain fills these in with
`advise --fix` (MCP `fix_spec`) when the author left them unset, writing
the value into the spec where the diff shows it. Nothing fills at compile
time (§14.13): compile, `plan`, decompile and `--design off` behave
exactly as before, and a spec that was never fixed builds the same bytes.

### The fills

Every fill is an `info` finding with a fix, never writes Superset's own
value (a fill that draws nothing new is only noise in the spec), and
changes no query. Thresholds are audience params (§6),
so `design.yaml` tunes them. §17 measured the grid and the value-label
thresholds; those marked judgement are usability choices, not pixel facts.

| rule | field | fills | only when |
|---|---|---|---|
| `default.x-label-format` | `x_label_format` | `%b %Y` at P1M, `%Y` at P1Y, `%d %b` at P1D or P1W | a timeseries chart (line, bar, area, scatter); for day labels, the chart's `time_range` spans at most `day_label_max_span_days` (365), since `%d %b` drops the year, and a 366-day span without a 29 February starts and ends on the same day and month. An omitted grain is read as its P1D default. Not on a categorical bar or a mixed chart, whose x axis may not be time |
| `default.compare-suffix` | `compare_suffix` | `vs previous month`, `vs 12 months earlier` | a trendline KPI with `compare_lag`, at a grain with a plain name (hour, day, week, month, quarter, year) |
| `default.count-format` | `number_format` | `,.0f` | every metric the chart shows is `COUNT` or `COUNT_DISTINCT`, on a chart with one `number_format` (not a table or a mixed chart); not with `contribution`, a 100 % stack, or a pivot aggregation that leaves fractions |
| `default.cell-bars` | `cell_bars` | `false` | a raw-mode table with a column named `id`, `code`, `year`, `zip`, `zipcode` or `postcode` as a whole trailing token (`order_id`, `fiscal_year`; not `uuid` or `zip_count`). With column types (`--profile`), only a numeric one counts. Aggregate tables draw bars on metrics only, so they never need it |
| `default.page-length` | `page_length` | the whole rows that fit beside the page-size bar and the pager | a table with an explicit `row_limit` larger than the rows that fit on one page, and a page of at least `page_min_rows` (3, judgement). The one grid model `size.table-window` reads, so the fill can never make that rule ask for more height |
| `default.search-box` | `search_box` | `true` | a raw-mode table with an explicit `row_limit` above `search_min_rows` (20, judgement), when the bar the box sits in hides no row: a paged table already draws it, and a table on one page must still fit every row beside it |
| `default.single-series-legend` | `show_legend` | `false` | a timeseries chart or categorical bar with one metric, no groupby, no series limit, no goal lines and no legend placement written, whose shown title or `y_axis_title` contains the metric's label, and that label is at least 3 characters long. Never a heatmap, whose legend is the colour scale |
| `default.value-labels` | `show_value` | `true` | a categorical bar with one metric, no groupby, no `contribution`, an explicit `row_limit` of at most `value_label_max_bars` (12). A vertical bar also needs a width of at least `value_label_min_width` (6/12); a horizontal bar needs the height to space its labels, 4.5 units plus 0.4125 a bar (§17) |

`narrative.big-number-format` stands down where `default.count-format`
offers the same remedy with a fix: one remedy, one finding.

The decision record's suggestion-only list stays without fills: category
sort (`chart.ordinal-order` reports ordinal names), y-axis truncation,
`compare_lag`, series limits (`chart.series-limit` reports them with
`--profile`), `show_totals` (Superset's totals row sums every group, not
the rows shown), and number or currency formats guessed from names. The
brief tells an author to set these only on request.

### Who owns a field

`design.filled` records, per chart, each field the brain filled and the value
it wrote: `{"Orders": {"page_length": 8}}`. That recorded value is what tells
the brain's work from the author's. Per chart and field, on every `--fix`:

- **Written, no record:** the author's. No fill ever touches it, even when it
  holds Superset's own value (`show_legend: true` keeps a legend). Rules know
  what was written from validation's `model_fields_set` (`RuleContext.written`);
  an explicit `null` counts as unset.
- **Recorded, and the chart still holds that value:** the brain's. It is
  recomputed from the chart as it is now, so a new height, grain, `row_limit`
  or groupby refreshes field and record together, and both are removed when
  the rule stops applying: a groupby added later brings the legend back.
- **Recorded, and the chart holds another value:** the author edited it. The
  record is dropped (a `fixed` entry of kind `release`) and the value kept; from
  then on it is written with no record, so the author's.
- **Recorded, and the field is gone:** the author deleted it. The record becomes
  `null` (kind `release`), and a null record means the brain never fills that
  field again. A deliberate deletion sticks; deleting the null entry lets the
  brain fill it once more. A value the author later writes there is theirs,
  and its null record is dropped.
- **Unset, no record:** filled when the rule applies, and recorded.

So the author takes a fill over by doing the obvious thing, editing or deleting
the field, and never needs to touch `design.filled`. (A standard's content keeps
a released record after an edit, so a later deletion of the edited value stays
deleted; a fill's dropped record lets `--fix` fill that field again. §15.33
lists aligning the two as phase 4 work.) To keep a field unset
before anything was filled, ignore the rule for that chart
(`"default.page-length@Orders"`).

The record is what makes both halves sound. Without it, an edit and a change
of input look alike: either every stale fill would freeze as the author's (a
legend hidden for one series staying hidden after a groupby adds five), or an
author's edit would be overwritten on the next `--fix`. Comparing the chart's
value with the value written tells them apart exactly. The list form
`{chart: [fields]}`, which never shipped in a release, is refused with a
message naming the new form.

`design.filled` is the brain's record of its own writes, the same direction as
§15.13, which names explicit provenance as the real fix for inferred signals.
It is validated: every key a chart, every field one the brain fills and the
chart has, every value one that field takes (or null). A renamed chart must
carry its entry. Brain output never silences a rule: `size.table-window` and
`size.grid-fit` still report a page the brain filled that no longer fits, but
leave the height alone, because that page follows the height and the fill
phase refits it; an author's page gets the ordinary height fix. Decompile
cannot recover provenance, so every field of a decompiled spec reads as the
author's.

### The loop, sketches, and redesign

Fills run after repairs settle (§9), so a page is computed from the height
the repairs leave. They write no geometry and no field a repair writes,
which keeps the loop's convergence invariant, and a second `--fix` is a
no-op (both tested). Charts in a sketch take fills like any other: no fill
changes a height or width, so the drawing stays true and §15.9's
stale-drawing disclosure never applies to them. `redesign` writes no fills;
its `next` line names `advise --fix` when some are waiting.

### Seeing them

- `advise`, and the advice `check` and `apply` carry, list each waiting
  fill as an `info` finding with `fixable: true`. Info never blocks
  `--design strict`.
- Each `fixed` record says `kind: "fill"` (or `"release"` when it hands a field
  to the author) and why.
- `chartwright explain <spec> [--chart NAME] [--json]` shows, per chart,
  every field a fill governs: its value, whether it came from the spec, a
  fill, or Superset's own default, the value `design.filled` recorded, the
  rule, the reason, and how to change it. MCP `explain_spec` returns the same
  JSON.

## 17. Calibration

Measured on 2026-10-03 against rendered Superset 4.1.4, 5.0.0 and 6.1.0, each
with the example data and its default theme, in headless Chromium at a 1600 px
wide viewport. Every holder measured exactly 40 px per spec unit on all three
releases.

### Method

- `chartwright apply` built calibration dashboards (slugs `cw-calib-*`):
  - raw tables at heights 6 to 20, four ways: plain, paged, with a search box,
    and with both;
  - pivots of 19 rows: with no column dimension or one, each with and without
    a totals row;
  - a 5-row by 12-month pivot at widths 4, 6 and 12 and heights 7 to 10;
  - categorical bars, vertical and horizontal, at widths 4, 6, 8 and 12 with
    4, 8, 12, 16 and 24 bars, labelled `12,345`-style (`,.0f`) or `71.4`-style
    (`.1f`);
  - time axes in each fill's label format.
- A Playwright script logged in, scrolled each chart into view and read the
  DOM:
  - the holder, title and header heights;
  - the bar above the rows and the pager;
  - each body row, and the rows that sit wholly inside every clipping
    ancestor, which are the rows a reader sees without the inner scrollbar.
- Value labels were read from each chart's ECharts instance: every label's
  box, whether ECharts hid it, and which labels overlap. Screenshots of every
  chart back the numbers.

### Tables

| | 4.1.4 | 5.0.0 | 6.1.0 | model |
|---|---|---|---|---|
| body row | 27.8 px | 27.8 px | 29.4 px | 0.74 units (29.6 px) |
| title, column header and padding | 97.8 px | 97.8 px | 99.4 px | 2.5 units (100 px) |
| bar above the rows (search box, or any `page_length`) | 39.1 px | 39.1 px | 41.1 px (33.1 px for a page size alone) | 1.05 units (42 px) |
| pager, when there is more than one page | 42.9 px | 42.9 px | 57.9 px | 1.45 units (58 px) |

A table with `page_length` draws the page-size bar even when every row is on
one page; the pager appears only with a second page.

Full rows visible at each height, from 6 to 20 units:

| table | 4.1.4 and 5.0.0 | 6.1.0 |
|---|---|---|
| plain | 5 6 8 9 10 12 13 15 16 18 19 20 22 23 25 | 4 6 7 8 10 11 12 14 15 17 18 19 21 22 23 |
| paged | 2 3 5 6 7 9 10 12 13 15 16 18 19 20 22 | 1 3 4 5 7 8 9 11 12 13 15 16 18 19 20 |
| search box | 3 5 6 8 9 10 12 13 15 16 18 19 21 22 23 | 3 4 6 7 8 10 11 12 14 15 17 18 19 21 22 |
| paged, with a search box | 2 3 5 6 7 9 10 12 13 15 16 18 19 20 22 | 1 2 4 5 6 8 9 10 12 13 15 16 17 19 20 |

### Pivots

The three releases drew pivots identically:

- a body row is 25.8 px on average (19 rows: 490.1 px);
- a header row is 26.3 px. A pivot with one metric draws a header row per
  column dimension, plus two;
- the card frame is 101 px;
- `column_totals` pins a 28.8 px totals row over the bottom of the grid, so it
  covers the last data row rather than adding one below it.

| pivot | rows visible at 6 to 20 units |
|---|---|
| no column dimension | 3 4 6 7 9 11 12 14 15 17 18 19 (all 19 from 17 units) |
| one column dimension | 2 3 5 6 8 10 11 13 14 16 17 19 (all 19 from 17 units) |
| no column dimension, totals (data rows clear of the totals row) | 2 3 5 6 8 10 11 13 14 16 17 19 |
| one column dimension, totals | 1 2 4 5 7 9 10 12 13 15 16 18 19 |

A pivot whose columns outgrow the panel scrolls sideways. Headless Chromium
draws overlay scrollbars, which take no room. With classic scrollbars the
horizontal bar took 11 px; Windows draws 17 px, which was not measured here.

The finding this explains: smoke called a 5-row cause by month pivot fine at
height 8, and its last row was hidden. Five rows under three header rows leave
11.6 px to spare at 8 units. A totals row (28.8 px) or a Windows scrollbar
(17 px) takes that room. The 5 by 12-month pivot with a totals row hid its fifth
row at 8 units on every release.

### The model, old and new

| constant (`chartwright/spec.py`) | before | now |
|---|---|---|
| `GRID_UNITS_PER_ROW` | 0.75 | 0.74 |
| `GRID_HEADER_UNITS` | 3 | 2.5 |
| `GRID_CONTROLS_UNITS` (search box or page size) | none | 1.05 |
| `GRID_PAGER_UNITS` (`_PAGER_ROWS` = 1 row, 0.75 units, in `rules.py`) | 0.75 | 1.45 |
| `PIVOT_UNITS_PER_ROW` | 0.75 | 0.65 |
| pivot header (`PIVOT_FRAME_UNITS` + `PIVOT_HEADER_ROW_UNITS` per header row) | 3, or 4 with column dimensions | 2.55 + 0.66 × (column dimensions + 2) |
| `PIVOT_TOTALS_UNITS` | none | 0.72 |
| `PIVOT_HSCROLL_UNITS` (pivots with column dimensions) | none | 0.45 |

Each constant is the largest of the three releases, rounded up, so the model
never counts a row any release hides. On 6.1.0 it counts at most one row fewer
than the screen shows: two on a pivot with column dimensions, for the
scrollbar allowance. `tests/test_grid_calibration.py` holds the measured
counts and checks both bounds.

What the old model got wrong:

- It knew nothing of the search box. A search box took a row from every
  table, and no rule or fill counted it.
- It counted the pager as one row (30 px). A paged table spends 82 to 91 px
  on its page-size bar and pager.
- The page fill therefore wrote pages the panel could not hold. At 8 units it
  wrote 5 rows where 6.1.0 shows 4, and at 12 units 11 where 6.1.0 shows 9.
- It gave pivots 120 px of header, or 160 px with column dimensions. They
  draw 154 px and 180 px, so at 6, 7 and 9 units it counted a row the pivot
  hid.
- It ignored the totals row.

### What changed

- `grid_header(chart, rows)` in `spec.py` gives every consumer the same header
  and row size for a table or pivot as configured. `size.table-window`,
  `size.pivot-window`, `size.grid-fit`, smoke, `default.page-length` and
  `default.search-box` all read it. `_PAGER_ROWS` is gone.
- `default.page-length` writes the whole rows that fit beside the page-size
  bar and the pager.
- `default.search-box` stands down on a table that shows every row on one
  page when the bar would push a row behind the scrollbar. A paged table
  already draws the bar, so a search box there costs nothing. Filling a
  search box can no longer hide a row.
- Smoke counts a pivot's totals row and names it in the warning.
- `default.value-labels` treats a horizontal bar separately (below).
- `day_label_max_span_days` is 365, down from 366.

Rendered check of the fills: tables at heights 6 to 20 filled by
`advise --fix` and applied to all three releases:

- from 8 to 20 units, every filled page fit with its page controls, and on
  raw tables its search box, with no inner scrollbar on any release;
- at 6 and 7 units no page fits beside a pager, so no page is filled and
  `size.table-window` keeps its warning;
- a 25-row table on one page took the search box at 23 units and showed all
  25 rows. At 21 units the fill stood down.

### Value labels on categorical bars

Superset sets no overlap handling on bar value labels (no `labelLayout`), so
ECharts never drops one: crowded labels overlap. A `12,345` label is 37.5 px
wide on 6.1.0 and 38.7 px on 4.1.4 and 5.0.0. A `71.4` label is 25.4 px. Labels
are 14.25 px tall on 4.1.4 and 5.0.0, and 12.25 px on 6.1.0.

| vertical bars | overlapping, on every release |
|---|---|
| `12,345` labels | 4/12 with 12, 16 or 24 bars; 6/12 with 24 bars |
| `71.4` labels | 4/12 with 16 or 24 bars |

- At 6/12 with 12 bars, `12,345` labels clear each other by 12 px or more.
- `value_label_max_bars` (12) and `value_label_min_width` (6/12) hold for
  labels up to six characters. Wider labels such as `1,234,567` need more room
  than the fill can see.
- Narrower panels hold fewer labels. At 4/12, 8 bars cleared on every release;
  the fill still waits for 6/12.

Horizontal bars put each label beside its bar, so the panel's height spaces
the labels, not its width:

- no label was clipped at any width, 4/12 included;
- 12 bars at 8 units overlapped on 4.1.4 and 5.0.0. 16 bars at 10 units and
  24 bars at 14 units did not;
- the chart spends 180 px on its title, legend and axis (5.0.0; 164 px on
  4.1.4 and 6.1.0), and a bar needs 16.5 px to keep its label clear;
- the fill now asks a horizontal bar for 4.5 units plus 0.4125 units a bar,
  instead of a width. Before, it filled labels on any horizontal bar at 6/12
  or wider, including the overlapping 12 bars at 8 units.

### Time-axis labels

`%b %Y` (monthly, 29 points), `%Y` (yearly) and `%d %b` (daily and weekly
windows of 30 to 366 days) rendered legibly at 4/12, 6/12 and 12/12 on every
release, with no overlapping labels. ECharts thins the ticks to fit.
`day_label_max_span_days` dropped from 366 to 365. A window of 366 days without
a 29 February starts and ends on the same day and month, so `%d %b` could name
two dates. Superset's `Last year` spans 365 days and still takes the fill.

### Not measured

- Other viewports and themes. The constants are pixel sizes, so a
  deployment with a larger font or denser theme needs its own measurement.
- Windows scrollbars. The 17 px allowance comes from the platform default,
  not from a rendered measurement.
- Pivot subtotal rows (`row_subtotals`), transposed pivots, metrics laid out
  as rows, and wrapped header text. They draw extra rows the model does not
  count.
- Wide raw tables that scroll sideways. The model reserves no scrollbar room
  for tables.
- Vertical bars at 5/12, and labels longer than six characters.
- The other thresholds this page names: axis heights, pie and heatmap
  geometry, the horizontal-bar height per bar in `size.hbar-window`, and
  `vbar_max_categories`.

`search_min_rows` (20) and `page_min_rows` (3) are usability judgement, not
pixel facts, and stay so. On page sizes, Nielsen recommends that "it's usually
better to offer a single default number — such as 10 or 20" ("Users'
Pagination Preferences and 'View All'", NN/g, 28 April 2013). A search box past
20 rows follows that scale.

## 18. Standards

A standard is the rule settings a team shares: the parameters, severities and
disabled rules `design.yaml` holds for one machine, kept in the repository
instead, where a pull request reviews them and every machine reads the same
files. A standard can lock rules and parameters so that no team, spec or
personal file below it loosens them. A standard can also carry content (header
and footer rows, CSS, colours, certification, number formats, a Superset theme,
a classification) that `standards apply` writes into its specs ("Content",
below). One dashboard may deviate from a lock only through the waivers file,
with an owner, a reason and an expiry ("Waivers", below). Compile, `plan`
and decompile never read a standards file, and a spec builds the same bundle
with or without one (§14.15); content reaches a dashboard only as fields
written in its spec. Rule settings also change what `advise --fix` writes,
since height repairs and fills read its parameters (§15.25).

### The files

A `standards/` folder holds YAML files, one standard each, in any subfolder:

```yaml
# standards/org.yaml
name: org
default: true                 # specs without design.standard follow this one
params:
  fold_units: 40
severity:
  narrative.title-style: warn
locked:
  rules: [size.axis-min-height, size.min-width]
  params: [fold_units]
```

```yaml
# standards/teams/finance.yaml
name: finance
extends: org
params:
  min_axis_height: 7
audiences:
  executive: {kpi_row_max: 4}
severity:
  size.axis-min-height: error   # raising a locked rule is allowed; lowering it is not
disable: [narrative.title-style]
```

| Key | Holds |
|---|---|
| `name` | Required. Letters, digits, `-` and `_`; unique in the folder. Specs name it in `design.standard`. |
| `extends` | Optional. One parent, by name. |
| `default` | Optional, `true` on at most one file: the standard a spec without `design.standard` follows. |
| `min_superset` | Optional, a release such as `"6.0"`: the content this file contributes is for that Superset release or later, and is held back from older instances ("Mixed Superset releases", below). Quote it, so YAML reads `"6.10"` as a release and not the number 6.1. |
| `params` | Parameters for every audience (§6), as in `design.yaml`, `recommended_heights` included. |
| `audiences` | Parameters for one audience, as in `design.yaml`. |
| `severity` | Rule id → `error`, `warn` or `info`. |
| `disable` | Rule ids the standard turns off. |
| `content` | Spec content `standards apply` writes ("Content", below). |
| `classifications` | The values `dashboard.classification` may take ("Content", below). |
| `locked` | `rules`: rule ids, and `params`: parameter names, that no layer below may loosen; `content`: content slots ("Content", below). |

Rule ids go through the alias table, so a renamed rule keeps working. An
unknown rule id, parameter or audience is an error that names the file, with
a did-you-mean: a misspelt lock would otherwise be a lock that never locks. A
`rule@Chart` entry is refused, since a standard covers dashboards whose charts
it can't know; per-chart exceptions stay in the spec's `design.ignore`.

### Layers

A chain holds at most three files, each extending the one before: the org's,
a unit's, and a team's (a team directly under the org skips the unit). The
dashboard's own `design` block is the layer after them. A chain of four files
is an error that names every file in it, as are a cycle and a parent no file
names. There is no folder cascade: a file's place in the folder means nothing,
and only `extends` builds a chain. Every file and chain is checked when the
folder loads, whether or not a spec uses it, so a broken file fails every run
that reads the folder.

How each key combines down the chain:

| Key | A lower layer |
|---|---|
| `params` | overrides per parameter; `recommended_heights` per chart type |
| `audiences` | overrides per audience, per parameter |
| `severity` | overrides per rule (a locked rule only upward) |
| `disable` | adds to the list; nothing below turns a disabled rule back on |
| `locked` | adds to the lists; nothing below unlocks |
| `name`, `extends`, `default` | belong to their own file, never inherited |

Within the resolved standard, an audience's block beats `params`, as in
`design.yaml`: a team that wants to override the org's executive value writes
it under `audiences.executive`.

### Locks

A rule a layer locks:

- can't be disabled below it, by a lower file or by `design.yaml`;
- can't have its severity lowered below it. The baseline is the level the
  chain has set so far, or, when none is set, the most severe level the rule
  emits (so `warn` on a `warn/error` rule is a lowering). Raising is allowed;
- can't be silenced by the spec's `design.ignore` or by `advise --ignore`, in
  any form (`rule`, `rule@Chart`, `rule@band`). The entry stays in the spec and
  shows in the advice as `refused_ignores`;
- doesn't stand down for a fractional height (§2.4): a hand-written 7.5 would
  otherwise unlock a sizing rule.

A locked parameter can't be set below the layer that locks it, in `params` or
in any `audiences` block, and `design.yaml` can't move it. The lock holds a
value, not a slot: by the locking layer the parameter must be set, in that
file or one it extends, either in `params` or under every audience in
`audiences`; a lock on an unset parameter is an error. Otherwise the value
would be the audience preset's, and a spec could move it by choosing a looser
`design.audience`. With per-audience values the spec's audience picks one of
the values the standard set.
A lower file that loosens a lock is an error naming both layers; the
spec-level and `design.yaml` attempts are set aside and reported, never
silently applied. Locks are recomputed from the standards files on every run;
nothing in a spec can claim or waive one. Only `standards/waivers.yaml`, beside
the standards files, lets one dashboard deviate from a lock ("Waivers",
below).

### Which standard a spec follows

The spec's `design.standard` names it. A spec without one follows the
standard marked `default: true`, if any, and otherwise none. Moving a
dashboard to another team is a one-line diff of that field.

`chartwright standards assign <files|folders|globs> --standard NAME` writes
the field into every spec it matches, so a folder layout or a list seeds the
assignment once: `standards assign specs/finance --standard finance`. It
checks the name first and writes nothing for an unknown one, leaves a spec
already naming the standard untouched, and never writes a file that isn't a
valid spec. There is no mapping file: the arguments are the mapping, and the
field is the one record a reader or an MCP client can see.

Older chartwright releases reject `design.standard` (the spec model forbids
unknown fields), so CI must run a release that knows it.

### Finding the folder

`--standards DIR` names it. Without the flag, chartwright looks for a
`standards` folder in the spec's own folder and each folder above it, up to
the repository root, the first folder holding `.git`:

- a folder counts only when at least one of its YAML files declares a
  standard (a mapping with a `name` key), so a repository's unrelated
  `standards/` (linter settings, style guides) changes nothing. Once a folder
  counts, every YAML file in it must be a valid standard;
- it uses the one it finds; nothing merges;
- two on the way up is an error, so a stray `specs/finance/standards/` can't
  quietly take over the specs beneath it;
- outside a git repository it finds nothing; pass `--standards`.

A spec that names a standard when no folder is found is an error, never a
silent pass. The MCP server sees specs, not paths, so it reads the folder
`$CHARTWRIGHT_STANDARDS_DIR` names, or, without the variable, discovers one
from its own working directory the same way; started inside the repository,
it applies what the CLI applies. The CLI never reads that variable, so a
setting on one machine can't change a CLI run.

### Where a standard applies

`advise` (with `--fix`), `explain`, and the advice `check` and `apply` carry
apply the spec's standard, on the CLI and through MCP (`advise_spec`,
`fix_spec`, `explain_spec`, `check_spec`, `build_dashboard`). Each value comes
from the last of these layers that sets it:

1. the audience preset (§6);
2. the standard, root first: the org's file, then the team's;
3. `design.yaml`, only outside a strict gate and only for what no standard
   locks;
4. the spec's `design` block: `audience` picks the preset, `ignore` silences
   open rules;
5. the CLI's `--audience` and `--ignore`, with the same limits.

A standard applies in strict gates too, since its files are the same on every
machine. `brief` and `redesign` take no spec, so they don't apply one.
Calibrated heights belong in the org standard's `params.recommended_heights`:
`calibrate --write` still writes `design.yaml`, which strict gates and
`standards check` set aside, so copy its proposals into the standard in a
pull request.

The advice payload (§10) gains, only when a standard applies:

- `standard`: `name`, `chain` (root first), `via` (`design.standard` or
  `default`), `locked` (`rules` and `params`, each with the layer that locked
  it) and `refused_ignores` (each refused entry and the layer that locks it);
- on each finding, `layer`: the standard that set its severity, `overlay`, or
  `rulebook` for the rule's own level; and `locked`;
- in the `overlay` block outside a strict gate, `set_aside`: the `params`,
  `disable` and `severity` entries a lock overrode;
- `waived`: locked findings a waiver let pass, and `warnings` for an expired
  waiver that still applied ("Waivers", below), only when a waiver names the
  dashboard.

Without a standards folder nothing here appears and nothing changes. `explain`
names the chain in its header (`standard org -> finance`) and in its JSON.
A standard that can't be resolved (a broken file, an unknown name) stops
`advise` and `explain` with a typed `standards` error; in the advice `check`
and `apply` carry it is an error note that `--design strict` blocks on, as a
broken `design.yaml` does (§15.14).

### standards check

`chartwright standards check <files|folders|globs> [--strict] [--report]`
runs `advise` over every spec with its standard applied and `design.yaml` set
aside entirely: no parameter, severity or disable entry from it reaches the
result, and the payload names the file it set aside. The specs must find one
standards folder between them. A JSON file without a top-level `spec_version`
(a `package.json` beside the specs) is no spec: it is listed under `skipped`
and never fails the run, here and in `standards assign`; a file that isn't
valid JSON is still reported as unreadable. It exits 1 when any spec has an
error finding, or a warn under `--strict`, or can't be read, or names a
standard the folder lacks; a broken standards folder fails the whole run.

It runs offline, so the data-aware rules (§8) don't run in it: a locked
data-aware rule is enforced only where advice has a live resolution, by
`check --design strict` or `advise --profile --strict`.

Per spec it reports `ok`, `standard`, `chain`, `via`, `audience`, `counts`,
`findings` (each with `layer` and `locked`), `ignored`, and `locks` (the
locked rules and parameters, and `refused_ignores`); a failing spec carries a
`design_gate` error that names any locked rule behind it, and, when they
apply, `waived` and, under `--superset-version`, `superset_version` and
`held` ("Waivers" and "Mixed Superset releases", below). MCP
`standards_check` returns the same entry for one spec. `--as-of DATE` reads
waiver expiry as of that day.

`--report` prints the fleet report instead: per spec its standard, `ok`,
`counts`, `by_rule` (findings counted by rule and severity) and `locks_hit`
(locked rules with a finding or a refused ignore); then `totals` across the
fleet: specs passed and failed, counts, `by_rule`, `by_standard` and
`locks_hit`. A spec with waived findings or held items lists them, and with a
waivers file the report adds its `waivers` block. Its exit code is the
check's: 1 when any spec fails. It reads the repository's specs; it doesn't
read a live instance.

### standards show

`chartwright standards show [NAME | --for SPEC] [--json]` prints a standard
after `extends`: each key with its value, the layer that set it and whether
it is locked, plus the files of the chain. Without a name it shows the
default. MCP `standards_show` returns the same JSON.

```
Standard finance: org -> finance
  org          standards/org.yaml
  finance      standards/teams/finance.yaml

  params.fold_units                40       org          locked
  params.min_axis_height           7        finance
  audiences.executive.kpi_row_max  4        finance
  severity.narrative.title-style   warn     org
  severity.size.axis-min-height    error    finance      locked
  disable.narrative.title-style             finance
  rule size.min-width                       org          locked
```

A standard with content lists it too: each item with its value, layer and
lock, the rows of every lifecycle state and classification, and a number
format per metric label (`content.footer[org][0]`, `content.number_format[Orders]`).
`--json` adds `content`, `locked.content` and `classifications`, and each
chain file's `min_superset` when it declares one.

### Content

A standard can carry spec content for a closed list of slots, and
`chartwright standards apply` writes it into every spec that follows the
standard. The content becomes ordinary fields of the spec, reviewed in the
diff like any edit; compile, `plan` and decompile never read the standards
files (§14.16).

```yaml
# standards/acme.yaml
name: acme
default: true
classifications: [public, internal, confidential]
content:
  footer:
    - [{markdown: "Acme Corp. Internal data: do not share outside the company.", width: 12, height: 1}]
  footer_by_classification:
    confidential:
      - [{markdown: "**Confidential**: named recipients only.", width: 12, height: 1}]
  header_by_lifecycle:
    deprecated:
      - [{markdown: "**Deprecated.** Use the dashboard named as its successor.", width: 12, height: 1}]
  css: |
    .dashboard-markdown { font-family: Inter, sans-serif; }
  label_colors: {Revenue: "#1FA8C9"}
locked:
  content: [footer, footer_by_classification, css]
```

```yaml
# standards/teams/ops.yaml
name: ops
extends: acme
content:
  header:
    - [{markdown: "**Operations** | questions: #ops-data", width: 12, height: 1}]
  css: |
    .dashboard-markdown h2 { color: #003366; }
  color_scheme: supersetColors
  certified_by: Ops analytics
  number_format: {Flights: ",.0f"}
```

| Slot | Writes | Down a chain |
|---|---|---|
| `header` | rows into `layout.header` | adds up: each layer's rows, the root's first, above the author's rows |
| `footer` | rows into `layout.footer` | adds up: each layer's rows, the innermost's first, below the author's rows, so the org's row sits at the very bottom |
| `header_by_lifecycle` | rows per `dashboard.lifecycle` state (`active` when the spec has none), as a banner at the top of the layer's header rows | adds up, as `header` |
| `footer_by_classification` | rows per `dashboard.classification`, after the layer's footer rows | adds up, as `footer` |
| `css` | one marked block per layer at the start of `dashboard.css`, root first, the author's CSS after them | adds up: one block per layer |
| `color_scheme`, `certified_by`, `certification_details` | the dashboard setting | the innermost layer's value |
| `theme` | `dashboard.theme`: a Superset theme by name, 6.0 or later ("A theme", below) | the innermost layer's value |
| `classification` | `dashboard.classification`, which must be in the `classifications` list ("Lifecycle and classification", below) | the innermost layer's value |
| `label_colors` | each label's colour | per label: keys add up, the innermost layer's colour wins a key |
| `number_format` | a chart's `number_format`, per metric label | per label, as `label_colors` |

Rows hold markdown blocks, headers and dividers, never a chart: a standard
can't know a dashboard's charts. For the same reason a number format is
keyed by metric label, and a chart takes one only when every metric it shows
maps to the same format; a chart that plots shares (`contribution`, a 100 %
stack) or a pivot whose aggregation changes the unit takes none. A CSS block
reads:

```css
/* cw:std acme 071e8af9c13a */
.dashboard-markdown { font-family: Inter, sans-serif; }
/* cw:end acme */
```

The hash is taken over the block's text. The markers are CSS comments, so they reach
Superset with the rest of the CSS, and Superset and decompile keep them as
written (§15.28). Standards write no geometry and no field a repair writes;
the markdown-height repair leaves a standard's rows alone (§15.30).

`locked.content` lists slots. A lock covers the items the locking layer and
the layers above it put in that slot, and like a parameter lock it needs a
value: a lock on a slot no layer at or above it fills is an error. Below the
lock a layer may still add its own rows, blocks and keys, but can't change a
locked colour scheme, certification, label or format: that is an error naming
both layers. A layer may narrow the `classifications` list it inherits, never
widen it. A standard that locks content also locks `standard.content-locked`
and `standard.css-hides`, so nothing below it (a lower file's `disable`,
`design.ignore`, `--ignore`, `design.yaml`) silences them. Certification details need
`certified_by` somewhere in the chain.

#### The record: design.standard_written

`standards apply` records each item it wrote, rows and CSS blocks by hash,
everything else by value. The spec of an `ops` dashboard classified
`confidential`, after `standards apply specs/ops`:

```json
"design": {
  "standard": "ops",
  "standard_written": {
    "charts[Flights].number_format": {"layer": "ops", "value": ",.0f"},
    "dashboard.certified_by": {"layer": "ops", "value": "Ops analytics"},
    "dashboard.color_scheme": {"layer": "ops", "value": "supersetColors"},
    "dashboard.css[acme]": {"layer": "acme", "hash": "071e8af9c13a"},
    "dashboard.css[ops]": {"layer": "ops", "hash": "d54caf00ff9b"},
    "dashboard.label_colors[Revenue]": {"layer": "acme", "value": "#1FA8C9"},
    "layout.footer[acme][0]": {"layer": "acme", "hash": "c2f56576d108"},
    "layout.footer[acme][classification=confidential][0]": {"layer": "acme", "hash": "a1ec23028ea5"},
    "layout.header[ops][0]": {"layer": "ops", "hash": "d3544e7e1c8c"}
  }
}
```

Each key names one item: a layer's row by its place in that layer's list, a
layer's CSS block, a setting, a label, a chart's format. The record is §16's
"value written" rule, item by item, so an edit to one row or block hands over
that item and nothing else:

| The spec holds | Unlocked | Locked |
|---|---|---|
| the value recorded | the standard's: refreshed when the standard changes | the same |
| another value (an edited setting, label, format or block; for a row, the row of the same shape where it stood) | released: the author's, and apply leaves it alone. The record keeps what was written, marked `"released": true`, so a later deletion of the author's value stands too | a violation: an error in `standards check`, reported and left by `standards apply`, rewritten by `standards apply --locked` with `was` |
| nothing, where the record has a value | deleted: the record is marked released, and the item is never written again until the author deletes that entry | a violation, as above |
| nothing, and no record | the standard's value is written | the same |
| the standard's value, with no record (a decompiled or adopted dashboard) | left as it is, never written twice; `--claim` records it | the same, except CSS: locked CSS found unmarked conforms only once `--claim` marks it, and is otherwise written as a block |
| a CSS block with no record whose marker hash still matches its text (the standard's own earlier version, as after a decompile) | left as it is; `--claim` takes it over and brings it up to date | a violation; `--claim` brings it up to date |
| a value of the author's, with no record | the author's | a violation |

This is where content parts from §16's fills: an edited fill drops its record, so
deleting the edited value lets `--fix` fill it again, while a released standard item
keeps its record, so the author's deletion is never undone. The decision record's
rule, "an author's edit or deletion wins", governs both; fills keep their shipped
behaviour until phase 4 revisits them.

Unmarked CSS counts only as whole top-level rules, in order, outside comments:
`.sidebar .dashboard-markdown {...}` doesn't hold the standard's
`.dashboard-markdown {...}`, and text inside a comment is no match. A block's
marker carries the hash of the text apply wrote, kept true on every write: a
marker whose hash no longer matches its text was edited by hand.

Header and footer rows are found by the hash recorded for them, taken over
the row with its defaults written out (a markdown block's width and height),
so an author can insert rows anywhere and a decompiled row still matches. A
record follows its hash when the standard adds, drops or reorders rows, and a
row that no longer matches is, when it can be told, the row of the same shape
where it stood (§15.29). A new CSS block goes after any `@charset`, `@import`
or `@namespace` the author's CSS starts with, which browsers honour only
before every rule.
`design.standard_written` and `design.filled` never record the same field
(§15.30), and the record never reaches the bundle.

#### standards apply

`chartwright standards apply <files|folders|globs> [--check [--strict]]
[--locked] [--claim] [--standard NAME] [--json]` writes each spec's standard into it,
deterministically, and rewrites only the files whose data changed. A spec that
follows no standard is left as it is (§15.32); a spec apply can't read in
full (CSS markers it can't parse) is left as it is and reported. It prints a
summary grouped by standard and item, ready for a pull request's description:

```
standards apply: 3 specs following a standard; wrote 3, 0 unchanged

ops (acme -> ops), 3 specs
  charts[Flights].number_format: same change × 3 (add ,.0f)
  dashboard.certified_by: same change × 3 (add Ops analytics)
  dashboard.color_scheme: same change × 3 (add supersetColors)
  dashboard.css[acme] (locked by acme): same change × 3 (add 071e8af9c13a)
  dashboard.css[ops]: same change × 3 (add d54caf00ff9b)
  dashboard.label_colors[Revenue]: same change × 3 (add #1FA8C9)
  layout.footer[acme][0] (locked by acme): same change × 3 (add c2f56576d108)
  layout.footer[acme][classification=confidential][0] (locked by acme): add a1ec23028ea5: specs/ops/delays.json
  layout.header[ops][0]: same change × 3 (add d3544e7e1c8c)
```

Later the `ops` file changes its header row; one author picked another colour
scheme and another rewrote the locked footer row. `--check` writes nothing:

```
standards apply --check: 3 specs following a standard; would write 3, 0 unchanged
1 spec lacks locked content as the standard has it now

ops (acme -> ops), 3 specs
  charts[Flights].number_format: 3 already current
  dashboard.certified_by: 3 already current
  dashboard.color_scheme: 1 released by authors, skipped: specs/ops/fleet.json; 2 already current
  dashboard.css[acme] (locked by acme): 3 already current
  dashboard.css[ops]: 3 already current
  dashboard.label_colors[Revenue]: 3 already current
  layout.footer[acme][0] (locked by acme): 1 changed by their authors but locked, left as they are (--locked rewrites them): specs/ops/routes.json; 2 already current
  layout.footer[acme][classification=confidential][0] (locked by acme): 1 already current
  layout.header[ops][0]: same change × 3 (refresh 3ea8388005ab)
```

- `--check` writes nothing and exits 1 when a spec lacks locked content as
  the standard has it now: never written, written in an older version, or
  changed by its author. Unlocked changes are listed and pass, so they reach
  each team in that team's own pull request; `standards check --strict`
  fails on them (§14.16).
- `--check --strict` fails on any change apply would make, unlocked content
  and the record included, like `black --check`, for a repository that wants
  every spec current at every commit.
- `--locked` also rewrites locked items their authors changed; each such
  change says `rewrite` and carries `was`. Without it they are reported, and
  apply exits 1.
- `--claim` records items whose value already equals the standard's, for a
  decompiled or adopted dashboard, and marks the standard's CSS where it
  sits unmarked in the dashboard's CSS.
- `--standard NAME` limits the run to the specs that follow that standard:
  one pull request per team.
- `--json` prints every spec's `changes` (`item`, `action`, `layer`,
  `locked_by`, `to`, `was`), `stale` (apply would change content),
  `locked_stale` (what `--check` fails on), `locked` (locked items left as
  their authors changed them), and the `summary` the text shows.

MCP `standards_apply` (`check`, `locked`, `claim`) does the same for one
spec and returns the patched spec.

#### What check reports

In every advice run that applies a standard with content (`advise`,
`standards check`, and the advice `check` and `apply` carry):

| Rule | Severity | Reports |
|---|---|---|
| `standard.content-locked` | error | a locked item the spec doesn't hold as the standard has it, naming the layer that locks it and what puts it back |
| `standard.content-stale` | warn | an unlocked item `standards apply` would add, refresh or remove |
| `standard.content-released` | info | an item the author took over, so a fleet report shows every override |
| `standard.classification` | error | a classification the standard's list lacks |
| `standard.waiver-expired` | error | a waiver naming this dashboard that has expired, in `standards check` and `advise` only ("Waivers", below). Locked whenever a waiver names the dashboard, and no waiver covers it |
| `standard.css-hides` | warn | a heuristic: a declaration it knows to hide elements, outside the blocks of the layers that lock header or footer rows (a lower layer's block included), comments set aside. It knows `display: none`; `visibility: hidden` or `collapse`; `content-visibility: hidden`; a zero opacity or font size in any spelling; `color: transparent`; a zero height or max-height with hidden overflow; `clip` or `clip-path`; `transform: scale(0)`; and a large negative offset (`left: -9999px`). Any other way to hide an element passes it; `standards verify-visible` checks the rendered dashboard ("Checking what readers see", below). The warning names the rule to look at; like `standard.content-locked`, it is locked with the content |

#### explain

`explain` gains a dashboard section when the spec's standard has content:
each item with its value, layer, lock, source (`standard`: as the standard
wrote it; `missing`: not written yet; `released`: the author took it over;
`author`: the author's own value), what `standards apply` would do, and how
to override it. A chart field the standard wrote shows source `standard` in
its chart's rows. MCP `explain_spec` returns the same JSON under
`dashboard`.

```
Dashboard content (standard acme -> ops, via design.standard)
  layout.footer[acme][0]           "Acme Corp. Internal data: share insi... acme       locked  author
      standard: "Acme Corp. Internal data: do not sha...
      standards apply --locked puts the standard's back
      override: acme locks it: change it in acme's standards file, not in the spec
  dashboard.css[ops]               d54caf00ff9b (1 line)                    ops        open    standard
      override: edit or delete it in the spec and it is yours; standards apply leaves it alone from then on
```

#### Lifecycle and classification

`dashboard.lifecycle` (`state`: `active`, `deprecated` or `sunset`, with an
optional `successor` slug and `sunset_date`) and `dashboard.classification`
(a word or a few, or one of the values the standard's `classifications`
lists) are spec-only. Compile, `plan` and decompile ignore them, so a
decompiled spec has neither and a spec builds the same bytes with them. They
reach a dashboard through a standard's `header_by_lifecycle` and
`footer_by_classification` rows. Changing either swaps those rows on the next
`standards apply`.

A lock on `footer_by_classification` (or `header_by_lifecycle`) holds the rows
of the classification (or state) the spec has now. Without more, the
classification and the lifecycle are the author's fields: reclassifying a
confidential dashboard as public removes its locked confidential row on the
next `standards apply`, and `standards check` reports that change only as a
`standard.content-stale` warning until then.

A standard that must control the classification assigns it, as a scalar slot,
and locks it:

```yaml
# standards/teams/finance-board.yaml
name: finance-board
extends: finance
content:
  classification: confidential
locked:
  content: [classification]
```

`standards apply` writes `dashboard.classification: confidential` and records
it like the colour scheme. Locked, a spec that changes it or removes it fails
`standards check` with `standard.content-locked` on
`dashboard.classification`, and `standards apply --locked` puts it back. The
classification footer rows then follow the standard's value, not the spec's,
so a reclassified dashboard keeps expecting its locked confidential row while
the error stands, and `--locked` restores the classification without removing
that row. A dashboard that needs another classification follows another
standard, or gets a waiver for `dashboard.classification`, under which its rows
follow its own field again. Unlocked, the assignment is a default: written
once, and the author's to change or delete, as any unlocked item. A lower layer
can't change a locked classification, and the value must be in the
`classifications` list.

A standard assigns one classification to every dashboard that follows it,
rather than one per dashboard, because the standards files are the only trusted
record: a per-dashboard value kept in the spec is the author's to edit in the
same pull request (the decision record's #4), and a list of dashboards in a
standards file would be a second assignment mechanism beside
`design.standard`. Dashboards of different sensitivity follow different team
standards, and the exceptions go in the waivers file, which is guarded like the
standards.

Superset has no field to carry them, checked at 4.1.4, 5.0.0 and 6.1.0:

- the dashboard model has no such column (`class Dashboard` in
  `superset/models/dashboard.py`, 4.1.4 and 5.0.0 lines 134-164, 6.1.0 lines
  135-177);
- `published: false` is not a deprecation: it hides the dashboard from
  every list but its owners' and admins' (`DashboardAccessFilter`,
  `superset/dashboards/filters.py:132,155` at all three tags), and with
  `DASHBOARD_RBAC` a dashboard with roles is closed to everyone else
  (`superset/security/manager.py` 4.1.4:2373, 5.0.0:2377, 6.1.0:2704). A
  deprecated dashboard must stay reachable so its readers see the banner
  naming the successor. `published` stays the author's field;
- a key `json_metadata` doesn't declare fails every later save of the
  dashboard's settings on 4.1.4 and 5.0.0 (`validate_json_metadata`,
  `superset/dashboards/schemas.py:107`), as `show_chart_timestamps` does
  (docs/CONTRACTS.md), and dashboard tags need 6.0.

### Waivers

`standards/waivers.yaml` records the dashboards that may deviate from a lock,
each with who approved it, why, and until when. It sits at the top of the
standards folder and is no standard: discovery and validation of standards
files skip it, and a folder holding only a waivers file is no standards
folder.

```yaml
# standards/waivers.yaml
waivers:
  - spec: specs/ops/wallboard.json      # the spec file: what pins the dashboard
    slug: ops-wallboard                # optional: must match too
    rule: layout.footer[acme][0]       # a content item, or a rule id
    owner: "@acme/data-platform"
    reason: The wallboard has no room for the legal row; legal agreed on 2026-09-30.
    expires: 2026-12-31                # valid through this day
  - spec: specs/finance/partner-revenue.json
    rule: dashboard.classification
    owner: Dana Reyes (legal)
    reason: Published to partners under contract C-114 as internal.
    expires: 2027-03-31
    layer: acme                        # only what the acme layer locks
```

| Key | Holds |
|---|---|
| `spec` and/or `slug` | The dashboard: its spec's path relative to the folder that holds the standards folder (preferred), its slug, or both, when both must match. At least one. |
| `rule` | A rule id (through the alias table, as everywhere), whose locked findings on the dashboard it covers; or a content item as `design.standard_written` keys it (`layout.footer[acme][0]`, `dashboard.css[acme]`, `dashboard.classification`), whose `standard.content-locked` finding it covers. `rule: standard.content-locked` covers every locked item. |
| `owner` | Required: who approved the exception, a CODEOWNERS team or a person. |
| `reason` | Required: why this dashboard may deviate. |
| `expires` | Required: a date, `YYYY-MM-DD`, the last day the waiver holds. |
| `layer` | Optional: the waiver covers only what this layer locks, so a waiver approved for a team's lock can't lift the org's. |

A malformed entry, an unknown key, a missing owner, reason or expiry, a rule
that is neither a rule id nor a content item, a layer no standards file names,
`standard.waiver-expired` as the rule, and a second entry for the same
dashboard, rule and layer are errors naming the entry, and fail every run that
reads the folder, as a broken standards file does.

**The trust boundary.** A spec's path belongs to the repository, where
CODEOWNERS and review decide who may move or add a file; its slug is a field
the spec's author edits in the same pull request as anything else. So a waiver
naming `spec` applies only to the file at that path, and with `slug` as well
only while that file keeps the slug: another spec that takes the slug gets
nothing, and its run says so. A slug-only waiver applies to any spec with that
slug; it still works, but every run that knows the spec's path warns that it
matches by slug alone and names the `spec:` line to add, and `--report` lists
a waiver that matched more than one spec under `matched_more_than_once`. The
MCP server sees specs and never paths, so there a waiver can match only by its
slug, and a pinned one that does says the path went unconfirmed. Name the spec.

What a waiver does, while it holds:

- the locked finding it covers leaves `findings` for `waived`: each entry has
  the finding, its rule and where, and the waiver's dashboard, owner, reason,
  expiry, `status` (`active` or `expired`) and `days_left`. `design.ignore`,
  `--ignore` and `design.yaml` still can't silence a lock: the waivers file is
  the only way past one;
- `standards apply` leaves a waived item as the dashboard has it: it adds,
  refreshes and, under `--locked`, rewrites nothing waived, and keeps the
  item's record as it is. `--check` passes it, the entry lists it under
  `waived`, and the summary says "differ under a waiver". A waiver written
  before the first apply keeps the item off the dashboard;
- under a waiver for `dashboard.classification`, the classification footer
  rows follow the dashboard's own classification again.

Expiry, as the decision record's #5 has it:

| Where | An expired waiver |
|---|---|
| `standards check`, `advise` (CLI and MCP) | fails: the finding it covered counts again, and `standard.waiver-expired` names the waiver, its owner and reason. Only for the specs the command was asked to check |
| `standards apply` | still applies, with a warning on the spec's entry |
| `check`, `apply`, `plan` (CLI and MCP) | still applies, with a warning in the advice block and in `apply`'s warnings; a strict design gate doesn't block on it |
| `restore` | never reads the waivers file |

So CI checks the specs a pull request changes with `standards check`, and an
expiry fails only the pull requests that touch that dashboard; a hotfix to
another dashboard, a deploy and a rollback never trip on it. An expired waiver
fails the dashboard's next pull request even when the dashboard conforms
again, so the stale entry is removed from the file. Every `standards check`
also lists, under `waiver_warnings`, each past-dated entry in the file, checked
spec or not, without failing.

`standards check --report` adds a `waivers` block for a scheduled job: every
entry counted (`total`, `active`), the `expired` ones, those `expiring` within
30 days (`--expiring-within DAYS`), those naming no spec the run read
(`unmatched`), and counts `by_owner` and `by_rule`. `--as-of DATE` (on
`standards check`, `standards apply`, `advise`, `check`, `apply`, and `as_of`
on the MCP tools) reads expiry as of that day, so a run repeats exactly.

```yaml
# .github/workflows/standards.yml (sketch)
on:
  pull_request:
  schedule: [{cron: "0 7 * * 1"}]
jobs:
  standards:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: {fetch-depth: 0}
      - run: pip install chartwright
      - if: github.event_name == 'pull_request'
        run: |
          changed=$(git diff --name-only --diff-filter=d origin/${{ github.base_ref }} -- 'specs/*.json')
          [ -z "$changed" ] || chartwright standards check $changed
      - if: github.event_name == 'schedule'
        run: chartwright standards check specs/ --report
```

The file needs the same guard as the standards: a CODEOWNERS line that sends
every change to the platform team, with the code host's required reviews on.

```
# .github/CODEOWNERS
/standards/            @acme/data-platform
/standards/waivers.yaml @acme/data-platform @acme/legal
```

Chartwright can't enforce CODEOWNERS or check that `owner` names someone who
approved the change: it reads the file as committed. Required reviews on the
code host are what make an exception platform-approved.

### Mixed Superset releases

One spec often deploys to instances on different releases, and it carries its
standard's content for all of them. Content a release can't take would fail
every deploy to that release, so `check`, `apply` and `plan` (and the MCP
`check_spec`, `build_dashboard` and `plan_dashboard`) hold it back per
instance instead.

An item is held back from a release when:

- the standards file that contributes it declares a later `min_superset`
  (each file's own content; a file that extends it declares its own); or
- it writes a version-gated field (`chartwright/versions.py`) the release
  can't take: today `theme`, which needs 6.0.0.

The deploying command asks the instance its release when the spec's standard
could hold something (a file with content declares `min_superset`, or the
content sets a gated field). A `--superset-version` stated as well is checked
against the release the instance reports: a different one is refused with
`superset_version_mismatch` before anything is resolved or written, since
holding by a release the instance doesn't run would drop content it takes;
when the instance reports none, the stated release is used and a warning says
it is unconfirmed. It removes each
held item the spec holds as the standard has it from what it sends (rows,
CSS blocks and settings alike), and lists it under `held` with the reason;
compile, the version check and `plan` see the spec without it. The spec on
disk is untouched. A value the author wrote, edited or released is never
held: it meets the version check and is refused there, as any field is. When
the instance doesn't report its release, nothing is held and a warning says
to pass `--superset-version`.

**A floor suspends its file's locks on older instances.** A file's
`min_superset` holds back everything that file contributes, locked content
included: an org file with `min_superset: "7.0"` and a locked legal footer
sends no footer to a 6.1 instance, and nothing there fails for it. That is
what decision #9 asks for, so it is not an error, but it is never silent: the
folder warns when it loads (`floor_suspends_lock`, under `standards_warnings`
in `standards check` and `warnings` in `standards show`), and a deploy that
holds locked content lists it with `locked_by` and a warning that its lock does
not apply there. Content every release must show belongs in a file without
`min_superset`.

`standards check --superset-version 5.0.0` (MCP `standards_check`'s
`superset_version`) shows the same offline: each spec's entry gains
`superset_version` and `held`, and held items are not expected, so a spec
that lacks one isn't reported for it on that release. Their records in
`design.standard_written` are kept as they are, never read as items the
standard dropped. Verified live: a standard's theme was held on 4.1.4 and
5.0.0, the dashboard applied and `plan` was clean, while an author's own
theme was refused.

### A theme

`dashboard.theme` names a Superset theme by the name Superset lists it under
(6.0 or later); a standard sets it with the `theme` slot. `check`, `apply` and
`plan` resolve the name on each instance before anything is written and
compile writes the theme's id into the bundle; decompile reads the name back
from an export, and `plan` reports `theme` when the live dashboard shows
another one. Left out, apply doesn't touch the theme and `plan` doesn't
compare it, so a theme chosen in the UI stays. docs/CONTRACTS.md, "Dashboard
theme", has the Superset source for each step. A theme carries palette and
fonts as Superset's own tokens; CSS stays the fallback for releases before 6.0
and for what tokens don't cover, as the decision record's #6 has it.

### Checking what readers see

`standard.css-hides` reads declarations and knows a list of ways to hide an
element. The real lock on what readers see is a check of the deployed
dashboard (the decision record's #6):

```
chartwright standards verify-visible specs/ops/delays.json --profile prod \
    [--standards DIR] [--superset-version R] [--as-of DATE] \
    [--min-contrast 2.0] [--timeout 60] [--screenshot delays.png]
```

It needs a browser, an optional extra never installed with chartwright
itself: `pip install 'chartwright[visual]'`, then `playwright install
chromium`. Without it the command exits 1 with `visual_extra_missing` and the
two commands to run.

What it looks for: each locked header and footer row of the spec's standard
that the spec holds as the standard has it, one target per rendered line
(markdown blocks by line, a header row by its text). A locked row the spec
lacks or changed is listed under `skipped` (`standards check` reports it),
and so is a row a waiver covers or the instance's release holds back.

How: it signs in on Superset's own sign-in page with the profile's username
and password (a Preset API token opens no browser session, and is refused),
opens the dashboard in headless Chromium at 1600×1200, scrolls it so rows
that render on scroll are drawn, and runs one script in the page. For each
target the script finds the deepest element whose text holds it (markdown
marks and case set aside), preferring a copy that renders, scrolls the
window (never the element's containers) to it, and reads the box of its text
(not of the element, which stays in place when `text-indent` or padding pushes
the glyphs out), its rendered text, its own and its containers' computed
styles, and the element at the centre of its visible part. Python then judges each one. A line is hidden
when it is:

- not on the page, or not rendered (`display: none` or `content-visibility`
  on it or a container, or its rendered text lacks it);
- `visibility: hidden`;
- without size, or off the page: right of the window's width, left of it, or
  outside the document;
- cut off by containers that clip their overflow, with less than half of it
  showing;
- transparent: the product of the opacities, `filter: opacity()` included,
  below 0.1; or clipped by `clip-path`, `clip` or `filter: brightness(0)`;
- smaller than 6 px as drawn: the computed size times any `scale()` down its
  containers;
- blurred: `filter: blur()` down its containers adding up to more than 2 px;
- of a colour whose WCAG contrast with the first opaque background behind it
  is below `--min-contrast` (2.0 by default; white on white is 1.0, `#ccc` on
  white 1.6), the text colour's alpha composited first;
- covered: another element is on top of its centre, or an opaque, positioned
  `::before` or `::after` sits on it or a container.

It prints the `items` (each with its text, `visible`, the `reasons` and the
measured `contrast`), `skipped` and `hidden`, and exits 1 when any line is
hidden. A browser problem is a typed error, never a traceback:
`visible_timeout` when the dashboard doesn't load within `--timeout`,
`visible_tls` when the browser doesn't trust the certificate, `visible_login`,
and `visible_browser` for anything else.

Chromium can't be handed a CA bundle file; it trusts the operating system's
certificates, where a corporate CA is normally installed. So a profile with
`ca_bundle` keeps certificate checks on, against the system's certificates,
and the payload's `tls` says so; only `verify = false` turns the checks off,
and `tls` says that too.

Verified live on 4.1.4, 5.0.0 and 6.1.0 with ten author stylesheets hiding
the locked footer: `display: none`, `visibility: hidden`, near-white text,
dark text on a dark band, `filter: opacity(0)`, a white `::after` overlay, a
shift 4000 px to the right, `text-indent: -9999px` with hidden overflow,
`filter: blur(4px)` and `transform: scale(0.05)`. It caught all ten on every
release, and the unchanged dashboard passed; `standard.css-hides` warns on the
first two only. What the browser measured for each is kept in
`tests/fixtures/visible/measurements.json`
(`tools/record_visible_measurements.py` refreshes it), and the offline suite
judges those measurements, so a change to the verdict is caught without a
browser.

Limits, each a way a line could be hidden and still pass:

- a background image or gradient behind the text leaves its contrast
  unmeasured;
- a pseudo-element overlay is judged by its style alone (opaque, positioned),
  since the browser reports no box for it; one beside the text, not over it,
  is reported anyway;
- part of a line hidden by its own child (`strong { color: white }`) is judged
  by the line's element;
- a text matched in two places counts as visible when either copy is, which
  still means a reader sees it;
- the check opens the dashboard's first tab; header and footer rows are
  outside tabs, so they show on every tab;
- it reads one moment after the dashboard settles; a rule that hides text
  later, on hover or by script, isn't seen.

The live CI job runs it on the three releases (`tools/ci_live_visible.py`).
Installing the browser is the optional step, since the browser is an optional
extra: when the install fails the check is skipped, and once it succeeds a
failing check fails the job. Elsewhere it is a post-deploy step you add after
`apply`.
