"""Fault injection: every apply-stage boundary must fail SAFE.

A FaultyClient delegates to the real client but trips an armed method with
either a typed SupersetAPIError (what a real network drop surfaces as after
client._send's conversion) or a fake HTTP 500 (the importer's failure shape).
For each fault we assert the contract:
  - apply returns a TYPED report (never a traceback) naming the right stage;
  - report.backup is set whenever mutation had begun on an existing dashboard;
  - auto-restore fires for prepare/import faults and the dashboard is BACK to
    its pre-apply state (plan against the previous spec is clean);
  - later-stage faults (linkage/scope/smoke) leave a recoverable dashboard:
    a clean re-apply converges (plan clean, ids stable).
Also proves restore completeness: `restore_bundle` rolls back surviving
charts' params AND numeric filter scopes (the raw importer does neither).

Usage:
    SDC_CI_PASSWORD=admin python tools/faultline.py --base-url http://host:8098
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

from chartwright.apply import apply as run_apply, restore_bundle
from chartwright.client import SupersetAPIError, SupersetClient
from chartwright.dashdiff import plan as run_plan
from chartwright.spec import load_spec

FIXTURE = REPO / "tests" / "fixtures" / "kitchen_sink.json"
SLUG = "sdc-faultline"


class FakeResponse:
    status_code = 500
    text = "injected fault: importer exploded"


class FaultyClient:
    """Delegates to a real SupersetClient; the armed method fails once."""

    def __init__(self, real: SupersetClient):
        self._real = real
        self._method: str | None = None
        self._kind = "error"
        self._at = 1
        self._calls = 0
        self.tripped = False

    def arm(self, method: str, kind: str = "error", at: int = 1) -> None:
        self._method, self._kind, self._at, self._calls, self.tripped = method, kind, at, 0, False

    def __getattr__(self, name):
        attr = getattr(self._real, name)
        if not callable(attr) or name != self._method:
            return attr

        def wrapper(*a, **kw):
            self._calls += 1
            if self._calls == self._at and not self.tripped:
                self.tripped = True
                if self._kind == "http500":
                    return FakeResponse()
                raise SupersetAPIError(f"injected fault in {name}", None, None)
            return attr(*a, **kw)

        return wrapper


def specs() -> tuple[dict, dict]:
    base = json.loads(FIXTURE.read_text(encoding="utf-8"))
    by_type = {}
    for c in base["charts"]:
        by_type.setdefault(c["type"], c)
    charts = [copy.deepcopy(by_type[t]) for t in ("big_number_total", "timeseries_line", "histogram")]
    names = [c["name"] for c in charts]
    histo = by_type["histogram"]
    v1 = {
        "spec_version": "1",
        "dashboard": {"title": "Faultline Board", "slug": SLUG},
        "charts": charts,
        "filters": [
            {"type": "time_range", "name": "Time Range"},
            {"type": "range", "name": "Window", "dataset": copy.deepcopy(histo["dataset"]),
             "column": histo["column"], "le": 50, "charts": [names[1]]},
        ],
        "layout": {"rows": [[names[0]], [names[1], names[2]]]},
    }
    v2 = copy.deepcopy(v1)
    v2["charts"] = v2["charts"][:2]  # drop the histogram: exercises stale-owned deletion
    v2["filters"][1]["le"] = 90
    v2["charts"][1]["row_limit"] = 7  # params change for restore-completeness checks
    v2["layout"] = {"rows": [[names[0], names[1]]]}
    for s in (v1, v2):
        load_spec(s)
    return v1, v2


def check(cond: bool, label: str, detail=""):
    if not cond:
        raise AssertionError(f"{label}: {detail}")


def run_fault(real, v1, v2, method, kind, want_stage, restored: bool, at: int = 1) -> None:
    """Baseline v1 -> apply v2 with the fault armed -> assert contract."""
    label = f"fault[{method}/{kind}@{want_stage}]"
    r0 = run_apply(load_spec(v1), real, "faultline")
    check(r0.ok, label, f"baseline apply failed: {r0.stage} {r0.import_detail}")
    check(run_plan(load_spec(v1), real).clean, label, "baseline plan not clean")

    faulty = FaultyClient(real)
    faulty.arm(method, kind, at)
    try:
        rep = run_apply(load_spec(v2), faulty, "faultline")
    except Exception as e:  # noqa: BLE001 - a traceback IS the failure
        raise AssertionError(f"{label}: apply raised instead of reporting: {type(e).__name__}: {e}")
    check(faulty.tripped, label, "fault never fired (call path changed?)")
    check(not rep.ok, label, "apply claimed ok despite the fault")
    check(rep.stage == want_stage, label, f"stage {rep.stage!r}, wanted {want_stage!r}")
    check(rep.backup is not None, label, "no backup recorded despite existing dashboard")

    if restored:
        check(any("AUTO-RESTORED" in w for w in rep.warnings), label,
              f"auto-restore did not fire: {rep.warnings}")
        # State equivalence via plan (a chart the faulty apply already DELETED
        # is necessarily recreated with a fresh id; id continuity there is
        # impossible by construction; content equivalence is the contract).
        p = run_plan(load_spec(v1), real)
        check(p.clean, label, f"dashboard NOT back to pre-apply state: {p.to_json()}")
    else:
        r2 = run_apply(load_spec(v2), real, "faultline")
        check(r2.ok, label, f"clean re-apply after fault failed: {r2.stage} {r2.import_detail}")
        check(run_plan(load_spec(v2), real).clean, label, "plan not clean after recovery re-apply")

    # Leave v1 in place for the next scenario's baseline.
    check(run_apply(load_spec(v1), real, "faultline").ok, label, "reset to v1 failed")
    print(f"{label}: OK")


def restore_completeness(real, v1, v2) -> None:
    """v1 -> v2 (params + scope changed) -> restore v1's backup -> the
    dashboard must be FULLY v1: params and numeric scopes, not just shell."""
    label = "restore-completeness"
    r1 = run_apply(load_spec(v1), real, "faultline")
    check(r1.ok, label, "v1 apply failed")
    r2 = run_apply(load_spec(v2), real, "faultline")
    check(r2.ok, label, "v2 apply failed")
    check(r2.backup, label, "v2 apply left no backup of v1")

    backup = Path(r2.backup).read_bytes()
    rr = restore_bundle(backup, SLUG, real)
    check(rr.ok, label, f"restore failed: {rr.stage} {rr.import_detail}")
    p = run_plan(load_spec(v1), real)
    check(p.clean, label, f"restore is not a full v1 (params/scopes drifted): {p.to_json()}")
    print(f"{label}: OK (params + scopes rolled back)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default=os.environ.get("SDC_CI_BASE_URL", "http://localhost:8098"))
    ap.add_argument("--username", default="admin")
    args = ap.parse_args(argv)
    real = SupersetClient(args.base_url, args.username,
                          os.environ.get("SDC_CI_PASSWORD", "admin"))
    real.login()
    v1, v2 = specs()

    # (method, kind, stage apply must report, auto-restore expected?, call#)
    scenarios = [
        ("export_dataset",          "error",   "prepare", True,  1),  # dataset round-trip
        ("delete_chart",            "error",   "prepare", True,  1),  # stale-owned deletion (v2 drops a chart)
        ("import_dashboard_bundle", "http500", "import",  True,  1),  # importer 500
        ("import_dashboard_bundle", "error",   "import",  True,  1),  # network drop mid-import
        ("dashboard_charts",        "error",   "linkage", False, 2),  # call#1 is the prepare-stage stale check
        ("chart_data",              "error",   "smoke",   False, 1),  # smoke query
    ]
    try:
        for method, kind, stage, restored, at in scenarios:
            run_fault(real, v1, v2, method, kind, stage, restored, at)
        restore_completeness(real, v1, v2)
    except AssertionError as e:
        print(f"FAULTLINE FAIL: {e}")
        return 1
    print("FAULTLINE PASS: every stage boundary fails safe; restore is complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
