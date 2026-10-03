# Deploy dashboards from git

Keep one spec file per dashboard in your repository and review each dashboard change as a short diff in its pull request. Before merge, CI confirms the change against your Superset instance and shows what it would change there; after merge, CI applies it, staging first if you want. A nightly job tells you when someone has edited a deployed dashboard in the UI.

## 1. Review a change as a diff

A spec diff is short enough to read in a pull request:

- You write only what you chose: the charts, filters, layout and datasets. Chartwright fills in the rest of Superset's settings when it builds.
- The file holds no ids. Chartwright derives the dashboard's id from its slug and each chart's id from the slug and the chart's name, so the same spec gets the same ids on every run and on every instance.
- Compiling a spec gives a byte-identical result each time: `chartwright compile spec.json -o out.zip` writes the import ZIP offline, and the same spec always writes the same bytes ([tested](VERIFICATION.md)). Its dataset ids are placeholders, so you deploy with `apply`.

Changing a number format, a filter default and adding a filter reads like this:

```diff
-    {"name": "Revenue", "type": "big_number_total", "metric": "SUM(amount)", "number_format": "$,.0f",
+    {"name": "Revenue", "type": "big_number_total", "metric": "SUM(amount)", "number_format": "$,.2f",
      "dataset": {"database": "warehouse", "table": "orders"}},
     {"name": "Orders", "type": "big_number_total", "metric": "COUNT(*)", "number_format": ",.0f",
      "dataset": {"database": "warehouse", "table": "orders"}}
   ],
   "filters": [
-    {"type": "time_range", "name": "Order date", "default": "No filter"}
+    {"type": "time_range", "name": "Order date", "default": "Last quarter"},
+    {"type": "select", "name": "Region", "column": "region",
+     "dataset": {"database": "warehouse", "table": "orders"}}
   ],
```

If prod matches the old spec, `chartwright plan` in the pull request reports Revenue under changed charts, Order date under changed filters and Region under added filters. Renaming a chart gives it a new id, so `plan` shows the old name removed and the new one added, and `apply` deletes the old chart.

## 2. Add the workflow

Commit a profiles file such as `ci/profiles.toml`. It names each instance and the environment variable its password comes from, never the password itself ([every sign-in option](MOVE-BETWEEN-INSTANCES.md)):

```toml
[staging]
base_url = "https://superset-staging.example.com"
username = "chartwright-deploy"
password_env = "SUPERSET_STAGING_PASSWORD"

[prod]
base_url = "https://superset.example.com"
username = "chartwright-deploy"
password_env = "SUPERSET_PROD_PASSWORD"
```

Store each password as a repository secret (Settings, then Secrets and variables, then Actions) under the name its `password_env` gives. Each job below copies only the secrets it needs into its environment. Preset workspaces work the same way, with `api_token_env` and `api_secret_env` naming two secrets.

Put the specs in `dashboards/` and add this workflow:

```yaml
# .github/workflows/dashboards.yml
name: dashboards
on:
  pull_request:
    paths: ["dashboards/**", "ci/profiles.toml"]
  push:
    branches: [main]
    paths: ["dashboards/**", "ci/profiles.toml"]
  schedule:
    - cron: "0 5 * * *"        # nightly comparison with prod
  workflow_dispatch:

env:
  CHARTWRIGHT_PROFILES: ci/profiles.toml

jobs:
  review:
    if: github.event_name == 'pull_request'
    runs-on: ubuntu-latest
    env:
      SUPERSET_PROD_PASSWORD: ${{ secrets.SUPERSET_PROD_PASSWORD }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: "3.12"}
      - run: pip install "chartwright>=0.2"
      - name: Validate and review each spec (offline)
        run: |
          for spec in dashboards/*.json; do
            chartwright validate "$spec"
            chartwright advise --strict "$spec"
          done
      - name: Check each spec against prod and show what would change
        run: |
          for spec in dashboards/*.json; do
            chartwright check "$spec" --profile prod
            rc=0
            chartwright plan "$spec" --profile prod > plan.json || rc=$?
            { echo "### $spec"; echo '~~~json'; cat plan.json; echo '~~~'; } >> "$GITHUB_STEP_SUMMARY"
            if [ "$rc" -ne 0 ] && ! jq -e '.dashboard == "create" or .dashboard == "update"' plan.json > /dev/null; then
              echo "::error file=$spec::plan stopped; see the run summary"
              exit 1
            fi
          done

  deploy-staging:              # optional; delete this job and the needs: line below to deploy straight to prod
    if: github.event_name == 'push'
    runs-on: ubuntu-latest
    concurrency: dashboards-staging
    env:
      SUPERSET_STAGING_PASSWORD: ${{ secrets.SUPERSET_STAGING_PASSWORD }}
      CHARTWRIGHT_BACKUP_DIR: backups
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: "3.12"}
      - run: pip install "chartwright>=0.2"
      - run: for spec in dashboards/*.json; do chartwright apply "$spec" --profile staging --design strict; done
      - uses: actions/upload-artifact@v4
        if: always()
        with: {name: backups-staging, path: backups/, if-no-files-found: ignore}

  deploy-prod:
    if: github.event_name == 'push'
    needs: deploy-staging
    runs-on: ubuntu-latest
    concurrency: dashboards-prod
    env:
      SUPERSET_PROD_PASSWORD: ${{ secrets.SUPERSET_PROD_PASSWORD }}
      CHARTWRIGHT_BACKUP_DIR: backups
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: "3.12"}
      - run: pip install "chartwright>=0.2"
      - run: for spec in dashboards/*.json; do chartwright apply "$spec" --profile prod --design strict; done
      - uses: actions/upload-artifact@v4
        if: always()
        with: {name: backups-prod, path: backups/, if-no-files-found: ignore}

  drift:
    if: github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'
    runs-on: ubuntu-latest
    env:
      SUPERSET_PROD_PASSWORD: ${{ secrets.SUPERSET_PROD_PASSWORD }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: "3.12"}
      - run: pip install "chartwright>=0.2"
      - name: Compare prod with each spec
        run: |
          status=0
          for spec in dashboards/*.json; do
            chartwright plan "$spec" --profile prod > plan.json || status=1
            { echo "### $spec"; echo '~~~json'; cat plan.json; echo '~~~'; } >> "$GITHUB_STEP_SUMMARY"
          done
          exit "$status"
```

What each job does:

- **review**, on every pull request: `validate` confirms each spec's fields and layout and `advise --strict` reviews its design (step 4), both offline; then `check` confirms that the datasets, columns and metrics the spec names exist on prod, listing any that are missing, and `plan` writes what would change on prod to the run summary.
- **deploy-staging** (optional) and **deploy-prod**, on merge to main: apply each spec to staging, then, if that succeeds, to prod, keeping each backup as a run artifact (step 5).
- **drift**, nightly and on demand: compares prod with each spec (step 5).

`concurrency` sits only on the two deploy jobs, so a deploy waits for the one already running, and a pull request never cancels a deploy. Each loop stops at the first spec that fails; fix it, and the next run deploys the rest.

Before you commit the workflow, confirm it parses:

```bash
python -c 'import sys, yaml; yaml.safe_load(open(sys.argv[1]))' .github/workflows/dashboards.yml
```

## 3. Read the exit codes

The commands below print JSON. Those that sign in exit 1 on a profile or sign-in error, and every command exits 2 on a mistyped command or option. Otherwise:

| Command | Exits 0 when | Exits 1 when |
|---|---|---|
| `validate` | The spec's fields and layout are valid | Any field, layout or sketch error |
| `check` | All the datasets, columns and metrics the spec names exist | Any is missing (all are listed); design findings, with `--design strict` |
| `plan` | Nothing differs between prod and the spec | Anything differs; the dashboard doesn't exist yet; a reference is missing; the slug holds a dashboard Chartwright didn't create |
| `apply` | The dashboard is built and every chart's query ran | A step failed (the report's `stage` names it); design findings, with `--design strict` |
| `advise` | No error findings (and no warnings, with `--strict`) | Findings at that level |

Because `plan` exits 1 both when the dashboard would change and when it couldn't compare, the review job runs `check` first, which fails on sign-in, profile and missing-reference errors. It then accepts plan's exit 1 only when the report's `dashboard` field says `create` or `update`. A report with `blocked`, or with no `dashboard` field at all, fails the job. When `blocked` comes from the spec naming something prod doesn't have, the report lists it in `resolution_errors`, the same list `check` gives.

## 4. Gate on design

- `chartwright advise --strict spec.json` runs the design review offline and exits 1 on any error or warning; without `--strict`, only errors fail it.
- `chartwright apply --design strict` applies the same threshold and stops before it changes anything on the instance. `check --design strict` adds the gate to `check`.
- To keep a finding you chose deliberately, add its rule id to the spec's `design.ignore` list. The rules are in [DESIGN-BRAIN.md](DESIGN-BRAIN.md).

## 5. Catch edits made in the Superset UI

Run the drift job nightly. `plan` exits 1 when prod no longer matches a spec, so the job fails and the run summary shows what changed:

- charts and filters added, changed or removed in the UI;
- title, layout, cross-filtering and series-colour changes;
- filters and filter scopes overwritten by a browser tab left open on an older copy of the dashboard ([tested on all three versions](VERIFICATION.md)).

To undo those edits, re-run the deploy job or merge the next change: `apply` writes the spec back over them and keeps each chart's id. To keep an edit, copy it into the spec first; `chartwright absorb` does that for chart heights.

Each apply to an existing dashboard backs it up first. On a CI runner that backup would vanish with the runner, so the deploy jobs set `CHARTWRIGHT_BACKUP_DIR` and upload it as an artifact. To restore one, see [HISTORY-AND-ROLLBACK.md](HISTORY-AND-ROLLBACK.md).

## Limits

- `plan` compares the settings a spec can hold. A UI edit to anything else, such as dashboard CSS or chart annotations, isn't reported and the next apply resets it, so make lasting changes in the spec.
- A chart added to a managed dashboard in the UI shows in `plan` as removed. On Superset 4.1.4 and 5.0.0 the next apply then fails at its linkage step, and on 6.1.0 apply takes the chart off the dashboard: add charts in the spec, and remove UI-added ones from the dashboard in Superset.
- A spec names each dataset by its database connection, schema and table, so staging must use prod's names for the same workflow to deploy to both. Where names differ, keep a copy of the specs per instance and point each deploy job at its own folder.
- The backup artifacts hold dashboard, chart and dataset definitions, and anyone who can read the repository's workflow runs can download them; keep the repository private, or drop the upload steps and run deploys from a machine you control.
- Chartwright signs in with a Superset account's own password (database or LDAP login) or with Preset API tokens, never through SSO or OAuth. On an instance where people sign in through SSO, ask your admin for a deploy account that has a Superset password.

## Related

- [HISTORY-AND-ROLLBACK.md](HISTORY-AND-ROLLBACK.md): backups, automatic restore and `chartwright restore`
- [MOVE-BETWEEN-INSTANCES.md](MOVE-BETWEEN-INSTANCES.md): profiles, sign-in options and what must exist on each instance
- [VERIFICATION.md](VERIFICATION.md): how determinism, drift detection and repair are tested
- [CONTRACTS.md](CONTRACTS.md): the Superset behaviour `apply` and `plan` are built around, cited to source
- [LIMITS.md](LIMITS.md): every limit, with what to do instead
