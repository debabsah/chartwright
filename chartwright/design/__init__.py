"""The design brain: a toggleable BI/UX intelligence layer.

Two halves over one rulebook (docs/DESIGN-BRAIN.md):
- Tier G, the brief (`brief.py`): design guidance the LLM reads BEFORE
  authoring a spec.
- Tier L, the critic (this module's `advise`): a deterministic linter over
  the finished spec, with a presentation-only autofix subset.

Off is really off: nothing here runs unless asked, and `spec_version` is
untouched.
"""

from __future__ import annotations

from ..resolver import Resolution
from ..spec import DashboardSpec, load_spec
from .fix import apply_fixes
from .model import (DESIGN_BRAIN_VERSION, RULES, SEVERITY_RANK, AdviceReport, Finding,
                    RuleContext, canonical_rule_id, known_rule_ids)
from .presets import DEFAULT_AUDIENCE, Overlay, load_overlay, params_for


def _suppression_keys(entries, unmatched: list[str]) -> set[str]:
    """Canonical ignore keys; entries naming no rule go to `unmatched` (a typo'd rule id
    would otherwise be a suppression that silently never suppresses)."""
    keys: set[str] = set()
    for entry in entries:
        rule_id, _, scope = entry.partition("@")
        canon = canonical_rule_id(rule_id)
        if canon not in RULES:
            unmatched.append(entry)
            continue
        keys.add(f"{canon}@{scope}" if scope else canon)
    return keys


def _suppressed_by(keys: set[str], f: Finding) -> bool:
    return f.rule in keys or f.key in keys or f.scope_key in keys


def advise(spec: DashboardSpec, *, audience: str | None = None,
           ignore: tuple[str, ...] = (), resolution: Resolution | None = None,
           prober=None, overlay: Overlay | None = None,
           chart: str | None = None, strict: bool = False) -> AdviceReport:
    """Run the rulebook. Precedence for the audience: caller > spec.design >
    default. `ignore` entries are rule ids ('size.pie-geometry') or per-chart
    keys ('size.pie-geometry@Sales by Region'); the spec's design.ignore and
    the overlay's disable list are merged in. `chart` keeps only that chart's
    findings (and its ignored and polished keys); a name the spec lacks is a
    ValueError.

    `strict` is for a gate (advise --strict, check/apply --design strict): the
    per-machine overlay may then only RAISE a severity. Its disable list, any
    severity it would lower and its parameters are set aside, so the gate passes or
    fails the same on every machine; the report's `overlay` block names what was
    set aside, and, without `strict`, everything the overlay changed."""
    if chart is not None and chart not in {c.name for c in spec.charts}:
        raise ValueError(f"no chart named {chart!r}; charts: {sorted(c.name for c in spec.charts)}")
    overlay = overlay if overlay is not None else load_overlay()
    design = spec.design
    aud = audience or (design.audience if design else None) or DEFAULT_AUDIENCE
    # A parameter has no direction (a larger fold budget is looser, a larger minimum
    # height stricter), so a strict gate reads the built-in presets alone.
    params = params_for(aud, None if strict else overlay)

    unmatched: list[str] = []
    suppressed = _suppression_keys(
        set(ignore) | set(design.ignore if design else ()), unmatched)
    # The overlay's own disable list: honoured, and reported, outside a strict gate.
    disabled = set() if strict else _suppression_keys(set(overlay.disable), unmatched)
    unmatched = sorted(set(unmatched))
    changed: list[dict] = []          # what the overlay did to this run's findings
    refused: dict[str, str] = {}      # severity overrides a strict gate would not lower

    ctx = RuleContext(spec, params, resolution, prober)
    findings: list[Finding] = []
    ignored: list[str] = []
    polished: list[str] = []
    for r in RULES.values():
        if r.data_aware and resolution is None:
            continue
        for f in r.fn(ctx):
            # Per-deployment severity override (single choke point). A strict gate
            # takes it only where it raises the finding's level.
            level = overlay.severity.get(f.rule)
            moved = None  # reported in `changed` only if the finding is reported
            if level and level != f.severity:
                if strict and SEVERITY_RANK[level] > SEVERITY_RANK[f.severity]:
                    refused[f.rule] = level
                else:
                    moved = {"finding": f.key if f.chart else f.scope_key,
                             "severity": [f.severity, level]}
                    f.severity = level
            # Explicit intent first: an ignore entry is ALWAYS visible in
            # `ignored`, even when the polish skip below would also apply.
            if _suppressed_by(suppressed, f):
                ignored.append(f.key if f.chart else f.scope_key)
                continue
            if _suppressed_by(disabled, f):
                ignored.append(f.key if f.chart else f.scope_key)
                changed.append({"finding": f.key if f.chart else f.scope_key, "disabled": True})
                continue
            # Fractional height = absorb's signature: a human already sized
            # this chart in the UI; HEIGHT opinions yield to that. Width and
            # data complaints survive -- absorb cannot write widths, so a
            # fractional height says nothing about them.
            # The skip is REPORTED (`polished`): this is an inferred signal,
            # and an inferred signal that silences a rule invisibly is
            # indistinguishable from the rule having passed.
            if f.height_driven and f.chart and ctx.human_polished(f.chart):
                polished.append(f.key)
                continue
            # A height fix on a sketch-drawn chart is real but leaves the
            # drawing stale; say so instead of silently diverging (WYSIWYG).
            if (f.fix and "height" in (f.fix.get("set") or {}) and f.chart
                    and ctx.is_sketch(f.chart)):
                f.detail += (" (the fix writes an explicit height that overrides the "
                             "sketch; to keep the drawing true, repeat the band's "
                             "line(s) instead)")
            findings.append(f)
            if moved:
                changed.append(moved)

    if chart is not None:
        findings = [f for f in findings if f.chart == chart]
        ignored = [k for k in ignored if k.endswith(f"@{chart}")]
        polished = [k for k in polished if k.endswith(f"@{chart}")]
        changed = [c for c in changed if c["finding"].endswith(f"@{chart}")]
    findings.sort(key=lambda f: (SEVERITY_RANK[f.severity], f.rule, f.chart or ""))
    return AdviceReport(
        ok=not any(f.severity == "error" for f in findings),
        audience=aud, findings=findings, ignored=sorted(set(ignored)),
        unmatched_ignores=unmatched, polished=sorted(set(polished)),
        overlay=_overlay_report(overlay, aud, strict, changed, refused),
    )


def _overlay_report(overlay: Overlay, audience: str, strict: bool,
                    changed: list[dict], refused: dict[str, str]) -> dict | None:
    """The `overlay` block of an AdviceReport: which per-machine design.yaml this run
    read, what it set, what it changed in this run's findings, and, under a strict
    gate, what was set aside. None when no overlay is in play."""
    if not overlay.active:
        return None
    params = overlay.param_names(audience)
    severity = dict(sorted(overlay.severity.items()))
    out: dict = {"path": str(overlay.source) if overlay.source else None, "strict": strict}
    if strict:
        out["severity"] = {k: v for k, v in severity.items() if k not in refused}
        out["changed"] = sorted(changed, key=lambda c: c["finding"])
        set_aside = {"params": params, "disable": sorted(overlay.disable),
                     "severity": dict(sorted(refused.items()))}
        set_aside = {k: v for k, v in set_aside.items() if v}
        if set_aside:
            out["set_aside"] = set_aside
        return out
    out.update(params=params, disable=sorted(overlay.disable), severity=severity,
               changed=sorted(changed, key=lambda c: c["finding"]))
    return out


def advise_and_fix(spec_data: dict, *, audience: str | None = None,
                   ignore: tuple[str, ...] = (), resolution: Resolution | None = None,
                   prober=None, overlay: Overlay | None = None,
                   max_rounds: int = 8, fills: bool = True,
                   chart: str | None = None, strict: bool = False) -> tuple[dict, AdviceReport]:
    """Fix loop: advise -> apply safe fixes -> re-advise until no fixable
    findings remain. Convergence invariant: KPI heights are clamped into 2..6
    by exactly one rule (size.kpi-height); every OTHER height fix only raises,
    and conflicting raises merge to max() -- so no two rules fight over one
    chart's height and the loop terminates. max_rounds and the no-progress
    check are backstops for invariant violations, not tuning knobs.

    Two phases: repairs (geometry, orientation) first, until none is left; only
    then the design defaults (the default.* fills, sec.16), which read the settled
    geometry. A fill that unsettles geometry sends the loop back to repairs, and a
    moved height refreshes the fills that read it on the next pass. `fills=False`
    stops after the repairs (redesign: only advise --fix and fix_spec write fills);
    `chart` fixes that one chart; `strict` advises as a gate does (see advise).
    Returns (patched spec data, final report with .fixed populated)."""
    data = spec_data
    fixed_all: list[dict] = []
    for _ in range(max_rounds):
        report = advise(load_spec(data), audience=audience, ignore=ignore,
                        resolution=resolution, prober=prober, overlay=overlay, chart=chart,
                        strict=strict)
        fixable = [f for f in report.findings if f.fix and (fills or f.kind == "repair")]
        # Fills wait until no repair is left: they read the geometry repairs settle.
        batch = [f for f in fixable if f.kind == "repair"] or fixable
        if not batch:
            report.fixed = fixed_all
            return data, report
        new_data, applied = apply_fixes(data, batch)
        if new_data == data:  # invariant violated: fixes made no progress
            report.fixed = fixed_all
            report.findings.append(Finding(
                rule="design.fix-stalled", severity="warn", chart=None, where="fix loop",
                detail=f"fixes {sorted(f.key for f in batch)} made no progress; report a rule bug",
            ))
            return data, report
        data = new_data
        fixed_all += applied
    report = advise(load_spec(data), audience=audience, ignore=ignore,
                    resolution=resolution, prober=prober, overlay=overlay, chart=chart,
                    strict=strict)
    report.fixed = fixed_all
    return data, report


def advice_payload(spec: DashboardSpec, resolution: Resolution | None = None, *,
                   audience: str | None = None, strict: bool = False) -> dict:
    """The advice block check and apply (CLI and MCP alike) carry. It never breaks the
    pipeline: a bad overlay degrades to an error note inside the block, not a crash.
    Under a strict gate that note BLOCKS (see gate_block); otherwise it is reported and
    the pipeline continues."""
    try:
        return advise(spec, audience=audience, resolution=resolution, strict=strict).payload()
    except Exception as e:  # noqa: BLE001 - advice must NEVER break check/apply
        return {"stage": "design", "ok": True, "design_brain": DESIGN_BRAIN_VERSION,
                "audience": audience or DEFAULT_AUDIENCE,
                "counts": {"error": 0, "warn": 0, "info": 0}, "findings": [],
                "fixed": [], "ignored": [],
                "errors": [{"code": "overlay" if isinstance(e, ValueError) else "advice",
                            "detail": str(e)}]}


def advise_gate_detail(report: AdviceReport) -> str:
    """The design_gate error `advise` (CLI --strict, MCP advise_spec strict) names when
    its report blocks."""
    if report.counts["error"] == 0:
        return ("warn-severity findings block under --strict; fix them, run "
                "`chartwright advise --fix`, or record deliberate exceptions "
                "in the spec's design.ignore")
    return ("error-severity findings block; fix them or record deliberate "
            "exceptions in the spec's design.ignore")


def gate_block(advice: dict) -> str | None:
    """Why a strict design gate blocks on this advice block, or None. A gate that cannot
    EVALUATE fails closed: advice degrades to counts of zero when the overlay is broken
    (advice_payload), and a silent pass there would turn one typo in a design.yaml into
    a disarmed gate everywhere it is used."""
    if advice.get("errors"):
        return ("design advice could not be evaluated: "
                + "; ".join(e.get("detail", "") for e in advice["errors"])
                + " -- fix it or pass --design off")
    if advice["counts"]["error"] or advice["counts"]["warn"]:
        return ("design findings block under --design strict; fix them, run "
                "`chartwright advise --fix`, or record deliberate exceptions "
                "in the spec's design.ignore")
    return None


# Import for side effect: rule registration. Kept at the bottom so RULES is
# populated by the time advise() iterates it, without a circular import. The
# default.* fills come after the rules, whose grid helpers they share.
from . import rules  # noqa: E402,F401
from . import defaults  # noqa: E402,F401
