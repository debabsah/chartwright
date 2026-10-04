"""Apply the safe-fix subset to the raw spec JSON (same mechanics as absorb:
patch the dict, caller re-validates and writes). Conflicting numeric fixes on
one field merge to the max -- every height fix is a raise-to-minimum, so max
satisfies all of them and the fix loop converges.
"""

from __future__ import annotations

import copy

from .model import Finding


def _md_block(data: dict, addr, ri, ii):
    """The raw markdown-block dict at (container, row, item), or None if the
    layout changed under us (stale indices are skipped, never errors). The
    container is None (layout rows), a tab index, [tab, sub-tab], or "footer"."""
    try:
        lay = data["layout"]
        if addr is None:
            rows = lay["rows"]
        elif addr == "footer":
            rows = lay["footer"]
        elif isinstance(addr, list):
            rows = lay["tabs"][addr[0]]["tabs"][addr[1]]["rows"]
        else:
            rows = lay["tabs"][addr]["rows"]
        row = rows[ri]
        item = (row["row"] if isinstance(row, dict) else row)[ii]
    except (KeyError, IndexError, TypeError):
        return None
    return item if isinstance(item, dict) and "markdown" in item else None


def _record(f: Finding, **rest) -> dict:
    """One applied fix: what changed, from what, and why. `kind` tells a design
    default the brain filled (`fill`) from a repair of something wrong (`repair`)."""
    return {"finding": f.key, "rule": f.rule, "kind": f.kind, **rest,
            "why": f.why or f.detail}


def _update_filled(out: dict, order: list[str], listed: dict, unlisted: dict) -> None:
    """Record fills in design.filled: chart order, sorted fields, empty entries
    (and an emptied block) removed, so a spec without fills carries no trace."""
    design = out.get("design") or {}
    filled = {k: list(v) for k, v in (design.get("filled") or {}).items()}
    for name in set(listed) | set(unlisted):
        fields = (set(filled.get(name, ())) | listed.get(name, set())) - unlisted.get(name, set())
        filled[name] = sorted(fields)
    rank = {n: i for i, n in enumerate(order)}
    filled = {n: filled[n] for n in sorted(filled, key=lambda n: rank.get(n, len(rank)))
              if filled[n]}
    if filled:
        out["design"] = {**design, "filled": filled}
    elif "design" in out:
        design.pop("filled", None)
        if design:
            out["design"] = design
        else:
            out.pop("design")


def apply_fixes(spec_data: dict, findings: list[Finding]) -> tuple[dict, list[dict]]:
    """Returns (patched deep copy, applied entries). Each entry says exactly
    what changed and from what: {finding, rule, kind, chart, set: {field: new},
    was: {field: old}, why} -- an autofix that can't show its diff is a mutation
    the user has to trust blind. A fill may also remove a field (`unset`, shown
    as null in `set`) and records its fields in design.filled (`list`/`unlist`).
    Unknown chart names / stale markdown indices are skipped, not errors."""
    out = copy.deepcopy(spec_data)
    by_name = {c.get("name"): c for c in out.get("charts", [])}
    merged: dict[str, dict] = {}
    unset: dict[str, set] = {}
    listed: dict[str, set] = {}
    unlisted: dict[str, set] = {}
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
                applied.append(_record(f, chart=None, md=f.fix["md"],
                                       set=dict(f.fix["set"]), was=was))
            continue
        name = f.fix.get("chart")
        if name not in by_name:
            continue
        tgt = merged.setdefault(name, {})
        for k, v in f.fix.get("set", {}).items():
            if k in tgt and isinstance(v, (int, float)) and isinstance(tgt[k], (int, float)):
                tgt[k] = max(tgt[k], v)
            else:
                tgt[k] = v
        unset.setdefault(name, set()).update(f.fix.get("unset", ()))
        listed.setdefault(name, set()).update(f.fix.get("list", ()))
        unlisted.setdefault(name, set()).update(f.fix.get("unlist", ()))
        chart_findings.append(f)
    for f in chart_findings:  # report the FINAL merged value per touched field
        name = f.fix["chart"]
        gone = list(f.fix.get("unset", ()))
        applied.append(_record(
            f, chart=name,
            set={**{k: merged[name][k] for k in f.fix.get("set", {})}, **{k: None for k in gone}},
            was={k: by_name[name].get(k) for k in [*f.fix.get("set", {}), *gone]},
        ))
    for name, sets in merged.items():
        by_name[name].update(sets)
    for name, fields in unset.items():
        for k in fields:
            by_name[name].pop(k, None)
    if any(listed.values()) or any(unlisted.values()):
        _update_filled(out, [c.get("name") for c in out.get("charts", [])],
                       {k: v for k, v in listed.items() if v},
                       {k: v for k, v in unlisted.items() if v})
    return out, applied
