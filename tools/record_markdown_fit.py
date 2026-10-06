"""Record how tall Superset draws markdown blocks of known content, so the
size.markdown-fit estimate (chartwright/design/markdown_fit.py) is tested offline
against real renders (tests/test_markdown_fit.py reads
tests/fixtures/markdown_fit/measurements.json).

For each instance, in headless Chromium, in a window 1600 and 1440 px wide (VIEWPORT,
the window the rule assumes):
- `blocks`: a dashboard (slug cw-test-mdfit-<release>) holding every CORPUS text at
  every width in WIDTHS. For each block it reads `content_w`, the width the text wraps
  in (the holder less its padding); `need`, the height that shows everything with no
  inner scrollbar, padding and outer margins included (scrollHeight with the holder
  squeezed to 1 px, so the content overflows and its full height is reported); and
  `text`, how far down the last line of text (or rule, or table) reaches;
- `filter_bar`: the about text at every width on a dashboard with a native filter,
  whose bar opens on the left and narrows the grid;
- `styled`: a dashboard's own markdown blocks under its own CSS (STYLED);
- `containers` (1600 px): the width a block wraps its text in from a header row, a
  sub-tab and a footer row, against layout rows;
- `styles` (1600 px): every markdown element's computed type, with no CSS, with
  every property set on every element from `.dashboard-markdown X` (PROBE_CSS,
  LINE_CSS), and under the styled dashboard's CSS: what the cascade gives each element;
- `verify` and `verify_css` (1440 px), with Chromium's classic scrollbars on, as
  Windows draws them: each corpus text at 6/12 at the height the estimate's fix
  writes (`fix`), the height `need` asks for rounded up to an 8 px grid row, and a
  unit less; each styled block at the fix's height and at a height that cuts it
  off (`then`). For each it records the box, whether it scrolls, and
  where the text ends (read before anything is squeezed), and with --shots saves a
  screenshot of the block.

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
from chartwright.design.markdown_fit import VIEWPORT, estimate  # noqa: E402
from chartwright.spec import load_spec  # noqa: E402

OUT = REPO / "tests" / "fixtures" / "markdown_fit" / "measurements.json"
WIDTHS = [12, 8, 6, 4, 3]
DS = {"database": "examples", "table": "cleaned_sales_data"}

# Definitions in five sections, with headings and a bullet list, like a dashboard's
# notes tab (about 1,000 words).
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

# A dashboard with its own CSS: its markdown blocks as (name, width, a height that
# cuts it off in a 1440 px window, text), the kind a sales review carries: a title
# with a subtitle, section captions with colour keys, a list, notes in sections, a
# footer. Its CSS sets the text sizes the way such a dashboard does: 17 px section
# headings with a rule under them, 13 px captions after them (`h4 + p`), 14.5 px
# text on 1.5 lines, no margin under the last paragraph or list, list items 4 px
# apart, colour keys drawn by `[itemprop]::before`, and rules for other components.
STYLED = [
    ('title', 9, 2.4, """## STOREFRONT
Quarterly sales review for the regional store network · 48 stores, 46 ranked · orders since 2016, returns since Mar 2019 · built from `storefront.json`"""),
    ('caption-week', 12, 2.0, """#### Orders across the week
<span itemprop="this-year"></span>**Apr–Jun 2026** · <span itemprop="last-year"></span>**Apr–Jun 2025** · <span itemprop="target"></span>**Target** — orders as a share of each week's busiest day, by weekday. Titles: the typical Monday lift; two quiet outlets show a 3-day sum."""),
    ('caption-next', 12, 2.4, """#### Stores to visit next
Branches that gained 150 orders a week or more. Shaded: growth well outside the usual seasonal swing. A jump over one year that vanishes over two is a bounce. Lift ÷ peak: the hard-week 3-day lift in orders against the best week. ◇ an estimate; a greyed yearly change has no till count behind it, and greyed refunds sit outside ±2% (Notes tab)."""),
    ('summary-list', 4, 13, """### What changed · 12 months to Sep 2026
- **North region grew fastest:** repeat orders rose 3.5 k a week (+4.8%). The city branch sells 53% more than in 2019, the mall 23% more; Store 12 (+12.3%) and Store 7 (+7.8%) led.
- **Weekend staffing falls short.** On the busiest Saturday in ten, ◇ over two extra shifts are needed in 18 of 20 large stores: Store 3 needs 2.9 and Store 9 needs 3.2. Only Stores 1 and 4 cope.
- **Two stores already have slack.** ◇ Their rotas exceed peak demand (1.48×, 1.27×), and their part-time pools hold 2.3× and 4.5× the hours a busy Saturday uses. Store 3 covers just 0.04× of its own Saturday peak.
- **Some gains look durable.** Across the year, weekly orders in 5 stores (2, 5, 7, 12 and 15) sat above their seasonal range for three months or longer, and basket size did in 7. Delivery has a full year of history in only 5 stores, none above its range so far. With 46 stores tested, roughly one such result could be luck alone.

*Updated Sat 3 Oct 2026 from the overnight load. Rankings cover store sales only; regional budgets come from finance.*"""),
    ('caption-rush', 12, 2.4, """#### When the rush arrives
Shows the store selected in the filters. Left, orders per hour across the months: a darker cell means more orders, and the lunch peak grows as delivery spreads. Right, each day's evening rush, whiskers at the busiest day in ten: rotas are planned for the whisker, not the box."""),
    ('caption-change', 12, 2.8, """#### What moved the basket, and will it hold?
The waterfall breaks the quarter-on-quarter movement in the **average** basket, Apr–Jun 2025 to Apr–Jun 2026, into price, volume and product mix: each bar is the part one driver added to the total. <span itemprop="up"></span>**raises the basket** · <span itemprop="down"></span>**lowers it**. The cards above use the median order. Right, each month's basket change scaled by how much that store normally varies: <span itemprop="this-year"></span>**outside the range** for three months in a row counts as durable, as does a <span itemprop="trend"></span>**12-month average** past the dotted line. The waterfall needs sales in both quarters (none for the two new stores); the range needs four prior years of history (not Stores 41 or 46)."""),
    ('caption-years', 12, 2.0, """#### Eight years of Saturday peaks
<span itemprop="this-year"></span>**Median Saturday** · <span itemprop="last-year"></span>**Busiest Saturday**, by month since online orders were first logged; gaps mark months a store was shut for a refit."""),
    ('caption-growth', 12, 2.4, """#### Which stores grow, and how steadily?
Repeat orders, the share from returning customers: loyalty schemes push it up and promotions hardly shift it. <span itemprop="this-year"></span>**Repeat orders added** · <span itemprop="last-year"></span>**All orders added**: where the two agree, growth comes from regulars. All orders swing with promotions and track the sale calendar. Listed branches gained 200 weekly orders or more."""),
    ('caption-stores', 12, 2.0, """#### Where repeat customers shop
Left, the same stores against their own 2019 sales. Right, two city branches, the riverside store (RIV) and the station store (STN), whose busiest hours fall at lunchtime, so their weekday base is mostly office trade."""),
    ('caption-staff', 12, 2.0, """#### Which stores are staffed for Saturday, and which need help?
Rostered staff per region, and on the left the rota's hours against the busiest Saturday: <span itemprop="this-year"></span>**on the rota** · <span itemprop="last-year"></span>**with open vacancies**; 1 means the rota exactly meets the peak (stores needing 40 hours or more). On the right, extra shifts needed to shorten the Saturday queue by a tenth on the hardest day in ten: above 2, the pool falls short and agency cover is needed."""),
    ('caption-floor', 12, 2.4, """#### Who serves the evening rush?
For the selected store: who is on the shop floor at 13:00, the lunch peak, and at 19:00, after offices close; stockroom and delivery staff aren't counted. Self-checkout use appears only where a store reports it to head office, and some stores count assisted sales twice while others count them once (Store 9 changed method last December), which is why the hourly view shows assisted sales alone. A month where tills and ledger disagree by over 5% stays empty (Store 22 from Dec 2025)."""),
    ('notes', 12, 15, """### Busy hours
**Busy hours** are the three consecutive hours with the most till transactions in a trading day, counted from opening to closing time and only on days the store is open for at least eight hours. Click-and-collect orders are left out, because they are picked before the doors open and would pull the busiest window into the early morning. Each store shows two figures: a **typical Saturday**, the median, and a **hard Saturday**, the ninth worst in ten.

### Loyal customers
**◇ Loyalty** is an estimate: the share of transactions paid with a card the store has seen in the previous ninety days. Cards stand in for identity, so cash customers are invisible to it, and stores near tourist sites read low all summer. The ranking greys any store whose cash share moved by more than five points. **Basket size** is the value of an average transaction before discounts and refunds, with gift cards left out. Figures marked *index* are scaled so the first full year is one hundred.

### Is it lasting?
A store's monthly result is compared with the same month of the previous year, then scaled by how much that store usually varies in that month, using every year from 2019–2025. Holidays that move between months, such as Easter, are averaged out first. A change is **lasting** when the scaled result stays outside the usual range for three consecutive months; with 46 stores, about one store a month would cross the range on noise alone, and about two would do so at least once in a year. A change is also **lasting** when the full year, averaged, lies far beyond the range. Stores open for fewer than four full years have no usual range yet and show no verdict.

### The waterfall
The waterfall breaks the movement in **average basket value** between two quarters into price, volume and product mix, in that order, so the three bars always add up to the total. The order matters: moving mix first would shift a little from price to mix.

### ◇ Estimates
- **Rota ÷ Saturday peak:** staffed hours on the published rota divided by the hours the busiest Saturday in ten would need. Rotas change during the week and sickness is not recorded, so the ratio overstates cover on bad days. The ranking table uses the same ratio against the typical Saturday. Rotas older than six weeks fall back to the store's template.
- **Extra shifts:** how many eight-hour shifts it takes to bring the hard Saturday's queue down by a tenth, assuming every shift starts on time.

### What the figures don't cover
Online-only orders, wholesale accounts, staff costs and margins are not in the till data. Use this page to choose which stores to visit; it does not set any budgets."""),
    ('caption-tills', 12, 2.4, """#### Do the tills add up?
Till variance is (card takings + cash takings − ledger sales) ÷ ledger sales and should stay close to zero; months outside ±2% are greyed. The remaining 39 stores agree within ±1%. Store 22's card data is incomplete from Dec 2025; its basket figures, which use card payments only, still hold. Store 9 has run above +2% since June."""),
    ('footer', 12, 1.2, """##### Source: store till system and head-office ledger, nightly extract (September 2026), for internal use only · ◇ = estimated figure, see the Notes tab for how each one is made · made with Chartwright from storefront.json"""),
]
STYLED_CSS = """/* Storefront: layout first, then the markdown's text sizes. */
.dashboard-component-tabs,
.dashboard-component-tabs .dashboard-component-tabs-content { background-color: #F5F6F8 !important; }
.dashboard-component-tabs .ant-tabs-tab { font-size: 13px; padding: 8px 0; }
.dashboard-component-chart-holder { border: 1px solid #E2E5EA; border-radius: 4px; }
.background--white .dashboard-component-chart-holder { border: 0; }
.dashboard-markdown .dashboard-component-chart-holder { background: transparent; border: 0; }
.header-title { font-size: 14px !important; font-weight: 600 !important; }
.dashboard-markdown { color: #20242C; }
.dashboard-markdown h2 { font-size: 24px; font-weight: 700; letter-spacing: -.02em; margin: 0; }
.dashboard-markdown h2 + p { font-size: 13px; color: #5F6672; margin: 6px 0 0; }
.dashboard-markdown h3 { font-size: 16px; font-weight: 600; margin: 12px 0 6px; }
.dashboard-markdown h4 { font-size: 17px; font-weight: 600 !important; margin: 0;
  padding-bottom: 6px; border-bottom: 2px solid #DDE1E7; }
.dashboard-markdown h4 + p { font-size: 13px; color: #5F6672; margin: 6px 0 0; line-height: 1.4; }
.dashboard-markdown h4 + p strong { color: #20242C; font-weight: 600; }
.dashboard-markdown p, .dashboard-markdown li { font-size: 14.5px; line-height: 1.5; }
/* no gap under the last paragraph or list */
.dashboard-markdown p:last-child, .dashboard-markdown ul:last-child { margin-bottom: 0 !important; }
.dashboard-markdown li { margin: 0 0 4px; }
/* 11.5 px on 1.4 lines: 16.1 px, a tenth of a px past a 48 px block's room */
.dashboard-markdown h5 { font-size: 11.5px; font-weight: 400; color: #5F6672; margin: 0; }
.dashboard-markdown em { color: #5F6672; font-style: normal; font-size: 12px; }
.dashboard-markdown code { font-family: inherit; font-size: 12px; background: #EEF0F3; padding: 1px 4px; }
/* colour keys: <span itemprop="..."></span> survives the sanitizer */
.dashboard-markdown [itemprop]::before { content: ""; display: inline-block; width: 12px; height: 8px;
  margin: 0 4px 0 2px; }
.dashboard-markdown [itemprop="this-year"]::before { background: #1F3A5F; }
.dashboard-markdown [itemprop="last-year"]::before { background: #B9C2CE; }
.dashboard-markdown [itemprop="target"]::before { background: #E3B04B; }
.dashboard-markdown [itemprop="up"]::before { background: #2E7D5B; }
.dashboard-markdown [itemprop="down"]::before { background: #B4463A; }
.dashboard-markdown [itemprop="trend"]::before { background: #55606E; }
/* other components */
[data-test-viz-type="big_number_total"] .header-line { font-size: 32px !important; line-height: 1.1 !important; }
[data-test-chart-name="Updated"] { position: relative; }
.superset-chart-table table tbody td { border-top: 1px solid #EEF0F3; }"""

# Every element markdown draws, for the computed-style probes below.
STYLE_PROBE = """# H1 text
## H2 text
### H3 text
#### H4 text
##### H5 text
###### H6 text

Para with *em* and **strong** and `code` and [a link](http://x).

Second para.

- item one
- item two
  - nested

1. first
2. second

| a | b |
|---|---|
| 1 | 2 |

> quoted

```
pre code
```

---

Last para."""
_ELEMENTS = ["h1", "h2", "h3", "h4", "h5", "h6", "p", "ul", "ol", "li", "table", "th", "td",
             "tr", "blockquote", "pre", "hr", "em", "strong", "code", "a"]
# Every property the estimate reads, on every element, from `.dashboard-markdown X`:
# which of them beat Superset's own styles.
PROBE_CSS = "\n".join(f".dashboard-markdown {e} {{ font-size: 10px; margin: 3px 0 5px; "
                      f"padding: 1px 0 2px; }}" for e in _ELEMENTS)
LINE_CSS = "\n".join(f".dashboard-markdown {e} {{ line-height: 30px; }}" for e in _ELEMENTS)


def spec_for(slug: str, title: str, layout: dict, filters: bool = False,
             css: str | None = None) -> dict:
    """A dashboard of markdown blocks; the one chart a spec needs sits in its own row."""
    count = f"{title} count"
    data = {
        "spec_version": "1",
        "dashboard": {"title": title, "slug": slug, **({"css": css} if css else {})},
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

# Each markdown block's elements in document order with their computed type: block
# elements as [tag, font size, line height, margin top, bottom, padding top, bottom,
# border top, bottom, horizontal inset, weight], inline ones with the index of the
# block element they sit in and their tags, outermost first.
STYLES_JS = r"""
() => {
  const BLOCK = new Set(['P','H1','H2','H3','H4','H5','H6','UL','OL','LI','TABLE','THEAD','TBODY',
                         'TR','TH','TD','BLOCKQUOTE','PRE','HR']);
  const px = v => Math.round(parseFloat(v) * 1000) / 1000;
  const out = {};
  for (const md of document.querySelectorAll('.dashboard-markdown')) {
    const holder = md.querySelector('.dashboard-component-chart-holder');
    if (!holder) continue;
    const blocks = [], inline = [];
    for (const el of holder.querySelectorAll('*')) {
      const s = getComputedStyle(el);
      const row = [px(s.fontSize), px(s.lineHeight), px(s.marginTop), px(s.marginBottom),
                   px(s.paddingTop), px(s.paddingBottom), px(s.borderTopWidth), px(s.borderBottomWidth),
                   px(s.marginLeft) + px(s.marginRight) + px(s.paddingLeft) + px(s.paddingRight)
                     + px(s.borderLeftWidth) + px(s.borderRightWidth), Number(s.fontWeight)];
      if (BLOCK.has(el.tagName)) { blocks.push([el.tagName.toLowerCase(), ...row]); el.dataset.cwAt = blocks.length - 1; continue; }
      if (el.closest('pre')) continue;
      const tags = [];
      let up = el;
      while (up && !BLOCK.has(up.tagName)) { tags.unshift(up.tagName.toLowerCase()); up = up.parentElement; }
      inline.push([Number(up.dataset.cwAt), tags, ...row]);
    }
    out[md.id] = {blocks, inline};
  }
  return out;
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


def _open(page, base: str, slug: str) -> None:
    page.goto(f"{base}/superset/dashboard/{slug}/")
    page.wait_for_load_state("networkidle")
    page.wait_for_selector(".dashboard-markdown")
    page.evaluate("document.fonts.ready.then(() => true)")
    page.wait_for_timeout(1500)


def _measure(page, base: str, slug: str, ids: list[str]) -> tuple[float, dict[str, dict]]:
    """Open the dashboard and measure its markdown blocks, scrolling until each has
    drawn. A block's first reading is kept: squeezing it for `need` can leave a
    classic scrollbar behind, which narrows the text on the next reading."""
    _open(page, base, slug)
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


def _styles(page, base: str, slug: str, ids: list[str]) -> dict[str, dict]:
    """Each markdown block's computed styles (STYLES_JS), scrolling until all drew."""
    _open(page, base, slug)
    seen: dict[str, dict] = {}
    for _ in range(400):
        for key, value in page.evaluate(STYLES_JS).items():
            if value["blocks"]:
                seen.setdefault(key, value)
        if all(i in seen for i in ids):
            return seen
        page.mouse.wheel(0, 600)
        page.wait_for_timeout(250)
    raise SystemExit(f"{slug}: never drew {[i for i in ids if i not in seen]}")


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


def _page(p, width: int, scrollbars: bool = False):
    # Playwright hides scrollbars in headless Chromium; a reader on Windows sees them.
    browser = p.chromium.launch(ignore_default_args=["--hide-scrollbars"] if scrollbars else None)
    page = browser.new_context(viewport={"width": width, "height": 1200}).new_page()
    page.set_default_timeout(90000)
    return browser, page


def verify(client, base: str, username: str, password: str, slug: str,
           blocks: list[tuple[str, str, int, float]], css: str | None = None,
           shots: Path | None = None, viewport: int = VIEWPORT) -> dict:
    """Apply each block, as (key, markdown, width, height), open the dashboard with
    Chromium's classic scrollbars on, as Windows draws them, and read each block's
    box, whether it scrolls and where its text ends. Deletes the dashboard."""
    from playwright.sync_api import sync_playwright

    data = spec_for(slug, slug.replace("-", " "), rows_of(
        [{"markdown": md, "width": w, "height": h} for _, md, w, h in blocks]), css=css)
    out: dict = {}
    try:
        _apply(client, data)
        with sync_playwright() as p:
            browser, page = _page(p, viewport, scrollbars=True)
            _login(page, base, username, password)
            ids = _ids(data)
            _, seen = _measure(page, base, slug, ids)
            for (key, _, w, h), i in zip(blocks, ids):
                b = seen[i]
                out[key] = {"width": w, "height": h, "box": b["box"], "scrolls": b["scrolls"],
                            "text": b["text"]}
                if shots is not None:
                    shots.mkdir(parents=True, exist_ok=True)
                    # The resizable container inside carries the same id.
                    page.locator(f".dashboard-markdown[id='{i}']").screenshot(
                        path=str(shots / f"{slug}-{key.replace('@', '-')}-{h:g}.png"))
            browser.close()
    finally:
        _delete(client, slug)
    return out


def verify_heights() -> tuple[list, list]:
    """The verify pass: each corpus text at 6/12 at the fix's height, at the height it
    measured (whole 8 px grid rows) and a unit less; each styled block at its own
    width, at the fix's height and at the height it had when it was cut off."""
    corpus = []
    for name, text in CORPUS.items():
        corpus += [(f"{name}@fix", text, 6, estimate(text, 6).units)]
    styled = []
    for name, width, then, text in STYLED:
        styled += [(f"{name}@fix", text, width, estimate(text, width, css=STYLED_CSS).units),
                     (f"{name}@then", text, width, then)]
    return corpus, styled


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
        "styled": spec_for(f"{slug}-s", f"cw test mdfit {tag} s", rows_of(
            [{"markdown": text, "width": w, "height": h} for _, w, h, text in STYLED]),
            css=STYLED_CSS),
    }
    probes = {name: spec_for(f"{slug}-{name}", f"cw test mdfit {tag} {name}", rows_of(
        [{"markdown": STYLE_PROBE, "width": 12, "height": 30}]), css=css)
        for name, css in (("default", None), ("probe", PROBE_CSS), ("line", LINE_CSS))}
    out: dict = {"viewports": {}, "containers": {}, "styles": {}}
    try:
        for data in [*specs.values(), *probes.values()]:
            _apply(client, data)
        with sync_playwright() as p:
            for width in (1600, VIEWPORT):
                browser, page = _page(p, width)
                _login(page, base, username, password)
                at = out["viewports"][str(width)] = {}
                ids = _ids(specs["blocks"])
                at["grid_w"], seen = _measure(page, base, specs["blocks"]["dashboard"]["slug"], ids)
                at["blocks"] = {f"{n}@{w}": _keep(seen[i]) for (n, w), i in zip(names, ids)}
                ids = _ids(specs["filter_bar"])
                at["grid_w_filter_bar"], seen = _measure(
                    page, base, specs["filter_bar"]["dashboard"]["slug"], ids)
                at["filter_bar"] = {f"about@{w}": _keep(seen[i]) for w, i in zip(WIDTHS, ids)}
                ids = _ids(specs["styled"])
                _, seen = _measure(page, base, specs["styled"]["dashboard"]["slug"], ids)
                at["styled"] = {name: _keep(seen[i]) for (name, *_), i in zip(STYLED, ids)}
                if width == 1600:
                    ids = _ids(specs["containers"])
                    _, seen = _measure(page, base, specs["containers"]["dashboard"]["slug"], ids)
                    where = {"header": "header@12", "footer": "footer@4"}
                    out["containers"] = {where.get(i.split("-")[2], "sub-tab@6"):
                                         seen[i]["content_w"] for i in ids}
                    ids = _ids(specs["styled"])
                    seen = _styles(page, base, specs["styled"]["dashboard"]["slug"], ids)
                    out["styles"]["styled"] = {name: seen[i] for (name, *_), i in zip(STYLED, ids)}
                    for name, data in probes.items():
                        ids = _ids(data)
                        out["styles"][name] = _styles(page, base, data["dashboard"]["slug"], ids)[ids[0]]
                browser.close()
    finally:
        for data in [*specs.values(), *probes.values()]:
            _delete(client, data["dashboard"]["slug"])
    corpus, styled = verify_heights()
    # A corpus text at the height it measured, and a unit less.
    for name, text in CORPUS.items():
        fits = min(100, math.ceil(out["viewports"][str(VIEWPORT)]["blocks"][f"{name}@6"]["need"] / 8) / 5)
        corpus += [(f"{name}@fits", text, 6, fits),
                   (f"{name}@short", text, 6, round(max(0.2, fits - 1), 1))]
    out["verify"] = verify(client, base, username, password, f"{slug}-v", corpus, shots=shots)
    out["verify_css"] = verify(client, base, username, password, f"{slug}-vs", styled,
                               css=STYLED_CSS, shots=shots)
    return release, out


def dump(value, indent: int = 0) -> str:
    """The fixture as JSON, an array that holds a plain value (a row: one element's
    computed style, a styled block) on a line of its own."""
    pad = " " * (indent + 1)
    if isinstance(value, dict):
        items = [f"{pad}{json.dumps(k)}: {dump(v, indent + 1)}" for k, v in sorted(value.items())]
        text = "{\n" + ",\n".join(items) + "\n" + " " * indent + "}" if items else "{}"
    elif isinstance(value, list) and value and all(isinstance(v, (list, dict)) for v in value):
        text = "[\n" + ",\n".join(pad + dump(v, indent + 1) for v in value) + "\n" + " " * indent + "]"
    elif isinstance(value, list):
        text = "[" + ", ".join(dump(v, indent) for v in value) + "]"
    else:
        text = json.dumps(value, ensure_ascii=False)
    return text + ("\n" if indent == 0 else "")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", action="append", required=True)
    ap.add_argument("--username", default="admin")
    ap.add_argument("--shots", type=Path, help="save a screenshot of each verify block here")
    args = ap.parse_args(argv)
    password = os.environ.get("SDC_CI_PASSWORD", "admin")  # sandbox default; never in argv

    old = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    rec = {"releases": old.get("releases", {})}
    rec["about"] = ("Markdown blocks Superset drew in headless Chromium, in px; written by "
                    "tools/record_markdown_fit.py, which says what each number is.")
    rec["corpus"] = CORPUS
    rec["widths"] = WIDTHS
    rec["styled"] = [list(s) for s in STYLED]
    rec["styled_css"] = STYLED_CSS
    rec["style_probe"] = {"markdown": STYLE_PROBE, "probe": PROBE_CSS, "line": LINE_CSS}
    for base in args.base_url:
        release, measured = record(base.rstrip("/"), args.username, password, args.shots)
        rec["releases"][release] = measured
        scrolled = sorted(k for k, v in {**measured["verify"], **measured["verify_css"]}.items()
                          if v["scrolls"])
        grids = {w: (v["grid_w"], v["grid_w_filter_bar"]) for w, v in measured["viewports"].items()}
        print(release, f"grids {grids}; containers {measured['containers']}; scrolls at",
              scrolled, flush=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(dump(rec), encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
