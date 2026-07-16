# The Design Brain

> **Status: SHIPPED (0.2.0) — all three phases.** This page is both the
> design and the reference for the implementation in `chartwright/design/`.
> The decision log at the bottom records every judgment call made without a
> review gate; §15 records where the implementation deliberately deviates
> from the original design text. Rendering-quality verification against a
> live Superset (UI eyeballing of advised-vs-unadvised dashboards) is still
> pending — the rulebook's thresholds come from the skill's field notes and
> BI literature, not yet from side-by-side screenshots.

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
  __init__.py      advise() entry point
  model.py         Finding, AdviceReport, RuleContext, @rule registry
  rules.py         all Tier L rule implementations (split when it outgrows one file)
  presets.py       audience parameter tables
  fix.py           apply_fixes(spec_data, findings) -> (new_data, applied)
  brief.py         render_brief(audience) -> markdown
  guidelines/      rule cards (packaged data, shipped in the wheel)
```

## 4. CLI surface

```
chartwright advise <spec> [--audience A] [--profile P] [--fix] [--strict]
                          [--ignore rule1,rule2] [--no-probe]
chartwright brief [--audience A]
chartwright redesign <slug-or-id> --profile P [-o spec.json] [--audience A] [--no-probe]
```

- `advise` (offline by default): evaluates the spec, prints an
  `AdviceReport` JSON (shape in §10). Exit 0 unless a finding of severity
  `error` exists, or `--strict` and any `warn` exists.
- `advise --profile P`: adds **data-aware** rules — column type checks and
  bounded cardinality probes against the live instance (§8). `--no-probe`
  keeps it to metadata already fetched by resolution (no queries).
- `advise --fix`: applies the safe-fix subset in place (same file-rewrite
  mechanics as `absorb`), re-validates, reports what changed. Idempotent: a
  second `--fix` run is a no-op.
- `brief`: prints the Tier G design brief for the audience — the document the
  skill reads before authoring. Compact by contract (≤ ~150 lines), because
  it lands in an LLM context window.
- `check`/`apply` gain `--design off|warn|strict` (default `warn`):
  - `warn`: advice rides along in the payload under `"advice"`, never blocks.
  - `strict`: `error`/`warn` findings block before import (same pre-flight
    position as referential resolution).
  - `off`: byte-identical to today, advice machinery never runs.
- MCP server: two new tools, `advise` and `brief`, thin wrappers as usual.

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
- `ignore`: rule ids to suppress, dashboard-wide (`rule.id`) or per chart
  (`rule.id@Chart Name`). Suppressions are reported in every AdviceReport
  (`"ignored"`), so silence is always visible.

Precedence everywhere: CLI flag > spec `design` block > built-in default.

## 6. Audiences

A professional designer designs for a reader. Rules read their thresholds
from an audience preset, so one rulebook serves three very different
dashboards. Heights are in spec units (1 unit = 40 px; a laptop viewport
minus Superset chrome is ≈ 22 units).

| Parameter | `executive` | `analytical` (default) | `operational` |
|---|---|---|---|
| Intent | one screen, few numbers, big | scrolling analysis, depth | dense wall/monitor view |
| `fold_units` (height budget, per tab) | 22 | 66 | 26 |
| `max_row_charts` (axis charts per row) | 3 | 4 | 5 |
| `kpi_per_row` (min–max) | 3–5 | 2–6 | 2–8 |
| `kpi_height` | 5 | 4 | 3 |
| `min_axis_height` | 8 | 6 | 5 |
| `table_visible_ratio` (min visible/row_limit) | 0.5 | 0.25 | 0.25 |
| `vbar_max_categories` | 6 | 8 | 8 |
| `pie_max_slices` | 5 | 7 | 7 |
| `series_max` (lines per timeseries) | 5 | 10 | 8 |

Presets are data (`presets.py`), not branches: rules never test the audience
name, only parameters. Adding an audience is adding a row.

## 7. The rulebook, v1

Stable ids (`category.slug`) are the public API — `ignore` lists key on
them, so renames require aliases. Severity: **error** = unreadable for any
audience; **warn** = below professional quality; **info** = polish nudge.
"Fix" marks the safe-autofix subset (presentation-only, §9). ⚡ marks
data-aware rules (only run with `--profile`, §8).

### size — minimum readable geometry

| id | sev | fix | rule |
|---|---|---|---|
| `size.axis-min-height` | warn | ✔ | timeseries/bar/heatmap/histogram below `min_axis_height` renders flattened, axis labels dropped → raise to 8 |
| `size.kpi-height` | warn | ✔ | big_number outside 2–6 units (starved or wasteful) → `kpi_height` |
| `size.pie-geometry` | warn | ✔h | pie/donut under 5/12 width or 8 height: ring shrinks, legend crowds → fix height; width fixable in rows mode, report-only under a sketch (finding includes a redrawn sketch suggestion) |
| `size.heatmap-geometry` | warn | ✔h | heatmap under 5/12 width (⚡ under 7/12 when x-cardinality > 12) or 6 height |
| `size.table-window` | warn | — | table height shows < `table_visible_ratio` of `row_limit` (~0.8 units/row + header): a 1,000-row list behind a 6-row window → raise height or lower row_limit (data-affecting: suggestion only) |
| `size.hbar-window` | warn | ✔ | horizontal bar needs ≈ `row_limit × 0.5 + 2` units of height; fix height up to the fold budget, otherwise suggest a lower row_limit |
| `size.row-harmony` | warn | ✔ | rows-mode heights differ within a row: Superset sizes the row to its tallest child, the rest get a ragged hole → equalize to the row max |

### layout — composition

| id | sev | fix | rule |
|---|---|---|---|
| `layout.kpi-first` | warn | — | big_numbers exist but appear after the first non-KPI row (summary precedes detail — inverted pyramid) |
| `layout.kpi-band` | warn | — | a KPI row outside `kpi_per_row` (one lonely KPI looks unfinished; seven read as noise) |
| `layout.row-density` | warn/error | — | more than `max_row_charts` axis charts in a row; **error** when any lands under 3/12 wide |
| `layout.row-fill` | warn | — | rows-mode widths sum well short of 12 with no markdown filler (lopsided band; sketches express this deliberately with `.`) |
| `layout.fold-budget` | warn | — | dashboard (or tab) height exceeds `fold_units` → suggest tabs or pruning; the finding names the row where the budget runs out |
| `layout.tab-balance` | info | — | tab chart counts skew worse than 4:1 → rebalance or inline the thin tab |
| `layout.orphan-chart` | info | — | a lone chart under 8/12 in its own row → widen to 12 or pair it |
| `layout.section-headers` | info | — | more than 8 charts and no markdown headers → readers need signposts |

### chart — encoding choice

| id | sev | fix | rule |
|---|---|---|---|
| `chart.vbar-categories` | warn | ✔o | vertical bar with `row_limit` absent (default 10,000) or > `vbar_max_categories`: Superset drops labels past ~8 → fix flips `orientation` to horizontal (presentation-safe); detail suggests `row_limit` ≈ 10 |
| `chart.pie-slices` | warn | — | pie `row_limit` absent (default 100) or > `pie_max_slices` (⚡ true cardinality when available) → cap slices or switch to horizontal bar |
| `chart.series-limit` | warn ⚡ | — | line/area/scatter `groupby` cardinality > `series_max` → spaghetti; suggest a top-N filter or a coarser dimension |
| `chart.metrics-per-bar` | warn | — | bar with ≥ 4 metrics → grouped bars become unreadable; a table/pivot answers it better |
| `chart.temporal-type` | error ⚡ | — | `time_column` resolves to a non-temporal column → chart renders broken or empty |
| `chart.histogram-bins` | info | — | bins outside 10–50 |
| `chart.treemap-depth` | warn | — | treemap `groupby` deeper than 2 levels → unreadable nesting |
| `chart.funnel-stages` | warn ⚡ | — | funnel stage cardinality outside 3–8 |
| `chart.heatmap-grid` | warn ⚡ | — | x-cardinality × y-cardinality > 400 cells → unreadable at any size |
| `chart.dupe` | info | — | two charts share dataset + type + metrics + dimensions → redundancy |

### data — query intent

| id | sev | fix | rule |
|---|---|---|---|
| `data.row-limit-intent` | info | — | bar/pie/table riding the large built-in default row_limit where the limit is doing design work → set it deliberately |
| `data.grain-vs-range` | warn | — | chart-level `time_range` and `time_grain` that yield ≲ 2 points (e.g. "Last week" at P1M) or ≳ 1,000 points, when both are literal enough to compute offline |

### narrative & filters — polish

| id | sev | fix | rule |
|---|---|---|---|
| `narrative.title-style` | info | — | chart-name casing is inconsistent across the dashboard. **Never autofixed**: names seed chart uuids; renames churn chart identity (§11) |
| `narrative.big-number-format` | info | — | big_number without `number_format` → raw float precision on a hero number; suggest `,.0f` / `.3s` |
| `narrative.filtered-title` | info | — | chart carries WHERE filters but the title mentions none of the filter values ("the chart says what it shows") |
| `filters.time-picker` | info | — | timeseries charts present but no `time_range` filter in the native bar |

Anything fuzzier than this (reading order beyond KPI-first, grouping related
metrics, matched granularity across a row, insight-stating titles) is Tier G:
it goes in the brief, not the linter. The rulebook version (`design_brain:
"1"`) is reported in every AdviceReport; rules carry a `since` marker.

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
  "design_brain": "1",
  "audience": "analytical",
  "counts": {"error": 0, "warn": 2, "info": 1},
  "findings": [
    {
      "rule": "size.pie-geometry",
      "severity": "warn",
      "chart": "Sales by Region",
      "where": "layout row 2",
      "detail": "pie at 3/12 x 4 units: ring shrinks and legend crowds; needs >= 5/12 x 8",
      "fixable": true
    }
  ],
  "fixed": [],
  "ignored": ["layout.fold-budget"]
}
```

`ok` is false only at gate level (any `error`, or `warn` under `--strict`).
Under `apply --design warn`, this object is embedded in the apply report as
`"advice"` and never affects `apply`'s own `ok`.

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

- **Per rule, table-driven:** a clean spec stays silent; a violating spec
  fires exactly the expected finding; the autofixed spec re-advises clean.
- **Property tests** (existing hypothesis setup): advise never raises on any
  valid spec; `--fix` output always re-validates; fix is idempotent.
- **Golden:** AdviceReport JSON for `examples/nyc_taxi_operations.json`,
  which must pass `analytical` clean — the example is the dogfood.
- **Brief budget test:** rendered brief stays ≤ 150 lines per audience.

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
