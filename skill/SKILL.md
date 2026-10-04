---
name: superset-dashboard
description: Build or modify an Apache Superset dashboard from a natural-language request by emitting a typed spec and compiling it with the chartwright CLI (guaranteed-correct import, no freeform dashboard JSON). Covers 15 chart types incl. pivot tables, per-chart WHERE filters, a native dashboard filter bar, markdown blocks, and tabs. Use when the user asks to create, generate, or change a Superset dashboard or chart. Never create Superset dashboards any other way (no raw REST calls, no hand-written import bundles, no UI automation).
---

# Superset dashboard via chartwright

You are the untrusted parser at the edge. Your ONLY creative output is a spec
JSON that satisfies the schema. Tested code does everything else.

## Location facts (do not assume CWD)

- Project root: `{{CHARTWRIGHT_HOME}}` (stamped by install-skill.py; if you see the
  literal placeholder, this skill was copied by hand; re-run
  `python install-skill.py` from the repo).
- The session may be launched from ANY directory. Always use absolute paths;
  never rely on the current working directory.
- CLI: `"{{CHARTWRIGHT_HOME}}/.venv/bin/chartwright"` (Windows: `"{{CHARTWRIGHT_HOME}}\.venv\Scripts\chartwright"`).
  Below, `CW` means that absolute path, ALWAYS QUOTED; enterprise repos often
  live under paths with spaces (OneDrive folders).
- Specs live in `{{CHARTWRIGHT_HOME}}/specs/`. They are the source of truth; keep them.
- The profile's credential env vars (whatever names `password_env` and, if
  used, `username_env` declare) must be set in the environment, or the
  profile must define `password_cmd`. If auth fails, show the ProfileError
  verbatim; do not invent variable names or prompt for raw passwords into
  files.

## Bright line

The spec is the ONLY artifact you may author. Never hand-write bundle YAML,
position_json, or REST payloads; never edit the compiler's output; never retry
a failed apply by "fixing" the dashboard in Superset. If the spec can't express
what the user wants, say exactly which feature is out of surface; do not
approximate it with a different mechanism.

## Procedure

1. `CW schema`: read the contract. Surface: 15 chart types (big numbers,
   timeseries line/bar/area/scatter, categorical bar, pie/donut, table,
   pivot_table, heatmap, histogram, funnel, treemap, mixed: bars + a line on
   two axes), metrics as saved names, `AGG(col) [AS Label]` or
   `SQL(expression) AS Label`, per-chart `filters` (WHERE: column/op/value
   or `sql`), goal lines (`annotations`) on line/bar/area/scatter/mixed,
   dashboard-level `filters` (select, time_range, numeric range, time_grain
   and time_column native filter bar; `dependencies` for cascading; `charts`
   to scope any of them), dashboard settings (colour scheme, description,
   certification, draft, refresh), layout as `rows`, `tabs`, or an
   ASCII `sketch` with a `legend`, markdown blocks in rows, `{"header": ...}`
   and `{"divider": true}` entries between rows, an optional
   `layout.footer` (rows below everything, shown under every tab), an optional
   `design` block (audience + rule suppressions; `design.filled` is written
   by `--fix`, never by you).
2. Design brain, ON by default: run `CW brief --audience <a>` and follow it
   while authoring. Infer the audience from the request: executive
   scorecard/leadership review -> `executive`; ops monitor/wall display ->
   `operational`; anything else -> `analytical`. Record the audience in the
   spec's `design.audience`. Brain OFF (user said "no design opinions" /
   "exactly as I specify"): skip the brief, skip step 6, and pass
   `--design off` to check and apply.
3. Write the spec to the specs dir (absolute path). Slug lowercase-kebab;
   chart names unique. Leave the design-default fields unset unless the user
   asked for a value (the brief lists them: time-axis label format, compare
   suffix, count number formats, table cell bars, page size and search box,
   a single series' legend, values on few bars); step 6 fills them.
4. `CW validate <abs-spec-path>`: fix schema errors (max 3 attempts).
5. `CW check <abs-spec-path> --profile <profile>`: fix referential errors
   (max 3 attempts total across 4+5; errors name the exact dataset/column/
   metric at fault). The payload carries an `advice` block (design findings);
   act on it in step 6. `superset_version_too_old` names a field the
   instance's Superset release can't take: remove it and tell the user.
   `superset_version_unknown` means the instance didn't report its release:
   ask the user for it and pass `--superset-version <release>` to check,
   apply and plan. Pass `version_warnings` (fields that release ignores) on
   to the user verbatim.
6. `CW advise <abs-spec-path> --profile <profile>`: the design critic, with
   data-aware rules (column types, cardinality). Apply what it suggests:
   run `CW advise <abs-spec-path> --fix` before any hand edit and before
   apply (MCP: `fix_spec`). It rewrites the file in place: safe geometry
   repairs, plus the design defaults it fills into unset fields, listed in
   the spec's `design.filled`. Each `fixed` entry says `kind: "fill"` or
   `"repair"` and why; keep the fills unless the user asked otherwise. Make
   the rest of your changes by editing THAT file; never regenerate the spec
   from your own copy, or the fills are lost. To keep a value of your own in
   a filled field, change it and remove the field from `design.filled`.
   `CW explain <abs-spec-path> [--chart NAME]` says where each value came
   from. A finding that is a deliberate exception goes in the spec's
   `design.ignore` as `"rule.id@Chart Name"`, and you tell the user. At most
   2 design iterations, then surface the remaining findings verbatim.
7. `CW apply <abs-spec-path> --profile <profile>`: on success give the user
   the dashboard_url and any smoke warnings verbatim. Apply backs up the
   previous state under `~/.config/chartwright/backups/<profile>/<slug>/` (restore with
   `CW restore <zip> --profile <profile>`).
8. Modify tool-born dashboards by editing their spec and re-running 5-7.
   Modify UI-born dashboards via
   `CW decompile <slug-or-id> --profile <profile> -o <abs-spec-path>`;
   show the user the lossiness report before editing. `CW advise` on a
   decompiled spec is a design audit of a legacy dashboard. To redesign one
   in one shot, `CW redesign <slug-or-id> --profile <profile> -o
   <abs-spec-path>`: decompile + audit + safe geometry fixes; act on the
   remaining structural findings by editing the spec (step 6 rules apply),
   show the user the losses and the `next` line, then apply. A dashboard the
   tool does not own comes back under a `-redesign` slug: apply builds it
   side by side, never over the original.
9. If drift is possible (someone edited in the UI), run
   `CW plan <abs-spec-path> --profile <profile>` first and surface what
   apply would change.

## Sketch heights (while drawing)

Each sketch line adds `line` height units (default `line: 2`, one unit =
40 px). KPI rows: 2 sketch lines. Axis charts (timeseries, bar, heatmap,
histogram): 4-5 lines; fewer renders flattened with labels dropped.
Pie/donut: 4+ lines and >= 5 of 12 width. The brief carries the full sizing
table; `CW advise` checks the result.

Heights the user polished by hand in the UI come back via
`CW absorb <abs-spec-path> --profile <profile>` as fractional units; the
design brain respects them (sizing rules go silent on those charts) and
learns from them over time (`CW calibrate`).

## Anti-evasion

| Temptation | Instead |
|---|---|
| Apply failed; tweak the generated ZIP/YAML by hand | Fix the spec; if the compiler is wrong, report the bug |
| Column doesn't resolve; guess a similar name | Show the user the resolver error and the dataset's actual columns |
| User wants a chart type outside the 15 | Say it's out of surface; offer the nearest supported type |
| Retry apply a 4th time with random changes | Stop; surface all errors verbatim |
| Advice finding seems wrong; hand-tune to dodge it | Record it in the spec's `design.ignore` and tell the user, or report a rule bug |
| A design default you'd rather not have; delete it, or rewrite the spec without it | `--fix` would fill it again: add `"default.rule@Chart Name"` to `design.ignore` and tell the user |
| "Quick" dashboard via POST /api/v1/dashboard/ | Never; the guarantee only exists through chartwright |
| Auth fails; hunt for password variables or files | Show the ProfileError; the user names their env var or password_cmd |
