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

import yaml

from . import ids
from .client import SupersetClient, SupersetAPIError
from .compiler import compile_bundle, native_filter_id
from .resolver import Resolution, resolve
from .smoke import SmokeResult, smoke
from .spec import DashboardSpec


@dataclass
class ApplyReport:
    ok: bool
    stage: str  # resolve | ownership | prepare | import | linkage | scope | smoke | done
    resolution_errors: list[dict] = field(default_factory=list)
    import_status: int | None = None
    import_detail: str | None = None
    dashboard_id: int | None = None
    dashboard_url: str | None = None
    backup: str | None = None
    smoke_results: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


def check(spec: DashboardSpec, client: SupersetClient) -> Resolution:
    return resolve(spec, client)


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
                    f"adopted; refusing to overwrite it. Run `chartwright adopt` on the dashboard you mean.")
        return (f"dashboard slug {slug!r} exists (id={existing['id']}), but this tool did not create "
                f"it; refusing to overwrite. Pick a different slug, or take it over with `chartwright adopt`.")
    return None


def _adopted_live_charts(client: SupersetClient, dashboard_id: int,
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
    by_id = {native_filter_id(spec, f.name): f.name for f in spec.filters}
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


def chart_payloads_from_bundle(bundle: bytes, dataset_ids: dict[str, int] | None = None) -> dict[str, dict]:
    """uuid -> ChartRestApi.put payload, from the compiled bundle's chart yamls.

    With ``dataset_ids`` (dataset uuid -> id on the target), the payload also moves the
    chart onto its spec dataset: params alone carry the new datasource, but the chart's
    own datasource_id would stay on the old one (observed live 2026-09-25: a chart moved
    from dataset 5 to 10 kept datasource_id 5).

    Used to update pre-existing owned charts IN PLACE. Slice ids must stay
    stable across re-applies: delete+reimport mints new ids, which invalidates
    native-filter scopes the moment any open browser tab writes its (stale,
    dead-id) metadata back; Superset dashboard metadata is last-writer-wins
    (observed live; docs/CONTRACTS.md, "How dashboard settings are stored")."""
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
                "query_context": None,
            }
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


def _unlink_charts(client: SupersetClient, dashboard_id: int,
                   charts: list[dict]) -> tuple[list[str], list[str]]:
    """Remove `dashboard_id` from each chart's dashboards, keeping the chart and its
    other dashboards. Returns (names unlinked, failures)."""
    done, failed = [], []
    for c in charts:
        try:
            detail = client.get(f"/api/v1/chart/{c['id']}")["result"]
            keep = [d["id"] for d in detail.get("dashboards") or [] if d.get("id") != dashboard_id]
            r = client.put_json(f"/api/v1/chart/{c['id']}", {"dashboards": keep})
        except SupersetAPIError as e:
            failed.append(f"{c['slice_name']!r}: {e}")
            continue
        if r.status_code == 200:
            done.append(c["slice_name"])
        else:
            failed.append(f"{c['slice_name']!r}: HTTP {r.status_code}")
    return done, failed


def _update_owned_charts_in_place(
    spec: DashboardSpec, client: SupersetClient, bundle: bytes,
    existing_by_uuid: dict[str, dict], resolution: Resolution | None = None,
) -> tuple[list[str], list[str]]:
    """PUT compiled params (and the spec dataset) onto pre-existing owned charts (ids
    stay stable). Returns (updated chart names, errors)."""
    owned = {str(spec.chart_uuid(c.name)): c.name for c in spec.charts}
    dataset_ids = {str(d.uuid): d.id for d in resolution.datasets.values()} if resolution else None
    payloads = chart_payloads_from_bundle(bundle, dataset_ids)
    updated, errors = [], []
    for u, summary in existing_by_uuid.items():
        payload = payloads.get(u)
        if payload is None:
            continue
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
        payloads = chart_payloads_from_bundle(zip_bytes, dataset_ids)
        existing = client.charts_by_uuids(
            {u: p["slice_name"] for u, p in payloads.items()})
        # A chart renamed since the backup (an adopted chart keeps its uuid when its
        # name changes) isn't found by its backed-up name: find it by uuid among the
        # restored dashboard's own charts.
        restored_dash = client.find_dashboard_by_slug(slug)
        if restored_dash is not None and len(existing) < len(payloads):
            by_uuid, _ = _adopted_live_charts(
                client, restored_dash["id"], client.export_dashboard(restored_dash["id"]))
            for u in payloads:
                if u not in existing and u in by_uuid:
                    existing[u] = by_uuid[u]
        restored = []
        for u, row in existing.items():
            rr = client.put_json(f"/api/v1/chart/{row['id']}", payloads[u])
            if rr.status_code != 200:
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
    except SupersetAPIError as e:
        report.import_detail = f"{e} (status={e.status})"
        return report
    report.stage = "done"
    report.ok = True
    return report


def apply(spec: DashboardSpec, client: SupersetClient, profile: str = "default") -> ApplyReport:
    report = ApplyReport(ok=False, stage="resolve")

    resolution = resolve(spec, client)
    report.resolution_errors = [e.as_dict() for e in resolution.errors]
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
        import datetime
        import os

        backup_dir = backup_dir_for(profile, spec.dashboard.slug)
        backup_dir.mkdir(parents=True, exist_ok=True)
        if not os.environ.get("CHARTWRIGHT_BACKUP_DIR"):
            # Zips hold dashboard/dataset metadata; gate the default tree to
            # the owner. (No-op on Windows; custom dirs are the user's to manage.)
            os.chmod(backup_dir.parent.parent, 0o700)
        stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
        backup_path = backup_dir / f"{stamp}.zip"
        backup_bytes = client.export_dashboard(existing["id"])
        backup_path.write_bytes(backup_bytes)
        report.backup = str(backup_path)

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
                    f"restore manually: chartwright restore {report.backup}"
                )
        except Exception as e:  # noqa: BLE001 - restore is best-effort recovery
            report.warnings.append(
                f"{reason}; auto-restore errored ({e}); restore manually: chartwright restore {report.backup}"
            )

    adopted_live: dict[str, dict] = {}
    if spec.dashboard.adopted:
        # The guard guarantees the adopted dashboard is at the slug, so a backup exists.
        # Charts are matched by uuid, not title: a chart renamed in the spec is still
        # the same chart, and only charts ON this dashboard can be touched.
        adopted_live, ambiguous = _adopted_live_charts(client, existing["id"], backup_bytes)
        if ambiguous:
            report.import_detail = (
                f"charts share a title on the adopted dashboard: {ambiguous}; nothing was changed. "
                "Give each a distinct title in Superset, then run `chartwright adopt` again.")
            return report
        absent = sorted(n for n, u in spec.dashboard.adopted.charts.items() if u not in adopted_live)
        if absent:
            report.import_detail = (
                f"adopted charts {absent} are not on the adopted dashboard; nothing was changed. "
                "Remove their entries from dashboard.adopted.charts, or run `chartwright adopt` again.")
            return report

    # Everything below mutates the instance; any failure must still return a
    # report (it carries the backup path) rather than a traceback.
    try:
        if existing is not None and not spec.dashboard.adopted:
            # Owned charts that fell OUT of the spec (removed or renamed away)
            # must be deleted, not left behind: pre-6.1 importers MERGE
            # dashboard_slices, so a lingering owned chart stays linked and
            # fails linkage (docs/CONTRACTS.md); on every version it would
            # otherwise accumulate as an instance orphan. uuid5 ownership
            # (uuid == chart_uuid(slug, its own name)) gates the delete;
            # user charts and UI-renamed drift are never touched.
            stale = {c["slice_name"] for c in client.dashboard_charts(existing["id"])}
            stale -= {c.name for c in spec.charts}
            if stale:
                owned_stale = client.charts_by_uuids(
                    {str(ids.chart_uuid(spec.dashboard.slug, n)): n for n in stale})
                for u, row in owned_stale.items():
                    client.delete_chart(row["id"])
                if owned_stale:
                    report.warnings.append(
                        f"deleted owned charts no longer in spec: {sorted(row['slice_name'] for row in owned_stale.values())}"
                    )

        extra = _roundtrip_dataset_files(resolution, client)
        # Slice ids must survive re-apply (see chart_payloads_from_bundle): existing
        # owned charts are updated in place AFTER import; the importer creates only
        # the missing ones and never overwrites existing charts.
        owned_names = {str(spec.chart_uuid(c.name)): c.name for c in spec.charts}
        if spec.dashboard.adopted:
            existing_by_uuid = {u: adopted_live[u] for u in owned_names if u in adopted_live}
            # A chart the tool created after adoption may have been taken off the
            # dashboard in the UI: find those by their derived uuid, as on any
            # tool-built dashboard (never foreign uuids: the adopted ones are all here).
            derived = {u: n for u, n in owned_names.items()
                       if u not in adopted_live and u == str(ids.chart_uuid(spec.dashboard.slug, n))}
            existing_by_uuid.update(client.charts_by_uuids(derived))
            shared = sorted(
                row["slice_name"] for u, row in existing_by_uuid.items() if u in adopted_live
                and any(d.get("id") != existing["id"] for d in
                        client.get(f"/api/v1/chart/{row['id']}")["result"].get("dashboards") or []))
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
                spec, client, bundle, existing_by_uuid, resolution)
            if update_errors:
                report.import_detail = "; ".join(update_errors)
                return report
            if updated:
                report.warnings.append(
                    f"re-apply: updated owned charts in place (ids stable): {sorted(updated)}"
                )

        if spec.dashboard.adopted:
            # After the import, so a failure here never leaves the dashboard half-changed
            # before it (6.1's importer has already relinked; older ones merge links).
            spec_uuids = set(owned_names)
            stale = {u: row for u, row in adopted_live.items() if u not in spec_uuids}
            # Charts the tool created after adoption carry its derived uuid: delete
            # those as on any tool-built dashboard. The rest were there when the
            # dashboard was adopted and may sit on other dashboards: take them off
            # this one, never delete them.
            made_here = {u: r for u, r in stale.items()
                         if u == str(ids.chart_uuid(spec.dashboard.slug, r["slice_name"]))}
            unlinked, failed = _unlink_charts(
                client, existing["id"], [r for u, r in stale.items() if u not in made_here])
            if failed:
                report.import_detail = f"could not take charts off the adopted dashboard: {failed}"
                _auto_restore("taking charts off the adopted dashboard failed")
                return report
            if unlinked:
                report.warnings.append(
                    f"took charts no longer in the spec off the adopted dashboard (not deleted): {sorted(unlinked)}")
            for row in made_here.values():
                client.delete_chart(row["id"])
            if made_here:
                report.warnings.append(
                    f"deleted owned charts no longer in spec: {sorted(r['slice_name'] for r in made_here.values())}")

        dash = client.find_dashboard_by_slug(spec.dashboard.slug)
        if dash:
            report.dashboard_id = dash["id"]
            report.dashboard_url = f"{client.base_url}/superset/dashboard/{spec.dashboard.slug}/"

        report.stage = "linkage"
        if report.dashboard_id is None:
            report.import_detail = "import returned 200 but dashboard not found at slug"
            return report
        linked = {c["slice_name"] for c in client.dashboard_charts(report.dashboard_id)}
        expected = {c.name for c in spec.charts}
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

    report.stage = "done"
    report.ok = True
    return report
