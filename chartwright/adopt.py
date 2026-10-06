"""Take over an existing dashboard in place.

`chartwright adopt` reads a live dashboard the tool did not create and writes a
spec that names THAT dashboard and its charts by their own uuids
(`dashboard.adopted`). Applying the spec then updates the same dashboard: same
id, same address, same chart ids. Adopt itself changes nothing in Superset;
`plan` compares what the first apply would write with what each chart has now,
and apply backs up before it changes anything.

The spec can't hold everything a dashboard built in the UI carries, so the first
apply resets some of it to the spec's form. Adopt lists every such reset it can
find (`resets`) and refuses while there are any, unless the caller accepts them
(`accept_reset`): decompile's losses (settings and charts the spec can't hold,
which the first apply drops or takes off the dashboard, a per-chart cross-filter
scope the spec can't carry among them) plus what decompile reads past without a
loss (the dashboard-wide cross-filter scope, charts exempt from auto-refresh, saved
chart queries, tab and filter ids).

Adopt also refuses, each time saying what to do instead, when the result could not
be applied safely:
- the dashboard has no URL name (slug), or one the spec format can't hold, or an
  all-digit one (Superset reads those as an id);
- two of its charts share a title (they couldn't be told apart by name);
- some charts also sit on other dashboards (applying would change them there too),
  unless `allow_shared`.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field

import yaml
from pydantic import ValidationError

from .apply import charts_on_other_dashboards
from .decompile import DecompileResult, _read_owners, decompile_bundle, live_dataset_lookup
from .spec import SLUG_PATTERN, load_spec


@dataclass
class AdoptResult:
    ok: bool
    spec: dict | None = None
    resets: list[dict] = field(default_factory=list)
    skipped_charts: list[str] = field(default_factory=list)
    shared_charts: list[str] = field(default_factory=list)
    detail: str = ""

    def payload(self) -> dict:
        out: dict = {"ok": self.ok, "stage": "adopt", "resets": self.resets}
        if self.skipped_charts:
            out["skipped_charts"] = self.skipped_charts
        if self.shared_charts:
            out["shared_charts"] = self.shared_charts
        if self.detail:
            out["detail"] = self.detail
        if not self.ok:
            out["errors"] = [{"code": "refused", "detail": self.detail}]
        return out


def _export_parts(export: bytes) -> tuple[dict, list[dict]]:
    """(dashboard yaml, chart yamls) from an export bundle."""
    zf = zipfile.ZipFile(io.BytesIO(export))
    dash: dict = {}
    charts: list[dict] = []
    for n in zf.namelist():
        if not n.endswith(".yaml"):
            continue
        if "/dashboards/" in n:
            dash = yaml.safe_load(zf.read(n)) or {}
        elif "/charts/" in n:
            charts.append(yaml.safe_load(zf.read(n)) or {})
    return dash, charts


def first_apply_resets(result: DecompileResult, export: bytes,
                       option_changes: dict[str, list[str]] | None = None) -> list[dict]:
    """What the first apply of the adopted spec resets, as {where, what}: every loss
    decompile reported, plus what it reads past without one. `option_changes` (chart
    -> option keys apply rewrites, from compiling the spec against the instance) names
    each chart whose stored options change; without it, saved queries are listed for
    every kept chart that has one."""
    from .dashdiff import adopted_metadata_resets

    resets = [{"where": loss["where"], "what": loss["what"]} for loss in result.losses_json()]
    dash, charts = _export_parts(export)
    meta = dash.get("metadata") or {}
    resets += [{"where": "dashboard", "what": what} for _, what in adopted_metadata_resets(meta)]
    kept = set(result.chart_uuids)
    if option_changes is not None:
        for name, keys in sorted(option_changes.items()):
            resets.append({"where": name,
                           "what": f"stored options the spec can't hold are rewritten: {keys}"})
        saved = sorted(c.get("slice_name") for c in charts
                       if c.get("query_context") and c.get("slice_name") in option_changes)
        if saved:
            resets.append({"where": "charts",
                           "what": f"saved queries of {saved} are cleared, since their options "
                                   f"change. CSV and text reports and the chart data API read "
                                   f"them; saving a chart in Explore rebuilds its saved query"})
    else:
        saved = sorted(c.get("slice_name") for c in charts
                       if c.get("query_context") and c.get("slice_name") in kept)
        if saved:
            resets.append({"where": "charts",
                           "what": f"saved queries of {saved} are cleared for each chart whose "
                                   f"options the first apply changes (`plan` lists them under "
                                   f"chart_option_changes). CSV and text reports and the chart "
                                   f"data API read them; saving a chart in Explore rebuilds its "
                                   f"saved query"})
    position = dash.get("position") or {}
    if any(isinstance(v, dict) and v.get("type") == "TAB" for v in position.values()):
        resets.append({"where": "layout",
                       "what": "tab ids change, so links that open a tab by its id open the first "
                               "tab instead"})
    if meta.get("native_filter_configuration"):
        resets.append({"where": "filters",
                       "what": "native filters get the tool's own ids, so links that saved a filter "
                               "state may not carry over; names and settings are kept"})
    return resets


def option_changes_for(spec_data: dict, resolution, export: bytes) -> dict[str, list[str]]:
    """Chart name -> the stored option keys the first apply rewrites: the adopted spec
    compiled against the instance, compared with each chart's stored params."""
    from .compiler import compile_bundle
    from .dashdiff import _bundle_charts, option_changes

    spec = load_spec(spec_data)
    compiled = _bundle_charts(compile_bundle(spec, resolution))
    live = _bundle_charts(export)
    out = {}
    for chart in spec.charts:
        u = str(spec.chart_uuid(chart.name))
        if u in compiled and u in live:
            keys = option_changes(compiled[u], live[u])
            if keys:
                out[chart.name] = keys
    return out


def adopted_spec(result: DecompileResult, export: bytes = b"", accept_reset: bool = False,
                 shared_charts: list[str] | None = None, allow_shared: bool = False,
                 option_changes: dict[str, list[str]] | None = None) -> AdoptResult:
    """Pure core: a decompiled dashboard and its export -> an adopting spec (or a refusal)."""
    resets = first_apply_resets(result, export, option_changes) if export else \
        [{"where": loss["where"], "what": loss["what"]} for loss in result.losses_json()]
    shared = sorted(shared_charts or [])

    def refuse(detail: str) -> AdoptResult:
        return AdoptResult(False, resets=resets, skipped_charts=result.skipped_charts,
                           shared_charts=shared, detail=detail)

    if not result.dashboard_uuid:
        return refuse("the export carries no dashboard uuid; it can't be adopted in place")
    if not result.source_slug:
        return refuse("this dashboard has no URL name (slug). Give it one in Superset (dashboard "
                      "properties), using lowercase letters, digits and hyphens, then run adopt again.")
    if result.source_slug.isdigit():
        return refuse(f"this dashboard's URL name {result.source_slug!r} is all digits, which Superset "
                      "reads as a dashboard id. Give it a name with a letter in Superset (dashboard "
                      "properties), then run adopt again.")
    if not re.fullmatch(SLUG_PATTERN, result.source_slug):
        return refuse(f"this dashboard's URL name {result.source_slug!r} isn't one a spec can hold "
                      "(lowercase letters, digits and hyphens, starting with a letter or digit). "
                      "Change it in Superset (dashboard properties), then run adopt again.")
    # Over every chart, skipped ones too: apply tells charts apart by title, so a
    # repeat anywhere on the dashboard would make every apply refuse.
    dupes = sorted({t for t in result.chart_titles if result.chart_titles.count(t) > 1})
    if dupes:
        return refuse(f"charts share a title on this dashboard: {dupes}. Give each a distinct title "
                      "in Superset, then run adopt again.")
    if shared and not allow_shared:
        return refuse(f"{len(shared)} chart(s) also appear on other dashboards: {shared}. Applying "
                      "updates a chart everywhere it appears, so those dashboards would change too. "
                      "Copy them in Superset first (Save as), or pass --allow-shared to adopt anyway.")
    if resets and not accept_reset:
        return refuse(f"the first apply would reset {len(resets)} thing(s) on this dashboard that a "
                      "spec can't hold (listed under resets). Change them in Superset first, or pass "
                      "--accept-reset to adopt anyway; `plan` then shows each chart the first apply "
                      "changes, and apply backs up first.")

    spec = dict(result.spec)
    spec["dashboard"] = {
        **spec["dashboard"],
        "adopted": {"dashboard_uuid": result.dashboard_uuid, "slug": spec["dashboard"]["slug"],
                    "charts": dict(result.chart_uuids)},
    }
    try:
        load_spec(spec)
    except ValidationError as e:
        return refuse(f"the spec read from this dashboard doesn't validate, so it can't be applied: {e}")
    return AdoptResult(True, spec=spec, resets=resets, skipped_charts=result.skipped_charts,
                       shared_charts=shared)


def adopt_live(slug_or_id: str, client, accept_reset: bool = False,
               allow_shared: bool = False) -> AdoptResult:
    dash = client.find_dashboard_by_slug(slug_or_id)
    if dash is not None:
        dashboard_id = dash["id"]
    elif slug_or_id.isdigit():
        dashboard_id = int(slug_or_id)
    else:
        raise ValueError(f"no dashboard with slug {slug_or_id!r}")
    export = client.export_dashboard(dashboard_id)
    result = decompile_bundle(export, live_dataset_lookup(client))
    # The owners go into the spec too, so apply keeps them (the import alone would only
    # add the account that applies; docs/CONTRACTS.md, "Dashboard owners").
    _read_owners(result, client, dashboard_id)
    # Only charts the spec keeps: a skipped chart is taken off, never updated.
    kept = set(result.chart_uuids)
    on_board = [c for c in client.dashboard_charts(dashboard_id) if c["slice_name"] in kept]
    shared = charts_on_other_dashboards(client, dashboard_id, on_board)
    # Compile the would-be spec against this instance to name every chart whose stored
    # options the first apply rewrites: decompile reads past some options with no loss.
    draft = adopted_spec(result, export, accept_reset=True, allow_shared=True)
    changes = None
    if draft.ok:
        from .resolver import resolve

        resolution = resolve(load_spec(draft.spec), client)
        if not resolution.ok:
            return AdoptResult(False, resets=draft.resets, skipped_charts=result.skipped_charts,
                               shared_charts=shared,
                               detail=f"the spec read from this dashboard names things this instance "
                                      f"can't resolve, so it can't be applied: "
                                      f"{[e.as_dict() for e in resolution.errors]}")
        changes = option_changes_for(draft.spec, resolution, export)
    return adopted_spec(result, export, accept_reset=accept_reset, shared_charts=shared,
                        allow_shared=allow_shared, option_changes=changes)

