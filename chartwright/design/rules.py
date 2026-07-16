"""The rulebook: every Tier L (lintable) design rule.

Grounded in BI practice (Few, Tufte, IBCS) and Superset rendering facts the
skill learned the hard way (axis charts flatten below ~6 units; vertical bars
drop labels past ~8 categories; pies crowd under 5/12 width). Each rule yields
Findings; thresholds come from the audience Params, never hard-coded branches
on audience names. Autofixes are presentation-only by principle: geometry and
orientation, never row limits, filters, or chart types.

Rule ids are a stable public surface (spec `design.ignore` keys on them).
"""

from __future__ import annotations

import json
import math
import re
from datetime import date

from .model import AXIS_TYPES, KPI_TYPES, TIMESERIES_TYPES, Finding, RuleContext, rule

# -- size: minimum readable geometry ------------------------------------------


@rule("size.axis-min-height", "warn", "axis charts below the audience minimum height flatten and drop labels", fixable=True)
def axis_min_height(ctx: RuleContext):
    for c in ctx.spec.charts:
        if c.type not in AXIS_TYPES:
            continue
        h = ctx.height(c.name)
        if h < ctx.params.min_axis_height:
            yield Finding(
                "size.axis-min-height", "warn", c.name, ctx.where(c.name),
                f"{c.type} at {h:g} units renders flattened with axis labels dropped; "
                f"needs >= {ctx.params.min_axis_height}",
                fix=ctx.fix_height(c, ctx.params.min_axis_height),
            )


@rule("size.kpi-height", "warn", "big numbers read best at 2-6 units", fixable=True)
def kpi_height(ctx: RuleContext):
    for c in ctx.spec.charts:
        if c.type not in KPI_TYPES:
            continue
        h = ctx.height(c.name)
        if not 2 <= h <= 6:
            target = min(6.0, max(2.0, ctx.params.kpi_height,
                                  ctx.params.recommended_heights.get(c.type, 0)))
            yield Finding(
                "size.kpi-height", "warn", c.name, ctx.where(c.name),
                f"big number at {h:g} units ({'starved' if h < 2 else 'wastes hero space'}); "
                f"2-6 reads best",
                fix={"chart": c.name, "set": {"height": target}},
            )


@rule("size.pie-geometry", "warn", "pies need >= 5/12 width and 8 height or the ring shrinks and the legend crowds", fixable=True)
def pie_geometry(ctx: RuleContext):
    for c in ctx.spec.charts:
        if c.type != "pie":
            continue
        w, h = ctx.width(c.name), ctx.height(c.name)
        if w >= 5 and h >= 8:
            continue
        parts = []
        if w < 5:
            hint = ("widen its sketch run to >= 5 of 12 cells" if ctx.is_sketch(c.name)
                    else "widen it to >= 5 of 12")
            parts.append(f"width {w}/12 ({hint})")
        if h < 8:
            parts.append(f"height {h:g} < 8")
        yield Finding(
            "size.pie-geometry", "warn", c.name, ctx.where(c.name),
            f"pie squeezed: {'; '.join(parts)}; ring shrinks and legend crowds",
            fix=ctx.fix_height(c, 8) if h < 8 else None,
        )


@rule("size.heatmap-geometry", "warn", "heatmaps need >= 5/12 width (7/12 with many columns) and 6 height", fixable=True)
def heatmap_geometry(ctx: RuleContext):
    for c in ctx.spec.charts:
        if c.type != "heatmap":
            continue
        w, h = ctx.width(c.name), ctx.height(c.name)
        min_w = 5
        if ctx.prober is not None and (ds := ctx.dataset_for(c)):
            if ctx.prober.more_than(ds, c.x_column, 12):
                min_w = 7
        if w >= min_w and h >= 6:
            continue
        parts = []
        if w < min_w:
            parts.append(f"width {w}/12 < {min_w}" + (" (x has > 12 columns)" if min_w == 7 else ""))
        if h < 6:
            parts.append(f"height {h:g} < 6")
        yield Finding(
            "size.heatmap-geometry", "warn", c.name, ctx.where(c.name),
            f"heatmap cramped: {'; '.join(parts)}",
            fix=ctx.fix_height(c, 8) if h < 6 else None,
        )


@rule("size.table-window", "warn", "a table's height should show a meaningful share of its row_limit")
def table_window(ctx: RuleContext):
    for c in ctx.spec.charts:
        if c.type != "table" or c.row_limit is None:
            continue
        h = ctx.height(c.name)
        visible = max(1, (h - 1) / 0.8)  # ~0.8 units per row after the header
        if visible < ctx.params.table_visible_ratio * c.row_limit:
            yield Finding(
                "size.table-window", "warn", c.name, ctx.where(c.name),
                f"table shows ~{visible:.0f} of {c.row_limit} rows at {h:g} units "
                f"(a scroll dungeon); raise height or lower row_limit",
            )


@rule("size.hbar-window", "warn", "horizontal bars need ~0.5 units of height per bar", fixable=True)
def hbar_window(ctx: RuleContext):
    for c in ctx.spec.charts:
        if c.type != "bar" or c.orientation != "horizontal" or c.row_limit is None:
            continue
        h = ctx.height(c.name)
        needed = c.row_limit * 0.5 + 2
        if h >= needed:
            continue
        fix = ctx.fix_height(c, math.ceil(needed)) if needed <= 20 else None
        yield Finding(
            "size.hbar-window", "warn", c.name, ctx.where(c.name),
            f"{c.row_limit} bars in {h:g} units squeezes each bar; needs ~{math.ceil(needed)}"
            + ("" if fix else f"; that exceeds a sane height, lower row_limit instead"),
            fix=fix,
        )


@rule("size.row-harmony", "warn", "charts sharing a row should share a height (Superset sizes the row to its tallest child)", fixable=True)
def row_harmony(ctx: RuleContext):
    for si, sec in enumerate(ctx.sections):
        if sec.mode != "rows":
            continue  # sketch rows draw their raggedness deliberately (dots)
        for bi, band in enumerate(sec.bands):
            names = [n for n in band.chart_names if ctx.charts[n].type not in KPI_TYPES]
            if len(names) < 2:
                continue
            heights = {n: ctx.height(n) for n in names}
            top = max(heights.values())
            for n, h in heights.items():
                if h < top:
                    yield Finding(
                        "size.row-harmony", "warn", n, ctx.where_band(si, bi),
                        f"{h:g} units beside a {top:g}-unit neighbor leaves a ragged hole; "
                        f"equalize to {top:g}",
                        fix={"chart": n, "set": {"height": top}},
                    )


# -- layout: composition -------------------------------------------------------


@rule("layout.kpi-first", "warn", "summary KPIs belong above detail charts (inverted pyramid)")
def kpi_first(ctx: RuleContext):
    for si, sec in enumerate(ctx.sections):
        first_detail = None
        for bi, band in enumerate(sec.bands):
            if any(ctx.charts[n].type not in KPI_TYPES for n in band.chart_names):
                first_detail = bi
                break
        if first_detail is None:
            continue
        late = [n for bi, band in enumerate(sec.bands) if bi > first_detail
                for n in band.chart_names if ctx.charts[n].type in KPI_TYPES]
        if late:
            yield Finding(
                "layout.kpi-first", "warn", None, ctx.where_band(si, first_detail),
                f"big numbers {late} sit below detail charts; lead with the summary band",
            )


@rule("layout.kpi-band", "warn", "KPIs get their own band, in readable numbers")
def kpi_band(ctx: RuleContext):
    p = ctx.params
    for si, sec in enumerate(ctx.sections):
        for bi, band in enumerate(sec.bands):
            kpis = [n for n in band.chart_names if ctx.charts[n].type in KPI_TYPES]
            others = [n for n in band.chart_names if ctx.charts[n].type not in KPI_TYPES]
            if kpis and others:
                yield Finding(
                    "layout.kpi-band", "warn", None, ctx.where_band(si, bi),
                    f"big numbers {kpis} share a row with detail charts {others}; "
                    f"give KPIs their own band",
                )
            elif kpis:
                has_md = any(i.is_markdown for i in band.items)
                if len(kpis) > p.kpi_row_max:
                    yield Finding(
                        "layout.kpi-band", "warn", None, ctx.where_band(si, bi),
                        f"{len(kpis)} KPIs in one band reads as noise; keep <= {p.kpi_row_max}",
                    )
                elif len(kpis) < p.kpi_row_min and not has_md:
                    yield Finding(
                        "layout.kpi-band", "warn", None, ctx.where_band(si, bi),
                        f"a band of {len(kpis)} KPI looks unfinished; aim for "
                        f"{p.kpi_row_min}-{p.kpi_row_max} or pair it with a markdown note",
                    )


@rule("layout.row-density", "warn", "too many axis charts in one row starves each of width")
def row_density(ctx: RuleContext):
    for si, sec in enumerate(ctx.sections):
        for bi, band in enumerate(sec.bands):
            axis = [(n, ctx.width(n)) for n in band.chart_names if ctx.charts[n].type in AXIS_TYPES]
            if len(axis) <= ctx.params.max_row_charts:
                continue
            sev = "error" if any(w < 3 for _, w in axis) else "warn"
            yield Finding(
                "layout.row-density", sev, None, ctx.where_band(si, bi),
                f"{len(axis)} axis charts in one row (max {ctx.params.max_row_charts}); "
                + ("some land under 3/12 wide, unreadable" if sev == "error"
                   else "split across rows"),
            )


@rule("layout.row-fill", "warn", "a row should fill the 12-column grid")
def row_fill(ctx: RuleContext):
    for si, sec in enumerate(ctx.sections):
        if sec.mode != "rows":
            continue  # sketches mark holes deliberately with '.'
        for bi, band in enumerate(sec.bands):
            total = sum(i.width for i in band.items)
            missing = 12 - total
            if missing <= 0:
                continue
            yield Finding(
                "layout.row-fill", "warn" if missing >= 3 else "info", None,
                ctx.where_band(si, bi),
                f"row widths sum to {total}/12, leaving a {missing}-column hole at the right; "
                f"widen a chart or add one",
            )


@rule("layout.fold-budget", "warn", "the dashboard should fit its audience's scroll budget")
def fold_budget(ctx: RuleContext):
    for si, sec in enumerate(ctx.sections):
        total = 0.0
        for bi, band in enumerate(sec.bands):
            total += band.height
            if total > ctx.params.fold_units:
                where = f"tab {sec.title!r}" if sec.title else "the dashboard"
                yield Finding(
                    "layout.fold-budget", "warn", None, ctx.where_band(si, bi),
                    f"{where} runs {total:g}+ units against a {ctx.params.audience} budget of "
                    f"{ctx.params.fold_units} (~{ctx.params.fold_units * 40}px); the budget runs "
                    f"out at row {bi}; move detail into tabs or prune",
                )
                break


@rule("layout.tab-balance", "info", "tabs should carry comparable weight")
def tab_balance(ctx: RuleContext):
    if len(ctx.sections) < 2:
        return
    counts = {s.title: sum(len(b.chart_names) for b in s.bands) for s in ctx.sections}
    lo, hi = min(counts.values()), max(counts.values())
    if hi >= 5 and hi > 4 * max(1, lo):
        thin = [t for t, n in counts.items() if n == lo]
        yield Finding(
            "layout.tab-balance", "info", None, "tabs",
            f"tab weight skews {hi}:{lo} ({counts}); rebalance or inline the thin tab(s) {thin}",
        )


@rule("layout.orphan-chart", "info", "a lone narrow chart in its own row looks unfinished")
def orphan_chart(ctx: RuleContext):
    for si, sec in enumerate(ctx.sections):
        if sec.mode != "rows":
            continue
        for bi, band in enumerate(sec.bands):
            if len(band.items) == 1 and band.items[0].charts and band.items[0].width < 8:
                yield Finding(
                    "layout.orphan-chart", "info", band.items[0].charts[0],
                    ctx.where_band(si, bi),
                    f"alone in its row at {band.items[0].width}/12; widen it to 12 or pair it",
                )


@rule("layout.section-headers", "info", "large flat dashboards need markdown signposts")
def section_headers(ctx: RuleContext):
    if len(ctx.sections) != 1 or ctx.sections[0].mode != "rows" or ctx.sections[0].title:
        return
    n = len(ctx.spec.charts)
    has_md = any(i.is_markdown for b in ctx.sections[0].bands for i in b.items)
    if n > 8 and not has_md:
        yield Finding(
            "layout.section-headers", "info", None, "layout",
            f"{n} charts with no markdown section headers; readers need signposts "
            f"(or split into tabs)",
        )


# -- chart: encoding choice ----------------------------------------------------


@rule("chart.vbar-categories", "warn", "vertical bars drop labels past ~8 categories; rank with horizontal bars", fixable=True)
def vbar_categories(ctx: RuleContext):
    p = ctx.params
    for c in ctx.spec.charts:
        if c.type != "bar" or c.orientation != "vertical":
            continue
        rl = c.row_limit
        if rl is not None and rl <= p.vbar_max_categories:
            continue
        if ctx.prober is not None and (ds := ctx.dataset_for(c)):
            if ctx.prober.more_than(ds, c.x_column, p.vbar_max_categories) is False:
                continue  # the data itself stays under the label limit
        fix = ({"chart": c.name, "set": {"orientation": "horizontal"}}
               if rl is not None and rl <= 15 else None)
        yield Finding(
            "chart.vbar-categories", "warn", c.name, ctx.where(c.name),
            (f"vertical bar with row_limit {rl}" if rl is not None
             else "vertical bar with no row_limit (defaults to 10,000)")
            + f": Superset drops category labels past ~{p.vbar_max_categories}; "
              "flip to horizontal and cap around 10",
            fix=fix,
        )


@rule("chart.pie-slices", "warn", "pies stop working past ~7 slices")
def pie_slices(ctx: RuleContext):
    p = ctx.params
    for c in ctx.spec.charts:
        if c.type != "pie":
            continue
        rl = c.row_limit
        if rl is not None and rl <= p.pie_max_slices:
            continue
        if ctx.prober is not None and (ds := ctx.dataset_for(c)):
            if ctx.prober.more_than(ds, c.groupby, p.pie_max_slices) is False:
                continue
        yield Finding(
            "chart.pie-slices", "warn", c.name, ctx.where(c.name),
            (f"pie with row_limit {rl}" if rl is not None
             else "pie with no row_limit (defaults to 100)")
            + f": more than {p.pie_max_slices} slices is unreadable; set row_limit "
              f"{p.pie_max_slices} or switch to a horizontal bar",
        )


@rule("chart.series-limit", "warn", "a timeseries with too many grouped series turns to spaghetti", data_aware=True)
def series_limit(ctx: RuleContext):
    if ctx.prober is None:
        return
    for c in ctx.spec.charts:
        if c.type not in TIMESERIES_TYPES or not c.groupby:
            continue
        ds = ctx.dataset_for(c)
        if ds is None:
            continue
        if ctx.prober.more_than(ds, c.groupby, ctx.params.series_max):
            yield Finding(
                "chart.series-limit", "warn", c.name, ctx.where(c.name),
                f"groupby {c.groupby!r} has more than {ctx.params.series_max} values: "
                f"a line per value is spaghetti; filter to the top few or use a coarser dimension",
            )


@rule("chart.metrics-per-bar", "warn", "many metrics per category read better as a table")
def metrics_per_bar(ctx: RuleContext):
    for c in ctx.spec.charts:
        if c.type == "bar" and len(c.metrics) >= 4:
            yield Finding(
                "chart.metrics-per-bar", "warn", c.name, ctx.where(c.name),
                f"{len(c.metrics)} metrics per category makes grouped bars unreadable; "
                f"a table or pivot answers this better",
            )


@rule("chart.temporal-type", "error", "a time axis must point at a temporal column", data_aware=True)
def temporal_type(ctx: RuleContext):
    for c in ctx.spec.charts:
        col = getattr(c, "time_column", None)
        if not col:
            continue
        ds = ctx.dataset_for(c)
        if ds is None:
            continue
        if ds.is_temporal(col) is False:
            yield Finding(
                "chart.temporal-type", "error", c.name, ctx.where(c.name),
                f"time_column {col!r} is not a temporal column on {ds.table!r}; "
                f"the chart will render broken or empty"
                + (f" (dataset main time column: {ds.main_dttm_col!r})" if ds.main_dttm_col else ""),
            )


@rule("chart.histogram-bins", "info", "histograms read best at 10-50 bins")
def histogram_bins(ctx: RuleContext):
    for c in ctx.spec.charts:
        if c.type == "histogram" and not 10 <= c.bins <= 50:
            yield Finding(
                "chart.histogram-bins", "info", c.name, ctx.where(c.name),
                f"{c.bins} bins ({'too coarse' if c.bins < 10 else 'too noisy'}); 20-30 suits most distributions",
            )


@rule("chart.treemap-depth", "warn", "treemaps past two grouping levels become unreadable nesting")
def treemap_depth(ctx: RuleContext):
    for c in ctx.spec.charts:
        if c.type == "treemap" and len(c.groupby) > 2:
            yield Finding(
                "chart.treemap-depth", "warn", c.name, ctx.where(c.name),
                f"{len(c.groupby)} nesting levels; keep to 2 or break the hierarchy into charts",
            )


@rule("chart.funnel-stages", "warn", "funnels need 3-8 ordered stages", data_aware=True)
def funnel_stages(ctx: RuleContext):
    if ctx.prober is None:
        return
    for c in ctx.spec.charts:
        if c.type != "funnel":
            continue
        ds = ctx.dataset_for(c)
        if ds is None:
            continue
        n = ctx.prober.count_up_to(ds, c.groupby, 8)
        if n is None:
            continue
        if n > 8 and (c.row_limit is None or c.row_limit > 8):
            yield Finding(
                "chart.funnel-stages", "warn", c.name, ctx.where(c.name),
                f"groupby {c.groupby!r} has more than 8 values; a funnel wants 3-8 ordered stages",
            )
        elif n < 3:
            yield Finding(
                "chart.funnel-stages", "warn", c.name, ctx.where(c.name),
                f"groupby {c.groupby!r} has only {n} value(s); a funnel wants 3-8 ordered stages",
            )


@rule("chart.heatmap-grid", "warn", "a heatmap past ~400 cells is unreadable at any size", data_aware=True)
def heatmap_grid(ctx: RuleContext):
    if ctx.prober is None:
        return
    for c in ctx.spec.charts:
        if c.type != "heatmap":
            continue
        ds = ctx.dataset_for(c)
        if ds is None:
            continue
        cx = ctx.prober.count_up_to(ds, c.x_column, 30)
        cy = ctx.prober.count_up_to(ds, c.y_column, 30)
        if cx and cy and cx * cy > 400:
            yield Finding(
                "chart.heatmap-grid", "warn", c.name, ctx.where(c.name),
                f"~{cx}x{cy} = {cx * cy}+ cells; filter or coarsen one axis to stay under ~400",
            )


@rule("chart.dupe", "info", "two charts answering the identical question is redundancy")
def chart_dupe(ctx: RuleContext):
    seen: dict[str, str] = {}
    for c in ctx.spec.charts:
        fp = json.dumps(c.model_dump(exclude={"name", "width", "height"}),
                        sort_keys=True, default=str)
        if fp in seen:
            yield Finding(
                "chart.dupe", "info", c.name, ctx.where(c.name),
                f"identical to {seen[fp]!r} (same dataset, type, metrics, dimensions, filters)",
            )
        else:
            seen[fp] = c.name


# -- data: query intent ---------------------------------------------------------


@rule("data.row-limit-intent", "info", "row limits doing design work should be deliberate, not defaults")
def row_limit_intent(ctx: RuleContext):
    for c in ctx.spec.charts:
        if c.type == "table" and c.row_limit is None:
            yield Finding(
                "data.row-limit-intent", "info", c.name, ctx.where(c.name),
                "table riding the default row_limit (1,000); set it deliberately",
            )
        elif c.type == "bar" and c.orientation == "horizontal" and c.row_limit is None:
            yield Finding(
                "data.row-limit-intent", "info", c.name, ctx.where(c.name),
                "horizontal bar with no row_limit (defaults to 10,000); a ranking wants ~10",
            )


_RANGE_DAYS = {"day": 1, "week": 7, "month": 30.4, "quarter": 91, "year": 365}
_GRAIN_DAYS = {"PT1H": 1 / 24, "P1D": 1, "P1W": 7, "P1M": 30.4, "P3M": 91, "P1Y": 365}


def _span_days(time_range: str) -> float | None:
    tr = time_range.strip().lower()
    m = re.fullmatch(r"last\s+(\d+)\s+(day|week|month|quarter|year)s?", tr)
    if m:
        return int(m.group(1)) * _RANGE_DAYS[m.group(2)]
    m = re.fullmatch(r"last\s+(day|week|month|quarter|year)", tr)
    if m:
        return _RANGE_DAYS[m.group(1)]
    if " : " in time_range:
        try:
            a, b = (date.fromisoformat(part.strip().split(" ")[0].split("T")[0])
                    for part in time_range.split(" : ", 1))
            return abs((b - a).days)
        except ValueError:
            return None
    return None


@rule("data.grain-vs-range", "warn", "the time grain should yield a sane number of points for the range")
def grain_vs_range(ctx: RuleContext):
    for c in ctx.spec.charts:
        if c.type not in TIMESERIES_TYPES or not c.time_range or not c.time_grain:
            continue
        span = _span_days(c.time_range)
        grain = _GRAIN_DAYS.get(c.time_grain)
        if span is None or grain is None:
            continue
        points = span / grain
        if points < 2:
            yield Finding(
                "data.grain-vs-range", "warn", c.name, ctx.where(c.name),
                f"{c.time_range!r} at grain {c.time_grain} yields ~{points:.1f} point(s); "
                f"a line needs a finer grain or a longer range",
            )
        elif points > 1000:
            yield Finding(
                "data.grain-vs-range", "warn", c.name, ctx.where(c.name),
                f"{c.time_range!r} at grain {c.time_grain} yields ~{points:,.0f} points; "
                f"coarsen the grain",
            )


# -- narrative & filters: polish -------------------------------------------------

_MINOR_WORDS = {"a", "an", "the", "of", "by", "vs", "and", "or", "in", "on",
                "per", "for", "to", "with", "at", "as"}


def _case_class(name: str) -> str | None:
    words = re.findall(r"[A-Za-z][A-Za-z']*", name)
    significant = [w for w in words[1:] if w.lower() not in _MINOR_WORDS and len(w) > 1]
    if not significant:
        return None
    if all(w[0].isupper() for w in significant):
        return "title"
    if all(w[0].islower() for w in significant):
        return "sentence"
    return None


@rule("narrative.title-style", "info", "chart titles should share one casing style")
def title_style(ctx: RuleContext):
    classes: dict[str, list[str]] = {"title": [], "sentence": []}
    for c in ctx.spec.charts:
        cls = _case_class(c.name)
        if cls:
            classes[cls].append(c.name)
    if classes["title"] and classes["sentence"]:
        minority = min(classes.values(), key=len)
        style = "Title Case" if minority is classes["title"] else "sentence case"
        yield Finding(
            "narrative.title-style", "info", None, "charts",
            f"mixed title casing; {minority} use {style} while the rest do not. "
            f"NOTE: renaming a chart changes its identity (uuid); align future names, "
            f"do not bulk-rename a live dashboard",
        )


@rule("narrative.big-number-format", "info", "hero numbers deserve a number format")
def big_number_format(ctx: RuleContext):
    for c in ctx.spec.charts:
        if c.type in KPI_TYPES and c.number_format is None:
            yield Finding(
                "narrative.big-number-format", "info", c.name, ctx.where(c.name),
                "no number_format: raw float precision on a hero number; ',.0f' or '.3s' read better",
            )


@rule("narrative.filtered-title", "info", "a filtered chart's title should say what it shows")
def filtered_title(ctx: RuleContext):
    for c in ctx.spec.charts:
        fragments = []
        for f in c.filters:
            if f.op not in ("==", "IN", "LIKE"):
                continue
            values = f.value if isinstance(f.value, list) else [f.value]
            fragments += [str(v).strip("%") for v in values
                          if len(str(v).strip("%")) >= 3 and not str(v).replace(".", "").isdigit()]
        if fragments and not any(frag.lower() in c.name.lower() for frag in fragments):
            yield Finding(
                "narrative.filtered-title", "info", c.name, ctx.where(c.name),
                f"filtered to {fragments} but the title doesn't say so; "
                f"name the scope so the chart says what it shows",
            )


@rule("filters.time-picker", "info", "time-based dashboards want a time range picker in the filter bar")
def time_picker(ctx: RuleContext):
    temporal = any(c.type in TIMESERIES_TYPES | {"big_number_trend"} for c in ctx.spec.charts)
    has_picker = any(f.type == "time_range" for f in ctx.spec.filters)
    if temporal and not has_picker:
        yield Finding(
            "filters.time-picker", "info", None, "filters",
            "timeseries charts but no time_range filter in the native bar; "
            "viewers will want to change the window",
        )
