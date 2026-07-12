"""Absorb UI height polish back into the spec file: the scalpel to
decompile's bulldozer.

Workflow this serves (the owner's ruling, 2026-07-11): the sketch gets a
dashboard 90-95% built in one shot; the last few percent of fine-grain sizing
happens by hand in the Superset UI. Without absorb, the next `chartwright apply`
rebuilds the layout from the spec and destroys that polish. `chartwright absorb`
reads the LIVE dashboard's geometry and patches ONLY per-chart heights into
the existing spec file; sketch, legend, metrics, structure untouched.

Heights only, by design: Superset widths ARE twelfths, so the UI cannot
express any width the sketch can't; width changes belong in the drawing.
Width drift is therefore REPORTED as advice, never written. Heights are where
the UI has finer resolution (8px) than the sketch quantum (40px); absorbed
values are written as fractional 40px units in 0.2 steps (8px), so the
polished geometry round-trips EXACTLY through the next apply.
"""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field

from . import ids
from .compiler import ROW_UNITS_PER_SPEC_UNIT, _position
from .spec import DashboardSpec


@dataclass
class AbsorbReport:
    ok: bool
    absorbed: list[dict] = field(default_factory=list)      # {chart, from_px, to_px, height}
    width_advice: list[dict] = field(default_factory=list)  # {chart, spec_width, live_width}
    unmatched_live: list[str] = field(default_factory=list)
    detail: str | None = None

    def to_json(self) -> str:
        return json.dumps({"stage": "absorb", **asdict(self)}, indent=2)


def _exact_units(row_units: int) -> float | int:
    """Superset row units (8px) -> spec units (40px), exactly representable."""
    units = row_units / ROW_UNITS_PER_SPEC_UNIT
    return int(units) if units.is_integer() else round(units, 1)


def absorb_heights(spec: DashboardSpec, spec_data: dict, live_position: dict) -> tuple[dict, AbsorbReport]:
    """Pure core: returns (patched copy of spec_data, report). spec_data is the
    raw JSON the spec file holds; only charts[*].height values are touched."""
    report = AbsorbReport(ok=True)
    slug = spec.dashboard.slug

    compiled = {
        v["meta"]["uuid"]: v["meta"]
        for v in _position(spec).values()
        if isinstance(v, dict) and v.get("type") == "CHART"
    }
    live = {
        v["meta"]["uuid"]: v["meta"]
        for v in live_position.values()
        if isinstance(v, dict) and v.get("type") == "CHART" and (v.get("meta") or {}).get("uuid")
    }
    report.unmatched_live = sorted(
        (m.get("sliceName") or u) for u, m in live.items() if u not in compiled
    )

    out = copy.deepcopy(spec_data)
    charts_by_name = {c["name"]: c for c in out.get("charts", [])}

    for chart in spec.charts:
        u = str(ids.chart_uuid(slug, chart.name))
        live_meta = live.get(u)
        if live_meta is None:
            continue  # chart not on the live board (e.g. never applied); nothing to absorb
        want, have = compiled[u], live_meta
        if have.get("width") != want["width"]:
            report.width_advice.append({
                "chart": chart.name,
                "spec_width": want["width"],
                "live_width": have.get("width"),
                "note": "widths are twelfths; redraw the sketch line (absorb writes heights only)",
            })
        live_h = have.get("height")
        if not isinstance(live_h, int) or live_h == want["height"]:
            continue
        charts_by_name[chart.name]["height"] = _exact_units(live_h)
        report.absorbed.append({
            "chart": chart.name,
            "from_px": want["height"] * 8,
            "to_px": live_h * 8,
            "height": charts_by_name[chart.name]["height"],
        })
    return out, report
