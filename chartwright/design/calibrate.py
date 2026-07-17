"""Phase 3: the brain learns from the humans it defers to.

Every non-dry-run `chartwright absorb` appends the heights humans dragged
charts to (the polish the brain is told to respect) to an append-only log.
`chartwright calibrate` mines that log: when a chart type is systematically
resized away from its recommended height, it proposes -- and with --write,
records -- a new recommended height in the design overlay, which both the
brief and the size-rule autofixes then honor.

Signal hygiene: one vote per (profile, slug, chart), latest wins, so one
dashboard absorbed ten times doesn't outvote nine dashboards absorbed once.
"""

from __future__ import annotations

import datetime
import json
import statistics
from pathlib import Path

from ..spec import DEFAULT_HEIGHT, DashboardSpec
from .presets import design_dir, load_overlay, overlay_path


def log_path() -> Path:
    return design_dir() / "absorb-log.jsonl"


def record_absorb(profile: str, spec: DashboardSpec, absorbed: list[dict]) -> None:
    """Append one event per absorbed height. Best-effort: a failure to log
    never fails an absorb."""
    if not absorbed:
        return
    types = {c.name: c.type for c in spec.charts}
    audience = spec.design.audience if spec.design else None
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().isoformat(timespec="seconds")
        with path.open("a", encoding="utf-8") as f:
            for row in absorbed:
                f.write(json.dumps({
                    "ts": ts, "profile": profile, "slug": spec.dashboard.slug,
                    "chart": row["chart"], "type": types.get(row["chart"]),
                    "height": row["height"], "audience": audience,
                }) + "\n")
    except OSError:
        pass


def _baseline(chart_type: str, recommended: dict) -> float:
    if chart_type in recommended:
        return float(recommended[chart_type])
    return float(DEFAULT_HEIGHT.get(chart_type, DEFAULT_HEIGHT["default"]))


def _parse_since(since: str | None) -> datetime.datetime | None:
    if not since:
        return None
    import re

    m = re.fullmatch(r"(\d+)d", since.strip())
    if not m:
        raise ValueError(f"--since must look like '90d', got {since!r}")
    return datetime.datetime.now() - datetime.timedelta(days=int(m.group(1)))


def calibrate(min_samples: int = 5, write: bool = False, since: str | None = None) -> dict:
    """Group logged heights by (audience, chart type); where the median drifts
    >= 1 unit from the current baseline with enough samples, propose it.
    Events logged without an audience calibrate the flat (all-audience)
    recommendation. --since 90d is the decay knob: older habits age out.
    --write merges proposals into the overlay (flat recommended_heights, or
    audiences.<aud>.recommended_heights for audience-tagged proposals)."""
    cutoff = _parse_since(since)
    events: dict[tuple, dict] = {}
    path = log_path()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not e.get("type") or not isinstance(e.get("height"), (int, float)):
                continue
            if cutoff is not None:
                try:
                    if datetime.datetime.fromisoformat(e.get("ts", "")) < cutoff:
                        continue
                except ValueError:
                    continue
            events[(e.get("profile"), e.get("slug"), e.get("chart"))] = e

    by_group: dict[tuple, list[float]] = {}  # (audience|None, type) -> heights
    for e in events.values():
        by_group.setdefault((e.get("audience"), e["type"]), []).append(float(e["height"]))

    overlay = load_overlay()
    proposals = []
    candidates = []  # observed but not (yet) actionable: the report says why
    for (aud, t), heights in sorted(by_group.items(), key=lambda kv: (kv[0][0] or "", kv[0][1])):
        rec = ((overlay.audiences.get(aud) or {}).get("recommended_heights")
               if aud else None) or overlay.recommended_heights
        current = _baseline(t, rec)
        median = round(statistics.median(heights), 1)
        entry = {"type": t, "audience": aud, "samples": len(heights),
                 "median": median, "current": current}
        if len(heights) >= min_samples and abs(median - current) >= 1:
            proposals.append(entry)
        else:
            entry["note"] = (f"needs >= {min_samples} samples" if len(heights) < min_samples
                             else "within 1 unit of current; no change")
            candidates.append(entry)
    report = {
        "stage": "calibrate", "ok": True, "events": len(events), "since": since,
        "proposals": proposals, "candidates": candidates, "written": False,
        "overlay": str(overlay_path()),
    }
    if write and proposals:
        import yaml

        p = overlay_path()
        data = {}
        if p.exists():
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        for prop in proposals:
            if prop["audience"]:
                tgt = (data.setdefault("audiences", {})
                       .setdefault(prop["audience"], {})
                       .setdefault("recommended_heights", {}))
            else:
                tgt = data.setdefault("recommended_heights", {})
            tgt[prop["type"]] = prop["median"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")
        report["written"] = True
    return report
