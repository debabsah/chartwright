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

from ..spec import DEFAULT_TIME_GRAIN
from .model import AXIS_TYPES, KPI_TYPES, TIMESERIES_TYPES, Finding, RuleContext, rule

# -- size: minimum readable geometry ------------------------------------------


@rule("size.min-width", "warn", "below 3/12 width a chart is unreadable; KPIs need 2/12")
def min_width(ctx: RuleContext):
    for c in ctx.spec.charts:
        if c.type in ("pie", "heatmap"):
            continue  # they own stricter geometry rules
        w = ctx.width(c.name)
        if c.type in KPI_TYPES:
            if w < 2:
                yield Finding(
                    "size.min-width", "warn", c.name, ctx.where(c.name),
                    f"big number at {w}/12: the value gets cropped; give it >= 2/12",
                )
        elif w < 3:
            yield Finding(
                "size.min-width", "error", c.name, ctx.where(c.name),
                f"{c.type} at {w}/12 is unreadable at any height; 3/12 is the hard floor",
            )
        elif w < 4:
            yield Finding(
                "size.min-width", "warn", c.name, ctx.where(c.name),
                f"{c.type} at {w}/12 is cramped; 4/12 or wider reads comfortably",
            )


@rule("size.axis-min-height", "warn", "axis charts below the audience minimum height flatten and drop labels", fixable=True)
def axis_min_height(ctx: RuleContext):
    for c in ctx.spec.charts:
        if c.type not in AXIS_TYPES or c.type == "heatmap":
            continue  # heatmap-geometry owns the heatmap's stricter floor
        if c.type == "bar" and c.orientation == "horizontal" and c.row_limit is not None:
            continue  # hbar-window's per-bar formula binds instead
        h = ctx.height(c.name)
        if h < ctx.params.min_axis_height:
            yield Finding(
                "size.axis-min-height", "warn", c.name, ctx.where(c.name),
                f"{c.type} at {h:g} units renders flattened with axis labels dropped; "
                f"needs >= {ctx.params.min_axis_height}",
                fix=ctx.fix_height(c, ctx.params.min_axis_height),
                height_driven=True,
            )


@rule("size.kpi-height", "warn", "big numbers read best at 2-6 units", fixable=True)
def kpi_height(ctx: RuleContext):
    for c in ctx.spec.charts:
        if c.type not in KPI_TYPES:
            continue
        h = ctx.height(c.name)
        if not 2 <= h <= 6:
            target = min(6, max(2, math.ceil(max(
                ctx.params.kpi_height, ctx.params.recommended_heights.get(c.type, 0)))))
            yield Finding(
                "size.kpi-height", "warn", c.name, ctx.where(c.name),
                f"big number at {h:g} units ({'starved' if h < 2 else 'wastes hero space'}); "
                f"2-6 reads best",
                fix={"chart": c.name, "set": {"height": target}},
                height_driven=True,
            )


@rule("size.pie-geometry", "warn", "pies need >= 5/12 width and 8 height or the ring shrinks and the legend crowds", fixable=True)
def pie_geometry(ctx: RuleContext):
    # Width and height are SEPARATE findings: a human-polished (fractional)
    # height silences only the height complaint; absorb can't write widths.
    for c in ctx.spec.charts:
        if c.type != "pie":
            continue
        w, h = ctx.width(c.name), ctx.height(c.name)
        if w < 5:
            hint = ("widen its sketch run to >= 5 of 12 cells" if ctx.is_sketch(c.name)
                    else "widen it to >= 5 of 12")
            yield Finding(
                "size.pie-geometry", "warn", c.name, ctx.where(c.name),
                f"pie squeezed: width {w}/12 ({hint}); ring shrinks and legend crowds",
            )
        if h < 8:
            yield Finding(
                "size.pie-geometry", "warn", c.name, ctx.where(c.name),
                f"pie squeezed: height {h:g} < 8; ring shrinks and legend crowds",
                fix=ctx.fix_height(c, 8), height_driven=True,
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
        if w < min_w:
            yield Finding(
                "size.heatmap-geometry", "warn", c.name, ctx.where(c.name),
                f"heatmap cramped: width {w}/12 < {min_w}"
                + (" (x has > 12 columns)" if min_w == 7 else ""),
            )
        if h < 6:
            yield Finding(
                "size.heatmap-geometry", "warn", c.name, ctx.where(c.name),
                f"heatmap cramped: height {h:g} below the readable floor of 6 (8 recommended)",
                fix=ctx.fix_height(c, 8), height_driven=True,
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
                height_driven=True,
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
            fix=fix, height_driven=True,
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
            # ceil: a fractional band max is a human-polished neighbor, and a
            # tool fix must never mint the fractional human-polish signature.
            target = min(100, math.ceil(top))
            for n, h in heights.items():
                if h < top:
                    yield Finding(
                        "size.row-harmony", "warn", n, ctx.where_band(si, bi),
                        f"{h:g} units beside a {top:g}-unit neighbor leaves a ragged hole; "
                        f"equalize to {target:g}",
                        fix={"chart": n, "set": {"height": target}},
                        height_driven=True,
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
    # Reasoning is per horizontal SLOT (a BandItem), not per flattened chart:
    # a vertical stack of KPIs beside a hero chart is the canonical sidebar
    # pattern the sketch grammar exists to express, not a mixed band.
    p = ctx.params
    for si, sec in enumerate(ctx.sections):
        for bi, band in enumerate(sec.bands):
            kpis = [n for n in band.chart_names if ctx.charts[n].type in KPI_TYPES]
            others = [n for n in band.chart_names if ctx.charts[n].type not in KPI_TYPES]
            if kpis and others:
                # Offenders are bare full-height KPI slots beside detail slots;
                # KPI-only stacks (sidebars) are exempt.
                bare = [i.charts[0] for i in band.items
                        if len(i.charts) == 1 and ctx.charts[i.charts[0]].type in KPI_TYPES]
                mixed_stacks = [i for i in band.items if len(i.charts) > 1
                                and any(ctx.charts[n].type in KPI_TYPES for n in i.charts)
                                and any(ctx.charts[n].type not in KPI_TYPES for n in i.charts)]
                if bare:
                    yield Finding(
                        "layout.kpi-band", "warn", None, ctx.where_band(si, bi),
                        f"big numbers {bare} sit full-height beside detail charts; give KPIs "
                        f"their own band, or stack them in a column beside the tall chart",
                    )
                elif mixed_stacks:
                    yield Finding(
                        "layout.kpi-band", "warn", None, ctx.where_band(si, bi),
                        "a stack mixes big numbers with detail charts; keep stacks homogeneous",
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


@rule("layout.row-density", "warn", "too many axis charts side by side starves each of width")
def row_density(ctx: RuleContext):
    # Horizontal SLOTS, not flattened charts: a stack of three charts occupies
    # one slot's width, so it counts once (the user already split vertically).
    for si, sec in enumerate(ctx.sections):
        for bi, band in enumerate(sec.bands):
            slots = [i for i in band.items
                     if any(ctx.charts[n].type in AXIS_TYPES for n in i.charts)]
            if len(slots) <= ctx.params.max_row_charts:
                continue
            sev = "error" if any(i.width < 3 for i in slots) else "warn"
            yield Finding(
                "layout.row-density", sev, None, ctx.where_band(si, bi),
                f"{len(slots)} side-by-side slots with axis charts "
                f"(max {ctx.params.max_row_charts}); "
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
            + f": more than {p.pie_max_slices} slices is unreadable. Prefer a horizontal "
              f"bar (rankings don't claim to be a whole); if it must stay a pie, know that "
              f"a row_limit redefines the whole -- the shown slices read as 100% -- so the "
              f"title must disclose the truncation (e.g. 'top {p.pie_max_slices} ...')",
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
        if not cx or not cy:
            continue
        # A saturated side (count == cap+1) hides the true product: a 6,000x10
        # grid reads as 31x10 = 310 and would pass. Re-probe the saturated
        # side at the cap the OTHER side implies before deciding.
        if cx * cy <= 400 and (cx > 30 or cy > 30):
            if cx > 30:
                cx = ctx.prober.count_up_to(ds, c.x_column, math.ceil(400 / cy)) or cx
            if cy > 30:
                cy = ctx.prober.count_up_to(ds, c.y_column, math.ceil(400 / cx)) or cy
        if cx * cy > 400:
            at_least = "at least " if cx > 30 or cy > 30 else "~"
            yield Finding(
                "chart.heatmap-grid", "warn", c.name, ctx.where(c.name),
                f"{at_least}{cx}x{cy} = {cx * cy}+ cells; filter or coarsen one axis "
                f"to stay under ~400",
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
        if c.type not in TIMESERIES_TYPES or not c.time_range:
            continue
        span = _span_days(c.time_range)
        # An omitted grain compiles to the P1D default -- the most common
        # LLM-authored shape is exactly the one that needs this rule.
        eff = c.time_grain or DEFAULT_TIME_GRAIN
        grain = _GRAIN_DAYS.get(eff)
        if span is None or grain is None:
            continue
        label = eff if c.time_grain else f"{eff} (the default when omitted)"
        points = span / grain
        if points < 2:
            yield Finding(
                "data.grain-vs-range", "warn", c.name, ctx.where(c.name),
                f"{c.time_range!r} at grain {label} yields ~{points:.1f} point(s); "
                f"a line needs a finer grain or a longer range",
            )
        elif points > 1000:
            yield Finding(
                "data.grain-vs-range", "warn", c.name, ctx.where(c.name),
                f"{c.time_range!r} at grain {label} yields ~{points:,.0f} points; "
                f"coarsen the grain",
            )


# -- narrative & filters: polish -------------------------------------------------

_MINOR_WORDS = {"a", "an", "the", "of", "by", "vs", "and", "or", "in", "on",
                "per", "for", "to", "with", "at", "as"}


def _case_class(name: str) -> str | None:
    words = re.findall(r"[A-Za-z][A-Za-z']*", name)
    # All-uppercase words are acronyms (AOV, SLA, YoY has mixed...): they say
    # nothing about the author's casing style, so they don't vote.
    significant = [w for w in words[1:]
                   if w.lower() not in _MINOR_WORDS and len(w) > 1 and not w.isupper()]
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
            # Strings only: booleans and numbers ("True", "42") are predicates,
            # not names a human would echo in a title.
            fragments += [v.strip("%") for v in values if isinstance(v, str)
                          and len(v.strip("%")) >= 3 and not v.replace(".", "").isdigit()]
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
