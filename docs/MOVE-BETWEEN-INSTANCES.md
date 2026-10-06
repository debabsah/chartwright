# Move dashboards between instances

Keep one spec file per dashboard and build the same dashboard on dev, staging, prod or any other Superset instance from it. To move a dashboard someone built in the UI, adopt it first ([below](#take-over-a-dashboard-made-in-the-ui)), then build it on each instance. Before each move you can check the target without changing it, and each apply backs up the dashboard it replaces.

## 1. Add a profile for each instance

Profiles live in `~/.config/chartwright/profiles.toml`, or in the file the `CHARTWRIGHT_PROFILES` environment variable names. A profile gives the instance's address and says where its password comes from. The file holds no passwords, tokens or secrets, so you can commit it next to your specs.

```toml
[dev]
base_url = "https://superset-dev.example.com"
username = "chartwright"
password_env = "SUPERSET_DEV_PASSWORD"

[staging]
base_url = "https://superset-staging.example.com"
username_env = "SUPERSET_DEPLOY_USER"
password_env = "SUPERSET_STAGING_PASSWORD"
password_cmd = ["op", "read", "op://Work/superset-staging/password"]

[prod]
base_url = "https://superset.example.com"
username_env = "SUPERSET_DEPLOY_USER"
password_env = "SUPERSET_PROD_PASSWORD"
auth_provider = "ldap"
ca_bundle = "/etc/ssl/certs/corp-root-ca.pem"
```

Pass the profile name to every command that talks to Superset: `--profile staging`.

| To sign in with | Put in the profile |
|---|---|
| A password from an environment variable | `password_env`, naming any variable you already use |
| A password from your password manager | `password_cmd`, a command that prints the password; the list form runs without a shell, and `~` is expanded |
| Both | Set both, as `staging` does: the variable is used when it's set and the command runs otherwise, so CI can export the variable while laptops read the password manager |
| A username from the environment | `username_env` in place of `username` |
| LDAP | `auth_provider = "ldap"` (the default, `"db"`, is Superset's database login) |
| Preset API tokens | `api_token_env` and `api_secret_env`, naming the variables that hold the token and the secret; `base_url` is the workspace address |
| The login preset-cli already saved | `preset_credentials_file`, the path to the `credentials.yaml` preset-cli wrote |
| A corporate root certificate | `ca_bundle`, the path to the certificate file (the `REQUESTS_CA_BUNDLE` variable works too) |
| A proxy | The standard `HTTPS_PROXY`, `HTTP_PROXY` and `NO_PROXY` environment variables, which every request follows |

A Preset profile looks like this:

```toml
[preset]
base_url = "https://<workspace>.<region>.app.preset.io"
api_token_env = "PRESET_API_TOKEN"
api_secret_env = "PRESET_API_SECRET"
```

If a sign-in fails, the error names the cause: an unset variable, a failed command, a certificate problem (with the `ca_bundle` hint), a proxy error, or a login page from an SSO portal or proxy answering in place of Superset's login API.

## 2. Create the data on the target

A spec carries the dashboard. The data it reads stays in Superset, so each instance needs it before the first move.

| Moves with the spec | Must already exist on the target |
|---|---|
| Title, slug, layout, tabs, header and footer rows, and markdown text | The database connection, under the name the spec uses |
| Every chart and its settings | Each dataset, matched by connection name, table and, when the spec gives one, schema |
| The filter bar with its defaults and chart scopes, the cross-filter setting, series colours | The columns and saved metrics the spec names |
| Dashboard CSS, colour scheme, description, certification, published or draft, refresh interval and tags | The owners the spec names, as accounts on the target, and the theme it names (Superset 6.0 and later) |

Chartwright reads datasets and connections and never creates them. Create them in Superset, or with the tooling you already use for them, before you apply. When a spec leaves out `schema`, a table found in two schemas of the same connection is reported as ambiguous; add the schema to the spec.

## 3. Check the target before you move

```bash
chartwright check orders.json --profile prod --design off
chartwright plan  orders.json --profile prod
```

`check` signs in, looks up every dataset, column and saved metric the spec names, and changes nothing (`--design off` leaves the design review out of its output). It exits 0 when everything resolves. Otherwise it exits 1 and lists every missing reference at once:

- a missing dataset comes with any tables of the same name in other connections or schemas, which usually points at a naming difference;
- a missing saved metric comes with the metrics the dataset does have;
- a missing column comes with up to three close matches, such as `region` for `Region`, and the dataset's columns.

`plan` shows what an apply would change on that instance: `create` when the dashboard isn't there yet, `blocked` when the slug belongs to a dashboard Chartwright didn't build or the spec names something the instance doesn't have or can't take, and otherwise the charts and filters added, changed and removed, plus title, layout, cross-filter, series-colour, CSS and dashboard-setting changes, including edits someone made in the UI there. It exits 0 when there's nothing to change and 1 otherwise. For a dashboard that doesn't exist yet, `plan` skips the reference lookup, so run `check` first.

## 4. When connection names differ between instances

A spec names each connection literally, and Chartwright has no per-instance name mapping. Where prod calls the connection something else, give prod its own copy of the spec, generated from the one you edit:

```bash
jq '(.. | objects | select(has("table")) | select(.database == "warehouse") | .database) = "warehouse_prod"' \
  orders.json > orders.prod.json
chartwright apply orders.prod.json --profile prod
```

This renames the connection in every chart's and filter's `dataset` block and leaves the rest alone. For schema names, run the same command with `.schema` in both places. Keep the slug the same in every copy, so each instance has one dashboard at the same address. Generate the copy in your deploy job, or commit it and change both files in the same pull request.

## 5. Apply

```bash
chartwright apply orders.json --profile staging
chartwright apply orders.json --profile prod
```

The first apply on an instance creates the dashboard; each later apply of a spec with that slug updates it. `apply` runs these stages in order and stops at the first one that fails. Its JSON report names the stage and the backup file.

| Stage | What happens |
|---|---|
| resolve | Each dataset, column and saved metric the spec names is looked up on this instance, as are the owners and theme it names, and every field is checked against the instance's Superset release. If anything is missing or can't be taken, all of it is listed and nothing is written |
| ownership | The apply stops if the slug belongs to a dashboard Chartwright didn't build or you didn't adopt |
| prepare | If the dashboard exists, it's exported to a backup file first, including any edits made in the UI. Charts Chartwright built that have left the spec are deleted, even if someone also added them to another dashboard |
| import | New charts are created. Charts Chartwright built before are updated in place and keep their ids |
| linkage | A chart the spec doesn't name, such as one added in the UI, is taken off the dashboard; then the charts on it are compared with the spec, because Superset's import can report success with charts missing |
| scope | Filters limited to named charts are pointed at those charts' ids on this instance |
| owners | The owners the spec names are set, when it names any |
| smoke | Every chart's query runs. An error fails the apply; a chart that returns no rows is a warning naming it |

- If anything fails during the prepare or import stage, including the in-place chart updates, the backup is restored automatically, whatever the error.
- If linkage, scope, owners or smoke fails, the new version stays live. Run `chartwright restore <backup.zip> --profile prod` to put the backup back, chart settings and filter scopes included.
- Backups are saved at `~/.config/chartwright/backups/<profile>/<slug>/<timestamp>.zip`, or under the folder `CHARTWRIGHT_BACKUP_DIR` names, each with a record of its instance beside it; the newest 50 of each dashboard are kept ([HISTORY-AND-ROLLBACK.md](HISTORY-AND-ROLLBACK.md)).

Ownership comes from the slug: the dashboard's uuid is derived from its slug, and each chart's from the slug and the chart's name, unless the spec adopted a dashboard (below). Two specs with the same slug manage the same dashboard, so give each team its own slug prefix.

## Take over a dashboard made in the UI

Adopt it on the instance where it lives. The spec that `adopt` writes names the dashboard and its charts by their own ids, so applying it updates the same dashboard: its address, id and chart ids stay, and links and embeds keep pointing at it.

1. Run `chartwright adopt regional-sales --profile dev -o regional-sales.json` (the slug or numeric id). It changes nothing in Superset.
    - If the first apply would reset settings a spec can't hold, such as a forecast on a chart or the dashboard-wide cross-filter scope, it lists each under `resets` and refuses. For a setting you need, rework the chart with fields the spec has (`chartwright schema` lists them), or leave the dashboard unadopted; pass `--accept-reset` to accept the rest.
    - If some charts also sit on other dashboards, it refuses, since applying changes them there too: give those dashboards their own copies in Superset, or pass `--allow-shared`.
2. Run `chartwright plan regional-sales.json --profile dev`. It names each chart whose stored settings the first apply rewrites, under `chart_option_changes`.
3. Run `chartwright apply regional-sales.json --profile dev`. It backs the dashboard up first, as always.

The adopted spec names the dashboard by its own id, so it applies wherever that dashboard is: on dev, and on any instance it reached through a Superset export. Where the slug is free, write a copy without the `dashboard.adopted` block and apply that copy, as in section 5; that instance gets a dashboard of its own at the same slug. The copy keeps the spec's `owners` and `theme`, so those accounts and that theme must exist there too, or edit them first:

```bash
jq 'del(.dashboard.adopted)' regional-sales.json > regional-sales.copy.json
chartwright apply regional-sales.copy.json --profile staging
```

To build a separate copy beside the original instead, decompile it ([CLONE-A-DASHBOARD.md](CLONE-A-DASHBOARD.md#start-from-a-dashboard-you-already-have)), which also lists what `decompile` carries over and what it can't.

## Compared with re-importing ZIP exports

The first three Superset facts are cited to source in [CONTRACTS.md](CONTRACTS.md), under "When a bundle is imported" and "Differences the tool absorbs".

- **Chart changes arrive.** Up to Superset 6.1.0, re-importing a dashboard export updates the dashboard but leaves charts that already exist on the target unchanged ([#34879](https://github.com/apache/superset/issues/34879), fixed after 6.1.0 and not yet released). Chartwright updates the charts it built through Superset's chart API, so new settings land and chart ids stay the same.
- **Missing charts are caught.** Superset 6.1.0 skips a chart whose dataset isn't in the bundle and still reports success. Chartwright adds the target's own dataset records to every import, then checks that every chart in the spec is on the dashboard.
- **Older releases are reachable.** A Superset 6.1.0 export doesn't import into 4.1.4 or 5.0.0. Chartwright builds from the spec and the target's own dataset records, and CI applies the same specs to all three releases; a field an older release can't take, such as tags or a theme before 6.0, is refused before anything is written ([SUPERSET-VERSIONS.md](SUPERSET-VERSIONS.md)).
- **There's a copy to go back to.** Superset 6.1.0 has no version history for dashboards (it's on Superset's master branch, not yet released). Chartwright saves a backup before every apply.

## Limits

| What's true today | What to do instead |
|---|---|
| Chartwright signs in with a Superset account's own password, through database or LDAP login, or with Preset API tokens, never through SSO or OAuth | On an instance where people sign in through SSO, ask your admin for an account that has a Superset password |
| CI's live runs sign in with a database login; LDAP and Preset sign-in aren't part of them | Run `chartwright check` against the instance first: it signs in and only reads |
| Datasets and connections must already exist on the target | Create them in Superset, or with your existing tooling, before the first apply |
| Connection and schema names are matched exactly, with no per-instance mapping | Generate a copy of the spec per instance, as in step 4 |
| The first apply of an adopted dashboard resets what a spec can't hold | `chartwright adopt` lists every such reset first and refuses until you pass `--accept-reset`, as above |
| Every apply overwrites edits made in the UI on that instance, dashboard CSS and settings included | Run `plan` first to see the edits, then copy the ones to keep into the spec; `chartwright absorb` copies chart heights set in the UI (heights only) |
| A chart added in the UI to a dashboard Chartwright manages is taken off it by the next apply (the chart stays in Superset's Charts list) | Add new charts to the spec |
| One dashboard per spec, and one spec per command (the `standards` commands take a folder) | Loop over a folder of specs ([DEPLOY-FROM-GIT.md](DEPLOY-FROM-GIT.md)) |
| A backup restores only onto the instance it came from | Pass `--to-other-instance` to `restore` to put it onto another one on purpose |

## Related

- [CLONE-A-DASHBOARD.md](CLONE-A-DASHBOARD.md): decompile in detail, and one copy per team or tenant
- [DEPLOY-FROM-GIT.md](DEPLOY-FROM-GIT.md): `check` and `plan` on pull requests, `apply` on merge
- [HISTORY-AND-ROLLBACK.md](HISTORY-AND-ROLLBACK.md): backups, `restore` and checking for UI edits
- [SUPERSET-VERSIONS.md](SUPERSET-VERSIONS.md): one spec on 4.1.4, 5.0.0 and 6.1.0
- [CONTRACTS.md](CONTRACTS.md): Superset's import behaviour, cited to source
- [VERIFICATION.md](VERIFICATION.md): how it's tested
