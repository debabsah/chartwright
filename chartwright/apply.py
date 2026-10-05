"""Orchestration: check -> prepare -> compile -> import -> linkage -> smoke.

Superset importer facts this flow is built around (verified live, 6.1.0):
- charts import ONLY if their dataset_uuid maps to a datasets/ file in the
  bundle, so referenced datasets are round-tripped from the target at apply
  time (read-only copies; existing datasets are never overwritten on import);
- the importer never overwrites existing charts (only the dashboard), so
  spec-owned charts are deleted before import to make edits propagate.
"""

from __future__ import annotations

import copy
import io
import json
import zipfile
from dataclasses import asdict, dataclass, field
from uuid import uuid5

import yaml

from . import ids
from .client import SupersetClient, SupersetAPIError
from .compiler import compile_bundle
from .resolver import Resolution, resolve
from .smoke import SmokeResult, smoke
from .spec import DashboardSpec


@dataclass
class ApplyReport:
    ok: bool
    stage: str  # resolve | ownership | prepare | import | linkage | scope | owners | smoke | done
    resolution_errors: list[dict] = field(default_factory=list)
    import_status: int | None = None
    import_detail: str | None = None
    dashboard_id: int | None = None
    dashboard_url: str | None = None
    backup: str | None = None
    smoke_results: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # The Superset release the spec was held to, and the fields it ignores
    # (chartwright.versions); set only when the spec uses a version-gated field.
    superset_version: str | None = None
    version_warnings: list[dict] = field(default_factory=list)
    # Custom SQL resolve could not check by name (Resolution.unchecked_sql); the
    # data check runs it with the profile's rights, so the report says which.
    unchecked_sql: list[dict] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


def check(spec: DashboardSpec, client: SupersetClient, superset_version: str | None = None) -> Resolution:
    return resolve(spec, client, superset_version)


def _live_dashboard_uuid(client: SupersetClient, existing: dict) -> str:
    # The list endpoint doesn't return uuid; fetch detail.
    detail = client.get(f"/api/v1/dashboard/{existing['id']}")["result"]
    if detail.get("uuid"):
        return str(detail["uuid"])
    # Superset 4.x omits uuid from the detail API; the export bundle is
    # authoritative (without this, apply can't recognize its own dashboard
    # on 4.x and every re-apply is refused).
    zf = zipfile.ZipFile(io.BytesIO(client.export_dashboard(existing["id"])))
    for n in zf.namelist():
        if "/dashboards/" in n and n.endswith(".yaml"):
            return str(yaml.safe_load(zf.read(n)).get("uuid"))
    return ""


def _ownership_guard(spec: DashboardSpec, client: SupersetClient) -> str | None:
    """Refuse to overwrite a dashboard at our slug that we did not create.
    uuid5-owned objects are safe to overwrite; anything else is someone's work,
    unless the spec adopted that exact dashboard (`chartwright adopt`). An adopted
    spec also needs its dashboard AT its slug: Superset's importer matches by uuid,
    so without this check it would overwrite the dashboard wherever it now lives,
    with no backup taken. Messages never print uuids (nothing to paste into a spec)."""
    slug = spec.dashboard.slug
    existing = client.find_dashboard_by_slug(slug)
    adopted = spec.dashboard.adopted
    if existing is None:
        if adopted:
            return (f"the dashboard this spec adopted is no longer at {slug!r} (renamed, moved or "
                    f"deleted); nothing was changed. Run `chartwright adopt` on it where it is now.")
        return None
    if _live_dashboard_uuid(client, existing) != str(spec.dashboard_uuid()):
        if adopted:
            return (f"the dashboard at {slug!r} (id={existing['id']}) is not the one this spec "
                    f"adopted; refusing to overwrite it. Run `chartwright adopt` on the dashboard "
                    f"you mean.")
        return (f"dashboard slug {slug!r} exists (id={existing['id']}), but this tool did not "
                f"create it; refusing to overwrite. Pick a different slug, or take it over with "
                f"`chartwright adopt`.")
    return None


def adopted_live_charts(client: SupersetClient, dashboard_id: int,
                        export: bytes) -> tuple[dict[str, dict], list[str]]:
    """uuid -> {id, slice_name} for every chart on the dashboard, joining the export
    (uuid by title) with the dashboard's chart list (id by title). Titles that
    repeat can't be joined; they come back in the second list."""
    zf = zipfile.ZipFile(io.BytesIO(export))
    uuids: dict[str, list[str]] = {}
    for n in zf.namelist():
        if "/charts/" in n and n.endswith(".yaml"):
            cy = yaml.safe_load(zf.read(n)) or {}
            uuids.setdefault(cy.get("slice_name"), []).append(str(cy.get("uuid")))
    rows: dict[str, list[dict]] = {}
    for c in client.dashboard_charts(dashboard_id):
        rows.setdefault(c["slice_name"], []).append(c)
    found, ambiguous = {}, []
    for name, rs in rows.items():
        us = uuids.get(name, [])
        if len(rs) == 1 and len(us) == 1:
            found[us[0]] = rs[0]
        else:
            ambiguous.append(name)
    return found, sorted(ambiguous)


def charts_on_other_dashboards(client: SupersetClient, dashboard_id: int,
                               charts: list[dict]) -> list[str]:
    """Titles of the given charts that also sit on a dashboard other than this one."""
    return sorted(
        c["slice_name"] for c in charts
        if any(d.get("id") != dashboard_id
               for d in client.get(f"/api/v1/chart/{c['id']}")["result"].get("dashboards") or []))


def scoped_filter_fixup(
    metadata: dict, spec: DashboardSpec, name_to_id: dict[str, int]
) -> tuple[dict, list[str]]:
    """Rewrite native-filter scopes for spec filters with a `charts` list.

    Slice ids don't exist at compile time, so the bundle ships every filter
    scoped to ROOT; this runs after import, when ids are known. Filters are
    matched by their deterministic uuid5 id. Pure: returns (new metadata,
    errors) and mutates nothing.
    """
    errors: list[str] = []
    scoped = {f.name: f.charts for f in spec.filters if getattr(f, "charts", None)}
    if not scoped:
        return metadata, errors
    meta = copy.deepcopy(metadata)
    by_id = {
        "NATIVE_FILTER-sdc-"
        + uuid5(ids.NAMESPACE, f"{spec.dashboard.slug}/filter/{f.name}").hex[:12]: f.name
        for f in spec.filters
    }
    seen: set[str] = set()
    for nf in meta.get("native_filter_configuration") or []:
        name = by_id.get(nf.get("id"))
        if name is None:
            continue
        seen.add(name)
        if name not in scoped:
            # Unscoped filter: normalize the cached chartsInScope too; the PUT
            # round-trip has been seen to leave it empty, which reads as "no
            # charts" to the frontend.
            nf["scope"] = {"rootPath": ["ROOT_ID"], "excluded": []}
            nf["chartsInScope"] = sorted(name_to_id.values())
            continue
        missing = [c for c in scoped[name] if c not in name_to_id]
        if missing:
            errors.append(f"filter {name!r}: scoped charts not on the dashboard: {missing}")
            continue
        included = sorted(name_to_id[c] for c in scoped[name])
        excluded = sorted(set(name_to_id.values()) - set(included))
        nf["scope"] = {"rootPath": ["ROOT_ID"], "excluded": excluded}
        nf["chartsInScope"] = included
    for name in scoped.keys() - seen:
        errors.append(f"filter {name!r}: not found in imported native_filter_configuration")
    return meta, errors


def _apply_filter_scopes(spec: DashboardSpec, client: SupersetClient, dashboard_id: int) -> list[str]:
    if not any(getattr(f, "charts", None) for f in spec.filters):
        return []
    detail = client.get(f"/api/v1/dashboard/{dashboard_id}")["result"]
    metadata = json.loads(detail.get("json_metadata") or "{}")
    name_to_id = {c["slice_name"]: c["id"] for c in client.dashboard_charts(dashboard_id)}
    new_meta, errors = scoped_filter_fixup(metadata, spec, name_to_id)
    if errors:
        return errors
    r = client.put_json(f"/api/v1/dashboard/{dashboard_id}", {"json_metadata": json.dumps(new_meta)})
    if r.status_code != 200:
        return [f"scope PUT failed: HTTP {r.status_code} {r.text[:300]}"]
    return []


def _unlink_charts_off_the_layout(client: SupersetClient, dashboard_id: int) -> str | None:
    """Link the dashboard to exactly the charts in its layout, as saving it in Superset
    does: a json_metadata PUT that carries `positions` sets the dashboard's charts to
    the ones those positions name (DashboardDAO.set_dash_metadata, 4.1.4, 5.0.0 and
    6.1.0 alike). The whole current metadata goes with it, because that call resets
    keys the payload leaves out. The charts themselves are never touched."""
    detail = client.get(f"/api/v1/dashboard/{dashboard_id}")["result"]
    metadata = json.loads(detail.get("json_metadata") or "{}")
    positions = json.loads(detail.get("position_json") or "{}")
    r = client.put_json(f"/api/v1/dashboard/{dashboard_id}",
                        {"json_metadata": json.dumps({**metadata, "positions": positions})})
    if r.status_code != 200:
        return (f"could not take charts added in Superset off the dashboard: HTTP "
                f"{r.status_code} {r.text[:300]}")
    return None


def _roundtrip_dataset_files(resolution: Resolution, client: SupersetClient) -> dict[str, bytes]:
    """Export every referenced dataset from the TARGET at apply time (never a
    cached copy) so the bundle carries the dataset/database YAMLs the importer
    requires. Existing datasets are not overwritten by dashboard import."""
    extra: dict[str, bytes] = {}
    for ds_id in sorted({d.id for d in resolution.datasets.values()}):
        blob = client.export_dataset(ds_id)
        zf = zipfile.ZipFile(io.BytesIO(blob))
        for n in zf.namelist():
            rel = n.split("/", 1)[1] if "/" in n else n
            if rel.startswith(("datasets/", "databases/")):
                extra.setdefault(rel, zf.read(n))
    return extra


# Chart fields beyond params that the spec owns (null when the spec omits them).
CHART_PUT_FIELDS = ("description", "certified_by", "certification_details", "cache_timeout")


def chart_payloads_from_bundle(bundle: bytes, dataset_ids: dict[str, int] | None = None,
                               keep_query_context: bool = False) -> dict[str, dict]:
    """uuid -> ChartRestApi.put payload, from the compiled bundle's chart yamls.

    With ``dataset_ids`` (dataset uuid -> id on the target), the payload also moves the
    chart onto its spec dataset: params alone carry the new datasource, but the chart's
    own datasource_id would stay on the old one (observed live 2026-09-25: a chart moved
    from dataset 5 to 10 kept datasource_id 5).

    Used to update pre-existing owned charts IN PLACE. Slice ids must stay
    stable across re-applies: delete+reimport mints new ids, which invalidates
    native-filter scopes the moment any open browser tab writes its (stale,
    dead-id) metadata back; Superset dashboard metadata is last-writer-wins
    (observed live; docs/CONTRACTS.md, "How dashboard settings are stored").

    ``keep_query_context`` carries the bundle's own saved query (a backup's, for
    restore); otherwise it is cleared, since new params make a stored one stale."""
    zf = zipfile.ZipFile(io.BytesIO(bundle))
    out: dict[str, dict] = {}
    for n in zf.namelist():
        if "/charts/" in n and n.endswith(".yaml"):
            cy = yaml.safe_load(zf.read(n))
            payload = {
                "slice_name": cy.get("slice_name"),
                "viz_type": cy.get("viz_type"),
                "params": json.dumps(cy.get("params") or {}),
                # params changed -> any stored query context is stale
                "query_context": cy.get("query_context") if keep_query_context else None,
            }
            # The chart's own fields travel too: the importer never overwrites an
            # existing chart, so without them a re-apply would leave a changed
            # description or certification at its old value. ChartPutSchema takes
            # each (allow_none) at 4.1.4, 5.0.0 and 6.1.0.
            for key in CHART_PUT_FIELDS:
                payload[key] = cy.get(key)
            ds_id = (dataset_ids or {}).get(str(cy.get("dataset_uuid")))
            if ds_id is not None:
                payload["datasource_id"] = ds_id
                payload["datasource_type"] = "table"
            out[str(cy.get("uuid"))] = payload
    return out


def bundle_dataset_ids(bundle: bytes, client: SupersetClient) -> dict[str, int]:
    """Dataset uuid -> id on the target, for each dataset the bundle ships. Looked
    up per table name (indexed server-side) and matched by uuid, as charts are."""
    zf = zipfile.ZipFile(io.BytesIO(bundle))
    wanted: dict[str, str] = {}
    for n in zf.namelist():
        if "/datasets/" in n and n.endswith(".yaml"):
            dy = yaml.safe_load(zf.read(n)) or {}
            if dy.get("uuid") and dy.get("table_name"):
                wanted[str(dy["uuid"])] = dy["table_name"]
    found: dict[str, int] = {}
    for table in set(wanted.values()):
        for d in client.find_datasets(table):
            if str(d.get("uuid")) in wanted:
                found[str(d["uuid"])] = d["id"]
    return found


def _update_owned_charts_in_place(
    spec: DashboardSpec, client: SupersetClient, bundle: bytes,
    existing_by_uuid: dict[str, dict], resolution: Resolution | None = None,
    live_bundle: bytes | None = None,
) -> tuple[list[str], list[str]]:
    """PUT compiled params (and the spec dataset) onto pre-existing owned charts (ids
    stay stable). Returns (updated chart names, errors).

    With ``live_bundle`` (the backup export), a chart whose stored options already
    equal what the bundle writes keeps its saved query: CSV and text reports and the
    chart data API read it, and nothing about the query changed."""
    from .dashdiff import _bundle_charts, option_changes

    owned = {str(spec.chart_uuid(c.name)): c.name for c in spec.charts}
    dataset_ids = {str(d.uuid): d.id for d in resolution.datasets.values()} if resolution else None
    payloads = chart_payloads_from_bundle(bundle, dataset_ids)
    compiled = _bundle_charts(bundle) if live_bundle else {}
    live = _bundle_charts(live_bundle) if live_bundle else {}
    updated, errors = [], []
    for u, summary in existing_by_uuid.items():
        payload = payloads.get(u)
        if payload is None:
            continue
        if (u in live and live[u].get("query_context") and u in compiled
                and not option_changes(compiled[u], live[u])
                and live[u].get("dataset_uuid") == compiled[u].get("dataset_uuid")):
            payload = {k: v for k, v in payload.items() if k != "query_context"}
        r = client.put_json(f"/api/v1/chart/{summary['id']}", payload)
        if r.status_code != 200:
            errors.append(f"chart {owned.get(u, u)!r}: update PUT HTTP {r.status_code} {r.text[:200]}")
        else:
            updated.append(owned.get(u, u))
    return updated, errors


def backup_dir_for(profile: str, slug: str) -> "Path":
    """~/.config/chartwright/backups/<profile>/<slug> (or $CHARTWRIGHT_BACKUP_DIR/<profile>/<slug>).

    Namespaced by PROFILE, not just slug: the same slug on two instances yields
    the same uuid5, so restore's ownership guard cannot tell a sandbox zip from
    a production one; only the directory layout can. CWD-independent."""
    import os
    from pathlib import Path

    custom = os.environ.get("CHARTWRIGHT_BACKUP_DIR")
    base = Path(custom) if custom else Path.home() / ".config" / "chartwright" / "backups"
    return base / profile / slug


def write_backup(backup_dir: "Path", data: bytes) -> "Path":
    """Write a backup zip under a new name; never overwrite an earlier one.

    Names are local time to the microsecond (``20261003T141502.123456.zip``),
    fixed width, so they sort oldest to newest. Two applies within one second
    used to share a name and the second overwrote the first. Exclusive create
    makes a collision a retry, never a silent overwrite."""
    import datetime

    while True:
        stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S.%f")
        path = backup_dir / f"{stamp}.zip"
        try:
            with open(path, "xb") as fh:
                fh.write(data)
            return path
        except FileExistsError:
            continue


def restore_bundle(zip_bytes: bytes, slug: str, client: SupersetClient) -> ApplyReport:
    """Restore a backup bundle COMPLETELY, not just import it. The importer
    never overwrites existing charts (docs/CONTRACTS.md), so surviving
    charts' params are PUT back in place from the bundle; and bundles ship
    ROOT filter scopes by design, so numeric scopes are recomputed from the
    bundle's own name-based markers (via its decompiled spec)."""
    report = ApplyReport(ok=False, stage="restore")
    try:
        r = client.import_dashboard_bundle(zip_bytes, overwrite=True)
        report.import_status = r.status_code
        if r.status_code != 200:
            report.import_detail = r.text[:2000]
            return report

        # With the dataset ids, a restore also moves each chart back onto its
        # backed-up dataset, so its datasource_id matches the params it gets.
        # Best effort: a failed lookup must not stop the restore itself.
        try:
            dataset_ids = bundle_dataset_ids(zip_bytes, client)
        except SupersetAPIError as e:
            dataset_ids = {}
            report.warnings.append(f"chart datasets not restored (dataset lookup failed: {e})")
        payloads = chart_payloads_from_bundle(zip_bytes, dataset_ids, keep_query_context=True)
        existing = client.charts_by_uuids(
            {u: p["slice_name"] for u, p in payloads.items()})
        # A chart renamed since the backup (an adopted chart keeps its uuid when its
        # name changes) isn't found by its backed-up name: find it by uuid among the
        # restored dashboard's own charts.
        if len(existing) < len(payloads):
            try:
                restored_dash = client.find_dashboard_by_slug(slug)
                by_uuid = adopted_live_charts(
                    client, restored_dash["id"], client.export_dashboard(restored_dash["id"]))[0] \
                    if restored_dash else {}
            except SupersetAPIError as e:  # best effort, like the dataset lookup above
                by_uuid = {}
                report.warnings.append(f"charts renamed since the backup not looked up ({e})")
            for u in payloads:
                if u not in existing and u in by_uuid:
                    existing[u] = by_uuid[u]
        restored, not_restored = [], []
        for u, row in existing.items():
            rr = client.put_json(f"/api/v1/chart/{row['id']}", payloads[u])
            if rr.status_code != 200:
                not_restored.append(payloads[u]["slice_name"])
                report.warnings.append(
                    f"chart {payloads[u]['slice_name']!r}: params restore PUT HTTP {rr.status_code}")
            else:
                restored.append(payloads[u]["slice_name"])
        if restored:
            report.warnings.append(f"restored chart params in place: {sorted(restored)}")

        dash = client.find_dashboard_by_slug(slug)
        if dash is None:
            report.import_detail = "restore imported but dashboard not found at slug"
            return report
        report.dashboard_id = dash["id"]
        report.dashboard_url = f"{client.base_url}/superset/dashboard/{slug}/"

        # 4.1.4 and 5.0.0 merge chart links on import, so a chart linked since the
        # backup (one a failed apply just added, or one added in Superset) would stay
        # linked to the restored dashboard; 6.1.0 unlinks it. Match the backup.
        in_backup = {p["slice_name"] for p in payloads.values()}
        extra = sorted({c["slice_name"] for c in client.dashboard_charts(dash["id"])}
                       - in_backup)
        if extra:
            unlink_error = _unlink_charts_off_the_layout(client, dash["id"])
            if unlink_error:
                report.warnings.append(unlink_error)
            else:
                report.warnings.append(
                    f"took charts the backup doesn't have off the dashboard: {extra}; they "
                    f"are not deleted")

        from .decompile import decompile_bundle, live_dataset_lookup
        from .spec import load_spec

        dec = decompile_bundle(zip_bytes, live_dataset_lookup(client))
        try:
            spec = load_spec(dec.spec)
        except Exception as e:  # noqa: BLE001 - degraded bundles restore without scopes
            report.warnings.append(f"scopes not reapplied (bundle spec not loadable: {e})")
        else:
            scope_errors = _apply_filter_scopes(spec, client, report.dashboard_id)
            for e in scope_errors:
                report.warnings.append(f"scope reapply: {e}")
        if not_restored:
            # The layout is back but these charts still hold the newer params: not a
            # restore anyone should be told succeeded.
            report.import_detail = (f"the dashboard was restored but charts {sorted(not_restored)} "
                                    f"were not; run the restore again")
            return report
    except SupersetAPIError as e:
        report.import_detail = f"{e} (status={e.status})"
        return report
    report.stage = "done"
    report.ok = True
    return report


def apply(spec: DashboardSpec, client: SupersetClient, profile: str = "default",
          superset_version: str | None = None) -> ApplyReport:
    report = ApplyReport(ok=False, stage="resolve")

    # Resolution also holds the spec to the instance's Superset release, so a
    # field that release can't take stops the apply here, before any write.
    resolution = resolve(spec, client, superset_version)
    report.resolution_errors = [e.as_dict() for e in resolution.errors]
    report.superset_version = resolution.superset_version
    report.version_warnings = resolution.version_warnings
    report.unchecked_sql = resolution.unchecked_sql
    if not resolution.ok:
        return report

    report.stage = "ownership"
    guard = _ownership_guard(spec, client)
    if guard:
        report.import_detail = guard
        return report

    report.stage = "prepare"
    backup_bytes: bytes | None = None
    existing = client.find_dashboard_by_slug(spec.dashboard.slug)
    if existing is not None:
        # Last-known-good insurance before we mutate anything: the previous
        # owned state, restorable with `chartwright restore <zip> --profile ...`.
        import os

        backup_dir = backup_dir_for(profile, spec.dashboard.slug)
        backup_dir.mkdir(parents=True, exist_ok=True)
        if not os.environ.get("CHARTWRIGHT_BACKUP_DIR"):
            # Zips hold dashboard/dataset metadata; gate the default tree to
            # the owner. (No-op on Windows; custom dirs are the user's to manage.)
            os.chmod(backup_dir.parent.parent, 0o700)
        backup_bytes = client.export_dashboard(existing["id"])
        report.backup = write_backup(backup_dir, backup_bytes).as_posix()

    def _auto_restore(reason: str) -> None:
        """Import failed mid-mutation: put the previous state back rather
        than leaving a half-updated dashboard live. Full restore: dashboard
        state, surviving charts' params, and numeric filter scopes."""
        if backup_bytes is None:
            return
        try:
            rr = restore_bundle(backup_bytes, spec.dashboard.slug, client)
            if rr.ok:
                report.warnings.append(f"{reason}; previous state AUTO-RESTORED from backup")
            else:
                report.warnings.append(
                    f"{reason}; auto-restore also failed ({rr.import_detail}); "
                    f"restore manually: chartwright restore {report.backup} --profile {profile}"
                )
        except Exception as e:  # noqa: BLE001 - restore is best-effort recovery
            report.warnings.append(
                f"{reason}; auto-restore errored ({e}); restore manually: chartwright restore {report.backup} --profile {profile}"
            )

    adopted_live: dict[str, dict] = {}
    if spec.dashboard.adopted:
        # The guard guarantees the adopted dashboard is at the slug, so a backup exists.
        # Charts are matched by uuid, not title: a chart renamed in the spec is still
        # the same chart, and only charts ON this dashboard can be touched.
        try:
            adopted_live, ambiguous = adopted_live_charts(client, existing["id"], backup_bytes)
        except SupersetAPIError as e:
            report.import_detail = f"{e} (status={e.status}); nothing was changed"
            return report
        if ambiguous:
            report.import_detail = (
                f"charts share a title on the adopted dashboard: {ambiguous}; nothing was "
                f"changed. Give each a distinct title in Superset, then run `chartwright adopt` "
                f"again.")
            return report
        absent = sorted(n for n, u in spec.dashboard.adopted.charts.items() if u not in adopted_live)
        if absent:
            report.import_detail = (
                f"adopted charts {absent} are not on the adopted dashboard; nothing was changed. "
                f"Remove their entries from dashboard.adopted.charts, or run `chartwright adopt` "
                f"again.")
            return report

    # Everything below mutates the instance; any failure must still return a
    # report (it carries the backup path) rather than a traceback.
    foreign_before: dict = {}
    owned_names = {str(spec.chart_uuid(c.name)): c.name for c in spec.charts}
    try:
        if spec.dashboard.adopted:
            # Adopted charts that left the spec were there when the dashboard was adopted
            # and may sit on other dashboards: they are taken off this one after the
            # import (below), never deleted. Charts the tool created since adoption carry
            # its derived uuid and are deleted, as on any tool-built dashboard, unless
            # another dashboard uses them.
            stale = {u: row for u, row in adopted_live.items() if u not in owned_names}
            made_here = {u: r for u, r in stale.items()
                         if u == str(ids.chart_uuid(spec.dashboard.slug, r["slice_name"]))}
            elsewhere = set(charts_on_other_dashboards(client, existing["id"],
                                                       list(made_here.values())))
            made_here = {u: r for u, r in made_here.items() if r["slice_name"] not in elsewhere}
            for row in made_here.values():
                client.delete_chart(row["id"])
            if made_here:
                report.warnings.append(
                    f"deleted owned charts no longer in spec: "
                    f"{sorted(r['slice_name'] for r in made_here.values())}")
            foreign_before = {r["id"]: r["slice_name"] for u, r in stale.items()
                              if u not in made_here}
        elif existing is not None:
            # Owned charts that fell OUT of the spec (removed or renamed away)
            # must be deleted, not left behind: pre-6.1 importers MERGE
            # dashboard_slices, so a lingering owned chart stays linked and
            # fails linkage (docs/CONTRACTS.md); on every version it would
            # otherwise accumulate as an instance orphan. uuid5 ownership
            # (uuid == chart_uuid(slug, its own name)) gates the delete;
            # user charts and UI-renamed drift are never touched.
            linked_before = client.dashboard_charts(existing["id"])
            stale = {c["slice_name"] for c in linked_before} - {c.name for c in spec.charts}
            if stale:
                owned_stale = client.charts_by_uuids(
                    {str(ids.chart_uuid(spec.dashboard.slug, n)): n for n in stale})
                for u, row in owned_stale.items():
                    client.delete_chart(row["id"])
                if owned_stale:
                    report.warnings.append(
                        f"deleted owned charts no longer in spec: {sorted(row['slice_name'] for row in owned_stale.values())}"
                    )
                # What is left was added in Superset (or is an owned chart renamed there,
                # which the import renames back and keeps): by id, since a name moves.
                deleted = {row["id"] for row in owned_stale.values()}
                foreign_before = {c["id"]: c["slice_name"] for c in linked_before
                                  if c["slice_name"] in stale and c.get("id") not in deleted}

        extra = _roundtrip_dataset_files(resolution, client)
        # Slice ids must survive re-apply (see chart_payloads_from_bundle): existing
        # owned charts are updated in place AFTER import; the importer creates only
        # the missing ones and never overwrites existing charts.
        if spec.dashboard.adopted:
            existing_by_uuid = {u: adopted_live[u] for u in owned_names if u in adopted_live}
            # A chart the tool created after adoption may have been taken off the
            # dashboard in the UI: find those by their derived uuid, as on any
            # tool-built dashboard (never foreign uuids: the adopted ones are all here).
            derived = {u: n for u, n in owned_names.items()
                       if u not in adopted_live and u == str(ids.chart_uuid(spec.dashboard.slug, n))}
            existing_by_uuid.update(client.charts_by_uuids(derived))
            shared = charts_on_other_dashboards(client, existing["id"], list(existing_by_uuid.values()))
            if shared:
                report.warnings.append(
                    f"these charts also appear on other dashboards and change there too: {shared}")
        else:
            existing_by_uuid = client.charts_by_uuids(owned_names)

        report.stage = "import"
        bundle = compile_bundle(spec, resolution, extra_files=extra)
        r = client.import_dashboard_bundle(bundle, overwrite=True)
        report.import_status = r.status_code
        if r.status_code != 200:
            report.import_detail = r.text[:2000]
            _auto_restore(f"import failed (HTTP {r.status_code})")
            return report

        if existing_by_uuid:
            updated, update_errors = _update_owned_charts_in_place(
                spec, client, bundle, existing_by_uuid, resolution, live_bundle=backup_bytes)
            if update_errors:
                # Still the import step: some charts may carry the new params
                # and others the old. Same outcome as a connection drop here,
                # which already restored (SupersetAPIError while stage is import).
                report.import_detail = "; ".join(update_errors)
                _auto_restore("updating charts in place failed after the import")
                return report
            if updated:
                report.warnings.append(
                    f"re-apply: updated owned charts in place (ids stable): {sorted(updated)}"
                )

        dash = client.find_dashboard_by_slug(spec.dashboard.slug)
        if dash:
            report.dashboard_id = dash["id"]
            report.dashboard_url = f"{client.base_url}/superset/dashboard/{spec.dashboard.slug}/"

        report.stage = "linkage"
        if report.dashboard_id is None:
            report.import_detail = "import returned 200 but dashboard not found at slug"
            return report
        expected = {c.name for c in spec.charts}
        now = client.dashboard_charts(report.dashboard_id)
        if any(c["slice_name"] not in expected for c in now):
            # 4.1.4 and 5.0.0 merge the dashboard's chart links on import, so a chart
            # added in Superset stays linked; 6.1.0 unlinks it. Saving the imported
            # layout makes every release match the spec (docs/CONTRACTS.md).
            foreign_before.update({c["id"]: c["slice_name"] for c in now
                                   if c["slice_name"] not in expected})
            unlink_error = _unlink_charts_off_the_layout(client, report.dashboard_id)
            if unlink_error:
                report.import_detail = unlink_error
                return report
            now = client.dashboard_charts(report.dashboard_id)
        still = {c.get("id") for c in now}
        unlinked = sorted({n for i, n in foreign_before.items() if i not in still})
        if unlinked:
            report.warnings.append(
                f"took charts the spec doesn't have off the dashboard: {unlinked}. They are "
                f"not deleted: find them under Charts. To keep one on the dashboard, add it to "
                f"the spec")
        linked = {c["slice_name"] for c in now}
        if linked != expected:
            report.import_detail = (
                f"dashboard chart linkage mismatch: missing={sorted(expected - linked)}, "
                f"unexpected={sorted(linked - expected)}"
            )
            return report

        report.stage = "scope"
        scope_errors = _apply_filter_scopes(spec, client, report.dashboard_id)
        if scope_errors:
            report.import_detail = "; ".join(scope_errors)
            return report

        if resolution.owner_ids is not None:
            # The bundle can't carry owners (ImportV1DashboardSchema has none), and the
            # import just made this account an owner; set the spec's list, resolved to
            # ids before anything was written (chartwright.owners).
            from .owners import set_owners

            report.stage = "owners"
            owner_error = set_owners(client, report.dashboard_id, resolution.owner_ids)
            if owner_error:
                report.import_detail = owner_error
                return report

        report.stage = "smoke"
        results: list[SmokeResult] = smoke(spec, resolution, client)
        report.smoke_results = [asdict(s) for s in results]
        report.warnings.extend(f"{s.chart}: {s.detail}" for s in results if s.warning)
        if any(not s.ok for s in results):
            return report
    except SupersetAPIError as e:
        report.import_detail = f"{e} (status={e.status})"
        if report.stage in ("prepare", "import"):
            _auto_restore(f"apply failed during {report.stage}: {e}")
        return report
    except Exception as e:  # noqa: BLE001 - a bug mid-mutation must still restore and report
        # Not a Superset error: most likely a bug here. Same rule as above, and
        # the report keeps the backup path, which a traceback would lose.
        report.import_detail = f"unexpected error during {report.stage}: {type(e).__name__}: {e}"
        if report.stage in ("prepare", "import"):
            _auto_restore(f"apply failed during {report.stage}: {type(e).__name__}: {e}")
        return report

    report.stage = "done"
    report.ok = True
    return report
