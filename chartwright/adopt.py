"""Take over an existing dashboard in place.

`chartwright adopt` reads a live dashboard the tool did not create and writes a
spec that names THAT dashboard and its charts by their own uuids
(`dashboard.adopted`). Applying the spec then updates the same dashboard: same
id, same address, same chart ids, so links, embeds and scheduled reports keep
pointing at it. Adopt itself changes nothing in Superset; `plan` shows what the
first apply would change, and apply backs up before it does.

Charts the spec cannot represent are the one hard stop: applying would take them
off the dashboard, so adopt refuses unless told to go ahead (`force`). Even then
they are only taken off this dashboard, never deleted.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .decompile import DecompileResult, decompile_live


@dataclass
class AdoptResult:
    ok: bool
    spec: dict | None = None
    losses: list[dict] = field(default_factory=list)
    skipped_charts: list[str] = field(default_factory=list)
    detail: str = ""

    def payload(self) -> dict:
        out = {"ok": self.ok, "stage": "adopt", "losses": self.losses}
        if self.skipped_charts:
            out["skipped_charts"] = self.skipped_charts
        if self.detail:
            out["detail"] = self.detail
        return out


def adopted_spec(result: DecompileResult, force: bool = False) -> AdoptResult:
    """Pure core: a decompiled dashboard -> an adopting spec (or a refusal)."""
    losses = result.losses_json()
    if not result.dashboard_uuid:
        return AdoptResult(False, losses=losses,
                           detail="the export carries no dashboard uuid; cannot adopt it in place")
    if result.skipped_charts and not force:
        return AdoptResult(
            False, losses=losses, skipped_charts=result.skipped_charts,
            detail=(f"{len(result.skipped_charts)} chart(s) can't be represented in a spec, so "
                    "applying would take them off this dashboard (they would not be deleted). "
                    "Remove or replace them in Superset first, or pass --force to adopt anyway."))
    spec = dict(result.spec)
    spec["dashboard"] = {
        **spec["dashboard"],
        "adopted": {"dashboard_uuid": result.dashboard_uuid, "charts": dict(result.chart_uuids)},
    }
    return AdoptResult(True, spec=spec, losses=losses, skipped_charts=result.skipped_charts)


def adopt_live(slug_or_id: str, client, force: bool = False) -> AdoptResult:
    return adopted_spec(decompile_live(slug_or_id, client), force=force)
