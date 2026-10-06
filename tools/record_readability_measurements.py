"""Record the type sizes Superset draws, so the readability rules' model of them is
tested offline against real measurements (tests/test_readability.py reads
tests/fixtures/readability/measurements.json; docs/DESIGN-BRAIN.md sec.17, "Type sizes").

For each instance: apply a small dashboard (a table, a pivot, a big number, a line chart)
once per stylesheet in VARIANTS, and a grid of big numbers at heights 2 to 10; open each
in headless Chromium at a 1440 x 900 window; and store what the browser computed: the
table and pivot cells' font sizes and row heights, the chart titles, each big number's
lines, and the font ECharts set for every label it drew (read by wrapping the canvas's
fillText, since no DOM holds that text). Dashboards are deleted afterwards, charts and all.

    pip install -e ".[visual]" && playwright install chromium
    SDC_CI_PASSWORD=admin python tools/record_readability_measurements.py \\
        --base-url http://localhost:8094 --base-url http://localhost:8095 \\
        --base-url http://localhost:8098

`--check` writes nothing: it measures the default, cells and kpi dashboards and exits 1
when the instance draws anything else than the fixture records for its release (the
live CI job runs it on each release).
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
from chartwright.spec import load_spec  # noqa: E402

OUT = REPO / "tests" / "fixtures" / "readability" / "measurements.json"
DS = {"database": "examples", "table": "cleaned_sales_data"}
PREFIX = "cw-test-readability"
VIEWPORT = (1440, 900)
# Each stylesheet tests one claim of the reader in chartwright/design/readability.py.
VARIANTS = {
    "default": "",
    # cell rules reach the cells everywhere; a pivot's header cells keep their own rule
    "cells": (".superset-chart-table td { font-size: 14px; }\n"
              ".superset-chart-table th { font-size: 13px; }\n"
              ".pivot_table_v_2 td { font-size: 14px; }\n"
              ".pivot_table_v_2 th { font-size: 13px; }\n"),
    # table-level rules: (0,1,1) beats 4.1.4's `.table-condensed`, not 6.x's own rule
    "table-level": (".superset-chart-table table { font-size: 15px; }\n"
                    ".pivot_table_v_2 table { font-size: 15px; }\n"),
    # a tie with Superset's own rule goes to the dashboard's CSS
    "tie": (".table-condensed { font-size: 15px; }\n"
            ".superset-chart-table table.table-condensed { font-size: 16px; }\n"
            ".pivot_table_v_2 table.pvtTable thead tr th, "
            ".pivot_table_v_2 table.pvtTable tbody tr th { font-size: 13px; }\n"),
    # the chart's container sets nothing the table draws; !important beats everything
    "container": (".superset-chart-table { font-size: 16px; }\n"
                  ".pivot_table_v_2 table { font-size: 15px !important; }\n"
                  ".subheader-line, .subtitle-line { font-size: 13px !important; }\n"),
    # line height and padding: Superset's own cell rules beat a dashboard's on some releases
    "spacing": (".superset-chart-table td, .superset-chart-table th "
                "{ padding: 2px; line-height: 1.2; }\n"
                ".pivot_table_v_2 td, .pivot_table_v_2 th { padding: 2px; line-height: 1.2; }\n"
                ".header-title { font-size: 13px; }\n"),
}


def small(slug: str, css: str) -> dict:
    charts = [
        {"name": "Order lines", "type": "table", "dataset": DS,
         "columns": ["country", "deal_size", "status"], "row_limit": 20, "width": 6, "height": 12},
        {"name": "Country pivot", "type": "pivot_table", "dataset": DS, "rows": ["country"],
         "columns": ["deal_size"], "metrics": ["COUNT(*)"], "row_limit": 500, "width": 6,
         "height": 12},
        {"name": "Orders", "type": "big_number_total", "dataset": DS, "metric": "COUNT(*)",
         "subtitle": "Orders booked", "number_format": ",.0f", "width": 4, "height": 5},
        {"name": "Sales by line", "type": "timeseries_line", "dataset": DS,
         "metrics": ["SUM(sales)"], "time_column": "order_date", "time_grain": "P1M",
         "groupby": "product_line", "width": 8, "height": 8},
    ]
    dash = {"title": f"Recorded: {slug}", "slug": slug}
    if css:
        dash["css"] = css
    return {"spec_version": "1", "dashboard": dash, "charts": charts,
            "layout": {"rows": [["Orders", "Sales by line"], ["Order lines", "Country pivot"]]}}


def kpi_grid(slug: str) -> dict:
    """A total with a subtitle and a trendline KPI with a comparison and a subtitle, at
    each height from 2 to 10, half the page wide: wide enough that height alone sets the
    size."""
    charts, rows = [], []
    for units in range(2, 11):
        row = [
            {"name": f"total {units}", "type": "big_number_total", "dataset": DS,
             "metric": "COUNT(*)", "subtitle": "Orders booked", "number_format": ",.0f",
             "width": 6, "height": units},
            {"name": f"trend {units}", "type": "big_number_trend", "dataset": DS,
             "metric": "SUM(sales)", "time_column": "order_date", "time_grain": "P1M",
             "compare_lag": 1, "compare_suffix": "vs previous month",
             "subtitle": "Booked revenue", "number_format": "$,.0f", "width": 6,
             "height": units},
        ]
        charts += row
        rows.append([c["name"] for c in row])
    return {"spec_version": "1", "dashboard": {"title": f"Recorded: {slug}", "slug": slug},
            "charts": charts, "layout": {"rows": rows}}


HOOK = r"""
(() => {
  const fill = CanvasRenderingContext2D.prototype.fillText;
  CanvasRenderingContext2D.prototype.fillText = function (text) {
    try {
      const c = this.canvas;
      c.__cwFonts = c.__cwFonts || {};
      c.__cwFonts[this.font] = (c.__cwFonts[this.font] || 0) + 1;
    } catch (e) {}
    return fill.apply(this, arguments);
  };
})();
"""

MEASURE_JS = r"""
() => {
  const px = el => el ? parseFloat(getComputedStyle(el).fontSize) : null;
  const h = el => Math.round(el.getBoundingClientRect().height * 10) / 10;
  const out = {};
  for (const holder of document.querySelectorAll('.dashboard-component-chart-holder')) {
    const title = holder.querySelector('.header-title');
    if (!title) continue;
    const item = {title: px(title)};
    const td = holder.querySelector('tbody td'), th = holder.querySelector('thead th');
    if (td) {
      item.td = px(td);
      item.th = px(th);
      item.row_label = px(holder.querySelector('tbody th'));
      const rows = [...holder.querySelectorAll('tbody tr')].slice(1, 6).map(h);
      item.row = rows.length ? Math.max(...rows) : null;
      item.head = Math.max(...[...holder.querySelectorAll('thead tr')].map(h));
    }
    for (const [cls, key] of [['header-line', 'value'], ['subheader-line', 'label'],
                              ['subtitle-line', 'subtitle']]) {
      const el = holder.querySelector('.' + cls);
      if (el) item[key] = px(el);
    }
    const fonts = {};
    for (const c of holder.querySelectorAll('canvas')) Object.assign(fonts, c.__cwFonts || {});
    if (Object.keys(fonts).length) item.canvas_fonts = Object.keys(fonts).sort();
    out[title.innerText.trim()] = item;
  }
  return out;
}
"""


def measure(base: str, username: str, password: str, slug: str) -> dict:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            ctx = browser.new_context(viewport={"width": VIEWPORT[0], "height": VIEWPORT[1]},
                                      device_scale_factor=1)
            ctx.add_init_script(HOOK)
            page = ctx.new_page()
            page.set_default_timeout(90_000)
            page.goto(f"{base}/login/")
            page.wait_for_selector("input[type=password]")
            page.locator("input:not([type=password]):not([type=hidden])").first.fill(username)
            page.fill("input[type=password]", password)
            page.keyboard.press("Enter")
            page.wait_for_load_state("networkidle")
            page.goto(f"{base}/superset/dashboard/{slug}/")
            page.wait_for_load_state("networkidle")
            for _ in range(3):      # draw every chart that renders on scroll, then settle
                for _ in range(12):
                    page.mouse.wheel(0, 700)
                    page.wait_for_timeout(400)
                page.wait_for_timeout(2000)
            return page.evaluate(MEASURE_JS)
        finally:
            browser.close()


def delete(client: SupersetClient, slug: str) -> None:
    dash = client.find_dashboard_by_slug(slug)
    if not dash:
        return
    charts = client.dashboard_charts(dash["id"])
    client._send(lambda: client.session.delete(
        f"{client.base_url}/api/v1/dashboard/{dash['id']}", timeout=60))
    for chart in charts:
        client.delete_chart(chart["id"])


CHECKED = ("default", "cells", "kpi")


def differences(expected, got, path: str = "") -> list[str]:
    """Where a measurement differs from the record: font sizes exactly, row heights to
    half a pixel (sub-pixel layout varies with the fonts a machine has)."""
    if isinstance(expected, dict) and isinstance(got, dict):
        out = []
        for key in sorted(set(expected) | set(got)):
            out += differences(expected.get(key), got.get(key), f"{path}/{key}")
        return out
    if isinstance(expected, (int, float)) and isinstance(got, (int, float)):
        slack = 0.5 if path.endswith(("/row", "/head")) else 0
        return [] if abs(expected - got) <= slack else [f"{path}: {got} (recorded {expected})"]
    return [] if expected == got else [f"{path}: {got!r} (recorded {expected!r})"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", action="append", required=True)
    ap.add_argument("--username", default="admin")
    ap.add_argument("--keep", action="store_true", help="leave the dashboards in place")
    ap.add_argument("--check", action="store_true",
                    help="compare with the fixture instead of writing it")
    args = ap.parse_args(argv)
    password = os.environ.get("SDC_CI_PASSWORD", "admin")  # sandbox default; never in argv

    record = json.loads(OUT.read_text()) if OUT.exists() else {"releases": {}}
    record["about"] = ("What headless Chromium computed at a 1440 x 900 window for a "
                       "table, a pivot, a big number and a line chart under each "
                       "stylesheet in css, and for big numbers at heights 2 to 10 (kpi); "
                       "written by tools/record_readability_measurements.py.")
    record["css"] = VARIANTS
    failures: list[str] = []
    for base in args.base_url:
        base = base.rstrip("/")
        client = SupersetClient(base, args.username, password)
        client.login()
        release, measured = client.superset_version(), {}
        specs = {name: small(f"{PREFIX}-{name}", css) for name, css in VARIANTS.items()}
        specs["kpi"] = kpi_grid(f"{PREFIX}-kpi")
        if args.check:
            if release not in record["releases"]:
                print(f"FAIL no measurements recorded for {release}")
                return 1
            specs = {name: specs[name] for name in CHECKED}
        for name, data in specs.items():
            spec = load_spec(data)
            report = run_apply(spec, client, "record")
            if not report.ok:
                print(report.to_json())
                return 1
            measured[name] = measure(base, args.username, password, spec.dashboard.slug)
            if args.check:
                diff = differences(record["releases"][release][name], measured[name],
                                   f"{release}/{name}")
                failures += diff
                print(("ok   " if not diff else "FAIL ") + f"{release} {name}"
                      + "".join(f"\n     {d}" for d in diff), flush=True)
            else:
                print(release, name, flush=True)
            if not args.keep:
                delete(client, spec.dashboard.slug)
        record["releases"][release] = measured
    if args.check:
        return 1 if failures else 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
