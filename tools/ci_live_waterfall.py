"""Live check of the waterfall (tests/fixtures/live_waterfall.json) on a real Superset.

A bridge needs rows for its opening, steps and closing, which no example dataset has,
so this first makes a virtual dataset, chartwright_ci_bridge, on the examples database;
and an opening total needs a theme that keeps zero on the value axis, so on 6.0.0 or
later it makes the fixture's theme too. Then, on 6.1.0 or later: apply two bridges (one
opening with a total, one with a rising first step) and two plain waterfalls -> the
data check passes the bridges (every step, the opening and the closing has a row, and
the closing reconciles) -> plan clean -> decompile reads the bridges back -> re-apply
keeps every chart id. Before 6.1.0: resolve refuses the bridges' steps and the theme
(superset_version_too_old) and warns for the 6.1.0 labels, and the plain waterfalls
apply with plan clean.

    SDC_CI_PASSWORD=admin python tools/ci_live_waterfall.py --base-url http://host:8098

`--slug` applies at another address; `--cleanup` deletes the dashboard, its charts, the
dataset and the theme afterwards. Exit codes: 0 pass, 1 failure, 2 resolver gate.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from chartwright.apply import apply as run_apply  # noqa: E402
from chartwright.client import SupersetClient  # noqa: E402
from chartwright.dashdiff import plan  # noqa: E402
from chartwright.decompile import decompile_live  # noqa: E402
from chartwright.resolver import resolve  # noqa: E402
from chartwright.spec import load_spec  # noqa: E402
from chartwright.versions import parse_version  # noqa: E402

FIXTURE = REPO / "tests" / "fixtures" / "live_waterfall.json"
DATASET = "chartwright_ci_bridge"
BRIDGES = ("Energy Bridge", "Energy Steps")
# Last year's output, four drivers, this year's: 1000 + 120 + 80 - 150 - 30 = 1020.
BRIDGE_SQL = (
    "SELECT 'FY2025' AS step, 1000.0 AS amount UNION ALL SELECT 'Solar', 120.0 "
    "UNION ALL SELECT 'Demand', 80.0 UNION ALL SELECT 'Wind', -150.0 "
    "UNION ALL SELECT 'Hydro', -30.0 UNION ALL SELECT 'FY2026', 1020.0")
# Keeps zero on a waterfall's value axis, so an opening total shows (WaterfallChart.opening).
AXIS_THEME = {"token": {}, "echartsOptionsOverridesByChartType": {
    "waterfall": {"yAxis": {"scale": False}}}}


def _fail(msg: str) -> int:
    print(f"FAIL: {msg}")
    return 1


def _dataset(client: SupersetClient) -> int:
    """The bridge's virtual dataset, made on the examples database if it isn't there."""
    found = client.find_datasets(DATASET)
    if found:
        return found[0]["id"]
    sales = client.find_datasets("cleaned_sales_data")[0]
    r = client.post_json("/api/v1/dataset/", {
        "database": sales["database"]["id"], "schema": sales.get("schema"),
        "table_name": DATASET, "sql": BRIDGE_SQL})
    if r.status_code not in (200, 201):
        raise SystemExit(f"FAIL: creating {DATASET}: HTTP {r.status_code} {r.text[:300]}")
    return r.json()["id"]


def _theme(client: SupersetClient, name: str) -> int | None:
    """The fixture's theme, made if it isn't there (Superset 6.0.0 or later has themes)."""
    found = [t for t in client.themes() if t.get("theme_name") == name]
    if found:
        return found[0]["id"]
    r = client.post_json("/api/v1/theme/", {"theme_name": name, "json_data": json.dumps(AXIS_THEME)})
    if r.status_code not in (200, 201):
        raise SystemExit(f"FAIL: creating the theme {name!r}: HTTP {r.status_code} {r.text[:300]}")
    return r.json()["id"]


def _cleanup(client: SupersetClient, slug: str, dataset_id: int, theme_id: int | None) -> None:
    dash = client.find_dashboard_by_slug(slug)
    if dash:
        charts = client.dashboard_charts(dash["id"])
        client.session.delete(f"{client.base_url}/api/v1/dashboard/{dash['id']}")
        for c in charts:
            client.delete_chart(c["id"])
    client.session.delete(f"{client.base_url}/api/v1/dataset/{dataset_id}")
    if theme_id is not None:
        client.session.delete(f"{client.base_url}/api/v1/theme/{theme_id}")
    print(f"cleaned up {slug!r}, {DATASET} and the theme")


def _applied(spec, client) -> tuple[int, list[int]] | str:
    """(dashboard id, chart ids) after an apply and a clean plan, or what went wrong."""
    report = run_apply(spec, client, "ci")
    if not report.ok:
        return f"apply ended at stage {report.stage!r}: {report.to_json()}"
    for s in report.smoke_results:
        if s["warning"]:
            return f"data check warned for {s['chart']!r}: {s['detail']}"
    p = plan(spec, client)
    if not p.clean:
        return f"plan not clean after apply: {p.to_json()}"
    return report.dashboard_id, sorted(c["id"] for c in client.dashboard_charts(report.dashboard_id))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default=os.environ.get("SDC_CI_BASE_URL", "http://localhost:8098"))
    ap.add_argument("--username", default="admin")
    ap.add_argument("--spec", default=str(FIXTURE), help="the spec to apply (its bridges are "
                    f"the charts named {' and '.join(BRIDGES)}, on the {DATASET} dataset)")
    ap.add_argument("--slug", default=None, help="apply at this slug instead of the spec's")
    ap.add_argument("--cleanup", action="store_true", help="delete what the check made")
    args = ap.parse_args(argv)
    password = os.environ.get("SDC_CI_PASSWORD", "admin")  # sandbox default; never in argv

    client = SupersetClient(args.base_url, args.username, password)
    client.login()
    raw = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    if args.slug:
        raw["dashboard"]["slug"] = args.slug
    release = parse_version(client.superset_version())
    dataset_id = _dataset(client)
    theme = raw["dashboard"].get("theme")
    theme_id = _theme(client, theme) if theme and release and release >= (6, 0, 0) else None
    try:
        return _check(client, raw, release)
    finally:
        if args.cleanup:
            _cleanup(client, raw["dashboard"]["slug"], dataset_id, theme_id)


def _check(client: SupersetClient, raw: dict, release) -> int:
    spec = load_spec(raw)
    res = resolve(spec, client)
    if release is not None and release < (6, 1, 0):
        refused = sorted((e.code, e.chart or "", e.ref) for e in res.errors)
        want = sorted([("superset_version_too_old", b, "steps") for b in BRIDGES]
                      + ([("superset_version_too_old", "", "theme")] if release < (6, 0, 0) else []))
        if refused != want:
            return _fail(f"before 6.1.0 the bridges' steps (and before 6.0.0 the theme) must be "
                         f"refused, and only they: {refused}")
        warned = sorted(w["field"] for w in res.version_warnings)
        if warned != ["decrease_label", "increase_label", "total_label"]:
            return _fail(f"the 6.1.0 labels must warn: {warned}")
        raw = {**raw, "dashboard": {k: v for k, v in raw["dashboard"].items() if k != "theme"},
               "charts": [c for c in raw["charts"] if c["name"] not in BRIDGES],
               "layout": {"rows": [r for r in raw["layout"]["rows"]
                                   if not set(r) & set(BRIDGES)]}}
        spec = load_spec(raw)
        res = resolve(spec, client)
    if not res.ok:
        print("RESOLVER GATE:", json.dumps([e.as_dict() for e in res.errors], indent=2))
        return 2

    first = _applied(spec, client)
    if isinstance(first, str):
        return _fail(first)
    bridges = [c for c in spec.charts if c.name in BRIDGES]
    if bridges:
        live = {c["name"]: c for c in decompile_live(spec.dashboard.slug, client).spec["charts"]}
        for want in bridges:
            got = live.get(want.name, {})
            if [got.get(f) for f in ("opening", "steps", "closing")] != [
                    want.opening, want.steps, want.closing]:
                return _fail(f"decompile did not read {want.name!r} back: {got}")
    second = _applied(spec, client)
    if isinstance(second, str):
        return _fail(f"re-apply: {second}")
    if first[1] != second[1]:
        return _fail(f"chart ids churned on re-apply: {first[1]} -> {second[1]}")
    kind = ("two bridges and two plain waterfalls" if bridges
            else "plain waterfalls (the bridges refused before 6.1.0)")
    print(f"LIVE WATERFALL PASS: {kind}, data check clean, plan clean, ids stable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
