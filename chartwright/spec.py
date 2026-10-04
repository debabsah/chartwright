"""The dashboard spec: the typed contract the LLM is allowed to emit.

Anything not expressible here does not exist. Validation errors are the only
feedback channel an LLM caller gets; keep messages precise and actionable.

Surface: 15 chart types, per-chart WHERE filters, a dashboard-level native
filter bar (select, time range, numeric range, time grain, time column),
markdown blocks, headers, dividers, and tabs.
"""

from __future__ import annotations

import math
import re
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

GRID_WIDTH = 12
DEFAULT_HEIGHT = {"big_number_total": 4, "big_number_trend": 5, "markdown": 4, "default": 8}
DEFAULT_ROW_LIMIT = {
    "timeseries_line": 10000, "timeseries_bar": 10000, "timeseries_area": 10000,
    "timeseries_scatter": 10000, "bar": 10000, "pie": 100, "table": 1000,
    "pivot_table": 10000, "heatmap": 10000, "histogram": 10000, "funnel": 10,
    "treemap": 100, "mixed": 10000,
}
DEFAULT_TIME_GRAIN = "P1D"

# How a rendered table/pivot grid consumes spec height. ONE model, used by the
# design critic (size.table-window, size.pivot-window, size.grid-fit) and by
# apply-time smoke, so offline advice, data-aware advice, and the apply warning
# can never disagree about the same chart. ~30px per grid row over a 40px unit;
# 3 units of card title + column header (one more when a pivot nests column
# dimensions). Calibration knob, same status as ROW_UNITS_PER_SPEC_UNIT.
GRID_UNITS_PER_ROW = 0.75
GRID_HEADER_UNITS = 3


def grid_units_for_rows(rows: float, header_units: float = GRID_HEADER_UNITS) -> float:
    """Spec height units needed to render `rows` grid rows without an inner scrollbar."""
    return rows * GRID_UNITS_PER_ROW + header_units


def grid_rows_visible(height: float, header_units: float = GRID_HEADER_UNITS) -> float:
    """Inverse: grid rows visible at `height` before the inner scrollbar starts."""
    return max(0.0, (height - header_units) / GRID_UNITS_PER_ROW)

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
    "Superset tags, e.g. [\"finance\", \"weekly\"]. Superset 6.1.0+ with the TAGGING_SYSTEM "
    "feature flag on: 4.1.4 and 5.0.0 reject a bundle that carries tags (the import fails "
    "and apply restores the previous state), and 6.1.0 without the flag ignores them, so "
    "plan keeps reporting them. An apply replaces the object's tags with this list, so [] "
    "removes them all, tags added in the UI included; omit the field to leave tags alone, "
    "in apply and in plan."
)


class _ChartBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, description="Chart title; unique within the dashboard (uuid seed input)")
    dataset: DatasetRef
    filters: list[ChartFilter] = Field(default_factory=list, description="WHERE conditions on this chart only")
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
    and tooltip); formula layers have no on-chart label in any release."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, description="Series name in the legend and tooltip, e.g. \"Goal\"")
    value: float | None = Field(default=None, description="A flat line at this y value, e.g. 80")
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


class BigNumberTrendChart(_ChartBase):
    type: Literal["big_number_trend"]
    metric: str
    time_column: str
    time_grain: str | None = None
    number_format: str | None = None


class _AxisChart(_ChartBase):
    """A chart with an x axis: how its labels read. Superset's default labels a time
    axis adaptively (full month names, January written as the year) at a spacing it
    picks from the width, then drops labels that collide, so 13 months can read
    'September, November, 2026, March' with gaps that differ chart to chart."""

    x_label_format: str | None = Field(
        default=None,
        description="d3 time format for the labels of a time x axis, e.g. '%b' (Sep); "
                    "omit for Superset's adaptive format",
    )
    x_label_every: bool = Field(
        default=False,
        description="A label at every x value: every time-grain step, or every category "
                    "(Superset 6.1.0+ controls; older releases ignore them)",
    )
    x_label_rotation: int | None = Field(
        default=None, ge=-90, le=90,
        description="Rotate the x-axis labels, in degrees, e.g. 45 for long category names",
    )
    annotations: list[Annotation] = Field(
        default_factory=list,
        description="Formula lines over the chart, e.g. a goal: [{\"name\": \"Goal\", \"value\": 80, "
                    "\"style\": \"dashed\"}] (Superset's FORMULA annotation layers)",
    )

    @model_validator(mode="after")
    def _annotation_names(self) -> "_AxisChart":
        names = [a.name for a in self.annotations]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"chart {self.name!r}: duplicate annotation names {dupes} (each is a series id)")
        return self


class _TimeseriesBase(_AxisChart, _ColorSchemeMixin):
    metrics: list[str] = Field(min_length=1)
    time_column: str
    time_grain: str | None = Field(default=None, description="ISO 8601 duration, e.g. P1D, P1W, P1M")
    time_range: str | None = Field(default=None, description='Superset time range; defaults to "No filter"')
    groupby: str | None = Field(default=None, description="At most one dimension column")
    row_limit: int | None = Field(default=None, ge=1)
    number_format: str | None = Field(default=None, description="d3 format for the value axis, e.g. '.1%'")


class TimeseriesLineChart(_TimeseriesBase):
    type: Literal["timeseries_line"]
    y_axis_max: float | None = Field(
        default=None,
        description="Top of the value axis, e.g. 1 for a share that can't pass 100 % (Superset 6.1.0+)",
    )


class TimeseriesBarChart(_TimeseriesBase):
    type: Literal["timeseries_bar"]


class TimeseriesAreaChart(_TimeseriesBase):
    type: Literal["timeseries_area"]


class TimeseriesScatterChart(_TimeseriesBase):
    type: Literal["timeseries_scatter"]


class BarChart(_AxisChart, _ColorSchemeMixin):
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
    number_format: str | None = Field(default=None, description="d3 format for the value axis, e.g. ',.0f'")


class PieChart(_ChartBase, _ColorSchemeMixin):
    type: Literal["pie"]
    metric: str
    groupby: str
    donut: bool = False
    row_limit: int | None = Field(default=None, ge=1)


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
        description="Labels queried but not displayed, e.g. a status only a colour rule reads (Superset 6.1+)",
    )
    number_formats: dict[str, str] = Field(
        default_factory=dict, description="d3 format per label, e.g. {\"Rate\": \".3f\"}",
    )
    cell_bars: bool | None = Field(
        default=None,
        description="Bars behind numeric cells (Superset draws them by default; false for ids, "
                    "years or a column a colour rule already speaks for)",
    )
    date_format: str | None = Field(default=None, description="strftime for date columns, e.g. '%Y-%m-%d'")

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
        for label, where in named:
            if label not in labels:
                raise ValueError(f"{where} {label!r} is not one of the table's labels {sorted(labels)}")
        return self


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


class HeatmapChart(_ChartBase):
    type: Literal["heatmap"]
    x_column: str
    y_column: str
    metric: str
    row_limit: int | None = Field(default=None, ge=1)


class HistogramChart(_ChartBase, _ColorSchemeMixin):
    type: Literal["histogram"]
    column: str
    bins: int = Field(default=10, ge=1, le=200)
    groupby: str | None = None
    row_limit: int | None = Field(default=None, ge=1)


class FunnelChart(_ChartBase, _ColorSchemeMixin):
    type: Literal["funnel"]
    metric: str
    groupby: str
    row_limit: int | None = Field(default=None, ge=1)


class TreemapChart(_ChartBase, _ColorSchemeMixin):
    type: Literal["treemap"]
    metric: str
    groupby: list[str] = Field(min_length=1)
    row_limit: int | None = Field(default=None, ge=1)


class MixedSeries(BaseModel):
    """One of a mixed chart's two queries: its metrics, drawn as bars or a line, on
    the primary (left) or secondary (right) value axis."""

    model_config = ConfigDict(extra="forbid")

    metrics: list[str] = Field(min_length=1)
    kind: Literal["bar", "line"] = "bar"
    axis: Literal["primary", "secondary"] = "primary"
    groupby: str | None = Field(default=None, description="At most one dimension column")
    markers: bool = Field(
        default=False,
        description="A marker at each point (a line over one category draws nothing without them)",
    )


class MixedChart(_AxisChart, _ColorSchemeMixin):
    """Bars and a line on two value axes (Superset's Mixed Chart), e.g. revenue
    as bars with revenue per order as a line. ``x_column`` is a time column (bucketed
    by ``time_grain``) or any column (a categorical axis, e.g. by cause). ``a`` and
    ``b`` are the two queries; the chart's own ``filters`` apply to both."""

    type: Literal["mixed"]
    x_column: str
    time_grain: str | None = Field(default=None, description="ISO 8601 duration for a time x axis, e.g. P1M")
    time_range: str | None = Field(default=None, description='Superset time range; defaults to "No filter"')
    a: MixedSeries
    b: MixedSeries
    row_limit: int | None = Field(default=None, ge=1)
    number_format: str | None = Field(default=None, description="d3 format for the primary axis")
    number_format_secondary: str | None = Field(default=None, description="d3 format for the secondary axis")


Chart = Annotated[
    Union[
        BigNumberChart, BigNumberTrendChart, TimeseriesLineChart, TimeseriesBarChart,
        TimeseriesAreaChart, TimeseriesScatterChart, BarChart, PieChart, TableChart,
        PivotTableChart, HeatmapChart, HistogramChart, FunnelChart, TreemapChart,
        MixedChart,
    ],
    Field(discriminator="type"),
]

CHART_TYPES = (
    "big_number_total", "big_number_trend", "timeseries_line", "timeseries_bar",
    "timeseries_area", "timeseries_scatter", "bar", "pie", "table", "pivot_table",
    "heatmap", "histogram", "funnel", "treemap", "mixed",
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
        description="Height units (1 = 40 px) in fifths: 0.2 = 8 px, one Superset grid row (1.6 = 64 px)")

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

    A sketch draws the layout as text: legend symbols map to chart names,
    character runs become twelfths of the 12-column grid, each line adds
    `line` height units (1 unit = 40 px; repeat lines for taller), and
    vertically stacked symbols compile to Superset COLUMN containers.
    '.' is a reserved EMPTY cell, legal trailing-right (a row narrower than
    the page) or at the BOTTOM of a slice/stack (a short chart beside a tall
    one); anywhere else is a named error (Superset packs left and upward)."""

    sketch: list[str] | None = Field(
        default=None, description="ASCII layout: one string per grid line; spaces are cosmetic"
    )
    legend: dict[str, str] | None = Field(
        default=None, description="Sketch symbol -> chart name"
    )
    line: int = Field(
        default=2, ge=1, le=20,
        description="Height units per sketch line (1 unit = 40 px)",
    )

    def parsed_sketch(self):
        from .sketch import parse_sketch_cached

        return parse_sketch_cached(
            tuple(self.sketch or ()), tuple(sorted((self.legend or {}).items())), self.line)


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


class DashboardMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    slug: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9-]*$", description="uuid seed input; lowercase kebab-case")
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
        description="Show each chart's last-queried time on its card. Superset 6.1.0+ only: "
                    "4.1.4 and 5.0.0 import it, then refuse to save the dashboard's settings "
                    "(their metadata schema rejects the key), so leave it off there.",
    )
    tags: list[str] | None = Field(default=None, description=TAGS_DESCRIPTION)

    @field_validator("refresh_frequency")
    @classmethod
    def _no_refresh_is_none(cls, v: int | None) -> int | None:
        # 0 is Superset's "don't refresh", which decompile reads back as omitted.
        return v or None

    @field_validator("filter_bar_orientation")
    @classmethod
    def _vertical_is_default(cls, v: str | None) -> str | None:
        # Vertical is Superset's default, which decompile reads back as omitted.
        return None if v == "vertical" else v

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
        return self


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


class Layout(_SketchHolder):
    """Flat rows, tabs, or an ASCII sketch; exactly one. Optionally a footer:
    rows below all of it, outside any tab, so a tabbed dashboard shows the
    footer under every tab."""

    model_config = ConfigDict(extra="forbid")

    rows: list[Row] | None = None
    tabs: list[Tab] | None = None
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
        return item_rows([*body, *(self.footer or [])])

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
                    charts = child.children if hasattr(child, "children") else [child]
                    for sc in charts:
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
        out: dict[str, object] = {}
        for holder in self.layout.sketch_holders():
            for row in holder.parsed_sketch():
                for child in row.children:
                    charts = child.children if hasattr(child, "children") else [child]
                    for sc in charts:
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
    queries, and an aggregate table's sort metric."""
    out: list[str] = []
    if getattr(chart, "metric", None):
        out.append(chart.metric)
    out += list(getattr(chart, "metrics", None) or [])
    if chart.type == "mixed":
        out += [*chart.a.metrics, *chart.b.metrics]
    if chart.type == "table" and chart.sort_by and not chart.columns:
        out.append(chart.sort_by)
    return out


def load_spec(data: dict) -> DashboardSpec:
    return DashboardSpec.model_validate(data)


def json_schema() -> dict:
    return DashboardSpec.model_json_schema()
