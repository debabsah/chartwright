#!/usr/bin/env python3
"""Write sundown.json, the Chartwright spec for the Sundown dashboard.

    python3 build_spec_sundown.py facts.json [palette] > sundown.json

facts.json holds the numbers the titles and time windows are built from, queried from a
Postgres warehouse loaded with EIA-930 hourly grid data and EIA-860M (schema `sundown`).
Titles carry units and windows; section names are plain; definitions live in chart
descriptions and the glossary table; colour carries status. A new facts.json changes the
data-driven titles, and `chartwright plan` shows what moved. Chart names stay fixed: they
are the charts' identity. The palette is palettes/<palette>.json (default coral-teal-v2).
"""

import json
import math
import sys
from datetime import date, timedelta
from pathlib import Path

F = json.load(open(sys.argv[1], encoding="utf-8"))
PALETTE = sys.argv[2] if len(sys.argv) > 2 else "coral-teal-v2"

DB, SCHEMA = "grid_warehouse", "sundown"


# ---------------------------------------------------------------- windows, all from the facts
def first_of_next_month(d: date) -> date:
    return (d.replace(day=28) + timedelta(days=4)).replace(day=1)


def axis_floor(lowest: float | None, step: float) -> float:
    """A value axis's floor from the data: zero, or the lowest value the chart can draw rounded
    down a step. Recomputed on each refresh, so a new low is never clipped."""
    return min(0, math.floor((lowest or 0) / step) * step)


def months_back(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 - n, 12)
    return date(d.year + y, m + 1, 1)


MONTH = F["month"]["label"]                                   # "Sep 2026"
MONTH_END = F["month"]["end"]
END = first_of_next_month(date.fromisoformat(F["month"]["start"]))        # exclusive: 2026-10-01
START12, START24 = months_back(END, 12), months_back(END, 24)
BATTERY_END = first_of_next_month(date.fromisoformat(F["eia860m_vintage"]))
T12M = F["t12m"]["label"]                                     # "12 months to Sep 2026"
QSTART, QEND = date.fromisoformat(F["quarter"]["start"]), date.fromisoformat(F["quarter"]["end"])
Q = f"{QSTART:%b}–{QEND:%b %Y}"                               # "Jul–Sep 2026"
Q_PRIOR = f"{QSTART:%b}–{QEND:%b} {QSTART.year - 1}"
N_RANKED = F["roster"]["n"]
N_REPORTING = F["n_bas_complete"]

# ---------------------------------------------------------------- palette (../palettes/<name>.json)
# Every colour comes from one palette file, so a palette swaps with one argument. A palette
# may re-tint the fuels; otherwise they keep the EIA / Electricity Maps conventions.
P = json.load(open(Path(__file__).parent / "palettes" / f"{PALETTE}.json", encoding="utf-8"))
NAVY, ACCENT, ON_NAVY = P["masthead"], P["accent"], P["on_masthead"]
GROUND, TILE, BORDER, RULE, GRID, REFC = P["ground"], P["tile"], P["border"], P["rule"], P["grid"], P["ref"]
INK, SEC, HEAD_BG = P["ink"], P["sec"], P["head_bg"]
THIS, PRIOR, SECOND = P["this"], P["prior"], P["second"]     # this period; prior period (a tint); a second series
UP, DOWN, TOTAL = P["up"], P["down"], P["total"]              # a bridge's steps and totals
CELLBAR = P["cellbar"]
STATUS = {k: tuple(v) for k, v in P["status"].items()}        # text, pill, row tint, fill
STATUS["neutral"] = (SEC, HEAD_BG, None, None)
FUEL = {
    "Solar": "#FFC702", "Wind": "#1A9E8C", "Gas": "#D4A56E", "Coal": "#84511D", "Nuclear": "#B8475A",
    "Hydro": "#3B9DD6", "Battery": "#8E5BD8", "Battery charging": "#C6ADEC", "Net imports": "#A9B8D0",
    "Exports": "#DDE3EC", "Other": "#C0BAB4", "Coal, nuclear, hydro": "#84511D",
}
FUEL.update(P.get("fuel", {}))                                 # a palette may re-tint the fuels
REGION = P["region"]
REF = {"style": "dashed", "color": REFC, "width": 1}           # threshold and reference lines


def kpi_delta(chart: str, up: str, down: str) -> str:
    """A trend KPI's delta as coloured text with ▲▼, coloured by what the change means: Superset marks
    the card .positive or .negative by the comparison's sign (BigNumberWithTrendline/transformProps.ts:227-232)."""
    rules = []
    for sign, status, arrow in (("positive", up, "▲"), ("negative", down, "▼")):
        rules.append(
            f'[data-test-chart-name="{chart}"] .superset-legacy-chart-big-number.{sign} .subheader-line:not(.subtitle-line) '
            f'{{{{ color: {STATUS[status][0]} !important; }}}}\n'
            f'[data-test-chart-name="{chart}"] .superset-legacy-chart-big-number.{sign} .subheader-line:not(.subtitle-line)::before '
            f'{{{{ content: "{arrow} "; }}}}')
    return "\n".join(rules)


KPI_DELTAS = [  # chart name, colour when it rises, colour when it falls
    ("Lower-48 demand", "neutral", "neutral"),
    ("Lower-48 evening ramp", "watch", "good"),          # a steeper ramp is less headroom
    ("Batteries installed", "good", "act"),
    ("Batteries operating", "good", "act"),
    ("Evening ramp, median day", "watch", "good"),
    ("Evening ramp, bad day", "watch", "good"),
    ("Baseload", "watch", "neutral"),                    # rising baseload: less headroom
]

CSS = ("""
/* Sundown. Structural CSS only: Superset's markdown sanitizer drops class and style. Rules on
   elements Superset paints itself say !important, or Superset's own equal-specificity rule wins. */
/* ground and tiles */
.dashboard-component-tabs,
.dashboard-component-tabs .dashboard-component-tabs-content {{ background-color: {GROUND} !important; }}
.dashboard-component-tabs {{ box-shadow: none !important; }}
.dashboard .dashboard-component-tabs {{ padding-left: 0 !important; }}
.dashboard-component-chart-holder {{ border: 1px solid {BORDER}; border-radius: 2px; }}
.background--white {{ border: 1px solid {BORDER}; border-radius: 2px; }}
.background--white .dashboard-component-chart-holder {{ border: 0; }}
.dashboard-component-chart-holder .header-controls {{ opacity: 0; transition: opacity .15s; }}
.dashboard-component-chart-holder:hover .header-controls {{ opacity: 1; }}
/* masthead: a navy band, white wordmark */
.background--white:has(.dashboard-markdown h2) {{ background-color: {NAVY} !important; border-color: {NAVY} !important; }}
.background--white:has(.dashboard-markdown h2) .dashboard-component-chart-holder {{ background-color: {NAVY} !important; }}
.dashboard-markdown h2 {{ font-size: 26px; font-weight: 700; letter-spacing: -.01em; margin: 0; color: #FFFFFF; }}
.dashboard-markdown h2 + p {{ font-size: 13.5px; color: {ON_NAVY}; margin: 4px 0 0; }}
.dashboard-markdown h2 + p code {{ background: rgba(255,255,255,.12); color: #FFFFFF; }}
[data-test-chart-name="Data through"] {{ text-align: right; position: relative; }}
[data-test-chart-name="Data through"] .header-title {{ font-size: 11.5px !important; font-weight: 500 !important;
  color: {ON_NAVY} !important; justify-content: flex-end; transform: translateX(16px); }}
[data-test-chart-name="Data through"] .header-title a {{ color: {ON_NAVY} !important; }}
[data-test-chart-name="Data through"] .superset-legacy-chart-big-number {{ align-items: flex-end !important; }}
[data-test-chart-name="Data through"] .text-container {{ align-items: flex-end !important; }}
[data-test-chart-name="Data through"] .header-controls {{ position: absolute; right: 0; }}
/* filter bar: 'More filters' as an outline button, not the theme's filled primary tint */
[data-test="dropdown-container-btn"] {{ background: transparent !important; border: 1px solid {RULE} !important;
  color: {SEC} !important; box-shadow: none !important; }}
[data-test="dropdown-container-btn"]:hover {{ border-color: {SEC} !important; color: {INK} !important; }}
[data-test="dropdown-container-btn"] .ant-badge-count {{ background: {HEAD_BG} !important; color: {SEC} !important; box-shadow: none !important; }}
/* tabs: inactive secondary, active ink on an accent underline */
.dashboard-component-tabs .ant-tabs-nav::before {{ border-bottom: 1px solid {RULE}; }}
.dashboard-component-tabs .anchor-link-container {{ display: none; }}
.dashboard-component-tabs .ant-tabs-tab {{ font-size: 14.5px; padding: 10px 0; color: {SEC}; }}
.dashboard-component-tabs .ant-tabs-tab-btn, .dashboard-component-tabs .dragdroppable-tab {{ padding: 0 !important; margin: 0 !important; }}
.dashboard-component-tabs .ant-tabs-tab-active .ant-tabs-tab-btn {{ font-weight: 600; color: {INK} !important; }}
.dashboard-component-tabs .ant-tabs-ink-bar {{ background: {ACCENT} !important; height: 3px !important; }}
.dashboard-component-tabs .ant-tabs-nav {{ margin-bottom: 14px !important; }}  /* clear of the KPI cards' accent edge */
/* section bands: a full-width band in the masthead colour, an accent left edge, white caps */
.dashboard-component-header {{ background: {NAVY} !important; border-left: 5px solid {ACCENT} !important;
  border-radius: 2px; padding: 0 14px !important; min-height: 34px; margin-top: 10px; display: flex !important;
  align-items: center !important; }}
.dashboard-component-header .editable-title, .dashboard-component-header .editable-title input {{ font-size: 13.5px !important;
  font-weight: 600 !important; color: #FFFFFF !important; letter-spacing: .07em !important; text-transform: uppercase; }}
.dashboard-component-header .anchor-link-container {{ display: none !important; }}
/* the one-line colour keys */
.dashboard-markdown:has(h5) .dashboard-component-chart-holder,
.dashboard-markdown:has(h6) .dashboard-component-chart-holder {{ background-color: transparent !important; border: 0 !important; }}
.dashboard-markdown h5 {{ font-size: 12px; font-weight: 400; color: {SEC}; margin: 0; }}
.dashboard-markdown h6 {{ font-size: 13px; font-weight: 500; color: {SEC}; margin: 0; }}
.dashboard-markdown h6 strong {{ color: {INK}; font-weight: 600; }}
.dashboard-markdown [itemprop]::before {{ content: ""; display: inline-block; width: 10px; height: 10px;
  border-radius: 2px; margin: 0 6px 0 2px; vertical-align: -1px; }}
.dashboard-markdown [itemprop="this"]::before {{ background: {THIS}; }}
.dashboard-markdown [itemprop="prior"]::before {{ background: {PRIOR}; }}
.dashboard-markdown [itemprop="solar"]::before {{ background: {SOLAR}; }}
.dashboard-markdown [itemprop="up"]::before {{ background: {UP}; }}
.dashboard-markdown [itemprop="down"]::before {{ background: {DOWN}; }}
.dashboard-markdown [itemprop="total"]::before {{ background: {TOTAL}; }}
.superset-chart-table .cell-bar {{ background-color: {CELLBAR} !important; }}
/* chart titles */
.header-title {{ font-size: 15px !important; font-weight: 600 !important; color: {INK}; }}
/* KPI cards: an accent top edge; the number in ink; the delta as coloured text with ▲▼, by meaning */
.dashboard-component-chart-holder:has([data-test-viz-type^="big_number"]):not(:has([data-test-chart-name="Data through"])) {{
  border-top: 3px solid {ACCENT} !important; }}
.header-line, .subheader-line, .superset-chart-table td {{ font-variant-numeric: tabular-nums; }}
[data-test-viz-type="big_number"] .header-line,
[data-test-viz-type="big_number_total"] .header-line {{ font-size: 36px !important; line-height: 1.1 !important;
  font-weight: 600; letter-spacing: -.015em; }}
[data-test-viz-type="big_number"] .header-line {{ color: {INK}; }}
[data-test-viz-type="big_number"] .subheader-line:not(.subtitle-line) {{ font-size: 13px !important; font-weight: 600;
  line-height: 1.4 !important; color: {SEC}; margin: 4px 0 2px; }}
[data-test-viz-type="big_number"] .subtitle-line,
[data-test-viz-type="big_number_total"] .subheader-line,
[data-test-viz-type="big_number_total"] .subtitle-line {{ font-size: 12.5px !important; color: {SEC}; line-height: 1.4 !important; }}
[data-test-viz-type="big_number_total"] .superset-legacy-chart-big-number {{ justify-content: flex-start !important; }}
[data-test-chart-name="Data through"] .header-line {{ font-size: 22px !important; color: #FFFFFF !important; }}
{KPI_DELTA_CSS}
/* tables: readable by standard (body 14 px on 36 px rows, header 13 px semibold), no vertical rules */
.superset-chart-table table thead th {{ background: {HEAD_BG} !important; color: {INK}; font-weight: 600; font-size: 13px !important; }}
.superset-chart-table table th, .superset-chart-table table td {{ border-left: 0 !important; border-right: 0 !important; }}
.superset-chart-table table tbody td {{ border-top: 1px solid {GRID}; font-size: 14px !important; height: 36px; color: {INK}; }}
""".format(GROUND=GROUND, BORDER=BORDER, NAVY=NAVY, ON_NAVY=ON_NAVY, RULE=RULE, SEC=SEC, INK=INK, ACCENT=ACCENT,
           THIS=THIS, PRIOR=PRIOR, GRID=GRID, HEAD_BG=HEAD_BG, UP=UP, DOWN=DOWN, TOTAL=TOTAL, CELLBAR=CELLBAR,
           SOLAR=FUEL["Solar"], KPI_DELTA_CSS="{KPI_DELTA_CSS}")
       .replace("{{", "{").replace("}}", "}")
       .replace("{KPI_DELTA_CSS}", "\n".join(kpi_delta(c, u, d) for c, u, d in KPI_DELTAS).replace("{{", "{").replace("}}", "}"))
       ).strip()

charts: list[dict] = []
BA_SCOPED: list[str] = []


def ds(table: str) -> dict:
    return {"database": DB, "schema": SCHEMA, "table": table}


def add(chart: dict, width: int, height: float, ba: bool = False) -> str:
    chart["width"], chart["height"] = width, height
    charts.append(chart)
    if ba:
        BA_SCOPED.append(chart["name"])
    return chart["name"]


def md(text: str, width: int = 12, height: float = 1.4) -> dict:
    return {"markdown": text, "width": width, "height": height}


def header(text: str) -> dict:
    return {"header": text, "size": "medium"}


def key(*items: tuple[str, str]) -> list:
    """A one-line colour key, the only text a section carries besides its header."""
    return [md("###### " + " · ".join(f'<span itemprop="{k}"></span>**{label}**' for k, label in items), 12, 1.4)]


def white(*items) -> dict:
    return {"row": list(items), "background": "white"}


def center(*labels: str) -> dict:
    """Every column centred: the author's choice for the larger tables."""
    return {label: "center" for label in labels}


def eq(column: str, value) -> dict:
    return {"column": column, "op": "==", "value": value}


def pct(x: float) -> str:
    return f"{x * 100:+.0f}%"


def gw(mw: float) -> str:
    return f"{mw / 1000:.1f} GW"


def status_rules(code_col: str, target: str, mapping: dict[int, str], paint: str = "cell", tint: str = "pill") -> list:
    """Colour rules keyed on a numeric status code: each code paints the target column (or the row)."""
    idx = {"text": 0, "pill": 1, "row": 2}[tint if paint == "cell" else "text"]
    out = []
    for code, status in mapping.items():
        colour = STATUS[status][idx]
        if colour is None:
            continue
        rule = {"metric": code_col, "operator": "=", "target": code, "color": colour, "apply_to": target}
        if paint == "text":
            rule["paint"] = "text"
        out.append(rule)
    return out


# ---------------------------------------------------------------- header and footer (every tab)
freshness = add({
    "name": "Data through", "type": "big_number_total", "dataset": ds("freshness"),
    "metric": "MAX(data_through_date)", "date_format": "%a, %-d %b %Y",
    "description": "The latest day with all 24 hours of demand reported by every grid operator in EIA-930.",
}, 3, 2.4)
masthead = md("## SUNDOWN\nData-centre siting across the US power grid", 9, 2.4)
footer = md(
    "##### Source: U.S. Energy Information Administration, EIA-930 Hourly Electric Grid Monitor and "
    f"EIA-860M ({F['eia860m_label']}), public domain · ◇ marks a proxy (Glossary) · "
    "Built with Chartwright", 12, 1.4)


# ---------------------------------------------------------------- Overview
def has_duck(h: dict) -> bool:
    """One rule for "duck" everywhere: solar a tenth of midday demand, a sunset window, a year-earlier ramp."""
    return ((h["sunset_share"] or 0) >= 0.5 and (h["solar_mid"] or 0) >= 0.10
            and h["sunset_p50_ly_mw"] is not None)


def duck_title(h: dict) -> str:
    if not has_duck(h):
        cur, ly = h["rise3h_p50_mw"], h["rise3h_p50_ly_mw"]
        return f"{h['label']} · {gw(cur)} · {pct(cur / ly - 1)} · 3-hour rise"
    cur, ly = h["sunset_p50_mw"], h["sunset_p50_ly_mw"]
    return f"{h['label']} · {gw(cur)} · {pct(cur / ly - 1)}"


SITING_FILTERS = [eq("in_roster", True), {"column": "baseload_change_mw", "op": ">=", "value": 150}]


def overview() -> dict:
    l48_month = [eq("period_type", "month")]
    demand = add({
        "name": "Lower-48 demand", "type": "big_number_trend", "dataset": ds("l48_period"),
        "display_name": "Demand, TWh",
        "filters": l48_month, "metric": "SQL(SUM(demand_twh_all_bas)) AS Demand, TWh",
        "time_column": "period_start", "time_grain": "P1M", "rolling_type": "sum", "rolling_periods": 12,
        "compare_lag": 12, "compare_suffix": "vs prior 12 months",
        "subtitle": f"Lower 48 · 12 months to {MONTH}", "number_format": ",.0f",
        "time_range": f"{months_back(END, 48)} : {END}", "trend_color": THIS, "y_axis_truncate": True,
        "description": "Electricity demand summed over every Lower-48 grid operator, trailing 12 months.",
    }, 3, 4.8)
    ramp = add({
        "name": "Lower-48 evening ramp", "type": "big_number_trend", "dataset": ds("l48_period"),
        "display_name": "Evening ramp, GW",
        "filters": l48_month, "metric": "SQL(MAX(coincident_sunset_ramp_p50_mw) / 1000) AS Evening ramp, GW",
        "time_column": "period_start", "time_grain": "P1M", "compare_lag": 12, "compare_suffix": "vs a year earlier",
        "subtitle": f"Lower 48 together · median day · {MONTH}",
        "number_format": ",.1f", "time_range": f"{months_back(END, 36)} : {END}", "trend_color": THIS,
        "y_axis_truncate": True,
        "description": "The largest 3-hour rise in Lower-48 net load (demand minus solar and wind) after the "
                       "solar peak, on the median day of the month. Rising means less headroom.",
    }, 3, 4.8)
    added = add({
        "name": "Baseload added", "type": "big_number_total", "dataset": ds("siting_ba"),
        "display_name": "◇ Baseload added, GW",
        "filters": [eq("in_roster", True), eq("baseload_night_agrees", True)],
        "metric": "SQL(SUM(baseload_change_mw) / 1000) AS Baseload added, GW",
        "subtitle": f"Sum of {F['baseload_agree']['n']} operators · {T12M}",
        "number_format": "+,.1f",
        "conditional_formatting": [{"operator": ">", "target": 0, "color": STATUS["watch"][0]}],
        "description": "Change in the 5th percentile of hourly demand (flat load) over 12 months, summed over "
                       "operators whose night hours agree. Amber: added load means less headroom.",
    }, 3, 4.8)
    batteries = add({
        "name": "Batteries installed", "type": "big_number_trend", "dataset": ds("battery_capacity_month"),
        "display_name": "Batteries, GW",
        "filters": [{"column": "region_name", "op": "IS NOT NULL"}],
        "metric": "SQL(SUM(operating_battery_mw) / 1000) AS Batteries, GW",
        "time_column": "month_start", "time_grain": "P1M", "compare_lag": 12, "compare_suffix": "vs a year earlier",
        "subtitle": f"Operating · EIA-860M, {F['eia860m_label']}", "number_format": ",.1f",
        "time_range": f"{months_back(BATTERY_END, 36)} : {BATTERY_END}", "trend_color": THIS, "y_axis_truncate": True,
        "description": "Operating battery nameplate capacity, Lower 48, from EIA-860M.",
    }, 3, 4.8)

    ducks = []
    for h in F["hero"]:
        ducks.append(add({
            "name": f"Shape of the day: {h['label']}", "type": "mixed", "dataset": ds("profile_ba"),
            "display_name": duck_title(h),
            "filters": [eq("ba", h["ba"]), eq("period_type", "quarter"), eq("is_latest", True)],
            "x_column": "hour_label",
            "a": {"metrics": ["SQL(MAX(solar_pct_peak_demand)) AS Solar"], "kind": "area", "opacity": 0.85,
                  "axis": "primary"},
            "b": {"metrics": [f"SQL(MAX(net_load_pct_peak_demand_ly)) AS Net load, {Q_PRIOR}",
                              f"SQL(MAX(net_load_pct_peak_demand)) AS Net load, {Q}"],
                  "kind": "line", "axis": "primary"},
            "y_axis_min": 0, "y_axis_max": 100, "number_format": ",.0f", "show_legend": False,
            "x_axis_title": "Hour of day", "y_axis_title": "% of daily peak",
            "description": "Net load (demand minus solar and wind) as a % of each day's peak demand, by hour, "
                           "averaged over the quarter. The title gives the median evening ramp and its change; "
                           "where solar is under a tenth of midday demand, the 3-hour rise.",
        }, 3, 6))

    siting = add({
        "name": "Siting screen", "type": "table", "dataset": ds("siting_ba"),
        "display_name": f"Operators that added 150 MW+ of baseload · {T12M}",
        "filters": SITING_FILTERS,
        "columns": ["ba_label", "region_name", "row_status", "baseload_status", "baseload_change_mw",
                    "baseload_growth_arrow", "baseload_growth_2y_arrow", "baseload_idx2019",
                    "ramp_p90_share_of_p95_demand", "cover_ratio", "npe10_duration_p90_h", "closure_display",
                    "row_status_code", "baseload_status_code", "cover_band", "hours_band", "closure_band"],
        "hidden": ["row_status_code", "baseload_status_code", "cover_band", "hours_band", "closure_band"],
        "sort_by": "baseload_change_mw", "row_limit": 14, "cell_bars": False,  # row tints hide bars on 6.1
        "column_headers": {
            "ba_label": "Operator", "region_name": "Region", "row_status": "Status",
            "baseload_status": "◇ Baseload", "baseload_change_mw": "◇ Baseload +MW",
            "baseload_growth_arrow": "1 yr", "baseload_growth_2y_arrow": "2 yr", "baseload_idx2019": "vs 2019",
            "ramp_p90_share_of_p95_demand": "Rise ÷ peak", "cover_ratio": "◇ Battery ÷ ramp",
            "npe10_duration_p90_h": "◇ Hours to cut 10%", "closure_display": "Closure",
        },
        "number_formats": {"baseload_change_mw": "+,.0f", "baseload_idx2019": ",.0f",
                           "ramp_p90_share_of_p95_demand": ".0%", "cover_ratio": ".2f",
                           "npe10_duration_p90_h": ".1f", "closure_display": "+.1%"},
        "column_widths": {"ba_label": 90, "region_name": 110, "row_status": 80, "baseload_status": 120},
        "column_align": center("ba_label", "region_name", "row_status", "baseload_status", "baseload_change_mw",
                               "baseload_growth_arrow", "baseload_growth_2y_arrow", "baseload_idx2019",
                               "ramp_p90_share_of_p95_demand", "cover_ratio", "npe10_duration_p90_h", "closure_display"),
        "conditional_formatting": (
            status_rules("row_status_code", "row", {1: "good", 2: "watch", 3: "act"}, paint="cell", tint="row")
            + status_rules("row_status_code", "row_status", {1: "good", 2: "watch", 3: "act"}, paint="text")
            + status_rules("baseload_status_code", "baseload_status", {3: "proven", 2: "watch", 1: "good"}, paint="text")
            + status_rules("baseload_status_code", "baseload_growth_arrow", {3: "proven", 2: "watch"}, paint="text")
            + status_rules("cover_band", "cover_ratio", {1: "good", 2: "watch", 3: "act"}, paint="text")
            + status_rules("hours_band", "npe10_duration_p90_h", {1: "good", 2: "watch", 3: "act"}, paint="text")
            + status_rules("closure_band", "closure_display", {3: "act"}, paint="text")
        ),
        "description": "Status: Act when cutting the peak 10% needs over 10 hours of storage or the data "
                       "doesn't reconcile (closure outside ±2%); Watch when it needs 4–10 hours, batteries cover "
                       "under half the bad-day ramp, or baseload rose beyond weather (proven); Good otherwise.",
    }, 12, 13.6)

    return {"title": "Overview", "rows": [
        [demand, ramp, added, batteries],
        header("Siting screen"),
        [siting],
        header("Shape of the day"),
        key(("this", Q), ("prior", Q_PRIOR), ("solar", "Solar")),
        list(ducks[:4]),
        list(ducks[4:]),
    ]}


# ---------------------------------------------------------------- Evening ramp (one grid operator)
def evening_ramp() -> dict:
    quarter = [eq("period_type", "quarter")]
    t12 = {"filters": [eq("period_type", "t12m")], "time_column": "period_end", "time_grain": "P1M",
           "time_range": f"{months_back(END, 36)} : {END}", "trend_color": THIS, "y_axis_truncate": True}
    r1 = add({
        "name": "Evening ramp, median day", "type": "big_number_trend", "dataset": ds("ba_period"),
        "display_name": "Evening ramp, GW", "filters": quarter,
        "metric": "SQL(MAX(sunset_ramp_p50_cmp_mw) / 1000) AS GW",
        "time_column": "period_start", "time_grain": "P3M", "compare_lag": 4, "compare_suffix": f"vs {Q_PRIOR}",
        "subtitle": f"Median day · {Q}", "number_format": ",.1f",
        "time_range": f"{months_back(END, 36)} : {END}", "trend_color": THIS, "y_axis_truncate": True,
        "description": "The largest 3-hour rise in net load after the solar peak, median day of the quarter. "
                       "Compared only where 9 days in 10 had a duck in both years.",
    }, 3, 4.8, ba=True)
    r2 = add({
        "name": "Evening ramp, bad day", "type": "big_number_trend", "dataset": ds("ba_period"),
        "display_name": "Bad-day ramp, GW", "metric": "SQL(MAX(sunset_ramp_p90_cmp_mw) / 1000) AS GW",
        "compare_lag": 12, "compare_suffix": "vs prior 12 months",
        "subtitle": f"1 day in 10 · {T12M}", "number_format": ",.1f", **t12,
        "description": "The evening ramp on 1 day in 10 (P90) over 12 months: what storage is sized to.",
    }, 3, 4.8, ba=True)
    r3 = add({
        "name": "Hours to cut the peak", "type": "big_number_trend", "dataset": ds("ba_period"),
        "display_name": "◇ Hours to cut 10%", "metric": "SQL(MAX(npe10_duration_p90_h)) AS Hours",
        "subtitle": f"Of storage · 1 day in 10 · {T12M}", "number_format": ",.1f", **t12,
        "description": "Hours of storage to cut the daily net-load peak by 10% on 1 day in 10. Within 4 h a "
                       "4-hour battery covers it; over 10 h needs long-duration storage.",
    }, 3, 4.8, ba=True)
    r4 = add({
        "name": "Baseload", "type": "big_number_trend", "dataset": ds("ba_period"), "display_name": "◇ Baseload, GW",
        "filters": [eq("period_type", "t12m")], "metric": "SQL(MAX(demand_p5_mw) / 1000) AS GW",
        "time_column": "period_end", "time_grain": "P1M", "compare_lag": 12, "compare_suffix": "vs prior 12 months",
        "subtitle": f"5th percentile of demand · {T12M}", "number_format": ",.1f",
        "time_range": f"{months_back(END, 36)} : {END}", "trend_color": THIS, "y_axis_truncate": True,
        "description": "The 5th percentile of hourly demand, a proxy for flat load.",
    }, 3, 4.8, ba=True)
    heat = add({
        "name": "Net load by hour and month", "type": "heatmap", "dataset": ds("profile_ba"),
        "display_name": "Net load by hour and month, MW · last 24 months",
        "filters": [eq("period_type", "month"), {"column": "period_start", "op": ">=", "value": str(START24)}],
        "x_column": "period_key", "y_column": "hour_axis", "metric": "SQL(MAX(net_load_mw)) AS Net load, MW",
        "x_order": "a_to_z", "color_scheme": P["heatmap"],
        "x_label_every": 3, "y_label_every": 6, "left_margin": 40,
        "description": "Mean net load by hour of day and month. Darker is more to serve; the evening ramp is "
                       "the climb from the midday trough to the evening peak.",
    }, 7, 8, ba=True)
    box = add({
        "name": "Daily evening ramps by month", "type": "box_plot", "dataset": ds("daily_ba"),
        "display_name": f"Daily evening ramp, GW · {T12M}",
        "filters": [{"column": "data_date", "op": ">=", "value": str(START12)},
                    {"column": "data_date", "op": "<=", "value": MONTH_END},
                    {"column": "sunset_ramp_mw", "op": "IS NOT NULL"}, eq("sunset_month_ok", True)],
        "metrics": ["SQL(MAX(sunset_ramp_mw) / 1000) AS Evening ramp, GW"],
        "distribute_across": ["data_date"], "groupby": ["month_key"], "whiskers": [10, 90],
        "row_limit": 1000, "number_format": ",.0f", "x_axis_title": "Month", "y_axis_title": "GW",
        "description": "Every day's evening ramp, one box per month: the box is the middle half, the whiskers "
                       "the 10th and 90th percentile days. Storage is sized to the upper whisker.",
    }, 5, 8, ba=True)
    bridge = add({
        "name": "Evening ramp bridge", "type": "waterfall", "dataset": ds("v_sunset_bridge"),
        "display_name": f"What moved the mean day's evening ramp, GW · {Q_PRIOR} to {Q}",
        "filters": [eq("is_latest", True)], "x_column": "step", "metric": "SUM(gw)",
        "opening": F["quarter"]["bridge_open"], "steps": ["Demand", "Solar", "Wind"],
        "closing": F["quarter"]["bridge_close"],
        "increase_color": UP, "decrease_color": DOWN, "total_color": TOTAL,  # 3:1 on white, as a graphic needs
        "x_axis_title": "Change by source", "y_axis_title": "GW",
        "increase_label": "Steepens the ramp", "decrease_label": "Eases the ramp",
        "show_value": True, "number_format": ",.1f", "show_legend": False,
        "description": "The change in the mean day's evening ramp split exactly into demand, solar and wind: each "
                       "step is how much that source's swing across the day's 3-hour window changed.",
    }, 7, 11.6, ba=True)
    movers = add({
        "name": "Evening ramp by operator", "type": "table", "dataset": ds("operator_ramp"),
        "display_name": f"Ten largest evening ramps, GW · {Q}",
        "filters": [eq("has_duck", 1)],
        "columns": ["ba_label", "sunset_ramp_p50_gw", "sunset_ramp_p50_arrow", "sunset_ramp_p90_gw",
                    "sunset_ramp_p50_sign"],
        "hidden": ["sunset_ramp_p50_sign"],
        "sort_by": "sunset_ramp_p50_gw", "row_limit": 10, "page_length": 0, "search_box": False,
        "cell_bars": ["sunset_ramp_p90_gw"], "color_by_sign": False,
        "column_headers": {"ba_label": "Operator", "sunset_ramp_p50_gw": "Median day",
                           "sunset_ramp_p50_arrow": f"vs {Q_PRIOR}", "sunset_ramp_p90_gw": "1 day in 10"},
        "number_formats": {"sunset_ramp_p50_gw": ",.1f", "sunset_ramp_p90_gw": ",.1f"},
        "column_align": center("ba_label", "sunset_ramp_p50_gw", "sunset_ramp_p50_arrow", "sunset_ramp_p90_gw"),
        "conditional_formatting": status_rules("sunset_ramp_p50_sign", "sunset_ramp_p50_arrow",
                                               {1: "watch", -1: "good"}, paint="text"),
        "description": "Operators with a duck (solar a tenth of midday demand): the evening ramp on the median "
                       "day and on 1 day in 10. A rising ramp is amber, a falling one green.",
    }, 5, 11.6)
    crit = F["z12_crit_evening"]
    signal = add({
        "name": "Is the change real", "type": "mixed", "dataset": ds("signal_month"),
        "display_name": "Evening ramp: each month's change ÷ weather noise",
        "filters": [eq("metric", "sunset_ramp_p50"), {"column": "month_start", "op": ">=", "value": str(START24)},
                    {"column": "z_noise", "op": "IS NOT NULL"}],
        "x_column": "month_start", "time_grain": "P1M", "x_label_format": "%b %y",
        "a": {"metrics": ["SQL(MAX(CASE WHEN outside_noise_band THEN z_noise END)) AS Outside the band",
                          "SQL(MAX(CASE WHEN NOT outside_noise_band THEN z_noise END)) AS Inside the band"],
              "kind": "bar", "axis": "primary", "show_value": True},  # one bar a month: the theme overlaps them
        "b": {"metrics": ["SQL(MAX(CASE WHEN n_z_12m = 12 THEN z_mean_12m END)) AS 12-month average"],
              "kind": "line", "axis": "primary"},
        "annotations": [{"name": "12-month test", "value": round(crit, 2), **REF}],
        "number_format": ",.1~f", "show_legend": True, "x_axis_title": "Month", "y_axis_title": "Change ÷ weather noise",
        "description": "Each month's year-on-year change divided by this operator's own weather noise for that "
                       "month. Outside the band three months running, or a 12-month average above the dashed "
                       "line, counts as real. Tested only with four reference years of a duck.",
    }, 7, 8, ba=True)
    verdicts = add({
        "name": "Is it real, by operator", "type": "table", "dataset": ds("signal_status"),
        "display_name": f"Verdict by operator · {T12M}",
        "filters": [{"sql": "baseload_code >= 2 OR rise3h_code >= 2 OR evening_code >= 2"}],
        "columns": ["ba_label", "baseload_verdict", "rise3h_verdict", "evening_verdict",
                    "baseload_code", "rise3h_code", "evening_code"],
        "hidden": ["baseload_code", "rise3h_code", "evening_code"],
        "sort_by": "baseload_code", "row_limit": F["signal_flagged_n"], "page_length": 0, "search_box": False,
        "column_headers": {"ba_label": "Operator", "baseload_verdict": "◇ Baseload",
                           "rise3h_verdict": "3-hour rise", "evening_verdict": "Evening ramp"},
        "column_align": center("ba_label", "baseload_verdict", "rise3h_verdict", "evening_verdict"),
        "conditional_formatting": (
            status_rules("baseload_code", "baseload_verdict", {3: "proven", 2: "watch"}, tint="pill")
            + status_rules("rise3h_code", "rise3h_verdict", {3: "proven", 2: "watch"}, tint="pill")
            + status_rules("evening_code", "evening_verdict", {3: "proven", 2: "watch"}, tint="pill")),
        "description": "Proven: the 12-month test passed (beyond what weather produces 1 time in 100). Watch: "
                       "outside the weather band three months running. Every operator with a proven or "
                       "watched rise on any test.",
    }, 5, 16.3)
    hist = add({
        "name": "Evening ramp since 2018", "type": "timeseries_line", "dataset": ds("ba_period"),
        "display_name": "Evening ramp, GW · median and bad day, by month since Jul 2018",
        "filters": [eq("period_type", "month")],
        "metrics": ["SQL(MAX(sunset_ramp_p50_mw) / 1000) AS Median day",
                    "SQL(MAX(sunset_ramp_p90_mw) / 1000) AS Bad day (P90)"],
        "time_column": "period_start", "time_grain": "P1M", "x_label_format": "%Y", "number_format": ",.0f",
        "y_axis_min": 0, "legend_position": "bottom", "time_range": f"2018-07-01 : {END}",
        "x_axis_title": "Month", "y_axis_title": "GW",
        "description": "Months when solar was under 5% of peak demand have no evening ramp and are left blank.",
    }, 7, 8, ba=True)
    return {"title": "Evening ramp", "tabs": [
        {"title": "Shape and drivers", "rows": [[r1, r2, r3, r4], header("By hour and by day"), [heat, box],
                                                header(f"What moved, {Q_PRIOR} to {Q}"),
                                                key(("up", "Steepens the ramp"), ("down", "Eases it"), ("total", "Mean day")),
                                                [bridge, movers]]},
        {"title": "Is it real", "sketch": ["HHHHHHHHHHHH",
                                           "SSSSSSSVVVVV", "SSSSSSSVVVVV", "SSSSSSSVVVVV", "SSSSSSSVVVVV",
                                           "LLLLLLLVVVVV", "LLLLLLLVVVVV", "LLLLLLLVVVVV", "LLLLLLLVVVVV", "LLLLLLLVVVVV"],
         "legend": {"H": {"header": "Change against weather"}, "S": signal, "V": verdicts, "L": hist}},
    ]}


# ---------------------------------------------------------------- Load growth
def growth() -> dict:
    added = add({
        "name": "Baseload and peak added by grid operator", "type": "bar", "dataset": ds("siting_ba"),
        "display_name": f"◇ Baseload and peak added, MW · {T12M}",
        "filters": SITING_FILTERS, "x_column": "ba_label",
        "metrics": ["SQL(MAX(baseload_change_mw)) AS Baseload added", "SQL(MAX(peak_change_mw)) AS Peak added"],
        "orientation": "horizontal", "row_limit": 13, "number_format": ",.0f", "legend_position": "bottom",
        "y_axis_min": axis_floor(F["peak_change_min_mw"], 500), "show_value": True,
        "x_axis_title": "Grid operator", "y_axis_title": "MW",
        "description": "Baseload is the 5th percentile of hourly demand, peak the 95th. Where they rise together "
                       "the new load is flat; peak also moves with summer weather.",
    }, 7, 12)
    index = add({
        "name": "Baseload against 2019", "type": "bar", "dataset": ds("siting_ba"),
        "display_name": f"◇ Baseload, 2019 = 100 · {T12M}",
        "filters": SITING_FILTERS, "x_column": "ba_label", "groupby": "baseload_status",
        "metrics": ["SQL(MAX(baseload_idx2019)) AS Baseload index"], "stack": True, "sort_by": "total",
        "annotations": [{"name": "2019 = 100", "value": 100, **REF}],
        "orientation": "horizontal", "number_format": ",.0f", "legend_position": "bottom",
        "show_value": True, "x_axis_title": "Grid operator", "y_axis_title": "Index, 2019 = 100",
        "description": "Each operator's baseload against its own 2019, coloured by whether its rise passes the "
                       "12-month test.",
    }, 5, 12)
    zones = add({
        "name": "Load-zone baseload since 2019", "type": "timeseries_line", "dataset": ds("subregion_period"),
        "display_name": "PJM load zones: ◇ baseload, 2019 = 100 · trailing 12 months",
        "filters": [eq("period_type", "t12m"), {"column": "subregion", "op": "IN", "value": ["DOM", "AEP"]}],
        "metrics": ["SQL(MAX(demand_p5_idx2019)) AS Baseload index"], "groupby": "subregion",
        "time_column": "period_end", "time_grain": "P1M", "x_label_format": "%Y", "number_format": ",.0f",
        "y_axis_min": 80, "y_axis_max": math.ceil((F["zones_idx_max"] + 10) / 20) * 20,
        "time_range": f"2020-12-01 : {END}", "legend_position": "bottom",
        "x_axis_title": "Month", "y_axis_title": "Index, 2019 = 100",
        "annotations": [{"name": "2019 = 100", "value": 100, **REF}],
        "description": "Dominion's Virginia zone (DOM) and American Electric Power's (AEP): lowest hours at "
                       "night, so their 5th percentile is flat load.",
    }, 12, 8)
    return {"title": "Load growth", "rows": [header("Baseload and peak added"), [added, index],
                                             header("Data-centre zones in PJM"), [zones]]}


# ---------------------------------------------------------------- Storage
def storage() -> dict:
    operating = add({
        "name": "Batteries operating", "type": "big_number_trend", "dataset": ds("battery_capacity_month"),
        "display_name": "Batteries operating, GW",
        "filters": [{"column": "region_name", "op": "IS NOT NULL"}],
        "metric": "SQL(SUM(operating_battery_mw) / 1000) AS GW",
        "time_column": "month_start", "time_grain": "P1M", "compare_lag": 12, "compare_suffix": "vs a year earlier",
        "subtitle": f"Lower 48 · EIA-860M, {F['eia860m_label']}", "number_format": ",.1f",
        "time_range": f"{months_back(BATTERY_END, 36)} : {BATTERY_END}", "trend_color": THIS, "y_axis_truncate": True,
    }, 4, 4.8)
    covered = add({
        "name": "Operators covered", "type": "big_number_total", "dataset": ds("siting_ba"),
        "display_name": "Operators whose batteries cover the ramp",
        "filters": [eq("in_roster", True), eq("cover_band", 1)], "metric": "COUNT(*)",
        "subtitle": f"Battery MW ≥ the bad-day ramp · of {N_RANKED}", "number_format": ",.0f",
        "conditional_formatting": [{"operator": ">", "target": 0, "color": STATUS["good"][0]}],
    }, 4, 4.8)
    within4 = add({
        "name": "Operators within 4 hours", "type": "big_number_total", "dataset": ds("siting_ba"),
        "display_name": "Operators a 4-hour battery covers",
        "filters": [eq("in_roster", True), eq("hours_band", 1)], "metric": "COUNT(*)",
        "subtitle": f"Cut the peak 10% in 4 h or less · of {N_RANKED}", "number_format": ",.0f",
        "conditional_formatting": [{"operator": ">", "target": 0, "color": STATUS["good"][0]}],
    }, 4, 4.8)
    installed = add({
        "name": "Batteries installed by region", "type": "timeseries_area", "dataset": ds("battery_capacity_month"),
        "display_name": f"Batteries operating by region, GW · EIA-860M, {F['eia860m_label']}",
        "filters": [{"column": "region_group", "op": "IS NOT NULL"}],
        "metrics": ["SQL(SUM(operating_battery_mw) / 1000) AS GW"], "groupby": "region_group",
        "time_column": "month_start", "time_grain": "P1M", "x_label_format": "%Y", "number_format": ",.0f",
        "stack": True, "opacity": 0.9, "time_range": f"2020-01-01 : {BATTERY_END}", "legend_position": "bottom",
        "x_axis_title": "Month", "y_axis_title": "GW",
    }, 12, 8)
    cover = add({
        "name": "Battery cover of the bad-day ramp", "type": "bar", "dataset": ds("siting_ba"),
        "display_name": f"◇ Battery MW ÷ bad-day evening ramp · {T12M}",
        "filters": [eq("in_roster", True), {"column": "sunset_ramp_p90_mw", "op": ">=", "value": 1000}],
        "x_column": "ba_label", "groupby": "cover_band_label", "stack": True, "sort_by": "total",
        "metrics": ["SQL(MAX(cover_ratio)) AS Battery ÷ ramp"],
        "orientation": "horizontal", "number_format": ",.2f", "legend_position": "bottom", "x_label_every": True,
        "show_value": True, "x_axis_title": "Grid operator", "y_axis_title": "Battery MW ÷ bad-day ramp",
        "annotations": [{"name": "Covers the ramp (1.0)", "value": 1, **REF},
                        {"name": "Half (0.5)", "value": 0.5, **REF}],
        "description": "Operating battery nameplate MW over the bad-day (P90) evening ramp, for operators whose "
                       "bad-day ramp is 1 GW or more. Nameplate isn't accredited capacity.",
    }, 6, 13)
    hours = add({
        "name": "Hours of storage to cut the peak", "type": "bar", "dataset": ds("siting_ba"),
        "display_name": f"◇ Hours of storage to cut the peak 10% · 1 day in 10 · {T12M}",
        "filters": [eq("in_roster", True), {"column": "peak_p95_mw", "op": ">=", "value": 5000}],
        "x_column": "ba_label", "groupby": "hours_band_label", "stack": True, "sort_by": "total",
        "metrics": ["SQL(MAX(npe10_duration_p90_h)) AS Hours"],
        "orientation": "horizontal", "number_format": ",.1f", "legend_position": "bottom", "x_label_every": True,
        "show_value": True, "x_axis_title": "Grid operator", "y_axis_title": "Hours of storage",
        "annotations": [{"name": "4-hour battery", "value": 4, **REF},
                        {"name": "Long duration (10 h)", "value": 10, **REF}],
        "description": "The energy above 90% of the day's peak net load divided by that 10%, on 1 day in 10: "
                       "perfect foresight, no losses. Operators with a peak over 5 GW.",
    }, 6, 13)
    return {"title": "Storage", "rows": [[operating, covered, within4], header("Batteries by region"), [installed],
                                         header("Where storage covers the ramp"), [cover, hours]]}


# ---------------------------------------------------------------- Supply (one grid operator)
def supply() -> dict:
    serve = add({
        "name": "What serves 13:00 and 20:00", "type": "bar", "dataset": ds("fuel_profile_ba"),
        "display_name": f"What supplies 13:00 and 20:00, MW · {Q}",
        "filters": [eq("is_latest", True), eq("closure_ok", True),
                    {"column": "hour_label", "op": "IN", "value": ["13:00", "20:00"]},
                    {"column": "fuel_group", "op": "IS NOT NULL"}],
        "x_column": "hour_label", "metrics": ["SQL(SUM(mean_mw)) AS MW"], "groupby": "fuel_group", "stack": True,
        "category_sort": "asc", "x_label_every": True, "number_format": ",.0f", "legend_position": "bottom",
        "legend_type": "plain", "show_value": True, "only_total": True,
        "x_axis_title": "Hour of day", "y_axis_title": "MW",
        "description": "Mean supply by fuel at 13:00, when solar peaks, and 20:00, after it sets. Below zero is "
                       "where part of it goes, exports and battery charging, so each bar nets to demand. Months "
                       "whose generation is more than 5% off demand are left out.",
    }, 6, 9, ba=True)
    battery = add({
        "name": "Battery output by hour", "type": "mixed", "dataset": ds("profile_ba"),
        "display_name": f"Battery discharge by hour, MW · {Q} vs {Q_PRIOR}",
        "filters": [eq("period_type", "quarter"), eq("is_latest", True)], "x_column": "hour_label",
        "a": {"metrics": [f"SQL(MAX(battery_discharge_ly_mw)) AS Battery, {Q_PRIOR}"], "kind": "line",
              "axis": "primary"},
        "b": {"metrics": [f"SQL(MAX(battery_discharge_mw)) AS Battery, {Q}"], "kind": "line", "axis": "primary"},
        "number_format": ",.0f", "show_legend": True, "x_axis_title": "Hour of day", "y_axis_title": "MW",
        "description": "Discharge only: EIA-930 reports some operators' batteries net of charging and others as "
                       "discharge only (ERCOT switched in Dec 2025).",
    }, 6, 9, ba=True)
    latest = [eq("is_latest", True), eq("closure_ok", True)]

    def share(fuel, hour):
        return (f"SQL(SUM(CASE WHEN fuel_group = '{fuel}' AND hour_label = '{hour}' THEN mean_mw END) / "
                f"NULLIF(SUM(CASE WHEN hour_label = '{hour}' AND mean_mw > 0 THEN mean_mw END), 0)) "
                f"AS {fuel} at {hour}")
    k_solar = add({"name": "Solar at 13:00", "type": "big_number_total", "dataset": ds("fuel_profile_ba"),
                   "display_name": "Solar at 13:00, share", "filters": latest, "metric": share("Solar", "13:00"),
                   "number_format": ".0%", "subtitle": f"Of supply at that hour · {Q}"}, 3, 4.6, ba=True)
    k_gas = add({"name": "Gas at 20:00", "type": "big_number_total", "dataset": ds("fuel_profile_ba"),
                 "display_name": "Gas at 20:00, share", "filters": latest, "metric": share("Gas", "20:00"),
                 "number_format": ".0%", "subtitle": f"Of supply at that hour · {Q}"}, 3, 4.6, ba=True)
    k_battery = add({"name": "Battery at 20:00", "type": "big_number_total", "dataset": ds("profile_ba"),
                     "display_name": "Battery at 20:00, MW",
                     "filters": [eq("period_type", "quarter"), eq("is_latest", True), eq("hour_label", "20")],
                     "metric": "SQL(MAX(battery_discharge_mw)) AS Battery at 20:00",
                     "number_format": ",.0f", "subtitle": f"Mean discharge, where reported · {Q}"},
                    3, 4.6, ba=True)
    closure = add({
        "name": "Balance closure", "type": "big_number_total", "dataset": ds("ba_period"),
        "display_name": "Balance closure",
        "filters": [eq("period_type", "quarter"), eq("is_latest", True)],
        # unrounded: Superset never colours a value of exactly 0 (BigNumberViz.tsx:216 at 6.1.0)
        "metric": "SQL(MAX(closure)) AS Closure",
        "number_format": ".1%", "subtitle": f"{Q} · ±2% guardrail",
        "conditional_formatting": [
            {"operator": "<", "target": -0.02, "color": STATUS["act"][0]},
            {"operator": ">", "target": 0.02, "color": STATUS["act"][0]},
            {"operator": "between", "target_left": -0.02, "target_right": 0.02, "color": STATUS["good"][0]}],
        "description": "(Net generation − interchange − demand) ÷ demand. Green within ±2%; red outside, where "
                       "fuel shares are approximate. Beyond ±5% the fuel mix is left blank.",
    }, 3, 4.6, ba=True)
    mix = add({
        "name": "Generation mix since 2018", "type": "timeseries_area", "dataset": ds("fuel_ba_period"),
        "display_name": "Generation mix, share · since Jul 2018",
        "filters": [eq("period_type", "month"), eq("closure_ok", True),
                    {"column": "fuel_group", "op": "IN",
                     "value": ["Solar", "Wind", "Gas", "Coal, nuclear, hydro", "Other"]}],
        "metrics": ["SQL(SUM(twh)) AS TWh"], "groupby": "fuel_group", "contribution": "row", "stack": True,
        "opacity": 0.9, "time_column": "period_start", "time_grain": "P1M", "x_label_format": "%Y",
        "number_format": ".0%", "y_axis_min": 0, "y_axis_max": 1, "time_range": f"2018-07-01 : {END}",
        "legend_position": "bottom", "x_axis_title": "Month", "y_axis_title": "Share of generation",
    }, 12, 8, ba=True)
    return {"title": "Supply", "rows": [[k_solar, k_gas, k_battery, closure], header("Solar peak and after sunset"),
                                        [serve, battery], header("Generation mix since 2018"), [mix]]}


# ---------------------------------------------------------------- About
def about() -> dict:
    glossary = add({
        "name": "Glossary", "type": "table", "dataset": ds("glossary"),
        "display_name": "Glossary · every measure on the dashboard",
        "columns": ["measure", "definition", "formula", "threshold", "source", "sort_order"],
        "hidden": ["sort_order"], "sort_by": "sort_order", "sort_ascending": True,
        "row_limit": F.get("glossary_rows", 40), "page_length": 0, "search_box": False,
        "column_headers": {"measure": "Measure", "definition": "Definition", "formula": "Formula",
                           "threshold": "Status bands", "source": "Source"},
        "column_widths": {"measure": 200, "definition": 420, "formula": 300, "threshold": 260, "source": 90},
        "column_align": {"measure": "left", "definition": "left", "formula": "left", "threshold": "left",
                         "source": "left"},
    }, 12, 52)
    dq_rows = F["dq"]["n_with_closure"] - F["dq"]["n_within_2pct_all_months"]
    dq = add({
        "name": "Data quality by grid operator", "type": "table", "dataset": ds("dq_ba"),
        "display_name": "Operators with a month past ±2% closure · latest 12 months",
        "filters": [{"column": "closure_abs", "op": "IS NOT NULL"}],
        "columns": ["ba_label", "months_beyond_2pct", "closure_worst_monthly", "closure_t12m", "n_months",
                    "demand_imputed_share", "reports_batteries"],
        "sort_by": "months_beyond_2pct", "row_limit": dq_rows, "page_length": 0, "search_box": False,
        "column_headers": {"ba_label": "Grid operator", "months_beyond_2pct": "Months past ±2%",
                           "closure_t12m": "12-month closure", "closure_worst_monthly": "Worst month",
                           "n_months": "Months", "demand_imputed_share": "Demand imputed",
                           "reports_batteries": "Reports batteries"},
        "number_formats": {"closure_t12m": "+.1%", "closure_worst_monthly": "+.1%", "demand_imputed_share": ".1%"},
        "column_align": center("ba_label", "months_beyond_2pct", "closure_worst_monthly", "closure_t12m", "n_months",
                               "demand_imputed_share", "reports_batteries"),
        "conditional_formatting": [
            {"metric": "months_beyond_2pct", "operator": ">", "target": 5, "color": STATUS["act"][2], "apply_to": "row"},
            {"metric": "months_beyond_2pct", "operator": "between", "target_left": 0, "target_right": 6,
             "color": STATUS["watch"][2], "apply_to": "row"},
            {"metric": "months_beyond_2pct", "operator": ">", "target": 5, "color": STATUS["act"][0],
             "apply_to": "months_beyond_2pct", "paint": "text"},
        ],
        "description": "Balance closure is (net generation − interchange − demand) ÷ demand and should sit near "
                       "zero. Red rows: six or more months past ±2%; amber: one to five.",
    }, 7, 15)
    caiso = add({
        "name": "CAISO balance closure", "type": "timeseries_line", "dataset": ds("dq_ba_month"),
        "display_name": f"Balance closure by month, % · CAISO and PJM · since {months_back(END, 33):%b %Y}",
        "filters": [{"column": "ba", "op": "IN", "value": ["CISO", "PJM"]}], "metrics": ["SQL(MAX(closure)) AS Closure"],
        "groupby": "ba_label",
        "time_column": "month_start", "time_grain": "P1M", "x_label_format": "%b %y", "number_format": ".0%",
        "time_range": f"{months_back(END, 33)} : {END}", "markers": False, "legend_position": "bottom",
        "x_axis_title": "Month", "y_axis_title": "Closure, % of demand",
        "annotations": [{"name": "+2%", "value": 0.02, **REF}, {"name": "−2%", "value": -0.02, **REF}],
        "description": "CAISO's fuel mix stops reconciling from Dec 2025 (its ramp uses only demand, solar and "
                       "wind and is unaffected); PJM runs above +2% each summer.",
    }, 5, 15)
    return {"title": "About", "rows": [header("Data quality"), [dq, caiso], header("Glossary"), [glossary]]}


tabs = [overview(), evening_ramp(), growth(), storage(), supply(), about()]

IGNORE = ([f"size.min-width@Shape of the day: {h['label']}" for h in F["hero"]]
          + ["chart.ordinal-order@Net load by hour and month", "layout.tab-balance", "filters.time-picker",
             "layout.fold-budget@tab-About-row-1"])  # one scrolling page, chosen over sub-tabs

STATUS_LABELS = {
    # a proven rise in a deeper amber than a rise, both 3:1 on white
    "Proven rise": P["proven_rise"], "Rising": P["rising"], "Flat or falling": STATUS["good"][3],
    "Covers the ramp": STATUS["good"][3], "Half or more": STATUS["watch"][3], "Under half": STATUS["act"][3],
    "Within 4 h": STATUS["good"][3], "4–10 h": STATUS["watch"][3], "Over 10 h": STATUS["act"][3],
}

spec = {
    "spec_version": "1",
    "design": {"ignore": IGNORE},
    "dashboard": {
        "title": "Sundown",
        "slug": "sundown",
        "theme": "Sundown",
        "css": CSS,
        "cross_filters": True,
        "filter_bar_orientation": "horizontal",
        "label_colors": {
            "Solar": FUEL["Solar"], f"Net load, {Q}": THIS, f"Net load, {Q_PRIOR}": PRIOR,
            "Median day": THIS, "Bad day (P90)": PRIOR,
            "Outside the band": STATUS["watch"][3], "Inside the band": PRIOR, "12-month average": INK,
            "Baseload added": THIS, "Peak added": PRIOR,
            f"Battery, {Q}": FUEL["Battery"], f"Battery, {Q_PRIOR}": P.get("battery_prior", "#CCB5ED"),
            **FUEL, **REGION, **STATUS_LABELS,
            "DOM": THIS, "AEP": SECOND, "CAISO": THIS, "PJM": SECOND, "Closure": THIS,
            "2019 = 100": REFC, "12-month test": REFC,
            # the box plot colours each month's box by its label: pin them all to one
            **{f"{y}-{m:02d}": PRIOR for y in range(END.year - 2, END.year + 1) for m in range(1, 13)},
        },
    },
    "charts": charts,
    "filters": [
        {"type": "select", "name": "Grid operator", "dataset": ds("ba_period"), "column": "ba_label",
         "multi": False, "default": ["ERCOT"], "required": True, "charts": BA_SCOPED},
    ],
    "layout": {
        "header": [{"row": [masthead, freshness], "background": "white"}],
        "tabs": tabs,
        "footer": [[footer]],
    },
}
json.dump(spec, sys.stdout, indent=2, ensure_ascii=False)
sys.stdout.write("\n")
