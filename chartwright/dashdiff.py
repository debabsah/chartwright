"""v1 plan/drift: what would apply change, and has the live dashboard drifted
from its spec? Terraform's plan verb, one dashboard at a time.

Exit contract (CLI): 0 = no changes (clean), 1 = changes pending / drift.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import ids
from .client import SupersetClient
from .decompile import decompile_live
from .spec import DEFAULT_ROW_LIMIT, DEFAULT_TIME_GRAIN, DashboardSpec, load_spec


@dataclass
class Plan:
    dashboard: str                     # create | update | unchanged | blocked
    detail: str | None = None
    charts_added: list[str] = field(default_factory=list)
    charts_changed: list[str] = field(default_factory=list)
    charts_removed: list[str] = field(default_factory=list)   # live-but-not-in-spec = orphaned on apply
    filters_added: list[str] = field(default_factory=list)
    filters_changed: list[str] = field(default_factory=list)  # incl. live numeric scopes out of sync
    filters_removed: list[str] = field(default_factory=list)
    title_changed: bool = False
    layout_changed: bool = False
    decompile_losses: list[dict] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return self.dashboard == "unchanged" and not (
            self.charts_added or self.charts_changed or self.charts_removed
            or self.filters_added or self.filters_changed or self.filters_removed
            or self.title_changed or self.layout_changed
        )

    def to_json(self) -> str:
        return json.dumps(
            {
                "dashboard": self.dashboard,
                "detail": self.detail,
                "clean": self.clean,
                "charts_added": self.charts_added,
                "charts_changed": self.charts_changed,
                "charts_removed": self.charts_removed,
                "filters_added": self.filters_added,
                "filters_changed": self.filters_changed,
                "filters_removed": self.filters_removed,
                "title_changed": self.title_changed,
                "layout_changed": self.layout_changed,
                "decompile_losses": self.decompile_losses,
            },
            indent=2,
        )


def _normalize(spec: DashboardSpec) -> dict:
    """Canonical form for comparison: validated model dump with every
    compiler default materialized, so spec-with-defaults-omitted and
    decompiled-with-defaults-present compare equal. Chart identity is the
    name, so chart list order is canonicalized by name."""
    data = spec.model_dump(exclude_none=True, by_alias=True)
    for chart in data["charts"]:
        chart["width"] = spec.resolved_item_width(chart["name"])
        chart["height"] = spec.resolved_height(chart["name"])
        if chart["type"] in DEFAULT_ROW_LIMIT:
            chart.setdefault("row_limit", DEFAULT_ROW_LIMIT[chart["type"]])
        if chart["type"] in ("timeseries_line", "timeseries_bar", "timeseries_area",
                             "timeseries_scatter", "big_number_trend"):
            chart.setdefault("time_grain", DEFAULT_TIME_GRAIN)
    data["charts"].sort(key=lambda c: c["name"])

    def norm_rows(model_rows, data_rows) -> None:
        for mrow, drow in zip(model_rows, data_rows):
            for mitem, ditem in zip(mrow, drow):
                if not isinstance(mitem, str):
                    ditem["width"] = spec.resolved_item_width(mitem)
                    ditem.setdefault("height", 4)

    def sketch_as_rows(holder) -> list[list]:
        """Sketch -> the rows form decompile produces for the same dashboard
        (columns flattened in order, matching decompile's named flattening),
        so a sketch spec and its live state compare equal."""
        rows = []
        for srow in holder.parsed_sketch():
            row = []
            for child in srow.children:
                for sc in (child.children if hasattr(child, "children") else [child]):
                    row.append(sc.name)
            rows.append(row)
        return rows

    if spec.layout.sketch:
        data["layout"] = {"rows": sketch_as_rows(spec.layout)}
    elif spec.layout.rows:
        norm_rows(spec.layout.rows, data["layout"]["rows"])
    else:
        for mtab, dtab in zip(spec.layout.tabs or [], data["layout"].get("tabs") or []):
            if mtab.sketch:
                dtab.pop("sketch", None)
                dtab.pop("legend", None)
                dtab["rows"] = sketch_as_rows(mtab)
            else:
                norm_rows(mtab.rows, dtab["rows"])
            dtab.pop("line", None)
    # `line` is sketch drawing config (units per sketch line), not geometry;
    # its non-None default survives the dump and would false-drift a sketch
    # spec against its rows-form live state.
    data["layout"].pop("line", None)
    return data


def plan(target: DashboardSpec, client: SupersetClient) -> Plan:
    from .resolver import resolve

    existing = client.find_dashboard_by_slug(target.dashboard.slug)
    if existing is None:
        return Plan(dashboard="create", detail="no dashboard at this slug",
                    charts_added=[c.name for c in target.charts])

    # uuid comes from the LIST row: Superset 4.x omits uuid from the detail
    # API (docs/CONTRACTS.md) but every supported version includes it in
    # list_columns; find_dashboard_by_slug already returned it.
    live_uuid = existing.get("uuid") or client.get(
        f"/api/v1/dashboard/{existing['id']}")["result"].get("uuid")
    if str(live_uuid) != str(ids.dashboard_uuid(target.dashboard.slug)):
        return Plan(dashboard="blocked",
                    detail=f"slug {target.dashboard.slug!r} exists but is not owned by this tool")

    resolution = resolve(target, client)
    if not resolution.ok:
        return Plan(dashboard="blocked",
                    detail=f"target spec has referential errors: {[e.as_dict() for e in resolution.errors]}")

    live_result = decompile_live(target.dashboard.slug, client)
    try:
        live_spec = load_spec(live_result.spec)
    except Exception as e:  # noqa: BLE001 - decompiled live state can be arbitrarily degraded
        return Plan(dashboard="update",
                    detail=f"live dashboard not representable as a spec ({e}); apply will rebuild it",
                    decompile_losses=live_result.losses_json())

    t, l = _normalize(target), _normalize(live_spec)
    p = Plan(dashboard="unchanged", decompile_losses=live_result.losses_json())

    t_charts = {c["name"]: c for c in t["charts"]}
    l_charts = {c["name"]: c for c in l["charts"]}
    # Dataset identity compares by resolved uuid, not by literal triple:
    # an omitted schema in the spec means "unambiguous", not "different".
    for chart in target.charts:
        t_charts[chart.name]["dataset"] = resolution.for_chart(chart.dataset).uuid
    for name in l_charts:
        l_charts[name]["dataset"] = live_result.dataset_uuids.get(name)
    p.charts_added = sorted(set(t_charts) - set(l_charts))
    p.charts_removed = sorted(set(l_charts) - set(t_charts))
    p.charts_changed = sorted(
        n for n in set(t_charts) & set(l_charts) if t_charts[n] != l_charts[n]
    )
    # Filters: same identity model as charts (name-keyed, dataset by resolved
    # uuid). Without this, the primary real-world drift (a stale browser tab
    # writing back metadata without our native filters) is invisible.
    t_filters = {f["name"]: dict(f) for f in t.get("filters", [])}
    l_filters = {f["name"]: dict(f) for f in l.get("filters", [])}
    for f in target.filters:
        if f.type in ("select", "range"):
            t_filters[f.name]["dataset"] = resolution.datasets[f.dataset.key()].uuid
    for name in l_filters:
        u = live_result.dataset_uuids.get(f"filter:{name}")
        if u:
            l_filters[name]["dataset"] = u
    p.filters_added = sorted(set(t_filters) - set(l_filters))
    p.filters_removed = sorted(set(l_filters) - set(t_filters))
    p.filters_changed = sorted(
        n for n in set(t_filters) & set(l_filters) if t_filters[n] != l_filters[n]
    )

    # Live NUMERIC scopes must equal what apply's scope stage would write.
    # The server accepts dead slice ids silently (docs/CONTRACTS.md) and
    # the name-based marker still reads back clean, so recompute and diff.
    if any(getattr(f, "charts", None) for f in target.filters):
        from .apply import scoped_filter_fixup

        detail = client.get(f"/api/v1/dashboard/{existing['id']}")["result"]
        meta = json.loads(detail.get("json_metadata") or "{}")
        name_to_id = {c["slice_name"]: c["id"] for c in client.dashboard_charts(existing["id"])}
        fixed, errors = scoped_filter_fixup(meta, target, name_to_id)
        live_nf = {nf.get("id"): nf for nf in meta.get("native_filter_configuration") or []}
        for nf in fixed.get("native_filter_configuration") or []:
            if nf != live_nf.get(nf.get("id")):
                nm = nf.get("name") or nf.get("id")
                if nm not in p.filters_changed:
                    p.filters_changed.append(nm)
        for e in errors:
            nm = f"scope: {e}"
            if nm not in p.filters_changed:
                p.filters_changed.append(nm)
        p.filters_changed.sort()

    p.title_changed = t["dashboard"]["title"] != l["dashboard"]["title"]
    # Whole-layout compare: a tabs layout has no "rows" key after
    # exclude_none dumping, so keyed access would KeyError.
    p.layout_changed = t["layout"] != l["layout"]
    if not p.clean:
        p.dashboard = "update"
    return p
