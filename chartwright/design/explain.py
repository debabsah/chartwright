"""`chartwright explain`: where each design-default field's value comes from.

Per chart, one row per field a default.* rule governs: its value, its source
(`spec` = the author's, `filled` = the brain's fill, still holding the value
design.filled records, which the row shows as `recorded`, `superset default` =
unset, so Superset draws its own), the rule, a one-line reason, and how to take
it over. Offline and deterministic: the same advise
run `check` and `apply` carry, read field by field (docs/DESIGN-BRAIN.md sec.16).
"""

from __future__ import annotations

import json

from ..spec import DashboardSpec
from . import advise
from .defaults import FILLS, _show
from .model import DESIGN_BRAIN_VERSION, RuleContext
from .presets import DEFAULT_AUDIENCE, Overlay, load_overlay, params_for
from .standards import Standard


def _row(ctx: RuleContext, chart, fill, pending: dict, ignored: set[str]) -> dict:
    field, key = fill.field, f"{fill.rule}@{chart.name}"
    rec = ctx.filled(chart.name)
    present = ctx.written(chart, field)
    # `filled` only while the chart still holds exactly the value the brain wrote;
    # an edited fill is already the author's (the next --fix records it as null).
    source = ("filled" if ctx.brain_owns(chart, field)
              else "spec" if present else "superset default")
    f = pending.get(key)
    if ctx.standard_holds(chart, field):
        # design.standard_written has the field: a standard's, or the author's after a
        # release; the brain never fills it (one owner per field).
        rec = ctx.spec.design.standard_written[f"charts[{chart.name}].{field}"]
        owned = (rec is not None and not rec.get("released") and present
                 and getattr(chart, field) == rec["value"])
        row = {"field": field, "value": getattr(chart, field) if present else None,
               "source": "standard" if owned else "spec" if present else "superset default",
               "rule": fill.rule,
               "reason": (f"the {rec['layer']} standard wrote it (design.standard_written); "
                          f"the brain leaves it alone" if owned else
                          "released from the standard, so yours; the brain leaves it alone"),
               "override": (f"the standard governs it: its row in the dashboard content "
                            f"section says whether it is locked" if owned
                            else "it is yours: edit it freely")}
        return row
    if key in ignored or fill.rule in ignored:
        reason = f"design.ignore holds {key}: the brain leaves this field alone"
    elif f is not None and f.kind == "release":
        reason = f"yours: {f.why}; advise --fix updates design.filled to match"
    elif f is not None:
        sets = f.fix.get("set", {})
        if field in sets:
            verb = "refreshes it to" if present else "fills"
            reason = f"advise --fix {verb} {_show(sets[field])}: {f.why}"
        else:
            reason = f"advise --fix removes it: {f.why}"
    elif field in rec and rec[field] is None:
        took = (f"yours: you changed the fill" if present
                else f"{fill.superset_text}; you deleted the fill")
        reason = (f"{took}, so the brain fills it no more while "
                  f"design.filled[{chart.name!r}] holds {field!r} as null")
    elif source == "spec":
        reason = "written in the spec, so the brain leaves it alone"
    else:
        _, why = fill.decide(ctx, chart)
        reason = why if source == "filled" else f"{fill.superset_text}; no fill: {why}"
    if source == "spec":
        override = "it is yours: edit it freely"
    elif source == "filled":
        override = f"{fill.override}: any value you write is yours from then on"
    elif f is not None and field in f.fix.get("set", {}):
        override = f"{fill.override}, or ignore {key} to keep Superset's default"
    else:
        override = f"write {field} in the spec to choose it yourself"
    row = {"field": field, "value": getattr(chart, field) if present else None,
           "source": source, "rule": fill.rule, "reason": reason, "override": override}
    if field in rec:
        row["recorded"] = rec[field]  # the value design.filled holds (null: deleted fill)
    return row


def explain(spec: DashboardSpec, *, audience: str | None = None, chart: str | None = None,
            overlay: Overlay | None = None, standard: Standard | None = None) -> dict:
    """The explain payload. Raises ValueError for an unknown chart or a broken overlay.
    With a `standard`, its parameters apply and the payload names its chain."""
    overlay = overlay if overlay is not None else load_overlay()
    report = advise(spec, audience=audience, overlay=overlay, chart=chart, standard=standard)
    aud = report.audience
    ctx = RuleContext(spec, params_for(aud or DEFAULT_AUDIENCE, overlay, standard))
    pending = {f.key: f for f in report.findings if f.kind != "repair" and f.fix}
    ignored = set(report.ignored)
    charts = []
    for c in spec.charts:
        if chart is not None and c.name != chart:
            continue
        rows = [_row(ctx, c, fill, pending, ignored)
                for fill in FILLS.values() if c.type in fill.types]
        charts.append({"chart": c.name, "type": c.type, "fields": rows})
    out = {"stage": "explain", "ok": True, "design_brain": DESIGN_BRAIN_VERSION,
           "audience": aud, "charts": charts}
    if standard is not None:
        out["standard"] = {"name": standard.name, "chain": list(standard.chain),
                           "via": standard.via}
        from . import content as C

        # Only a standard with content (or a spec it once wrote content into) has a
        # dashboard section, so a rules-only standard explains exactly as before.
        if chart is None and (C.has_content(standard)
                              or (spec.design and spec.design.standard_written)):
            out["dashboard"] = C.explain_rows(standard, spec)
    return out


def render_text(payload: dict) -> str:
    head = f"design brain {payload['design_brain']}, audience {payload['audience']}"
    if payload.get("standard"):
        head += f", standard {' -> '.join(payload['standard']['chain'])}"
    lines = [f"Design defaults ({head})"]
    if payload.get("dashboard") is not None:
        from .content import describe

        std = payload["standard"]
        lines += ["", f"Dashboard content (standard {' -> '.join(std['chain'])}, "
                      f"via {std['via']})"]
        if not payload["dashboard"]:
            lines.append("  the standard writes no content into this dashboard")
        width = max((len(r["item"]) for r in payload["dashboard"]), default=0)
        for r in payload["dashboard"]:
            value = describe(r["value"], r["slot"]) if r["value"] is not None else "-"
            lock = "locked" if r["locked"] else "open"
            lines.append(f"  {r['item']:<{width}}  {value:<40} {r['layer'] or '-':<10} "
                         f"{lock:<7} {r['source']}")
            if r.get("standard") is not None:
                lines.append(f"      standard: {describe(r['standard'], r['slot'])}")
            if r.get("pending"):
                lines.append(f"      {r['pending']}")
            lines.append(f"      override: {r['override']}")
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
