---
name: superset-dashboard
description: Build or modify an Apache Superset dashboard from a natural-language request by emitting a typed spec and compiling it with the chartwright CLI (guaranteed-correct import, no freeform dashboard JSON). Covers 14 chart types incl. pivot tables, per-chart WHERE filters, a native dashboard filter bar, markdown blocks, and tabs. Use when the user asks to create, generate, or change a Superset dashboard or chart. Never create Superset dashboards any other way (no raw REST calls, no hand-written import bundles, no UI automation).
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

1. `CW schema`: read the contract. Surface: 14 chart types (big numbers,
   timeseries line/bar/area/scatter, categorical bar, pie/donut, table,
   pivot_table, heatmap, histogram, funnel, treemap), per-chart `filters`
   (WHERE), dashboard-level `filters` (select + time_range native filter bar,
   time_range with an optional `default`), layout as `rows`, `tabs`, or an
   ASCII `sketch` with a `legend`, markdown blocks in rows.
2. Write the spec to the specs dir (absolute path). Slug lowercase-kebab;
   chart names unique.
3. `CW validate <abs-spec-path>`: fix schema errors (max 3 attempts).
4. `CW check <abs-spec-path> --profile <profile>`: fix referential errors
   (max 3 attempts total across 3+4; errors name the exact dataset/column/
   metric at fault).
5. `CW apply <abs-spec-path> --profile <profile>`: on success give the user
   the dashboard_url and any smoke warnings verbatim. Apply backs up the
   previous state under `~/.config/chartwright/backups/<profile>/<slug>/` (restore with
   `CW restore <zip> --profile <profile>`).
6. Modify tool-born dashboards by editing their spec and re-running 4-5.
   Modify UI-born dashboards via
   `CW decompile <slug-or-id> --profile <profile> -o <abs-spec-path>`;
   show the user the lossiness report before editing.
7. If drift is possible (someone edited in the UI), run
   `CW plan <abs-spec-path> --profile <profile>` first and surface what
   apply would change.

## Design rules (the dashboard must read well as generated)

Sketch heights: each sketch line adds `line` height units (default `line: 2`,
one unit = 40 px). Draw:

- Big-number (KPI) rows: 2 sketch lines.
- Every chart with axes (timeseries, bar, heatmap, histogram): 4 to 5 sketch
  lines. Fewer than 4 renders flattened, with axis labels dropped.
- Pie/donut: 4+ lines and at least 5 of 12 row width, or the ring shrinks
  and the legend crowds it.
- A heatmap with many columns: 7 of 12 width or more, 5 lines.

Chart choice:

- A ranking over a dimension with many or long values: `bar` with
  `"orientation": "horizontal"` and a `row_limit` near 10; vertical bars fit
  at most ~8 category labels before Superset starts dropping them. When the
  user wants several measures per item, use a `table` instead.
- A histogram over a long-tailed column: trim the tail with a chart-level
  WHERE filter and name the trim in the chart title so the chart says what
  it shows; 20 to 30 bins.
- Category axes sort alphabetically. When the dataset carries an
  order-encoded label column (labels prefixed with their sort index), chart
  that column instead of the natural-name column.
- One dominant category flattens its siblings in a vertical bar; that is the
  data talking, not a defect; note it or filter it, don't hide it.

## Anti-evasion

| Temptation | Instead |
|---|---|
| Apply failed; tweak the generated ZIP/YAML by hand | Fix the spec; if the compiler is wrong, report the bug |
| Column doesn't resolve; guess a similar name | Show the user the resolver error and the dataset's actual columns |
| User wants a chart type outside the 14 | Say it's out of surface; offer the nearest supported type |
| Retry apply a 4th time with random changes | Stop; surface all errors verbatim |
| "Quick" dashboard via POST /api/v1/dashboard/ | Never; the guarantee only exists through chartwright |
| Auth fails; hunt for password variables or files | Show the ProfileError; the user names their env var or password_cmd |
