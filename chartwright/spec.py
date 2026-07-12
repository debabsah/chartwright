"""The dashboard spec: the typed contract the LLM is allowed to emit.

Anything not expressible here does not exist. Validation errors are the only
feedback channel an LLM caller gets; keep messages precise and actionable.

Surface: 14 chart types, per-chart WHERE filters, a dashboard-level native
filter bar (select + time_range), markdown blocks, and tabs.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

GRID_WIDTH = 12
DEFAULT_HEIGHT = {"big_number_total": 4, "big_number_trend": 5, "markdown": 4, "default": 8}
DEFAULT_ROW_LIMIT = {
    "timeseries_line": 10000, "timeseries_bar": 10000, "timeseries_area": 10000,
    "timeseries_scatter": 10000, "bar": 10000, "pie": 100, "table": 1000,
    "pivot_table": 10000, "heatmap": 10000, "histogram": 10000, "funnel": 10,
    "treemap": 100,
}
DEFAULT_TIME_GRAIN = "P1D"

ADHOC_AGGREGATES = ("SUM", "AVG", "COUNT", "COUNT_DISTINCT", "MIN", "MAX")
_ADHOC_RE = re.compile(r"^(SUM|AVG|COUNT|COUNT_DISTINCT|MIN|MAX)\((.+?)\)(?:\s+AS\s+(.+))?$")

# The RAG hexes Superset's own conditional-formatting picker offers.
FORMAT_COLOR_HEX = {"green": "#ACE1C4", "amber": "#FDE380", "red": "#EFA1AA"}

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
    names. Optional display label via ``AGG(col) AS Pretty Label`` (AS uppercase)."""
    m = _ADHOC_RE.match(metric)
    if not m:
        return None
    return {"aggregate": m.group(1), "column": m.group(2).strip(), "label": m.group(3)}


def metric_label(metric: str) -> str:
    """What Superset will display (and key row data by) for this metric string."""
    parsed = parse_metric(metric)
    if parsed and parsed["label"]:
        return parsed["label"]
    return metric


class ChartFilter(BaseModel):
    """A WHERE-clause condition on the chart's dataset."""

    model_config = ConfigDict(extra="forbid")

    column: str
    op: FilterOp = "=="
    value: str | int | float | bool | list[str | int | float] | None = None

    @model_validator(mode="after")
    def _value_shape(self) -> "ChartFilter":
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

    def default_height(self) -> int:
        return DEFAULT_HEIGHT.get(self.type, DEFAULT_HEIGHT["default"])  # type: ignore[attr-defined]


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


class _TimeseriesBase(_ChartBase):
    metrics: list[str] = Field(min_length=1)
    time_column: str
    time_grain: str | None = Field(default=None, description="ISO 8601 duration, e.g. P1D, P1W, P1M")
    time_range: str | None = Field(default=None, description='Superset time range; defaults to "No filter"')
    groupby: str | None = Field(default=None, description="At most one dimension column")
    row_limit: int | None = Field(default=None, ge=1)


class TimeseriesLineChart(_TimeseriesBase):
    type: Literal["timeseries_line"]


class TimeseriesBarChart(_TimeseriesBase):
    type: Literal["timeseries_bar"]


class TimeseriesAreaChart(_TimeseriesBase):
    type: Literal["timeseries_area"]


class TimeseriesScatterChart(_TimeseriesBase):
    type: Literal["timeseries_scatter"]


class BarChart(_ChartBase):
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


class PieChart(_ChartBase):
    type: Literal["pie"]
    metric: str
    groupby: str
    donut: bool = False
    row_limit: int | None = Field(default=None, ge=1)


class TableChart(_ChartBase):
    type: Literal["table"]
    columns: list[str] | None = Field(default=None, description="Raw-records mode: plain columns")
    metrics: list[str] | None = None
    groupby: list[str] | None = None
    row_limit: int | None = Field(default=None, ge=1)
    sort_by: str | None = Field(default=None, description="Metric or column to sort by (aggregate mode: metric)")

    @model_validator(mode="after")
    def _mode(self) -> "TableChart":
        aggregate = bool(self.metrics or self.groupby)
        raw = bool(self.columns)
        if aggregate and raw:
            raise ValueError("table chart: use either columns (raw mode) or metrics+groupby (aggregate mode), not both")
        if not aggregate and not raw:
            raise ValueError("table chart: provide columns (raw mode) or metrics+groupby (aggregate mode)")
        return self


class FormatRule(BaseModel):
    """One RAG band on one pivot metric. Superset colors by FIXED value bands;
    it cannot compare a cell to another column, so band a normalized metric
    (e.g. a %-of-goal ratio) when thresholds differ per row."""

    model_config = ConfigDict(extra="forbid")

    metric: str = Field(description="Display label of one of the chart's metrics")
    operator: Literal["<", ">", "between"]
    target: float | None = Field(default=None, description="Threshold for < or >")
    target_left: float | None = Field(default=None, description="Lower bound for 'between'")
    target_right: float | None = Field(default=None, description="Upper bound for 'between'")
    color: Literal["green", "amber", "red"]

    @model_validator(mode="after")
    def _target_shape(self) -> "FormatRule":
        if self.operator == "between":
            if self.target is not None or self.target_left is None or self.target_right is None:
                raise ValueError("'between' needs target_left + target_right (and no target)")
        elif self.target is None or self.target_left is not None or self.target_right is not None:
            raise ValueError(f"operator {self.operator!r} needs target (and no target_left/right)")
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
    conditional_formatting: list[FormatRule] = Field(default_factory=list)

    @model_validator(mode="after")
    def _dims(self) -> "PivotTableChart":
        if not self.rows and not self.columns:
            raise ValueError("pivot_table needs at least one of rows/columns")
        labels = {metric_label(m) for m in self.metrics}
        for rule in self.conditional_formatting:
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


class HistogramChart(_ChartBase):
    type: Literal["histogram"]
    column: str
    bins: int = Field(default=10, ge=1, le=200)
    groupby: str | None = None
    row_limit: int | None = Field(default=None, ge=1)


class FunnelChart(_ChartBase):
    type: Literal["funnel"]
    metric: str
    groupby: str
    row_limit: int | None = Field(default=None, ge=1)


class TreemapChart(_ChartBase):
    type: Literal["treemap"]
    metric: str
    groupby: list[str] = Field(min_length=1)
    row_limit: int | None = Field(default=None, ge=1)


Chart = Annotated[
    Union[
        BigNumberChart, BigNumberTrendChart, TimeseriesLineChart, TimeseriesBarChart,
        TimeseriesAreaChart, TimeseriesScatterChart, BarChart, PieChart, TableChart,
        PivotTableChart, HeatmapChart, HistogramChart, FunnelChart, TreemapChart,
    ],
    Field(discriminator="type"),
]

CHART_TYPES = (
    "big_number_total", "big_number_trend", "timeseries_line", "timeseries_bar",
    "timeseries_area", "timeseries_scatter", "bar", "pie", "table", "pivot_table",
    "heatmap", "histogram", "funnel", "treemap",
)


class SelectFilter(BaseModel):
    """Native filter bar: value picker over one dataset column."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["select"]
    name: str = Field(min_length=1)
    dataset: DatasetRef
    column: str
    multi: bool = True


class TimeRangeFilter(BaseModel):
    """Native filter bar: dashboard-wide time range picker.

    ``default`` is a Superset time-range expression (``"Last month"``,
    ``"2026-05-01 : 2026-06-01"``) that pre-fills the picker on load; viewers
    can still change it. Omitted, the picker starts empty (Superset shows
    "No filter").
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["time_range"]
    name: str = Field(min_length=1)
    default: str | None = None


class RangeFilter(BaseModel):
    """Native filter bar: numeric range on one dataset column (filter_range).

    ``le``/``ge`` set the DEFAULT bound(s) users see on load. One bound gives a
    single-handle slider (Superset's single-value mode); both give a range;
    equal bounds give exact-match mode. ``charts`` scopes the filter to the
    named charts only (default: every chart), resolved to slice ids after
    import, since ids don't exist at compile time."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["range"]
    name: str = Field(min_length=1)
    dataset: DatasetRef
    column: str
    le: float | None = Field(default=None, description="Default upper bound (x ≤ N)")
    ge: float | None = Field(default=None, description="Default lower bound (x ≥ N)")
    charts: list[str] | None = Field(
        default=None,
        description="Chart names this filter governs; omit for all charts",
    )

    @model_validator(mode="after")
    def _bounds(self) -> "RangeFilter":
        if self.le is not None and self.ge is not None and self.ge > self.le:
            raise ValueError(f"range filter {self.name!r}: ge ({self.ge}) > le ({self.le})")
        if self.charts is not None and not self.charts:
            raise ValueError(f"range filter {self.name!r}: charts must be omitted or non-empty")
        return self


DashboardFilter = Annotated[
    Union[SelectFilter, TimeRangeFilter, RangeFilter], Field(discriminator="type")
]


class MarkdownBlock(BaseModel):
    """A text block in the layout (headers, notes)."""

    model_config = ConfigDict(extra="forbid")

    markdown: str = Field(min_length=1)
    width: int | None = Field(default=None, ge=1, le=GRID_WIDTH)
    height: int | None = Field(default=None, ge=1, le=100)


RowItem = Union[str, MarkdownBlock]


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
        from .sketch import parse_sketch

        return parse_sketch(self.sketch or [], self.legend or {}, self.line)


class Tab(_SketchHolder):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    rows: list[list[RowItem]] | None = None

    @model_validator(mode="after")
    def _rows_or_sketch(self) -> "Tab":
        if bool(self.rows) == bool(self.sketch):
            raise ValueError(f"tab {self.title!r}: provide exactly one of rows / sketch")
        if self.sketch and not self.legend:
            raise ValueError(f"tab {self.title!r}: a sketch needs a legend")
        return self


class DashboardMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    slug: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9-]*$", description="uuid seed input; lowercase kebab-case")


class Layout(_SketchHolder):
    """Flat rows, tabs, or an ASCII sketch; exactly one."""

    model_config = ConfigDict(extra="forbid")

    rows: list[list[RowItem]] | None = None
    tabs: list[Tab] | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> "Layout":
        given = [bool(self.rows), bool(self.tabs), bool(self.sketch)]
        if sum(given) != 1:
            raise ValueError("layout: provide exactly one of rows / tabs / sketch")
        if self.sketch and not self.legend:
            raise ValueError("layout: a sketch needs a legend")
        return self

    def all_rows(self) -> list[list[RowItem]]:
        if self.rows:
            return self.rows
        return [row for tab in (self.tabs or []) for row in (tab.rows or [])]

    def sketch_holders(self) -> list["_SketchHolder"]:
        if self.sketch:
            return [self]
        return [t for t in (self.tabs or []) if t.sketch]


class DashboardSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec_version: Literal["1"]
    dashboard: DashboardMeta
    charts: list[Chart] = Field(min_length=1)
    filters: list[DashboardFilter] = Field(default_factory=list, description="Native filter bar")
    layout: Layout

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
        return max(1, (GRID_WIDTH - explicit_total) // len(implicit))

    def resolved_height(self, name: str) -> float:
        chart = next(c for c in self.charts if c.name == name)
        if chart.height is not None:   # explicit height wins (absorb writes here)
            return chart.height
        sc = self._sketch_charts().get(name)
        if sc is not None:
            return sc.height
        return chart.default_height()


def load_spec(data: dict) -> DashboardSpec:
    return DashboardSpec.model_validate(data)


def json_schema() -> dict:
    return DashboardSpec.model_json_schema()
