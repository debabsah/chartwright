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
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().isoformat(timespec="seconds")
        with path.open("a", encoding="utf-8") as f:
            for row in absorbed:
                f.write(json.dumps({
                    "ts": ts, "profile": profile, "slug": spec.dashboard.slug,
                    "chart": row["chart"], "type": types.get(row["chart"]),
                    "height": row["height"],
                }) + "\n")
    except OSError:
        pass


def _baseline(chart_type: str, recommended: dict) -> float:
    if chart_type in recommended:
        return float(recommended[chart_type])
    return float(DEFAULT_HEIGHT.get(chart_type, DEFAULT_HEIGHT["default"]))


def calibrate(min_samples: int = 5, write: bool = False) -> dict:
    """Group logged heights by chart type; where the median drifts >= 1 unit
    from the current baseline with enough samples, propose it. --write merges
    proposals into the overlay's recommended_heights."""
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
            if e.get("type") and isinstance(e.get("height"), (int, float)):
                events[(e.get("profile"), e.get("slug"), e.get("chart"))] = e

    by_type: dict[str, list[float]] = {}
    for e in events.values():
        by_type.setdefault(e["type"], []).append(float(e["height"]))

    overlay = load_overlay()
    proposals = []
    candidates = []  # observed but not (yet) actionable: the report says why
    for t, heights in sorted(by_type.items()):
        current = _baseline(t, overlay.recommended_heights)
        median = round(statistics.median(heights), 1)
        entry = {"type": t, "samples": len(heights), "median": median, "current": current}
        if len(heights) >= min_samples and abs(median - current) >= 1:
            proposals.append(entry)
        else:
            entry["note"] = (f"needs >= {min_samples} samples" if len(heights) < min_samples
                             else "within 1 unit of current; no change")
            candidates.append(entry)
    report = {
        "stage": "calibrate", "ok": True, "events": len(events),
        "proposals": proposals, "candidates": candidates, "written": False,
        "overlay": str(overlay_path()),
    }
    if write and proposals:
        import yaml

        p = overlay_path()
        data = {}
        if p.exists():
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        rec = data.setdefault("recommended_heights", {})
        for prop in proposals:
            rec[prop["type"]] = prop["median"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")
        report["written"] = True
    return report
