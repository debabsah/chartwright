"""The dashboard spec: the typed contract the LLM is allowed to emit.

Anything not expressible here does not exist. Validation errors are the only
feedback channel an LLM caller gets; keep messages precise and actionable.

Surface: 17 chart types, per-chart WHERE filters, a dashboard-level native
filter bar (select, time range, numeric range, time grain, time column),
markdown blocks, headers, dividers, and tabs.
"""

from __future__ import annotations

import math
import re
import unicodedata
import uuid as _uuid
from typing import Annotated, Any, ClassVar, Literal, Union

from pydantic import (BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator,
                      model_validator)

GRID_WIDTH = 12
DEFAULT_HEIGHT = {"big_number_total": 4, "big_number_trend": 5, "markdown": 4, "default": 8}
DEFAULT_ROW_LIMIT = {
    "timeseries_line": 10000, "timeseries_bar": 10000, "timeseries_area": 10000,
    "timeseries_scatter": 10000, "bar": 10000, "pie": 100, "table": 1000,
    "pivot_table": 10000, "heatmap": 10000, "histogram": 10000, "funnel": 10,
    "treemap": 100, "mixed": 10000, "waterfall": 10000,
}
DEFAULT_TIME_GRAIN = "P1D"

# How a rendered table or pivot spends spec height. ONE model, read by the design
# critic (size.table-window, size.pivot-window, size.grid-fit), the page-length and
# search-box fills, and apply-time smoke, so offline advice, data-aware advice, the
# fills and the apply warning can never disagree about the same chart. Every consumer
# asks grid_fit() what a chart drawing N body rows needs; they differ only in where N
# comes from (the rows a query returned, a probed count, or the row_limit ceiling).
#
# Measured 2026-10-03 on rendered Superset 4.1.4, 5.0.0 and 6.1.0 (1600 px viewport,
# default theme; docs/DESIGN-BRAIN.md, "Calibration"). Each constant is the largest
# the three releases draw, rounded up, so the model may leave a row empty but never
# counts a row the panel hides. 1 unit = 40 px.
GRID_UNITS_PER_ROW = 0.74      # table body row: 29.4 px at 6.1.0, 27.8 px at 4.1.4 and 5.0.0
GRID_HEADER_UNITS = 2.5        # card title, column header and card padding: 99.4 px / 97.8 px
GRID_CONTROLS_UNITS = 1.05     # the bar above the rows that search_box or any page_length
                               # adds: 41.1 px / 39.1 px (6.1.0 draws 33.1 px for a page size alone)
GRID_PAGER_UNITS = 1.45        # the pager under a table of more than one page: 57.9 px / 42.9 px
GRID_TOTALS_UNITS = 0.74       # show_totals' Summary row: the table foot, which DataTable keeps
                               # out of the scrolling body (6.1.0 plugin-chart-table DataTable/
                               # hooks/useSticky.tsx:183,217); not measured, so one body row
PIVOT_UNITS_PER_ROW = 0.65     # pivot body row: 25.8 px on average (19 rows: 490.1 px), as 6.1.0's
                               # styles give it: 12 px text at line-height 1.4, 4 px padding above
                               # and below and a 1 px rule (react-pivottable/Styles.ts:27,33,113,115)
PIVOT_FRAME_UNITS = 2.55       # pivot card title, margins and padding: 101 px
PIVOT_HEADER_ROW_UNITS = 0.66  # one pivot header row: 26.3 px (pivot_frame counts them)
PIVOT_TOTALS_UNITS = 0.72      # the totals row column_totals pins over the bottom rows: 28.8 px
                               # (sticky at the bottom, Styles.ts:55-59)
PIVOT_HSCROLL_UNITS = 0.45     # the horizontal scrollbar under a pivot whose column dimensions
                               # outgrow the panel: 11 px in Chromium's classic scrollbars, and
                               # Windows draws 17 px; overlay scrollbars (macOS) take none.
                               # 6.1.0 styles its own at 8 px (PivotTableChart.tsx:70-73)
# The metric names: one more attribute of a pivot's rows or columns, which Superset adds
# even for a single metric (6.1.0 plugin-chart-pivot-table PivotTableChart.tsx:98,382-386).
PIVOT_METRIC = "\x00metric"


def grid_units_for_rows(rows: float, header_units: float = GRID_HEADER_UNITS,
                        row_units: float = GRID_UNITS_PER_ROW) -> float:
    """Spec height units needed to render `rows` grid rows without an inner scrollbar."""
    return rows * row_units + header_units


def grid_rows_visible(height: float, header_units: float = GRID_HEADER_UNITS,
                      row_units: float = GRID_UNITS_PER_ROW) -> float:
    """Inverse: grid rows visible at `height` before the inner scrollbar starts."""
    return max(0.0, (height - header_units) / row_units)


def table_header_units(*, controls: bool = False, pager: bool = False,
                       totals: bool = False) -> float:
    """Everything a table draws besides its body rows: card title, column header and
    padding, plus the bar above the rows (`controls`: a search box, or any
    page_length, which draws a page-size picker even on one page), the pager under
    them (`pager`: more rows than one page holds) and the Summary row (`totals`:
    show_totals)."""
    return (GRID_HEADER_UNITS + (GRID_CONTROLS_UNITS if controls else 0)
            + (GRID_PAGER_UNITS if pager else 0) + (GRID_TOTALS_UNITS if totals else 0))


def pivot_axes(chart) -> tuple[list[str], list[str]]:
    """The (row, column) attributes a pivot draws, as 6.1.0's PivotTableChart.tsx:377-386
    builds them: transpose swaps the dimensions, and the metric names (PIVOT_METRIC) are
    one more attribute, on the columns unless metrics_layout is rows; outermost, or
    innermost with combine_metric."""
    rows, cols = ((list(chart.columns), list(chart.rows)) if chart.transpose
                  else (list(chart.rows), list(chart.columns)))
    side = rows if chart.metrics_layout == "rows" else cols
    side.insert(len(side) if chart.combine_metric else 0, PIVOT_METRIC)
    return rows, cols


def pivot_frame(chart) -> tuple[int, bool, bool]:
    """(header rows, horizontal-scrollbar room, totals row) of a pivot as configured.
    6.1.0's TableRenderers.tsx draws a header row per column attribute, and one naming
    the row attributes when there are any (:1418-1423); the totals row with
    column_totals, and always when there are no row attributes (:367, :1429). Column
    dimensions, not metric names, are what can outgrow the panel."""
    rows, cols = pivot_axes(chart)
    return (len(cols) + (1 if rows else 0), any(a != PIVOT_METRIC for a in cols),
            chart.column_totals or not rows)


def pivot_header_units(header_rows: int, *, hscroll: bool = False,
                       totals: bool = False) -> float:
    """Everything a pivot draws besides its body rows: card frame, its header rows,
    room for a horizontal scrollbar (`hscroll`) and the pinned totals row (`totals`)."""
    return (PIVOT_FRAME_UNITS + PIVOT_HEADER_ROW_UNITS * header_rows
            + (PIVOT_HSCROLL_UNITS if hscroll else 0)
            + (PIVOT_TOTALS_UNITS if totals else 0))


def pivot_rows(chart, records: list[dict]) -> tuple[int, int]:
    """(leaf rows, subtotal rows) a pivot draws for its query's records: a body row per
    distinct row key, never per record (the query returns rows x columns records). The
    metric names count as a row attribute when laid out as rows, and row_subtotals adds
    a row per distinct key prefix (6.1.0 react-pivottable/utilities.ts:957-975). With no
    row attribute there is no body row: the pivot is its totals row."""
    rows, _ = pivot_axes(chart)
    if not rows:
        return 0, 0
    labels = [metric_label(m) for m in chart.metrics] if PIVOT_METRIC in rows else [None]
    keys = {tuple(label if a == PIVOT_METRIC else r.get(a) for a in rows)
            for r in records for label in labels}
    subtotals = (len({k[:i] for k in keys for i in range(1, len(rows))})
                 if chart.row_subtotals else 0)
    return len(keys), subtotals


def pivot_rows_from_counts(chart, counts: dict[str, int]) -> tuple[int, int, bool]:
    """(leaf rows, subtotal rows, exact) a pivot draws, from the distinct values of each
    row dimension counted one column at a time (`counts`). Exact with at most one row
    dimension; with more, the combinations number at least the largest count, so the
    leaf rows are a lower bound and subtotals go uncounted (exact False)."""
    rows, _ = pivot_axes(chart)
    if not rows:
        return 0, 0, True
    size = {a: len(chart.metrics) if a == PIVOT_METRIC else counts[a] for a in rows}
    dims = [a for a in rows if a != PIVOT_METRIC]
    if len(dims) > 1:
        per = len(chart.metrics) if PIVOT_METRIC in rows else 1
        return max(size[a] for a in dims) * per, 0, False
    subtotals = (sum(math.prod(size[a] for a in rows[:i]) for i in range(1, len(rows)))
                 if chart.row_subtotals else 0)
    return math.prod(size.values()), subtotals, True


def table_page(chart) -> int | None:
    """Rows on one page of a paged table (page_length > 0), never more than row_limit;
    None when every row is on one page."""
    if not getattr(chart, "page_length", None):
        return None
    return min(chart.page_length, chart.row_limit) if chart.row_limit else chart.page_length


def grid_header(chart, rows: int | None = None) -> tuple[float, float]:
    """(header units, units per body row) of a table or pivot chart as configured,
    drawing `rows` body rows (None: unknown, so a paged table counts its pager)."""
    if chart.type == "pivot_table":
        header_rows, hscroll, totals = pivot_frame(chart)
        return (pivot_header_units(header_rows, hscroll=hscroll, totals=totals),
                PIVOT_UNITS_PER_ROW)
    page = chart.page_length or 0
    return (table_header_units(controls=bool(page) or chart.search_box,
                               pager=bool(page) and (rows is None or rows > page),
                               totals=chart.show_totals),
            GRID_UNITS_PER_ROW)


def grid_fit(chart, rows: int | None) -> tuple[int | None, float, float]:
    """(body rows the panel must hold, header units, units per body row) of a table or
    pivot drawing `rows` body rows: the one estimate that smoke (the rows the query
    returned), size.grid-fit (a probed count), size.pivot-window (what the spec alone
    fixes) and size.table-window (the row_limit ceiling) each compare a height with. A
    paged table holds one page beside its pager. None: unknown (a paged table still
    holds one page)."""
    header, row = grid_header(chart, rows)
    page = table_page(chart) if chart.type == "table" else None
    if page is not None and (rows is None or rows > page):
        rows = page
    return rows, header, row

ADHOC_AGGREGATES = ("SUM", "AVG", "COUNT", "COUNT_DISTINCT", "MIN", "MAX")
_ADHOC_RE = re.compile(r"^(SUM|AVG|COUNT|COUNT_DISTINCT|MIN|MAX)\((.+?)\)(?:\s+AS\s+(.+))?$")
# A custom-SQL metric: SQL(<any SQL expression>) AS <label>. The label is
# required: Superset otherwise labels the series with the (truncated) SQL.
_SQL_METRIC_RE = re.compile(r"^SQL\((.+)\)\s+AS\s+(.+)$", re.S)

# The categorical colour schemes every supported release ships (superset-ui-core
# color/colorSchemes/categorical/*.ts; the same 18 ids at 4.1.4, 5.0.0 and 6.1.0).
# A deployment can register more (EXTRA_CATEGORICAL_COLOR_SCHEMES), so other names
# are accepted and the design brain warns about them (narrative.color-scheme).
SUPERSET_COLOR_SCHEMES = (
    "supersetColors", "supersetAndPresetColors", "presetColors", "bnbColors",
    "d3Category10", "d3Category20", "d3Category20b", "d3Category20c",
    "echarts4Colors", "echarts5Colors", "googleCategory10c", "googleCategory20c",
    "lyftColors", "modernSunset", "colorsOfRainbow", "blueToGreen", "redToYellow",
    "wavesOfBlue",
)

# The RAG hexes Superset's own conditional-formatting picker offers (cell backgrounds).
FORMAT_COLOR_HEX = {"green": "#ACE1C4", "amber": "#FDE380", "red": "#EFA1AA"}
# Text needs darker shades of the same three: each is >= 4.5:1 on white (WCAG AA).
FORMAT_TEXT_HEX = {"green": "#1B7F3B", "amber": "#8A6100", "red": "#B3261E"}
HEX_COLOUR_RE = re.compile(r"#[0-9A-F]{6}", re.IGNORECASE)

FilterOp = Literal["==", "!=", ">", ">=", "<", "<=", "IN", "NOT IN", "LIKE", "IS NULL", "IS NOT NULL"]
_LIST_OPS = ("IN", "NOT IN")
_NULL_OPS = ("IS NULL", "IS NOT NULL")


class DatasetRef(BaseModel):
    """A dataset is referenced, never created. Names alone are not unique
    across databases/schemas, so the reference is a triple."""

    model_config = ConfigDict(extra="forbid")

    database: str = Field(description="Superset database connection name")
    schema_: str | None = Field(
        default=None, alias="schema", description="Schema name; omit if unambiguous"
    )
    table: str = Field(description="Dataset (table) name as registered in Superset")

    def key(self) -> str:
        return f"{self.database}/{self.schema_ or ''}/{self.table}"


def parse_metric(metric: str) -> dict | None:
    """{'aggregate','column','label'} for ad-hoc metrics, None for saved-metric
    names. Optional display label via ``AGG(col) AS Pretty Label`` (AS uppercase).
    A custom-SQL metric, ``SQL(expr) AS Label``, gives {'sql','label'} with
    aggregate and column None."""
    s = _SQL_METRIC_RE.match(metric)
    if s and s.group(1).strip() and s.group(2).strip():
        return {"aggregate": None, "column": None, "sql": s.group(1).strip(),
                "label": s.group(2).strip()}
    m = _ADHOC_RE.match(metric)
    if not m:
        return None
    return {"aggregate": m.group(1), "column": m.group(2).strip(), "label": m.group(3)}


def malformed_sql_metric(metric: str) -> bool:
    """A string that starts like a custom-SQL metric but is not one (no label, empty SQL)."""
    return metric.startswith("SQL(") and (parse_metric(metric) or {}).get("sql") is None


def metric_label(metric: str) -> str:
    """What Superset will display (and key row data by) for this metric string."""
    parsed = parse_metric(metric)
    if parsed and parsed["label"]:
        return parsed["label"]
    return metric


class ChartFilter(BaseModel):
    """A WHERE-clause condition on the chart's dataset: column/op/value, or
    ``sql`` for a custom SQL condition."""

    model_config = ConfigDict(extra="forbid")

    column: str | None = None
    op: FilterOp = "=="
    value: str | int | float | bool | list[str | int | float] | None = None
    sql: str | None = Field(
        default=None,
        description="A custom SQL WHERE condition instead of column/op/value, e.g. "
                    "\"amount > 0 OR status = 'refunded'\". `check` cannot verify the "
                    "columns inside SQL; apply's data check runs the query.",
    )

    @model_validator(mode="after")
    def _value_shape(self) -> "ChartFilter":
        if self.sql is not None:
            if not self.sql.strip():
                raise ValueError("filter sql must be a non-empty SQL condition")
            # op keeps its default: a dumped-and-reloaded sql filter carries it
            if self.column is not None or self.value is not None or self.op != "==":
                raise ValueError("a sql filter takes no column, op or value")
            self.sql = self.sql.strip()
            return self
        if self.column is None:
            raise ValueError("a filter needs a column (or sql)")
        if self.op in _NULL_OPS:
            if self.value is not None:
                raise ValueError(f"filter op {self.op!r} takes no value")
        elif self.op in _LIST_OPS:
            if not isinstance(self.value, list) or not self.value:
                raise ValueError(f"filter op {self.op!r} needs a non-empty list value")
        elif isinstance(self.value, list):
            raise ValueError(f"filter op {self.op!r} takes a scalar value, not a list")
        elif self.value is None:
            raise ValueError(f"filter op {self.op!r} needs a value")
        return self


def _tag_list(tags: list[str], where: str) -> list[str]:
    clean = [t.strip() for t in tags]
    if any(not t for t in clean):
        raise ValueError(f"{where} tags must be non-empty strings")
    if any(":" in t for t in clean):
        # Superset reserves "type:value" names (owner:1, type:chart) for its own tags.
        raise ValueError(f"{where} tags cannot contain ':' (Superset reserves those for its own tags)")
    if len(set(clean)) != len(clean):
        raise ValueError(f"{where} tags must be unique")
    return clean


TAGS_DESCRIPTION = (
    "Superset tags, e.g. [\"finance\", \"weekly\"]. Superset 6.0.0 or later, with the "
    "TAGGING_SYSTEM feature flag on. 4.1.4 and 5.0.0 can't import a bundle that carries "
    "tags, so check, apply and plan refuse the field there before anything is written; "
    "6.0.0 or later without the flag ignores them, so plan keeps reporting them. An apply "
    "replaces the object's tags with this list, so [] removes them all, tags added in the "
    "UI included; omit the field to leave tags alone, in apply and in plan."
)


class _ChartBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, description="Chart title; unique within the dashboard (uuid seed input)")
    dataset: DatasetRef
    filters: list[ChartFilter] = Field(default_factory=list, description="WHERE conditions on this chart only")
    time_range: str | None = Field(
        default=None,
        description='Superset time range for this chart alone, e.g. "Last 30 days"; defaults to '
                    '"No filter". A chart without a time axis of its own is filtered on its '
                    "dataset's main time column, so a dataset without one ignores it.",
    )
    width: int | None = Field(default=None, ge=1, le=GRID_WIDTH)
    height: float | None = Field(
        default=None, ge=1, le=100,
        description="Height in 40px units. Fractional values (0.2 steps = Superset's "
                    "8px grid) are tool-written by `chartwright absorb`; humans write integers.",
    )
    display_name: str | None = Field(
        default=None, min_length=1,
        description="A shorter title shown on this dashboard's chart card (Superset's "
                    "sliceNameOverride); `name` stays the chart's name everywhere else",
    )
    description: str | None = Field(
        default=None, min_length=1,
        description="What the chart shows, e.g. a metric definition; dashboard viewers "
                    "open it with \"Show chart description\" in the chart menu",
    )
    certified_by: str | None = Field(
        default=None, min_length=1, description="Who certified the chart: Superset shows a certified badge")
    certification_details: str | None = Field(
        default=None, min_length=1, description="The certified badge's tooltip text; needs certified_by")
    cache_timeout: int | None = Field(
        default=None, ge=1, description="Seconds Superset caches this chart's query results; omit for the default")
    tags: list[str] | None = Field(default=None, description=TAGS_DESCRIPTION)

    @model_validator(mode="after")
    def _chart_metadata(self) -> "_ChartBase":
        if self.display_name == self.name:
            self.display_name = None  # the card shows `name` anyway; decompile reads it as omitted
        if self.certification_details and not self.certified_by:
            raise ValueError(f"chart {self.name!r}: certification_details needs certified_by")
        if self.tags is not None:
            self.tags = _tag_list(self.tags, f"chart {self.name!r}")
        return self

    def default_height(self) -> int:
        return DEFAULT_HEIGHT.get(self.type, DEFAULT_HEIGHT["default"])  # type: ignore[attr-defined]


LegendPosition = Literal["top", "bottom", "left", "right"]


class _Legend(BaseModel):
    """Where a chart's legend sits, or whether it shows at all. Superset's default is a
    scrolling legend along the top."""

    show_legend: bool = Field(default=True, description="false hides the legend")
    legend_position: LegendPosition | None = Field(
        default=None, description="top (Superset's default), bottom, left or right")
    legend_type: Literal["scroll", "plain"] | None = Field(
        default=None,
        description="scroll (Superset's default: one line with arrows) or plain (every "
                    "entry, wrapped; the panel calls it List)",
    )

    def _check_legend(self) -> None:
        if not self.show_legend and (set_value(self, "legend_position")
                                     or set_value(self, "legend_type")):
            raise ValueError(f"chart {self.name!r}: legend_position and legend_type need "  # type: ignore[attr-defined]
                             "show_legend (the legend is hidden)")


def hex_to_rgb(hex_colour: str) -> dict:
    h = hex_colour.lstrip("#")
    return {"r": int(h[0:2], 16), "g": int(h[2:4], 16), "b": int(h[4:6], 16), "a": 1}


class _ColorSchemeMixin(BaseModel):
    """Charts whose control panel declares color_scheme (every supported release)."""

    color_scheme: str | None = Field(
        default=None, min_length=1,
        description="Categorical colour scheme for this chart's series, e.g. \"bnbColors\"; a "
                    "dashboard color_scheme overrides it on the dashboard. Superset ships "
                    + ", ".join(SUPERSET_COLOR_SCHEMES)
                    + "; other names must be registered by your deployment "
                      "(EXTRA_CATEGORICAL_COLOR_SCHEMES) or Superset uses its default.",
    )


ANNOTATION_STYLES = ("solid", "dashed", "dotted")


class Annotation(BaseModel):
    """A FORMULA annotation layer: a line drawn from a formula in x, e.g. a goal
    line at y = 80. Superset draws it as a series named ``name`` (in the legend
    and tooltip); formula layers have no on-chart label in any release. On a
    horizontal bar the value axis runs across, so the line stands upright at its
    value (transformFormulaAnnotation swaps the point to [y, x] there)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, description="Series name in the legend and tooltip, e.g. \"Goal\"")
    value: float | None = Field(
        default=None,
        description="A line at this value on the value axis, e.g. 80: flat across most charts, "
                    "upright across a horizontal bar (a 1.0x threshold, a 4-hour limit)")
    formula: str | None = Field(
        default=None, min_length=1,
        description="Instead of value: a formula in x, e.g. \"2*x + 10\" (x is the "
                    "x-axis value; epoch milliseconds on a time axis)",
    )
    color: str | None = Field(
        default=None, pattern=r"^#[0-9A-Fa-f]{6}$",
        description="#RRGGBB; omit to take a colour from the chart's scheme",
    )
    style: Literal["solid", "dashed", "dotted"] = "solid"
    width: float = Field(default=1, gt=0, le=20, description="Line width in px")
    opacity: Literal["low", "medium", "high"] | None = Field(
        default=None, description="0.2 / 0.5 / 0.8; omit for opaque")

    @model_validator(mode="after")
    def _value_or_formula(self) -> "Annotation":
        if self.formula is not None:
            text = self.formula.strip()
            try:
                # A formula that is a plain number IS a value: decompile reads it
                # back as one, so keeping it a formula would show as drift in plan.
                number = float(text.split("=", 1)[1] if text.startswith("y") and "=" in text else text)
            except ValueError:
                number = None
            if number is not None and self.value is None:
                self.value, self.formula = number, None
            else:
                self.formula = text
        if (self.value is None) == (self.formula is None):
            raise ValueError(f"annotation {self.name!r}: give exactly one of value or formula")
        if self.value is not None and not math.isfinite(self.value):
            raise ValueError(f"annotation {self.name!r}: value must be a finite number")
        if self.color is not None:
            self.color = self.color.upper()
        return self


class BigNumberChart(_ChartBase):
    type: Literal["big_number_total"]
    metric: str
    subtitle: str | None = None
    number_format: str | None = Field(default=None, description="d3 format string, e.g. ',.0f'")
    date_format: str | None = Field(
        default=None, min_length=1,
        description="Show the number as a date, in a d3 time format, e.g. \"%a %-d %b %Y\" "
                    "(Sat 3 Oct 2026): a freshness tile over MAX(<date column>). A date or "
                    "timestamp metric takes it as it is, and a number is read as epoch "
                    "milliseconds (Superset's Force date format); not with number_format",
    )

    @model_validator(mode="after")
    def _one_format(self) -> "BigNumberChart":
        if self.date_format and self.number_format:
            raise ValueError(f"chart {self.name!r}: date_format shows the number as a date, "
                             "so number_format never applies; keep one")
        return self


class BigNumberTrendChart(_ChartBase):
    type: Literal["big_number_trend"]
    metric: str
    time_column: str
    time_grain: str | None = None
    number_format: str | None = None
    compare_lag: int | None = Field(
        default=None, ge=1,
        description="Compare the latest value with the one this many time-grain steps "
                    "earlier, shown as a percentage change under the number, e.g. 1 at P1M is "
                    "month over month. With rolling_type both values are windows: lag 12 "
                    "on a trailing-12 sum compares this year's 12 months with the 12 before",
    )
    compare_suffix: str | None = Field(
        default=None,
        description='Text after the percentage change, e.g. "vs last month". Leave it unset '
                    "unless asked: `advise --fix` fills it from the grain, compare_lag and "
                    "the rolling window")
    subtitle: str | None = Field(
        default=None,
        description="A line of context under the number (Superset 6.0.0 or later; older "
                    "releases ignore it)",
    )
    trend_color: Literal["green", "amber", "red"] | Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{6}$")] | None = Field(
        default=None,
        description="Colour of the trendline: green, amber or red (the text shades colour "
                    "rules use) or any #RRGGBB; Superset's default is teal #007A87",
    )
    rolling_type: Literal["sum", "mean", "std", "cumsum"] | None = Field(
        default=None,
        description="A rolling window over the trendline, which the number and compare_lag "
                    "then read: sum, mean or std over the last rolling_periods time-grain "
                    "steps, or cumsum, a running total from the first point. sum at P1M "
                    "with rolling_periods 12 is a trailing-12-month total, and compare_lag "
                    "12 compares it with the 12 months before. The chart's time range must "
                    "hold rolling_periods + compare_lag steps, or no change shows. Say the "
                    "window in the title or subtitle: the number is no longer the latest "
                    "step's",
    )
    rolling_periods: int | None = Field(
        default=None, ge=1,
        description="With rolling_type sum, mean or std: the window, in time-grain steps, "
                    "e.g. 12 at P1M for a trailing year",
    )
    rolling_min_periods: int | None = Field(
        default=None, ge=0,
        description="Steps a window needs before the trendline shows it. Omitted, it is "
                    "rolling_periods: the line starts at the first full window, so every "
                    "point (and the number) is a whole trailing window; 0 also draws the "
                    "partial windows of the first steps, as Superset's own default does",
    )
    y_axis_truncate: bool = Field(
        default=False,
        description="Fit the trendline to its values instead of drawing it up from zero "
                    "(Superset's Start y-axis at 0, unticked). A trailing-12-month total "
                    "that moves a few percent is a flat line over a solid block from zero; "
                    "fitted, the line shows the movement. Say so where the reader expects a "
                    "zero baseline",
    )

    @field_validator("trend_color", mode="before")
    @classmethod
    def _trend_colour(cls, v):
        if v is None or v in FORMAT_TEXT_HEX:
            return v
        if not isinstance(v, str) or not HEX_COLOUR_RE.fullmatch(v):
            raise ValueError(f"trend_color must be green, amber, red or #RRGGBB, got {v!r}")
        return v.upper()

    @model_validator(mode="after")
    def _compare(self) -> "BigNumberTrendChart":
        if self.compare_suffix and self.compare_lag is None:
            raise ValueError(f"chart {self.name!r}: compare_suffix needs compare_lag")
        windowed = self.rolling_periods is not None or self.rolling_min_periods is not None
        if windowed and self.rolling_type is None:
            raise ValueError(f"chart {self.name!r}: rolling_periods and rolling_min_periods "
                             "need rolling_type")
        if self.rolling_type == "cumsum" and windowed:
            raise ValueError(f"chart {self.name!r}: rolling_type cumsum is a running total "
                             "from the first point; it takes no rolling_periods or "
                             "rolling_min_periods")
        if self.rolling_type in ("sum", "mean", "std") and self.rolling_periods is None:
            raise ValueError(f"chart {self.name!r}: rolling_type {self.rolling_type} needs "
                             "rolling_periods, the window in time-grain steps (e.g. 12 at "
                             "P1M for a trailing year)")
        if (self.rolling_min_periods is not None and self.rolling_periods is not None
                and self.rolling_min_periods > self.rolling_periods):
            raise ValueError(f"chart {self.name!r}: rolling_min_periods "
                             f"({self.rolling_min_periods}) can't pass rolling_periods "
                             f"({self.rolling_periods}): a window never holds more steps")
        return self

    def trend_rgb(self) -> dict | None:
        if self.trend_color is None:
            return None
        return hex_to_rgb(FORMAT_TEXT_HEX.get(self.trend_color, self.trend_color))


TREND_DEFAULT_HEX = "#007A87"  # Superset's PRIMARY_COLOR {r: 0, g: 122, b: 135}, every release


class _AxisChart(_ChartBase):
    """A chart with an x axis: how its labels read. Superset's default labels a time
    axis adaptively (full month names, January written as the year) at a spacing it
    picks from the width, then drops labels that collide, so 13 months can read
    'September, November, 2026, March' with gaps that differ chart to chart."""

    x_axis_title: str | None = Field(default=None, description="Title under the x axis")
    y_axis_title: str | None = Field(
        default=None, description="Title above the value axis, e.g. its unit")
    y_axis_min: float | None = Field(
        default=None,
        description="Bottom of the value axis. Superset hands the bound to the chart as the "
                    "axis minimum on every release, so it widens the axis or cuts values "
                    "below it off at the edge; omitted, the axis fits the data",
    )
    y_axis_max: float | None = Field(
        default=None,
        description="Top of the value axis, e.g. 1 for a share that can't pass 100 %. Like "
                    "y_axis_min it is the axis maximum on every release: values above it are "
                    "cut off at the edge",
    )
    y_axis_truncate: bool = Field(
        default=False,
        description="Fit the value axis to the data instead of always including zero "
                    "(Superset's Truncate Y Axis); a y_axis_min or y_axis_max wins on its side",
    )
    y_axis_log: bool = Field(default=False, description="A logarithmic value axis")
    x_label_format: str | None = Field(
        default=None,
        description="d3 time format for the labels of a time x axis, e.g. '%b' (Sep); "
                    "omit for Superset's adaptive format. On a timeseries chart, leave it "
                    "unset unless asked: `advise --fix` fills one from the time grain",
    )
    x_label_every: bool = Field(
        default=False,
        description="A label at every x value: every time-grain step, or every category "
                    "(Superset 6.1.0 or later; 6.0.0 takes it on a mixed chart's category axis "
                    "only, and older releases ignore it)",
    )
    x_label_rotation: int | None = Field(
        default=None, ge=-90, le=90,
        description="Rotate the x-axis labels, in degrees, e.g. 45 for long category names",
    )
    annotations: list[Annotation] = Field(
        default_factory=list,
        description="Formula lines over the chart, e.g. a goal: [{\"name\": \"Goal\", \"value\": 80, "
                    "\"style\": \"dashed\"}] (Superset's FORMULA annotation layers); on a "
                    "horizontal bar a value stands upright across the bars",
    )

    @model_validator(mode="after")
    def _annotation_names(self) -> "_AxisChart":
        names = [a.name for a in self.annotations]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"chart {self.name!r}: duplicate annotation names {dupes} (each is a series id)")
        return self

    def _check_y_axis(self) -> None:
        _check_bounds(self.name, "", self.y_axis_min, self.y_axis_max, self.y_axis_log)


def _check_bounds(name: str, suffix: str, lo: float | None, hi: float | None, log: bool) -> None:
    if lo is not None and hi is not None and lo >= hi:
        raise ValueError(f"chart {name!r}: y_axis_min{suffix} ({lo:g}) must be below "
                         f"y_axis_max{suffix} ({hi:g})")
    if log and lo is not None and lo <= 0:
        raise ValueError(f"chart {name!r}: a logarithmic axis starts above zero; "
                         f"y_axis_min{suffix} is {lo:g}")


class _SeriesDisplay(BaseModel):
    """Values on the marks, stacking, 100 % stacks, and a top-N series limit, for the
    charts whose Superset panel has them (line, bar, area, scatter, categorical bar)."""

    show_value: bool = Field(default=False, description="Write each value on its bar or point")
    stack: bool | Literal["stream", "expand"] = Field(
        default=False,
        description="true stacks the series; \"stream\" (line, area, scatter) is a streamgraph "
                    "around a centre line; \"expand\" (area only) stacks to 100 %",
    )
    only_total: bool = Field(
        default=True,
        description="With show_value on a stacked chart, label each stack's total only "
                    "(Superset's default); false labels every segment",
    )
    contribution: Literal["row", "series"] | None = Field(
        default=None,
        description="Plot shares instead of values: \"row\" is each series' share of its x "
                    "value's total (with stack, a 100 % stacked chart); \"series\" is each "
                    "value's share of its own series' total",
    )
    series_limit: int | None = Field(
        default=None, ge=1,
        description="With groupby: keep the top N series, ranked by series_limit_metric "
                    "(default the first metric), largest first",
    )
    series_limit_metric: str | None = Field(
        default=None, description="Metric that ranks the series for series_limit, written like any metric")
    series_limit_ascending: bool = Field(
        default=False, description="Keep the N smallest series instead of the largest")

    def _check_series_display(self, stacks: tuple) -> None:
        name = self.name  # type: ignore[attr-defined]
        if self.stack not in stacks:
            allowed = ", ".join(json_value(s) for s in stacks if s is not False)
            raise ValueError(f"chart {name!r}: stack {json_value(self.stack)} is not available "
                             f"on {self.type}; use {allowed}")  # type: ignore[attr-defined]
        if not self.only_total and not (self.show_value and self.stack):
            raise ValueError(f"chart {name!r}: only_total applies to show_value on a stacked chart")
        if self.series_limit is not None and not self.groupby:  # type: ignore[attr-defined]
            raise ValueError(f"chart {name!r}: series_limit needs groupby (it keeps the top N series)")
        if self.series_limit is None and (self.series_limit_metric or self.series_limit_ascending):
            raise ValueError(f"chart {name!r}: series_limit_metric and series_limit_ascending "
                             "need series_limit")


def json_value(v) -> str:
    return "true" if v is True else "false" if v is False else f'"{v}"'


# The line, bar, area and scatter panels have one number format, "Axis Format"
# (y_axis_format), for the value axis, the values on the marks and the tooltip
# (Timeseries/transformProps.ts at 4.1.4, 5.0.0 and 6.1.0); 6.1.0's x_axis_number_format
# formats a numeric x axis only.
_VALUE_FORMAT = ("d3 format for the value axis, e.g. {example}. Superset uses the same "
                 "format for the values show_value writes and the tooltip; a '~' drops "
                 "trailing zeros, so ',.1~f' labels the axis 0, 5, 10 and a value 12.5")


class _TimeseriesBase(_SeriesDisplay, _Legend, _AxisChart, _ColorSchemeMixin):
    metrics: list[str] = Field(min_length=1)
    time_column: str
    time_grain: str | None = Field(default=None, description="ISO 8601 duration, e.g. P1D, P1W, P1M")
    groupby: str | None = Field(default=None, description="At most one dimension column")
    row_limit: int | None = Field(default=None, ge=1)
    number_format: str | None = Field(default=None, description=_VALUE_FORMAT.format(example="'.1%'"))

    STACKS: ClassVar[tuple] = (False, True, "stream")

    @model_validator(mode="after")
    def _display(self):
        self._check_legend()
        self._check_y_axis()
        self._check_series_display(self.STACKS)
        return self


def _marker_size():
    return Field(default=None, ge=0, le=20, description="Marker size, 0-20 (Superset's default 6)")


class TimeseriesLineChart(_TimeseriesBase):
    type: Literal["timeseries_line"]
    markers: bool = Field(default=False, description="A marker at each point")
    marker_size: int | None = _marker_size()
    area: bool = Field(default=False, description="Fill the area under each line")
    opacity: float | None = Field(
        default=None, ge=0, le=1, description="Opacity of the area fill, 0-1 (Superset's default 0.2)")

    @model_validator(mode="after")
    def _line_style(self) -> "TimeseriesLineChart":
        if set_value(self, "marker_size") is not None and not self.markers:
            raise ValueError(f"chart {self.name!r}: marker_size needs markers")
        if set_value(self, "opacity") is not None and not self.area:
            raise ValueError(f"chart {self.name!r}: opacity is the area fill's; it needs area")
        return self


class TimeseriesBarChart(_TimeseriesBase):
    type: Literal["timeseries_bar"]
    STACKS: ClassVar[tuple] = (False, True)


class TimeseriesAreaChart(_TimeseriesBase):
    type: Literal["timeseries_area"]
    markers: bool = Field(default=False, description="A marker at each point")
    marker_size: int | None = _marker_size()
    opacity: float | None = Field(
        default=None, ge=0, le=1,
        description="Opacity of the area fill, 0-1 (default 0.2). Each area's edge stays "
                    "a full-strength line: Superset's area chart has no line width")
    STACKS: ClassVar[tuple] = (False, True, "stream", "expand")

    @model_validator(mode="after")
    def _check_marker_size(self) -> "TimeseriesAreaChart":
        if set_value(self, "marker_size") is not None and not self.markers:
            raise ValueError(f"chart {self.name!r}: marker_size needs markers")
        return self


class TimeseriesScatterChart(_TimeseriesBase):
    type: Literal["timeseries_scatter"]
    marker_size: int | None = Field(
        default=None, ge=0, le=20, description="Point size, 0-20 (default 6)")


class BarChart(_SeriesDisplay, _Legend, _AxisChart, _ColorSchemeMixin):
    """Categorical bar: any column on the x axis.

    ``orientation: "horizontal"`` draws ranked lists with long labels the
    readable way (bars run left to right, labels get a full line each).
    """

    type: Literal["bar"]
    x_column: str
    metrics: list[str] = Field(min_length=1)
    groupby: str | None = None
    row_limit: int | None = Field(default=None, ge=1)
    orientation: Literal["vertical", "horizontal"] = "vertical"
    number_format: str | None = Field(default=None, description=_VALUE_FORMAT.format(example="',.0f'"))
    category_sort: Literal["asc", "desc"] | None = Field(
        default=None,
        description="Order the bars by their category (x_column) instead of by the first "
                    "metric: \"asc\" reads A to Z or 0 to 9 (hours, ranks, '1-Mon'), left to "
                    "right or top to bottom on a horizontal bar; \"desc\" reverses it. The "
                    "query is ordered by the category too, so a row_limit keeps the first "
                    "categories in that order, not the largest values. With series_limit the query "
                    "stays ordered by the series ranking (Superset has one \"Sort query by\" "
                    "control for both), so there a row_limit keeps the largest values",
    )
    sort_by: str | None = Field(
        default=None, min_length=1,
        description="Rank the bars by something other than the first metric, largest first "
                    "(on the left, or on top of a horizontal bar). \"total\" ranks stacked or "
                    "grouped bars by their sum; a metric, written like any metric, ranks them "
                    "by its value and orders the query by it, so a row_limit keeps the top "
                    "bars by it. The metric need not be drawn: two charts sorted by the same "
                    "one share an order. A metric needs no groupby; with \"total\" a row_limit "
                    "keeps the rows with the largest first metric, so for a top N by total "
                    "write the total as a metric, e.g. \"SQL(SUM(a) + SUM(b)) AS Total\"",
    )

    TOTAL: ClassVar[str] = "total"

    @model_validator(mode="after")
    def _display(self) -> "BarChart":
        self._check_legend()
        self._check_y_axis()
        self._check_series_display((False, True))
        self._check_sort_by()
        return self

    def several_series(self) -> bool:
        return bool(self.groupby) or len(self.metrics) > 1

    def sort_metric(self) -> str | None:
        """The metric sort_by ranks by, or None (unset, or "total")."""
        return self.sort_by if self.sort_by not in (None, self.TOTAL) else None

    def _check_sort_by(self) -> None:
        if self.sort_by is None:
            return
        name = self.name
        if self.category_sort:
            raise ValueError(f"chart {name!r}: set category_sort or sort_by, not both "
                             "(one orders the bars by label, the other by a measure)")
        if self.contribution:
            raise ValueError(f"chart {name!r}: sort_by ranks by values, and contribution plots "
                             "shares (a sort metric would count in each bar's share)")
        if self.sort_by == self.TOTAL:
            if not self.several_series():
                raise ValueError(f"chart {name!r}: sort_by \"total\" ranks several series by "
                                 "their sum; one series already ranks by its metric")
        elif self.groupby:
            # sortOperator.ts skips a chart with a groupby (4.1.4 and 5.0.0 :46, 6.1.0 :45):
            # the plugin then sorts the categories by name or by a sum, min, max or mean
            # of the series (SortSeriesType), never by one metric.
            raise ValueError(f"chart {name!r}: Superset ranks grouped bars by their series "
                             f"only; use sort_by \"total\", or drop the groupby to rank by "
                             f"{self.sort_by!r}")


class PieChart(_Legend, _ChartBase, _ColorSchemeMixin):
    type: Literal["pie"]
    metric: str
    groupby: str
    donut: bool = False
    row_limit: int | None = Field(default=None, ge=1)
    label_type: Literal["key", "value", "percent", "key_value", "key_percent",
                        "key_value_percent", "value_percent"] | None = Field(
        default=None,
        description="What each slice's label shows: key (the category), value, percent, or a "
                    "combination such as key_value; default key_percent",
    )
    number_format: str | None = Field(default=None, description="d3 format for values in labels and tooltips")
    show_total: bool = Field(default=False, description="The total in the middle (best on a donut)")
    labels_outside: bool = Field(default=True, description="false puts the labels on the slices")

    @model_validator(mode="after")
    def _legend(self) -> "PieChart":
        self._check_legend()
        return self


class FormatRule(BaseModel):
    """One solid colour band on one metric: green / amber / red, or any
    #RRGGBB. A pivot colours a cell by its OWN value only, so band a
    normalized metric (e.g. a %-of-goal ratio) when thresholds differ per
    row. A table can read one column and paint another (apply_to): colour a
    number by a status column beside it."""

    model_config = ConfigDict(extra="forbid")

    metric: str = Field(description="Display label of the metric (table: or column) whose value is tested")
    operator: Literal["<", ">", "=", "between"]
    target: float | None = Field(default=None, description="Threshold for <, > or =")
    target_left: float | None = Field(default=None, description="Lower bound for 'between'")
    target_right: float | None = Field(default=None, description="Upper bound for 'between'")
    color: Literal["green", "amber", "red"] | Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{6}$")] = Field(
        description="green, amber or red (Superset's own picker colours; text paint uses a darker "
                    "shade of each), or any #RRGGBB, used as written for cell and text",
    )
    apply_to: str | None = Field(
        default=None,
        description="Table only: the label of the column to paint, or \"row\"; default the metric's own cells",
    )
    paint: Literal["cell", "text"] = Field(
        default="cell", description="Paint the cell background (default) or the text, e.g. an arrow")

    @field_validator("color", mode="before")
    @classmethod
    def _colour(cls, v):
        if not isinstance(v, str):
            raise ValueError(f"color must be green, amber, red or #RRGGBB, got {v!r}")
        if v in FORMAT_COLOR_HEX:
            return v
        if HEX_COLOUR_RE.fullmatch(v):
            return v.upper()
        raise ValueError(f"color must be green, amber, red or #RRGGBB, got {v!r}")

    def paint_hex(self) -> str:
        """The colour Superset paints: a name's shade for this paint, or the hex as written."""
        if self.color in FORMAT_COLOR_HEX:
            return (FORMAT_TEXT_HEX if self.paint == "text" else FORMAT_COLOR_HEX)[self.color]
        return self.color

    @model_validator(mode="after")
    def _target_shape(self) -> "FormatRule":
        if self.operator == "between":
            if self.target is not None or self.target_left is None or self.target_right is None:
                raise ValueError("'between' needs target_left + target_right (and no target)")
        elif self.target is None or self.target_left is not None or self.target_right is not None:
            raise ValueError(f"operator {self.operator!r} needs target (and no target_left/right)")
        return self


class TableChart(_ChartBase):
    type: Literal["table"]
    columns: list[str] | None = Field(default=None, description="Raw-records mode: plain columns")
    metrics: list[str] | None = None
    groupby: list[str] | None = None
    row_limit: int | None = Field(default=None, ge=1)
    sort_by: str | None = Field(
        default=None,
        description="Sort key, DESCENDING (a ranking) unless sort_ascending. Aggregate "
                    "mode: a metric written the same way (it need not be displayed). "
                    "Raw mode: a column name.",
    )
    sort_ascending: bool = Field(default=False, description="Sort sort_by ascending (a fixed row order)")
    conditional_formatting: list[FormatRule] = Field(
        default_factory=list,
        description="Colour rules. A rule reads its metric's value and, with apply_to, "
                    "paints another column or the whole row (Superset 6.1+).",
    )
    hidden: list[str] = Field(
        default_factory=list,
        description="Labels queried but not displayed, e.g. a status only a colour rule reads (Superset 6.0+)",
    )
    number_formats: dict[str, str] = Field(
        default_factory=dict, description="d3 format per label, e.g. {\"Rate\": \".3f\"}",
    )
    cell_bars: bool | None = Field(
        default=None,
        description="Bars behind numeric cells (Superset draws them by default; false for ids, "
                    "years or a column a colour rule already speaks for). `advise --fix` "
                    "fills false on a raw table with id, code, year or zip columns",
    )
    date_format: str | None = Field(default=None, description="strftime for date columns, e.g. '%Y-%m-%d'")
    page_length: int | None = Field(
        default=None, ge=0,
        description="Rows per page, with a pager under the table; 0 shows every row on one "
                    "page. Omitted, Superset pages at 200 rows only once the table passes "
                    "5,000 cells. Leave it unset unless asked: `advise --fix` fills the rows "
                    "that fit the panel when row_limit outgrows it",
    )
    show_totals: bool = Field(
        default=False, description="A totals row under the table (aggregate mode)")
    search_box: bool = Field(
        default=False,
        description="A search box over the table's rows. `advise --fix` fills true on a raw "
                    "table with a row_limit above 20 (search_min_rows in design.yaml), "
                    "when the search bar pushes none of its rows out of the panel")
    column_align: dict[str, Literal["left", "center", "right"]] = Field(
        default_factory=dict,
        description="Text alignment per label, e.g. {\"Region\": \"center\"}; Superset's "
                    "default puts numbers right and text left",
    )
    column_widths: dict[str, Annotated[int, Field(ge=1)]] = Field(
        default_factory=dict,
        description="Minimum width in pixels per label, e.g. {\"Customer\": 220}; a column "
                    "still grows when the table has room",
    )
    column_headers: dict[str, str] = Field(
        default_factory=dict,
        description="Header text per label, e.g. {\"SUM(revenue)\": \"Revenue\"} "
                    "(Superset 6.0.0 or later; older releases show the label)",
    )

    def labels(self) -> list[str]:
        return [metric_label(m) for m in self.metrics or []] + list(self.groupby or []) + list(self.columns or [])

    @model_validator(mode="after")
    def _mode(self) -> "TableChart":
        aggregate = bool(self.metrics or self.groupby)
        raw = bool(self.columns)
        if aggregate and raw:
            raise ValueError("table chart: use either columns (raw mode) or metrics+groupby (aggregate mode), not both")
        if not aggregate and not raw:
            raise ValueError("table chart: provide columns (raw mode) or metrics+groupby (aggregate mode)")
        if self.sort_ascending and not self.sort_by:
            raise ValueError("table chart: sort_ascending needs sort_by")
        labels = set(self.labels())
        named = [(r.metric, "conditional_formatting metric") for r in self.conditional_formatting]
        named += [(r.apply_to, "conditional_formatting apply_to") for r in self.conditional_formatting
                  if r.apply_to not in (None, "row")]
        named += [(h, "hidden") for h in self.hidden] + [(k, "number_formats") for k in self.number_formats]
        named += [(k, attr) for attr in ("column_align", "column_widths", "column_headers")
                  for k in getattr(self, attr)]
        for label, where in named:
            if label not in labels:
                raise ValueError(f"{where} {label!r} is not one of the table's labels {sorted(labels)}")
        if self.show_totals and raw:
            raise ValueError("table chart: show_totals needs aggregate mode (metrics); a raw table has no totals row")
        return self


# Pivot aggregate choices, identical in PivotTable controlPanel.tsx at 4.1.4, 5.0.0, 6.1.0.
PivotAggregate = Literal[
    "Count", "Count Unique Values", "List Unique Values", "Sum", "Average", "Median",
    "Sample Variance", "Sample Standard Deviation", "Minimum", "Maximum", "First", "Last",
    "Sum as Fraction of Total", "Sum as Fraction of Rows", "Sum as Fraction of Columns",
    "Count as Fraction of Total", "Count as Fraction of Rows", "Count as Fraction of Columns",
]
PivotOrder = Literal["a_to_z", "z_to_a", "value_asc", "value_desc"]
PIVOT_ORDER = {"a_to_z": "key_a_to_z", "z_to_a": "key_z_to_a",
               "value_asc": "value_a_to_z", "value_desc": "value_z_to_a"}


class PivotTableChart(_ChartBase):
    type: Literal["pivot_table"]
    rows: list[str] = Field(default_factory=list, description="Dimension columns on pivot rows")
    columns: list[str] = Field(default_factory=list, description="Dimension columns on pivot columns")
    metrics: list[str] = Field(min_length=1)
    row_limit: int | None = Field(default=None, ge=1)
    combine_metric: bool = Field(
        default=False,
        description="Nest metrics under each column-dimension group (column-outer layout)",
    )
    date_format: str | None = Field(
        default=None,
        description="strftime for temporal pivot headers, e.g. '%m/%d/%y'",
    )
    row_totals: bool = Field(
        default=False, description="A total per row, as a column at the right (e.g. the year beside its months)")
    column_totals: bool = Field(default=False, description="A total per column, as a row at the bottom")
    measure_totals: bool = Field(
        default=False,
        description="With several metrics and combine_metric off: a total after each metric's "
                    "block of columns (Superset's column subtotals), where row_totals would add "
                    "the metrics together",
    )
    number_format: str | None = Field(default=None, description="d3 format for the cells and totals, e.g. ',.0f'")
    conditional_formatting: list[FormatRule] = Field(default_factory=list)
    aggregate_function: PivotAggregate = Field(
        default="Sum",
        description="How a cell combines the rows under it, Superset's names: Sum (default), "
                    "Average, Median, Count, Minimum, Maximum, Sum as Fraction of Total, ...",
    )
    row_order: PivotOrder = Field(
        default="a_to_z",
        description="Row order: a_to_z or z_to_a by label, value_asc or value_desc by value",
    )
    column_order: PivotOrder = Field(default="a_to_z", description="Column order, as row_order")
    row_subtotals: bool = Field(
        default=False, description="A subtotal under each group of an outer row dimension")
    transpose: bool = Field(default=False, description="Swap rows and columns")
    metrics_layout: Literal["columns", "rows"] = Field(
        default="columns", description="Lay several metrics out as columns (default) or as rows")

    @model_validator(mode="after")
    def _dims(self) -> "PivotTableChart":
        if not self.rows and not self.columns:
            raise ValueError("pivot_table needs at least one of rows/columns")
        labels = {metric_label(m) for m in self.metrics}
        for rule in self.conditional_formatting:
            if rule.apply_to is not None:
                raise ValueError("conditional_formatting apply_to is table-only: a pivot colours a cell by its own value")
            if rule.metric not in labels:
                raise ValueError(
                    f"conditional_formatting metric {rule.metric!r} is not one of the "
                    f"chart's metric labels {sorted(labels)}"
                )
        return self


# Superset's sequential colour schemes (superset-ui-core color/colorSchemes/sequential,
# common.ts + d3.ts), the same 56 ids at 4.1.4, 5.0.0 and 6.1.0.
SequentialScheme = Literal[
    "blue_white_yellow", "fire", "white_black", "black_white", "dark_blue", "pink_grey",
    "greens", "purples", "oranges", "red_yellow_blue", "brown_white_green",
    "purple_white_green", "superset_seq_1", "superset_seq_2", "superset_div_1",
    "superset_div_2", "preset_seq_1", "preset_seq_2", "preset_div_1", "preset_div_2",
    "echarts_gradient", "deck_gl_heatmap_gradient", "schemeRdBu", "schemeBrBG", "schemePRGn",
    "schemePiYG", "schemePuOr", "schemeRdGy", "schemeRdYlBu", "schemeRdYlGn", "schemeSpectral",
    "schemeBlues", "schemeGreens", "schemeGrays", "schemeOranges", "schemePurples",
    "schemeReds", "schemeViridis", "schemeInferno", "schemeMagma", "schemeWarm", "schemeCool",
    "schemeCubehelixDefault", "schemeBuGn", "schemeBuPu", "schemeGnBu", "schemeOrRd",
    "schemePuBuGn", "schemePuBu", "schemePuRd", "schemeRdPu", "schemeYlGnBu", "schemeYlGn",
    "schemeYlOrBr", "schemeYlOrRd",
]
HEATMAP_DEFAULT_SCHEME = "superset_seq_1"


class HeatmapChart(_ChartBase):
    type: Literal["heatmap"]
    x_column: str
    y_column: str
    metric: str
    row_limit: int | None = Field(default=None, ge=1)
    show_values: bool = Field(default=False, description="Write each cell's value in the cell")
    color_scheme: SequentialScheme | None = Field(
        default=None,
        description="Superset's sequential colour scheme for the cells, e.g. schemeBlues, "
                    "schemeYlOrRd, superset_seq_2; default superset_seq_1",
    )
    number_format: str | None = Field(default=None, description="d3 format for cell values, e.g. ',.0f'")
    show_percentage: bool = Field(
        default=True,
        description="The tooltip shows the cell's share (of what normalize_across names); "
                    "false shows the value only",
    )
    normalize_across: Literal["heatmap", "x", "y"] = Field(
        default="heatmap",
        description="What the colour scale and the tooltip share compare a cell with: the "
                    "whole heatmap (default), its x value's column, or its y value's row",
    )
    show_legend: bool = Field(default=True, description="false hides the colour scale")
    x_order: PivotOrder | None = Field(
        default=None,
        description="The x labels, left to right: a_to_z (the default) or z_to_a by label, "
                    "value_asc or value_desc by the metric",
    )
    y_order: PivotOrder | None = Field(
        default=None,
        description="The y labels, top to bottom: a_to_z puts the first label on top, e.g. "
                    "a cohort triangle's oldest cohort; value_asc or value_desc by the "
                    "metric. Omitted, Superset draws the labels A to Z from the bottom up, "
                    "which reads z_to_a. A value order ranks each label by its total from "
                    "Superset 6.1.0; before, by its largest or smallest cell",
    )
    # Heatmap controlPanel.tsx xscale_interval / yscale_interval (-1 auto, 1-50) and
    # left_margin ('auto' or px, free-form) at 4.1.4, 5.0.0 and 6.1.0.
    x_label_every: int | None = Field(
        default=None, ge=1, le=50,
        description="A label every N x values, counted from the first: 6 on an hour axis "
                    "reads 0, 6, 12, 18, and 1 (or true) labels every value. Omit for "
                    "Superset's automatic spacing, which drops labels that would collide",
    )
    y_label_every: int | None = Field(
        default=None, ge=1, le=50,
        description="As x_label_every, counted up the y axis from its bottom label (ECharts steps from index 0, the bottom): with y_order \"a_to_z\", whose first label is on top, a step of 6 on 24 hours labels 23, 17, 11, 05; leave y_order unset to read 00, 06, 12, 18 upward")
    left_margin: int | None = Field(
        default=None, ge=0, le=200,
        description="Room, in px, left of the y-axis labels. Superset 6.1.0 draws them wider "
                    "than it measures them and cuts the longest label's first letters off "
                    "at the card's edge; 16 clears them. Omit for none. Leave it unset unless "
                    "asked: `advise --fix` fills it",
    )

    @field_validator("x_label_every", "y_label_every", mode="before")
    @classmethod
    def _label_every(cls, v):
        # A bar's x_label_every is true or false; here true is 1 (every value) and
        # false is Superset's automatic spacing, so both spellings read the same.
        return None if v is False else v


class HistogramChart(_ChartBase, _ColorSchemeMixin):
    type: Literal["histogram"]
    column: str
    bins: int = Field(default=10, ge=1, le=200)
    groupby: str | None = None
    row_limit: int | None = Field(default=None, ge=1)
    x_axis_title: str | None = Field(default=None, description="Title under the x axis")
    y_axis_title: str | None = Field(default=None, description="Title of the count axis")


# The funnel's label_type is a numeric enum (EchartsFunnelLabelType, Funnel/types.ts:
# Key=0 ... ValuePercent=6, the same order at 4.1.4, 5.0.0 and 6.1.0).
FUNNEL_LABEL_TYPES = ("key", "value", "percent", "key_value", "key_percent",
                      "key_value_percent", "value_percent")
LabelType = Literal["key", "value", "percent", "key_value", "key_percent",
                    "key_value_percent", "value_percent"]


class FunnelChart(_Legend, _ChartBase, _ColorSchemeMixin):
    type: Literal["funnel"]
    metric: str
    groupby: str
    row_limit: int | None = Field(default=None, ge=1)
    label_type: LabelType | None = Field(
        default=None,
        description="What each stage's label shows: key (the stage, default), value, percent, "
                    "or a combination such as key_value_percent",
    )
    number_format: str | None = Field(default=None, description="d3 format for values in labels and tooltips")

    @model_validator(mode="after")
    def _legend(self) -> "FunnelChart":
        self._check_legend()
        if set_value(self, "legend_type"):
            raise ValueError(f"chart {self.name!r}: Superset's funnel has no legend_type "
                             "(it always scrolls); set legend_position only")
        return self


class TreemapChart(_ChartBase, _ColorSchemeMixin):
    type: Literal["treemap"]
    metric: str
    groupby: list[str] = Field(min_length=1)
    row_limit: int | None = Field(default=None, ge=1)
    label_type: Literal["key", "value", "key_value"] | None = Field(
        default=None, description="What each tile's label shows: key, value, or key_value (default)")
    number_format: str | None = Field(default=None, description="d3 format for values in labels and tooltips")


class MixedSeries(BaseModel):
    """One of a mixed chart's two queries: its metrics, drawn as bars, a line or a
    filled area, on the primary (left) or secondary (right) value axis. No release gives
    a query's line a width or dash of its own (docs/CONTRACTS.md). To draw a reference
    series lighter, such as last year under this year, put it in its own query and give
    it a pale colour with the dashboard's label_colors, or make it an area with a low
    opacity."""

    model_config = ConfigDict(extra="forbid")

    metrics: list[str] = Field(min_length=1)
    kind: Literal["bar", "line", "area"] = Field(
        default="bar",
        description="bar, line, or area: a line with the space under it filled. A line "
                    "draws over an area whichever query holds it, so an area under a line "
                    "takes either order",
    )
    opacity: float | None = Field(
        default=None, ge=0, le=1,
        description="With kind area: the fill's opacity, 0-1 (Superset's default 0.2). "
                    "Superset draws the area's edge as a line in the same colour at full "
                    "strength; at 1 the edge disappears into the fill, a filled area with "
                    "no outline (give it a light colour with dashboard label_colors)",
    )
    axis: Literal["primary", "secondary"] = "primary"
    groupby: str | None = Field(default=None, description="At most one dimension column")
    markers: bool = Field(
        default=False,
        description="A marker at each point (a line over one category draws nothing without them)",
    )
    show_value: bool = Field(default=False, description="Write each value on its bar or point")
    stack: bool = Field(default=False, description="Stack this query's series (needs groupby or several metrics)")
    only_total: bool = Field(
        default=True,
        description="With show_value and stack, label each stack's total only (Superset 6.0.0 or "
                    "later, its default); false labels every segment, as older releases always do",
    )
    series_limit: int | None = Field(
        default=None, ge=1,
        description="With groupby: keep the top N series, ranked by series_limit_metric "
                    "(default the first metric), largest first",
    )
    series_limit_metric: str | None = Field(
        default=None, description="Metric that ranks the series for series_limit")
    series_limit_ascending: bool = Field(
        default=False, description="Keep the N smallest series instead of the largest")

    @model_validator(mode="after")
    def _display(self) -> "MixedSeries":
        if set_value(self, "opacity") is not None and self.kind != "area":
            raise ValueError("opacity is an area's fill; it needs kind \"area\"")
        if not self.only_total and not (self.show_value and self.stack):
            raise ValueError("only_total applies to show_value on a stacked series")
        if self.series_limit is not None and not self.groupby:
            raise ValueError("series_limit needs groupby (it keeps the top N series)")
        if self.series_limit is None and (self.series_limit_metric or self.series_limit_ascending):
            raise ValueError("series_limit_metric and series_limit_ascending need series_limit")
        return self


class MixedChart(_Legend, _AxisChart, _ColorSchemeMixin):
    """Bars and a line on two value axes (Superset's Mixed Chart), e.g. revenue
    as bars with revenue per order as a line, or a filled area under a line (``kind:
    "area"``). ``x_column`` is a time column (bucketed
    by ``time_grain``) or any column (a categorical axis, e.g. by cause). ``a`` and
    ``b`` are the two queries; the chart's own ``filters`` apply to both. The y_axis_*
    fields set the primary (left) axis; their *_secondary twins the right one."""

    type: Literal["mixed"]
    x_column: str
    time_grain: str | None = Field(default=None, description="ISO 8601 duration for a time x axis, e.g. P1M")
    a: MixedSeries
    b: MixedSeries
    row_limit: int | None = Field(default=None, ge=1)
    # MixedTimeseries/transformProps.ts formats each axis, and the tooltip values of the
    # series on it, with that axis's format (:268-282 and :605-631 at 4.1.4, :348-357 and
    # :800-827 at 6.1.0), but query A's value labels with y_axis_format and query B's with
    # y_axis_format_secondary, whichever axis each sits on (:374-380 and :421-427 at 4.1.4,
    # :449-455 and :522-528 at 6.1.0).
    number_format: str | None = Field(
        default=None,
        description="d3 format for the primary axis and the tooltip values of its series; "
                    "Superset also writes query a's show_value labels in it, whichever axis "
                    "a sits on")
    number_format_secondary: str | None = Field(
        default=None,
        description="d3 format for the secondary axis and the tooltip values of its series; "
                    "Superset also writes query b's show_value labels in it, whichever axis "
                    "b sits on")
    y_axis_title_secondary: str | None = Field(default=None, description="Title of the secondary axis")
    y_axis_min_secondary: float | None = Field(default=None, description="Bottom of the secondary axis")
    y_axis_max_secondary: float | None = Field(default=None, description="Top of the secondary axis")
    y_axis_log_secondary: bool = Field(default=False, description="A logarithmic secondary axis")

    @model_validator(mode="after")
    def _display(self) -> "MixedChart":
        self._check_legend()
        self._check_y_axis()
        _check_bounds(self.name, "_secondary", self.y_axis_min_secondary,
                      self.y_axis_max_secondary, self.y_axis_log_secondary)
        return self


# Superset's own bar colours for a waterfall: the colour pickers' defaults in
# Waterfall/controlPanel.tsx (4.1.4 and 5.0.0 :72, :81, :90; 6.1.0 :78, :108, :150),
# the same three in transformProps.ts.
WATERFALL_DEFAULT_HEX = {"increase_color": "#5AC189", "decrease_color": "#E04355",
                         "total_color": "#666666"}
# x_label_rotation -> the waterfall's and box plot's x_ticks_layout choices ("auto"
# is Superset's default: no rotation set).
TICK_LAYOUTS = {0: "flat", 45: "45°", 90: "90°"}
# A bridge's closing total is named by its own order key (compiler.steps_order_sql), and
# every step's key sorts before it: keys are '0' and three digits, so the name must
# start with a letter or a digit 1-9, which every common collation sorts after '0'.
CLOSING_NAME_RE = re.compile(r"^(?:[^\W\d_]|[1-9])")
# With an opening total, the opening is named by its own key too, and the steps' keys
# are the opening's name and a number (OPENING_KEY_GAP, then three digits), so the
# opening must sort first and the closing after the last key.
OPENING_KEY_GAP = " "
OPENING_OTHER_KEY = " 9999"   # after every step's key: a value steps doesn't list
BRIDGE_TOTAL = "Total"        # the breakdown both totals of an opened bridge carry


def _letters(text: str) -> str:
    """The letters and digits of a name, case and accents folded: what the linguistic
    collations (en_US.UTF-8 and the like) compare first, skipping spaces and punctuation."""
    folded = unicodedata.normalize("NFKD", text.casefold())
    return "".join(ch for ch in folded if ch.isalnum())


def sorts_before(a: str, b: str) -> bool:
    """a sorts before b in a byte-order (C, binary) collation and in a linguistic one."""
    return a < b and _letters(a) < _letters(b)


def _named_colour(field: str, v):
    """green, amber or red (the text shades colour rules use) or #RRGGBB, upper-cased."""
    if v is None or v in FORMAT_TEXT_HEX:
        return v
    if not isinstance(v, str) or not HEX_COLOUR_RE.fullmatch(v):
        raise ValueError(f"{field} must be green, amber, red or #RRGGBB, got {v!r}")
    return v.upper()


def named_hex(colour: str) -> str:
    """The #RRGGBB a named or hex colour paints."""
    return FORMAT_TEXT_HEX.get(colour, colour)


NamedColour = Literal["green", "amber", "red"] | Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{6}$")]


def _bar_colour(what: str, stock: str):
    return Field(
        default=None,
        description=f"Colour of the {what} bars: green, amber or red (the text shades colour "
                    f"rules use) or any #RRGGBB; Superset's default is {stock}",
    )


class WaterfallChart(_ChartBase):
    """Steps that add up to a running total (Superset's Waterfall), e.g. a bridge from
    last year's revenue through each driver to this year's. Each value of ``x_column``
    is a bar rising or falling by ``metric``; Superset adds the closing total.

    Superset draws the steps in the x_column's own order (labels A to Z). For a bridge
    in its own order, ``steps`` lists them and ``closing`` names the dataset row that
    closes it (Superset 6.1.0 or later). The first step rises from zero in the increase
    colour; ``opening`` draws the opening row as a total instead, on a dashboard whose
    theme keeps zero on the value axis, which the plugin otherwise lets float up."""

    type: Literal["waterfall"]
    x_column: str = Field(
        description="The column whose values are the steps, one bar each, e.g. a revenue "
                    "driver; or a time column, one bar per time_grain period")
    metric: str = Field(
        description="The one measure every step adds to the running total, e.g. "
                    "SUM(delta); it must add up, so not AVG, MIN, MAX or COUNT_DISTINCT")
    time_grain: str | None = Field(
        default=None,
        description="For a time x_column: the period of each bar, e.g. P1M; omitted, each "
                    "distinct timestamp is a bar")
    groupby: str | None = Field(
        default=None,
        description="Break each x value down by this column (Superset's Breakdowns): its "
                    "values as steps, then that x value's running total")
    steps: list[str] | None = Field(
        default=None, min_length=1, max_length=1000,
        description="The x_column's values in the order to draw them, e.g. [\"FY2025\", "
                    "\"Price\", \"Volume\", \"Mix\"]: the opening first, or after opening when "
                    "that is set; values not listed are drawn after them, A to Z. Needs "
                    "closing. Superset 6.1.0 or later: older releases draw a running total "
                    "after every step, so check, apply and plan refuse it there")
    closing: str | None = Field(
        default=None, min_length=1,
        description="With steps: the x_column value of the row that closes the bridge, "
                    "e.g. \"FY2026\". Superset draws it last, as the running total in "
                    "total_color, under its own name; the row's own value is not drawn, so a "
                    "closing that doesn't reconcile with the steps shows their sum (apply's "
                    "data check says so). Without opening, it must start with a letter or a "
                    "digit 1-9")
    opening: str | None = Field(
        default=None, min_length=1,
        description="With steps and closing: the x_column value of the row that opens the "
                    "bridge, e.g. \"FY2025\", drawn first as a total in total_color under its "
                    "own name, instead of the first step rising in the increase colour. It "
                    "must sort before closing (FY2025 before FY2026). The dashboard needs a "
                    "theme (dashboard.theme, Superset 6.0.0 or later) whose JSON keeps zero on "
                    "the value axis, {\"echartsOptionsOverridesByChartType\": {\"waterfall\": "
                    "{\"yAxis\": {\"scale\": false}}}}: without it the axis floats up and cuts "
                    "the opening away, so check and apply refuse it")
    total_label: str | None = Field(
        default=None, min_length=1,
        description="Without steps: the name of the closing total Superset adds, e.g. "
                    "\"FY2026\" (Superset 6.1.0 or later; older releases name it Total)")
    increase_color: NamedColour | None = _bar_colour("rising", "green #5AC189")
    decrease_color: NamedColour | None = _bar_colour("falling", "red #E04355")
    total_color: NamedColour | None = _bar_colour("total", "grey #666666")
    increase_label: str | None = Field(
        default=None, min_length=1,
        description="What the legend and tooltip call a rising step, e.g. \"Gain\" "
                    "(Superset 6.1.0 or later; older releases say Increase)")
    decrease_label: str | None = Field(
        default=None, min_length=1,
        description="What the legend and tooltip call a falling step, e.g. \"Loss\" "
                    "(Superset 6.1.0 or later; older releases say Decrease)")
    show_value: bool = Field(default=False, description="Write each step's value on its bar")
    show_legend: bool = Field(
        default=False, description="A legend naming the rising, falling and total bars "
                                   "(Superset's waterfall shows none by default)")
    number_format: str | None = Field(
        default=None, description="d3 format for the value axis, labels and tooltip, e.g. ',.0f'")
    x_axis_title: str | None = Field(default=None, description="Title under the x axis")
    y_axis_title: str | None = Field(default=None, description="Title of the value axis, e.g. its unit")
    x_label_rotation: Literal[0, 45, 90] | None = Field(
        default=None, description="Rotate the step labels: 0, 45 or 90 degrees (Superset's "
                                  "X Tick Layout); omitted, Superset lays them out itself")
    x_label_format: str | None = Field(
        default=None, description="d3 time format for the labels of a time x_column, e.g. '%b %Y'")
    row_limit: int | None = Field(default=None, ge=1)

    @field_validator("increase_color", "decrease_color", "total_color", mode="before")
    @classmethod
    def _colours(cls, v, info):
        return _named_colour(info.field_name, v)

    @model_validator(mode="after")
    def _bridge(self) -> "WaterfallChart":
        name = self.name
        if self.steps is None:
            for field in ("closing", "opening"):
                if getattr(self, field) is not None:
                    raise ValueError(f"chart {name!r}: {field} needs steps (the bridge's order)")
            return self
        dupes = sorted({s for s in self.steps if self.steps.count(s) > 1})
        if dupes:
            raise ValueError(f"chart {name!r}: steps lists {dupes} more than once")
        if any(not s for s in self.steps):
            raise ValueError(f"chart {name!r}: steps must be non-empty values of {self.x_column!r}")
        if self.closing is None:
            raise ValueError(f"chart {name!r}: steps needs closing: the x_column value of "
                             "the row that closes the bridge, drawn as its total")
        for field in ("closing", "opening"):
            if getattr(self, field) in self.steps:
                raise ValueError(f"chart {name!r}: {field} {getattr(self, field)!r} is also a "
                                 "step; name the opening and closing rows apart from steps")
        if self.opening is None:
            if len(self.steps) < 2:
                raise ValueError(f"chart {name!r}: steps needs at least 2 values, the opening "
                                 "and a step (or set opening)")
            if not CLOSING_NAME_RE.match(self.closing):
                raise ValueError(f"chart {name!r}: closing {self.closing!r} must start with a "
                                 "letter or a digit 1-9 (Superset sorts the bars by keys that "
                                 "must come before the closing's name)")
        else:
            if self.opening == self.closing:
                raise ValueError(f"chart {name!r}: opening and closing are the same row")
            if not sorts_before(self.opening + OPENING_OTHER_KEY, self.closing):
                raise ValueError(
                    f"chart {name!r}: opening {self.opening!r} must sort before closing "
                    f"{self.closing!r}, as FY2025 does before FY2026: Superset orders the bars "
                    f"by keys made from the opening's name. Without opening, the first step "
                    f"rises from zero and the names have no such limit")
            if BRIDGE_TOTAL in self.steps:
                raise ValueError(f"chart {name!r}: with opening, no step can be named "
                                 f"{BRIDGE_TOTAL!r}: Superset marks the two totals with it")
        for field in ("time_grain", "groupby", "total_label"):
            if getattr(self, field) is not None:
                why = ("the closing row names the total" if field == "total_label"
                       else "steps orders the values of one categorical x_column")
                raise ValueError(f"chart {self.name!r}: {field} does not go with steps ({why})")
        return self

    def colour_hex(self, field: str) -> str:
        """The #RRGGBB Superset paints for one of the three bar colours."""
        value = getattr(self, field)
        return named_hex(value) if value is not None else WATERFALL_DEFAULT_HEX[field]


Percentiles = Annotated[list[int], Field(min_length=2, max_length=2)]


class BoxPlotChart(_ChartBase, _ColorSchemeMixin):
    """The distribution of a measure in each group (Superset's Box Plot): its median,
    quartiles, whiskers and outliers, e.g. the daily sales of each month. Each row of
    ``distribute_across`` (a day, with time_grain P1D) is one observation of the
    ``metrics``; ``groupby`` draws one box per value, each from its own observations."""

    type: Literal["box_plot"]
    metrics: list[str] = Field(
        min_length=1,
        description="The measure each observation takes, e.g. [\"SUM(sales)\"]: a box per "
                    "group and metric, all on one value axis")
    distribute_across: list[str] = Field(
        min_length=1,
        description="The columns whose rows are the observations (Superset's Distribute "
                    "across), e.g. [\"order_date\"] with time_grain P1D: one observation a day")
    groupby: list[str] = Field(
        default_factory=list,
        description="One box per value of these columns (Superset's Dimensions), e.g. "
                    "[\"month\"]; omitted, one box for every observation")
    time_grain: str | None = Field(
        default=None,
        description="The period of a time column in distribute_across, e.g. P1D; omitted, "
                    "each distinct timestamp is one observation")
    whiskers: Literal["tukey", "min_max"] | Percentiles = Field(
        default="tukey",
        description="How far the whiskers reach: \"tukey\" (to the furthest value within 1.5 "
                    "times the box's height, values beyond drawn as outliers; Superset's "
                    "default), \"min_max\" (the lowest and highest values, no outliers), or two "
                    "percentiles, e.g. [5, 95], with the values beyond them as outliers")
    number_format: str | None = Field(
        default=None, description="d3 format for the value axis and tooltip, e.g. '$,.0f'")
    x_label_format: str | None = Field(
        default=None, description="d3 time format for groups of a time column, e.g. '%b %Y'")
    x_axis_title: str | None = Field(default=None, description="Title under the x axis")
    y_axis_title: str | None = Field(default=None, description="Title above the value axis, e.g. its unit")
    x_label_rotation: Literal[0, 45, 90] | None = Field(
        default=None, description="Rotate the group labels: 0, 45 or 90 degrees (Superset's "
                                  "X Tick Layout); omitted, Superset lays them out itself")
    row_limit: int | None = Field(
        default=None, ge=1,
        description="The most rows the query returns, observations of every group together; "
                    "past it, the boxes miss observations (apply's data check says when it "
                    "is reached). Superset 6.0.0 or later declares it; omitted, 6.x stops at "
                    "10,000 rows and older releases at the server's ROW_LIMIT")

    @model_validator(mode="after")
    def _distribution(self) -> "BoxPlotChart":
        both = sorted(set(self.distribute_across) & set(self.groupby))
        if both:
            raise ValueError(f"chart {self.name!r}: {both} are in both distribute_across and "
                             "groupby; a box would hold one observation")
        for field in ("distribute_across", "groupby", "metrics"):
            values = getattr(self, field)
            if len(set(values)) != len(values):
                raise ValueError(f"chart {self.name!r}: {field} lists a value twice")
        if isinstance(self.whiskers, list):
            lo, hi = self.whiskers
            if not 0 <= lo < hi <= 100:
                raise ValueError(f"chart {self.name!r}: whisker percentiles {self.whiskers} must "
                                 "be two whole numbers from 0 to 100, the lower first, e.g. [5, 95]")
        return self


Chart = Annotated[
    Union[
        BigNumberChart, BigNumberTrendChart, TimeseriesLineChart, TimeseriesBarChart,
        TimeseriesAreaChart, TimeseriesScatterChart, BarChart, PieChart, TableChart,
        PivotTableChart, HeatmapChart, HistogramChart, FunnelChart, TreemapChart,
        MixedChart, WaterfallChart, BoxPlotChart,
    ],
    Field(discriminator="type"),
]

CHART_TYPES = (
    "big_number_total", "big_number_trend", "timeseries_line", "timeseries_bar",
    "timeseries_area", "timeseries_scatter", "bar", "pie", "table", "pivot_table",
    "heatmap", "histogram", "funnel", "treemap", "mixed", "waterfall", "box_plot",
)


class _FilterBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    description: str | None = Field(
        default=None, min_length=1, description="Shown as the filter's tooltip in the filter bar")

    def _check_scope(self, kind: str) -> None:
        charts = getattr(self, "charts", None)
        if charts is not None and not charts:
            raise ValueError(f"{kind} filter {self.name!r}: charts must be omitted or non-empty")


def _charts_scope():
    return Field(default=None, description="Chart names this filter governs; omit for all charts")


def _dependencies():
    return Field(
        default=None,
        description="Names of other select, range or time_range filters whose values narrow this "
                    "filter's list (Superset's \"Values are dependent on other filters\"), e.g. "
                    "[\"Region\"] for a city filter",
    )


class _PreFilterMixin(BaseModel):
    """Superset's "Pre-filter available values": conditions on the filter's own
    dataset that limit the values it lists (select) or its bounds (range)."""

    pre_filter: list[ChartFilter] = Field(
        default_factory=list,
        description="WHERE conditions on the filter's dataset that limit the values it offers, "
                    "e.g. [{\"column\": \"active\", \"value\": true}]",
    )
    time_range: str | None = Field(
        default=None, min_length=1,
        description="Pre-filter by time: only rows in this Superset time range, e.g. \"Last year\"; "
                    "needs time_column",
    )
    time_column: str | None = Field(
        default=None, min_length=1, description="The temporal column time_range applies to")

    def _check_pre_filter(self, kind: str, name: str) -> None:
        if (self.time_range is None) != (self.time_column is None):
            raise ValueError(f"{kind} filter {name!r}: time_range and time_column go together")


class SelectFilter(_FilterBase, _PreFilterMixin):
    """Native filter bar: value picker over one dataset column.

    ``default`` pre-selects values on load (viewers can still change them); a
    relative value such as "This year" keeps a default right as time passes, where
    a literal year would go stale. ``charts`` scopes the filter to the named charts
    only (default: every chart), resolved to slice ids after import, as on range."""

    type: Literal["select"]
    dataset: DatasetRef
    column: str
    multi: bool = True
    default: list[str | int | float] | None = Field(
        default=None, description="Values selected on load; omit for none",
    )
    default_to_first: bool = Field(
        default=False,
        description="Select the first value on load (Superset's own option; with sort_descending "
                    "that is the latest of sortable values like '2026 (this year)', so it never goes stale)",
    )
    sort_descending: bool = Field(default=False, description="List (and pick the first of) the values Z-A")
    sort_metric: str | None = Field(
        default=None, min_length=1,
        description="Order the values by this saved metric of the filter's dataset instead of "
                    "by the values themselves (sort_descending still sets the direction)",
    )
    required: bool = Field(default=False, description="A value must stay selected (no empty filter)")
    search_all_options: bool = Field(
        default=False,
        description="Search every value in the database as the viewer types, not only the "
                    "first values loaded (for high-cardinality columns)",
    )
    inverse_selection: bool = Field(
        default=False, description="Exclude the selected values instead of keeping only them")
    dependencies: list[str] | None = _dependencies()
    charts: list[str] | None = _charts_scope()

    @model_validator(mode="after")
    def _default_and_scope(self) -> "SelectFilter":
        if self.default is not None and not self.default:
            raise ValueError(f"select filter {self.name!r}: default must be omitted or non-empty")
        if self.default and self.default_to_first:
            raise ValueError(f"select filter {self.name!r}: default and default_to_first are exclusive (Superset allows one)")
        if self.default and not self.multi and len(self.default) > 1:
            raise ValueError(f"select filter {self.name!r}: a single-select default takes one value")
        self._check_scope("select")
        self._check_pre_filter("select", self.name)
        return self


class TimeRangeFilter(_FilterBase):
    """Native filter bar: time range picker.

    ``default`` is a Superset time-range expression (``"Last month"``,
    ``"2026-05-01 : 2026-06-01"``) that pre-fills the picker on load; viewers
    can still change it. Omitted, the picker starts empty (Superset shows
    "No filter"). ``charts`` scopes it to the named charts (default: every chart).
    """

    type: Literal["time_range"]
    default: str | None = None
    charts: list[str] | None = _charts_scope()

    @model_validator(mode="after")
    def _scope(self) -> "TimeRangeFilter":
        self._check_scope("time_range")
        return self


class RangeFilter(_FilterBase, _PreFilterMixin):
    """Native filter bar: numeric range on one dataset column (filter_range).

    ``le``/``ge`` set the DEFAULT bound(s) users see on load. One bound gives a
    single-handle slider (Superset's single-value mode); both give a range;
    equal bounds give exact-match mode. ``charts`` scopes the filter to the
    named charts only (default: every chart), resolved to slice ids after
    import, since ids don't exist at compile time."""

    type: Literal["range"]
    dataset: DatasetRef
    column: str
    le: float | None = Field(default=None, description="Default upper bound (x ≤ N)")
    ge: float | None = Field(default=None, description="Default lower bound (x ≥ N)")
    dependencies: list[str] | None = _dependencies()
    charts: list[str] | None = _charts_scope()

    @model_validator(mode="after")
    def _bounds(self) -> "RangeFilter":
        if self.le is not None and self.ge is not None and self.ge > self.le:
            raise ValueError(f"range filter {self.name!r}: ge ({self.ge}) > le ({self.le})")
        self._check_scope("range")
        self._check_pre_filter("range", self.name)
        return self


class TimeGrainFilter(_FilterBase):
    """Native filter bar: a time grain picker (day, week, month...) that re-buckets
    the time axis of the charts in scope. The grains offered are the dataset's
    database's own."""

    type: Literal["time_grain"]
    dataset: DatasetRef
    default: str | None = Field(
        default=None, min_length=1, description="Grain selected on load, an ISO 8601 duration, e.g. P1M")
    required: bool = Field(default=False, description="A grain must stay selected")
    charts: list[str] | None = _charts_scope()

    @model_validator(mode="after")
    def _scope(self) -> "TimeGrainFilter":
        self._check_scope("time_grain")
        return self


class TimeColumnFilter(_FilterBase):
    """Native filter bar: lets viewers pick which of the dataset's temporal columns
    the dashboard's time range applies to (e.g. order date or ship date)."""

    type: Literal["time_column"]
    dataset: DatasetRef
    default: str | None = Field(default=None, min_length=1, description="Temporal column selected on load")
    required: bool = Field(default=False, description="A column must stay selected")
    charts: list[str] | None = _charts_scope()

    @model_validator(mode="after")
    def _scope(self) -> "TimeColumnFilter":
        self._check_scope("time_column")
        return self


DashboardFilter = Annotated[
    Union[SelectFilter, TimeRangeFilter, RangeFilter, TimeGrainFilter, TimeColumnFilter],
    Field(discriminator="type"),
]
# Filter types another filter may depend on (Superset's ALLOW_DEPENDENCIES,
# FiltersConfigModal at 4.1.4/5.0.0, FiltersConfigModal/hooks/useFilterOperations.ts at 6.1.0).
DEPENDENCY_PARENT_TYPES = ("select", "range", "time_range")
# Filter types whose native filter targets a dataset.
DATASET_FILTER_TYPES = ("select", "range", "time_grain", "time_column")


class MarkdownBlock(BaseModel):
    """A text block in the layout (headers, notes)."""

    model_config = ConfigDict(extra="forbid")

    markdown: str = Field(min_length=1)
    width: int | None = Field(default=None, ge=1, le=GRID_WIDTH)
    height: int | float | None = Field(
        default=None, ge=0.2, le=100,
        description="Height units (1 = 40 px) in fifths: 0.2 = 8 px, one Superset grid row (1.6 = 64 px). "
                    "Superset draws a block at least 1 unit tall")

    @field_validator("height")
    @classmethod
    def _whole_grid_rows(cls, h: int | float | None) -> int | float | None:
        if h is not None and abs(h * 5 - round(h * 5)) > 1e-9:
            raise ValueError(f"markdown height {h}: use fifths of a unit (0.2 = one 8 px grid row), e.g. 1.6")
        return h


RowItem = Union[str, MarkdownBlock]


class HeaderBlock(BaseModel):
    """A full-width section title between rows (Superset's Header component).
    Superset places headers beside rows, never inside one, so a header is a
    row of its own in ``rows``: ``{"header": "Revenue", "size": "large"}``."""

    model_config = ConfigDict(extra="forbid")

    header: str = Field(min_length=1, description="The header text")
    size: Literal["small", "medium", "large"] = Field(
        default="medium", description="Text size; medium is what Superset's own new header uses")
    background: Literal["transparent", "white"] = Field(
        default="transparent", description="white (\"Solid\" on Superset 6.1) draws a card behind it")


class DividerBlock(BaseModel):
    """A full-width horizontal rule between rows (Superset's Divider component):
    ``{"divider": true}`` as a row of its own in ``rows``."""

    model_config = ConfigDict(extra="forbid")

    divider: Literal[True]


class StyledRow(BaseModel):
    """A row with a background: ``{"row": ["A", "B"], "background": "white"}``.
    A plain list is a row with Superset's default transparent background."""

    model_config = ConfigDict(extra="forbid")

    row: list[RowItem] = Field(min_length=1)
    background: Literal["white"] = Field(
        description="white (\"Solid\" on Superset 6.1): the row's charts sit on one card")


# One entry of `rows` / `footer`: a row of charts and markdown (a list, or a
# StyledRow), or a full-width header or divider between rows.
Row = Union[list[RowItem], StyledRow, HeaderBlock, DividerBlock]


def row_items(row) -> list | None:
    """The charts and markdown blocks of a layout row (model or raw JSON), or
    None for a header or divider."""
    if isinstance(row, list):
        return row
    if isinstance(row, StyledRow):
        return row.row
    if isinstance(row, dict) and isinstance(row.get("row"), list):
        return row["row"]
    return None


def item_rows(rows) -> list[list]:
    """The rows that hold charts or markdown, headers and dividers skipped."""
    return [items for items in (row_items(r) for r in rows or []) if items is not None]


class _SketchHolder(BaseModel):
    """Shared surface for ASCII layout sketches (see chartwright/sketch.py).

    A sketch draws the layout as text: legend symbols map to chart names (or
    stand for a markdown or header block), character runs become twelfths of
    the 12-column grid, each line adds `line` height units (1 unit = 40 px;
    repeat lines for taller), and vertically stacked symbols compile to
    Superset COLUMN containers. A header is one line: across the whole sketch
    it is a section title between bands, anywhere else it sits in a column.
    '.' is a reserved EMPTY cell, legal trailing-right (a row narrower than
    the page) or at the BOTTOM of a slice/stack (a short chart beside a tall
    one); anywhere else is a named error (Superset packs left and upward)."""

    sketch: list[str] | None = Field(
        default=None, description="ASCII layout: one string per grid line; spaces are cosmetic"
    )
    legend: dict[str, str | MarkdownBlock | HeaderBlock] | None = Field(
        default=None,
        description="Sketch symbol -> chart name, or a block drawn like a chart: "
                    "{\"markdown\": \"...\"} (its height in fifths, optional, wins over the "
                    "drawn one; the drawing sets its width) or {\"header\": \"...\", \"size\": "
                    "..., \"background\": ...} (one line; across the whole sketch it is a "
                    "section title, elsewhere it sits above or below the charts it shares a "
                    "column with)",
    )
    line: int = Field(
        default=2, ge=1, le=20,
        description="Height units per sketch line (1 unit = 40 px)",
    )

    @field_validator("legend")
    @classmethod
    def _blocks_take_drawn_widths(cls, legend):
        for symbol, entry in (legend or {}).items():
            if isinstance(entry, MarkdownBlock) and entry.width is not None:
                raise ValueError(f"legend {symbol!r}: a sketch draws its markdown blocks' "
                                 "widths; drop width and draw the block as wide as it should be")
        return legend

    def parsed_sketch(self):
        from .sketch import BlockRef, parse_sketch_cached

        legend = tuple(sorted(
            (symbol, entry if isinstance(entry, str)
             else BlockRef("markdown" if isinstance(entry, MarkdownBlock) else "header"))
            for symbol, entry in (self.legend or {}).items()))
        return parse_sketch_cached(tuple(self.sketch or ()), legend, self.line)

    def sketch_block(self, block) -> "MarkdownBlock | HeaderBlock":
        """The legend's block for a parsed SketchBlock."""
        return self.legend[block.symbol]

    def sketch_block_height(self, block) -> float:
        """A parsed block's height: a markdown block's own height wins over the drawn
        one, as a chart's does; a header's is what the drawing gives it."""
        entry = self.sketch_block(block)
        return (entry.height if isinstance(entry, MarkdownBlock) and entry.height is not None
                else block.height)


class Tab(_SketchHolder):
    """A dashboard tab: rows, a sketch, or ``tabs`` (sub-tabs, one level deep, each
    with rows or a sketch), e.g. a region tab with a sub-tab per row."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    rows: list[Row] | None = None
    tabs: list["Tab"] | None = Field(
        default=None, description="Sub-tabs (one level), instead of rows / sketch")

    @model_validator(mode="after")
    def _rows_or_sketch(self) -> "Tab":
        if sum([bool(self.rows), bool(self.sketch), bool(self.tabs)]) != 1:
            raise ValueError(f"tab {self.title!r}: provide exactly one of rows / sketch / tabs")
        if self.sketch and not self.legend:
            raise ValueError(f"tab {self.title!r}: a sketch needs a legend")
        seen: set[str] = set()
        for sub in self.tabs or []:
            if sub.tabs:
                raise ValueError(f"tab {self.title!r} > {sub.title!r}: sub-tabs nest one level only")
            if sub.title in seen:
                raise ValueError(f"tab {self.title!r}: duplicate sub-tab title {sub.title!r}")
            seen.add(sub.title)
        return self


SLUG_PATTERN = r"^[a-z0-9][a-z0-9-]*$"
# A classification is a word or a few: letters, digits, spaces, '-', '_' and '.'
# ("internal", "Highly Confidential", "pii-restricted"). It names standard content
# (design.standard_written keys hold it), so it never holds brackets or '='.
CLASSIFICATION_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}$"
LIFECYCLE_STATES = ("active", "deprecated", "sunset")


class Lifecycle(BaseModel):
    """Where a dashboard is in its life. Spec-only: compile, plan and decompile never
    read it, and Superset has no field for it. A standard turns it into what readers see,
    a banner row per state (docs/DESIGN-BRAIN.md sec.18, "Content")."""

    model_config = ConfigDict(extra="forbid")

    state: Literal["active", "deprecated", "sunset"] = Field(
        description="active (in use), deprecated (still works; readers should move to the "
                    "successor) or sunset (retired, kept for reference)")
    successor: str | None = Field(
        default=None, pattern=SLUG_PATTERN,
        description="The slug of the dashboard that replaces this one; deprecated or "
                    "sunset only")
    sunset_date: str | None = Field(
        default=None, pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="The day it is (or was) retired, YYYY-MM-DD; deprecated or sunset only")

    @model_validator(mode="after")
    def _consistent(self) -> "Lifecycle":
        import datetime

        if self.sunset_date is not None:
            try:
                datetime.date.fromisoformat(self.sunset_date)
            except ValueError:
                raise ValueError(f"dashboard lifecycle: sunset_date {self.sunset_date!r} is "
                                 "not a real day; write YYYY-MM-DD") from None
        if self.state == "active" and (self.successor or self.sunset_date):
            raise ValueError("dashboard lifecycle: an active dashboard has no successor or "
                             "sunset_date; set state to deprecated or sunset first")
        return self


SLUG_PATTERN = r"^[a-z0-9][a-z0-9-]*$"  # a dashboard's URL name, as a spec holds it


class AdoptedIdentity(BaseModel):
    """The existing dashboard this spec takes over in place, written by `chartwright
    adopt`. Without it, a spec's dashboard and charts get ids derived from the slug,
    and the tool refuses to touch any dashboard it did not create; with it, apply
    updates THIS dashboard and these charts (same ids, same address)."""

    model_config = ConfigDict(extra="forbid")

    dashboard_uuid: str = Field(description="uuid of the adopted dashboard")
    slug: str = Field(
        min_length=1,
        description="The adopted dashboard's address (URL name), as recorded by adopt. Must equal "
                    "dashboard.slug: copying an adopted spec to make a new dashboard means removing this "
                    "whole block; moving the dashboard means changing its address in Superset and adopting again.")
    charts: dict[str, str] = Field(
        default_factory=dict,
        description="Chart name -> uuid of the adopted chart. Renaming a chart in the spec renames it in "
                    "Superset; rename its key here too.",
    )

    @model_validator(mode="after")
    def _uuids(self) -> "AdoptedIdentity":
        def canonical(label: str, value: str) -> str:
            try:
                return str(_uuid.UUID(value))  # one spelling, as Superset exports it
            except ValueError:
                raise ValueError(f"adopted: {label!r} is not a uuid: {value!r}") from None
        self.dashboard_uuid = canonical("dashboard_uuid", self.dashboard_uuid)
        self.charts = {k: canonical(k, v) for k, v in self.charts.items()}
        values = list(self.charts.values())
        dupes = {v for v in values if values.count(v) > 1}
        if dupes:
            raise ValueError(f"adopted: one chart uuid mapped to several names: {sorted(dupes)}")
        return self


class DashboardMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    slug: str = Field(min_length=1, pattern=SLUG_PATTERN, description="uuid seed input; lowercase kebab-case")
    cross_filters: bool = Field(
        default=False,
        description=(
            "Superset cross-filtering on this dashboard: clicking a value in one chart "
            "filters every other chart whose dataset has that column (across tabs). "
            "Default off, matching what the tool has always written; set true to turn it on. "
            "Spec-owned, so a UI toggle shows up as drift in `plan` and is repaired by `apply`."
        ),
    )
    label_colors: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "A fixed colour per series label on every chart of the dashboard, e.g. "
            "{\"Revenue\": \"#1FA8C9\"}. Superset otherwise assigns colours as the page "
            "loads, so a measure can change colour between charts and visits; these win over "
            "the scheme and the per-view map (6.1.0 applyColors)."
        ),
    )
    css: str | None = Field(
        default=None,
        description=(
            "CSS for the dashboard: the same thing as Superset's own \"Edit CSS\", e.g. "
            "\".dashboard-markdown h1 { color: #1A1A1A; }\". Omitted means none. "
            "Spec-owned, so CSS edited in the UI shows up as drift in `plan` and is "
            "replaced by `apply`."
        ),
    )

    color_scheme: str | None = Field(
        default=None, min_length=1,
        description="Categorical colour scheme for every chart on the dashboard (Superset's "
                    "dashboard \"Color scheme\"), e.g. \"supersetColors\"; it overrides each "
                    "chart's own. Superset ships " + ", ".join(SUPERSET_COLOR_SCHEMES)
                    + "; other names must be registered by your deployment "
                      "(EXTRA_CATEGORICAL_COLOR_SCHEMES) or Superset uses its default.",
    )
    description: str | None = Field(
        default=None, min_length=1, description="The dashboard's description in Superset")
    certified_by: str | None = Field(
        default=None, min_length=1, description="Who certified the dashboard: Superset shows a certified badge")
    certification_details: str | None = Field(
        default=None, min_length=1, description="The certified badge's tooltip text; needs certified_by")
    published: bool = Field(
        default=True,
        description="false keeps the dashboard a draft (Superset's Draft badge; Superset "
                    "lists a draft only for its owners and admins)",
    )
    refresh_frequency: int | None = Field(
        default=None, ge=0,
        description="Reload every chart every N seconds while the dashboard is open, e.g. "
                    "300; omit (or 0) for no automatic refresh",
    )
    filter_bar_orientation: Literal["vertical", "horizontal"] | None = Field(
        default=None,
        description="Where the filter bar sits: vertical (the left side panel, Superset's "
                    "default) or horizontal (above the charts). On 4.1.4 and 5.0.0 horizontal "
                    "needs the HORIZONTAL_FILTER_BAR feature flag; without it Superset shows "
                    "the vertical bar.",
    )
    show_chart_timestamps: bool = Field(
        default=False,
        description="Show each chart's last-queried time on its card. Superset 6.1.0 or "
                    "later: 4.1.4, 5.0.0 and 6.0.0 import it, then refuse to save the "
                    "dashboard's settings (their metadata schema rejects the key), so check, "
                    "apply and plan refuse it there before anything is written.",
    )
    tags: list[str] | None = Field(default=None, description=TAGS_DESCRIPTION)
    owners: list[str] | None = Field(
        default=None,
        description="The dashboard's owners: Superset usernames, or the email address of "
                    "each account, e.g. [\"jdoe\", \"ana@example.com\"]. Use emails on "
                    "4.1.4 and 5.0.0, whose API returns no usernames unless "
                    "FAB_ADD_SECURITY_API is on. apply sets them after the import, keeping "
                    "the account that applies as an owner too (Superset adds it on every "
                    "import, and a non-admin account needs it to import again), and plan "
                    "reports a difference. Omitted, apply leaves "
                    "the live owners alone and plan doesn't compare them. Accounts only: "
                    "Superset's owners are users, not roles.",
    )
    theme: str | None = Field(
        default=None, min_length=1, max_length=250,
        description="A Superset theme for the dashboard, by its name as Superset lists it "
                    "(Settings > Themes), e.g. \"Acme Brand\". Superset 6.0.0 or later: "
                    "check, apply and plan resolve the name on each instance and refuse an "
                    "unknown or ambiguous one before anything is written; 4.1.4 and 5.0.0 "
                    "have no themes and refuse the import, so check refuses the field there. "
                    "Omitted, apply leaves the dashboard's theme as it is (a theme chosen in "
                    "the UI stays) and plan doesn't compare it.",
    )
    lifecycle: Lifecycle | None = Field(
        default=None,
        description="Where the dashboard is in its life: {\"state\": \"deprecated\", "
                    "\"successor\": \"sales-v2\", \"sunset_date\": \"2026-12-31\"}. Spec-only: "
                    "compile and plan ignore it, and Superset has no such field. A standard "
                    "can turn it into a banner row (`standards apply`). Omitted means active.",
    )
    classification: str | None = Field(
        default=None, pattern=CLASSIFICATION_PATTERN,
        description="How sensitive the dashboard is, e.g. \"internal\" or \"confidential\": a "
                    "free word, or one of the values the repository's standard lists. "
                    "Spec-only: compile and plan ignore it. A standard can key footer rows "
                    "off it (`standards apply`).",
    )
    adopted: AdoptedIdentity | None = Field(
        default=None,
        description=(
            "Set by `chartwright adopt`: the existing dashboard (and its charts) this spec "
            "manages in place. Leave it out for dashboards the tool creates."
        ),
    )

    @model_validator(mode="after")
    def _adopted_slug(self) -> "DashboardMeta":
        # The adopted uuid points at one existing dashboard. A copy of the spec under
        # a new slug would still carry it and overwrite the ORIGINAL on apply, so the
        # two must move together; this fails offline, at validate, before any import.
        if self.adopted and self.adopted.slug != self.slug:
            raise ValueError(
                f"dashboard.slug {self.slug!r} differs from dashboard.adopted.slug "
                f"{self.adopted.slug!r}. Making a new dashboard from a copy of this spec? Remove "
                f"dashboard.adopted. Moving the adopted dashboard to a new address? Change it in "
                f"Superset, then run `chartwright adopt` on it again.")
        return self

    @field_validator("owners")
    @classmethod
    def _owner_names(cls, owners: list[str] | None) -> list[str] | None:
        if owners is None:
            return None
        seen: set[str] = set()
        for name in owners:
            if not name or name != name.strip():
                raise ValueError(f"dashboard owners: {name!r} must be a username or an "
                                 "email, without surrounding spaces")
            if name.casefold() in seen:
                raise ValueError(f"dashboard owners: {name!r} is listed twice")
            seen.add(name.casefold())
        return owners

    @field_validator("theme")
    @classmethod
    def _theme_name(cls, v: str | None) -> str | None:
        if v is not None and (not v.strip() or v != v.strip()):
            raise ValueError(f"dashboard theme: {v!r} must be a theme's name as Superset "
                             "lists it, without surrounding spaces")
        return v

    @field_validator("css")
    @classmethod
    def _blank_css_is_none(cls, v: str | None) -> str | None:
        # Blank CSS is no CSS: decompile reads it back as omitted, so keeping
        # "  " here would show as drift in `plan` forever.
        return v if v is not None and v.strip() else None

    @model_validator(mode="after")
    def _hex_colours(self) -> "DashboardMeta":
        bad = {k: v for k, v in self.label_colors.items() if not re.fullmatch(r"#[0-9A-Fa-f]{6}", v)}
        if bad:
            raise ValueError(f"label_colors must be #RRGGBB: {bad}")
        if self.certification_details and not self.certified_by:
            raise ValueError("dashboard certification_details needs certified_by")
        if self.tags is not None:
            self.tags = _tag_list(self.tags, "dashboard")
        if self.lifecycle is not None and self.lifecycle.successor == self.slug:
            raise ValueError(f"dashboard lifecycle: successor {self.slug!r} is this "
                             "dashboard's own slug; name the dashboard that replaces it")
        return self


# The chart fields the design brain may fill with a design default (`advise --fix`,
# docs/DESIGN-BRAIN.md section 16). design.filled may list only these.
BRAIN_FILLABLE_FIELDS = ("cell_bars", "compare_suffix", "date_format", "left_margin", "number_format",
                         "page_length", "search_box", "show_legend", "show_value",
                         "x_label_format")


# design.standard_written: one entry per item of spec content a standard wrote
# (`standards apply`, docs/DESIGN-BRAIN.md sec.18 "Content"). The key names the item;
# rows and CSS blocks are recorded by hash, everything else by value.
STANDARD_NAME = r"[A-Za-z0-9][A-Za-z0-9_-]*"
STANDARD_ITEM_KEYS = {
    "row": re.compile(
        rf"^layout\.(header|footer)\[({STANDARD_NAME})\]"
        r"(?:\[(lifecycle|classification)=([^\]=]+)\])?\[(\d+)\]$"),
    "css": re.compile(rf"^dashboard\.css\[({STANDARD_NAME})\]$"),
    "scalar": re.compile(
        r"^dashboard\.(color_scheme|certified_by|certification_details|theme|classification)$"),
    "label": re.compile(r"^dashboard\.label_colors\[(.+)\]$", re.S),
    "number_format": re.compile(r"^charts\[(.+)\]\.number_format$", re.S),
}
STANDARD_HASH = re.compile(r"^[0-9a-f]{12}$")


def standard_item_kind(key: str) -> tuple[str, re.Match] | None:
    """(kind, match) for a design.standard_written key, or None."""
    for kind, pattern in STANDARD_ITEM_KEYS.items():
        m = pattern.match(key)
        if m:
            return kind, m
    return None


def _check_written_entry(key: str, record) -> None:
    found = standard_item_kind(key)
    if found is None:
        raise ValueError(
            f"design.standard_written: {key!r} names no item a standard writes (layout.header"
            f"[layer][n], layout.footer[layer][n], dashboard.css[layer], dashboard.color_scheme, "
            f"dashboard.certified_by, dashboard.certification_details, dashboard.theme, "
            f"dashboard.classification, dashboard.label_colors"
            f"[label], charts[name].number_format)")
    kind, m = found
    if kind == "row" and m.group(3) and (m.group(1), m.group(3)) not in (
            ("header", "lifecycle"), ("footer", "classification")):
        raise ValueError(f"design.standard_written: {key!r}: a standard keys header rows off "
                         f"the lifecycle and footer rows off the classification")
    if record is None:
        return  # the author's: deleted (or, for a row, changed); never written again
    by = "hash" if kind in ("row", "css") else "value"
    # An item the author took over keeps what was written, marked released: a row's
    # record then follows it when the standard reorders its rows, and a later deletion
    # of the author's value is never undone.
    keys = set(record) if isinstance(record, dict) else None
    released = keys == {"layer", by, "released"}
    if keys != {"layer", by} and not released:
        raise ValueError(f"design.standard_written[{key!r}] must be null or "
                         f"{{\"layer\": ..., \"{by}\": ...}}, as standards apply writes it")
    if released and record["released"] is not True:
        raise ValueError(f"design.standard_written[{key!r}]: released must be true")
    layer = record["layer"]
    if not isinstance(layer, str) or not re.fullmatch(STANDARD_NAME, layer):
        raise ValueError(f"design.standard_written[{key!r}]: layer {layer!r} is not a "
                         f"standard's name")
    if kind in ("row", "css") and layer != m.group(2 if kind == "row" else 1):
        raise ValueError(f"design.standard_written[{key!r}]: layer {layer!r} is not the layer "
                         f"its key names")
    if by == "hash" and not (isinstance(record["hash"], str) and STANDARD_HASH.match(record["hash"])):
        raise ValueError(f"design.standard_written[{key!r}]: hash must be 12 hex digits")
    if by == "value":
        value = record["value"]
        if not isinstance(value, str) or not value:
            raise ValueError(f"design.standard_written[{key!r}]: value must be a non-empty "
                             f"string")
        if kind == "label" and not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
            raise ValueError(f"design.standard_written[{key!r}]: value must be #RRGGBB")


class DesignConfig(BaseModel):
    """Per-dashboard design-brain settings (see docs/DESIGN-BRAIN.md).

    Additive and optional: a spec without this block behaves exactly as
    before. `ignore` entries are rule ids ('size.pie-geometry'), optionally
    scoped to one chart ('size.pie-geometry@Sales by Region'); suppressed
    findings are still reported as ignored, so silence stays visible."""

    model_config = ConfigDict(extra="forbid")

    audience: Literal["executive", "analytical", "operational"] | None = Field(
        default=None, description="Design preset; CLI --audience overrides")
    ignore: list[str] = Field(
        default_factory=list, description="Design rule ids to suppress")
    standard: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
        description="The standard this dashboard follows: the `name` of a file in the "
                    "repository's standards/ directory. advise, check, apply and "
                    "`standards check` apply its rule settings; omitted, the standard "
                    "marked `default: true` applies, if there is one. Compile ignores it.")
    filled: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Written by `chartwright advise --fix`, not by hand: per chart name, each "
                    "field it filled with a design default and the value it wrote. While the "
                    "chart still holds that value, --fix keeps it up to date. Edit or delete "
                    "the field and it is yours: --fix records null here and fills it no more, "
                    "until you delete that entry. Compile ignores this block.",
    )
    standard_written: dict[str, dict[str, Any] | None] = Field(
        default_factory=dict,
        description="Written by `chartwright standards apply`, not by hand: each item of "
                    "spec content a standard wrote (a header or footer row, a CSS block, a "
                    "label colour, a dashboard setting, a chart's number_format), the layer "
                    "that wrote it and its value or hash. While the spec still holds that "
                    "value, apply keeps it up to date. Edit or delete an unlocked item and it "
                    "is yours (null here: never written again, until you delete that entry); "
                    "a locked item stays the standard's. Compile ignores this block.",
    )

    @field_validator("standard_written", mode="before")
    @classmethod
    def _written_entries(cls, written):
        if isinstance(written, dict):
            for key, record in written.items():
                _check_written_entry(key, record)
        return written

    @field_validator("filled", mode="before")
    @classmethod
    def _filled_fields(cls, filled):
        for chart, record in (filled or {}).items() if isinstance(filled, dict) else ():
            if not isinstance(record, dict):
                raise ValueError(f"design.filled[{chart!r}] must map each filled field to the "
                                 f"value --fix wrote, e.g. {{\"page_length\": 8}}")
            bad = sorted(set(record) - set(BRAIN_FILLABLE_FIELDS))
            if bad:
                raise ValueError(f"design.filled[{chart!r}]: {bad} are not fields the design "
                                 f"brain fills (it fills {list(BRAIN_FILLABLE_FIELDS)})")
        return filled


class Layout(_SketchHolder):
    """Flat rows, tabs, or an ASCII sketch; exactly one. Optionally a header
    and a footer: rows above and below all of it, outside any tab, so a tabbed
    dashboard shows them above and under every tab."""

    model_config = ConfigDict(extra="forbid")

    rows: list[Row] | None = None
    tabs: list[Tab] | None = None
    header: list[Row] | None = Field(
        default=None,
        description="Rows above the rows / tabs / sketch, outside any tab (shown above every "
                    "tab), e.g. a banner or a note on the data")
    footer: list[Row] | None = Field(
        default=None,
        description="Rows below the rows / tabs / sketch, outside any tab (shown under every tab)")

    @model_validator(mode="after")
    def _exactly_one(self) -> "Layout":
        given = [bool(self.rows), bool(self.tabs), bool(self.sketch)]
        if sum(given) != 1:
            raise ValueError("layout: provide exactly one of rows / tabs / sketch")
        if self.sketch and not self.legend:
            raise ValueError("layout: a sketch needs a legend")
        seen: set[str] = set()
        for t in self.tabs or []:
            if t.title in seen:
                raise ValueError(
                    f"duplicate tab title {t.title!r}: tab titles must be unique "
                    "(they name tabs in diffs, advice, and the UI)")
            seen.add(t.title)
        return self

    def leaf_tabs(self) -> list[Tab]:
        """The tabs that hold content: each top tab, or its sub-tabs."""
        return [leaf for tab in (self.tabs or []) for leaf in (tab.tabs or [tab])]

    def all_rows(self) -> list[list[RowItem]]:
        """Every row of charts and markdown (headers and dividers skipped)."""
        body = self.rows or [row for tab in self.leaf_tabs() for row in (tab.rows or [])]
        return item_rows([*(self.header or []), *body, *(self.footer or [])])

    def sketch_holders(self) -> list["_SketchHolder"]:
        if self.sketch:
            return [self]
        return [t for t in self.leaf_tabs() if t.sketch]


class DashboardSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec_version: Literal["1"]
    dashboard: DashboardMeta
    charts: list[Chart] = Field(min_length=1)
    filters: list[DashboardFilter] = Field(default_factory=list, description="Native filter bar")
    layout: Layout
    design: DesignConfig | None = Field(default=None, description="Design-brain settings (optional)")

    @model_validator(mode="after")
    def _adopted_names(self) -> "DashboardSpec":
        from . import ids

        adopted = self.dashboard.adopted
        if adopted:
            unknown = sorted(set(adopted.charts) - {c.name for c in self.charts})
            if unknown:
                raise ValueError(
                    f"dashboard.adopted.charts names charts not in the spec: {unknown}; "
                    "rename the key together with its chart, or drop the entry")
            # A chart outside the adopted map gets a uuid derived from its name; if that
            # equals an adopted chart's uuid, two charts would share one identity.
            taken = set(adopted.charts.values())
            clash = sorted(c.name for c in self.charts if c.name not in adopted.charts
                           and str(ids.chart_uuid(self.dashboard.slug, c.name)) in taken)
            if clash:
                raise ValueError(
                    f"charts {clash} would get the same id as an adopted chart; give them "
                    "another name, or run `chartwright adopt` again")
        return self

    @field_validator("charts")
    @classmethod
    def _unique_names(cls, charts: list[Chart]) -> list[Chart]:
        seen: set[str] = set()
        for c in charts:
            if c.name in seen:
                raise ValueError(
                    f"duplicate chart name {c.name!r}: chart names must be unique within a dashboard "
                    "(identical uuid seeds would silently collide)"
                )
            seen.add(c.name)
        return charts

    @model_validator(mode="after")
    def _filled_names_charts(self) -> "DashboardSpec":
        """design.filled holds values its fields take (or null: a fill the author edited
        or deleted). An entry for a chart that was renamed or removed, or a field the
        chart no longer has, is accepted: default.stale-record reports it and
        `advise --fix` drops it, so renaming a chart never stops the spec building."""
        by_name = {c.name: c for c in self.charts}
        for name, record in (self.design.filled if self.design else {}).items():
            chart = by_name.get(name)
            if chart is None:
                continue
            fields = type(chart).model_fields
            for field, value in record.items():
                if value is None or field not in fields:
                    continue
                info = fields[field]
                kind = (Annotated[(info.annotation, *info.metadata)] if info.metadata
                        else info.annotation)
                try:
                    TypeAdapter(kind, config=ConfigDict(strict=True)).validate_python(value)
                except ValidationError as e:
                    raise ValueError(f"design.filled[{name!r}][{field!r}]: {value!r} is not a "
                                     f"value {field} takes ({e.errors()[0]['msg']})") from None
        return self

    @model_validator(mode="after")
    def _one_owner_per_field(self) -> "DashboardSpec":
        """A field has one owner: the brain (design.filled) or a standard
        (design.standard_written), never both, or each would read the other's write as
        the author's edit and neither would own it. An entry for a chart that was
        renamed or removed, or no longer has a number_format, is accepted: `standards
        check` warns about it and `standards apply` drops it."""
        if self.design is None:
            return self
        by_name = {c.name: c for c in self.charts}
        for key in self.design.standard_written:
            kind, m = standard_item_kind(key)
            if kind != "number_format":
                continue
            name = m.group(1)
            chart = by_name.get(name)
            if chart is None or "number_format" not in type(chart).model_fields:
                continue
            if "number_format" in self.design.filled.get(name, {}):
                raise ValueError(
                    f"{name!r}: number_format is recorded in design.filled and in "
                    f"design.standard_written; a field has one owner. standards apply drops "
                    f"the design.filled entry when the standard takes the field")
        return self

    @field_validator("filters")
    @classmethod
    def _unique_filter_names(cls, filters: list[DashboardFilter]) -> list[DashboardFilter]:
        seen: set[str] = set()
        for f in filters:
            if f.name in seen:
                raise ValueError(f"duplicate filter name {f.name!r}")
            seen.add(f.name)
        return filters

    @model_validator(mode="after")
    def _filter_scopes_resolve(self) -> "DashboardSpec":
        names = {c.name for c in self.charts}
        for f in self.filters:
            for target in getattr(f, "charts", None) or []:
                if target not in names:
                    raise ValueError(
                        f"filter {f.name!r} scopes unknown chart {target!r} "
                        f"(spec charts: {sorted(names)})"
                    )
        return self

    @model_validator(mode="after")
    def _filter_dependencies_resolve(self) -> "DashboardSpec":
        by_name = {f.name: f for f in self.filters}
        parents: dict[str, list[str]] = {}
        for f in self.filters:
            deps = getattr(f, "dependencies", None)
            if deps is None:
                continue
            if not deps:
                raise ValueError(f"filter {f.name!r}: dependencies must be omitted or non-empty")
            if len(set(deps)) != len(deps):
                raise ValueError(f"filter {f.name!r}: duplicate dependencies {deps}")
            for parent in deps:
                if parent == f.name:
                    raise ValueError(f"filter {f.name!r} cannot depend on itself")
                if parent not in by_name:
                    raise ValueError(
                        f"filter {f.name!r} depends on unknown filter {parent!r} "
                        f"(spec filters: {sorted(by_name)})")
                if by_name[parent].type not in DEPENDENCY_PARENT_TYPES:
                    raise ValueError(
                        f"filter {f.name!r} depends on {parent!r}, a {by_name[parent].type} filter; "
                        f"Superset lets a filter depend on {', '.join(DEPENDENCY_PARENT_TYPES)} filters only")
            parents[f.name] = list(deps)

        def cycle(name: str, trail: tuple[str, ...]) -> tuple[str, ...] | None:
            if name in trail:
                return (*trail, name)
            for p in parents.get(name, []):
                found = cycle(p, (*trail, name))
                if found:
                    return found
            return None

        for name in parents:
            found = cycle(name, ())
            if found:
                raise ValueError(f"filter dependencies form a cycle: {' -> '.join(found)}")
        return self

    @model_validator(mode="after")
    def _opened_bridges_have_a_theme(self) -> "DashboardSpec":
        # The plugin's value axis floats (scale: true) and has no bounds: only a theme's
        # ECharts override keeps zero on it, so an opening total shows (check verifies
        # the theme's JSON on the instance).
        opened = [c.name for c in self.charts if getattr(c, "opening", None)]
        if opened and self.dashboard.theme is None:
            raise ValueError(
                f"charts {opened} open with a total, which needs dashboard.theme: a theme "
                f"whose JSON keeps zero on a waterfall's value axis, {{\"echartsOptionsOverrides"
                f"ByChartType\": {{\"waterfall\": {{\"yAxis\": {{\"scale\": false}}}}}}}}; "
                f"without it, drop opening and the first step rises from zero")
        return self

    @model_validator(mode="after")
    def _sql_metrics_labelled(self) -> "DashboardSpec":
        for c in self.charts:
            for m in chart_metrics(c):
                if malformed_sql_metric(m):
                    raise ValueError(
                        f"chart {c.name!r}: metric {m!r} looks like a custom-SQL metric but is not "
                        "one; write SQL(<expression>) AS <Label>, e.g. "
                        "\"SQL(100.0 * SUM(a) / NULLIF(SUM(b), 0)) AS Rate\"")
        return self

    @model_validator(mode="after")
    def _layout_consistent(self) -> "DashboardSpec":
        from .sketch import SketchBlock, sketch_items

        by_name = {c.name: c for c in self.charts}
        placed: set[str] = set()
        for i, row in enumerate(self.layout.all_rows()):
            if not row:
                raise ValueError(f"layout row {i} is empty")
            explicit = 0
            implicit = 0
            for item in row:
                if isinstance(item, str):
                    if item not in by_name:
                        raise ValueError(f"layout row {i} references unknown chart {item!r}")
                    if item in placed:
                        raise ValueError(f"chart {item!r} appears more than once in the layout")
                    placed.add(item)
                    w = by_name[item].width
                else:
                    w = item.width
                if w is None:
                    implicit += 1
                else:
                    explicit += w
            if implicit and GRID_WIDTH - explicit < implicit:
                raise ValueError(
                    f"layout row {i}: explicit widths leave {GRID_WIDTH - explicit} units for "
                    f"{implicit} unsized item(s); widths in a row must sum to <= {GRID_WIDTH}"
                )
            if not implicit and explicit > GRID_WIDTH:
                raise ValueError(f"layout row {i}: widths sum to {explicit} > {GRID_WIDTH}; no wrapping, no clamping")
        for holder in self.layout.sketch_holders():
            where = f"tab {holder.title!r}" if isinstance(holder, Tab) else "layout"
            try:
                parsed = holder.parsed_sketch()
            except ValueError as e:
                raise ValueError(f"{where}: {e}") from e
            for row in parsed:
                for child in row.children:
                    for sc in sketch_items(child):
                        if isinstance(sc, SketchBlock):
                            continue  # a markdown or header block: no chart to place
                        if sc.name not in by_name:
                            raise ValueError(f"{where}: legend maps to unknown chart {sc.name!r}")
                        if sc.name in placed:
                            raise ValueError(f"chart {sc.name!r} appears more than once in the layout")
                        placed.add(sc.name)
        missing = set(by_name) - placed
        if missing:
            raise ValueError(f"charts not placed in layout: {sorted(missing)}")
        return self

    # -- geometry -------------------------------------------------------------

    def _row_of(self, want) -> list[RowItem]:
        for row in self.layout.all_rows():
            for item in row:
                if item is want or (isinstance(item, str) and item == want):
                    return row
        raise KeyError(want)

    def _item_width(self, item: RowItem) -> int | None:
        if isinstance(item, str):
            return next(c for c in self.charts if c.name == item).width
        return item.width

    def _sketch_charts(self) -> dict[str, object]:
        """Chart name -> SketchChart for every chart placed via a sketch.
        Sketch geometry is authoritative: widths and heights are drawn."""
        from .sketch import SketchBlock, sketch_items

        out: dict[str, object] = {}
        for holder in self.layout.sketch_holders():
            for row in holder.parsed_sketch():
                for child in row.children:
                    for sc in sketch_items(child):
                        if not isinstance(sc, SketchBlock):
                            out[sc.name] = sc
        return out

    def resolved_item_width(self, item: RowItem) -> int:
        if isinstance(item, str):
            sc = self._sketch_charts().get(item)
            if sc is not None:
                return sc.width
        row = self._row_of(item)
        explicit_total = sum(self._item_width(x) or 0 for x in row)
        implicit = [x for x in row if self._item_width(x) is None]
        own = self._item_width(item)
        if own is not None:
            return own
        # Largest-remainder: implicit widths must SUM to the full remainder or
        # every 5-implicit row renders with a phantom 2-column hole at the
        # right (floor division used to drop it). Earlier items get the +1.
        base, rem = divmod(GRID_WIDTH - explicit_total, len(implicit))
        # Position by IDENTITY for blocks, value only for chart names (unique by
        # validator). Two equal markdown blocks both matched the first one's
        # index under `==`, so both took the +1 and the row summed past 12.
        pos = next(i for i, x in enumerate(implicit)
                   if x is item or (isinstance(item, str) and x == item))
        return max(1, base + (1 if pos < rem else 0))

    # -- identity: derived from the slug, or the adopted dashboard's own ids ----

    def dashboard_uuid(self) -> _uuid.UUID:
        from . import ids

        adopted = self.dashboard.adopted
        return _uuid.UUID(adopted.dashboard_uuid) if adopted else ids.dashboard_uuid(self.dashboard.slug)

    def chart_uuid(self, name: str) -> _uuid.UUID:
        from . import ids

        adopted = self.dashboard.adopted
        if adopted and name in adopted.charts:
            return _uuid.UUID(adopted.charts[name])
        return ids.chart_uuid(self.dashboard.slug, name)

    def resolved_height(self, name: str) -> float:
        chart = next(c for c in self.charts if c.name == name)
        if chart.height is not None:   # explicit height wins (absorb writes here)
            return chart.height
        sc = self._sketch_charts().get(name)
        if sc is not None:
            return sc.height
        return chart.default_height()


def chart_metrics(chart) -> list[str]:
    """Every metric string a chart names: its metric(s), a mixed chart's two
    queries, an aggregate table's or a bar's sort metric and the metric ranking a
    series limit."""
    out: list[str] = []
    if getattr(chart, "metric", None):
        out.append(chart.metric)
    out += list(getattr(chart, "metrics", None) or [])
    if getattr(chart, "series_limit_metric", None):
        out.append(chart.series_limit_metric)
    if chart.type == "mixed":
        out += [*chart.a.metrics, *chart.b.metrics]
        out += [s.series_limit_metric for s in (chart.a, chart.b) if s.series_limit_metric]
    if chart.type == "table" and chart.sort_by and not chart.columns:
        out.append(chart.sort_by)
    if chart.type == "bar" and chart.sort_metric():
        out.append(chart.sort_metric())
    return out


def load_spec(data: dict) -> DashboardSpec:
    return DashboardSpec.model_validate(data)


# -- a written Superset default ---------------------------------------------------------

# Superset's own value for an optional field. An author may write it, and the model
# keeps it as written (model_fields_set and a dump of the spec show it). Superset
# draws the written default exactly like the omitted field, and decompile can't tell
# the two apart, so compile, plan and the spec's own checks read a written default
# as unset (docs/CONTRACTS.md, "A written Superset default").
SUPERSET_DEFAULTS: dict[str, dict[str, object]] = {
    "legend": {"legend_position": "top", "legend_type": "scroll"},  # every chart with a legend
    # Every chart with the field; each is the control's default at 4.1.4, 5.0.0 and 6.1.0.
    # time_range: NO_TIME_RANGE (superset-ui-chart-controls shared-controls/
    # sharedControls.tsx:210, :215 at 6.1.0). number_format: y_axis_format's
    # DEFAULT_NUMBER_FORMAT, which the pivot's valueFormat and the mixed chart's secondary
    # format spread (sharedControls.tsx:289, :307 at 6.1.0; utils/D3Formatting.ts:55), and
    # 'SMART_NUMBER' in the pie's, funnel's and treemap's DEFAULT_FORM_DATA
    # (plugin-chart-echarts Pie/types.ts:78, Funnel/types.ts:72 (:71 at 6.1.0),
    # Treemap/types.ts:64 (:63 at 6.1.0)). x_label_format: x_axis_time_format's
    # DEFAULT_TIME_FORMAT, smart_date (sharedControls.tsx:320, :341 at 6.1.0;
    # D3Formatting.ts:67, :70 at 5.0.0, :78 at 6.1.0). x_label_rotation: xAxisLabelRotation
    # 0 (plugin-chart-echarts src/defaults.ts:31).
    "chart": {"time_range": "No filter", "number_format": "SMART_NUMBER",
              "number_format_secondary": "SMART_NUMBER", "x_label_format": "smart_date",
              "x_label_rotation": 0},
    # table_timestamp_format: SMART_DATE_ID (plugin-chart-table controlPanel.tsx:394, :413
    # at 5.0.0, :488 at 6.1.0).
    "table": {"date_format": "smart_date"},
    "big_number_trend": {"trend_color": TREND_DEFAULT_HEX},
    "timeseries_line": {"marker_size": 6, "opacity": 0.2},
    "timeseries_area": {"marker_size": 6, "opacity": 0.2},
    "timeseries_scatter": {"marker_size": 6},
    "pie": {"label_type": "key_percent"},
    "funnel": {"label_type": "key"},
    "treemap": {"label_type": "key_value"},
    # The labels A to Z on both axes, the y axis from the bottom up, so it reads z_to_a
    # from the top (sort_x_axis and sort_y_axis alpha_asc, which the tool always wrote).
    "heatmap": {"color_scheme": HEATMAP_DEFAULT_SCHEME, "x_order": "a_to_z", "y_order": "z_to_a"},
    "mixed_series": {"opacity": 0.2},  # a mixed chart's query a or b
    "waterfall": dict(WATERFALL_DEFAULT_HEX),
    "dashboard": {"refresh_frequency": 0, "filter_bar_orientation": "vertical"},
}


def _default_fields(model: BaseModel) -> dict[str, object]:
    kind = ("dashboard" if isinstance(model, DashboardMeta)
            else "mixed_series" if isinstance(model, MixedSeries) else getattr(model, "type", None))
    shared = {**SUPERSET_DEFAULTS["legend"], **SUPERSET_DEFAULTS["chart"]} if kind != "dashboard" else {}
    out = {f: v for f, v in shared.items() if f in type(model).model_fields}
    if kind == "big_number_trend" and model.rolling_periods is not None:
        # Omitted, a window's min periods is the window itself: compiled alike, read back alike.
        out["rolling_min_periods"] = model.rolling_periods
    return {**out, **SUPERSET_DEFAULTS.get(kind, {})}


def set_value(model: BaseModel, field: str):
    """The field's value, or None when it holds Superset's own default."""
    value = getattr(model, field)
    defaults = _default_fields(model)
    return None if field in defaults and value == defaults[field] else value


def without_superset_defaults(spec: DashboardSpec) -> DashboardSpec:
    """The spec with each written Superset default read as unset: what compile builds
    from and what plan compares, so a written default and an omitted field agree."""
    def strip(model: BaseModel) -> BaseModel:
        update = {f: None for f in _default_fields(model) if set_value(model, f) is None
                  and getattr(model, f) is not None}
        if isinstance(model, MixedChart):
            for key in ("a", "b"):
                series = getattr(model, key)
                if strip(series) is not series:
                    update[key] = strip(series)
        return model.model_copy(update=update) if update else model

    return spec.model_copy(update={"dashboard": strip(spec.dashboard),
                                   "charts": [strip(c) for c in spec.charts]})


def json_schema() -> dict:
    return DashboardSpec.model_json_schema()
