# Move dashboards between instances

Keep one spec file per dashboard and build the same dashboard on dev, staging, prod or any other Superset instance from it. To move a dashboard someone built in the UI, generate its spec first, then build it on each instance. Before each move you can check the target without changing it, and each apply backs up the dashboard it replaces.

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
| Title, slug, layout, tabs and markdown text | The database connection, under the name the spec uses |
| Every chart and its settings | Each dataset, matched by connection name, table and, when the spec gives one, schema |
| The filter bar with its defaults and chart scopes, the cross-filter setting, series colours | The columns and saved metrics the spec names |

Chartwright reads datasets and connections and never creates them. Create them in Superset, or with the tooling you already use for them, before you apply. When a spec leaves out `schema`, a table found in two schemas of the same connection is reported as ambiguous; add the schema to the spec.

## 3. Check the target before you move

```bash
chartwright check orders.json --profile prod --design off
chartwright plan  orders.json --profile prod
```

`check` signs in, looks up every dataset, column and saved metric the spec names, and changes nothing (`--design off` leaves the design review out of its output). It exits 0 when everything resolves. Otherwise it exits 1 and lists every missing reference at once:

- a missing dataset comes with any tables of the same name in other connections or schemas, which usually points at a naming difference;
- a missing saved metric comes with the metrics the dataset does have;
- a missing column comes with the dataset's column count, not its column names, so open the dataset in Superset to see them.

`plan` shows what an apply would change on that instance: `create` when the dashboard isn't there yet, `blocked` when the slug belongs to a dashboard Chartwright didn't build, and otherwise the charts and filters added, changed and removed, plus title, layout, cross-filter and series-colour changes, including edits someone made in the UI there. It exits 0 when there's nothing to change and 1 otherwise. For a dashboard that doesn't exist yet, `plan` skips the reference lookup, so run `check` first.

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
| resolve | Each dataset, column and saved metric the spec names is looked up on this instance. If any are missing, all of them are listed and nothing is written |
| ownership | The apply stops if the slug belongs to a dashboard Chartwright didn't build ("Pick a different slug") |
| prepare | If the dashboard exists, it's exported to a backup file first, including any edits made in the UI. Charts Chartwright built that have left the spec are deleted, even if someone also added them to another dashboard |
| import | New charts are created. Charts Chartwright built before are updated in place and keep their ids |
| linkage | The charts on the dashboard are compared with the spec, because Superset's import can report success with charts missing |
| scope | Filters limited to named charts are pointed at those charts' ids on this instance |
| smoke | Every chart's query runs. An error fails the apply; a chart that returns no rows is a warning naming it |

- If anything fails during the prepare or import stage, including the in-place chart updates, the backup is restored automatically, whatever the error.
- If linkage, scope or smoke fails, the new version stays live. Run `chartwright restore <backup.zip> --profile prod` to put the backup back, chart settings and filter scopes included.
- Backups are saved at `~/.config/chartwright/backups/<profile>/<slug>/<timestamp>.zip`, or under the folder `CHARTWRIGHT_BACKUP_DIR` names.

Ownership comes from the slug alone: the dashboard's uuid is derived from its slug, and each chart's from the slug and the chart's name. Two specs with the same slug manage the same dashboard, so give each team its own slug prefix.

## Move a dashboard made in the UI

1. Generate its spec on the instance where it lives: `chartwright decompile regional-sales --profile dev -o regional-sales.json` (the slug or numeric id). Read the `losses` list it prints.
2. Change `dashboard.slug` in the spec, for example to `regional-sales-v2`. Applying at the original slug is refused, because Chartwright didn't build that dashboard. Use the new slug on every instance.
3. Run `chartwright validate regional-sales.json`, then `check` against each target. If connection names differ, generate a copy per instance as in step 4.
4. Apply it to each instance: `--profile dev`, then `staging`, then `prod`.
5. On dev, compare the new dashboard with the original. Then point bookmarks, links and scheduled reports at the new one, and delete the original in Superset.

What `decompile` carries over, what it lists as lost, and the settings it drops without a note: [CLONE-A-DASHBOARD.md](CLONE-A-DASHBOARD.md#start-from-a-dashboard-you-already-have).

## Compared with re-importing ZIP exports

The first three Superset facts are cited to source in [CONTRACTS.md](CONTRACTS.md), under "When a bundle is imported" and "Differences the tool absorbs".

- **Chart changes arrive.** Up to Superset 6.1.0, re-importing a dashboard export updates the dashboard but leaves charts that already exist on the target unchanged ([#34879](https://github.com/apache/superset/issues/34879), fixed after 6.1.0 and not yet released). Chartwright updates the charts it built through Superset's chart API, so new settings land and chart ids stay the same.
- **Missing charts are caught.** Superset 6.1.0 skips a chart whose dataset isn't in the bundle and still reports success. Chartwright adds the target's own dataset records to every import, then checks that every chart in the spec is on the dashboard.
- **Older releases are reachable.** A Superset 6.1.0 export doesn't import into 4.1.4 or 5.0.0. Chartwright builds from the spec and the target's own dataset records, and CI applies the same specs to all three releases ([SUPERSET-VERSIONS.md](SUPERSET-VERSIONS.md)).
- **There's a copy to go back to.** Superset 6.1.0 has no version history for dashboards (it's on Superset's master branch, not yet released). Chartwright saves a backup before every apply.

## Limits

| What's true today | What to do instead |
|---|---|
| Chartwright signs in with a Superset account's own password, through database or LDAP login, or with Preset API tokens, never through SSO or OAuth | On an instance where people sign in through SSO, ask your admin for an account that has a Superset password |
| CI's live runs sign in with a database login; LDAP and Preset sign-in aren't part of them | Run `chartwright check` against the instance first: it signs in and only reads |
| Datasets and connections must already exist on the target | Create them in Superset, or with your existing tooling, before the first apply |
| Connection and schema names are matched exactly, with no per-instance mapping | Generate a copy of the spec per instance, as in step 4 |
| A dashboard Chartwright didn't build, whether made in the UI or imported from an export of one, can't be updated at its current slug | Decompile it and build it at a new slug, as above |
| Every apply overwrites edits made in the UI on that instance, and resets dashboard CSS, colour scheme and refresh interval | Run `plan` first to see the edits, then copy the ones to keep into the spec; `chartwright absorb` copies chart heights set in the UI (heights only) |
| A chart added in the UI to a dashboard Chartwright manages makes `apply` fail at linkage on 4.1.4 and 5.0.0; on 6.1.0 the chart is taken off the dashboard | Add new charts to the spec |
| One dashboard per spec and per command | Loop over a folder of specs ([DEPLOY-FROM-GIT.md](DEPLOY-FROM-GIT.md)) |
| Backups are kept per profile name, not per instance | Keep one profile name per instance, and restore with the profile that made the backup |

## Related

- [CLONE-A-DASHBOARD.md](CLONE-A-DASHBOARD.md): decompile in detail, and one copy per team or tenant
- [DEPLOY-FROM-GIT.md](DEPLOY-FROM-GIT.md): `check` and `plan` on pull requests, `apply` on merge
- [HISTORY-AND-ROLLBACK.md](HISTORY-AND-ROLLBACK.md): backups, `restore` and checking for UI edits
- [SUPERSET-VERSIONS.md](SUPERSET-VERSIONS.md): one spec on 4.1.4, 5.0.0 and 6.1.0
- [CONTRACTS.md](CONTRACTS.md): Superset's import behaviour, cited to source
- [VERIFICATION.md](VERIFICATION.md): how it's tested
