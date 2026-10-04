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
from .standards import Standard, StandardsError, StandardsSource, report_block


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
           chart: str | None = None, strict: bool = False,
           standard: Standard | None = None) -> AdviceReport:
    """Run the rulebook. Precedence for the audience: caller > spec.design >
    default. `ignore` entries are rule ids ('size.pie-geometry') or per-chart
    keys ('size.pie-geometry@Sales by Region'); the spec's design.ignore and
    the overlay's disable list are merged in. `chart` keeps only that chart's
    findings (and its ignored and polished keys); a name the spec lacks is a
    ValueError.

    `strict` is for a gate (advise --strict, check/apply --design strict): the
    per-machine overlay then counts for nothing. Its disable list, its severities
    and its parameters are all set aside, so the gate passes or fails the same on
    every machine; the report's `overlay` block names what was set aside, and,
    without `strict`, everything the overlay changed.

    `standard` is the spec's resolved standard (design/standards.py), applied between
    the audience preset and the spec's design block: its parameters, severities and
    disable list apply in every run, strict or not. A rule it locks can't be silenced
    by `ignore`, design.ignore, the overlay or the polish skip, and the overlay can't
    move a parameter it locks; whatever a lock set aside is reported."""
    if chart is not None and chart not in {c.name for c in spec.charts}:
        raise ValueError(f"no chart named {chart!r}; charts: {sorted(c.name for c in spec.charts)}")
    overlay = overlay if overlay is not None else load_overlay()
    design = spec.design
    aud = audience or (design.audience if design else None) or DEFAULT_AUDIENCE
    # A parameter has no direction (a larger fold budget is looser, a larger minimum
    # height stricter), so a strict gate reads the built-in presets alone (and the
    # standard, which is the same on every machine).
    params = params_for(aud, None if strict else overlay, standard)
    locked = standard.locked_rules if standard else {}

    def is_locked(entry: str) -> bool:
        return canonical_rule_id(entry.partition("@")[0]) in locked

    unmatched: list[str] = []
    requested = set(ignore) | set(design.ignore if design else ())
    refused = sorted(e for e in requested if is_locked(e))
    suppressed = _suppression_keys(requested - set(refused), unmatched)
    # The overlay's own disable list: honoured, and reported, outside a strict gate.
    disabled = set() if strict else _suppression_keys(
        {e for e in overlay.disable if not is_locked(e)}, unmatched)
    std_disabled = set(standard.disable) if standard else set()
    unmatched = sorted(set(unmatched))
    changed: list[dict] = []          # what the overlay did to this run's findings
    # The standard's severities, then (outside a strict gate, which takes no severity
    # from the overlay, raises included) the overlay's for rules no standard locks.
    severity = dict(standard.severity) if standard else {}
    set_by = {r: standard.origins[f"severity.{r}"] for r in severity} if standard else {}
    if not strict:
        for r, level in overlay.severity.items():
            if not is_locked(r):
                severity[r] = level
                set_by[r] = "overlay"

    ctx = RuleContext(spec, params, resolution, prober, standard)
    findings: list[Finding] = []
    ignored: list[str] = []
    polished: list[str] = []
    for r in RULES.values():
        if r.data_aware and resolution is None:
            continue
        for f in r.fn(ctx):
            # Severity override (single choke point): the standard's, or the overlay's.
            level = severity.get(f.rule)
            moved = None  # reported in `changed` only if the finding is reported
            if level and level != f.severity:
                if set_by[f.rule] == "overlay":
                    before = (standard.severity.get(f.rule) if standard else None) or f.severity
                    if before != level:
                        moved = {"finding": f.key if f.chart else f.scope_key,
                                 "severity": [before, level]}
                f.severity = level
            if standard is not None:
                f.layer = set_by.get(f.rule, "rulebook")
                f.locked = f.rule in locked
            # Explicit intent first: an ignore entry is ALWAYS visible in
            # `ignored`, even when the polish skip below would also apply.
            if _suppressed_by(suppressed, f):
                ignored.append(f.key if f.chart else f.scope_key)
                continue
            if f.rule in std_disabled:
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
            # indistinguishable from the rule having passed. A locked rule never
            # yields to it: a hand-written 7.5 would otherwise unlock it.
            if (f.height_driven and f.chart and ctx.human_polished(f.chart)
                    and f.rule not in locked):
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
        overlay=_overlay_report(overlay, aud, strict, changed, standard),
        standard=report_block(standard, refused) if standard else None,
    )


def _overlay_report(overlay: Overlay, audience: str, strict: bool,
                    changed: list[dict], standard: Standard | None = None) -> dict | None:
    """The `overlay` block of an AdviceReport: which per-machine design.yaml this run
    read, what it set, what it changed in this run's findings, and what was set aside
    (so a user sees why the file had no effect): everything under a strict gate, and
    outside one, the entries a standard's lock overrode. None when no overlay is in
    play."""
    if not overlay.active:
        return None
    params = overlay.param_names(audience)
    severity = dict(sorted(overlay.severity.items()))
    out: dict = {"path": str(overlay.source) if overlay.source else None, "strict": strict}
    if strict:
        set_aside = {"params": params, "disable": sorted(overlay.disable),
                     "severity": severity}
        set_aside = {k: v for k, v in set_aside.items() if v}
        if set_aside:
            out["set_aside"] = set_aside
        return out
    disable = sorted(overlay.disable)
    set_aside = {}
    if standard is not None:
        rules, lp = standard.locked_rules, standard.locked_params

        def is_locked(entry: str) -> bool:
            return canonical_rule_id(entry.partition("@")[0]) in rules

        set_aside = {"params": [p for p in params if p in lp],
                     "disable": [e for e in disable if is_locked(e)],
                     "severity": {r: v for r, v in severity.items() if is_locked(r)}}
        set_aside = {k: v for k, v in set_aside.items() if v}
        params = [p for p in params if p not in lp]
        disable = [e for e in disable if not is_locked(e)]
        severity = {r: v for r, v in severity.items() if not is_locked(r)}
    out.update(params=params, disable=disable, severity=severity,
               changed=sorted(changed, key=lambda c: c["finding"]))
    if set_aside:
        out["set_aside"] = set_aside
    return out


def advise_and_fix(spec_data: dict, *, audience: str | None = None,
                   ignore: tuple[str, ...] = (), resolution: Resolution | None = None,
                   prober=None, overlay: Overlay | None = None,
                   max_rounds: int = 8, fills: bool = True,
                   chart: str | None = None, strict: bool = False,
                   standard: Standard | None = None) -> tuple[dict, AdviceReport]:
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
    `chart` fixes that one chart; `strict` advises as a gate does and `standard` applies
    the spec's standard (see advise).
    Returns (patched spec data, final report with .fixed populated)."""
    data = spec_data
    fixed_all: list[dict] = []
    for _ in range(max_rounds):
        report = advise(load_spec(data), audience=audience, ignore=ignore,
                        resolution=resolution, prober=prober, overlay=overlay, chart=chart,
                        strict=strict, standard=standard)
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
                    strict=strict, standard=standard)
    report.fixed = fixed_all
    return data, report


def advice_payload(spec: DashboardSpec, resolution: Resolution | None = None, *,
                   audience: str | None = None, strict: bool = False,
                   standards: StandardsSource | None = None) -> dict:
    """The advice block check and apply (CLI and MCP alike) carry. It never breaks the
    pipeline: a bad overlay, or a standard that can't be resolved from `standards`,
    degrades to an error note inside the block, not a crash. Under a strict gate that
    note BLOCKS (see gate_block); otherwise it is reported and the pipeline continues."""
    try:
        standard = standards.standard_for(spec) if standards is not None else None
        return advise(spec, audience=audience, resolution=resolution, strict=strict,
                      standard=standard).payload()
    except Exception as e:  # noqa: BLE001 - advice must NEVER break check/apply
        code = (e.code if isinstance(e, StandardsError)
                else "overlay" if isinstance(e, ValueError) else "advice")
        return {"stage": "design", "ok": True, "design_brain": DESIGN_BRAIN_VERSION,
                "audience": audience or DEFAULT_AUDIENCE,
                "counts": {"error": 0, "warn": 0, "info": 0}, "findings": [],
                "fixed": [], "ignored": [],
                "errors": [{"code": code, "detail": str(e)}]}


def locked_note(findings: list[dict], levels: tuple[str, ...]) -> str:
    """The sentence a gate adds when blocking findings belong to rules a standard locks:
    design.ignore can't silence those, so the gate must not suggest it for them."""
    rules = sorted({f["rule"] for f in findings if f.get("locked") and f["severity"] in levels})
    if not rules:
        return ""
    return (f"; a standard locks {', '.join(rules)}, so design.ignore can't silence "
            f"{'it' if len(rules) == 1 else 'them'}: fix the spec")


def advise_gate_detail(report: AdviceReport) -> str:
    """The design_gate error `advise` (CLI --strict, MCP advise_spec strict) names when
    its report blocks."""
    findings = [f.as_dict() for f in report.findings]
    if report.counts["error"] == 0:
        return ("warn-severity findings block under --strict; fix them, run "
                "`chartwright advise --fix`, or record deliberate exceptions "
                "in the spec's design.ignore" + locked_note(findings, ("warn",)))
    return ("error-severity findings block; fix them or record deliberate "
            "exceptions in the spec's design.ignore" + locked_note(findings, ("error",)))


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
                "in the spec's design.ignore"
                + locked_note(advice["findings"], ("error", "warn")))
    return None


# Import for side effect: rule registration. Kept at the bottom so RULES is
# populated by the time advise() iterates it, without a circular import. The
# default.* fills come after the rules, whose grid helpers they share.
from . import rules  # noqa: E402,F401
from . import defaults  # noqa: E402,F401
from . import standard_rules  # noqa: E402,F401
