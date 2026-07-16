"""Apply the safe-fix subset to the raw spec JSON (same mechanics as absorb:
patch the dict, caller re-validates and writes). Conflicting numeric fixes on
one field merge to the max -- every height fix is a raise-to-minimum, so max
satisfies all of them and the fix loop converges.
"""

from __future__ import annotations

import copy

from .model import Finding


def apply_fixes(spec_data: dict, findings: list[Finding]) -> tuple[dict, list[str]]:
    """Returns (patched deep copy, applied finding keys). Only findings that
    carry a fix are touched; unknown chart names are skipped, not errors."""
    out = copy.deepcopy(spec_data)
    by_name = {c.get("name"): c for c in out.get("charts", [])}
    merged: dict[str, dict] = {}
    applied: list[str] = []
    for f in findings:
        if not f.fix or f.fix.get("chart") not in by_name:
            continue
        tgt = merged.setdefault(f.fix["chart"], {})
        for k, v in f.fix["set"].items():
            if k in tgt and isinstance(v, (int, float)) and isinstance(tgt[k], (int, float)):
                tgt[k] = max(tgt[k], v)
            else:
                tgt[k] = v
        applied.append(f.key)
    for name, sets in merged.items():
        by_name[name].update(sets)
    return out, applied
