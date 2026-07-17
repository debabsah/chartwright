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
from .compiler import _metric_payload
from .resolver import Resolution
from .spec import DashboardSpec

# Issue #1 calibration: ~30 px per rendered grid row; 3 height units (120 px)
# for the card title + column-header block, one more when a pivot nests
# column-dimension headers.
_ROW_PX = 30
_HEADER_UNITS = 3


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
        header_units = _HEADER_UNITS + (1 if chart.columns else 0)
        what = f"pivot renders ~{leaf} leaf rows"
    else:
        leaf = len(data)  # already capped by the query's row_limit
        header_units = _HEADER_UNITS
        what = f"table renders ~{leaf} rows"
    height = spec.resolved_height(chart.name)
    needed_px = leaf * _ROW_PX + header_units * 40
    have_px = height * 40
    if needed_px <= have_px:
        return None
    return (f"{what} (~{needed_px:.0f}px) but height={height:g} ({have_px:.0f}px): "
            f"rows will hide behind an inner scrollbar; raise height to "
            f"~{math.ceil(needed_px / 40)} units or cap row_limit")


@dataclass
class SmokeResult:
    chart: str
    ok: bool
    warning: bool
    detail: str


def _filters_payload(chart) -> list[dict]:
    return [{"col": f.column, "op": f.op, "val": f.value} for f in chart.filters]


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
    return q


def smoke_chart(chart, spec: DashboardSpec, resolution: Resolution, client: SupersetClient) -> SmokeResult:
    ds = resolution.for_chart(chart.dataset)
    ctx = {
        "datasource": {"id": ds.id, "type": "table"},
        "queries": [_query_for(chart, spec)],
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
    fit = _fit_warning(chart, spec, result)
    if fit:
        return SmokeResult(chart.name, True, True, f"{rows} rows; {fit}")
    return SmokeResult(chart.name, True, False, f"{rows} rows")


def smoke(spec: DashboardSpec, resolution: Resolution, client: SupersetClient) -> list[SmokeResult]:
    return [smoke_chart(c, spec, resolution, client) for c in spec.charts]
