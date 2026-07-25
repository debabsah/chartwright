# How Chartwright Compares

Chartwright is one of several command-line tools for managing Apache Superset assets. They overlap less than the category name suggests: each automates a different job, and most shops could reasonably run more than one. This page states what each tool does so you can pick by the job in front of you.

Facts checked 2026-07-24 against each project's public README and docs. If something here has gone stale, open an issue and it will be corrected.

## The short version

| Tool | Where a dashboard starts | What gets checked | Built for |
|---|---|---|---|
| [Chartwright](https://github.com/debabsah/chartwright) | a spec you (or an AI) write: a small JSON description of the dashboard you want | every dataset, column, and metric confirmed to exist before anything is built; every chart compared to the spec and its query run once after; drift diff on demand | authoring dashboards as reviewable code on open-source Superset |
| [preset-cli / superset-cli](https://github.com/preset-io/backend-sdk) | the Superset UI; you export the built dashboard as YAML and optionally add Jinja templates | warns when target files already exist (pass `--overwrite` to replace); `delete-assets` dry-runs by default | syncing exported assets and dbt projects into Preset workspaces or standalone Superset |
| [sup](https://github.com/preset-io/superset-sup) | the Superset UI; sync configs point at source and target workspaces | `sync run --dry-run` previews the sync operations | power-user and agent workflows: SQL from the terminal, asset search, backup and restore, cross-workspace sync (beta, per its README) |
| [terraform-provider-superset](https://github.com/platacard/terraform-provider-superset) | Terraform HCL | Terraform's own plan and apply lifecycle | databases, datasets, roles, and users; dashboards and charts are not among its resources |

## The deepest difference: which way the dashboard flows

With preset-cli and sup, a dashboard begins life in the Superset UI. Export and sync then carry that finished dashboard somewhere else: into git, into another workspace, into production, with Jinja templating to parametrize it along the way. The UI is the editor; the files are the transport.

With Chartwright, the flow runs the other way. The spec is the editor: a person or an AI writes a short description of the dashboard, and `chartwright apply` builds it. The UI is where people consume the result (and where hand-polish like a dragged chart height can be pulled back into the spec with `chartwright absorb`). Files built by either flow use Superset's standard import format underneath, so nothing here locks you in.

Neither direction is better in general. If your dashboards are built by analysts in the UI and your problem is promoting them between environments, the export-and-sync tools do that job today. If your problem is that dashboards should be written, reviewed, and verified like code, that is the job Chartwright was built for.

## What "verified" means in Chartwright

The export-and-sync tools check transport concerns: existing targets, previewable operations. Chartwright's job is compilation, so what it verifies is content, at three points:

- **Before writing:** `chartwright check` signs in read-only and confirms every dataset, column, and metric the spec names actually exists. A misspelled column is a clear error before anything is created, which matters most when an AI wrote the spec.
- **After writing:** `chartwright apply` compares the finished dashboard chart-by-chart against the spec and runs each chart's query once to prove it loads data.
- **On demand:** `chartwright plan` diffs the spec against the live dashboard, field by field: charts, filters, scopes, title, layout. It passes when they match and fails when they drifted, ready as a CI gate.

`chartwright decompile` closes the loop from the other side: it turns a dashboard built in the UI into a spec, listing anything it could not carry over.

## Tool notes

**preset-cli / superset-cli** is maintained by Preset, the company behind much of Superset's development. Beyond native asset sync it exports RLS rules, roles, users, and ownership, and can build datasets straight from a dbt Core or dbt Cloud project. It runs against Preset workspaces (`preset-cli`) or any standalone Superset (`superset-cli`).

**sup** is Preset's newer CLI, self-described as beta, with a strong terminal experience: run SQL against any workspace database, search charts and datasets server-side, export chart data, back up and restore assets with dependency tracking, and machine-readable output modes aimed at scripts and AI agents.

**terraform-provider-superset** brings the databases-datasets-roles-users layer of a Superset instance under Terraform. If your platform team already lives in Terraform, it can own that layer while a dashboard tool owns the dashboards.

## Using them together

The tools compose because they hold different ends of the stack:

- Manage connections, roles, and users with Terraform; author the dashboards on top of them as Chartwright specs.
- Sync dataset definitions from your dbt project with preset-cli; point Chartwright specs at those datasets.
- Keep ad-hoc SQL, asset search, and scheduled backups on sup; keep the dashboards you want under PR review on Chartwright specs, with `plan` as the merge gate.
- Migrating instances? `superset-cli export-assets` moves everything wholesale; `chartwright decompile` adopts the dashboards you want to manage as code from then on.
