"""Live check of saved queries (triage Q): the query Superset's frontend builds for each
chart is captured in a headless browser and stored, so the chart's data endpoint and
CSV export work, as scheduled reports need.

Flow, for tests/fixtures/kitchen_sink.json at its own slug: apply -> save the queries
-> every chart's data endpoint answers and its CSV export is CSV (a zip of CSVs for the
two-query mixed chart) -> re-apply keeps every saved query -> a chart whose options
change loses its now-stale query, and saving again restores it.
Needs the visual extra (`pip install -e ".[visual]"` and `playwright install chromium`).

    SDC_CI_PASSWORD=admin python tools/ci_live_saved_queries.py --base-url http://host:8098

Exit codes: 0 pass, 1 failure, 2 resolver gate.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from chartwright.apply import apply as run_apply  # noqa: E402
from chartwright.client import SupersetClient  # noqa: E402
from chartwright.resolver import resolve  # noqa: E402
from chartwright.savedqueries import save_queries  # noqa: E402
from chartwright.spec import load_spec  # noqa: E402


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
    raw["dashboard"]["slug"] = "ci-saved-queries"
    raw["dashboard"]["title"] = "CI saved queries"
    spec = load_spec(raw)
    client = SupersetClient(args.base_url, args.username, password)
    client.login()
    if not resolve(spec, client).ok:
        print("RESOLVER GATE")
        return 2
    profile = SimpleNamespace(base_url=args.base_url, username=args.username, password=password,
                              api_token=None, ca_bundle=None, verify=True)

    def charts() -> dict[str, int]:
        dash = client.find_dashboard_by_slug(spec.dashboard.slug)
        return {c["slice_name"]: c["id"] for c in client.dashboard_charts(dash["id"])}

    def saved(chart_id: int) -> str | None:
        return client.get(f"/api/v1/chart/{chart_id}")["result"].get("query_context")

    first = run_apply(spec, client, "ci")
    if not first.ok:
        return _fail(f"apply: {first.stage} {first.import_detail}")
    ids = charts()
    with_query = [n for n, i in ids.items() if saved(i)]
    # A chart re-applied over an earlier run keeps a query saved then; a fresh one has none.
    out = save_queries(client, profile, ids)
    if not out["ok"]:
        return _fail(f"saving the queries: {json.dumps(out)[:1500]}")
    for name, chart_id in ids.items():
        r = client.session.get(f"{client.base_url}/api/v1/chart/{chart_id}/data/",
                               params={"format": "csv"}, timeout=120)
        # A chart of two queries (mixed) exports a zip of two CSVs.
        many = len(json.loads(saved(chart_id) or "{}").get("queries") or []) > 1
        kind = r.headers.get("Content-Type") or ""
        if r.status_code != 200 or ("zip" if many else "csv") not in kind:
            return _fail(f"{name}: CSV export HTTP {r.status_code} {kind}")

    again = run_apply(spec, client, "ci")
    if not again.ok:
        return _fail(f"re-apply: {again.stage} {again.import_detail}")
    lost = [n for n, i in charts().items() if not saved(i)]
    if lost:
        return _fail(f"re-apply cleared the saved queries of {lost}")

    changed = json.loads(json.dumps(raw))
    target = next(c for c in changed["charts"] if c["type"] == "timeseries_line")
    target["row_limit"] = (target.get("row_limit") or 10000) - 1
    third = run_apply(load_spec(changed), client, "ci")
    if not third.ok:
        return _fail(f"apply with a changed chart: {third.stage} {third.import_detail}")
    if saved(charts()[target["name"]]):
        return _fail(f"{target['name']!r} kept a saved query its new options made stale")
    out = save_queries(client, profile, {target["name"]: charts()[target["name"]]})
    if not out["ok"] or not saved(charts()[target["name"]]):
        return _fail(f"saving {target['name']!r} again: {json.dumps(out)[:800]}")

    print(f"LIVE SAVED-QUERIES PASS: {len(ids)} charts saved and answering as CSV "
          f"({len(with_query)} had one before), kept on re-apply, cleared when stale and saved again")
    return 0


if __name__ == "__main__":
    sys.exit(main())
