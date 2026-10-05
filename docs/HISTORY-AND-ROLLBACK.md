# Back up and roll back dashboards

Roll a dashboard back to how it was before any apply, including its charts' settings and its filter scopes. Every `chartwright apply` to a dashboard that already exists first saves the live dashboard as a ZIP, with no step on your part, and `chartwright restore` puts that ZIP back.

## 1. Find the backup

Each backup is saved at:

```text
~/.config/chartwright/backups/<profile>/<slug>/<timestamp>.zip
```

- The timestamp is the local time of the apply, to the microsecond, such as `20261003T141502.123456.zip`, so the newest backup sorts last and no backup is ever overwritten.
- Beside each ZIP, a `<timestamp>.json` record names the instance it came from (its address), the profile, the slug and the dashboard id. Keep the two together: `restore` reads the record to check the instance.
- `apply` keeps the newest 50 backups of each dashboard per profile and deletes older ones, each ZIP with its record. Set `CHARTWRIGHT_BACKUP_KEEP` to keep more, or to 0 to keep them all.
- Set `CHARTWRIGHT_BACKUP_DIR` to keep backups somewhere else; the `<profile>/<slug>/` folders are added under it.
- The JSON report from `apply` gives the file's path in its `backup` field.
- On macOS and Linux the default folder is readable only by you. If you set `CHARTWRIGHT_BACKUP_DIR`, protect that folder yourself: the ZIPs hold dashboard, chart and dataset definitions.

`apply` saves the backup after it has confirmed every reference and that it owns the dashboard, and before it changes anything. The backup holds the dashboard exactly as it was live, including any edits made in the Superset UI since the last apply, so you can get back a UI edit that an apply overwrote.

## 2. Know when apply restores it for you

`apply` runs in steps: resolve, ownership, prepare, import, linkage, scope, owners and smoke. When a step fails, `apply` exits 1 and its report's `stage` names the step. Whether the backup goes back on its own depends on the step:

| What fails | What's live afterwards |
|---|---|
| Superset refuses the import | The backup, restored automatically |
| Anything fails while charts that left the spec are deleted, while datasets are read, or during the import, whether Superset rejects a request, the connection drops or Chartwright hits a bug | The backup, restored automatically |
| Superset rejects an update to a chart that already existed, or the connection drops during it | The backup, restored automatically |
| The linkage step finds charts missing from the dashboard, or can't take off a chart the spec doesn't name (one added in the UI) | The new state; restore the backup by hand |
| Filter scopes or the spec's owners can't be set, or a chart's query fails in the smoke step | The new state; restore the backup by hand |

An automatic restore is noted in the report's `warnings`; it also deletes any chart the failed apply had just created. If the automatic restore fails too, the warning names the backup to pass to `chartwright restore`.

## 3. Restore a backup by hand

```bash
ls ~/.config/chartwright/backups/prod/orders/
chartwright restore ~/.config/chartwright/backups/prod/orders/20261003T141502.123456.zip --profile prod
```

`restore` does more than re-import the ZIP:

- It confirms the backup holds a dashboard Chartwright built, or is one of its own backups in that profile's backup folder (which covers a dashboard you adopted), and refuses any other ZIP.
- It reads the record beside the ZIP and refuses a backup taken on another instance than the profile points at, naming both. Pass `--to-other-instance` to restore it there on purpose, for example onto a rebuilt instance at a new address. A backup with no record (from before 0.5.0) restores with a warning that the instance couldn't be checked.
- It imports the dashboard: its title, layout and filters as they were, linked to exactly the backup's charts.
- It writes each chart's settings and saved query back from the backup and points the chart at its backed-up dataset. Superset 6.1.0's importer leaves charts that already exist unchanged ([CONTRACTS.md](CONTRACTS.md), "When a bundle is imported"; [#34879](https://github.com/apache/superset/issues/34879), fixed after 6.1.0, not yet released), so a plain re-import brings back the dashboard but not its charts' settings.
- It deletes the charts Chartwright created after the backup that end up on no dashboard, and names any other chart it took off.
- It puts filters that apply to named charts back onto those charts.
- It prints a JSON report listing the charts it restored, and exits 0 on success and 1 on failure, naming any chart whose settings it couldn't put back.

The [fault-injection tests](VERIFICATION.md) alter charts' settings and filter scopes, restore the backup, then require `plan` against the old spec to come back clean.

## 4. Then revert the spec

`restore` changes the dashboard in Superset, not your spec, so the next `apply` would build the newer version again. Revert the spec in git to match:

```bash
git revert <commit>                                   # or: git checkout <commit> -- dashboards/orders.json
chartwright plan dashboards/orders.json --profile prod
```

`plan` exits 0 when the restored dashboard matches the reverted spec. If it reports differences, the backup most likely held UI edits the spec never had: copy them into the spec, or apply the spec to replace them. When CI deploys your specs ([DEPLOY-FROM-GIT.md](DEPLOY-FROM-GIT.md)), merging the revert applies the reverted spec, which also rolls the dashboard back, rebuilt from the spec rather than from the backup.

## 5. Undo an edit made in the Superset UI

To undo a UI edit made since the last apply, apply the spec again:

```bash
chartwright plan dashboards/orders.json --profile prod     # shows what the UI edit changed
chartwright apply dashboards/orders.json --profile prod    # writes the spec back over it
```

Backups are taken only when `apply` runs, so no backup holds the state from before a UI edit. The spec does, and that apply also backs up the edited dashboard first, in case you want the edit back.

## Compared with re-importing a Superset export

- In Superset 6.1.0, re-importing an export updates the dashboard but leaves its existing charts' settings unchanged (#34879 above). `restore` writes them back.
- Superset 6.1.0 keeps no copy of a dashboard before it changes; you export by hand first. Chartwright saves one before every apply to an existing dashboard. Superset's master branch adds version history and soft delete, not yet in a release.
- Re-importing an export after deleting its assets can fail ([#44309](https://github.com/apache/superset/issues/44309), open). `restore` imports through the same importer and may fail the same way when a dataset the backup's charts read has been deleted; recreate the dataset in Superset and apply the spec instead.

## Limits

- A backup covers one dashboard and its charts. To back up a whole instance, back up Superset's metadata database.
- The first apply at a slug saves no backup, since there was nothing to save; to undo it, delete the dashboard and its charts in Superset.
- `restore` deletes only the charts Chartwright created that it leaves on no dashboard. A chart made in Superset, or one that also sits on another dashboard, stays; the report names it, and you can delete it in Superset's chart list.
- `restore` saves no backup of the state it replaces; to return to the spec's version, apply the spec.
- A backup taken on Superset 6.1.0 doesn't import on 4.1.4 or 5.0.0 ([CONTRACTS.md](CONTRACTS.md)); after a downgrade, apply the spec instead.
- A backup written on a CI runner disappears with the runner; upload it as an artifact with its record, as the [deploy workflow](DEPLOY-FROM-GIT.md) does, and fetch it with `gh run download <run-id> -n backups-prod -D backups`. For a dashboard you adopted, restore it with `CHARTWRIGHT_BACKUP_DIR=backups` set, so the downloaded ZIP counts as one of Chartwright's own backups.
- The MCP server has no restore tool; run `chartwright restore` from the command line.

## Related

- [DEPLOY-FROM-GIT.md](DEPLOY-FROM-GIT.md): review changes in pull requests, deploy from CI and compare prod with the specs nightly
- [MOVE-BETWEEN-INSTANCES.md](MOVE-BETWEEN-INSTANCES.md): profiles, and the steps `apply` runs
- [VERIFICATION.md](VERIFICATION.md): how backups, automatic restore and `restore` are tested
- [CONTRACTS.md](CONTRACTS.md): Superset's importer behaviour, cited to source
- [LIMITS.md](LIMITS.md): every limit, with what to do instead
