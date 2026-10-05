"""Post-import smoke: exercise each chart via POST /api/v1/chart/data with a
query context constructed from the chart's own spec (imported charts carry no
saved query_context, so the GET variant won't work).

Semantic: data-level sanity: the chart's references are valid on the live
instance AND a representative query for its fields returns rows. It does not
replicate each viz's post-processing (binning, pivoting, normalization); that
stays the renderer's job.

Pass = HTTP 200 AND non-empty result payload. Empty = warning naming the
chart, not a silent pass.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .client import SupersetClient
from .compiler import _metric_payload, mixed_time_axis, time_binding, waterfall_time_axis
from .resolver import Resolution
from .spec import DashboardSpec, grid_header, grid_units_for_rows, metric_label


def _fit_warning(chart, spec: DashboardSpec, result: list) -> str | None:
    """Data-driven height fit for table/pivot_table (issue #1). The spec's
    height is a fixed layout property while rendered rows grow with the data:
    rows past the fold hide behind the chart's INNER scrollbar, the dashboard
    looks complete, and nothing else would ever complain. Smoke already holds
    the query result, so the comparison costs nothing extra."""
    if chart.type not in ("table", "pivot_table"):
        return None
    data: list[dict] = []
    for q in result:
        data.extend(q.get("data") or [])
    if not data:
        return None
    if chart.type == "pivot_table":
        if not chart.rows:
            return None  # columns-only pivot: a single metric band, height-safe
        leaf = len({tuple(r.get(d) for d in chart.rows) for r in data})
        header, row = grid_header(chart)
        what = f"pivot renders ~{leaf} leaf rows" + (" and its totals row" if chart.column_totals else "")
    else:
        leaf = len(data)  # already capped by the query's row_limit
        header, row = grid_header(chart, leaf)
        what = f"table renders ~{leaf} rows"
        if chart.page_length and leaf > chart.page_length:
            # A paged table renders one page and its page controls (size.table-window's model).
            leaf = chart.page_length
            what = f"table renders a {chart.page_length}-row page and its pager"
    height = spec.resolved_height(chart.name)
    # Shared grid model (chartwright/spec.py): the design critic's
    # size.grid-fit and size.table-window read the same numbers, so pre-apply
    # advice and this post-apply warning can never contradict each other.
    needed = grid_units_for_rows(leaf, header, row)
    if needed <= height:
        return None
    return (f"{what} (~{needed * 40:.0f}px) but height={height:g} ({height * 40:.0f}px): "
            f"rows will hide behind an inner scrollbar; raise height to "
            f"~{math.ceil(needed)} units or cap row_limit")


def _bridge_warning(chart, result: list) -> str | None:
    """A bridge (a waterfall with steps) checked against its rows: every step and the
    closing has one, nothing else does, and the closing reconciles. Superset draws the
    closing as the running total, not the row's own value, so a closing that doesn't
    add up still looks right on the chart; this is where it shows."""
    if chart.type != "waterfall" or chart.steps is None:
        return None
    label = metric_label(chart.metric)
    values: dict[str, float | None] = {}
    for q in result:
        for row in q.get("data") or []:
            key = str(row.get(chart.x_column))
            value = row.get(label)
            values[key] = value if isinstance(value, (int, float)) else None
    out = []
    if chart.opening is not None and chart.opening not in values:
        out.append(f"no row for opening {chart.opening!r}, so the bridge starts at zero")
    missing = [s for s in chart.steps if s not in values]
    if missing:
        out.append(f"no rows for steps {missing}")
    if chart.closing not in values:
        out.append(f"no row for closing {chart.closing!r}, so Superset draws no closing total")
    extra = sorted(v for v in values if v not in chart.steps and v not in (chart.closing, chart.opening))
    if extra:
        out.append(f"{extra} are not in steps: Superset draws them after the listed steps, A to Z")
    closing = values.get(chart.closing)
    steps = [v for k, v in values.items() if k != chart.closing]
    if closing is not None and all(v is not None for v in steps):
        added = sum(steps)
        # A millionth: floating-point sums of a reconciled bridge differ far less, a
        # closing that doesn't reconcile far more.
        if not math.isclose(closing, added, rel_tol=1e-6, abs_tol=1e-6):
            out.append(f"the closing row holds {closing:,.6g} but the steps add to {added:,.6g}: "
                       f"Superset draws {added:,.6g} as {chart.closing!r}")
    return "bridge: " + "; ".join(out) if out else None


# Superset 6.x fills an unset box plot row limit with the control's default
# (sharedControls.tsx row_limit, 6.1.0 :236); smoke asks for as many.
BOX_PLOT_ROWS = 10000


def _observations_warning(chart, rows: int) -> str | None:
    """A box plot's query returns its observations before the boxplot step makes
    quartiles of them; a query that stops at its row limit leaves some out."""
    if chart.type != "box_plot" or rows < (chart.row_limit or BOX_PLOT_ROWS):
        return None
    where = ("its row_limit" if chart.row_limit else
             f"{BOX_PLOT_ROWS:,}, where Superset 6.0.0 or later stops an unset row_limit")
    return (f"the observations reached {where} ({rows:,} rows), so the boxes may leave "
            f"some out; raise row_limit or coarsen distribute_across")


@dataclass
class SmokeResult:
    chart: str
    ok: bool
    warning: bool
    detail: str


def _filters_payload(chart) -> list[dict]:
    return [{"col": f.column, "op": f.op, "val": f.value} for f in chart.filters if f.sql is None]


def _sql_where(chart) -> str | None:
    """A chart's custom-SQL filters as the query's extra WHERE text (extras.where,
    accepted by the chart data API in every supported release)."""
    parts = [f"({f.sql})" for f in chart.filters if f.sql is not None]
    return " AND ".join(parts) or None


def _with_where(q: dict, chart) -> dict:
    where = _sql_where(chart)
    if where:
        q["extras"]["where"] = where
    return q


def _with_time_range(q: dict, chart, ds) -> dict:
    """The chart's own time_range, bound the way the compiled chart binds it
    (compiler.time_binding): a TEMPORAL_RANGE filter on a time axis, else the
    dataset's main time column as granularity. A chart without a time_range
    keeps the unfiltered query."""
    binding = time_binding(chart, ds) if chart.time_range else None
    if binding is None:
        return q
    kind, column = binding
    q["time_range"] = chart.time_range
    if kind == "axis":
        q["filters"] = q["filters"] + [
            {"col": column, "op": "TEMPORAL_RANGE", "val": chart.time_range}]
    else:
        q["granularity"] = column
    return q


def _time_axis(column: str, grain: str | None) -> dict:
    return {
        "columnType": "BASE_AXIS",
        "label": column,
        "sqlExpression": column,
        "expressionType": "SQL",
        "timeGrain": grain or "P1D",
    }


def _query_for(chart, spec: DashboardSpec) -> dict:
    slug = spec.dashboard.slug
    q: dict = {
        "filters": _filters_payload(chart),
        "extras": {"time_grain_sqla": getattr(chart, "time_grain", None) or "P1D"},
        "time_range": "No filter",
        "row_limit": getattr(chart, "row_limit", None) or 1000,
        "columns": [],
        "metrics": [],
        "orderby": [],
    }

    def metric(m: str):
        return _metric_payload(m, slug, chart.name)

    t = chart.type
    if t == "big_number_total":
        q["metrics"] = [metric(chart.metric)]
    elif t == "big_number_trend":
        q["metrics"] = [metric(chart.metric)]
        q["columns"] = [_time_axis(chart.time_column, chart.time_grain)]
    elif t in ("timeseries_line", "timeseries_bar", "timeseries_area", "timeseries_scatter"):
        q["metrics"] = [metric(m) for m in chart.metrics]
        q["columns"] = [_time_axis(chart.time_column, chart.time_grain)] + (
            [chart.groupby] if chart.groupby else []
        )
    elif t == "bar":
        q["metrics"] = [metric(m) for m in chart.metrics]
        q["columns"] = [chart.x_column] + ([chart.groupby] if chart.groupby else [])
    elif t == "pie":
        q["metrics"] = [metric(chart.metric)]
        q["columns"] = [chart.groupby]
    elif t == "table":
        if chart.columns:
            q["columns"] = chart.columns
        else:
            q["columns"] = chart.groupby or []
            q["metrics"] = [metric(m) for m in (chart.metrics or [])]
    elif t == "pivot_table":
        q["metrics"] = [metric(m) for m in chart.metrics]
        q["columns"] = [*chart.rows, *chart.columns]
    elif t == "heatmap":
        q["metrics"] = [metric(chart.metric)]
        q["columns"] = [chart.x_column, chart.y_column]
    elif t == "histogram":
        # data sanity: the raw column has values; binning is the renderer's job
        q["columns"] = [chart.column] + ([chart.groupby] if chart.groupby else [])
    elif t == "funnel":
        q["metrics"] = [metric(chart.metric)]
        q["columns"] = [chart.groupby]
    elif t == "treemap":
        q["metrics"] = [metric(chart.metric)]
        q["columns"] = list(chart.groupby)
    return _with_where(q, chart)


def _waterfall_query(chart, spec: DashboardSpec, ds) -> dict:
    """One row per step (Waterfall/buildQuery.ts: the x axis, then the breakdown). A
    bridge is queried by its step column, so its rows can be checked by name."""
    x = (_time_axis(chart.x_column, chart.time_grain)
         if chart.time_grain and waterfall_time_axis(chart, ds) else chart.x_column)
    return _with_where({
        "filters": _filters_payload(chart),
        "extras": {"time_grain_sqla": chart.time_grain or "P1D"},
        "time_range": "No filter",
        "row_limit": chart.row_limit or 1000,
        "columns": [x] + ([chart.groupby] if chart.groupby else []),
        "metrics": [_metric_payload(chart.metric, spec.dashboard.slug, chart.name)],
        "orderby": [],
    }, chart)


def _box_plot_query(chart, spec: DashboardSpec, ds) -> dict:
    """The observations, as BoxPlot/buildQuery.ts asks for them before its boxplot
    step: each time column of distribute_across at the grain, then the groups."""
    across = [_time_axis(c, chart.time_grain)
              if chart.time_grain and ds.is_temporal(c) is not False else c
              for c in chart.distribute_across]
    return _with_where({
        "filters": _filters_payload(chart),
        "extras": {"time_grain_sqla": chart.time_grain or "P1D"},
        "time_range": "No filter",
        "row_limit": chart.row_limit or BOX_PLOT_ROWS,
        "columns": [*across, *chart.groupby],
        "metrics": [_metric_payload(m, spec.dashboard.slug, chart.name) for m in chart.metrics],
        "orderby": [],
    }, chart)


def _mixed_queries(chart, spec: DashboardSpec, ds) -> list[dict]:
    """Query A and query B, as MixedTimeseries/buildQuery.ts sends them."""
    x = (_time_axis(chart.x_column, chart.time_grain)
         if mixed_time_axis(chart, ds) else chart.x_column)
    out = []
    for series in (chart.a, chart.b):
        out.append(_with_where({
            "filters": _filters_payload(chart),
            "extras": {"time_grain_sqla": chart.time_grain or "P1D"},
            "time_range": "No filter",
            "row_limit": chart.row_limit or 1000,
            "columns": [x] + ([series.groupby] if series.groupby else []),
            "metrics": [_metric_payload(m, spec.dashboard.slug, chart.name) for m in series.metrics],
            "orderby": [],
        }, chart))
    return out


def smoke_chart(chart, spec: DashboardSpec, resolution: Resolution, client: SupersetClient) -> SmokeResult:
    ds = resolution.for_chart(chart.dataset)
    if chart.type == "mixed":
        queries = _mixed_queries(chart, spec, ds)
    elif chart.type == "waterfall":
        queries = [_waterfall_query(chart, spec, ds)]
    elif chart.type == "box_plot":
        queries = [_box_plot_query(chart, spec, ds)]
    else:
        queries = [_query_for(chart, spec)]
    ctx = {
        "datasource": {"id": ds.id, "type": "table"},
        "queries": [_with_time_range(q, chart, ds) for q in queries],
        "result_format": "json",
        "result_type": "full",
    }
    r = client.chart_data(ctx)
    if r.status_code != 200:
        return SmokeResult(chart.name, False, False, f"HTTP {r.status_code}: {r.text[:500]}")
    try:
        result = r.json()["result"]
        rows = sum(len(q.get("data") or []) for q in result)
    except Exception as e:  # noqa: BLE001 - malformed body is a failure, whatever the shape
        return SmokeResult(chart.name, False, False, f"unparseable chart/data response: {e}")
    if rows == 0:
        return SmokeResult(chart.name, True, True, "query succeeded but returned 0 rows")
    fit = (_fit_warning(chart, spec, result) or _bridge_warning(chart, result)
           or _observations_warning(chart, rows))
    if fit:
        return SmokeResult(chart.name, True, True, f"{rows} rows; {fit}")
    return SmokeResult(chart.name, True, False, f"{rows} rows")


def smoke(spec: DashboardSpec, resolution: Resolution, client: SupersetClient) -> list[SmokeResult]:
    return [smoke_chart(c, spec, resolution, client) for c in spec.charts]
