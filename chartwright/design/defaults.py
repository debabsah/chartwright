"""Design defaults: the default.* rule family (docs/DESIGN-BRAIN.md sec.16).

A fill is a design default the brain writes INTO the spec, through `advise --fix`
and MCP `fix_spec` only: an explicit field the author reads in the diff and can
edit or take over. Compile, plan and decompile see an ordinary field; nothing here
runs at compile time, so a spec that was never fixed builds the same bytes.

design.filled records, per chart, each field the brain filled and the VALUE it
wrote. That value is what tells the brain's work from the author's:
- not recorded and written: the author's. Never touched.
- recorded, and the chart holds exactly that value: the brain's. Every --fix
  recomputes it from the chart as it is now (a new height, row_limit, grain or
  groupby) and updates field and record together, or removes both when the rule
  no longer applies.
- recorded, and the chart holds another value: the author edited it. --fix
  releases it (records null, keeps the value); from then on it is the author's.
- recorded, and the chart no longer holds it: the author deleted it. --fix records
  null.
- recorded as null: the author's field, whether they edited or deleted the fill.
  Never filled again until the author deletes the record, so an edit that is later
  deleted stays deleted (the same released record content standards keep).
- not recorded and not written: unset; filled when the rule applies.

An entry for a chart the spec no longer has, or a field that chart no longer has
(renamed, removed, or another chart type), validates; default.stale-record says
so and --fix drops it.

Every rule is info severity, fixable, presentation-only (the query is unchanged),
and never writes Superset's own default: that draws nothing new, and an empty
change is not a decision worth a line in the spec.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Callable

from ..spec import (DEFAULT_TIME_GRAIN, grid_rows_visible, metric_label, parse_metric,
                    table_header_units)
from .model import KPI_TYPES, TIMESERIES_TYPES, Finding, RuleContext, rule
from .rules import _span_days


@dataclass(frozen=True)
class Fill:
    rule: str
    field: str
    types: frozenset
    # (ctx, chart) -> (value to fill or None, one-line reason either way)
    decide: Callable[[RuleContext, Any], tuple[Any, str]]
    superset: Any            # Superset's own value for the unset field: never filled
    superset_text: str       # what Superset draws when the field is unset
    override: str            # how the author takes the field over


FILLS: dict[str, Fill] = {}


def _show(v) -> str:
    return "true" if v is True else "false" if v is False else repr(v)


def _release(ctx: RuleContext, fill: Fill, c, detail: str, why: str) -> Finding:
    """A fix that hands a field to the author and leaves the chart's value alone: the
    record becomes null, so the field is never filled again until the author deletes
    the record."""
    return Finding(fill.rule, "info", c.name, ctx.where(c.name), detail,
                   fix={"chart": c.name, "set": {}, "record": {fill.field: None}},
                   why=why, release=True)


def _findings(ctx: RuleContext, fill: Fill):
    for c in ctx.spec.charts:
        if c.type not in fill.types:
            continue
        field = fill.field
        if ctx.standard_holds(c, field):
            continue  # a standard's (or the author's, released from it): one owner per field
        rec = ctx.filled(c.name)
        recorded = field in rec
        present = ctx.written(c, field)
        current = getattr(c, field) if present else None
        where = f"design.filled[{c.name!r}][{field!r}]"
        if not recorded:
            if present:
                continue  # the author's value
        elif rec[field] is None:
            continue  # released: the author edited or deleted the fill; never filled again
        elif not present:
            yield _release(
                ctx, fill, c,
                f"{field} was filled and you deleted it; --fix records {where} as null "
                f"and fills it no more (delete that entry to let it)",
                "the author deleted the filled value")
            continue
        elif current != rec[field]:
            yield _release(
                ctx, fill, c,
                f"{field} {_show(current)} is not the {_show(rec[field])} the brain filled, "
                f"so it is yours; --fix records {where} as null, keeps your value and "
                f"fills the field no more, even if you delete it later (delete that entry "
                f"to let it)",
                f"the author changed the filled {_show(rec[field])} to {_show(current)}")
            continue
        # Unset, or still exactly the brain's fill: decide from the chart as it is now.
        value, reason = fill.decide(ctx, c)
        if value is not None and value == fill.superset:
            value = None  # never write Superset's own default
        if value is None:
            if recorded:
                yield Finding(
                    fill.rule, "info", c.name, ctx.where(c.name),
                    f"{field} {_show(current)} was filled and no longer applies ({reason}); "
                    f"--fix removes it and its record, back to {fill.superset_text}",
                    fix={"chart": c.name, "set": {}, "unset": [field], "unrecord": [field]},
                    why=f"no longer applies: {reason}",
                )
            continue
        if present and current == value:
            continue  # the fill is up to date
        if present:
            detail = (f"{field} {_show(current)} was filled for the chart as it was; --fix "
                      f"refreshes it to {_show(value)}: {reason}. Change it yourself to "
                      f"keep a value of your own")
        else:
            detail = (f"{field} is unset; --fix fills {_show(value)}: {reason}. "
                      f"To keep {fill.superset_text}, ignore {fill.rule}@{c.name}")
        yield Finding(
            fill.rule, "info", c.name, ctx.where(c.name), detail,
            fix={"chart": c.name, "set": {field: value}, "record": {field: value}},
            why=reason,
        )


def _fill(rule_id: str, field: str, types, doc: str, *, superset, superset_text: str,
          override: str, since: str = "5"):
    def deco(decide):
        f = Fill(rule_id, field, frozenset(types), decide, superset, superset_text, override)
        FILLS[rule_id] = f

        def fn(ctx: RuleContext):
            yield from _findings(ctx, f)

        rule(rule_id, "info", doc, fixable=True, since=since)(fn)
        return decide
    return deco


@rule("default.stale-record", "info",
      "design.filled names only charts and fields the spec has", fixable=True, since="7")
def stale_record(ctx: RuleContext):
    """A record left by a chart that was renamed, removed or given another type. It
    validates, so the spec still builds; --fix drops it. A renamed chart's fills are the
    author's from then on, unless its entry is renamed with it first."""
    design = ctx.spec.design
    charts = {c.name: c for c in ctx.spec.charts}
    for name, rec in (design.filled if design else {}).items():
        chart = charts.get(name)
        if chart is None:
            yield Finding(
                "default.stale-record", "info", name, f"design.filled[{name!r}]",
                f"design.filled names chart {name!r}, which the spec no longer has "
                f"(renamed or removed); --fix drops the entry. If you renamed the chart, "
                f"rename the entry with it instead, so the brain keeps its fills current",
                fix={"forget": name, "unrecord": sorted(rec)},
                why="the chart was renamed or removed", release=True)
            continue
        foreign = sorted(f for f in rec if f not in type(chart).model_fields)
        if foreign:
            yield Finding(
                "default.stale-record", "info", name, f"design.filled[{name!r}]",
                f"a {chart.type} chart has no {', '.join(foreign)}; --fix drops "
                f"{'them' if len(foreign) > 1 else 'it'} from design.filled[{name!r}]",
                fix={"chart": name, "set": {}, "unrecord": foreign},
                why=f"the chart is now a {chart.type}", release=True)


# -- the time axis --------------------------------------------------------------

_LABEL_FORMATS = {"P1M": ("%b %Y", "Sep 2026"), "P1Y": ("%Y", "2026"),
                  "P1D": ("%d %b", "08 Sep"), "P1W": ("%d %b", "08 Sep")}


def _grain(c) -> tuple[str, str]:
    """(grain key, how to name it): the effective grain, with Superset's week-anchor
    spellings read as P1W. An omitted grain compiles to the P1D default, so it is
    read as P1D too: a written P1D and an omitted grain are the same chart."""
    grain = c.time_grain or DEFAULT_TIME_GRAIN
    key = "P1W" if "P1W" in grain else grain
    return key, (grain if c.time_grain else f"{grain} (the default)")


@_fill("default.x-label-format", "x_label_format", TIMESERIES_TYPES,
       "a time axis labels its points in its grain's own format ('Sep 2026' by month); "
       "day and week labels only over a year or less",
       superset="smart_date", superset_text="Superset's adaptive labels",
       override="write x_label_format yourself, e.g. '%b'")
def _x_label_format(ctx: RuleContext, c):
    key, label = _grain(c)
    if key not in _LABEL_FORMATS:
        return None, f"grain {label} has no one label format"
    fmt, example = _LABEL_FORMATS[key]
    if key in ("P1D", "P1W"):
        # '%d %b' has no year: past a year the same label names two dates.
        limit = ctx.params.day_label_max_span_days
        span = _span_days(c.time_range) if c.time_range else None
        if span is None or span > limit:
            why = ("the chart has no time_range" if not c.time_range
                   else f"time_range {c.time_range!r} "
                   + ("is not a span the brain can read" if span is None
                      else f"spans more than {limit} days"))
            return None, f"day labels drop the year, and {why}"
    return fmt, f"grain {label} reads as {example!r}"


_UNITS = {"PT1H": "hour", "P1D": "day", "P1W": "week", "P1M": "month",
          "P3M": "quarter", "P1Y": "year"}


@_fill("default.compare-suffix", "compare_suffix", {"big_number_trend"},
       "a trendline KPI's change says what it compares against ('vs previous month')",
       superset="", superset_text="Superset's bare percentage",
       override="write compare_suffix yourself, e.g. 'vs last month'")
def _compare_suffix(ctx: RuleContext, c):
    if c.compare_lag is None:
        return None, "no compare_lag, so no change is shown"
    key, label = _grain(c)
    unit = _UNITS.get(key)
    if unit is None:
        return None, f"grain {label} has no plain name"
    lag = c.compare_lag
    text = f"vs previous {unit}" if lag == 1 else f"vs {lag} {unit}s earlier"
    return text, f"compare_lag {lag} at grain {label}"


# -- number formats -------------------------------------------------------------

_COUNTS = ("COUNT", "COUNT_DISTINCT")
# Pivot aggregations that keep a count a whole number.
_WHOLE_AGGREGATES = {"Sum", "Count", "Count Unique Values", "Minimum", "Maximum", "First", "Last"}
COUNT_FORMAT_TYPES = (KPI_TYPES | TIMESERIES_TYPES
                      | {"bar", "pie", "pivot_table", "heatmap", "funnel", "treemap"})


@_fill("default.count-format", "number_format", COUNT_FORMAT_TYPES,
       "counts read as whole numbers with thousands separators (',.0f')",
       superset="SMART_NUMBER", superset_text="Superset's SMART_NUMBER ('12.3k')",
       override="write number_format yourself, e.g. '.3s'")
def _count_format(ctx: RuleContext, c):
    shown = [c.metric] if hasattr(c, "metric") else list(c.metrics)
    other = [m for m in shown if (parse_metric(m) or {}).get("aggregate") not in _COUNTS]
    if other:
        return None, f"{other} {'is' if len(other) == 1 else 'are'} not COUNT or COUNT_DISTINCT"
    if getattr(c, "contribution", None):
        return None, "contribution plots shares, not counts"
    if getattr(c, "stack", None) == "expand":
        return None, "a 100% stack plots shares, not counts"
    if c.type == "pivot_table" and c.aggregate_function not in _WHOLE_AGGREGATES:
        return None, f"aggregate_function {c.aggregate_function!r} can leave fractions"
    return ",.0f", "a count is a whole number: '12,345', not '12.3k'"


# -- tables -----------------------------------------------------------------------

# Identifier columns, by name: a whole token id, code, year, zip, zipcode or
# postcode at the end of the name (order_id, country_code, fiscal_year, zip).
# Deliberately narrow: 'uuid', 'yearly_total' and 'zip_count' do not match.
ID_LIKE = re.compile(r"(?:^|_)(?:id|code|year|zip|zipcode|postcode)$", re.I)


@_fill("default.cell-bars", "cell_bars", {"table"},
       "a raw table with id, code, year or zip columns draws no cell bars (a bar behind "
       "an identifier reads as an amount)",
       superset=True, superset_text="Superset's bars behind every number",
       override="write cell_bars: true to keep the bars")
def _cell_bars(ctx: RuleContext, c):
    if not c.columns:
        return None, "an aggregate table draws bars on its metrics only, never on a dimension"
    hits = [col for col in c.columns if ID_LIKE.search(col)]
    ds = ctx.dataset_for(c)
    if ds is not None and ds.column_types:
        # With types (advise --profile), only numeric columns draw a bar at all.
        hits = [h for h in hits if ds.column_types.get(h, 0) == 0]
    if not hits:
        return None, "no id, code, year or zip column"
    return False, f"{hits} identify rows rather than measure them; bars would rank them"


@_fill("default.page-length", "page_length", {"table"},
       "a table whose row_limit outgrows its panel pages by the rows that fit beside "
       "its page controls",
       superset=None, superset_text="Superset's own paging (200 rows once past 5,000 cells)",
       override="write page_length yourself (0 shows every row on one page)")
def _page_length(ctx: RuleContext, c):
    if not ctx.written(c, "row_limit"):
        return None, "row_limit is not set, so Superset pages on its own"
    h = ctx.height(c.name)
    # The one grid model size.table-window reads (spec.py): a page fills the whole rows
    # that fit beside the page-size bar and the pager, so a fill can never make that
    # rule ask for more height.
    fits = math.floor(grid_rows_visible(h, table_header_units(controls=c.search_box)))
    if c.row_limit <= fits:
        return None, f"all {c.row_limit} rows fit at height {h:g}"
    page = math.floor(grid_rows_visible(h, table_header_units(controls=True, pager=True)))
    if page < ctx.params.page_min_rows:
        return None, f"height {h:g} fits {page} rows beside a pager, too few to page; raise the height"
    return page, (f"row_limit {c.row_limit} at height {h:g}: {fits} rows fit on one page, "
                  f"{page} beside the page controls")


@_fill("default.search-box", "search_box", {"table"},
       "a raw table of more than ~20 rows gets a search box, when its rows still fit "
       "beside it (the 20 is judgement)",
       superset=False, superset_text="no search box",
       override="write search_box: false")
def _search_box(ctx: RuleContext, c):
    if not c.columns:
        return None, "an aggregate table (the fill is for raw tables)"
    if not ctx.written(c, "row_limit"):
        return None, "row_limit is not set"
    n = ctx.params.search_min_rows
    if c.row_limit <= n:
        return None, f"row_limit {c.row_limit} is {n} rows or fewer"
    if not c.page_length:
        # A paged table already draws the bar the search box sits in. On one page, the
        # bar takes room from the rows, and must not push any behind the scrollbar.
        h = ctx.height(c.name)
        fits = math.floor(grid_rows_visible(h, table_header_units(controls=True)))
        if c.row_limit > fits:
            return None, (f"{c.row_limit} rows on one page at height {h:g}: a search bar "
                          f"would leave room for {fits}; page the table or raise the height")
    return True, (f"up to {c.row_limit} raw rows: searching beats scrolling "
                  f"(the {n}-row threshold is judgement)")


# -- legends and labels -------------------------------------------------------------


@_fill("default.single-series-legend", "show_legend", TIMESERIES_TYPES | {"bar"},
       "a single series named by the chart or y-axis title needs no legend",
       superset=True, superset_text="Superset's legend",
       override="write show_legend: true")
def _single_series_legend(ctx: RuleContext, c):
    if len(c.metrics) != 1 or c.groupby or c.series_limit is not None:
        return None, "more than one series, which the legend tells apart"
    if c.annotations:
        return None, "the legend names its goal lines"
    if ctx.written(c, "legend_position") or ctx.written(c, "legend_type"):
        return None, "legend_position or legend_type is set, so the legend is placed on purpose"
    label = metric_label(c.metrics[0])
    title = c.display_name or c.name
    where = [what for what, text in (("title", title), ("y-axis title", c.y_axis_title))
             if text and len(label) >= 3 and label.lower() in text.lower()]
    if not where:
        return None, f"no title names {label!r}, so the legend is its only label"
    return False, f"one series, {label!r}, already named by the {where[0]}"


# A horizontal bar's value label sits beside it, so the panel's height spaces the
# labels, not its width. Measured (docs/DESIGN-BRAIN.md, "Calibration"): the chart
# spends 180 px on its title, legend and axis (5.0.0; 164 px on 4.1.4 and 6.1.0), and
# a label is 14.25 px tall (12.25 px on 6.1.0), so a bar needs 16.5 px to keep its
# label clear of the next one. 12 bars at 8 units overlapped on 4.1.4 and 5.0.0.
HBAR_FRAME_UNITS = 4.5
HBAR_LABEL_UNITS = 0.4125


@_fill("default.value-labels", "show_value", {"bar"},
       "few bars carry their values: <= 12 bars, on a panel >= 6/12 wide (vertical) or "
       "tall enough to space the labels (horizontal)",
       superset=False, superset_text="no values on the bars",
       override="write show_value: false")
def _value_labels(ctx: RuleContext, c):
    if len(c.metrics) != 1 or c.groupby:
        return None, "more than one series, so labels would crowd"
    if c.contribution:
        return None, "contribution plots shares"
    if not ctx.written(c, "row_limit"):
        return None, "row_limit is not set, so the number of bars is unknown"
    most, min_w = ctx.params.value_label_max_bars, ctx.params.value_label_min_width
    n = c.row_limit
    if n > most:
        return None, f"up to {n} bars, more than {most}"
    if c.orientation == "horizontal":
        h = ctx.height(c.name)
        need = HBAR_FRAME_UNITS + HBAR_LABEL_UNITS * n
        if h < need:
            return None, (f"{n} horizontal bars at height {h:g}: their labels need "
                          f"~{math.ceil(need)} units to clear each other")
        return True, f"at most {n} bars at height {h:g}: each value reads without the axis"
    w = ctx.width(c.name)
    if w < min_w:
        return None, f"{w}/12 wide, narrower than {min_w}/12"
    return True, f"at most {n} bars at {w}/12 wide: each value reads without the axis"


# -- heatmaps -----------------------------------------------------------------------

# A heatmap's y labels sit inside its grid (containLabel), whose left edge is the card's
# edge while left_margin is Superset's 'auto' (Heatmap/transformProps.ts 6.1.0 :354-357).
# Seen on 6.1.0: the longest label is drawn wider than the room the grid gave it and
# loses its first letters there ('rucks and Buses', a 95 px label; 'ustralian Gift
# Network, Co', 150 px). 8 px cleared the first and 16 px both; 4.1.4 and 5.0.0 drew
# them whole. Its own spec field is the only way: on 6.1.0 Heatmap.tsx:25 renders
# <Echart> without vizType, so a theme's per-chart-type overrides never reach it.
HEATMAP_LABEL_ROOM = 16


@_fill("default.heatmap-label-room", "left_margin", {"heatmap"},
       "a heatmap keeps 16 px left of its y labels, where Superset 6.1.0 cuts off the "
       "longest one's first letters",
       superset=None, superset_text="no margin (Superset's 'auto')",
       override="write left_margin yourself (0 for none)", since="12")
def _heatmap_label_room(ctx: RuleContext, c):
    return HEATMAP_LABEL_ROOM, ("Superset 6.1.0 cuts the first letters off the longest y "
                                "label at the card's edge; 16 px keeps labels up to ~300 px whole")
