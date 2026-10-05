"""Post-import smoke: exercise each chart via POST /api/v1/chart/data with a
query context constructed from the chart's own spec (imported charts carry no
saved query_context, so the GET variant won't work).

Semantic: data-level sanity: the chart's references are valid on the live
instance AND a representative query for its fields returns rows. It does not
replicate each viz's post-processing (binning, pivoting, normalization); that
stays the renderer's job.

Pass = HTTP 200 AND non-empty result payload. Empty = warning naming the
chart, not a silent pass; so are values an IN filter lists on a column the
chart groups or draws by that return no rows (they would draw nothing).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .client import SupersetClient
from .compiler import _metric_payload, mixed_time_axis, time_binding
from .resolver import Resolution
from .spec import DashboardSpec, grid_header, grid_units_for_rows


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


def _window_warning(chart, rows: int) -> str | None:
    """A rolling trendline KPI whose data holds too few time buckets for its window and
    comparison (the design brain's data.rolling-window-span, from the real row count).
    The backend keeps the windows from bucket rolling_min_periods on (pandas_postprocessing/
    rolling.py), and compare_lag reads that many points back from the latest."""
    if chart.type != "big_number_trend" or chart.rolling_type in (None, "cumsum"):
        return None
    least = max(1, chart.rolling_periods if chart.rolling_min_periods is None
                else chart.rolling_min_periods)
    lag = chart.compare_lag or 0
    points = max(0, rows - least + 1)
    if points >= max(lag + 1, 2):
        return None
    return (f"a {least}-step rolling window" + (f" with compare_lag {lag}" if lag else "")
            + f" over {rows} time buckets draws {points} trendline point(s)"
            + (" and no change" if lag else "") + "; widen the chart's time range")


def _value_key(v) -> str:
    """A filter value or a returned one, compared as text: a list of [2, 4] matches the 2
    and 4 a numeric column returns, and 2.0 reads as 2."""
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _series_limited(chart, column: str) -> bool:
    """The chart keeps only its top N series by `column`, so listed values it drops are
    the limit's doing, not missing data."""
    holders = (chart.a, chart.b) if chart.type == "mixed" else (chart,)
    return any(getattr(h, "series_limit", None) and getattr(h, "groupby", None) == column
               for h in holders)


def _empty_values_warning(chart, queries: list[dict], result: list, ds) -> str | None:
    """Values a chart lists with IN on a column it groups or draws by, which come back
    with no rows: a line chart that names four zones where two have data draws two lines
    and a legend of two, and nothing on the dashboard says the other two are missing.
    Only a column the query returns can be checked, since the data says which values
    came back; a time column comes back as timestamps, so it is left alone."""
    notes = []
    for f in chart.filters:
        if f.op != "IN" or f.sql is not None or not isinstance(f.value, list):
            continue
        column = f.column
        pairs = [(q, r) for q, r in zip(queries, result) if column in q.get("columns", [])]
        if not pairs or ds.is_temporal(column) or _series_limited(chart, column):
            continue
        back = {_value_key(row.get(column)) for _, r in pairs for row in r.get("data") or []}
        missing = [v for v in f.value if _value_key(v) not in back]
        if not missing:
            continue
        capped = any(len(r.get("data") or []) >= q.get("row_limit", math.inf) for q, r in pairs)
        names = ", ".join(repr(v) for v in missing)
        notes.append(f"{column} IN lists {names}, which return{'s' if len(missing) == 1 else ''}"
                     f" no rows" + (" within the row limit" if capped else "")
                     + f", so the chart draws nothing for {'it' if len(missing) == 1 else 'them'}")
    return "; ".join(notes) or None


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
    queries = [_with_time_range(q, chart, ds) for q in (
        _mixed_queries(chart, spec, ds) if chart.type == "mixed" else [_query_for(chart, spec)])]
    ctx = {
        "datasource": {"id": ds.id, "type": "table"},
        "queries": queries,
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
    notes = [w for w in (_fit_warning(chart, spec, result) or _window_warning(chart, rows),
                         _empty_values_warning(chart, queries, result, ds)) if w]
    if notes:
        return SmokeResult(chart.name, True, True, f"{rows} rows; " + "; ".join(notes))
    return SmokeResult(chart.name, True, False, f"{rows} rows")


def smoke(spec: DashboardSpec, resolution: Resolution, client: SupersetClient) -> list[SmokeResult]:
    return [smoke_chart(c, spec, resolution, client) for c in spec.charts]
