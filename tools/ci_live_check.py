"""Live matrix check: the guarantee exercised against a real Superset.

Flow: resolve (typed version-gate signal) -> apply fresh -> re-apply and
assert slice-id stability (the clobber class seen in real use). Used by CI per
Superset version; runnable by hand against any sandbox:

    SDC_CI_PASSWORD=admin python tools/ci_live_check.py --base-url http://host:8098

Exit codes: 0 pass, 1 apply/stability failure, 2 resolver gate (the spec's
datasets/columns don't exist on this version's example data, a NAMED finding
for docs/CONTRACTS.md, not necessarily a bug).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from chartwright.apply import apply as run_apply
from chartwright.client import SupersetClient
from chartwright.resolver import resolve
from chartwright.spec import load_spec


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default=os.environ.get("SDC_CI_BASE_URL", "http://localhost:8098"))
    ap.add_argument("--username", default="admin")
    ap.add_argument("--spec", default=str(REPO / "tests" / "fixtures" / "kitchen_sink.json"))
    args = ap.parse_args(argv)
    password = os.environ.get("SDC_CI_PASSWORD", "admin")  # sandbox default; never in argv

    spec = load_spec(json.loads(Path(args.spec).read_text(encoding="utf-8")))
    client = SupersetClient(args.base_url, args.username, password)
    client.login()

    res = resolve(spec, client)
    if not res.ok:
        print("RESOLVER GATE (spec does not fit this instance's datasets):")
        print(json.dumps([e.as_dict() for e in res.errors], indent=2))
        return 2

    r1 = run_apply(spec, client, "ci")
    print(r1.to_json())
    if not r1.ok:
        print(f"FAIL: first apply ended at stage {r1.stage!r}")
        return 1

    ids1 = sorted(c["id"] for c in client.dashboard_charts(r1.dashboard_id))

    r2 = run_apply(spec, client, "ci")
    print(r2.to_json())
    if not r2.ok:
        print(f"FAIL: re-apply ended at stage {r2.stage!r}")
        return 1
    ids2 = sorted(c["id"] for c in client.dashboard_charts(r2.dashboard_id))

    if ids1 != ids2:
        print(f"FAIL: slice ids churned on re-apply: {ids1} -> {ids2}")
        return 1

    print(f"LIVE CHECK PASS: {len(spec.charts)} charts, ids stable across re-apply ({len(ids1)} slices)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
