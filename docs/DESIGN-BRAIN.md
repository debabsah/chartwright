# The Design Brain

> **Status: SHIPPED — design brain 3.** This page is both the design and the
> reference for the implementation in `chartwright/design/`. The decision log
> at the bottom records every judgment call made without a review gate; §15
> records where the implementation deliberately deviates from the design
> text. A verified multi-lens review of the first implementation produced the
> ranked roadmap in [DESIGN-BRAIN-V2.md](DESIGN-BRAIN-V2.md) (36 of 37 items
> in the v2 batch, the last closed afterwards — see that page's status note);
> a later full review of core + brain produced the version-3 changes recorded
> in §15.11 onward. §7's rule table is now genuinely generated
> (`tools/gen_rule_table.py`, checked by `tests/test_docs.py`) — the v2 claim
> that it was pointed at a placeholder snippet, and the table had drifted.
>
> **Still pending: rendering-quality verification against a live Superset.**
> The thresholds come from the skill's field notes and BI literature, not
> from measured pixels, and `docs/CONTRACTS.md` carries no design entries
> while citing every other Superset behaviour to source. Treat the numbers as
> informed judgement until a visual harness lands.

## 1. Problem

Chartwright guarantees a dashboard **imports correctly**. It does not
guarantee the dashboard **reads well**. Nothing stops a spec from shipping a
pie chart squeezed into 2 of 12 columns, a vertical bar with 40 category
labels (Superset silently drops most of them), a KPI buried under three rows
of tables, or a 4,000-pixel scroll for an executive audience. Today the only
design knowledge in the system is a prose section in `skill/SKILL.md` —
frozen inside one prompt, invisible to the CLI, not versioned as a surface,
not toggleable, and not testable.

The design brain is that missing layer: a codified, continuously-improvable
body of BI/UX design knowledge (Few, Tufte, IBCS, and Superset-specific
rendering facts) that the toolchain can consult, enforce, and explain — and
that the user can switch off.

## 2. Principles

1. **Same trust model as the rest of the tool.** The LLM stays the untrusted
   parser at the edge. Design judgment that requires intelligence is
   delivered *to* the LLM as versioned knowledge; design judgment that can be
   mechanically checked is enforced *after* the LLM in tested code. Nothing
   about the brain loosens the bright line (the spec remains the only
   LLM-authored artifact).
2. **Advice, not authority.** The brain never silently changes what data a
   chart shows. Autofixes are presentation-only (geometry, orientation).
   Anything that would change the data shown (row limits, filters, chart
   type) is a finding with a suggested edit, never an automatic one.
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

- **Tier G — the brief.** Composition, grouping, narrative flow, when to use
  tabs, title-as-insight, color restraint: judgments only an intelligence can
  make. Delivered as a compact markdown brief the skill loads before writing
  a spec. Continuously improvable by editing guideline files; no code change.
- **Tier L — the critic.** Predicates over the spec (optionally enriched with
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
  presets.py       audience parameter tables + design.yaml overlay
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
                          [--ignore rule1,rule2] [--no-probe]
chartwright brief [--audience A]
chartwright redesign <slug-or-id> --profile P [-o spec.json] [--audience A] [--no-probe]
chartwright calibrate [--write] [--min-samples N] [--since 90d]
```

- `advise` (offline by default): evaluates the spec, prints an
  `AdviceReport` JSON (shape in §10). Exit 0 unless a finding of severity
  `error` exists, or `--strict` and any `warn` exists.
- `advise --profile P`: adds **data-aware** rules — column type checks and
  bounded cardinality probes against the live instance (§8). `--no-probe`
  keeps it to metadata already fetched by resolution (no queries).
- `advise --fix`: applies the safe-fix subset in place (same file-rewrite
  mechanics as `absorb`; formatting normalizes), re-validates, and reports
  each change as `{rule, chart, set: {field: new}, was: {field: old}}` plus
  the `written` path. Idempotent: a second `--fix` run is a no-op.
- `brief`: prints the Tier G design brief for the audience — the document the
  skill reads before authoring. Compact by contract (a test caps the line
  count), because it lands in an LLM context window.
- `check`/`apply` gain `--design off|warn|strict` (default `warn`):
  - `warn`: advice rides along in the payload under `"advice"`, never blocks.
  - `strict`: `error`/`warn` findings block (a `design_gate` entry lands in
    `errors` and the exit code is 1); apply blocks BEFORE anything on the
    instance is touched.
  - `off`: byte-identical to the pre-brain behavior, advice machinery never
    runs.
- `calibrate`: the learning loop (§13 phase 3) — mines absorb history into
  per-audience recommended heights; `--since` is the decay knob.
- MCP server: `design_brief`, `advise_spec`, `fix_spec`, `redesign_dashboard`
  mirror the CLI verbs; `check_spec` carries the advice block.

## 5. Spec surface

One additive optional block (models stay `extra="forbid"`):

```json
"design": {
  "audience": "executive",
  "ignore": ["size.pie-geometry", "layout.fold-budget@Ops Detail"]
}
```

- `audience`: this dashboard's preset; CLI `--audience` overrides it, the
  built-in default (`analytical`) applies when both are absent.
- `ignore`: rule ids to suppress, dashboard-wide (`rule.id`), per chart
  (`rule.id@Chart Name`), or per band for findings that name no chart
  (`rule.id@tab-Ops-row-1` — the finding's `where`, slugified). Suppressions
  are reported in every AdviceReport (`"ignored"`), so silence is always
  visible; entries whose rule id doesn't exist come back as
  `unmatched_ignores` instead of silently suppressing nothing.

Precedence everywhere: CLI flag > spec `design` block > built-in default.

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

Presets are data (`presets.py`), not branches: rules never test the audience
name, only parameters. Adding an audience is adding a row.

### House style: design.yaml

`~/.config/chartwright/design.yaml` (or `$CHARTWRIGHT_DESIGN_DIR/design.yaml`)
overlays the presets for a whole deployment — no fork of the rulebook. Five
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

## 7. The rulebook

Stable ids (`category.slug`) are the public API — `ignore`/`disable` lists
and severity overrides key on them, and renames keep working through the
alias table. Severity: **error** = unreadable for any audience; **warn** =
below professional quality; **info** = polish nudge. The overlay can override
it per deployment. A `sev` of `warn/error` means the rule's DEFAULT is `warn`
but it escalates per finding — worth reading closely, because `ok` is
error-driven, so those rules can fail a run while advertising `warn`.
"fix" marks the safe-autofix subset (presentation-only, §9).
"data" marks rules that only run with a live resolution (`--profile`);
several offline rules additionally sharpen or stand down when probes are
available (noted in their text). `since` is the design-brain version that
introduced the rule: "2" the post-review batch
(docs/DESIGN-BRAIN-V2.md), "3" the review burn-down (§15.11 onward).

The table below is GENERATED from the registry by
`tools/gen_rule_table.py --write`; do not hand-edit it. `tests/test_docs.py`
fails when it drifts.

<!-- BEGIN rule-table: generated by tools/gen_rule_table.py; do not hand-edit -->

| id | sev | fix | data | since | rule |
|---|---|---|---|---|---|
| `chart.dupe` | info | — | — | 1 | two charts answering the identical question is redundancy |
| `chart.format-bands` | warn/info | — | — | 2 | conditional-formatting bands must tell one coherent story per metric |
| `chart.funnel-stages` | warn | — | ⚡ | 1 | funnels need 3-8 ordered stages |
| `chart.heatmap-grid` | warn | — | ⚡ | 1 | a heatmap past ~400 cells is unreadable at any size |
| `chart.histogram-bins` | info | — | — | 1 | histograms read best at 10-50 bins |
| `chart.metrics-per-bar` | warn | — | — | 1 | many metrics per category read better as a table |
| `chart.ordinal-order` | info | — | — | 2 | ordinal dimensions (weekday, month) sort alphabetically unless order-encoded |
| `chart.pie-slices` | warn | — | — | 1 | pies stop working past ~7 slices |
| `chart.pivot-columns` | warn | — | ⚡ | 2 | column-dim values x metrics = rendered columns; past ~15 the pivot scrolls sideways |
| `chart.pivot-dims` | warn | — | — | 2 | a pivot past three total dimensions is unreadable nesting |
| `chart.series-limit` | warn | — | ⚡ | 1 | a timeseries with too many grouped series turns to spaghetti |
| `chart.temporal-type` | error | — | ⚡ | 1 | a time axis must point at a temporal column |
| `chart.treemap-depth` | warn | — | — | 1 | treemaps past two grouping levels become unreadable nesting |
| `chart.treemap-vs-bar` | info | — | ⚡ | 2 | a one-level treemap of few categories is a worse bar chart |
| `chart.trend-grain` | info | — | — | 2 | trend tiles at a fine grain over full history draw thousands of points in a small card |
| `chart.vbar-categories` | warn | ✔ | — | 1 | vertical bars drop labels past ~8 categories; rank with horizontal bars |
| `data.grain-vs-range` | warn | — | — | 1 | the time grain should yield a sane number of points for the range |
| `data.row-limit-intent` | info | — | — | 1 | row limits doing design work should be deliberate, not defaults |
| `data.top-n-sort` | warn | — | — | 2 | a limit without an order is a sample, not a ranking |
| `data.unwindowed-history` | warn | — | — | 3 | timeseries charts with no way to bound the window draw ALL history at their grain |
| `filters.count` | warn | — | — | 2 | past ~6 select pickers a filter bar stops being navigable (and each costs a query on load) |
| `filters.duplicate-column` | info | — | — | 2 | two filters on the same column fight each other |
| `filters.range-default` | info | — | — | 2 | a range slider with no default bounds spans the whole domain |
| `filters.select-cardinality` | warn | — | ⚡ | 2 | a select over hundreds of distinct values is an unusable picker |
| `filters.time-default` | info | — | — | 2 | an undefaulted time picker loads the dashboard over ALL history |
| `filters.time-picker` | info | — | — | 1 | time-based dashboards want a time range picker in the filter bar |
| `layout.fold-budget` | warn | — | — | 1 | the dashboard should fit its audience's scroll budget |
| `layout.kpi-band` | warn | — | — | 1 | KPIs get their own band, in readable numbers |
| `layout.kpi-first` | warn | — | — | 1 | summary KPIs belong above detail charts (inverted pyramid) |
| `layout.markdown-height` | info | ✔ | — | 2 | a one-line markdown header doesn't need a chart-sized block |
| `layout.orphan-chart` | info | — | — | 1 | a lone narrow chart in its own row looks unfinished |
| `layout.row-density` | warn/error | — | — | 1 | too many axis charts side by side starves each of width |
| `layout.row-fill` | warn/info | — | — | 1 | a row should fill the 12-column grid |
| `layout.section-headers` | info | — | — | 1 | large flat dashboards need markdown signposts |
| `layout.tab-balance` | info | — | — | 1 | tabs should carry comparable weight |
| `narrative.big-number-format` | info | — | — | 1 | hero numbers deserve a number format |
| `narrative.filtered-title` | info | — | — | 1 | a filtered chart's title should say what it shows |
| `narrative.format-consistency` | info | — | — | 2 | one measure, one number format |
| `narrative.title-style` | info | — | — | 1 | chart titles should share one casing style |
| `size.axis-min-height` | warn | ✔ | — | 1 | axis charts below the audience minimum height flatten and drop labels |
| `size.grid-fit` | warn | ✔ | ⚡ | 2 | table/pivot heights must fit their data-driven row counts (they grow after authoring) |
| `size.hbar-window` | warn | ✔ | — | 1 | horizontal bars need ~0.5 units of height per bar |
| `size.heatmap-geometry` | warn | ✔ | — | 1 | heatmaps need >= 5/12 width (7/12 with many columns) and 6 height |
| `size.kpi-height` | warn | ✔ | — | 1 | big numbers read best at 2-6 units |
| `size.min-width` | warn/error | — | — | 2 | below 3/12 width a chart is unreadable; KPIs need 2/12 |
| `size.pie-geometry` | warn | ✔ | — | 1 | pies need >= 5/12 width and 8 height or the ring shrinks and the legend crowds |
| `size.pivot-window` | warn | — | — | 2 | a pivot's height should show a meaningful share of its row_limit |
| `size.row-harmony` | warn | ✔ | — | 1 | charts sharing a row should share a height (Superset sizes the row to its tallest child) |
| `size.table-window` | warn | — | — | 1 | a table's height should show a meaningful share of its row_limit |

<!-- END rule-table -->

Anything fuzzier than this (reading order beyond KPI-first, grouping
related metrics, matched granularity across a row, insight-stating titles)
is Tier G: it goes in the brief, not the linter.

## 8. Data-aware mode

Resolution already fetches full column metadata (`dataset_detail`) and
discards everything but names. Phase 2 extends `ResolvedDataset` with
`column_types` (Superset's `type_generic`) and the `is_dttm` set — zero extra
API calls — which powers `chart.temporal-type`.

Cardinality (`chart.pie-slices` ⚡, `chart.series-limit`, `chart.funnel-stages`,
`chart.heatmap-grid`) uses one bounded probe per distinct (dataset, column):
a `COUNT` grouped query through `/api/v1/chart/data` (the smoke module's
existing machinery) with `row_limit = threshold + 1` — the rule only needs
"more than N", never the true count. Probes are cached per run, skipped
entirely under `--no-probe`, and never run for `advise` without `--profile`.

## 9. Autofix semantics

- **Safe set only:** heights, widths (rows mode), bar orientation. All
  presentation; a fixed spec queries identically to the unfixed one.
- Mechanics mirror `absorb`: findings carry a patch
  (`{"chart": "Top Products", "set": {"height": 8}}`), `fix.py` applies them
  to the raw spec JSON, the result is re-validated before writing, and the
  report lists every applied fix as `rule@chart`.
- **Idempotent by test:** fixed specs re-advise with zero fixable findings.
- Sketch layouts: heights are fixable (an explicit `chart.height` overrides
  sketch height by existing precedence in `spec.resolved_height`); widths are
  not (the drawing is authoritative) — width findings under a sketch include
  a suggested redrawn sketch line for the LLM or human to adopt.
- Fractional heights (absorb's signature) suppress sizing rules on that
  chart: human polish wins (§2.4).

## 10. Output contract

`advise` speaks the same JSON dialect as every other stage:

```json
{
  "stage": "design",
  "ok": true,
  "design_brain": "2",
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
     "chart": "Sales by Region", "set": {"height": 8}, "was": {"height": 4}}
  ],
  "ignored": ["layout.fold-budget"],
  "unmatched_ignores": ["size.pie-geometri"],
  "polished": ["size.axis-min-height@Weekly Orders"]
}
```

- `ok` is false iff a finding of severity `error` exists. The `--strict` gate
  (and `--design strict` on check/apply) rides the exit code and appends a
  `design_gate` entry to `errors`; it does not redefine `ok`'s meaning.
- `fixed` entries disclose the full diff (`set` new values, `was` old);
  `advise --fix` additionally reports the `written` file path.
- `unmatched_ignores` lists ignore/disable entries whose rule id doesn't
  exist — a typo'd suppression is surfaced, never a silent no-op.
- `polished` lists sizing findings withheld because the chart carries a
  human-polished (fractional) height — §2.4's deference, made visible.
  `ignored` is the user's *explicit* intent; `polished` is the brain's own
  *inference*, and an inference that silences a rule invisibly reads exactly
  like the rule having passed. Present only when non-empty.
- Under `apply --design warn`, this object is embedded in the apply report
  as `"advice"` and never affects `apply`'s own `ok`. Under
  `--design strict` the gate fails CLOSED: if advice could not be evaluated
  at all (a broken `design.yaml`), that blocks too, rather than reporting
  counts of zero and passing.

## 11. Interactions with the existing system

- **`absorb`:** fractional heights silence sizing rules (§9). The two flows
  compose: brain roughs in professional geometry, human drags to taste,
  absorb records it, brain respects it forever after.
- **`decompile`:** running `advise` on a decompiled spec is a **design audit
  of any legacy UI-built dashboard** — an emergent feature worth documenting:
  `chartwright decompile old-dash -o spec.json && chartwright advise spec.json`.
- **`redesign`:** the one-shot form of the above: decompile → data-aware
  audit → safe geometry fixes → redesigned spec + losses + remaining
  structural findings. Ownership decides where it lands: tool-born
  dashboards redesign in place; UI-born ones come back under a `-redesign`
  slug (title suffixed too) so apply builds the redesign **side by side**
  and the original is never overwritten. Structural findings stay findings —
  the spec author (usually the skill-driven LLM) acts on them before apply.
- **`smoke` (issue #1):** table/pivot heights are fixed layout properties
  while rendered rows are data-driven — data that grows after authoring hides
  new rows behind the chart's inner scrollbar with everything looking green.
  Smoke now compares the rows its query already fetched against the
  configured height and warns on every apply (`~9 leaf rows (~430px) but
  height=8 (320px)`); the data-aware `size.grid-fit` rule catches the same
  class pre-apply for single-dimension grids. All of it — smoke,
  `size.grid-fit`, `size.table-window`, `size.pivot-window` — reads ONE grid
  model (`grid_units_for_rows` / `grid_rows_visible` in `chartwright/spec.py`),
  so the offline critic, the data-aware critic, and the apply-time warning
  cannot give one chart three different verdicts.
- **Chart identity:** no rule may ever autofix a chart `name` — names seed
  uuid5 identity; a rename is a delete+create on the live instance.
- **`plan`/golden tests:** advise is pure spec-side analysis; compiled bytes
  are untouched, golden tests unaffected.
- **Skill (`skill/SKILL.md`):** the static "Design rules" section is replaced
  by two procedure steps:
  - *Step 1.5* — brain on (default): run `CW brief --audience <inferred>`
    and follow it while authoring. Infer audience from the request
    ("executive scorecard" / "ops monitor" / default analytical). Brain off
    (user said "no design opinions" / "exactly as I specify"): skip the
    brief, pass `--design off`.
  - *Step 4.5* — after `check` passes: `CW advise <spec> --profile <p>`;
    apply or consciously `ignore` findings (with the user, in the spec's
    `design.ignore`) — at most 2 design iterations, then surface remaining
    findings verbatim.
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
  `analytical` (its two deliberate exceptions recorded in `design.ignore`).
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
| **3** | calibration loop: mine absorb history and backups for systematic human corrections (e.g. tables consistently dragged from 8 to 11 units) and propose preset updates — the brain learns from every human polish it was told to respect |

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
   audience), not a blanket 8 — a dense operational board shouldn't be
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
   `severity` map rather than a boolean param.
9. **Sketch WYSIWYG resolved as disclosure, not withholding**: height fixes
   still apply to sketch-drawn charts (explicit heights legitimately override
   the drawing — absorb's precedent), and the finding says the drawing goes
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
    `chartwright/spec.py`; the rules and smoke import them.
12. **`table_visible_ratio` is 0.5 for every audience**, up from 0.25 on
    `analytical`/`operational`. Below half, the MAJORITY of the rows the
    author deliberately asked for sit behind the inner scrollbar — the exact
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
    verbs as the defect; it is not — they are different payloads with
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
    silent when a time_range filter exists without a default —
    `filters.time-default` already names that one-line fix, and double-
    reporting one remedy at two severities is noise. Deployments that want
    that case to bite raise it via the overlay `severity` map (§15.8).
19. **`decompile` says when its dataset index is truncated.** The lookup
    stops at a page cap; past it, a real dataset became "uuid not resolvable"
    and its chart was dropped — a wrong answer wearing the costume of an
    honest loss, which is the one failure this decompiler must never produce.
