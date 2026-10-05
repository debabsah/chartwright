"""Record how tall Superset draws markdown blocks of known content, so the
size.markdown-fit estimate (chartwright/design/markdown_fit.py) is tested offline
against real renders (tests/test_markdown_fit.py reads
tests/fixtures/markdown_fit/measurements.json).

For each instance, in headless Chromium at a 1600 x 1200 viewport:
- `blocks`: a dashboard (slug cw-test-mdfit-<release>) holding every CORPUS text at
  every width in WIDTHS. For each block it reads `content_w`, the width the text wraps
  in (the holder less its padding); `need`, the height that shows everything with no
  inner scrollbar, padding and outer margins included (scrollHeight with the holder
  squeezed to 1 px, so the content overflows and its full height is reported); and
  `text`, how far down the last line of text (or rule, or table) reaches;
- `filter_bar`: the about text at every width on a dashboard with a native filter,
  whose bar opens on the left and narrows the grid;
- `containers`: the width a block wraps its text in from a header row, a sub-tab and
  a footer row, against layout rows;
- `verify`: with Chromium's classic scrollbars on, as Windows draws them, each text at
  6/12 at three heights: the one the estimate's fix writes (`fix`), the one `need`
  asks for rounded up to an 8 px grid row, and a unit less. For each it records the
  box, whether it scrolls, and where the text ends (read before anything is
  squeezed), and with --shots saves a screenshot of the block.

Every dashboard is deleted at the end.

    pip install -e ".[visual]" && playwright install chromium
    SDC_CI_PASSWORD=admin python tools/record_markdown_fit.py \\
        --base-url http://localhost:8094 --base-url http://localhost:8098 [--shots DIR]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from chartwright.apply import apply as run_apply  # noqa: E402
from chartwright.client import SupersetClient  # noqa: E402
from chartwright.compiler import _position  # noqa: E402
from chartwright.design.markdown_fit import estimate  # noqa: E402
from chartwright.spec import load_spec  # noqa: E402

OUT = REPO / "tests" / "fixtures" / "markdown_fit" / "measurements.json"
WIDTHS = [12, 8, 6, 4, 3]
DS = {"database": "examples", "table": "cleaned_sales_data"}

# Definitions in five sections, with headings and a bullet list, like the About tab
# that rendered cut off mid-section at 10 units (about 1,000 words).
ABOUT = """## How to read this dashboard

Every figure on these tabs comes from the operations warehouse, refreshed nightly at 02:00 UTC. A day closes when the last flight of the schedule lands, so the most recent day can move until the following morning. Figures are shown in the network's reporting currency and converted at the month's average rate, never the daily rate, so a month never changes once it has closed. Where a chart says "network" it means every route the schedule operated in the period, including seasonal routes, and excludes charters, ferry flights and positioning flights, which carry no revenue passengers.

Hover over any point to see its exact value, and use the filter bar on the left to narrow every chart at once. A filter applies to every tab, not only the one in view. The period selector defaults to the last full quarter; widen it to compare a season with the same season a year earlier.

## Punctuality

- **On-time departure**: the share of departures that leave the gate within 15 minutes of the scheduled time. Cancelled flights are excluded from both sides of the ratio; diverted flights count by their original schedule.
- **On-time arrival**: the share of arrivals that reach the gate within 15 minutes of the scheduled time. This is the measure the regulator publishes and the one the monthly report leads with.
- **Average delay**: the mean minutes late across flights that were late, measured gate to gate. Early flights count as zero, never as negative minutes, so an early arrival cannot hide a late one.
- **Delay cause**: the first code the station controller recorded. Reactionary delays, caused by a late inbound aircraft, are grouped separately so that a single morning disruption does not read as a day of independent failures.
- **Completion factor**: flights operated as a share of flights scheduled two days before departure. Schedule changes made earlier than that are planning, not disruption, and do not count against it.

## Capacity and demand

Capacity is measured in available seat kilometres: every seat offered, multiplied by the great-circle distance of the sector it was offered on. Demand is measured in revenue passenger kilometres: every fare-paying passenger, multiplied by the same distance. Staff travel, infants without a seat and award tickets redeemed at zero fare are not revenue passengers.

Load factor is demand divided by capacity. It is shown both for the network and per route, and the route figures are weighted by distance, so a long sector moves the network average more than a short one. A route with a load factor above 90 percent for four consecutive weeks is flagged for a capacity review; one below 60 percent for the same period is flagged for a schedule review. Both flags appear in the route table on the Network tab and clear themselves once the route returns to the normal band.

Seat counts come from the aircraft actually operated, not the aircraft planned, so an equipment swap on the day changes the capacity of that flight. Blocked seats, such as crew rest seats on long sectors, are removed from the count.

## Revenue and yield

Passenger revenue is the fare the passenger paid for each sector, net of taxes, airport charges and fuel surcharges, which pass through to third parties. A ticket covering several sectors is prorated across them by distance. Ancillary revenue, such as checked bags, seat selection and onboard sales, is reported separately and never mixed into yield.

Yield is passenger revenue per revenue passenger kilometre, shown in cents. Unit revenue is passenger revenue per available seat kilometre, which falls when seats fly empty even if every passenger paid more. Read the two together: rising yield with falling unit revenue usually means fares went up faster than the planes filled.

Refunds are booked against the month of the original sale, not the month of the refund, so a large refund wave can revise a closed month's revenue. The revision is noted in the monthly summary and the chart marks the revised point with a hollow marker.

## Cost and efficiency

Unit cost is operating cost per available seat kilometre, shown in cents, with fuel shown both inside and outside the total because fuel moves with markets the airline does not control. Operating cost includes crew, maintenance, airport and navigation charges, aircraft ownership and overheads, and excludes one-off items such as restructuring charges, which are listed in the notes of the month in which they occur.

Fuel burn per block hour is measured from the aircraft's own fuel records, not from the flight plan, and compared with the same aircraft type's fleet average. A tail that burns more than three percent above its type's average for a month is listed on the Fleet tab for an engineering review.

Aircraft utilisation is block hours per aircraft per day, counting only aircraft in the operating fleet. Aircraft in heavy maintenance, in storage or on lease to another operator are left out, so a fleet with many aircraft parked does not look less productive than it is.

## Data quality and changes

Each tab shows the time of its last successful refresh in the footer. If a source system is late, the affected charts show the previous day's figures and a note says which source is behind. A missing day is never filled in or estimated; it stays visibly empty until the source delivers it.

Definitions change rarely and only at the start of a quarter. When one does, the change is listed here with its date, the previous definition is kept for one year, and charts that span the change mark it with a vertical line so that a step in the series is not mistaken for a change in performance.

Questions about a figure go to the analytics team through the request form linked in the header. Please include the tab, the chart title and the filters you had set, so the figure can be reproduced exactly.
"""

CORPUS = {
    "about": ABOUT,
    "strip-text": "Figures refresh nightly at 02:00 UTC.",
    "strip-h1": "# Network performance",
    "strip-h2": "## Punctuality",
    "strip-h3": "### Delay causes",
    "strip-h4": "#### Notes",
    "strip-bold": "**Source:** operations warehouse, refreshed nightly",
    "two-lines": "Figures refresh nightly at 02:00 UTC.  \nThe most recent day can still move.",
    "br-html": "Figures refresh nightly.<br>The most recent day can move.<br><br>Questions go to the analytics team.",
    "section": ("## Capacity and demand\n\nCapacity is measured in available seat kilometres: "
                "every seat offered, multiplied by the great-circle distance of the sector it "
                "was offered on. Demand is measured in revenue passenger kilometres.\n\n"
                "- **Load factor**: demand divided by capacity\n"
                "- **Yield**: passenger revenue per revenue passenger kilometre\n"
                "- **Unit cost**: operating cost per available seat kilometre"),
    "loose-list": ("- First, filter to the period you need.\n\n"
                   "- Then pick a route from the table on the Network tab.\n\n"
                   "- Finally, open the delay causes for that route."),
    "nested-list": ("1. Punctuality\n   - On-time departure\n   - On-time arrival\n"
                    "2. Capacity\n   - Seat kilometres\n   - Load factor\n3. Revenue"),
    "table": ("| Measure | Unit | Source |\n|---|---|---|\n"
              "| On-time arrival | % of arrivals | Station records |\n"
              "| Load factor | % of seats | Reservations |\n"
              "| Yield | cents per RPK | Revenue accounting |\n"
              "| Unit cost | cents per ASK | Finance |\n"
              "| Utilisation | block hours per day | Maintenance |"),
    "caps-digits": ("ASK 1,234,567,890 | RPK 987,654,321 | LF 80.0% | CASK 10.25 | RASK 11.40 | "
                    "OTP-15 88.2% | CF 99.1% | BH/AC/DAY 12.4 | FUEL KG/BH 2,345 | "
                    "Q3 FY2026 VS Q3 FY2025: +4.2% ASK, +6.1% RPK, +1.5 PTS LF"),
    "quote-code": ("> Figures shown are provisional until the month closes.\n\n"
                   "```\nload_factor = rpk / ask\nyield = revenue / rpk\n```\n\n"
                   "---\n\nLast updated by the analytics team."),
    "long-word": ("See https://example.org/dashboards/operations/definitions/punctuality-and-"
                  "capacity-measures-for-the-network-report for the full list."),
    "setext": "Network report\n==============\n\nQuarterly figures\n-----------------\n\nBody text.",
}




def spec_for(slug: str, title: str, layout: dict, filters: bool = False) -> dict:
    """A dashboard of markdown blocks; the one chart a spec needs sits in its own row."""
    count = f"{title} count"
    data = {
        "spec_version": "1",
        "dashboard": {"title": title, "slug": slug},
        "charts": [{"name": count, "type": "big_number_total", "dataset": DS,
                    "metric": "COUNT(*)"}],
        "layout": layout,
    }
    target = data["layout"]["tabs"][0]["tabs"][0] if "tabs" in layout else data["layout"]
    target["rows"] = [*target.get("rows", []), [count]]
    if filters:
        data["filters"] = [{"name": "Deal size", "type": "select", "dataset": DS,
                            "column": "deal_size"}]
    return data


def rows_of(blocks: list[dict]) -> dict:
    return {"rows": [[b] for b in blocks]}


# Reads every markdown block drawn so far (a dashboard may draw rows only as they
# scroll into view). `need` squeezes the holder to 1 px so its content overflows:
# Chromium's scrollHeight then counts the padding and the children's outer margins,
# the height that shows the block with no inner scrollbar.
MEASURE_JS = r"""
() => {
  const grid = document.querySelector('.grid-container').getBoundingClientRect();
  const out = [];
  for (const md of document.querySelectorAll('.dashboard-markdown')) {
    const holder = md.querySelector('.dashboard-component-chart-holder');
    if (!holder || !holder.querySelector('p, h1, h2, h3, h4, h5, h6, ul, ol, table, pre')) continue;
    const cs = getComputedStyle(holder);
    const pad = parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight);
    const box = holder.clientHeight, scrolls = holder.scrollHeight > holder.clientHeight;
    const top = holder.getBoundingClientRect().top;
    const range = document.createRange();
    range.selectNodeContents(holder);
    const text = range.getBoundingClientRect().bottom - top;
    const keep = holder.style.height;
    holder.style.height = '1px';
    const need = holder.scrollHeight;
    holder.style.height = keep;
    out.push({id: md.id, content_w: holder.clientWidth - pad, box, scrolls, need,
              text: Math.round(text * 10) / 10});
  }
  return {grid_w: grid.width, blocks: out};
}
"""


def _login(page, base: str, username: str, password: str) -> None:
    page.goto(f"{base}/login/")
    page.wait_for_selector("input[type=password]")
    page.locator("input:not([type=password]):not([type=hidden])").first.fill(username)
    page.fill("input[type=password]", password)
    page.keyboard.press("Enter")
    page.wait_for_load_state("networkidle")


def _ids(data: dict) -> list[str]:
    return [k for k in _position(load_spec(data)) if k.startswith("MARKDOWN-")]


def _measure(page, base: str, slug: str, ids: list[str]) -> tuple[float, dict[str, dict]]:
    """Open the dashboard and measure its markdown blocks, scrolling until each has
    drawn. A block's first reading is kept: squeezing it for `need` can leave a
    classic scrollbar behind, which narrows the text on the next reading."""
    page.goto(f"{base}/superset/dashboard/{slug}/")
    page.wait_for_load_state("networkidle")
    page.wait_for_selector(".dashboard-markdown")
    page.wait_for_timeout(1500)
    seen: dict[str, dict] = {}
    grid_w = 0.0
    for _ in range(400):
        got = page.evaluate(MEASURE_JS)
        grid_w = got["grid_w"]
        for b in got["blocks"]:
            seen.setdefault(b["id"], b)
        if all(i in seen for i in ids):
            break
        page.mouse.wheel(0, 600)
        page.wait_for_timeout(250)
    missing = [i for i in ids if i not in seen]
    if missing:
        raise SystemExit(f"{slug}: never drew {missing}")
    return grid_w, seen


def _apply(client, data: dict) -> None:
    report = run_apply(load_spec(data), client, "record")
    if not report.ok:
        raise SystemExit(report.to_json())


def _delete(client, slug: str) -> None:
    dash = client.find_dashboard_by_slug(slug)
    if dash is None:
        return
    charts = client.dashboard_charts(dash["id"])
    url = f"{client.base_url}/api/v1/dashboard/{dash['id']}"
    client._send(lambda: client.session.delete(url, timeout=60))
    for c in charts:
        client.delete_chart(c["id"])


def _keep(b: dict) -> dict:
    return {k: b[k] for k in ("content_w", "need", "text")}


def verify(client, base: str, username: str, password: str, slug: str,
           heights: list[tuple[str, str, float]], shots: Path | None = None) -> dict:
    """Apply each CORPUS text at 6/12 at the heights given, as (text, kind, height), open
    the dashboard with Chromium's classic scrollbars on, as Windows draws them, and read
    each block's box, whether it scrolls and where its text ends. Deletes the dashboard."""
    from playwright.sync_api import sync_playwright

    data = spec_for(slug, slug.replace("-", " "), rows_of(
        [{"markdown": CORPUS[n], "width": 6, "height": h} for n, _, h in heights]))
    out: dict = {}
    try:
        _apply(client, data)
        with sync_playwright() as p:
            # Playwright hides scrollbars in headless Chromium; a reader on Windows sees them.
            browser = p.chromium.launch(ignore_default_args=["--hide-scrollbars"])
            page = browser.new_context(viewport={"width": 1600, "height": 1200}).new_page()
            page.set_default_timeout(90000)
            _login(page, base, username, password)
            ids = _ids(data)
            _, seen = _measure(page, base, slug, ids)
            for (name, kind, h), i in zip(heights, ids):
                b = seen[i]
                out[f"{name}@{kind}"] = {"height": h, "box": b["box"], "scrolls": b["scrolls"],
                                         "text": b["text"]}
                if shots is not None:
                    shots.mkdir(parents=True, exist_ok=True)
                    # The resizable container inside carries the same id.
                    page.locator(f".dashboard-markdown[id='{i}']").screenshot(
                        path=str(shots / f"{slug}-{name}-{kind}-{h:g}.png"))
            browser.close()
    finally:
        _delete(client, slug)
    return out


def record(base: str, username: str, password: str, shots: Path | None) -> tuple[str, dict]:
    from playwright.sync_api import sync_playwright

    client = SupersetClient(base, username, password)
    client.login()
    release = client.superset_version()
    tag = release.replace(".", "")
    slug = f"cw-test-mdfit-{tag}"
    names = [(name, w) for name in CORPUS for w in WIDTHS]
    md = lambda name, w: {"markdown": CORPUS[name], "width": w, "height": 4}  # noqa: E731
    specs = {
        "blocks": spec_for(slug, f"cw test mdfit {tag}", rows_of([md(n, w) for n, w in names])),
        "filter_bar": spec_for(f"{slug}-f", f"cw test mdfit {tag} f",
                               rows_of([md("about", w) for w in WIDTHS]), filters=True),
        "containers": spec_for(f"{slug}-c", f"cw test mdfit {tag} c", {
            "header": [[md("about", 12)]],
            "tabs": [{"title": "Tab", "tabs": [{"title": "Sub-tab", "rows": [[md("about", 6)]]}]}],
            "footer": [[md("about", 4)]],
        }),
    }
    out: dict = {"blocks": {}, "filter_bar": {}, "containers": {}}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_context(viewport={"width": 1600, "height": 1200}).new_page()
            page.set_default_timeout(90000)
            _login(page, base, username, password)
            for key, data in specs.items():
                _apply(client, data)
                ids = _ids(data)
                grid_w, seen = _measure(page, base, data["dashboard"]["slug"], ids)
                if key == "blocks":
                    out["grid_w"] = grid_w
                    out["blocks"] = {f"{n}@{w}": _keep(seen[i]) for (n, w), i in zip(names, ids)}
                elif key == "filter_bar":
                    out["grid_w_filter_bar"] = grid_w
                    out["filter_bar"] = {f"about@{w}": _keep(seen[i]) for w, i in zip(WIDTHS, ids)}
                else:
                    where = {"header": "header@12", "footer": "footer@4"}
                    out["containers"] = {where.get(i.split("-")[2], "sub-tab@6"):
                                         seen[i]["content_w"] for i in ids}
            browser.close()
    finally:
        for data in specs.values():
            _delete(client, data["dashboard"]["slug"])
    # Each text at the fix's height, at the height it measured (whole 8 px grid rows),
    # and a unit less.
    heights = []
    for name in CORPUS:
        fits = min(100, math.ceil(out["blocks"][f"{name}@6"]["need"] / 8) / 5)
        heights += [(name, "fix", estimate(CORPUS[name], 6).units), (name, "fits", fits),
                    (name, "short", round(max(0.2, fits - 1), 1))]
    out["verify"] = verify(client, base, username, password, f"{slug}-v", heights, shots)
    return release, out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", action="append", required=True)
    ap.add_argument("--username", default="admin")
    ap.add_argument("--shots", type=Path, help="save a screenshot of each verify block here")
    args = ap.parse_args(argv)
    password = os.environ.get("SDC_CI_PASSWORD", "admin")  # sandbox default; never in argv

    rec = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {"releases": {}}
    rec["about"] = ("Markdown blocks Superset drew in headless Chromium at a 1600 x 1200 "
                    "viewport, in px; written by tools/record_markdown_fit.py, which says "
                    "what each number is.")
    rec["corpus"] = CORPUS
    rec["widths"] = WIDTHS
    for base in args.base_url:
        release, measured = record(base.rstrip("/"), args.username, password, args.shots)
        rec["releases"][release] = measured
        scrolled = sorted(k for k, v in measured["verify"].items() if v["scrolls"])
        print(release, f"grid {measured['grid_w']} px ({measured['grid_w_filter_bar']} px "
              f"beside the filter bar); containers {measured['containers']}; scrolls at",
              scrolled, flush=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rec, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
