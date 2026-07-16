"""Render the Tier G design brief: what the LLM reads BEFORE authoring a spec.

Assembled from the packaged guideline files, the audience's parameter values,
a summary of what the critic will later enforce (so the author pre-complies
instead of iterating), and any house guidance from the overlay. Budget: the
brief lands in an LLM context window, so it stays compact by contract
(tests enforce a line ceiling).
"""

from __future__ import annotations

from importlib import resources

from .model import RULES
from .presets import Overlay, load_overlay, params_for


def _guideline(name: str) -> str:
    return (resources.files("chartwright.design") / "guidelines" / name).read_text(encoding="utf-8")


def render_brief(audience: str, overlay: Overlay | None = None) -> str:
    overlay = overlay if overlay is not None else load_overlay()
    p = params_for(audience, overlay)
    intent = {
        "executive": "one screen, few numbers, big; every extra chart costs attention",
        "analytical": "scrolling analysis; depth over fold, structure over density",
        "operational": "dense monitor view; everything visible, nothing decorative",
    }[audience]

    # ASCII only: Windows pipes default to cp1252; the brief must survive any console.
    lines = [
        f"# Design brief: {audience}",
        "",
        f"Intent: {intent}.",
        "",
        "## Budgets and sizes (1 height unit = 40 px; widths are twelfths of the page)",
        "",
        f"- Height budget per tab: {p.fold_units} units (~{p.fold_units * 40}px). Over budget -> tabs or prune.",
        f"- KPI band: {p.kpi_row_min}-{p.kpi_row_max} big numbers, {p.kpi_height} units tall, first band on the page.",
        f"- Axis charts (timeseries/bar/heatmap/histogram): >= {p.min_axis_height} units tall, 8 is the comfortable default.",
        f"- At most {p.max_row_charts} axis charts per row; below 3/12 width a chart is unreadable.",
        "- Pie/donut: >= 5/12 wide, >= 8 tall. Heatmap: >= 5/12 wide (7/12 when many columns), >= 6 tall.",
        f"- Vertical bars: <= {p.vbar_max_categories} categories, then flip horizontal. Pies: <= {p.pie_max_slices} slices.",
        f"- Timeseries: <= {p.series_max} grouped series. Tables: height should show >= {p.table_visible_ratio:.0%} of row_limit (~0.8 units/row).",
        "- Charts sharing a row share a height; Superset sizes the row to its tallest child.",
    ]
    if p.recommended_heights:
        rec = ", ".join(f"{t}: {h:g}" for t, h in sorted(p.recommended_heights.items()))
        lines.append(f"- Calibrated house heights (from real usage): {rec}.")

    exemplar = {
        "executive": [
            '"KKK MMM NNN QQQ",   K/M/N/Q: four KPIs, one band, above everything',
            '"LLLLLLLL SSSS",     L: the one trend that answers the question',
            '"LLLLLLLL SSSS",     S: its single most useful breakdown',
            '"LLLLLLLL SSSS",     (three lines at line: 2 = 6 units < the 22-unit budget)',
        ],
        "analytical": [
            '"KKKK MMMM NNNN",    KPI band first',
            '"LLLLLLLL SSSS",     L: trend (4-5 lines tall); S: breakdown stacked beside it',
            '"LLLLLLLL SSSS",',
            '"LLLLLLLL PPPP",     P: second breakdown completes the sidebar',
            '"TTTTTTTTTTTT",      T: the detail table, full width, below the fold is fine',
            '"TTTTTTTTTTTT",',
        ],
        "operational": [
            '"KKK MMM NNN QQQ",   dense KPI band (up to 8 fit)',
            '"LLLLLL SSSSSS",     two half-width monitors per band',
            '"LLLLLL SSSSSS",',
            '"AAAAAA BBBBBB",     everything visible, one screen, no scroll',
            '"AAAAAA BBBBBB",',
        ],
    }[audience]
    lines += ["", "## The canonical shape (a sketch to start from)", ""] + [f"  {l}" for l in exemplar]

    lines += [
        "",
        "## Defaults the compiler fills in (the critic flags the ones that matter)",
        "",
        "- Omitted height -> 8 units (KPIs 4, markdown 4). Omitted width -> the row splits evenly.",
        "- Omitted row_limit -> 10,000 (pie 100, table 1,000, funnel 10): set it deliberately on bar/pie/table/pivot.",
        "- Omitted time_grain -> P1D. Grains: PT1H P1D P1W P1M P3M P1Y. Points ~= range/grain; budget ~40 for bars, ~300 for lines.",
        "- number_format is d3: ',.0f' thousands, '.1%' percent, '.3s' SI units, '$,.2f' money. One measure, one format.",
        "- Native filter bar: select pickers (few, they query on load), a time_range WITH a default, numeric range sliders.",
    ]

    lines += ["", _guideline("chart-choice.md").strip(), "", _guideline("composition.md").strip()]

    lines += ["", "## What the critic enforces (`chartwright advise`)", ""]
    by_cat: dict[str, list[str]] = {}
    for r in RULES.values():
        by_cat.setdefault(r.id.split(".")[0], []).append(f"`{r.id}` {r.doc}")
    for cat in sorted(by_cat):
        lines.append(f"- **{cat}**: " + "; ".join(sorted(by_cat[cat])))
    lines += ["", "Suppress a deliberate exception in the spec: "
              '`"design": {"ignore": ["rule.id@Chart Name"]}` -- never dodge a finding by hand-tuning output.']

    if overlay.brief_extra.strip():
        lines += ["", "## House guidance", "", overlay.brief_extra.strip()]
    return "\n".join(lines) + "\n"
