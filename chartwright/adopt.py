"""Take over an existing dashboard in place.

`chartwright adopt` reads a live dashboard the tool did not create and writes a
spec that names THAT dashboard and its charts by their own uuids
(`dashboard.adopted`). Applying the spec then updates the same dashboard: same
id, same address, same chart ids. Adopt itself changes nothing in Superset;
`plan` compares what the first apply would write with what each chart has now,
and apply backs up before it changes anything.

Adopt refuses, each time saying what to do instead, when the result could not be
applied safely:
- the dashboard has no URL name (slug), or one the spec format can't hold, or an
  all-digit one (Superset reads those as an id);
- two of its charts share a title (they couldn't be told apart by name);
- some charts can't be represented (applying would take them off the dashboard);
- some charts also sit on other dashboards (applying would change them there too).
The last two can be overridden, separately: `force` for charts a spec can't
represent, `allow_shared` for charts on other dashboards.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import ValidationError

from .apply import charts_on_other_dashboards
from .decompile import DecompileResult, decompile_bundle, live_dataset_lookup
from .spec import SLUG_PATTERN, load_spec


@dataclass
class AdoptResult:
    ok: bool
    spec: dict | None = None
    losses: list[dict] = field(default_factory=list)
    skipped_charts: list[str] = field(default_factory=list)
    shared_charts: list[str] = field(default_factory=list)
    detail: str = ""

    def payload(self) -> dict:
        out: dict = {"ok": self.ok, "stage": "adopt", "losses": self.losses}
        if self.skipped_charts:
            out["skipped_charts"] = self.skipped_charts
        if self.shared_charts:
            out["shared_charts"] = self.shared_charts
        if self.detail:
            out["detail"] = self.detail
        if not self.ok:
            out["errors"] = [{"code": "refused", "detail": self.detail}]
        return out


def adopted_spec(result: DecompileResult, force: bool = False,
                 shared_charts: list[str] | None = None, allow_shared: bool = False) -> AdoptResult:
    """Pure core: a decompiled dashboard -> an adopting spec (or a refusal)."""
    losses = result.losses_json()
    shared = sorted(shared_charts or [])

    def refuse(detail: str) -> AdoptResult:
        return AdoptResult(False, losses=losses, skipped_charts=result.skipped_charts,
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
    if result.skipped_charts and not force:
        return refuse(f"{len(result.skipped_charts)} chart(s) can't be represented in a spec, so "
                      "applying would take them off this dashboard (they would not be deleted). "
                      "Remove or replace them in Superset first, or pass --force to adopt anyway.")
    if shared and not allow_shared:
        return refuse(f"{len(shared)} chart(s) also appear on other dashboards: {shared}. Applying "
                      "updates a chart everywhere it appears, so those dashboards would change too. "
                      "Copy them in Superset first (Save as), or pass --allow-shared to adopt anyway.")

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
    return AdoptResult(True, spec=spec, losses=losses, skipped_charts=result.skipped_charts,
                       shared_charts=shared)


def adopt_live(slug_or_id: str, client, force: bool = False, allow_shared: bool = False) -> AdoptResult:
    dash = client.find_dashboard_by_slug(slug_or_id)
    if dash is not None:
        dashboard_id = dash["id"]
    elif slug_or_id.isdigit():
        dashboard_id = int(slug_or_id)
    else:
        raise ValueError(f"no dashboard with slug {slug_or_id!r}")
    result = decompile_bundle(client.export_dashboard(dashboard_id), live_dataset_lookup(client))
    shared = charts_on_other_dashboards(client, dashboard_id, client.dashboard_charts(dashboard_id))
    return adopted_spec(result, force=force, shared_charts=shared, allow_shared=allow_shared)
