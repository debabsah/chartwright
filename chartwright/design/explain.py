"""`chartwright explain`: where each design-default field's value comes from.

Per chart, one row per field a default.* rule governs: its value, its source
(`spec` = the author's, `filled` = the brain's fill, listed in design.filled,
`superset default` = unset, so Superset draws its own), the rule, a one-line
reason, and how to take it over. Offline and deterministic: the same advise
run `check` and `apply` carry, read field by field (docs/DESIGN-BRAIN.md sec.16).
"""

from __future__ import annotations

import json

from ..spec import DashboardSpec
from . import advise
from .defaults import FILLS, _show
from .model import DESIGN_BRAIN_VERSION, RuleContext
from .presets import DEFAULT_AUDIENCE, Overlay, load_overlay, params_for


def _row(ctx: RuleContext, chart, fill, pending: dict, ignored: set[str]) -> dict:
    field, key = fill.field, f"{fill.rule}@{chart.name}"
    listed, present = field in ctx.filled(chart.name), ctx.written(chart, field)
    source = "filled" if listed and present else "spec" if present else "superset default"
    f = pending.get(key)
    if key in ignored or fill.rule in ignored:
        reason = f"design.ignore holds {key}: the brain leaves this field alone"
    elif f is not None:
        sets = f.fix.get("set", {})
        if field in sets:
            verb = "refreshes it to" if present else "fills"
            reason = f"advise --fix {verb} {_show(sets[field])}: {f.why}"
        elif field in f.fix.get("unset", ()):
            reason = f"advise --fix removes it: {f.why}"
        else:
            reason = f"advise --fix drops it from design.filled: {f.why}"
    elif source == "spec":
        reason = "written in the spec, so the brain leaves it alone"
    else:
        _, why = fill.decide(ctx, chart)
        reason = why if source == "filled" else f"{fill.superset_text}; no fill: {why}"
    if source == "spec":
        override = "it is yours: edit it freely"
    elif source == "filled":
        override = f"{fill.override}, or remove {field!r} from design.filled to keep this value"
    elif f is not None and field in f.fix.get("set", {}):
        override = f"{fill.override}, or ignore {key} to keep Superset's default"
    else:
        override = f"write {field} in the spec to choose it yourself"
    return {"field": field, "value": getattr(chart, field) if present else None,
            "source": source, "rule": fill.rule, "reason": reason, "override": override}


def explain(spec: DashboardSpec, *, audience: str | None = None, chart: str | None = None,
            overlay: Overlay | None = None) -> dict:
    """The explain payload. Raises ValueError for an unknown chart or a broken overlay."""
    overlay = overlay if overlay is not None else load_overlay()
    report = advise(spec, audience=audience, overlay=overlay, chart=chart)
    aud = report.audience
    ctx = RuleContext(spec, params_for(aud or DEFAULT_AUDIENCE, overlay))
    pending = {f.key: f for f in report.findings if f.kind == "fill" and f.fix}
    ignored = set(report.ignored)
    charts = []
    for c in spec.charts:
        if chart is not None and c.name != chart:
            continue
        rows = [_row(ctx, c, fill, pending, ignored)
                for fill in FILLS.values() if c.type in fill.types]
        charts.append({"chart": c.name, "type": c.type, "fields": rows})
    return {"stage": "explain", "ok": True, "design_brain": DESIGN_BRAIN_VERSION,
            "audience": aud, "charts": charts}


def render_text(payload: dict) -> str:
    lines = [f"Design defaults (design brain {payload['design_brain']}, "
             f"audience {payload['audience']})"]
    for c in payload["charts"]:
        lines += ["", f"{c['chart']}  ({c['type']})"]
        if not c["fields"]:
            lines.append(f"  no design default applies to a {c['type']} chart")
        for r in c["fields"]:
            value = "-" if r["value"] is None else json.dumps(r["value"], ensure_ascii=False)
            lines += [f"  {r['field']:<15} {value:<12} {r['source']:<17} {r['rule']}",
                      f"      {r['reason']}",
                      f"      override: {r['override']}"]
    return "\n".join(lines) + "\n"
