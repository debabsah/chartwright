"""Apply the safe-fix subset to the raw spec JSON (same mechanics as absorb:
patch the dict, caller re-validates and writes). Conflicting numeric fixes on
one field merge to the max -- every height fix is a raise-to-minimum, so max
satisfies all of them and the fix loop converges.
"""

from __future__ import annotations

import copy

from .model import Finding


def _md_block(data: dict, ti, ri, ii):
    """The raw markdown-block dict at (tab, row, item), or None if the layout
    changed under us (stale indices are skipped, never errors)."""
    try:
        rows = (data["layout"]["tabs"][ti]["rows"] if ti is not None
                else data["layout"]["rows"])
        item = rows[ri][ii]
    except (KeyError, IndexError, TypeError):
        return None
    return item if isinstance(item, dict) and "markdown" in item else None


def apply_fixes(spec_data: dict, findings: list[Finding]) -> tuple[dict, list[dict]]:
    """Returns (patched deep copy, applied entries). Each entry says exactly
    what changed and from what: {finding, rule, chart, set: {field: new},
    was: {field: old}} -- an autofix that can't show its diff is a mutation
    the user has to trust blind. Unknown chart names / stale markdown indices
    are skipped, not errors."""
    out = copy.deepcopy(spec_data)
    by_name = {c.get("name"): c for c in out.get("charts", [])}
    merged: dict[str, dict] = {}
    chart_findings: list[Finding] = []
    applied: list[dict] = []
    for f in findings:
        if not f.fix:
            continue
        if "md" in f.fix:  # markdown blocks have no name; addressed by position
            block = _md_block(out, *f.fix["md"])
            if block is not None:
                was = {k: block.get(k) for k in f.fix["set"]}
                block.update(f.fix["set"])
                applied.append({"finding": f.key, "rule": f.rule, "chart": None,
                                "md": f.fix["md"], "set": dict(f.fix["set"]), "was": was})
            continue
        if f.fix.get("chart") not in by_name:
            continue
        tgt = merged.setdefault(f.fix["chart"], {})
        for k, v in f.fix["set"].items():
            if k in tgt and isinstance(v, (int, float)) and isinstance(tgt[k], (int, float)):
                tgt[k] = max(tgt[k], v)
            else:
                tgt[k] = v
        chart_findings.append(f)
    for f in chart_findings:  # report the FINAL merged value per touched field
        name = f.fix["chart"]
        applied.append({
            "finding": f.key, "rule": f.rule, "chart": name,
            "set": {k: merged[name][k] for k in f.fix["set"]},
            "was": {k: by_name[name].get(k) for k in f.fix["set"]},
        })
    for name, sets in merged.items():
        by_name[name].update(sets)
    return out, applied
