"""Design defaults: the default.* rule family (docs/DESIGN-BRAIN.md sec.16).

A fill is a design default the brain writes INTO the spec, through `advise --fix`
and MCP `fix_spec` only: an explicit field the author reads in the diff and can
edit or take over. Compile, plan and decompile see an ordinary field; nothing here
runs at compile time, so a spec that was never fixed builds the same bytes.

Who owns a field, per chart (design.filled is the record):
- written in the spec and not listed in design.filled: the author's. Never touched.
- listed in design.filled: the brain's. Every --fix recomputes it from the chart as
  it is now (a new height, row_limit, grain or groupby), and removes it when the
  rule no longer applies. The author takes it over by removing it from the list,
  or by editing it to a value this rule never writes (`could_write`): the next
  --fix then drops it from the list and leaves the value alone.
- not written: unset; filled when the rule applies.

Every rule is info severity, fixable, presentation-only (the query is unchanged),
and never writes Superset's own default: that draws nothing new, and an empty
change is not a decision worth a line in the spec.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Callable

from ..spec import DEFAULT_TIME_GRAIN, grid_rows_visible, metric_label, parse_metric
from .model import KPI_TYPES, TIMESERIES_TYPES, Finding, RuleContext, rule
from .rules import _PAGER_ROWS, _span_days


@dataclass(frozen=True)
class Fill:
    rule: str
    field: str
    types: frozenset
    # (ctx, chart) -> (value to fill or None, one-line reason either way)
    decide: Callable[[RuleContext, Any], tuple[Any, str]]
    superset: Any            # Superset's own value for the unset field: never filled
    superset_text: str       # what Superset draws when the field is unset
    could_write: Callable[[Any], bool]  # a value this rule can produce
    override: str            # how the author takes the field over


FILLS: dict[str, Fill] = {}


def _show(v) -> str:
    return "true" if v is True else "false" if v is False else repr(v)


def _findings(ctx: RuleContext, fill: Fill):
    for c in ctx.spec.charts:
        if c.type not in fill.types:
            continue
        field = fill.field
        listed = field in ctx.filled(c.name)
        present = ctx.written(c, field)
        if present and not listed:
            continue  # the author's value
        current = getattr(c, field) if present else None
        if listed and present and not fill.could_write(current):
            yield Finding(
                fill.rule, "info", c.name, ctx.where(c.name),
                f"{field} {_show(current)} is not a value the brain fills, so it is yours "
                f"now; --fix drops {field!r} from design.filled and keeps the value",
                fix={"chart": c.name, "set": {}, "unlist": [field]},
                why="the author edited the filled value; it is theirs now",
            )
            continue
        value, reason = fill.decide(ctx, c)
        if value is not None and value == fill.superset:
            value = None  # never write Superset's own default
        if value is None:
            if listed and present:
                yield Finding(
                    fill.rule, "info", c.name, ctx.where(c.name),
                    f"{field} {_show(current)} was filled and no longer applies ({reason}); "
                    f"--fix removes it, back to {fill.superset_text}",
                    fix={"chart": c.name, "set": {}, "unset": [field], "unlist": [field]},
                    why=f"no longer applies: {reason}",
                )
            elif listed:
                yield Finding(
                    fill.rule, "info", c.name, ctx.where(c.name),
                    f"design.filled lists {field!r}, which the chart no longer holds; "
                    f"--fix drops the entry",
                    fix={"chart": c.name, "set": {}, "unlist": [field]},
                    why="the filled field is gone from the spec",
                )
            continue
        if present and current == value:
            continue  # the fill is up to date
        if present:
            detail = (f"{field} {_show(current)} was filled for the chart as it was; --fix "
                      f"refreshes it to {_show(value)}: {reason}. To keep "
                      f"{_show(current)}, remove {field!r} from design.filled[{c.name!r}]")
        else:
            detail = (f"{field} is unset; --fix fills {_show(value)}: {reason}. "
                      f"To keep {fill.superset_text}, ignore {fill.rule}@{c.name}")
        yield Finding(
            fill.rule, "info", c.name, ctx.where(c.name), detail,
            fix={"chart": c.name, "set": {field: value}, "list": [field]},
            why=reason,
        )


def _fill(rule_id: str, field: str, types, doc: str, *, superset, superset_text: str,
          could_write: Callable[[Any], bool], override: str):
    def deco(decide):
        f = Fill(rule_id, field, frozenset(types), decide, superset, superset_text,
                 could_write, override)
        FILLS[rule_id] = f

        def fn(ctx: RuleContext):
            yield from _findings(ctx, f)

        rule(rule_id, "info", doc, fixable=True, since="5")(fn)
        return decide
    return deco


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
       could_write=lambda v: v in {fmt for fmt, _ in _LABEL_FORMATS.values()},
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
_SUFFIX_RE = re.compile(r"vs (previous (hour|day|week|month|quarter|year)"
                        r"|\d+ (hour|day|week|month|quarter|year)s earlier)")


@_fill("default.compare-suffix", "compare_suffix", {"big_number_trend"},
       "a trendline KPI's change says what it compares against ('vs previous month')",
       superset="", superset_text="Superset's bare percentage",
       could_write=lambda v: isinstance(v, str) and bool(_SUFFIX_RE.fullmatch(v)),
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
       could_write=lambda v: v == ",.0f",
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
       could_write=lambda v: v is False,
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
       "a table whose row_limit outgrows its panel pages by what fits, one row left for "
       "the pager",
       superset=None, superset_text="Superset's own paging (200 rows once past 5,000 cells)",
       could_write=lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 1,
       override="write page_length yourself (0 shows every row on one page)")
def _page_length(ctx: RuleContext, c):
    if not ctx.written(c, "row_limit"):
        return None, "row_limit is not set, so Superset pages on its own"
    h = ctx.height(c.name)
    # The one grid formula size.table-window reads: a page plus its pager fills the
    # whole rows that fit, so a fill can never make that rule ask for more height.
    fits = math.floor(grid_rows_visible(h))
    if c.row_limit <= fits:
        return None, f"all {c.row_limit} rows fit at height {h:g}"
    page = fits - _PAGER_ROWS
    if page < ctx.params.page_min_rows:
        return None, f"height {h:g} fits {fits} rows, too few to page; raise the height"
    return page, (f"row_limit {c.row_limit} at height {h:g}: {fits} rows fit, "
                  f"{page} per page and one for the pager")


@_fill("default.search-box", "search_box", {"table"},
       "a raw table of more than ~20 rows gets a search box (threshold unsourced)",
       superset=False, superset_text="no search box",
       could_write=lambda v: v is True,
       override="write search_box: false")
def _search_box(ctx: RuleContext, c):
    if not c.columns:
        return None, "an aggregate table (the fill is for raw tables)"
    if not ctx.written(c, "row_limit"):
        return None, "row_limit is not set"
    n = ctx.params.search_min_rows
    if c.row_limit <= n:
        return None, f"row_limit {c.row_limit} is {n} rows or fewer"
    return True, (f"up to {c.row_limit} raw rows: searching beats scrolling "
                  f"(the {n}-row threshold is unsourced)")


# -- legends and labels -------------------------------------------------------------


@_fill("default.single-series-legend", "show_legend", TIMESERIES_TYPES | {"bar"},
       "a single series named by the chart or y-axis title needs no legend",
       superset=True, superset_text="Superset's legend",
       could_write=lambda v: v is False,
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


@_fill("default.value-labels", "show_value", {"bar"},
       "few bars on a wide panel carry their values (<= 12 bars, >= 6/12 wide; "
       "thresholds unsourced)",
       superset=False, superset_text="no values on the bars",
       could_write=lambda v: v is True,
       override="write show_value: false")
def _value_labels(ctx: RuleContext, c):
    if len(c.metrics) != 1 or c.groupby:
        return None, "more than one series, so labels would crowd"
    if c.contribution:
        return None, "contribution plots shares"
    if not ctx.written(c, "row_limit"):
        return None, "row_limit is not set, so the number of bars is unknown"
    most, min_w = ctx.params.value_label_max_bars, ctx.params.value_label_min_width
    if c.row_limit > most:
        return None, f"up to {c.row_limit} bars, more than {most}"
    w = ctx.width(c.name)
    if w < min_w:
        return None, f"{w}/12 wide, narrower than {min_w}/12"
    return True, (f"at most {c.row_limit} bars at {w}/12 wide: each value reads without "
                  f"the axis (thresholds {most} bars and {min_w}/12 are unsourced)")
