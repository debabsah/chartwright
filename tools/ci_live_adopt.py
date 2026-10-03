"""Live check for adopt-in-place against a real Superset.

Flow: import a dashboard that looks hand-built (random uuids) -> a plain spec at
its slug is refused -> `adopt` it -> plan is clean -> apply updates the SAME
dashboard (same id, same chart ids) -> a chart dropped from the spec is taken off
the dashboard but still exists -> re-apply keeps ids stable. Used by CI per
Superset version; runnable by hand against any sandbox:

    SDC_CI_PASSWORD=admin python tools/ci_live_adopt.py --base-url http://host:8098

Exit codes: 0 pass, 1 failure, 2 resolver gate (spec doesn't fit the instance).
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from chartwright.adopt import adopt_live
from chartwright.apply import _roundtrip_dataset_files
from chartwright.apply import apply as run_apply
from chartwright.client import SupersetClient
from chartwright.compiler import compile_bundle
from chartwright.dashdiff import plan
from chartwright.resolver import resolve
from chartwright.spec import load_spec
from chartwright.testing import edit_bundle


def _hand_built(bundle: bytes) -> bytes:
    """The same bundle with every dashboard and chart uuid random, as the UI makes them."""
    fresh: dict[str, str] = {}

    def new(name: str) -> str:
        return fresh.setdefault(name, str(uuid.uuid4()))

    def rewrite(path, doc):
        if "/dashboards/" in path:
            doc["uuid"] = str(uuid.uuid4())
            for node in doc["position"].values():
                if isinstance(node, dict) and node.get("type") == "CHART":
                    node["meta"]["uuid"] = new(node["meta"]["sliceName"])
        elif "/charts/" in path:
            doc["uuid"] = new(doc["slice_name"])

    return edit_bundle(bundle, rewrite)


def _drop_chart(data: dict, name: str) -> dict:
    out = copy.deepcopy(data)
    out["charts"] = [c for c in out["charts"] if c["name"] != name]
    out["dashboard"]["adopted"]["charts"].pop(name, None)

    def scrub(node):
        if isinstance(node, list):
            kept = [scrub(x) for x in node if x != name]
            return [x for x in kept if x != []]
        if isinstance(node, dict):
            return {k: scrub(v) for k, v in node.items()}
        return node

    out["layout"] = scrub(out["layout"])
    for f in out.get("filters", []):
        if f.get("charts"):
            f["charts"] = [c for c in f["charts"] if c != name] or None
            if f["charts"] is None:
                f.pop("charts")
    return out


def _linked(client, dashboard_id: int) -> dict[str, int]:
    return {c["slice_name"]: c["id"] for c in client.dashboard_charts(dashboard_id)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default=os.environ.get("SDC_CI_BASE_URL", "http://localhost:8098"))
    ap.add_argument("--username", default="admin")
    ap.add_argument("--spec", default=str(REPO / "tests" / "fixtures" / "kitchen_sink.json"))
    args = ap.parse_args(argv)
    password = os.environ.get("SDC_CI_PASSWORD", "admin")  # sandbox default; never in argv

    data = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    slug = f"ci-adopt-{int(time.time())}"
    data["dashboard"]["slug"] = slug
    data["dashboard"]["title"] = f"{data['dashboard']['title']} (adopt check)"
    spec = load_spec(data)
    client = SupersetClient(args.base_url, args.username, password)
    client.login()

    res = resolve(spec, client)
    if not res.ok:
        print("RESOLVER GATE:", json.dumps([e.as_dict() for e in res.errors], indent=2))
        return 2

    # 1. A dashboard the tool did not create.
    bundle = compile_bundle(spec, res, extra_files=_roundtrip_dataset_files(res, client))
    r = client.import_dashboard_bundle(_hand_built(bundle), overwrite=True)
    if r.status_code != 200:
        print(f"FAIL: could not import the hand-built dashboard: HTTP {r.status_code} {r.text[:500]}")
        return 1
    before = client.find_dashboard_by_slug(slug)
    before_charts = _linked(client, before["id"])

    # 2. A plain spec at that slug is refused.
    refused = run_apply(spec, client, "ci")
    if refused.ok or "chartwright adopt" not in (refused.import_detail or ""):
        print(f"FAIL: a plain spec was not refused: {refused.to_json()}")
        return 1

    # 3. Adopt it; plan against the live dashboard is clean.
    adopted = adopt_live(slug, client)
    print(json.dumps(adopted.payload(), indent=2))
    if not adopted.ok:
        print(f"FAIL: adopt refused: {adopted.detail}")
        return 1
    adopted_spec = load_spec(adopted.spec)
    p = plan(adopted_spec, client)
    print(p.to_json())
    if p.dashboard == "blocked" or not p.clean:
        print("FAIL: plan of the adopted spec is not clean")
        return 1

    # 4. Apply updates the same dashboard and the same charts.
    r1 = run_apply(adopted_spec, client, "ci")
    print(r1.to_json())
    if not r1.ok:
        print(f"FAIL: apply of the adopted spec ended at stage {r1.stage!r}")
        return 1
    if r1.dashboard_id != before["id"] or _linked(client, r1.dashboard_id) != before_charts:
        print(f"FAIL: not in place: dashboard {before['id']} -> {r1.dashboard_id}, "
              f"charts {before_charts} -> {_linked(client, r1.dashboard_id)}")
        return 1

    # 5. A chart dropped from the spec leaves the dashboard but is not deleted.
    dropped = adopted_spec.charts[-1].name
    smaller = load_spec(_drop_chart(adopted.spec, dropped))
    r2 = run_apply(smaller, client, "ci")
    print(r2.to_json())
    if not r2.ok:
        print(f"FAIL: apply after dropping {dropped!r} ended at stage {r2.stage!r}")
        return 1
    if dropped in _linked(client, r2.dashboard_id):
        print(f"FAIL: {dropped!r} is still on the dashboard")
        return 1
    try:
        client.get(f"/api/v1/chart/{before_charts[dropped]}")
    except Exception as e:  # noqa: BLE001 - any failure here means the chart is gone
        print(f"FAIL: {dropped!r} was deleted ({e})")
        return 1

    # 6. Re-apply keeps ids stable.
    ids_a = _linked(client, r2.dashboard_id)
    r3 = run_apply(smaller, client, "ci")
    if not r3.ok or _linked(client, r3.dashboard_id) != ids_a or r3.dashboard_id != before["id"]:
        print(f"FAIL: re-apply churned ids or failed: {r3.to_json()}")
        return 1

    print(f"ADOPT CHECK PASS: dashboard {before['id']} kept its id and {len(before_charts)} chart ids; "
          f"{dropped!r} taken off, not deleted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
