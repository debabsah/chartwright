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

from ..spec import (
    DEFAULT_HEIGHT,
    DEFAULT_ROW_LIMIT,
    DEFAULT_TIME_GRAIN,
    SUPERSET_COLOR_SCHEMES,
    WATERFALL_DEFAULT_HEX,
    HeaderBlock,
    PIVOT_METRIC,
    _ColorSchemeMixin,
    grid_fit,
    grid_rows_visible,
    grid_units_for_rows,
    pivot_axes,
    pivot_frame,
    pivot_rows_from_counts,
    row_items,
    table_page,
    hex_to_rgb,
    parse_metric,
    set_value,
    table_header_units,
    without_superset_defaults,
)
from ..visible import contrast
from .markdown_fit import Fit, box_px, estimate
from .model import AXIS_TYPES, KPI_TYPES, TIMESERIES_TYPES, Finding, RuleContext, rule

# -- size: minimum readable geometry ------------------------------------------


@rule("size.min-width", "warn", "below 3/12 width a chart is unreadable; KPIs need 2/12",
      since="2", severities=("warn", "error"))
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


_PROBE_CAP = 60  # distinct values a grid probe counts before it saturates


def _row_dims(c) -> list[str]:
    """The dimensions a table's or pivot's body rows are keyed on: a pivot's row
    attributes less the metric names (spec.pivot_axes), an aggregate table's groupby."""
    if c.type == "pivot_table":
        return [a for a in pivot_axes(c)[0] if a != PIVOT_METRIC]
    return list(c.groupby or []) if not c.columns else []


def _counted(c, dims: list[str]) -> str:
    """What a grid's row count counts, for a finding: the dimensions, and the metrics
    when a pivot lays them out as rows."""
    what = " x ".join(repr(d) for d in dims)
    if c.type == "pivot_table" and len(c.metrics) > 1 and PIVOT_METRIC in pivot_axes(c)[0]:
        what = f"{what} x {len(c.metrics)} metrics" if what else f"{len(c.metrics)} metrics"
    return what


def _probed_rows(ctx: RuleContext, c) -> tuple[int, int, bool] | None:
    """(leaf rows, subtotal rows, exact) a table or pivot draws, from one bounded
    distinct count per row dimension (advise --profile; the probes are cached). None
    offline, when a probe fails, or for a chart a per-column count can't size (a raw
    table, a grid with no row dimension). Exact with one dimension that didn't saturate
    the probe; otherwise a lower bound. size.grid-fit reports it; size.table-window and
    size.pivot-window leave a chart it sizes to that rule, so one advise run never
    gives a chart two height targets."""
    dims = _row_dims(c) if c.type in ("table", "pivot_table") else []
    if ctx.prober is None or not dims or (ds := ctx.dataset_for(c)) is None:
        return None
    # row_limit caps the records, so the distinct row keys too (a pivot's records are
    # rows x columns, never fewer than its rows).
    cap = c.row_limit or DEFAULT_ROW_LIMIT[c.type]
    probe_cap = min(cap, _PROBE_CAP)
    counts, saturated = {}, False
    for d in dims:
        n = ctx.prober.count_up_to(ds, d, probe_cap)
        if n is None:
            return None
        saturated |= n > probe_cap and probe_cap < cap
        counts[d] = min(n, cap)
    if c.type == "pivot_table":
        leaf, subtotals, exact = pivot_rows_from_counts(c, counts)
    else:
        leaf, subtotals, exact = min(max(counts.values()), cap), 0, len(dims) == 1
    return leaf, subtotals, exact and not saturated


def _pivot_overhead(c) -> str:
    """What a pivot draws besides its body rows, in words (spec.pivot_frame)."""
    header_rows, hscroll, totals = pivot_frame(c)
    parts = ["the card frame", f"{header_rows} header row" + ("s" if header_rows != 1 else "")]
    if totals:
        parts.append("its totals row")
    if hscroll:
        parts.append("room for a horizontal scrollbar")
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _brain_page(ctx: RuleContext, c) -> bool:
    """The page is default.page-length's own fill (the value design.filled records for
    it). That page follows the height, so a sizing rule must not raise the
    height to fit a stale one: the fill phase refreshes it to the panel instead. The
    rule still REPORTS a page that doesn't fit, without a height fix: brain output must
    never silence a rule (the v2 echo chamber), and a stale page whose fill is ignored
    would otherwise pass unseen."""
    return ctx.brain_owns(c, "page_length")


@rule("size.table-window", "warn", "a table's height should show every row its row_limit allows, "
      "or one whole page", fixable=True)
def table_window(ctx: RuleContext):
    for c in ctx.spec.charts:
        if c.type != "table" or (c.row_limit is None and not c.page_length):
            continue
        if _probed_rows(ctx, c) is not None:
            continue  # size.grid-fit sizes it by its real row count
        h = ctx.height(c.name)
        # The question smoke asks after apply, of the most rows the table can render:
        # row_limit (the ceiling, which a top-N or raw table reaches), or one page. Same
        # grid_fit as size.grid-fit, the fills and smoke (spec.py); the header counts
        # the search bar, the page-size bar, the pager and the Summary row it draws.
        held, header, row = grid_fit(c, c.row_limit)
        visible = grid_rows_visible(h, header, row)
        # Whole rows, rounded down: rounding 6.67 up once told a 6-row page it showed
        # ~7 rows while asking for more height (the pager was what didn't fit).
        fits = f"table at {h:g} units fits {math.floor(visible)} full rows"
        page = table_page(c)
        if page is not None:
            # A paged table shows one page at a time, plus its page controls: the whole
            # page should fit, but rows beyond it are a click away, not a scroll.
            want = page
            shown = f"{fits} beside its page controls, short of its {page}-row page"
        else:
            # table_visible_ratio is 1 by default: the target is every row either way,
            # so advise never passes a height smoke then flags when the data fills it.
            want = ctx.params.table_visible_ratio * c.row_limit
            shown = (f"{fits} of its {c.row_limit}; the rest hide behind an inner scrollbar "
                     f"once the data fills row_limit")
        if visible < want:
            brain = page is not None and _brain_page(ctx, c)
            target = grid_units_for_rows(held, header, row)
            yield Finding(
                "size.table-window", "warn", c.name, ctx.where(c.name),
                f"{shown}; "
                + ("the page is a design default: advise --fix refits it to the panel"
                   if brain else
                   f"raise height to ~{math.ceil(target)} or "
                   + ("lower page_length" if page is not None else "lower row_limit")),
                fix=ctx.fix_height(c, math.ceil(target))
                if target <= 20 and not brain else None,
                height_driven=True,
            )


@rule("size.table-chrome", "info",
      "a table whose rows all show in its panel draws no page-size picker, pager or search "
      "box", since="10")
def table_chrome(ctx: RuleContext):
    """DataTables chrome over rows the reader already sees. Any page_length above 0 draws
    the page-size picker, on one page too (hasPagination, plugin-chart-table
    DataTable/DataTable.tsx:123 at 4.1.4 and 5.0.0, :173 at 6.1.0), and the pager joins
    it from a second page (:407 at 4.1.4 and 5.0.0, :616 at 6.1.0); search_box
    (include_search) draws the search bar (:383 at 4.1.4 and 5.0.0, :593 at 6.1.0). The
    brain's own fills never add them to such a table (default.page-length,
    default.search-box), so this names the author's."""
    for c in ctx.spec.charts:
        if c.type != "table" or c.row_limit is None:
            continue
        h = ctx.height(c.name)
        # The rows the panel shows with nothing above or below them (spec.py's grid model).
        shown = math.floor(grid_rows_visible(h, table_header_units()))
        if c.row_limit > shown:
            continue  # the rows outgrow the panel: paging and search earn their room
        chrome, fields = [], []
        if c.page_length and not ctx.brain_owns(c, "page_length"):
            pages = math.ceil(c.row_limit / c.page_length)
            chrome.append("a page-size picker" + (f" and a pager over {pages} pages"
                                                  if pages > 1 else ""))
            fields.append(f"page_length {c.page_length}")
        if c.search_box and not ctx.brain_owns(c, "search_box"):
            chrome.append("a search box")
            fields.append("search_box")
        if not chrome:
            continue
        yield Finding(
            "size.table-chrome", "info", c.name, ctx.where(c.name),
            f"all {c.row_limit} rows show at height {h:g}, yet {' and '.join(fields)} "
            f"draw{'s' if len(fields) == 1 else ''} {' and '.join(chrome)} over them; "
            f"remove {'it' if len(fields) == 1 else 'them'} so the table reads whole "
            f"(page_length 0 shows every row on one page with no picker)",
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


@rule("size.pivot-window", "warn", "a pivot's height must hold its header rows, totals row and the "
      "body rows its spec fixes (advise --profile counts the rest)", fixable=True, since="2")
def pivot_window(ctx: RuleContext):
    """A pivot draws a body row per distinct row key, never per record: row_limit caps
    the records (rows x columns), so it says nothing about the rows on screen. Offline
    the spec fixes only a floor: a row per metric laid out as rows, at least one value
    per row dimension, and no body row at all without row attributes (spec.pivot_rows).
    The count beyond it is size.grid-fit's, from probes, and smoke's, from the data."""
    for c in ctx.spec.charts:
        if c.type != "pivot_table" or _probed_rows(ctx, c) is not None:
            continue  # a probed pivot is size.grid-fit's: it knows the real row count
        dims = _row_dims(c)
        leaf, subtotals, _ = pivot_rows_from_counts(c, dict.fromkeys(dims, 1))
        held, header, row = grid_fit(c, leaf + subtotals)
        needed = grid_units_for_rows(held, header, row)
        h = ctx.height(c.name)
        if needed <= h:
            continue
        frame = f"{_pivot_overhead(c)} ({round(header, 2):g} units)"
        if not dims:
            # Exact: without a row dimension the spec fixes every row the pivot draws.
            yield Finding(
                "size.pivot-window", "warn", c.name, ctx.where(c.name),
                f"pivot at {h:g} units: " + (f"its {held} rows beside " if held else "")
                + f"{frame} need ~{math.ceil(needed)} units; raise height to ~{math.ceil(needed)}",
                fix=ctx.fix_height(c, math.ceil(needed)) if needed <= 20 else None,
                height_driven=True,
            )
            continue
        # A floor only, so no fix: one that still hides rows would only move the warning
        # to smoke. The count that sizes the pivot comes from --profile or the data.
        first = "the first row" if held == 1 else f"the first {held} rows"
        per = " x ".join(repr(d) for d in dims) + " value" + (
            " and metric" if len(c.metrics) > 1 and PIVOT_METRIC in pivot_axes(c)[0] else "")
        yield Finding(
            "size.pivot-window", "warn", c.name, ctx.where(c.name),
            f"pivot at {h:g} units holds {math.floor(grid_rows_visible(h, header, row))} "
            f"full rows beside {frame}; with a row per {per} it needs "
            f"~{math.ceil(needed)} units for {first} and {row:g} for each more: "
            f"advise --profile counts them",
            height_driven=True,
        )


@rule("size.grid-fit", "warn", "table/pivot heights must fit their data-driven row counts (they grow after authoring)",
      fixable=True, data_aware=True, since="2")
def grid_fit_rule(ctx: RuleContext):
    """Pre-apply half of issue #1 (the apply-time half lives in smoke): probe
    the actual dimension cardinality and check the configured height fits.
    Exact for one row dimension (a single-groupby aggregate table, a pivot with one
    row dimension, its metrics laid out as rows or not); with more, per-column probes
    give only a lower bound (the largest count), reported without a fix."""
    if ctx.prober is None:
        return
    for c in ctx.spec.charts:
        probed = _probed_rows(ctx, c)
        if probed is None:
            continue
        leaf, subtotals, exact = probed
        # Shared grid model (chartwright/spec.py), the grid_fit smoke calls: the header
        # counts the controls, pager, header rows and totals the chart draws, and a
        # paged table holds one page (its pager is in the header), not every row.
        held, header, row = grid_fit(c, leaf + subtotals)
        needed = math.ceil(grid_units_for_rows(held, header, row))
        h = ctx.height(c.name)
        if needed <= h:
            continue
        # A brain-filled page follows the height: report, but leave the fix to the fill.
        brain = c.type == "table" and table_page(c) is not None and _brain_page(ctx, c)
        totals = pivot_frame(c)[2] if c.type == "pivot_table" else c.show_totals
        about = "~" if exact else "at least "
        paged = held != leaf + subtotals  # more rows than a page: the page is exact
        yield Finding(
            "size.grid-fit", "warn", c.name, ctx.where(c.name),
            f"{_counted(c, _row_dims(c))} yields {about}{leaf} rendered rows"
            + (f", {subtotals} subtotal rows" if subtotals else "")
            + (" and a totals row" if totals else "")
            + (f"; its {held}-row page needs ~{needed} units" if paged
               else f" needing {about}{needed} units")
            + f"; height {h:g} hides the tail behind an inner scrollbar -- and row counts "
            f"grow with the data, so this only gets worse",
            fix=ctx.fix_height(c, needed) if (exact or paged) and needed <= 20 and not brain
            else None,
            height_driven=True,
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


@rule("layout.row-density", "warn", "too many axis charts side by side starves each of width",
      severities=("warn", "error"))
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


@rule("layout.row-fill", "warn", "a row should fill the 12-column grid",
      severities=("warn", "info"))
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
    # A header sits above every tab, so its height is spent before each tab's own
    # rows; a footer comes after them, so it is counted on its own.
    header = sum(b.height for s in ctx.sections if s.header for b in s.bands)
    for si, sec in enumerate(ctx.sections):
        body = not (sec.header or sec.footer)
        if body and header > ctx.params.fold_units:
            continue  # the header alone overflows: reported there, once
        total = header if body else 0.0
        for bi, band in enumerate(sec.bands):
            total += band.height
            if total > ctx.params.fold_units:
                where = "the dashboard" if sec.label == "layout" else sec.label
                with_header = f" (with the header's {header:g})" if body and header else ""
                yield Finding(
                    "layout.fold-budget", "warn", None, ctx.where_band(si, bi),
                    f"{where} runs {total:g}+ units{with_header} against a "
                    f"{ctx.params.audience} budget of "
                    f"{ctx.params.fold_units} (~{ctx.params.fold_units * 40}px); the budget runs "
                    f"out at row {bi}; move detail into tabs or prune",
                )
                break


@rule("layout.tab-balance", "info", "tabs should carry comparable weight")
def tab_balance(ctx: RuleContext):
    if len(ctx.body_sections) < 2:
        return
    counts = {s.title: sum(len(b.chart_names) for b in s.bands) for s in ctx.body_sections}
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


@rule("layout.section-headers", "info", "large flat dashboards need section headers (header rows or markdown)")
def section_headers(ctx: RuleContext):
    body = ctx.body_sections
    if len(body) != 1 or body[0].mode != "rows" or body[0].title:
        return
    n = len(ctx.spec.charts)
    has_md = any(i.is_markdown for b in body[0].bands for i in b.items)  # a footer note is no signpost
    has_md = has_md or any(isinstance(r, HeaderBlock) for r in ctx.spec.layout.rows or [])
    if n > 8 and not has_md:
        yield Finding(
            "layout.section-headers", "info", None, "layout",
            f"{n} charts with no section headers (header rows or markdown); readers need signposts "
            f"(or split into tabs)",
        )


# -- chart: encoding choice ----------------------------------------------------


@rule("chart.vbar-categories", "warn", "vertical bars drop labels past ~8 categories; rank with horizontal bars", fixable=True)
def vbar_categories(ctx: RuleContext):
    p = ctx.params
    for c in ctx.spec.charts:
        if c.type != "bar" or c.orientation != "vertical":
            continue
        if c.category_sort and c.x_label_every:
            # An ordered axis (hours, ranks) reads as a sequence left to right, and every
            # label is drawn: flipping it to a ranked horizontal list would lose the order.
            continue
        rl = c.row_limit
        if rl is not None and rl <= p.vbar_max_categories:
            continue
        if ctx.prober is not None and (ds := ctx.dataset_for(c)):
            if ctx.prober.more_than(ds, c.x_column, p.vbar_max_categories) is False:
                continue  # the data itself stays under the label limit
        lead = (f"vertical bar with row_limit {rl}" if rl is not None
                else "vertical bar with no row_limit (defaults to 10,000)")
        if c.category_sort:
            # Sorted by category, the bars are a sequence, not a ranking: keep them
            # vertical and draw every label instead.
            yield Finding(
                "chart.vbar-categories", "warn", c.name, ctx.where(c.name),
                f"{lead}: Superset drops category labels past ~{p.vbar_max_categories}; "
                "these bars are sorted by category, so set x_label_every to draw every "
                "label (Superset 6.1+), or cap the row_limit",
                fix={"chart": c.name, "set": {"x_label_every": True}},
            )
            continue
        fix = ({"chart": c.name, "set": {"orientation": "horizontal"}}
               if rl is not None and rl <= 15 else None)
        yield Finding(
            "chart.vbar-categories", "warn", c.name, ctx.where(c.name),
            lead + f": Superset drops category labels past ~{p.vbar_max_categories}; "
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
        if c.series_limit is not None and c.series_limit <= ctx.params.series_max:
            continue  # series_limit already keeps the top few
        ds = ctx.dataset_for(c)
        if ds is None:
            continue
        if ctx.prober.more_than(ds, c.groupby, ctx.params.series_max):
            yield Finding(
                "chart.series-limit", "warn", c.name, ctx.where(c.name),
                f"groupby {c.groupby!r} has more than {ctx.params.series_max} values: "
                f"a line per value is spaghetti; set series_limit to keep the top "
                f"{ctx.params.series_max}, filter, or use a coarser dimension",
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


# -- chart: waterfalls ---------------------------------------------------------------

# Aggregates whose values don't add: a running total of them means nothing (two
# averages don't make the average of both; distinct counts overlap).
_NON_ADDITIVE = ("AVG", "MIN", "MAX", "COUNT_DISTINCT")
WATERFALL_MAX_STEPS = 12
# A chart card's background in Superset's light theme, and the contrast WCAG 2.1 asks of
# a graphic against what is next to it (1.4.11 Non-text Contrast).
PANEL_RGB = (255, 255, 255)
GRAPHIC_CONTRAST = 3.0
_BAR_COLOURS = ("increase_color", "decrease_color", "total_color")


def _on_panel(hex_colour: str) -> float:
    rgb = hex_to_rgb(hex_colour)
    return contrast((rgb["r"], rgb["g"], rgb["b"]), PANEL_RGB)


@rule("chart.waterfall-additive", "warn",
      "a waterfall's steps must add up: SUM or COUNT of one measure, never AVG, MIN, MAX or "
      "COUNT_DISTINCT", since="9")
def waterfall_additive(ctx: RuleContext):
    for c in ctx.spec.charts:
        if c.type != "waterfall":
            continue
        agg = (parse_metric(c.metric) or {}).get("aggregate")
        if agg in _NON_ADDITIVE:
            yield Finding(
                "chart.waterfall-additive", "warn", c.name, ctx.where(c.name),
                f"metric {c.metric!r}: a waterfall adds every step to a running total and "
                f"draws that as the closing, and {agg} values don't add up; chart a SUM or "
                f"COUNT of the change, or show the {agg} as plain bars",
            )


@rule("chart.waterfall-order", "info",
      "a bridge reads in its own order, and Superset draws a waterfall's steps A to Z "
      "unless steps orders them", since="9")
def waterfall_order(ctx: RuleContext):
    for c in ctx.spec.charts:
        if c.type != "waterfall" or c.steps is not None or c.time_grain:
            continue
        ds = ctx.dataset_for(c)
        if ds is not None and (ds.is_temporal(c.x_column) or ds.column_types.get(c.x_column) == 0):
            continue  # periods or numbers: their own order is the reading order
        yield Finding(
            "chart.waterfall-order", "info", c.name, ctx.where(c.name),
            f"the steps of {c.x_column!r} draw A to Z, then Superset's total; for a bridge, "
            f"list them in steps, opening first, and name the closing row in closing "
            f"(Superset 6.1.0 or later)",
        )


@rule("chart.waterfall-colors", "info",
      "a waterfall's rising, falling and total bars take colours set for the dashboard, "
      "each at least 3:1 against the panel", since="9", severities=("info", "warn"))
def waterfall_colors(ctx: RuleContext):
    for c in ctx.spec.charts:
        if c.type != "waterfall":
            continue
        low = [(f, c.colour_hex(f)) for f in _BAR_COLOURS
               if set_value(c, f) is not None and _on_panel(c.colour_hex(f)) < GRAPHIC_CONTRAST]
        for field, hexed in low:
            yield Finding(
                "chart.waterfall-colors", "warn", c.name, ctx.where(c.name),
                f"{field} {hexed} is {_on_panel(hexed):.1f}:1 against the white panel, under "
                f"the {GRAPHIC_CONTRAST:g}:1 WCAG asks of a graphic; darken it",
            )
        stock = [f for f in _BAR_COLOURS if set_value(c, f) is None]
        if stock:
            faint = [f"{f} {WATERFALL_DEFAULT_HEX[f]} ({_on_panel(WATERFALL_DEFAULT_HEX[f]):.1f}:1)"
                     for f in stock if _on_panel(WATERFALL_DEFAULT_HEX[f]) < GRAPHIC_CONTRAST]
            yield Finding(
                "chart.waterfall-colors", "info", c.name, ctx.where(c.name),
                f"{', '.join(stock)} {'is' if len(stock) == 1 else 'are'} Superset's stock "
                + (f"colour; {', '.join(faint)} falls under the {GRAPHIC_CONTRAST:g}:1 a graphic "
                   f"needs against the white panel. " if faint else "colour. ")
                + "Set all three: green and red (the text shades, "
                  f"{_on_panel('#1B7F3B'):.1f}:1 and {_on_panel('#B3261E'):.1f}:1) or your "
                  "palette's, and a neutral total that reads as a total",
            )


@rule("chart.waterfall-axis-titles", "info",
      "a waterfall's axis titles sit where Superset puts them, over wide tick labels",
      since="9")
def waterfall_axis_titles(ctx: RuleContext):
    # Waterfall/transformProps.ts, all three releases (6.1.0 :445-464): both titles sit
    # mid-axis with no nameGap (ECharts' 15 px) and a 16 or 20 px pad, and the panel has
    # no margin control for either. Seen on 6.1.0: '$3.00M' under the y title.
    for c in ctx.spec.charts:
        if c.type != "waterfall":
            continue
        if c.y_axis_title:
            yield Finding(
                "chart.waterfall-axis-titles", "info", c.name, ctx.where(c.name),
                f"y_axis_title {c.y_axis_title!r}: Superset draws a waterfall's y-axis title "
                f"about 40 px left of the axis, inside the column of tick labels, with no "
                f"margin to set, so a label wider than about five characters ('$4.72M') can "
                f"run through it; name the unit in the chart's title or its number_format",
            )
        if c.x_axis_title and c.x_label_rotation in (45, 90):
            yield Finding(
                "chart.waterfall-axis-titles", "info", c.name, ctx.where(c.name),
                f"x_axis_title {c.x_axis_title!r} with labels rotated {c.x_label_rotation} "
                f"degrees: the rotated labels hang over the title, which Superset keeps about "
                f"30 px under the axis; drop the title or the rotation",
            )


@rule("chart.waterfall-steps", "warn",
      f"a waterfall past ~{WATERFALL_MAX_STEPS} steps stops reading as a bridge", since="9")
def waterfall_steps(ctx: RuleContext):
    # Offline it counts a bridge's own steps; with a live resolution (advise --profile) it
    # probes the values of an x column that steps doesn't order, as vbar-categories does.
    for c in ctx.spec.charts:
        if c.type != "waterfall" or c.groupby:
            continue
        if c.steps is not None:
            n = len(c.steps)
            if n > WATERFALL_MAX_STEPS:
                yield Finding(
                    "chart.waterfall-steps", "warn", c.name, ctx.where(c.name),
                    f"{n} steps between the opening and the closing; past ~"
                    f"{WATERFALL_MAX_STEPS} the bars thin and their labels drop: fold the "
                    f"small ones into one 'Other' row",
                )
            continue
        if ctx.prober is None or c.time_grain:
            continue
        if c.row_limit is not None and c.row_limit <= WATERFALL_MAX_STEPS:
            continue
        ds = ctx.dataset_for(c)
        if ds is None or ds.is_temporal(c.x_column):
            continue
        if ctx.prober.more_than(ds, c.x_column, WATERFALL_MAX_STEPS):
            yield Finding(
                "chart.waterfall-steps", "warn", c.name, ctx.where(c.name),
                f"{c.x_column!r} has more than {WATERFALL_MAX_STEPS} values, one bar each; past "
                f"~{WATERFALL_MAX_STEPS} the bars thin and their labels drop: fold the small "
                f"ones into one 'Other' row or filter",
            )


# -- chart: box plots ------------------------------------------------------------------

BOX_PLOT_MAX_GROUPS = 20
# Time grains from finest to coarsest, and the period a group column's name says it is.
# A day column is left out: day of week and day of month read alike by name.
_GRAIN_RANK = {"PT1S": 0, "PT1M": 0, "PT1H": 0, "P1D": 1, "P1W": 2, "P1M": 3, "P3M": 4, "P1Y": 5}
_PERIOD_COLUMN = re.compile(r"(?:^|_)(week|month|quarter|qtr|year)(?:_|$|id$|name$)", re.I)
_PERIOD_RANK = {"week": 2, "month": 3, "quarter": 4, "qtr": 4, "year": 5}


@rule("chart.box-plot-observations", "warn",
      "a box needs many observations: distribute across a finer grain than the groups",
      since="9")
def box_plot_observations(ctx: RuleContext):
    for c in ctx.spec.charts:
        if c.type != "box_plot" or not c.time_grain:
            continue
        grain = _GRAIN_RANK.get("P1W" if "P1W" in c.time_grain else c.time_grain)
        for col in c.groupby:
            m = _PERIOD_COLUMN.search(col)
            if grain is None or not m or grain < _PERIOD_RANK[m.group(1).lower()]:
                continue
            yield Finding(
                "chart.box-plot-observations", "warn", c.name, ctx.where(c.name),
                f"observations at {c.time_grain} grouped by {col!r}: each box holds one "
                f"observation per {m.group(1).lower()}, too few for quartiles; distribute "
                f"across a finer grain, e.g. P1D",
            )
            break


@rule("chart.box-plot-groups", "warn",
      f"past ~{BOX_PLOT_MAX_GROUPS} boxes each thins to a sliver and the labels drop",
      since="9")
def box_plot_groups(ctx: RuleContext):
    # Offline the groups are unknown; with a live resolution it probes one group column,
    # as vbar-categories does.
    if ctx.prober is None:
        return
    for c in ctx.spec.charts:
        if c.type != "box_plot" or len(c.groupby) != 1:
            continue
        ds = ctx.dataset_for(c)
        if ds is None:
            continue
        if ctx.prober.more_than(ds, c.groupby[0], BOX_PLOT_MAX_GROUPS):
            yield Finding(
                "chart.box-plot-groups", "warn", c.name, ctx.where(c.name),
                f"{c.groupby[0]!r} has more than {BOX_PLOT_MAX_GROUPS} values, a box each; "
                f"group coarser, filter to the groups that matter, or chart the spread per "
                f"group as a table",
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


@rule("chart.pivot-dims", "warn", "a pivot past three total dimensions is unreadable nesting", since="2")
def pivot_dims(ctx: RuleContext):
    for c in ctx.spec.charts:
        if c.type == "pivot_table" and len(c.rows) + len(c.columns) > 3:
            yield Finding(
                "chart.pivot-dims", "warn", c.name, ctx.where(c.name),
                f"{len(c.rows)} row + {len(c.columns)} column dimensions; keep the total "
                f"to 3 or split the question across charts",
            )


@rule("chart.pivot-columns", "warn", "column-dim values x metrics = rendered columns; past ~15 the pivot scrolls sideways", data_aware=True, since="2")
def pivot_columns(ctx: RuleContext):
    if ctx.prober is None:
        return
    for c in ctx.spec.charts:
        if c.type != "pivot_table" or not c.columns:
            continue
        ds = ctx.dataset_for(c)
        if ds is None:
            continue
        rendered = len(c.metrics)
        for col in c.columns:
            n = ctx.prober.count_up_to(ds, col, 16)
            if n is None:
                rendered = None
                break
            rendered *= n
        if rendered is not None and rendered > 15:
            yield Finding(
                "chart.pivot-columns", "warn", c.name, ctx.where(c.name),
                f"~{rendered}+ rendered columns ({len(c.metrics)} metric(s) x column-dim "
                f"values); the pivot scrolls sideways -- filter the column dimension or "
                f"move it to rows",
            )


@rule("chart.format-bands", "warn", "conditional-formatting bands must tell one coherent story per metric",
      since="2", severities=("warn", "info"))
def format_bands(ctx: RuleContext):
    for c in ctx.spec.charts:
        if c.type != "pivot_table" or not c.conditional_formatting:
            continue
        by_metric: dict[str, list] = {}
        for r in c.conditional_formatting:
            lo, hi = ((r.target_left, r.target_right) if r.operator == "between"
                      else (float("-inf"), r.target) if r.operator == "<"
                      else (r.target, float("inf")))
            # compared by the shade painted, so "green" and its own hex agree
            by_metric.setdefault(r.metric, []).append((lo, hi, r.color, r.paint_hex()))
        for metric, bands in by_metric.items():
            for i in range(len(bands)):
                for j in range(i + 1, len(bands)):
                    (a0, a1, ca, ha), (b0, b1, cb, hb) = bands[i], bands[j]
                    if a0 < b1 and b0 < a1 and ha != hb:
                        yield Finding(
                            "chart.format-bands", "warn", c.name, ctx.where(c.name),
                            f"metric {metric!r}: {ca} and {cb} bands overlap "
                            f"(cell color depends on rule order, not the value); "
                            f"make the ranges disjoint",
                        )
            if len(bands) == 1:
                yield Finding(
                    "chart.format-bands", "info", c.name, ctx.where(c.name),
                    f"metric {metric!r} has a single {bands[0][2]} band: one color is "
                    f"decoration, not a signal; band the full green/amber/red story "
                    f"or drop it",
                )


_ORDINAL_RE = re.compile(r"(^|_)(day|weekday|dow|month|quarter|hour)(_|$|name)", re.I)


@rule("chart.ordinal-order", "info", "ordinal dimensions (weekday, month) sort alphabetically unless order-encoded", since="2")
def ordinal_order(ctx: RuleContext):
    def dims(c):
        if c.type == "bar":
            return [c.x_column]
        if c.type == "heatmap":
            # An axis ordered by value is ordered on purpose, not by label.
            return [col for col, order in ((c.x_column, c.x_order), (c.y_column, c.y_order))
                    if order not in ("value_asc", "value_desc")]
        if c.type == "pivot_table":
            return [*c.rows, *c.columns]
        if c.type == "box_plot":
            return list(c.groupby)  # the boxplot step groups and sorts by them
        return []

    def own_order(c, column) -> bool:
        # A column the dataset reports numeric or temporal sorts in its own order
        # (1..12); only names sort alphabetically. Known with a live resolution only.
        ds = ctx.dataset_for(c)
        return ds is not None and ds.column_types.get(column) in (0, 2)

    for c in ctx.spec.charts:
        hits = [d for d in dims(c) if d and _ORDINAL_RE.search(d)]
        if c.type != "bar":
            hits = [d for d in hits if not own_order(c, d)]
        if hits and c.type == "bar" and not c.category_sort:
            # A bar sorts by its first metric unless category_sort is set, so an
            # order-encoded label alone changes nothing.
            yield Finding(
                "chart.ordinal-order", "info", c.name, ctx.where(c.name),
                f"{hits} look ordinal but these bars sort by their first metric; set "
                f"category_sort \"asc\", over an order-encoded label column (e.g. '1-Mon') "
                f"if the dataset has one, since Superset sorts labels alphabetically",
            )
        elif hits:
            yield Finding(
                "chart.ordinal-order", "info", c.name, ctx.where(c.name),
                f"{hits} look ordinal but Superset sorts categories alphabetically "
                f"(Apr, Aug, Dec...); chart an order-encoded label column "
                f"(e.g. '1-Mon') if the dataset has one",
            )


@rule("chart.treemap-vs-bar", "info", "a one-level treemap of few categories is a worse bar chart", data_aware=True, since="2")
def treemap_vs_bar(ctx: RuleContext):
    if ctx.prober is None:
        return
    for c in ctx.spec.charts:
        if c.type != "treemap" or len(c.groupby) != 1:
            continue
        ds = ctx.dataset_for(c)
        if ds is None:
            continue
        if ctx.prober.more_than(ds, c.groupby[0], 10) is False:
            yield Finding(
                "chart.treemap-vs-bar", "info", c.name, ctx.where(c.name),
                f"one grouping level with <= 10 values: a horizontal bar shows the "
                f"same data with readable labels and comparable lengths",
            )


@rule("chart.dupe", "info", "two charts answering the identical question is redundancy")
def chart_dupe(ctx: RuleContext):
    seen: dict[str, str] = {}
    # A written Superset default (legend at the top) is the same chart as an omitted one.
    for c in without_superset_defaults(ctx.spec).charts:
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
        elif c.type == "pivot_table" and c.row_limit is None:
            yield Finding(
                "data.row-limit-intent", "info", c.name, ctx.where(c.name),
                "pivot riding the default row_limit (10,000); set it deliberately",
            )


@rule("data.top-n-sort", "warn", "a limit without an order is a sample, not a ranking", since="2")
def top_n_sort(ctx: RuleContext):
    for c in ctx.spec.charts:
        if (c.type == "table" and (c.metrics or c.groupby) and c.sort_by is None
                and c.row_limit is not None and c.row_limit <= 100):
            yield Finding(
                "data.top-n-sort", "warn", c.name, ctx.where(c.name),
                f"row_limit {c.row_limit} with no sort_by shows {c.row_limit} ARBITRARY "
                f"rows, not a top {c.row_limit}; set sort_by to the ranking metric",
            )
        elif (c.type == "bar" and c.category_sort and c.series_limit is not None
                and c.row_limit is not None and c.row_limit < DEFAULT_ROW_LIMIT["bar"]):
            # One "Sort query by" control (timeseries_limit_metric) ranks the series and
            # orders the query (buildQueryObject.ts, normalizeOrderBy.ts), so with a
            # series limit the query can't also be ordered by the category.
            yield Finding(
                "data.top-n-sort", "warn", c.name, ctx.where(c.name),
                f"row_limit {c.row_limit} with category_sort and series_limit keeps the "
                f"{c.row_limit} largest values, not the first categories: Superset orders "
                "the query by the series ranking, so the axis shows gaps; drop row_limit, "
                "or drop series_limit so the query is ordered by the category",
            )


_RANGE_DAYS = {"day": 1, "week": 7, "month": 30.4, "quarter": 91, "year": 365}
_GRAIN_DAYS = {"PT1S": 1 / 86400, "PT1M": 1 / 1440, "PT1H": 1 / 24,
               "P1D": 1, "P1W": 7, "P1M": 30.4, "P3M": 91, "P1Y": 365}
# Points a chart type can render before it stops informing: bars must read as
# discrete periods; lines/areas add nothing past a few hundred points at
# dashboard width.
_POINT_BUDGET = {"timeseries_bar": 40, "timeseries_line": 300,
                 "timeseries_area": 300, "timeseries_scatter": 300}


def _grain_days(grain: str) -> float | None:
    if grain in _GRAIN_DAYS:
        return _GRAIN_DAYS[grain]
    if "P1W" in grain:  # Superset's week-anchor spellings (1969-12-28T.../P1W)
        return 7
    return None


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
        grain = _grain_days(eff)
        if span is None or grain is None:
            continue
        label = eff if c.time_grain else f"{eff} (the default when omitted)"
        points = span / grain
        budget = _POINT_BUDGET.get(c.type, 300)
        if span == 0:
            yield Finding(
                "data.grain-vs-range", "warn", c.name, ctx.where(c.name),
                f"{c.time_range!r} spans 0 days; extend the time_range",
            )
        elif points < 2:
            yield Finding(
                "data.grain-vs-range", "warn", c.name, ctx.where(c.name),
                f"{c.time_range!r} at grain {label} yields ~{points:.1f} point(s); "
                f"a line needs a finer grain or a longer range",
            )
        elif points > budget:
            hint = ("bars stop reading as discrete periods; switch to a line or coarsen "
                    "the grain" if c.type == "timeseries_bar" else "coarsen the grain")
            yield Finding(
                "data.grain-vs-range", "warn", c.name, ctx.where(c.name),
                f"{c.time_range!r} at grain {label} yields ~{points:,.0f} points "
                f"(budget ~{budget} for {c.type}); {hint}",
            )


def _loaded_range(ctx: RuleContext, c) -> tuple[str | None, str]:
    """The time range a chart loads with, and where it comes from: a defaulted dashboard
    time_range filter in its scope replaces the chart's own (the filter's extra form data
    overrides time_range), else the chart's time_range."""
    for f in ctx.spec.filters:
        if f.type == "time_range" and f.default and (f.charts is None or c.name in f.charts):
            return f.default, f"the time_range filter {f.name!r} loads it with"
    return c.time_range, "its time_range"


@rule("data.rolling-window-span", "warn",
      "a rolling trendline KPI's time range must hold its window and its comparison", since="9")
def rolling_window_span(ctx: RuleContext):
    """A trailing-12-month total over "Last year" draws one point: the backend keeps only
    windows that hold rolling_min_periods steps, and compare_lag needs that many points
    more. With too few, the trendline is a dot and no change shows, silently."""
    for c in ctx.spec.charts:
        if c.type != "big_number_trend" or c.rolling_type in (None, "cumsum"):
            continue
        time_range, source = _loaded_range(ctx, c)
        span = _span_days(time_range) if time_range else None
        eff = c.time_grain or DEFAULT_TIME_GRAIN
        grain = _grain_days(eff)
        if span is None or grain is None:
            continue
        least = max(1, c.rolling_periods if c.rolling_min_periods is None else c.rolling_min_periods)
        lag = c.compare_lag or 0
        # A range of N grain steps touches about N + 1 buckets. The backend keeps the
        # windows from bucket `least` on, and the comparison needs `lag` points before the
        # latest; without one, a line still needs two points.
        buckets = round(span / grain) + 1
        points = max(0, buckets - least + 1)
        wanted = max(lag + 1, 2)
        if points >= wanted:
            continue
        needs = least + wanted - 1
        yield Finding(
            "data.rolling-window-span", "warn", c.name, ctx.where(c.name),
            f"{source} {time_range!r}: about {buckets} {eff} buckets, but a {least}-step "
            f"window" + (f" with compare_lag {lag}" if lag else "") + f" needs {needs}: the "
            f"trendline draws {points} point(s)" + (" and no change shows" if lag else "")
            + "; widen the range"
            + (" or leave this chart out of that filter's `charts`" if "filter" in source else ""),
        )


_FINE_GRAINS = (None, "PT1S", "PT1M", "PT1H", "P1D")


def _unwindowed(ctx: RuleContext) -> bool:
    """No defaulted dashboard time filter, so first load spans ALL history."""
    return not any(f.type == "time_range" and f.default for f in ctx.spec.filters)


@rule("chart.trend-grain", "info", "trend tiles at a fine grain over full history draw thousands of points in a small card", since="2")
def trend_grain(ctx: RuleContext):
    if not _unwindowed(ctx):
        return
    for c in ctx.spec.charts:
        # A trend with its own time_range is bounded whatever the dashboard does.
        if c.type == "big_number_trend" and c.time_grain in _FINE_GRAINS and not c.time_range:
            yield Finding(
                "chart.trend-grain", "info", c.name, ctx.where(c.name),
                f"sparkline at grain {c.time_grain or 'P1D (default)'} with no defaulted "
                f"dashboard time window draws full history daily; coarsen to P1W/P1M, "
                f"give the chart a time_range, or give the time_range filter a default",
            )


@rule("data.unwindowed-history", "warn",
      "timeseries charts with no way to bound the window draw ALL history at their grain",
      since="3")
def unwindowed_history(ctx: RuleContext):
    """The commonest real-world Superset dashboard failure, and the one the
    rulebook missed entirely: nothing bounds the time window, so the board
    queries the full table and draws a point per day on every load.

    Reported ONCE for the dashboard, not once per chart: it is a single
    property of the dashboard with a single fix, and a six-timeseries board
    would otherwise emit six warns for it.

    Deliberately silent when a time_range filter EXISTS without a default --
    `filters.time-default` already names that exact one-line fix, and
    double-reporting one remedy at two severities is noise. Deployments that
    want it to bite raise that rule via the overlay's `severity` map, which is
    the mechanism sec.15.8 chose for precisely this.

    `data.grain-vs-range` is the sibling for when a range IS set: it can count
    the points. This one cannot, because the span is "however much data
    exists" -- which is what makes it dangerous. Not autofixable: every remedy
    changes what data the chart shows (sec.2.2)."""
    if not _unwindowed(ctx) or any(f.type == "time_range" for f in ctx.spec.filters):
        return
    exposed = [c.name for c in ctx.spec.charts
               if c.type in TIMESERIES_TYPES and not c.time_range
               and c.time_grain in _FINE_GRAINS]
    if not exposed:
        return
    yield Finding(
        "data.unwindowed-history", "warn", None, "filters",
        f"{exposed} have no time_range and the dashboard has no time_range filter at "
        f"all: at a daily-or-finer grain every load queries and draws the dataset's "
        f"FULL history. Add a time_range filter WITH a default (e.g. 'Last quarter'), "
        f"set the charts' time_range, or coarsen the grain",
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
    from .defaults import FILLS  # function-level: defaults imports this module

    counts, dates = FILLS["default.count-format"], FILLS["default.date-tile"]
    for c in ctx.spec.charts:
        if c.type in KPI_TYPES and c.number_format is None and not getattr(c, "date_format", None):
            if counts.decide(ctx, c)[0] is not None:
                continue  # default.count-format offers this remedy with a fix; one finding
            if c.type == "big_number_total" and dates.decide(ctx, c)[0] is not None:
                continue  # a date: default.date-tile formats it as one
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


@rule("filters.count", "warn", "past ~6 select pickers a filter bar stops being navigable (and each costs a query on load)", since="2")
def filters_count(ctx: RuleContext):
    selects = [f.name for f in ctx.spec.filters if f.type == "select"]
    if len(selects) > ctx.params.max_filter_selects:
        yield Finding(
            "filters.count", "warn", None, "filters",
            f"{len(selects)} select filters (max {ctx.params.max_filter_selects} for this "
            f"audience); each is a distinct-values query on every load -- keep the few "
            f"viewers actually change, move the rest to per-chart WHERE filters",
        )


@rule("filters.duplicate-column", "info", "two filters on the same column fight each other", since="2")
def filters_duplicate(ctx: RuleContext):
    seen: dict[tuple, str] = {}
    for f in ctx.spec.filters:
        if f.type not in ("select", "range"):
            continue
        key = (f.dataset.key(), f.column)
        if key in seen:
            yield Finding(
                "filters.duplicate-column", "info", None, "filters",
                f"filters {seen[key]!r} and {f.name!r} both target "
                f"{f.column!r}; viewers get two controls with one meaning",
            )
        else:
            seen[key] = f.name


@rule("filters.select-cardinality", "warn", "a select over hundreds of distinct values is an unusable picker", data_aware=True, since="2")
def filters_select_cardinality(ctx: RuleContext):
    if ctx.prober is None:
        return
    for f in ctx.spec.filters:
        if f.type != "select":
            continue
        ds = (ctx.resolution.datasets.get(f.dataset.key())
              if ctx.resolution is not None else None)
        if ds is None:
            continue
        if ctx.prober.more_than(ds, f.column, 500):
            yield Finding(
                "filters.select-cardinality", "warn", None, "filters",
                f"select {f.name!r} on {f.column!r} has more than 500 distinct values: "
                f"an unusable picker and a heavy load-time query; use a numeric range "
                f"filter or a coarser column",
            )


@rule("filters.time-default", "info", "an undefaulted time picker loads the dashboard over ALL history", since="2")
def filters_time_default(ctx: RuleContext):
    for f in ctx.spec.filters:
        if f.type == "time_range" and not f.default:
            yield Finding(
                "filters.time-default", "info", None, "filters",
                f"time_range filter {f.name!r} has no default: first load scans and "
                f"draws full history; set a default window (e.g. 'Last quarter')",
            )


@rule("filters.range-default", "info", "a range slider with no default bounds spans the whole domain", since="2")
def filters_range_default(ctx: RuleContext):
    for f in ctx.spec.filters:
        if f.type == "range" and f.le is None and f.ge is None:
            yield Finding(
                "filters.range-default", "info", None, "filters",
                f"range filter {f.name!r} sets neither ge nor le; give it a default "
                f"bound so the slider starts somewhere meaningful",
            )


@rule("narrative.format-consistency", "info", "one measure, one number format", since="2")
def format_consistency(ctx: RuleContext):
    by_metric: dict[str, dict] = {}
    for c in ctx.spec.charts:
        if c.type in KPI_TYPES:
            by_metric.setdefault(c.metric, {})[c.name] = c.number_format
    for metric, charts in by_metric.items():
        if len(charts) > 1 and len(set(charts.values())) > 1:
            yield Finding(
                "narrative.format-consistency", "info", None, "charts",
                f"metric {metric!r} renders with different number formats across "
                f"{sorted(charts)}: {sorted(set(str(v) for v in charts.values()))}; "
                f"pick one",
            )


@rule("narrative.color-scheme", "warn",
      "a colour scheme Superset doesn't ship draws the default palette unless your deployment registers it",
      since="4")
def color_scheme(ctx: RuleContext):
    known = set(SUPERSET_COLOR_SCHEMES)
    named = [(None, "dashboard", ctx.spec.dashboard.color_scheme)]
    # A heatmap's color_scheme is a sequential scheme the schema already limits.
    named += [(c.name, ctx.where(c.name), c.color_scheme) for c in ctx.spec.charts
              if isinstance(c, _ColorSchemeMixin)]
    for chart, where, scheme in named:
        if scheme and scheme not in known:
            near = [k for k in SUPERSET_COLOR_SCHEMES if k.lower() == scheme.lower()]
            hint = f"; did you mean {near[0]!r}?" if near else (
                "; fine if your deployment registers it (EXTRA_CATEGORICAL_COLOR_SCHEMES)")
            yield Finding(
                "narrative.color-scheme", "warn", chart, where,
                f"color_scheme {scheme!r} is not one Superset ships{hint}",
            )


def _markdown_blocks(ctx: RuleContext):
    """Every markdown block in the layout's rows, as (container, label, row, item,
    block). Raw layout indices, so a fix can address the block (markdown has no name
    to key on): the container is None (layout rows), a tab index, [tab, sub-tab],
    "header" or "footer", and the row index counts headers and dividers. fix.py reads
    the same."""
    lay = ctx.spec.layout
    sources = []
    if lay.header:
        sources.append(("header", "header", lay.header))
    if lay.rows:
        sources.append((None, "layout", lay.rows))
    for ti, t in enumerate(lay.tabs or []):
        if t.rows:
            sources.append((ti, f"tab {t.title!r}", t.rows))
        for si, sub in enumerate(t.tabs or []):
            if sub.rows:
                title = f"{t.title} > {sub.title}"
                sources.append(([ti, si], f"tab {title!r}", sub.rows))
    if lay.footer:
        sources.append(("footer", "footer", lay.footer))
    for addr, label, rows in sources:
        for ri, row in enumerate(rows):
            for ii, item in enumerate(row_items(row) or []):
                if not isinstance(item, str):
                    yield addr, label, ri, ii, item


def _markdown_estimate(ctx: RuleContext, item) -> Fit:
    """How the block's text fits at its width. The filter bar narrows the grid when it
    opens by default: on the left, beside a dashboard with native filters. A horizontal
    bar sits above the grid, but 4.1.4 and 5.0.0 draw it on the left without the
    HORIZONTAL_FILTER_BAR flag, so that case is bounded both ways."""
    d = ctx.spec.dashboard
    bar = (bool(ctx.spec.filters) if d.filter_bar_orientation != "horizontal"
           else None if ctx.spec.filters else False)
    return estimate(item.markdown, ctx.spec.resolved_item_width(item), bar)


_TYPE_CSS = re.compile(r"(?<![\w-])(?:font(?:-family|-size)?|line-height|letter-spacing|"
                       r"word-spacing)\s*:", re.I)


@rule("layout.markdown-height", "info", "a one-line markdown header doesn't need a chart-sized block", fixable=True, since="2")
def markdown_height(ctx: RuleContext):
    owned = ctx.standard_rows()
    for addr, label, ri, ii, item in _markdown_blocks(ctx):
        if addr in ("header", "footer") and (addr, ri) in owned:
            continue  # a standard's row: the standard is its one owner, not a repair
        lines = [l for l in item.markdown.splitlines() if l.strip()]
        h = item.height or DEFAULT_HEIGHT["markdown"]
        if len(lines) > 1 or h < 3:
            continue
        # The height the line takes, padding and margins included (size.markdown-fit's
        # estimate): a level-1 heading needs 2.4 units, a plain line 1.6.
        fit = _markdown_estimate(ctx, item)
        if fit.media or h <= fit.units:
            continue
        yield Finding(
            "layout.markdown-height", "info", None, f"{label} row {ri}",
            f"one-line markdown block at {h:g} units; {fit.units:g} fits it",
            fix={"md": [addr, ri, ii], "set": {"height": fit.units}},
        )


@rule("size.markdown-fit", "warn",
      "a markdown block must be tall enough for its text: Superset cuts off the rest, "
      "with no scrollbar on macOS", fixable=True, since="11", severities=("warn", "info"))
def markdown_fit(ctx: RuleContext):
    """The text's height, estimated from its markdown and its width (markdown_fit.py),
    against the block's. Warns only when the lower bound of the estimate already cuts
    letters off (8 px, one grid row, past the bottom edge): text Superset hides with no
    sign in a screenshot. A block whose last line merely touches the edge, or whose
    padding doesn't fit, gets an info: it scrolls a few px, and Windows draws a
    scrollbar in it. That is how a one-line strip under 1.6 units, or a heading strip
    under 2, falls short.

    The fix raises the height to the upper bound, which fits on every release. None
    when the block holds an image (its height is unknown), when the text needs more
    than the 100-unit maximum, or in a header or footer row a standard owns (its
    height is the standard's; only a cut-off text is reported there)."""
    d = ctx.spec.dashboard
    if d.css and _TYPE_CSS.search(d.css):
        caveat = "; the dashboard's CSS sets its own type, so this assumes Superset's default"
    elif d.theme:
        caveat = f"; this assumes Superset's default type, which the theme {d.theme!r} may change"
    else:
        caveat = ""
    owned = ctx.standard_rows()
    for addr, label, ri, ii, item in _markdown_blocks(ctx):
        h = item.height or DEFAULT_HEIGHT["markdown"]
        box = box_px(h)
        fit = _markdown_estimate(ctx, item)
        if fit.need_low <= box:
            continue
        cut = fit.text_low - box >= 8
        standard = addr in ("header", "footer") and (addr, ri) in owned
        if standard and not cut:
            continue
        w = ctx.spec.resolved_item_width(item)
        need = (f"at least ~{fit.units:g} units with its image" if fit.media
                else f"~{fit.units:g} units")
        if cut:
            hidden = max(1, round((fit.text_low - box) / 22))
            lead = (f"markdown text runs past its block: about {hidden} line"
                    f"{'s' if hidden > 1 else ''} cut off at the bottom edge, hidden behind "
                    f"an inner scrollbar macOS doesn't draw")
        else:
            lead = (f"markdown block {fit.need_low - box:.0f} px short: its last line or its "
                    f"padding reaches the bottom edge, so the block scrolls, and Windows draws "
                    f"a scrollbar in it")
        if standard:
            tail = "; a standard owns this row, so its height is the standard's to change"
        elif fit.units > 100:
            tail = "; past the 100-unit maximum, so split the text or give it a tab of its own"
        else:
            tail = f"; raise the height{' or widen it' if w < 12 else ''}"
        yield Finding(
            "size.markdown-fit", "warn" if cut else "info", None, f"{label} row {ri}",
            f"{lead}; it needs {need} at {w}/12, has {h:g}{tail}{caveat}",
            fix=({"md": [addr, ri, ii], "set": {"height": fit.units}}
                 if not (standard or fit.media or fit.units > 100) else None),
        )
