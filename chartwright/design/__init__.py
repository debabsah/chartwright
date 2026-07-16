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
from .model import RULES, AdviceReport, Finding, RuleContext
from .presets import DEFAULT_AUDIENCE, Overlay, load_overlay, params_for

DESIGN_BRAIN_VERSION = "1"


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
    suppressed = set(ignore) | set(design.ignore if design else ()) | set(overlay.disable)

    ctx = RuleContext(spec, params, resolution, prober)
    findings: list[Finding] = []
    ignored: list[str] = []
    for r in RULES.values():
        if r.data_aware and resolution is None:
            continue
        for f in r.fn(ctx):
            # Fractional height = absorb's signature: a human already sized
            # this chart in the UI; geometry opinions yield to that.
            if f.rule.startswith("size.") and f.chart and ctx.human_polished(f.chart):
                continue
            if f.rule in suppressed or f.key in suppressed:
                ignored.append(f.key)
                continue
            findings.append(f)

    order = {"error": 0, "warn": 1, "info": 2}
    findings.sort(key=lambda f: (order[f.severity], f.rule, f.chart or ""))
    return AdviceReport(
        ok=not any(f.severity == "error" for f in findings),
        audience=aud, findings=findings, ignored=sorted(set(ignored)),
    )


def advise_and_fix(spec_data: dict, *, audience: str | None = None,
                   ignore: tuple[str, ...] = (), resolution: Resolution | None = None,
                   prober=None, overlay: Overlay | None = None,
                   max_rounds: int = 5) -> tuple[dict, AdviceReport]:
    """Fix loop: advise -> apply safe fixes -> re-advise until no fixable
    findings remain (heights only ever rise, so this converges; max_rounds is
    a backstop, not a tuning knob). Returns (patched spec data, final report
    with .fixed populated)."""
    data = spec_data
    fixed_all: list[str] = []
    for _ in range(max_rounds):
        report = advise(load_spec(data), audience=audience, ignore=ignore,
                        resolution=resolution, prober=prober, overlay=overlay)
        fixable = [f for f in report.findings if f.fix]
        if not fixable:
            report.fixed = fixed_all
            return data, report
        data, applied = apply_fixes(data, fixable)
        fixed_all += applied
    report = advise(load_spec(data), audience=audience, ignore=ignore,
                    resolution=resolution, prober=prober, overlay=overlay)
    report.fixed = fixed_all
    return data, report


# Import for side effect: rule registration. Kept at the bottom so RULES is
# populated by the time advise() iterates it, without a circular import.
from . import rules  # noqa: E402,F401
