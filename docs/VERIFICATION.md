# Verification

How this tool is tested, what each layer proves, and how to reproduce all of
it. The companion page, `docs/CONTRACTS.md`, cites every Superset behavior
the tool depends on to Superset's source at releases 4.1.4, 5.0.0, and
6.1.0; this page covers what is proven by actually running the tool.

## What is tested, and why

The correctness guarantee lives in typed code: the spec is validated, every
reference is resolved before anything is built, and the result is verified
after import. Testing therefore concentrates on the dimensions that vary in
real use and that a single test run holds constant:

- **Version**: Superset 4.1.4, 5.0.0, and 6.1.0, as real containers rather
  than mocks.
- **Environment**: Linux and Windows, which differ in text encoding, path
  rules, and zip format details.
- **Time**: hundreds of edit, apply, and verify cycles per dashboard rather
  than one.
- **Other writers**: a scripted second writer that overwrites dashboard
  metadata the way a stale browser tab does. Superset stores dashboard
  metadata last-writer-wins in every supported version (CONTRACTS, "How
  dashboard settings are stored").
- **Faults**: injected failures at every stage boundary of `apply`.

Every layer below runs in CI on every push: the offline suite on
`ubuntu-latest` and `windows-latest`, then per-version live jobs that boot a
real `apache/superset` container at each of the three releases and run the
live check, a 25-cycle soak, the second-writer scenarios, and fault
injection.

## The matrix

| Layer | Proves | Where it runs |
|---|---|---|
| Offline suite (123 tests, 15 modules) | Contract, determinism, round-trips, credentials | every push, Linux + Windows |
| Chart-option contract | Every emitted chart option is declared by each version's plugin source | every push |
| Live guarantee check | 15-chart apply, per-chart data check, ids stable across re-apply | every push, all 3 versions |
| Lifecycle soak | 500 randomized edit cycles with invariants held | 500 cycles on 6.1.0 and 4.1.4 before release; 25 cycles per version on every push |
| Second-writer scenarios | Stale-tab overwrites detected by `plan`, repaired by `apply` | every push, all 3 versions |
| Fault injection | A typed failure at every stage boundary; complete restore | every push, all 3 versions |
| Real-instance use | Production 4.1.x on Windows, real datasets, real users | ongoing |

## 1. Offline suite: 123 tests

- **Spec contract** (`test_spec.py`, `test_spec_v2.py`): validation
  semantics. A layout is rows or tabs, never both; row widths must fit the
  grid; chart names must be unique (duplicate names would silently collide
  as identities); filter bounds and value shapes are checked; unknown
  fields are rejected everywhere, so a typo fails loudly instead of being
  ignored.
- **Deterministic compiler** (`test_compiler.py`): compiled bundles are
  compared byte for byte against checked-in reference copies, with zip
  timestamps and entry order pinned, so the same spec produces the
  identical bundle on any build platform.
- **Lossless round-trips** (`test_decompile.py`): decompiling a compiled
  spec reproduces that spec exactly, across the full surface (15 charts
  covering all 14 chart types, chart filters, the native filter bar,
  markdown, tabs), and stays stable under a second round-trip. Decompiling
  a real 25-chart export that uses unsupported chart types names every
  loss; nothing drops silently.
- **Randomized round-trips** (`test_property_roundtrip.py`): a seeded
  random editor mutates a spec while keeping it valid (the same generator
  the live soak uses) through 20 seeds and 6 steps each; compile then
  decompile must reproduce the spec at every step. Seeds make every
  failure exactly reproducible.
- **Credential portability** (`test_profiles.py`): passwords from an
  environment variable of any name, usernames from an environment variable
  (with the literal value taking precedence), credential-manager commands
  in both shell-string and argument-list form, home-directory expansion,
  TLS options, and a named error when no credential source exists.
- **Drift and plan normalization** (`test_dashdiff.py`), **MCP parity**
  (`test_mcp_server.py`: the six MCP tools mirror the CLI one to one),
  **backup layout** (`test_backup_layout.py`: backups are separated per
  profile and the location override is honored), plus dedicated suites for
  pivot formatting, range filters, layout sketches, and absorb.

## 2. Chart options, checked against plugin source

Superset's backend has no schema for chart options; each chart type's
options are defined only by its frontend plugin. The file
`tools/contracts/params-contract.json` holds the option names for the 13
emitted chart types, extracted from plugin source at each supported
release, and `tools/params_drift.py` (run by `test_params_contract.py`)
fails the build if the compiler ever emits an option a target release does
not declare. Current status: clean against all three releases; the single
tool-owned key (`sdc_categorical_bar`) is allowlisted with its rationale
recorded. Option drift in Superset's plugins, the natural failure mode of
any tool that writes chart options, becomes a failing build here instead of
a runtime surprise.

## 3. Live guarantee check (`tools/ci_live_check.py`)

Runs against a real instance, start to finish. First, pre-flight
resolution, which collects every bad reference into typed errors. Then an
apply of the complete example spec (`tests/fixtures/kitchen_sink.json`),
which exercises all 14 chart types, per-chart WHERE filters, a native
filter bar with a value picker, a time range, and a numeric range scoped to
specific charts, plus markdown and tabs. Then a per-chart data check: each
chart's query must return HTTP 200 and rows; an empty chart is a named
warning, never a silent pass. Finally a second apply of the same spec,
asserting that every chart keeps its id. Id stability matters because
dashboard metadata references charts by id: changing ids is what turns a
stale browser tab into a writer that corrupts filter scopes.

## 4. Lifecycle soak (`tools/soak.py`)

The loop users actually live in, run to exhaustion. Each cycle applies one
seeded random edit within the supported surface (add, remove, or rename
charts; retitle; add, remove, or rescope filters; regenerate the layout;
switch between rows and tabs) and then asserts, against the live instance:

1. `apply` completes, including link verification and the per-chart data
   check;
2. surviving charts keep their ids;
3. `plan` is clean immediately after `apply`;
4. decompiling the tool's own dashboard loses nothing;
5. periodically, a dry-run `absorb` changes nothing (heights round-trip).

Before release, this ran 500 consecutive green cycles on 6.1.0 and on 4.1.4
(the 4.1.4 run grew to 20 charts and 10 filters under the random edits).
Every failure dumps the seed, the spec, and the report for one-command
reproduction.

## 5. Second-writer scenarios (`tools/adversary.py`)

Superset stores dashboard metadata last-writer-wins in every supported
version, and on 4.1.x a closing browser tab writes its whole stale copy
back (CONTRACTS has the file and line citations). This harness performs
those overwrites deliberately and asserts that `plan` detects each one and
`apply` repairs it without changing any chart id:

- A tab from before a filter was rescoped writes the old scope back.
- A tab from before a filter existed erases that filter entirely.
- Filter scopes are corrupted to reference deleted chart ids, which the
  server accepts silently; `plan` recomputes the scopes and refuses to
  call the dashboard clean.
- A dashboard with cross-filtering turned off keeps that setting through
  the tool's own writes (the API turns it back on when the key is
  omitted).

All four scenarios pass on 4.1.4, 5.0.0, and 6.1.0.

## 6. Fault injection (`tools/faultline.py`)

A wrapping client trips a chosen method with the same typed error a real
network drop produces, or a fake HTTP 500 for the importer. A fault at
every stage boundary (the dataset round-trip and stale-chart deletion
during preparation, an importer 500 and a mid-import drop, the post-import
link verification, the data check) must produce a typed report naming the
stage, never a traceback, and a recorded backup whenever mutation had
begun. Then:

- for faults during preparation or import, the automatic restore fires,
  and `plan` against the pre-apply spec comes back clean: the dashboard is
  back, not half-updated;
- for faults after import, a clean re-apply converges.

Restore is verified to be complete. Because Superset's importer never
overwrites existing charts, a naive re-import restores only the dashboard
shell. The restore path additionally writes surviving charts' settings
back from the backup and recomputes filter scopes from the backup's own
name-based markers; the test changes chart settings and scopes, restores,
and requires `plan` against the old spec to be spotless.

## 7. Real-instance use

Beyond sandboxes, the tool is used against a production Superset 4.1.x
instance on Windows by a working analyst: real warehouse datasets,
dashboards used weekly, real concurrent browser sessions. That round of use
produced an incident ledger (every entry root-caused and closed; the
defects appear in the table below) and directly motivated the soak, the
second-writer scenarios, and fault injection.

## Defect ledger: what each layer caught

Twenty real defects found by these layers, none of which the original
unit suite could see. The layer that caught each one is the reason that
layer exists.

| # | Defect | Caught by |
|---|---|---|
| 1 | On Windows, spec files were read in the legacy cp1252 encoding, corrupting UTF-8 text | real-instance use |
| 2 | The 4.x dashboard detail API omits the uuid, so the ownership check refused the tool's own dashboard | real-instance use |
| 3 | Zip metadata varies by platform, so compiled bundles differed between Windows and Linux | real-instance use |
| 4 | A metadata round-trip left unscoped filters applying to no charts at all | real-instance use |
| 5 | Re-apply deleted and re-imported charts, changing their ids; stale browser tabs then corrupted filter scopes | real-instance use; fixed at the root by updating charts in place |
| 6 | `apply` passed a list where the chart lookup expects a dict (introduced during a merge) | live check, first run |
| 7 | A Windows executable path, embedded in a quoted TOML test fixture, broke on backslashes | Windows CI, first run |
| 8 | A range filter scoped to specific charts could not be rebuilt from a backup; the scope dropped silently | randomized round-trips, seeds 6 and 9 |
| 9 | `plan` could not run against the tool's own dashboard on 4.x; it depended on the uuid the detail API omits there | soak on 4.1.4, cycle 1 |
| 10 | On 4.x and 5.0, renaming or removing a chart left the old chart still linked to the dashboard, because import merges links instead of replacing them | soak on 4.1.4, cycle 10 |
| 11 | Renaming a chart left the old chart behind as an orphan, on every version | fixed by the same root fix as #10: the tool deletes its own charts once they leave the spec |
| 12 | `plan` never compared filters, so the most common real-world drift was invisible to it | designing the second-writer scenarios |
| 13 | Filter scopes pointing at deleted chart ids read back as clean | second-writer scenario: corrupted scopes |
| 14 | Restore brought back the dashboard but not the surviving charts' settings | fault injection: restore design |
| 15 | Restore left filter scopes at their defaults instead of the saved ones | fault injection: restore completeness |
| 16 | Big-number subtitles rendered at the full card height on 6.1.0, huge and cropped, because the compiler left font sizes to a plugin fallback | building the public demo on real data |
| 17 | The params drift checker compiled a fixture with no subtitles, so the conditional `subheader` key was never checked and its 6.1.0 rename went unnoticed | auditing every visual control after #16 |
| 18 | Charts without their own time column ignored the dashboard time filter entirely, while the filter bar still counted them as filtered | live check of the time filter default: a one-week filter left a full-month total |
| 19 | `plan` crashed on any sketch-layout spec; every prior fixture and soak used rows or tabs | running `plan` against the demo dashboard |
| 20 | A labeled `COUNT(*)` metric lost its label on decompile, so `plan` reported drift forever on clean dashboards | the same `plan` run, after #19 was fixed |

## Reproduce everything

```bash
# offline (any machine, no Superset needed)
python -m pytest tests/ -q                 # 123 tests
python tools/params_drift.py --all         # chart options vs plugin source, 3 versions

# live (any sandbox; sandbox/up.sh --tag <v> boots one)
export SDC_CI_PASSWORD=admin
python tools/ci_live_check.py --base-url http://localhost:8098
python tools/soak.py       --base-url http://localhost:8098 --cycles 500 --seed 1
python tools/adversary.py  --base-url http://localhost:8098
python tools/faultline.py  --base-url http://localhost:8098
```

CI definition: `.github/workflows/ci.yml`.

## Known coverage limits

Stated plainly, so the green above means something:

- **Auth**: database and LDAP login only. Instances that allow only SSO or
  OAuth are out of scope, because Superset disables the password login API
  there.
- **Versions**: 4.1.4, 5.0.0, and 6.1.0 exactly; other release lines are
  untested.
- **Concurrency**: the second-writer harness scripts the known stale-tab
  patterns; arbitrary multi-writer races are not exhaustively explored.
- **Permissions**: all verification runs as an admin. Restricted roles
  (permission errors, datasets hidden by row-level security) are untested.
- **Rendering**: layout correctness is verified structurally and by spot
  screenshots, not by pixel comparison; visual taste remains human.
- **Data engines**: live layers run against Superset's example datasets
  plus one production warehouse. Other engines (Presto, BigQuery, and the
  rest) are untested; the tool only reads metadata and issues chart-data
  queries, but engine-specific column-type quirks could surface in the
  data check.
