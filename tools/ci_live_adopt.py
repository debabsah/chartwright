"""Live check for adopt-in-place against a real Superset.

Flow: import a dashboard that looks hand-built (random uuids), one chart with a saved
query -> a plain spec at its slug is refused -> `adopt` refuses until its resets are
accepted -> plan rewrites no chart's options (filters get the tool's ids) -> apply
updates the SAME
dashboard (same id, same chart ids) -> a chart dropped from the spec is taken off
the dashboard but still exists -> a renamed chart keeps its id -> a chart shared
with another dashboard makes adopt refuse, and dropping it takes it off this
dashboard only -> re-apply keeps ids stable; the saved query survives an apply that
leaves its chart's options alone. Used by CI per
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
    # A saved query, as Explore stores on save (reports and the chart data API read it).
    saved_name = sorted(before_charts)[0]
    saved_query = json.dumps({"queries": [{"ci": "saved"}]})
    client.put_json(f"/api/v1/chart/{before_charts[saved_name]}", {"query_context": saved_query})

    # 2. A plain spec at that slug is refused.
    refused = run_apply(spec, client, "ci")
    if refused.ok or "chartwright adopt" not in (refused.import_detail or ""):
        print(f"FAIL: a plain spec was not refused: {refused.to_json()}")
        return 1

    # 3. Adopt refuses until the resets are accepted, then writes the spec; plan finds
    #    no chart whose stored options the first apply rewrites.
    first_try = adopt_live(slug, client)
    if first_try.ok or not first_try.resets or "--accept-reset" not in first_try.detail:
        print(f"FAIL: adopt did not list resets and refuse: {json.dumps(first_try.payload())}")
        return 1
    adopted = adopt_live(slug, client, accept_reset=True)
    print(json.dumps(adopted.payload(), indent=2))
    if not adopted.ok:
        print(f"FAIL: adopt refused: {adopted.detail}")
        return 1
    if not any(saved_name in r["what"] for r in adopted.resets):
        print(f"FAIL: adopt did not list the saved query of {saved_name!r} among the resets")
        return 1
    adopted_spec = load_spec(adopted.spec)
    p = plan(adopted_spec, client)
    print(p.to_json())
    if p.dashboard == "blocked" or p.chart_option_changes or p.charts_changed:
        print("FAIL: plan of the adopted spec rewrites chart options")
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
    # The settings comparison must hold on a real instance: once applied, the
    # options stored on each chart are exactly what the spec writes.
    p_after = plan(adopted_spec, client)
    if not p_after.clean:
        print(f"FAIL: plan right after the first apply is not clean: {p_after.to_json()}")
        return 1
    # The chart's options didn't change, so its saved query must still be there.
    kept = client.get(f"/api/v1/chart/{before_charts[saved_name]}")["result"].get("query_context")
    if kept != saved_query:
        print(f"FAIL: the saved query of {saved_name!r} was not kept: {kept!r}")
        return 1

    # 5. Renaming an adopted chart in the spec renames that chart; its id stays.
    first = adopted_spec.charts[0].name
    renamed_data = json.loads(json.dumps(adopted.spec).replace(json.dumps(first), json.dumps(first + " (renamed)")))
    r_ren = run_apply(load_spec(renamed_data), client, "ci")
    if not r_ren.ok or _linked(client, r_ren.dashboard_id).get(first + " (renamed)") != before_charts[first]:
        print(f"FAIL: rename did not keep the chart: {r_ren.to_json()}")
        return 1

    # 6. A chart that also sits on another dashboard: adopt refuses by default, and
    #    dropping it from the spec takes it off THIS dashboard only.
    other = load_spec({**json.loads(json.dumps(data)), "dashboard": {
        "title": "Adopt check (other)", "slug": f"{slug}-other"}})
    r_other = run_apply(other, client, "ci")
    if not r_other.ok:
        print(f"FAIL: could not build the second dashboard: {r_other.to_json()}")
        return 1
    dropped = adopted_spec.charts[-1].name
    shared_id = before_charts[dropped]
    detail = client.get(f"/api/v1/chart/{shared_id}")["result"]
    keep = sorted({d["id"] for d in detail.get("dashboards") or []} | {r_other.dashboard_id})
    client.put_json(f"/api/v1/chart/{shared_id}", {"dashboards": keep})
    if adopt_live(slug, client, accept_reset=True).ok:
        print("FAIL: adopt did not refuse a dashboard with a shared chart")
        return 1
    smaller_data = _drop_chart(renamed_data, dropped)
    smaller = load_spec(smaller_data)
    r2 = run_apply(smaller, client, "ci")
    print(r2.to_json())
    if not r2.ok:
        print(f"FAIL: apply after dropping {dropped!r} ended at stage {r2.stage!r}")
        return 1
    if dropped in _linked(client, r2.dashboard_id):
        print(f"FAIL: {dropped!r} is still on the adopted dashboard")
        return 1
    try:
        still = {d["id"] for d in client.get(f"/api/v1/chart/{shared_id}")["result"].get("dashboards") or []}
    except Exception as e:  # noqa: BLE001 - any failure here means the chart is gone
        print(f"FAIL: {dropped!r} was deleted ({e})")
        return 1
    if r_other.dashboard_id not in still:
        print(f"FAIL: {dropped!r} was taken off the other dashboard too")
        return 1

    # 7. An adopted chart no other dashboard uses is taken off and kept, on no dashboard.
    lone = smaller.charts[-1].name
    lone_id = _linked(client, r2.dashboard_id)[lone]
    smaller_data = _drop_chart(smaller_data, lone)
    r_lone = run_apply(load_spec(smaller_data), client, "ci")
    if not r_lone.ok:
        print(f"FAIL: apply after dropping {lone!r} ended at stage {r_lone.stage!r}: {r_lone.import_detail}")
        return 1
    try:
        on = client.get(f"/api/v1/chart/{lone_id}")["result"].get("dashboards") or []
    except Exception as e:  # noqa: BLE001
        print(f"FAIL: {lone!r} was deleted ({e})")
        return 1
    if on:
        print(f"FAIL: {lone!r} is still on dashboards {on}")
        return 1
    smaller = load_spec(smaller_data)

    # 8. Re-apply keeps ids stable.
    ids_a = _linked(client, r2.dashboard_id)
    r3 = run_apply(smaller, client, "ci")
    if not r3.ok or _linked(client, r3.dashboard_id) != ids_a or r3.dashboard_id != before["id"]:
        print(f"FAIL: re-apply churned ids or failed: {r3.to_json()}")
        return 1

    print(f"ADOPT CHECK PASS: dashboard {before['id']} kept its id and {len(before_charts)} chart ids; "
          f"plan clean after apply; a saved query kept; a renamed chart kept its id; shared chart "
          f"{dropped!r} taken off this "
          f"dashboard only; unshared chart {lone!r} kept on no dashboard")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
