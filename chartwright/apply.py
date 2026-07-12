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


def _ownership_guard(spec: DashboardSpec, client: SupersetClient) -> str | None:
    """Refuse to overwrite a dashboard at our slug that we did not create.
    uuid5-owned objects are safe to overwrite; anything else is someone's work."""
    existing = client.find_dashboard_by_slug(spec.dashboard.slug)
    if existing is None:
        return None
    # The list endpoint doesn't return uuid; fetch detail.
    detail = client.get(f"/api/v1/dashboard/{existing['id']}")["result"]
    existing_uuid = str(detail.get("uuid"))
    if not detail.get("uuid"):
        # Superset 4.x omits uuid from the detail API; the export bundle is
        # authoritative (without this, apply can't recognize its own dashboard
        # on 4.x and every re-apply is refused).
        zf = zipfile.ZipFile(io.BytesIO(client.export_dashboard(existing["id"])))
        for n in zf.namelist():
            if "/dashboards/" in n and n.endswith(".yaml"):
                existing_uuid = str(yaml.safe_load(zf.read(n)).get("uuid"))
                break
    if existing_uuid != str(ids.dashboard_uuid(spec.dashboard.slug)):
        return (
            f"dashboard slug {spec.dashboard.slug!r} exists (id={existing['id']}) with uuid "
            f"{existing_uuid}, which this tool does not own; refusing to overwrite. "
            f"Pick a different slug."
        )
    return None


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


def chart_payloads_from_bundle(bundle: bytes) -> dict[str, dict]:
    """uuid -> ChartRestApi.put payload, from the compiled bundle's chart yamls.

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
            out[str(cy.get("uuid"))] = {
                "slice_name": cy.get("slice_name"),
                "viz_type": cy.get("viz_type"),
                "params": json.dumps(cy.get("params") or {}),
                # params changed -> any stored query context is stale
                "query_context": None,
            }
    return out


def _update_owned_charts_in_place(
    spec: DashboardSpec, client: SupersetClient, bundle: bytes,
    existing_by_uuid: dict[str, dict],
) -> tuple[list[str], list[str]]:
    """PUT compiled params onto pre-existing owned charts (ids stay stable).
    Returns (updated chart names, errors)."""
    owned = {str(ids.chart_uuid(spec.dashboard.slug, c.name)): c.name for c in spec.charts}
    payloads = chart_payloads_from_bundle(bundle)
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

        payloads = chart_payloads_from_bundle(zip_bytes)
        existing = client.charts_by_uuids(
            {u: p["slice_name"] for u, p in payloads.items()})
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

    # Everything below mutates the instance; any failure must still return a
    # report (it carries the backup path) rather than a traceback.
    try:
        if existing is not None:
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
        owned_names = {str(ids.chart_uuid(spec.dashboard.slug, c.name)): c.name for c in spec.charts}
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
            updated, update_errors = _update_owned_charts_in_place(spec, client, bundle, existing_by_uuid)
            if update_errors:
                report.import_detail = "; ".join(update_errors)
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
