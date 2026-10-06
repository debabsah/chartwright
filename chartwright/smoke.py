"""Post-import smoke: exercise each chart via POST /api/v1/chart/data with a
query context constructed from the chart's own spec (imported charts carry no
saved query_context, so the GET variant won't work).

Semantic: data-level sanity: the chart's references are valid on the live
instance AND a representative query for its fields returns rows. It does not
replicate each viz's post-processing (binning, pivoting, normalization); that
stays the renderer's job.

Each chart is queried as the dashboard queries it on load: with the default of
every native filter in its scope (filter_defaults), so row counts, the empty
check and the height fit read the rows a viewer first sees.

Pass = HTTP 200 AND non-empty result payload. Empty = warning naming the
chart, not a silent pass; so are values an IN filter lists on a column the
chart groups or draws by that return no rows (they would draw nothing).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

from .client import SupersetClient
from .compiler import (_metric_payload, _num, _range_default_mask, mixed_time_axis, time_binding,
                       waterfall_time_axis)
from .resolver import Resolution
from .spec import (DEFAULT_ROW_LIMIT, DashboardSpec, grid_fit, grid_header, grid_units_for_rows,
                   metric_label, pivot_frame, pivot_rows)


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
        # The rows the pivot draws, not the records the query returned (rows x columns).
        leaf, subtotals = pivot_rows(chart, data)
        rows = leaf + subtotals
        what = ("pivot renders only its totals row" if not rows else  # no row attribute
                f"pivot renders ~{leaf} leaf rows"
                + (f", {subtotals} subtotal rows" if subtotals else "")
                + (" and its totals row" if pivot_frame(chart)[2] else ""))
        remedy = "filter it to fewer rows"  # row_limit counts records, not pivot rows
    else:
        rows = len(data)  # already capped by the query's row_limit
        what = f"table renders ~{rows} rows" + (" and its totals row" if chart.show_totals else "")
        remedy = "cap row_limit"
    held, header, row = grid_fit(chart, rows)
    if held != rows:
        # A paged table renders one page and its page controls (size.table-window's model).
        what = f"table renders a {held}-row page and its pager"
    height = spec.resolved_height(chart.name)
    # Shared grid model (chartwright/spec.py): the design critic's size.grid-fit,
    # size.pivot-window and size.table-window ask grid_fit the same question, so
    # pre-apply advice and this post-apply warning can never contradict each other.
    needed = grid_units_for_rows(held, header, row)
    if needed <= height:
        return None
    return (f"{what} (~{needed * 40:.0f}px) but height={height:g} ({height * 40:.0f}px): "
            f"rows will hide behind an inner scrollbar; raise height to "
            f"~{math.ceil(needed)} units or {remedy}")


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
    # The dashboard filter defaults the query ran with ("Carrier = All carriers").
    filter_defaults: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        """The JSON shape: filter_defaults only when there were some, so a dashboard
        without filter defaults reports exactly as before."""
        out = asdict(self)
        if not self.filter_defaults:
            del out["filter_defaults"]
        return out


def _filters_payload(filters) -> list[dict]:
    return [{"col": f.column, "op": f.op, "val": f.value} for f in filters if f.sql is None]


def _sql_where(filters) -> str | None:
    """Custom-SQL filters as the query's extra WHERE text (extras.where, accepted
    by the chart data API in every supported release)."""
    parts = [f"({f.sql})" for f in filters if f.sql is not None]
    return " AND ".join(parts) or None


def _with_where(q: dict, chart) -> dict:
    where = _sql_where(chart.filters)
    if where:
        q["extras"]["where"] = where
    return q


def _with_time_range(q: dict, chart, ds, time_range: str | None = None) -> dict:
    """A time range (a dashboard filter's default, else the chart's own
    time_range), bound the way the compiled chart binds it (compiler.time_binding):
    a TEMPORAL_RANGE filter on a time axis, else the dataset's main time column as
    granularity. Without a time range the query stays unfiltered."""
    time_range = time_range or chart.time_range
    binding = time_binding(chart, ds) if time_range else None
    if binding is None:
        return q
    kind, column = binding
    q["time_range"] = time_range
    if kind == "axis":
        q["filters"] = q["filters"] + [
            {"col": column, "op": "TEMPORAL_RANGE", "val": time_range}]
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
    # A table or pivot asks for the rows its compiled chart asks for (the compiler's
    # default when row_limit is unset), so the height fit counts every row it draws: a
    # 1,000-record cap misses leaf rows of a pivot riding its 10,000 default.
    grid = chart.type in ("table", "pivot_table")
    q: dict = {
        "filters": _filters_payload(chart.filters),
        "extras": {"time_grain_sqla": getattr(chart, "time_grain", None) or "P1D"},
        "time_range": "No filter",
        "row_limit": getattr(chart, "row_limit", None) or (
            DEFAULT_ROW_LIMIT[chart.type] if grid else 1000),
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
        "filters": _filters_payload(chart.filters),
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
        "filters": _filters_payload(chart.filters),
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
            "filters": _filters_payload(chart.filters),
            "extras": {"time_grain_sqla": chart.time_grain or "P1D"},
            "time_range": "No filter",
            "row_limit": chart.row_limit or 1000,
            "columns": [x] + ([series.groupby] if series.groupby else []),
            "metrics": [_metric_payload(m, spec.dashboard.slug, chart.name) for m in series.metrics],
            "orderby": [],
        }, chart))
    return out


# -- the dashboard's filter defaults ---------------------------------------------
#
# How Superset applies a native filter's value on load (6.1.0 source): every chart in
# the filter's scope gets the filter's extraFormData (getFormDataWithExtraFilters.ts:
# 465-476, the scope being its chartsInScope, activeAllDashboardFilters.ts:113-122).
# Its `filters` are appended to the chart's query (buildQueryObject.ts:69-74 and 90);
# time_range overrides the query's and time_grain_sqla its extras
# (processExtraFormData.ts:31-44, constants.ts:30-53), which become the time axis'
# grain (normalizeTimeColumn.ts:57-71). The backend then applies a filter only when
# the chart's own dataset has a column of that name and drops it otherwise
# (superset/models/helpers.py:3024-3026 and 3069, reported as rejected at 3484-3492;
# the same check at helpers.py:1758 and 1765 in 4.1.4, 1769 and 1776 in 5.0.0).


@dataclass
class FilterDefault:
    """One native filter's value on load, as it reaches the charts in its scope."""

    label: str                       # as a result names it: "Carrier = All carriers"
    charts: list[str] | None         # the filter's scope; None is every chart
    column: str | None = None        # applies only to charts whose dataset has it
    filters: list[dict] = field(default_factory=list)
    time_range: str | None = None
    time_grain: str | None = None
    time_column: str | None = None
    unapplied: str | None = None     # why smoke can't apply it, said in the result


def _show(value) -> str:
    return "<NULL>" if value is None else str(_num(value) if isinstance(value, float) else value)


def _select_default(f, values: list, suffix: str = "") -> FilterDefault:
    # The plugin rebuilds the default's data mask on load, and under inverse selection
    # it excludes the values (SelectFilterPlugin.tsx:197-225 and 349-352,
    # filters/utils.ts:33-58), whatever the saved mask says.
    op = "NOT IN" if f.inverse_selection else "IN"
    label = (f"{f.name} excludes {' and '.join(_show(v) for v in values)}" if f.inverse_selection
             else f"{f.name} = {' or '.join(_show(v) for v in values)}")
    return FilterDefault(label + suffix, f.charts, f.column, [{"col": f.column, "op": op, "val": values}])


def _range_label(f) -> str:
    ge, le = (None if b is None else _num(b) for b in (f.ge, f.le))
    if ge is not None and le is not None:
        return f"{f.name} = {le}" if ge == le else f"{ge} <= {f.name} <= {le}"
    return f"{f.name} <= {le}" if le is not None else f"{f.name} >= {ge}"


def _ancestors(name: str, by_name: dict) -> list[str]:
    """Every filter `name` depends on, nearest parent last, as Superset walks them
    (nativeFilters/dependencyGraph.ts:52-74 at 6.1.0): the nearest time range wins."""
    out: list[str] = []
    seen = {name}

    def visit(n: str) -> None:
        for p in getattr(by_name[n], "dependencies", None) or []:
            if p not in seen:
                seen.add(p)
                visit(p)
                out.append(p)

    visit(name)
    return out


def _first_value(f, by_name: dict, resolved: dict, resolution: Resolution,
                 client: SupersetClient) -> FilterDefault | None:
    """`default_to_first`: the filter's first value, read with the query the Select
    filter sends (Select/buildQuery.ts:56-66: its column, ordered by itself or by
    sort_metric, ascending unless sort_descending, types.ts:80) and picked as the
    plugin does, the first row (SelectFilterPlugin.tsx:359-366). The query carries
    the filter's pre-filter (nativeFilters/utils.ts:91-99) and the values its
    dependencies load with (FilterControls/state.ts:47-62). One row is enough: the
    ordering decides which value comes first."""
    ds = resolution.datasets[f.dataset.key()]
    q: dict = {
        "columns": [f.column],
        "metrics": [f.sort_metric] if f.sort_metric else [],
        "orderby": [[f.sort_metric or f.column, not f.sort_descending]],
        "filters": _filters_payload(f.pre_filter),
        "extras": {},
        "time_range": f.time_range or "No filter",
        "row_limit": 1,
    }
    where = _sql_where(f.pre_filter)
    if where:
        q["extras"]["where"] = where
    if f.time_column:
        q["granularity"] = f.time_column
    for parent in _ancestors(f.name, by_name):
        d = resolved.get(parent)
        if d is None:
            continue
        if d.unapplied:
            return FilterDefault(f"{f.name}'s first value", f.charts, f.column,
                                 unapplied=f"it depends on {parent}, which smoke could not apply")
        if d.filters and d.column in ds.columns:
            q["filters"] = q["filters"] + d.filters
        if d.time_range:
            q["time_range"] = d.time_range
    r = client.chart_data({"datasource": {"id": ds.id, "type": "table"}, "queries": [q],
                           "result_format": "json", "result_type": "full"})
    if r.status_code != 200:
        return FilterDefault(f"{f.name}'s first value", f.charts, f.column,
                             unapplied=f"reading it failed: HTTP {r.status_code}")
    try:
        data = r.json()["result"][0].get("data") or []
    except Exception:  # noqa: BLE001 - malformed body: say so, whatever the shape
        return FilterDefault(f"{f.name}'s first value", f.charts, f.column,
                             unapplied="its value query returned an unreadable response")
    if not data or f.column not in data[0]:
        return None  # no values: the dashboard selects none either
    return _select_default(f, [data[0][f.column]], " (its first value)")


def _default_of(f, by_name: dict, resolved: dict, resolution: Resolution,
                client: SupersetClient) -> FilterDefault | None:
    if f.type == "select":
        if f.default:
            return _select_default(f, list(f.default))
        if f.default_to_first:
            return _first_value(f, by_name, resolved, resolution, client)
        return None
    if f.type == "range":
        mask = _range_default_mask(f)  # the bounds the bundle's default applies
        if mask is None:
            return None
        return FilterDefault(_range_label(f), f.charts, f.column, mask["extraFormData"]["filters"])
    if not f.default:
        return None
    label = f"{f.name} = {f.default}"
    if f.type == "time_range":
        return FilterDefault(label, f.charts, time_range=f.default)
    if f.type == "time_grain":
        return FilterDefault(label, f.charts, time_grain=f.default)
    return FilterDefault(label, f.charts, time_column=f.default)


def filter_defaults(spec: DashboardSpec, resolution: Resolution,
                    client: SupersetClient) -> list[FilterDefault]:
    """Each native filter's value on load. Only `default_to_first` reads data, one
    small query per filter; a filter's parents resolve before it."""
    by_name = {f.name: f for f in spec.filters}
    resolved: dict[str, FilterDefault | None] = {}

    def resolve(name: str) -> FilterDefault | None:
        if name not in resolved:
            f = by_name[name]
            for parent in getattr(f, "dependencies", None) or []:
                resolve(parent)
            resolved[name] = _default_of(f, by_name, resolved, resolution, client)
        return resolved[name]

    return [d for d in (resolve(f.name) for f in spec.filters) if d is not None]


def _axes(queries: list[dict]) -> list[dict]:
    return [c for q in queries for c in q["columns"]
            if isinstance(c, dict) and c.get("columnType") == "BASE_AXIS"]


def _with_filter_defaults(queries: list[dict], chart, ds,
                          defaults: list[FilterDefault]) -> tuple[list[dict], list[str], list[str]]:
    """The chart's queries with the defaults in its scope, and which were applied
    and which smoke could not apply. A default that changes nothing in this chart's
    query (a column its dataset lacks, a time range it has no time binding for, a
    grain without a time axis) is neither: the dashboard doesn't apply it either."""
    binding = time_binding(chart, ds)
    applied: list[str] = []
    unapplied: list[str] = []
    # A later time range or grain overrides an earlier one (nativeFilters/utils.ts:125-134).
    overrides: dict[str, tuple[str, str]] = {}  # "time_range" | "time_grain" -> (value, label)
    time_columns: list[str] = []
    ranged = chart.time_range not in (None, "No filter")
    for d in defaults:
        if d.charts is not None and chart.name not in d.charts:
            continue
        if d.column is not None and d.column not in ds.columns:
            continue
        if d.unapplied:
            unapplied.append(f"{d.label} ({d.unapplied})")
        elif d.filters:
            for q in queries:
                q["filters"] = q["filters"] + d.filters
            applied.append(d.label)
        elif d.time_range is not None:
            ranged = ranged or d.time_range != "No filter"
            if binding is not None:
                overrides["time_range"] = (d.time_range, d.label)
        elif d.time_grain is not None and _axes(queries):
            overrides["time_grain"] = (d.time_grain, d.label)
        elif d.time_column is not None:
            time_columns.append(d.label)
    time_range = overrides.get("time_range", (None,))[0]
    if "time_grain" in overrides:
        grain = overrides["time_grain"][0]
        for q in queries:
            q["extras"]["time_grain_sqla"] = grain
        for axis in _axes(queries):
            axis["timeGrain"] = grain
    applied += [label for _, label in overrides.values()]
    queries = [_with_time_range(q, chart, ds, time_range) for q in queries]
    if time_columns and (ranged or (binding is not None and binding[0] == "axis")):
        # A time column filter moves the chart's time range, and its time axis, onto
        # another column (query_context_factory.py:235-309 at 6.1.0); smoke doesn't.
        unapplied += [f"{label} (smoke keeps the chart's own time column)" for label in time_columns]
    return queries, applied, unapplied


def smoke_chart(chart, spec: DashboardSpec, resolution: Resolution, client: SupersetClient,
                defaults: list[FilterDefault] | None = None) -> SmokeResult:
    ds = resolution.for_chart(chart.dataset)
    if chart.type == "mixed":
        queries = _mixed_queries(chart, spec, ds)
    elif chart.type == "waterfall":
        queries = [_waterfall_query(chart, spec, ds)]
    elif chart.type == "box_plot":
        queries = [_box_plot_query(chart, spec, ds)]
    else:
        queries = [_query_for(chart, spec)]
    queries, applied, unapplied = _with_filter_defaults(queries, chart, ds, defaults or [])
    ctx = {
        "datasource": {"id": ds.id, "type": "table"},
        "queries": queries,
        "result_format": "json",
        "result_type": "full",
    }
    with_defaults = f" with the dashboard's filter defaults: {', '.join(applied)}" if applied else ""
    not_applied = f"; not applied: {', '.join(unapplied)}" if unapplied else ""

    def outcome(ok: bool, warning: bool, detail: str) -> SmokeResult:
        return SmokeResult(chart.name, ok, warning, detail + not_applied, applied)

    r = client.chart_data(ctx)
    if r.status_code != 200:
        return outcome(False, False, f"HTTP {r.status_code}: {r.text[:500]}"
                       + (f"; queried{with_defaults}" if applied else ""))
    try:
        result = r.json()["result"]
        rows = sum(len(q.get("data") or []) for q in result)
    except Exception as e:  # noqa: BLE001 - malformed body is a failure, whatever the shape
        return outcome(False, False, f"unparseable chart/data response: {e}")
    if rows == 0:
        return outcome(True, True, f"query succeeded but returned 0 rows{with_defaults}")
    notes = [w for w in ((_fit_warning(chart, spec, result) or _bridge_warning(chart, result)
                          or _observations_warning(chart, rows) or _window_warning(chart, rows)),
                         _empty_values_warning(chart, queries, result, ds)) if w]
    if notes:
        return outcome(True, True, f"{rows} rows{with_defaults}; " + "; ".join(notes))
    return outcome(True, False, f"{rows} rows{with_defaults}")


def smoke(spec: DashboardSpec, resolution: Resolution, client: SupersetClient) -> list[SmokeResult]:
    defaults = filter_defaults(spec, resolution, client)
    return [smoke_chart(c, spec, resolution, client, defaults) for c in spec.charts]
