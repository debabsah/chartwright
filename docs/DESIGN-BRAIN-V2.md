# Design Brain v2: Review Findings and Roadmap

> **Status: EXECUTED.** All 37 roadmap items below landed in the four
> `design brain v2 batch A-D` commits (design_brain version "2"); §15 of
> [DESIGN-BRAIN.md](DESIGN-BRAIN.md) records the few places execution
> deliberately deviated from a proposal. This page remains the review
> record.
>
> Originally produced 2026-07-16 by a
> multi-lens agent review of the shipped design brain (docs/DESIGN-BRAIN.md).
> Method: seven independent review lenses (core machinery, per-rule logic,
> adversarial spec fuzzing, BI/UX domain expertise, tool ergonomics,
> architecture/learning-loop, doc truth), each empowered to execute the code
> against scratch specs. Every bug, edge-case, and doc-drift claim was then
> independently re-verified by an adversarial agent instructed to refute it.
> **80 findings survived verification, 0 were refuted**
> (17 bugs, 15 edge cases, 20 gaps, 14 doc drifts,
> 10 improvements, 4 missed opportunities), deduplicated and
> ranked into the 37 items below. Raw findings: work-specs/design-brain-v2-review-raw.json (local, untracked).

## Themes

1. Inferred signals lie: provenance must be explicit — the fractional-height 'human polish' heuristic, ok:true under strict gates, and silently inert ignore entries all make the brain's own output or the user's explicit intent invisible; state should be tracked, not guessed from side effects.
2. Config is a trust boundary: design.yaml, design.ignore, and MCP arguments are user/org-edited inputs that currently crash, no-op, or corrupt specs silently — validate values (not just key names) once at load with typed errors.
3. Sketch normalization loses structure: flattening vertical stacks into flat chart lists makes count-based rules (row-density, kpi-band) condemn the exact layouts the sketch grammar exists to express; rules must reason over horizontal slots and items.
4. Preach it, lint it: the brief and guidelines state thresholds (3/12 width, ~30 bars, format consistency, pie integrity) the rulebook never enforces — and docs claim tests, aliases, and behaviors that don't exist; every promise needs either machinery or a correction.
5. Blind spots in the chart/filter taxonomy: pivot_table, range filters, sort order, and color have zero rules, and nothing guards exhaustiveness when a 15th chart type arrives — coverage gaps should fail CI, not fall through silently.
6. Close the learning loop honestly: absorb -> calibrate -> fix must not forge human signatures, must report what it observed, and needs audience granularity and decay so learned heights improve dashboards instead of blinding or misleading the rulebook.

## Roadmap

Ordered by the review's prioritization: confirmed bugs and false-positive
risks first, then the design-knowledge gaps that most improve real
dashboards, then architecture and polish. Effort: S < 1h single-file,
M multi-file, L new subsystem.

### 1. Fix `chartwright absorb` NameError (missing `import json`) that crashes after mutating the spec file

`bug` · impact **high** · effort **S**

Confirmed: every real absorb run rewrites the spec and appends the calibration log, THEN crashes in AbsorbReport.to_json with code 'unexpected'. The tool mutates user files while reporting failure, and the entire phase-3 learning loop looks broken. One-line fix, zero risk.

**Proposal:** Add `import json` to absorb.py. Add one test asserting AbsorbReport.to_json() round-trips (covers the CLI-only path tests currently miss). Reorder the absorb CLI branch to print the report before file side effects so a reporting failure can never follow a silent mutation.

### 2. Kill the echo chamber: tool-written fractional heights forge the 'human polished' signature and permanently blind size.* rules

`bug` · impact **high** · effort **M**

Confirmed by four independent lenses (core/adversarial/toolux/arch — merged). advise --fix and calibrate-fed fix_height write fractional heights; human_polished() then suppresses every size.* finding on charts no human touched, including width complaints the fix never addressed and violations under a stricter audience. Report says ok:true over hidden defects. The learning loop's output disables the rulebook it calibrates. Also swallows width-only findings on genuinely absorbed charts (absorb can never write widths) and drops polished+ignored findings from the ignored list.

**Proposal:** Three coordinated changes: (a) math.ceil autofix height targets in fix_height/row-harmony so tool output never mints the fractional signature (one-line core fix); (b) narrow the suppression to height-driven complaints — split or dimension-tag the pie/heatmap geometry findings so width clauses survive on polished charts; (c) move the suppressed-key check before the polish skip in advise() so explicit design.ignore intent always appears in `ignored`. Add regression tests for the vbar-flip/row-harmony masking scenario and the width-only case.

### 3. Validate design.yaml overlay values (types, ranges, audience keys) and clamp fix targets to spec bounds

`bug` · impact **high** · effort **M**

Confirmed across three lenses (merged). The overlay is an org-wide user-edited trust boundary; today a string value crashes check/apply even at --design warn as 'unexpected' TypeError (the code's own message admits it's wrong), scalar `disable` silently becomes a set of characters, recommended_heights > 100 makes the fix loop corrupt the spec then blame the user's overlay, audience-key typos are silently ignored, and audience-level recommended_heights wholesale replaces params-level entries instead of merging.

**Proposal:** One validation pass in load_overlay/params_for raising ValueError (the CLI's existing typed channel): disable must be list[str]; params/audiences dicts with numeric values; audiences keyed by AUDIENCE_NAMES; recommended_heights dict[str, number] range-checked 1..100 (also when calibrate writes it). Clamp fix_height targets to the spec's height cap. Fix the recommended_heights per-key merge across all four layers. Belt-and-braces: _advice_payload catches Exception, honoring its 'never break the pipeline' contract. ~20 lines in presets.py + small model.py/cli.py touches, kills eight confirmed symptoms.

### 4. data.grain-vs-range must apply the compiler's P1D default when time_grain is omitted

`bug` · impact **high** · effort **S**

Confirmed: the most common LLM-authored shape (optional time_grain omitted) compiles identically to an explicit P1D chart but gets zero advice — 'last 5 years' silently renders ~1,825 daily points. The rule is disabled precisely on the specs that need it most.

**Proposal:** In grain_vs_range, evaluate `_GRAIN_DAYS.get(c.time_grain or DEFAULT_TIME_GRAIN)` and drop time_grain from the skip condition (keep skipping only on missing/unparseable time_range). One rule change + two test rows.

### 5. Add a standalone minimum-width rule and make presets arithmetically self-consistent

`bug` · impact **high** · effort **M**

Confirmed: the brief preaches 'below 3/12 is unreadable' but width is only checked when a row already exceeds max_row_charts — a 2/12 timeseries in a 2-chart row passes clean, and operational's max_row_charts=5 (15/12 needed) invites exactly the layout the rulebook's own error branch condemns. Merged with the coverage gap: tables/pivots/funnels/treemaps have NO width rule at all (four 3/12 tables: zero findings), and 1/12 KPIs pass under operational.

**Proposal:** New size.min-width rule over all non-markdown charts: axis/table/pivot < 3/12 error, < 4/12 warn (pie/heatmap keep their dedicated rules); KPI floor at 2/12. Cap or derive max_row_charts at floor(12/3)=4 and drop operational's to 4 with a doc note. Tests for the exactly-at-max row and the table-row case.

### 6. chart.pie-slices: stop recommending row_limit truncation that manufactures a lying part-to-whole

`bug` · impact **high** · effort **S**

Confirmed: Superset computes pie percentages over returned rows only and the spec has no 'other' bucket, so the rule's 'set row_limit 7' remedy makes the top-7 read as 100% of the whole — violating the one integrity constraint pies have. The critic is actively teaching LLM authors to misstate shares.

**Proposal:** Reword the finding to prefer horizontal bar; if the pie stays, require the title to disclose truncation. Add one guideline line: 'a row_limit on a pie redefines the whole; a truncated pie lies about share'. Rule text + guideline + test-string updates.

### 7. Count horizontal slots, not flattened charts: fix row-density and kpi-band false positives on sketch stacks

`false-positive` · impact **high** · effort **S**

Confirmed twice each (merged four findings). Band.chart_names flattens sketch columns, so layout.row-density warns (and can falsely ERROR, blocking --design strict) on charts the user already split vertically, and layout.kpi-band flags the canonical KPI-sidebar-beside-hero-chart pattern the sketch grammar exists to express. These punish the tool's own best layouts and push users to ignore-list legitimate rules.

**Proposal:** In row_density and kpi_band, iterate band.items: an item counts once, at item.width, and a pure-KPI stack is its own virtual band (only flag KPI/detail mixing within one item or in rows mode). Update DESIGN-BRAIN's kpi-band row to document the mixing branch. One file (rules.py) + sketch-stack tests, which currently don't exist.

### 8. Update the stale MCP tool-surface pin, install the [mcp] extra in CI, and close MCP parity gaps

`bug` · impact **medium** · effort **M**

Confirmed: test_mcp_server.py pins 6 tools while 9 exist, and importorskip silently skips the whole file in dev — the 'pins the tool surface' guarantee is dead and the 3 design tools have zero coverage. Merged with parity gaps: advise_spec drops resolution_errors (agent can't tell data-aware rules were skipped), check_spec/build carry no advice block (brain default-OFF over MCP, contradicting decision-log #1), findings say fixable:true with no way to obtain the fix, and bad audience returns code 'overlay'.

**Proposal:** Pin the 9-tool list and add the mcp extra to dev/CI. Attach resolution_errors in advise_spec; add the advice block to check_spec; add a fix_spec/advise_and_fix MCP tool returning patched spec_json; validate audience against AUDIENCE_NAMES with code 'audience'. Offline FastMCP tests for design_brief/advise_spec.

### 9. Preset sanity: executive kpi_row_min to 2, operational fold_units to 22

`false-positive` · impact **medium** · effort **S**

Confirmed: a classic two-KPI executive hero band fires 'looks unfinished' (false positive on a professional layout), and operational's fold budget (26) exceeds the ~22-unit viewport its own intent line ('everything visible' wall monitors) requires — the preset contradicts itself.

**Proposal:** presets.py value changes: executive kpi_row_min=2; operational fold_units=22 (or rewrite the intent line). The kpi width floor lands with the size.min-width rule (rank 5). Update the two pinned tests.

### 10. calibrate: report the per-type candidate entries it already builds but throws away

`bug` · impact **medium** · effort **S**

Confirmed dead code: 'needs >= N samples' / 'within 1 unit' notes are constructed then never appended, so users see events:4, proposals:[] with no explanation of what was observed or how far they are from a proposal — the learning loop looks inert exactly when it's warming up.

**Proposal:** Append non-qualifying entries under a `candidates` key in the calibrate report. ~3 lines in calibrate.py + one test.

### 11. chart.heatmap-grid: fix the probe-cap blind spot that passes 6,000-cell grids

`bug` · impact **medium** · effort **S**

Confirmed: count_up_to saturates at 31, so any grid whose smaller axis has <= 12 values (day-of-week is 7 — the most common heatmap shape) can never exceed the 400-cell threshold no matter how huge the other axis is. Merged with the double-probe ordering nit (small-cap probe runs first, wasting a live query).

**Proposal:** When a side saturates, warn with 'at least NxM cells' phrasing (or re-probe at ceil(400/other)). Probe once at the max cap any rule requests per (dataset, column) — or have more_than probe at a fixed generous cap. Note the unbounded server-side GROUP BY cost in the probe docstring.

### 12. narrative rule noise pack: acronym Title-Case misclassification and boolean/negative filter-title fragments

`false-positive` · impact **medium** · effort **S**

Both confirmed. BI names are dense with acronyms (MRR/ARR/KPI), so title-style flags perfectly consistent dashboards — and its own NOTE admits renaming is dangerous, making the bogus advice costly. Boolean flag filters demand 'True' appear in titles; negative numbers slip the digit guard. Cheap, common, credibility-eroding noise.

**Proposal:** Two one-line guards in rules.py: skip fully-uppercase words in _case_class; skip non-string values (isinstance check, bool before int) in filtered_title's fragment loop. Tests for each.

### 13. Deduplicate size findings: axis-min-height must skip heatmaps (and hbars with row_limit)

`false-positive` · impact **medium** · effort **S**

Confirmed: one short heatmap yields two warns with contradictory targets (6 vs 8); convergence rests on the max-merge crutch. Inflated counts and conflicting guidance in a report an LLM acts on verbatim.

**Proposal:** Skip heatmap in axis_min_height (it owns a stricter rule) and horizontal bars with row_limit set (hbar-window's formula binds). Align heatmap-geometry's '< 6' message with its fix target of 8. rules.py + tests.

### 14. Validate tab-title uniqueness in the spec

`bug` · impact **medium** · effort **S**

Confirmed three times (merged): duplicate titles collapse layout.tab-balance's title-keyed counts into fabricated skews (blaming the fattest tab family as 'thin', or masking a real 5:1 skew), and make every 'tab X row N' where-string ambiguous. The spec already validates chart and filter name uniqueness — tabs were just missed.

**Proposal:** Add a uniqueness validator for tab titles in Layout mirroring _unique_names (root fix: repairs tab-balance counts AND where() ambiguity for all callers). One spec.py validator + test.

### 15. Make the fix-loop convergence invariant explicit and guarded

`bug` · impact **low** · effort **S**

Confirmed (merged three findings): fix.py's max() merge and both docstrings claim 'heights only ever rise', but size.kpi-height lowers. Safe today only by accident of rule membership — the first future rule touching KPI heights ping-pongs or silently discards the lowering.

**Proposal:** Fix the two docstrings to state the real invariant (KPI heights clamped 2-6 by exactly one rule; all other height fixes only rise) and add one contract test asserting no two fixable rules target the same chart's height in opposing directions. Optionally have advise_and_fix detect a no-progress round and stop with the conflicting keys in the report.

### 16. pivot_table rule pack: end the rulebook's worst blind spot

`gap` · impact **high** · effort **M**

Not one of the 31 rules covers pivot_table despite its 10,000-row default and COLUMNS metrics layout — the tool can emit its worst scroll dungeon (3x2 dims, 5 metrics, 160px tall, 10k rows) with ok:true. Highest-value knowledge gap by rendered-damage-per-rule.

**Proposal:** Four rules: size.pivot-window (height vs row_limit, reuse table-window's 0.8 units/row math), chart.pivot-columns (data-aware: column-cardinality x metrics > ~15 rendered columns), chart.pivot-dims (rows+columns > 3, mirrors treemap-depth), extend data.row-limit-intent to pivot_table. One pivot limits line in chart-choice.md. rules.py + guideline + tests.

### 17. Filter-bar rule pack: count, duplicates, cardinality, undefaulted time windows, range filters

`gap` · impact **medium** · effort **M**

filters.* has exactly one rule; a 7-select bar with duplicate columns and an undefaulted all-history time picker (slow, meaningless for executives) passes clean. Filter overload is a top documented dashboard failure. Merged with the confirmed range-filter invisibility (third filter type absent from docstring, rules, brief).

**Proposal:** Add filters.count (selects > ~6, threshold per preset), filters.duplicate-column, filters.select-cardinality (existing probe machinery, > ~500 distinct), filters.time-default (info; warn for executive), and a bare-range-filter info. Fix the spec module docstring ('select + time_range') and mention range filters once in the brief.

### 18. grain-vs-range v2: per-type point budgets, KPI-trend coverage, complete grain table

`gap` · impact **medium** · effort **M**

Merged four findings: timeseries_bar's ~30-period cap is stated in the guideline but the linter allows 1000 (730 daily bars pass); big_number_trend sparklines full-scan all history with no grain guidance anywhere; _GRAIN_DAYS misses PT1M/PT1S and Superset week-anchor grains ('last day' at PT1M = 1,440 points, the exact pathology, silently skipped); zero-span ranges get 'use a finer grain' advice no grain can satisfy.

**Proposal:** Per-type point budgets in grain_vs_range (bar ~40, line/area ~300); new info rule for big_number_trend with absent/fine grain and no defaulted time window; add PT1M/PT1S and week-anchor spellings to _GRAIN_DAYS (or a tiny ISO-duration regex); when span==0 say 'extend the time_range' and drop the grain suggestion. All in rules.py + one chart-choice.md line each.

### 19. data.top-n-sort: a limit without an order is a sample, not a ranking

`gap` · impact **medium** · effort **S**

The classic silent ranking bug: 'Top Ten Stores' with row_limit 10 and no sort_by shows 10 arbitrary rows and no rule notices. Bars are compiler-protected; tables are not, and sort order is otherwise absent from the rulebook.

**Proposal:** New warn rule: aggregate-mode table with row_limit <= ~100 and no sort_by. One line in chart-choice.md's ranking row. rules.py + test.

### 20. Fix the factually wrong category-sort guidance and add chart.ordinal-order

`gap` · impact **medium** · effort **M**

Confirmed: chart-choice.md says 'category axes sort alphabetically' but the compiler metric-sorts bars/pies/funnels (the prescribed order-encoded-label workaround is inert for bars — x_axis_sort is hardcoded to the metric). The real failure — weekday/month/funnel-stage scrambled by metric or alphabetically ('Apr, Aug, Dec') — is unmentioned and unlinted.

**Proposal:** Rewrite the guideline per chart family (metric-sorted: good for rankings, wrong for ordinals; heatmap/pivot: alphabetical). Add info rule chart.ordinal-order with a cheap column-name heuristic (day|weekday|month|quarter|dow). Guideline + rules.py + tests.

### 21. Brief upgrade: canonical exemplar sketch, defaults table, format/grain vocabulary

`gap` · impact **medium** · effort **S**

The brief uses 56 of its 150-line budget and never states the defaults the critic later judges (height 8, row_limit 10,000), the d3 tokens, or the grain vocabulary it demands — and prior art (Show Me, Draco, LIDA) is unanimous that exemplars beat rule lists for generative compliance. Cheapest lever on first-shot spec quality.

**Proposal:** Spend ~25 lines in brief.py: a 10-line canonical ASCII sketch per audience intent, a 4-line defaults table ('omit height -> 8; omit row_limit -> 10,000 — the critic flags both'), one line each for d3 formats and ISO grains. Update the line-budget test.

### 22. narrative.format-consistency rule and an honest guideline about what the spec can express

`gap` · impact **medium** · effort **M**

Tier G demands 'consistent number formats per measure' but no rule checks it, and tables/pivots hardcode SMART_NUMBER so the guideline is inexpressible on any mixed dashboard — the brain instructs the LLM to do the impossible.

**Proposal:** New info rule: identical metric strings across KPI charts with unequal number_format. Soften the guideline to what the spec can express today. File a spec v-next note for per-metric d3 format on table/pivot/timeseries (do not build it yet).

### 23. First color knowledge: chart.format-bands and RAG/heatmap-scale guideline lines

`gap` · impact **medium** · effort **M**

'Color' appears nowhere in rules.py, yet FormatRule is the one surface the spec controls color on: overlapping RAG bands, green-for-high inversions, and lone decorative bands all pass. Heatmaps pin a sequential scheme that is wrong for signed deltas and no guideline mentions it.

**Proposal:** Add chart.format-bands (warn: overlapping/contradictory ranges per metric; info: single lone band). Guideline lines: RAG polarity (red = adverse, IBCS) and 'sequential scale suits magnitudes; avoid heatmapping signed deltas'. rules.py + composition.md + tests.

### 24. Small chart-choice rules: treemap-vs-bar and one-line markdown header height

`gap` · impact **low** · effort **S**

Two cheap, self-contained wins: a single-level low-cardinality treemap is strictly dominated by a horizontal bar (Cleveland-McGill) and never flagged; unsized one-line markdown headers burn 4 units (160px), undercutting the whitespace guideline, and are autofixable under existing safety policy.

**Proposal:** Info rule chart.treemap-vs-bar (len(groupby)==1 and cardinality <= ~10). Fixable info rule layout.markdown-height (<= 1 non-empty line, height >= 3 -> set 2). One guideline line each.

### 25. Validate ignore/disable entries and give band findings scoped keys

`architecture` · impact **medium** · effort **M**

Merged three findings: typo'd or stale design.ignore/--ignore/overlay-disable entries are silently inert (the user believes a rule is off while it fires — contradicting the module's own 'silence stays visible' philosophy), and band-level findings key on the bare rule id, so accepting one deliberate band silences the rule dashboard-wide.

**Proposal:** In advise(): split each suppression key on '@', require the rule-id half to exist in RULES (alias-aware once rank 27 lands), surface unmatched entries as `unmatched_ignores` in the payload. Give band findings scoped keys from their stable where-strings ('layout.kpi-band@tab-Ops-row-2') accepted alongside current forms. design/__init__.py + model.py + docs + tests.

### 26. Chart-type exhaustiveness contract test for the design taxonomy

`architecture` · impact **medium** · effort **S**

A 15th chart type falls through the design brain silently: compiler/resolver/decompile fail loudly if missed, but the hand-maintained KPI/TIMESERIES/AXIS sets and rule string literals just have no opinion and nobody is told. The repo already uses this exact pattern (test_params_contract).

**Proposal:** Declare the standalone set (pie, table, pivot_table, funnel, treemap) in design/model.py and one contract test: KPI_TYPES | AXIS_TYPES | STANDALONE == set(CHART_TYPES). New types then fail CI until consciously classified.

### 27. Make DESIGN-BRAIN's promises true: golden dogfood test, single version constant, since/alias mechanism

`architecture` · impact **medium** · effort **M**

Confirmed (merged two findings): the doc claims hypothesis property tests and an nyc_taxi golden test that do not exist — the sharpest truth violation found in a doc that declares itself 'the reference'. Rule-id aliases are promised for renames but no mechanism exists (a rename would orphan every ignore/disable list), `since` markers don't exist, and the '1' version string is triplicated with the constant unused.

**Proposal:** Add the ~6-line golden test (example advises clean at analytical); add an 'advise never raises' loop over the existing seeded mutator or delete the property-test claim; add `since` to Rule (default '1') and an ALIASES dict consulted in the suppression check; make payload() and cli.py import DESIGN_BRAIN_VERSION. Rewrite §12 to match.

### 28. Per-deployment severity overrides, and make Rule.severity honest metadata

`architecture` · impact **medium** · effort **M**

Orgs on --design strict have exactly one knob (full suppression) when they disagree with a level. Worse, the registry's severity already disagrees with what two rules emit (row-density warn->error, row-fill warn->info), so any future override or doc tooling keyed on Rule.severity mis-maps today, and no test relates emitted to registered severities.

**Proposal:** Add `severity: {rule_id: level}` to the Overlay, applied at the single collection choke point in advise() (one dict lookup). Document Rule.severity as the default level; add a test whitelisting the two known escalation/demotion variances. Rides on rank 3's overlay validation.

### 29. Calibration v2: log audience per absorb event, key recommendations audience->type, add --since decay

`architecture` · impact **medium** · effort **M**

Heights dragged on dense operational monitors and spacious executive scorecards pour into one immortal global median; fix_height's max(floor, rec) then inflates operational autofixes with analytical-learned heights, and the brief prints it as house truth for all audiences. Votes from deleted dashboards live forever.

**Proposal:** Log spec.design.audience in each absorb event (backward compatible). Key overlay recommended_heights as audience->type with flat-type fallback. Add `calibrate --since 90d` as the cheap decay knob. Skip weighting schemes until the median demonstrably misleads.

### 30. Cache sketch parsing and geometry lookups: advise is measured ~cubic on sketch dashboards

`architecture` · impact **medium** · effort **S**

Confirmed with profiles: resolved_height does an O(N) scan and re-parses EVERY sketch holder from raw ASCII per call; 62 sketch charts = 316-506ms per advise, times 6 fix-loop rounds, times redesign on decompiled dashboards — multi-second runs spent re-parsing the same ASCII art (parse_sketch called 188x per advise).

**Proposal:** Two small caches, no behavior change: RuleContext.height() returns self.geo[name].height (already computed at construction); memoize parse_sketch per _SketchHolder. Add the benchmark script's worst case as a smoke test bound.

### 31. advise --fix reports what changed (old/new values) and discloses the file rewrite

`improvement` · impact **medium** · effort **M**

The fix report is keys-only; the LLM must re-read the file to learn what was set, and the full-file canonical re-serialization turns a 3-value change into a whole-file git diff with no marker that the file was rewritten. Directly serves the skill's 'apply or consciously ignore' loop and the MCP fix_spec tool (rank 8).

**Proposal:** Report fixed entries as objects {rule, chart, set:{field:new}, was:{field:old}}, add the written path to the payload, and document that --fix normalizes formatting (shared with absorb). fix.py + cli.py + tests.

### 32. check --design strict gate emits the same design_gate error entry as apply

`improvement` · impact **medium** · effort **S**

Same gate, two error surfaces: check flips ok:false with empty errors and stage 'resolve' (agent must infer), while apply dies with an explicit design_gate code and remediation text. Related confirmed doc-drift: §10 claims payload ok reflects --strict but AdviceReport.ok is errors-only.

**Proposal:** Append the design_gate error entry to check's payload['errors']; one-line §10 doc fix ('ok is false iff an error exists; the strict gate rides the exit code'). cli.py + DESIGN-BRAIN.md + test.

### 33. Distribute the implicit-width remainder so row-fill stops blaming users for the tool's arithmetic

`improvement` · impact **medium** · effort **S**

Merged two findings: rows-mode floor division leaves real rendered holes (7 widthless charts -> 7/12) that layout.row-fill then tells the author to hand-fix, though they expressed no width opinion. Sketch mode already solves this with largest-remainder scaling in the same repo.

**Proposal:** Reuse the largest-remainder approach in resolved_item_width so all-implicit rows sum to exactly 12 (compiler and rules share the function, so the render fixes itself and the FP disappears). Align the row-fill §7 severity cell ('warn/info') while touching it. spec.py + doc line + tests.

### 34. Decide the sketch-autofix WYSIWYG policy: don't silently break the drawing

`improvement` · impact **low** · effort **M**

Confirmed: a height autofix on a sketch chart overrides drawn geometry, leaves the sketch text lying to the next reader, and row-harmony deliberately skips sketch sections so the tool-introduced raggedness is never reported — final report clean.

**Proposal:** In sketch sections, withhold height autofixes and advise editing the sketch instead ('repeat the line to make this band 6 units'); or at minimum re-run row-harmony on bands the fix loop touched so the raggedness is reported. Pairs with the provenance work in rank 2.

### 35. Docs discoverability pass: README design-brain section, FEATURES verb table, design.yaml reference, VERIFICATION coverage

`doc-drift` · impact **medium** · effort **M**

Merged four confirmed findings: the 0.2.0 headline feature is invisible on the landing page (zero README mentions, no DESIGN-BRAIN link), the canonical CLI verb table under-reports by a third (missing exactly the four design verbs), the advertised design.yaml overlay format is documented nowhere outside source, and VERIFICATION.md's suite breakdown omits all four design test modules — a reader concludes the feature is untested.

**Proposal:** One PR: README 'design critic' bullet + advise quick-start line + DESIGN-BRAIN link; four rows in the FEATURES verb table; a ~15-line 'House style: design.yaml' section (five keys, worked example, $CHARTWRIGHT_DESIGN_DIR) linked from FEATURES; one VERIFICATION bullet for the design test modules.

### 36. Doc precision sweep: stale counts and eight confirmed §7/§10/legend/docstring drifts

`doc-drift` · impact **medium** · effort **S**

All confirmed, all trivial: 125/16/six counts stale across three docs (actual 161/20/9), 'two new MCP tools' (three shipped), rule count 27 vs 31, hbar-window 'fold budget' vs hard-coded 20, row-limit-intent 'bar/pie/table' misattribution, row-fill severity split, the ⚡ legend over-claim, and the spec docstring's 'select + time_range'. Wrong docs poison design.ignore targeting and strict-gating expectations.

**Proposal:** One editing pass; de-number prose everywhere except VERIFICATION.md ('the full offline suite') so the next feature doesn't re-rot the counts. Split the ⚡ legend into id-level (profile-only) vs description-level (offline, sharpens with probe).

### 37. CLI/payload polish: degraded-payload audience key, redesign overwrite guard, --help formatting, 1-indexed locations

`polish` · impact **low** · effort **S**

Four small confirmed papercuts: the degraded advice payload drops the 'audience' key exactly when the overlay breaks (agents keying on it crash), redesign's default output silently clobbers CWD files, --help renders the docstring as a run-on wall with six verbs undescribed, and 0-indexed 'row 0' locations invite off-by-one edits by agents.

**Proposal:** Add audience to the fallback dict; refuse the default redesign path when it exists ('pass -o to overwrite'); RawDescriptionHelpFormatter + help= strings for the six original verbs; 1-index row references and cite sketch line ranges for sketch bands (the one behavior-visible change — update tests).

## Verification ledger

| lens | findings kept |
|---|---|
| adversarial | 12 |
| arch | 10 |
| biux | 15 |
| core | 8 |
| docs | 12 |
| rules | 11 |
| toolux | 12 |

## Appendix: confirmed findings index

| # | finding | category | impact | lens |
|---|---|---|---|---|
| 1 | pivot_table is a complete rulebook blind spot | gap | high | biux |
| 2 | Top-N without an order: capped tables are arbitrary samples and no rule notices | gap | medium | biux |
| 3 | big_number_trend has no grain/range guidance anywhere | gap | medium | biux |
| 4 | timeseries_bar's ~30-period cap is stated in the guideline but the linter allows 1000 | gap | medium | biux |
| 5 | Filter-bar composition has one rule; select-count, duplicates, cardinality, and undefaulted time windows are unchecked | gap | medium | biux |
| 6 | Number-format consistency per measure: promised in Tier G, unlintable and partly inexpressible | gap | medium | biux |
| 7 | No color knowledge at all: conditional-formatting bands unchecked, heatmap scale semantics unaddressed | gap | medium | biux |
| 8 | Single-level treemap vs bar: the Cleveland-McGill call the rulebook never makes | missed-opportunity | low | biux |
| 9 | One-line markdown headers default to 4 units, undercutting the whitespace guideline | missed-opportunity | low | biux |
| 10 | The brief teaches rules but shows no exemplar and hides the defaults the rules judge | improvement | medium | biux |
| 11 | design.yaml overlay format is advertised but documented nowhere | gap | medium | docs |
| 12 | VERIFICATION.md's offline-suite breakdown has zero coverage of the design brain | gap | medium | docs |
| 13 | README.md is silent on the design brain and doesn't link DESIGN-BRAIN.md | missed-opportunity | medium | docs |
| 14 | Minimum readable width is preached but never enforced; operational preset permits layouts the rulebook itself calls unreadable | bug | high | biux |
| 15 | chart.pie-slices advice manufactures a lying part-to-whole | bug | high | biux |
| 16 | Category sort-order guidance is factually wrong for bar/pie/funnel and silent on ordinal scrambling | doc-drift | medium | biux |
| 17 | Preset value sanity: exec kpi_row_min=3 false-positives a 2-KPI hero band; operational fold=26 contradicts its own intent | edge-case | medium | biux |
| 18 | Doc drift: spec docstring and design doc still say the filter bar is 'select + time_range'; range filters are invisible to the design brain | doc-drift | low | biux |
| 19 | DESIGN-BRAIN §12 claims two tests that do not exist (hypothesis property tests, nyc_taxi golden) | doc-drift | high | docs |
| 20 | Stale hard-coded counts: '125 tests', '16 modules', 'six MCP tools' across three docs | doc-drift | medium | docs |
| 21 | FEATURES.md 'CLI Verbs' table is missing all four design-brain verbs | doc-drift | medium | docs |
| 22 | DESIGN-BRAIN §10: 'ok is false at gate level (warn under --strict)' — payload ok never reflects --strict | doc-drift | medium | docs |
| 23 | Rule count is 31, not 27 (DESIGN-BRAIN §15.4 and the working notes both say 27) | doc-drift | low | docs |
| 24 | size.hbar-window: doc says fix caps at the fold budget; code uses a hard-coded 20 | doc-drift | low | docs |
| 25 | data.row-limit-intent: doc says 'bar/pie/table'; code covers table + horizontal bar only | doc-drift | low | docs |
| 26 | layout.row-fill emits info for small holes; §7 table lists it as plain warn | doc-drift | low | docs |
| 27 | §7 legend over-claims: '⚡ marks data-aware rules (only run with --profile)' but two ⚡-annotated rules run offline | doc-drift | low | docs |
| 28 | Overlay values are never shape/type-validated: strings crash as 'unexpected' TypeError, scalar disable silently no-ops, out-of-range numbers blow up the fix loop | bug | high | core |
| 29 | Fractional-height 'human polished' suppression swallows width-only size findings, though absorb can never write widths | bug | medium | core |
| 30 | layout.row-density counts vertically STACKED sketch charts as side-by-side row occupants -> false-positive warn | edge-case | medium | core |
| 31 | Duplicate tab titles collapse layout.tab-balance counts (dict keyed by title) and nothing validates tab-title uniqueness | bug | low | core |
| 32 | Polish-suppression runs before ignore bookkeeping, so a finding that is both polished and ignored vanishes from the ignored list | edge-case | low | core |
| 33 | params_for: audience-level recommended_heights wholesale replaces params-level entries instead of merging | gap | low | core |
| 34 | Unknown rule ids in design.ignore / --ignore / overlay disable are accepted silently, so typos no-op invisibly | gap | low | core |
| 35 | Fix-loop convergence rests on an unstated invariant: the one LOWERING fix (size.kpi-height) never collides with the raising fixes | improvement | low | core |
| 36 | data.grain-vs-range is silently disabled when time_grain is omitted, though the compiler defaults it to P1D | bug | high | rules |
| 37 | chart.heatmap-grid probe cap (30) is too low for the 400-cell threshold: huge grids with one small axis pass silently | edge-case | medium | rules |
| 38 | layout.kpi-band mixing warn fires on the canonical sketch pattern the tool itself supports: a stacked KPI column beside a tall chart | bug | medium | rules |
| 39 | size.axis-min-height and size.heatmap-geometry double-report the same defect with conflicting targets | bug | low | rules |
| 40 | fix-loop convergence docstrings are false: size.kpi-height lowers heights | doc-drift | low | rules |
| 41 | narrative.filtered-title turns boolean filters into title demands ('True' should appear in the title) | edge-case | low | rules |
| 42 | layout.tab-balance collapses duplicate tab titles and then reports a fabricated skew | edge-case | low | rules |
| 43 | rows-mode implicit widths floor-divide (sketch mode uses largest-remainder), so layout.row-fill blames the user for a hole the resolver created | improvement | medium | rules |
| 44 | No width rule covers tables/pivots/funnels/treemaps: a row of four 3/12-wide tables passes with zero findings | gap | medium | rules |
| 45 | _GRAIN_DAYS misses sub-hour grains and Superset week-variant grains, silently skipping grain-vs-range | gap | low | rules |
| 46 | Band-level findings cannot be ignored per-band: suppressing one accepted layout.kpi-band silences all of them | gap | low | rules |
| 47 | Autofix-written fractional heights inherit absorb's 'human polished' signature and silence later size rules | bug | high | adversarial |
| 48 | Malformed design.yaml overlay values crash advise with a raw TypeError ('unexpected' CLI error) | bug | high | adversarial |
| 49 | fix_height doesn't clamp to the spec's height cap: overlay/calibrate heights > 100 make the fix loop corrupt the spec, then blame the user | bug | medium | adversarial |
| 50 | layout.row-density counts vertically stacked sketch-column charts as side-by-side row occupants (can escalate to a false error) | bug | medium | adversarial |
| 51 | layout.kpi-band flags the canonical KPI-sidebar stack (KPIs stacked in a column beside a hero chart) | edge-case | medium | adversarial |
| 52 | narrative.title-style misclassifies acronyms as Title Case, flagging uniformly-cased dashboards | bug | low | adversarial |
| 53 | narrative.filtered-title stringifies boolean filter values: "filtered to ['True'] but the title doesn't say so" | edge-case | low | adversarial |
| 54 | Autofix on sketch layouts silently breaks the drawing's WYSIWYG contract with no follow-up finding | edge-case | low | adversarial |
| 55 | Duplicate tab titles are valid and make design-brain 'where' strings ambiguous (and collapse tab-balance weights) | edge-case | low | adversarial |
| 56 | design.ignore entries that match nothing are silently inert — a typo quietly re-enables the rule | gap | medium | adversarial |
| 57 | Implicit-width floor division under-fills rows, then layout.row-fill blames the author for the tool's own arithmetic | improvement | medium | adversarial |
| 58 | data.grain-vs-range suggests 'a finer grain' for zero-length ranges where no grain can help | improvement | low | adversarial |
| 59 | MCP tool-surface test is stale (missing the 3 design-brain tools) and silently skipped in dev | bug | medium | toolux |
| 60 | calibrate builds per-type 'note' entries (needs >= N samples / within 1 unit) then throws them away | bug | medium | toolux |
| 61 | Tool-written fractional heights (calibrated autofixes) masquerade as human polish and permanently silence size rules | edge-case | medium | toolux |
| 62 | README has zero mention of the design brain — the headline 0.2.0 feature is invisible on the landing page | doc-drift | medium | toolux |
| 63 | Degraded advice payload (broken overlay inside check/apply) drops the 'audience' key and reports ok:true | edge-case | low | toolux |
| 64 | redesign/decompile default output lands in CWD and silently overwrites | edge-case | low | toolux |
| 65 | MCP parity gaps that matter: advise_spec drops resolution_errors, check/build carry no advice, 'fixable' is a dead-end signal | gap | medium | toolux |
| 66 | check --design strict gate flips ok:false with empty errors and stage 'resolve' — no design_gate marker | gap | medium | toolux |
| 67 | advise --fix rewrites the whole file in canonical formatting and reports keys without values | improvement | medium | toolux |
| 68 | Finding locations are 0-indexed ('layout row 0') and reference normalized bands, not sketch lines | improvement | low | toolux |
| 69 | Bad audience over MCP is reported as code 'overlay' | improvement | low | toolux |
| 70 | chartwright --help renders the module docstring as an unreadable run-on wall; core verbs lack help lines | improvement | low | toolux |
| 71 | `chartwright absorb` crashes with NameError after mutating the spec file (missing `import json` in absorb.py) | bug | high | arch |
| 72 | Echo chamber: calibrated fractional heights written by --fix masquerade as human polish and permanently blind all size.* rules on the chart | bug | high | arch |
| 73 | Versioning story promised in the doc is not implemented: no `since` markers, no rule-id aliases, and the design_brain version string is triplicated | doc-drift | medium | arch |
| 74 | Sketch-mode advise is superlinear (measured ~cubic): parsed_sketch() and chart lookup re-run per height query | edge-case | medium | arch |
| 75 | Fix-merge convergence rests on an accidental invariant: fix.py's max() assumes all height fixes raise, but size.kpi-height lowers | edge-case | low | arch |
| 76 | Overlay values are never type-validated; one bad value in design.yaml crashes check/apply even at --design warn | gap | high | arch |
| 77 | No per-deployment severity override, and Rule.severity is already unreliable metadata that any future override would mis-key on | gap | medium | arch |
| 78 | Calibration learns one global height per chart type: no audience granularity, no decay, votes live forever | missed-opportunity | medium | arch |
| 79 | A 15th chart type falls through the design brain silently: taxonomy sets and rule literals have no exhaustiveness guard | gap | medium | arch |
| 80 | Heatmap cardinality probe fires twice per column because rule registration order runs the small-cap probe first | improvement | low | arch |
