"""Live check of a chart added in Superset to a chartwright dashboard (triage H).

4.1.4 and 5.0.0 merge a dashboard's chart links on import, so a chart added in the UI
stayed linked and apply failed at linkage; 6.1.0 unlinks it without a word. Flow, for
tests/fixtures/kitchen_sink.json at its own slug:
apply -> add a chart the way the UI does (create it, save the dashboard with it in the
layout) -> re-apply: ok, the chart named in a warning, off the dashboard, not deleted,
plan clean -> apply again (a backup without the chart) -> restore the re-apply's backup:
the chart is linked again -> restore the later backup: off again, plan clean.

    SDC_CI_PASSWORD=admin python tools/ci_live_ui_chart.py --base-url http://host:8098

Exit codes: 0 pass, 1 failure, 2 resolver gate.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from chartwright.apply import apply as run_apply, restore_bundle  # noqa: E402
from chartwright.client import SupersetClient  # noqa: E402
from chartwright.dashdiff import plan  # noqa: E402
from chartwright.resolver import resolve  # noqa: E402
from chartwright.spec import load_spec  # noqa: E402

UI_NAME = "Added in the UI (ci)"


def _fail(msg: str) -> int:
    print(f"FAIL: {msg}")
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default=os.environ.get("SDC_CI_BASE_URL", "http://localhost:8098"))
    ap.add_argument("--username", default="admin")
    args = ap.parse_args(argv)
    password = os.environ.get("SDC_CI_PASSWORD", "admin")  # sandbox default; never in argv

    raw = json.loads((REPO / "tests" / "fixtures" / "kitchen_sink.json").read_text(encoding="utf-8"))
    raw["dashboard"]["slug"] = "ci-ui-chart"
    raw["dashboard"]["title"] = "CI UI-added chart"
    spec = load_spec(raw)
    client = SupersetClient(args.base_url, args.username, password)
    client.login()
    res = resolve(spec, client)
    if not res.ok:
        print("RESOLVER GATE:", json.dumps([e.as_dict() for e in res.errors], indent=2))
        return 2

    def linked() -> set[str]:
        return {c["slice_name"] for c in client.dashboard_charts(dash_id)}

    def clean_plan(when: str) -> str | None:
        p = plan(spec, client)
        return None if p.clean else f"plan not clean {when}: {p.to_json()}"

    first = run_apply(spec, client, profile="ci")
    if not first.ok:
        return _fail(f"first apply: {first.stage} {first.import_detail}")
    dash_id = first.dashboard_id

    # Add a chart the way the UI does: create it, then save the dashboard with it in the
    # layout (a json_metadata PUT carrying positions, as the dashboard's Save sends).
    for c in client.find_charts_by_name(UI_NAME):
        client.delete_chart(c["id"])
    ds_id = next(iter(res.datasets.values())).id
    r = client.post_json("/api/v1/chart/", {
        "slice_name": UI_NAME, "viz_type": "big_number_total", "datasource_id": ds_id,
        "datasource_type": "table",
        "params": json.dumps({"metric": "count", "viz_type": "big_number_total"})})
    if r.status_code != 201:
        return _fail(f"creating the UI chart: HTTP {r.status_code} {r.text[:300]}")
    ui_id = r.json()["id"]
    detail = client.get(f"/api/v1/dashboard/{dash_id}")["result"]
    pos = json.loads(detail["position_json"])
    pos["ROW-ci-ui"] = {"type": "ROW", "id": "ROW-ci-ui", "children": ["CHART-ci-ui"],
                        "parents": ["ROOT_ID", "GRID_ID"],
                        "meta": {"background": "BACKGROUND_TRANSPARENT"}}
    pos["CHART-ci-ui"] = {"type": "CHART", "id": "CHART-ci-ui", "children": [],
                          "parents": ["ROOT_ID", "GRID_ID", "ROW-ci-ui"],
                          "meta": {"chartId": ui_id, "width": 4, "height": 50,
                                   "sliceName": UI_NAME}}
    pos["GRID_ID"]["children"].append("ROW-ci-ui")
    meta = json.loads(detail.get("json_metadata") or "{}")
    r = client.put_json(f"/api/v1/dashboard/{dash_id}",
                        {"json_metadata": json.dumps({**meta, "positions": pos})})
    if r.status_code != 200 or UI_NAME not in linked():
        return _fail(f"adding the UI chart to the dashboard: HTTP {r.status_code}")

    again = run_apply(spec, client, profile="ci")
    if not again.ok:
        return _fail(f"re-apply with a UI chart: {again.stage} {again.import_detail}")
    if not any(UI_NAME in w and "off the dashboard" in w for w in again.warnings):
        return _fail(f"re-apply did not name the UI chart: {again.warnings}")
    if UI_NAME in linked():
        return _fail("the UI chart is still on the dashboard after re-apply")
    if client.get(f"/api/v1/chart/{ui_id}")["result"]["slice_name"] != UI_NAME:
        return _fail("the UI chart is gone; apply must never delete it")
    if (err := clean_plan("after re-apply")):
        return _fail(err)

    third = run_apply(spec, client, profile="ci")
    if not third.ok:
        return _fail(f"third apply: {third.stage} {third.import_detail}")
    back = restore_bundle(Path(again.backup).read_bytes(), spec.dashboard.slug, client)
    if not back.ok or UI_NAME not in linked():
        return _fail(f"restoring the backup that has the UI chart: {back.import_detail} "
                     f"linked={sorted(linked())}")
    back = restore_bundle(Path(third.backup).read_bytes(), spec.dashboard.slug, client)
    if not back.ok or UI_NAME in linked():
        return _fail(f"restoring the backup without the UI chart: {back.import_detail} "
                     f"linked={sorted(linked())} warnings={back.warnings}")
    if (err := clean_plan("after restoring")):
        return _fail(err)

    client.delete_chart(ui_id)
    print("LIVE UI-CHART PASS: taken off on re-apply, kept on the instance, restores match "
          "their backups, plan clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
