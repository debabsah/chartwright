"""Second-writer scenarios: the stale-tab clobber class, scripted.

Superset dashboard metadata is last-writer-wins with no concurrency control
in any supported version (docs/CONTRACTS.md). These scenarios simulate the
second writer (an open browser tab writing its stale in-memory metadata
back) and assert the tool's contract: `plan` DETECTS the drift, re-apply
REPAIRS it, and slice ids never churn while doing so.

Scenarios:
  A  stale write-back: tab from before a re-scope reverts the metadata
  B  pre-filter tab: tab from before a filter EXISTED clobbers it away
  C  dead-id scopes: numeric scope ids corrupted (server accepts silently)
  D  cross_filters_enabled=False survives the tool's scope-stage PUT

Usage:
    SDC_CI_PASSWORD=admin python tools/adversary.py --base-url http://host:8098
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from chartwright.apply import apply as run_apply
from chartwright.client import SupersetClient
from chartwright.dashdiff import plan as run_plan
from chartwright.spec import load_spec

FIXTURE = REPO / "tests" / "fixtures" / "kitchen_sink.json"
SLUG = "sdc-adversary"


def build_specs() -> tuple[dict, dict, dict]:
    """v0: no range filter; v1a: range filter scoped to two charts;
    v1b: same filter re-scoped to one chart with a different bound."""
    base = json.loads(FIXTURE.read_text(encoding="utf-8"))
    by_type = {}
    for c in base["charts"]:
        by_type.setdefault(c["type"], c)
    charts = [copy.deepcopy(by_type[t]) for t in ("big_number_total", "timeseries_line", "histogram")]
    names = [c["name"] for c in charts]
    histo = by_type["histogram"]
    select = next(f for f in base["filters"] if f["type"] == "select")

    v0 = {
        "spec_version": "1",
        "dashboard": {"title": "Adversary Board", "slug": SLUG},
        "charts": charts,
        "filters": [copy.deepcopy(select), {"type": "time_range", "name": "Time Range"}],
        "layout": {"rows": [[names[0]], [names[1], names[2]]]},
    }
    window = {
        "type": "range", "name": "Window",
        "dataset": copy.deepcopy(histo["dataset"]),
        "column": histo["column"], "le": 50,
        "charts": [names[1], names[2]],
    }
    v1a = copy.deepcopy(v0)
    v1a["filters"] = v0["filters"] + [window]
    v1b = copy.deepcopy(v1a)
    v1b["filters"][-1]["charts"] = [names[1]]
    v1b["filters"][-1]["le"] = 90
    for s in (v0, v1a, v1b):
        load_spec(s)
    return v0, v1a, v1b


class Harness:
    def __init__(self, client: SupersetClient):
        self.client = client
        self.dash_id: int | None = None

    def apply(self, spec_dict: dict, label: str) -> dict[str, int]:
        report = run_apply(load_spec(spec_dict), self.client, "adversary")
        assert report.ok, f"{label}: apply failed at {report.stage}: {report.import_detail}"
        self.dash_id = report.dashboard_id
        return {c["slice_name"]: c["id"] for c in self.client.dashboard_charts(self.dash_id)}

    def meta(self) -> dict:
        detail = self.client.get(f"/api/v1/dashboard/{self.dash_id}")["result"]
        return json.loads(detail.get("json_metadata") or "{}")

    def put_meta(self, meta: dict, label: str) -> None:
        r = self.client.put_json(f"/api/v1/dashboard/{self.dash_id}",
                                 {"json_metadata": json.dumps(meta)})
        assert r.status_code == 200, f"{label}: adversary PUT rejected HTTP {r.status_code}: {r.text[:200]}"

    def plan(self, spec_dict: dict):
        return run_plan(load_spec(spec_dict), self.client)


def scenario_a(h: Harness, v1a: dict, v1b: dict) -> None:
    """Stale tab reverts a re-scope; plan must see it, re-apply must fix it."""
    ids_a = h.apply(v1a, "A: baseline")
    stale = h.meta()
    ids_b = h.apply(v1b, "A: re-scope")
    assert ids_a == ids_b, f"A: ids churned on re-apply: {ids_a} -> {ids_b}"
    assert h.plan(v1b).clean, "A: plan not clean right after apply"

    h.put_meta(stale, "A")  # the tab closes; beforeunload fires
    p = h.plan(v1b)
    assert not p.clean and any("Window" in n for n in p.filters_changed), \
        f"A: plan blind to the stale write-back: {p.to_json()}"

    ids_r = h.apply(v1b, "A: repair")
    assert ids_r == ids_b, "A: repair churned ids"
    assert h.plan(v1b).clean, "A: plan not clean after repair"
    print("scenario A (stale write-back): detect + repair OK")


def scenario_b(h: Harness, v0: dict, v1a: dict) -> None:
    """Tab predating the filter's existence clobbers it away entirely."""
    h.apply(v0, "B: pre-filter state")
    stale = h.meta()
    ids_1 = h.apply(v1a, "B: filter added")
    assert h.plan(v1a).clean, "B: plan not clean right after apply"

    h.put_meta(stale, "B")
    p = h.plan(v1a)
    assert p.filters_added == ["Window"], \
        f"B: plan must name the clobbered filter as missing: {p.to_json()}"

    ids_r = h.apply(v1a, "B: repair")
    assert ids_r == ids_1, "B: repair churned ids"
    assert h.plan(v1a).clean, "B: plan not clean after repair"
    print("scenario B (pre-filter tab clobber): detect + repair OK")


def scenario_c(h: Harness, v1a: dict) -> None:
    """Dead numeric scope ids (server accepts silently) must not read as clean."""
    h.apply(v1a, "C: baseline")
    meta = h.meta()
    for nf in meta.get("native_filter_configuration") or []:
        if nf.get("sdc_scope_charts"):
            nf["scope"] = {"rootPath": ["ROOT_ID"], "excluded": [999901, 999902]}
            nf["chartsInScope"] = [999903]
    h.put_meta(meta, "C")

    p = h.plan(v1a)
    assert not p.clean and any("Window" in n for n in p.filters_changed), \
        f"C: plan blind to dead-id scopes: {p.to_json()}"

    h.apply(v1a, "C: repair")
    assert h.plan(v1a).clean, "C: plan not clean after repair"
    print("scenario C (dead-id scopes): detect + repair OK")


def scenario_d(h: Harness, v1a: dict) -> None:
    """cross_filters_enabled=False must survive the scope stage's PUT
    (the server force-stamps True when the key is OMITTED; see the
    metadata contract in docs/CONTRACTS.md)."""
    h.apply(v1a, "D: baseline")
    meta = h.meta()
    meta["cross_filters_enabled"] = False
    h.put_meta(meta, "D")

    h.apply(v1a, "D: re-apply over the setting")
    after = h.meta()
    assert after.get("cross_filters_enabled") is False, \
        f"D: scope-stage PUT re-enabled cross filters: {after.get('cross_filters_enabled')!r}"
    print("scenario D (cross_filters_enabled preserved): OK")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default=os.environ.get("SDC_CI_BASE_URL", "http://localhost:8098"))
    ap.add_argument("--username", default="admin")
    args = ap.parse_args(argv)

    client = SupersetClient(args.base_url, args.username,
                            os.environ.get("SDC_CI_PASSWORD", "admin"))
    client.login()
    v0, v1a, v1b = build_specs()
    h = Harness(client)

    try:
        scenario_a(h, v1a, v1b)
        scenario_b(h, v0, v1a)
        scenario_c(h, v1a)
        scenario_d(h, v1a)
    except AssertionError as e:
        print(f"ADVERSARY FAIL: {e}")
        return 1
    print("ADVERSARY PASS: all 4 scenarios detect + repair")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
