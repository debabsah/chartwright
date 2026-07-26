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
from .model import (DESIGN_BRAIN_VERSION, RULES, AdviceReport, Finding, RuleContext,
                    canonical_rule_id, known_rule_ids)
from .presets import DEFAULT_AUDIENCE, Overlay, load_overlay, params_for


def advise(spec: DashboardSpec, *, audience: str | None = None,
           ignore: tuple[str, ...] = (), resolution: Resolution | None = None,
           prober=None, overlay: Overlay | None = None) -> AdviceReport:
    """Run the rulebook. Precedence for the audience: caller > spec.design >
    default. `ignore` entries are rule ids ('size.pie-geometry') or per-chart
    keys ('size.pie-geometry@Sales by Region'); the spec's design.ignore and
    the overlay's disable list are merged in."""
    overlay = overlay if overlay is not None else load_overlay()
    design = spec.design
    aud = audience or (design.audience if design else None) or DEFAULT_AUDIENCE
    params = params_for(aud, overlay)

    # Ignore entries are validated up front: a typo'd rule id would otherwise
    # be a suppression that silently never suppresses.
    raw_suppressed = set(ignore) | set(design.ignore if design else ()) | set(overlay.disable)
    suppressed: set[str] = set()
    unmatched: list[str] = []
    for entry in raw_suppressed:
        rule_id, _, scope = entry.partition("@")
        canon = canonical_rule_id(rule_id)
        if canon not in RULES:
            unmatched.append(entry)
            continue
        suppressed.add(f"{canon}@{scope}" if scope else canon)

    ctx = RuleContext(spec, params, resolution, prober)
    findings: list[Finding] = []
    ignored: list[str] = []
    polished: list[str] = []
    for r in RULES.values():
        if r.data_aware and resolution is None:
            continue
        for f in r.fn(ctx):
            # Per-deployment severity override (single choke point).
            f.severity = overlay.severity.get(f.rule, f.severity)
            # Explicit intent first: an ignore entry is ALWAYS visible in
            # `ignored`, even when the polish skip below would also apply.
            if f.rule in suppressed or f.key in suppressed or f.scope_key in suppressed:
                ignored.append(f.key if f.chart else f.scope_key)
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

    order = {"error": 0, "warn": 1, "info": 2}
    findings.sort(key=lambda f: (order[f.severity], f.rule, f.chart or ""))
    return AdviceReport(
        ok=not any(f.severity == "error" for f in findings),
        audience=aud, findings=findings, ignored=sorted(set(ignored)),
        unmatched_ignores=sorted(unmatched), polished=sorted(set(polished)),
    )


def advise_and_fix(spec_data: dict, *, audience: str | None = None,
                   ignore: tuple[str, ...] = (), resolution: Resolution | None = None,
                   prober=None, overlay: Overlay | None = None,
                   max_rounds: int = 5) -> tuple[dict, AdviceReport]:
    """Fix loop: advise -> apply safe fixes -> re-advise until no fixable
    findings remain. Convergence invariant: KPI heights are clamped into 2..6
    by exactly one rule (size.kpi-height); every OTHER height fix only raises,
    and conflicting raises merge to max() -- so no two rules fight over one
    chart's height and the loop terminates. max_rounds and the no-progress
    check are backstops for invariant violations, not tuning knobs.
    Returns (patched spec data, final report with .fixed populated)."""
    data = spec_data
    fixed_all: list[str] = []
    for _ in range(max_rounds):
        report = advise(load_spec(data), audience=audience, ignore=ignore,
                        resolution=resolution, prober=prober, overlay=overlay)
        fixable = [f for f in report.findings if f.fix]
        if not fixable:
            report.fixed = fixed_all
            return data, report
        new_data, applied = apply_fixes(data, fixable)
        if new_data == data:  # invariant violated: fixes made no progress
            report.fixed = fixed_all
            report.findings.append(Finding(
                rule="design.fix-stalled", severity="warn", chart=None, where="fix loop",
                detail=f"fixes {sorted(f.key for f in fixable)} made no progress; report a rule bug",
            ))
            return data, report
        data = new_data
        fixed_all += applied
    report = advise(load_spec(data), audience=audience, ignore=ignore,
                    resolution=resolution, prober=prober, overlay=overlay)
    report.fixed = fixed_all
    return data, report


# Import for side effect: rule registration. Kept at the bottom so RULES is
# populated by the time advise() iterates it, without a circular import.
from . import rules  # noqa: E402,F401
