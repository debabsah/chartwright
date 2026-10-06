"""Pre-flight referential resolution: where the correctness guarantee is earned.

Every dataset triple, column, saved metric, and ad-hoc aggregate column in the
spec is resolved against the live Superset metadata API. All failures are
collected (not fail-fast) and surfaced as machine-readable errors BEFORE import.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .client import SupersetClient
from .spec import DATASET_FILTER_TYPES, DashboardSpec, DatasetRef, parse_metric


@dataclass
class ResolvedDataset:
    id: int
    uuid: str
    table: str
    schema: str | None
    database_name: str
    columns: list[str]
    metrics: list[str]
    main_dttm_col: str | None = None
    # Superset GenericDataType per column (0 numeric, 1 string, 2 temporal,
    # 3 boolean); absent when the instance doesn't report it. Consumed by the
    # design brain's type-aware rules; resolution itself only needs names.
    column_types: dict[str, int] = field(default_factory=dict)
    temporal_columns: list[str] = field(default_factory=list)

    def is_temporal(self, column: str) -> bool | None:
        """True/False when the instance reported a type, None when unknown."""
        if column in self.temporal_columns:
            return True
        tg = self.column_types.get(column)
        return None if tg is None else tg == 2


@dataclass
class ResolutionError:
    code: str          # dataset_not_found | dataset_ambiguous | column_not_found | metric_not_found | bad_metric
    #                    | superset_version_too_old | superset_version_unknown (chartwright.versions)
    #                    | owner_not_found | owner_ambiguous | owner_account_unknown (chartwright.owners)
    #                    | theme_not_found | theme_ambiguous | theme_lookup_failed (dashboard.theme)
    #                    | waterfall_opening_axis (a bridge's opening, on a theme without the axis fix)
    chart: str | None
    ref: str
    detail: str
    # column_not_found: the dataset's closest column names, best first, so an
    # agent can correct the spec without parsing `detail` or asking Superset.
    # owner_not_found / owner_ambiguous: the owner values to write instead.
    candidates: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Resolution:
    datasets: dict[str, ResolvedDataset] = field(default_factory=dict)  # DatasetRef.key() -> resolved
    errors: list[ResolutionError] = field(default_factory=list)
    # Custom SQL (SQL(...) AS label metrics, sql filters) names columns inside
    # free text, so resolution cannot check it: listed here, never an error.
    # Apply's data check runs every chart's query, which is where bad SQL fails.
    unchecked_sql: list[dict] = field(default_factory=list)
    # The Superset release the spec was held to (chartwright.versions), when a
    # version-gated field made it matter; None when no gated field is in use or
    # the instance did not say. Fields that release ignores warn here.
    superset_version: str | None = None
    version_warnings: list[dict] = field(default_factory=list)
    # dashboard.owners as the user ids apply PUTs after the import (the named
    # accounts plus the signed-in one, chartwright.owners); None when the spec
    # leaves owners alone.
    owner_ids: list[int] | None = None
    # dashboard.theme resolved on this instance (Superset 6.0+): the theme's id, which
    # the bundle carries as theme_id, and its uuid, which an export names. None when
    # the spec names no theme.
    theme_id: int | None = None
    theme_uuid: str | None = None

    @property
    def ok(self) -> bool:
        return not self.errors

    def for_chart(self, ref: DatasetRef) -> ResolvedDataset:
        return self.datasets[ref.key()]


def resolve(spec: DashboardSpec, client: SupersetClient,
            superset_version: str | None = None) -> Resolution:
    """`superset_version` states the target release; omitted, the instance is
    asked, and only when the spec uses a version-gated field."""
    res = Resolution()
    _check_version(spec, client, res, superset_version)
    cache: dict[str, ResolvedDataset | None] = {}

    def dataset_for(ref: DatasetRef) -> ResolvedDataset | None:
        key = ref.key()
        if key not in cache:
            cache[key] = _resolve_dataset(ref, client, res)
        if cache[key] is not None:
            res.datasets[key] = cache[key]
        return cache[key]

    for chart in spec.charts:
        ds = dataset_for(chart.dataset)
        if ds is None:
            continue
        _check_chart_fields(chart, ds, res)
        _check_where(chart.filters, chart.name, ds, res, "filter column")

    for f in spec.filters:
        if f.type not in DATASET_FILTER_TYPES:
            continue
        ds = dataset_for(f.dataset)
        if ds is None:
            continue
        where = f"filter:{f.name}"
        if f.type in ("select", "range"):
            _check_column(f.column, where, ds, res, "native filter column")
            _check_where(f.pre_filter, where, ds, res, "pre_filter column")
            if f.time_column:
                _check_column(f.time_column, where, ds, res, "pre-filter time_column")
        if f.type == "select" and f.sort_metric and f.sort_metric not in ds.metrics:
            # The filter form offers saved metrics only (FiltersConfigForm.tsx sortMetric).
            res.errors.append(ResolutionError(
                "metric_not_found", where, f.sort_metric,
                f"sort_metric must be a saved metric on {ds.table!r} (has: {ds.metrics})",
            ))
        if f.type == "time_column" and f.default:
            _check_column(f.default, where, ds, res, "time_column default")
    if spec.dashboard.theme is not None and not any(e.ref == "theme" for e in res.errors):
        _resolve_theme(spec.dashboard.theme, client, res)
        if res.theme_id is not None:
            _check_opening_axis(spec, client, res)
    if spec.dashboard.owners is not None:
        from .owners import resolve_owners

        res.owner_ids, owner_errors = resolve_owners(spec.dashboard.owners, client)
        res.errors += [ResolutionError(e.code, None, e.ref, e.detail, e.candidates)
                       for e in owner_errors]
    return res


def _check_version(spec: DashboardSpec, client: SupersetClient, res: Resolution,
                   stated: str | None) -> None:
    from .versions import check_spec_version, format_version, gated_fields_used, parse_version

    uses = gated_fields_used(spec)
    if not uses:
        release = parse_version(stated)
        res.superset_version = format_version(release) if release else None
        return
    checked = check_spec_version(spec, stated if stated else client.superset_version(), uses)
    res.superset_version = checked.version
    res.version_warnings = checked.warnings
    res.errors += [ResolutionError(e["code"], e["chart"], e["ref"], e["detail"]) for e in checked.errors]


def _resolve_theme(name: str, client: SupersetClient, res: Resolution) -> None:
    """dashboard.theme by name -> the instance's theme (Superset 6.0+). Names match
    exactly; theme_name has no unique constraint, so two themes of one name are an
    error, never a guess. Runs only after the version check passed: a release before
    6.0.0 has no theme API."""
    import difflib

    from .client import SupersetAPIError

    try:
        themes = client.themes()
    except SupersetAPIError as e:
        res.errors.append(ResolutionError(
            "theme_lookup_failed", None, "theme",
            f"the instance's themes could not be read ({e}); the account needs read access "
            f"to themes (Superset's \"can read on Theme\") to apply dashboard.theme"))
        return
    found = [t for t in themes if t.get("theme_name") == name]
    if len(found) == 1:
        res.theme_id, res.theme_uuid = found[0]["id"], str(found[0].get("uuid"))
        return
    names = sorted({str(t.get("theme_name")) for t in themes if t.get("theme_name")})
    if found:
        res.errors.append(ResolutionError(
            "theme_ambiguous", None, "theme",
            f"{len(found)} themes are named {name!r} on this instance (ids "
            f"{sorted(t['id'] for t in found)}); rename all but one in Superset "
            f"(Settings > Themes)"))
        return
    near = difflib.get_close_matches(name, names, n=3, cutoff=0.5)
    hint = f"; did you mean {', '.join(repr(n) for n in near)}?" if near else "."
    res.errors.append(ResolutionError(
        "theme_not_found", None, "theme",
        f"no theme named {name!r} on this instance{hint} Themes: "
        f"{', '.join(names) if names else 'none'}", near))


def keeps_zero_on_waterfalls(theme_json) -> bool:
    """A theme's JSON sets the waterfall's value axis to keep zero (ECharts yAxis.scale
    false), for waterfalls or every chart. Superset 6.x merges these over each chart's
    own options (plugin-chart-echarts components/Echart.tsx, mergeEchartsThemeOverrides
    at 6.1.0, from SupersetTheme echartsOptionsOverrides and ...ByChartType)."""
    import json

    try:
        config = json.loads(theme_json) if isinstance(theme_json, str) else (theme_json or {})
    except ValueError:
        return False
    for overrides in ((config.get("echartsOptionsOverridesByChartType") or {}).get("waterfall"),
                      config.get("echartsOptionsOverrides")):
        y_axis = (overrides or {}).get("yAxis") if isinstance(overrides, dict) else None
        if isinstance(y_axis, dict) and y_axis.get("scale") is False:
            return True
    return False


def _check_opening_axis(spec: DashboardSpec, client: SupersetClient, res: Resolution) -> None:
    """A bridge drawn with an opening total needs a theme that keeps zero on the value
    axis: the plugin's own axis floats up to the smallest total (defaultYAxis scale: true)
    and cuts the opening away (seen on 6.1.0)."""
    opened = [c.name for c in spec.charts if getattr(c, "opening", None)]
    if not opened:
        return
    from .client import SupersetAPIError

    try:
        theme_json = client.get(f"/api/v1/theme/{res.theme_id}")["result"].get("json_data")
    except SupersetAPIError as e:
        theme_json, failed = None, str(e)
    else:
        failed = None
    if keeps_zero_on_waterfalls(theme_json):
        return
    why = (f"its JSON could not be read ({failed})" if failed else
           "its JSON doesn't keep zero on a waterfall's value axis")
    for name in opened:
        res.errors.append(ResolutionError(
            "waterfall_opening_axis", name, "opening",
            f"opening draws the bridge's first bar as a total, which needs zero on the value "
            f"axis, and dashboard.theme {spec.dashboard.theme!r} can't give it: {why}. Add "
            f"{{\"echartsOptionsOverridesByChartType\": {{\"waterfall\": {{\"yAxis\": "
            f"{{\"scale\": false}}}}}}}} to the theme's JSON in Superset (Settings > Themes), "
            f"or drop opening and the first step rises from zero"))


def _check_where(filters, where: str, ds: "ResolvedDataset", res: "Resolution", what: str) -> None:
    for f in filters:
        if f.sql is not None:
            res.unchecked_sql.append({"chart": where, "sql": f.sql})
        else:
            _check_column(f.column, where, ds, res, what)


def _resolve_dataset(ref: DatasetRef, client: SupersetClient, res: Resolution) -> ResolvedDataset | None:
    candidates = client.find_datasets(ref.table)
    matches = []
    for c in candidates:
        db_name = (c.get("database") or {}).get("database_name")
        schema = c.get("schema") or None
        if db_name != ref.database:
            continue
        if ref.schema_ is not None and schema != ref.schema_:
            continue
        matches.append(c)
    if not matches:
        near = [f"{(c.get('database') or {}).get('database_name')}/{c.get('schema') or ''}/{c['table_name']}" for c in candidates]
        res.errors.append(ResolutionError(
            "dataset_not_found", None, ref.key(),
            f"no dataset {ref.table!r} in database {ref.database!r}"
            + (f" schema {ref.schema_!r}" if ref.schema_ else "")
            + (f"; same-named candidates: {near}" if near else ""),
        ))
        return None
    if len(matches) > 1:
        cands = [f"{(m.get('database') or {}).get('database_name')}/{m.get('schema') or ''}/{m['table_name']}" for m in matches]
        res.errors.append(ResolutionError(
            "dataset_ambiguous", None, ref.key(),
            f"multiple datasets match; add a schema to disambiguate: {cands}",
        ))
        return None
    m = matches[0]
    detail = client.dataset_detail(m["id"])
    cols = detail.get("columns", [])
    return ResolvedDataset(
        id=m["id"],
        uuid=str(m["uuid"]),
        table=m["table_name"],
        schema=m.get("schema") or None,
        database_name=(m.get("database") or {}).get("database_name"),
        columns=[c["column_name"] for c in cols],
        metrics=[x["metric_name"] for x in detail.get("metrics", [])],
        main_dttm_col=detail.get("main_dttm_col") or None,
        column_types={c["column_name"]: c["type_generic"] for c in cols
                      if c.get("type_generic") is not None},
        temporal_columns=[c["column_name"] for c in cols
                          if c.get("is_dttm") or c.get("type_generic") == 2],
    )


def _check_metric(metric: str, chart_name: str, ds: ResolvedDataset, res: Resolution) -> None:
    adhoc = parse_metric(metric)
    if adhoc is not None and adhoc.get("sql") is not None:
        res.unchecked_sql.append({"chart": chart_name, "sql": adhoc["sql"], "metric": metric})
        return
    if adhoc is None:
        if metric not in ds.metrics:
            res.errors.append(ResolutionError(
                "metric_not_found", chart_name, metric,
                f"not a saved metric on {ds.table!r} (has: {ds.metrics}) and not an "
                f"ad-hoc aggregate of the form AGG(column), AGG in SUM/AVG/COUNT/COUNT_DISTINCT/MIN/MAX, "
                f"or a custom-SQL metric SQL(expression) AS Label",
            ))
        return
    col = adhoc["column"]
    if col != "*" and col not in ds.columns:
        _missing_column(col, chart_name, ds, res, f"ad-hoc metric {metric!r}: column {col!r}")


SUGGESTIONS = 3       # close matches named per missing column
LISTED_COLUMNS = 50   # the dataset's columns listed in the message, then "and N more"


def close_columns(col: str, columns: list[str]) -> list[str]:
    """Up to SUGGESTIONS column names closest to `col`, best first. A match
    that differs only in case always comes first; the rest are by spelling
    similarity (difflib, ignoring case)."""
    import difflib

    exact = [c for c in columns if c.lower() == col.lower()]
    lowered = {c.lower(): c for c in columns}
    near = difflib.get_close_matches(col.lower(), list(lowered), n=SUGGESTIONS, cutoff=0.6)
    out = exact + [lowered[n] for n in near if lowered[n] not in exact]
    return out[:SUGGESTIONS]


def _missing_column(col: str, chart_name: str, ds: ResolvedDataset, res: Resolution, lead: str) -> None:
    candidates = close_columns(col, ds.columns)
    hint = f"; did you mean {', '.join(repr(c) for c in candidates)}?" if candidates else "."
    shown = ds.columns[:LISTED_COLUMNS]
    more = f", and {len(ds.columns) - LISTED_COLUMNS} more" if len(ds.columns) > LISTED_COLUMNS else ""
    res.errors.append(ResolutionError(
        "column_not_found", chart_name, col,
        f"{lead} not on dataset {ds.table!r}{hint} Columns: {', '.join(shown)}{more}",
        candidates,
    ))


def _check_column(col: str, chart_name: str, ds: ResolvedDataset, res: Resolution, what: str = "column") -> None:
    if col not in ds.columns:
        _missing_column(col, chart_name, ds, res, f"{what} {col!r}")


def _check_chart_fields(chart, ds: ResolvedDataset, res: Resolution) -> None:
    t = chart.type
    if getattr(chart, "series_limit_metric", None):
        # The metric that ranks the series for a series limit (line, bar, area, scatter).
        _check_metric(chart.series_limit_metric, chart.name, ds, res)
    if t in ("big_number_total", "big_number_trend"):
        _check_metric(chart.metric, chart.name, ds, res)
        if t == "big_number_trend":
            _check_column(chart.time_column, chart.name, ds, res, "time_column")
    elif t in ("timeseries_line", "timeseries_bar", "timeseries_area", "timeseries_scatter"):
        for m in chart.metrics:
            _check_metric(m, chart.name, ds, res)
        _check_column(chart.time_column, chart.name, ds, res, "time_column")
        if chart.groupby:
            _check_column(chart.groupby, chart.name, ds, res, "groupby")
    elif t == "bar":
        for m in chart.metrics:
            _check_metric(m, chart.name, ds, res)
        if chart.sort_metric() and chart.sort_metric() not in chart.metrics:
            _check_metric(chart.sort_metric(), chart.name, ds, res)
        _check_column(chart.x_column, chart.name, ds, res, "x_column")
        if chart.groupby:
            _check_column(chart.groupby, chart.name, ds, res, "groupby")
    elif t == "mixed":
        _check_column(chart.x_column, chart.name, ds, res, "x_column")
        for series in (chart.a, chart.b):
            for m in series.metrics:
                _check_metric(m, chart.name, ds, res)
            if series.groupby:
                _check_column(series.groupby, chart.name, ds, res, "groupby")
            if series.series_limit_metric:
                _check_metric(series.series_limit_metric, chart.name, ds, res)
    elif t == "pie":
        _check_metric(chart.metric, chart.name, ds, res)
        _check_column(chart.groupby, chart.name, ds, res, "groupby")
    elif t == "table":
        for c in chart.columns or []:
            _check_column(c, chart.name, ds, res)
        for m in chart.metrics or []:
            _check_metric(m, chart.name, ds, res)
        for g in chart.groupby or []:
            _check_column(g, chart.name, ds, res, "groupby")
        if chart.sort_by:
            # Resolved like everything else it references: raw mode sorts by a
            # column, aggregate mode by a metric. Unchecked, a typo here used to
            # pass check and then silently not sort.
            if chart.columns:
                _check_column(chart.sort_by, chart.name, ds, res, "sort_by")
            else:
                _check_metric(chart.sort_by, chart.name, ds, res)
    elif t == "pivot_table":
        for m in chart.metrics:
            _check_metric(m, chart.name, ds, res)
        for c in chart.rows:
            _check_column(c, chart.name, ds, res, "pivot row")
        for c in chart.columns:
            _check_column(c, chart.name, ds, res, "pivot column")
    elif t == "heatmap":
        _check_metric(chart.metric, chart.name, ds, res)
        _check_column(chart.x_column, chart.name, ds, res, "x_column")
        _check_column(chart.y_column, chart.name, ds, res, "y_column")
    elif t == "histogram":
        _check_column(chart.column, chart.name, ds, res, "column")
        if chart.groupby:
            _check_column(chart.groupby, chart.name, ds, res, "groupby")
    elif t == "funnel":
        _check_metric(chart.metric, chart.name, ds, res)
        _check_column(chart.groupby, chart.name, ds, res, "groupby")
    elif t == "treemap":
        _check_metric(chart.metric, chart.name, ds, res)
        for g in chart.groupby:
            _check_column(g, chart.name, ds, res, "groupby")
    elif t == "waterfall":
        _check_metric(chart.metric, chart.name, ds, res)
        _check_column(chart.x_column, chart.name, ds, res, "x_column")
        if chart.groupby:
            _check_column(chart.groupby, chart.name, ds, res, "groupby")
    elif t == "box_plot":
        for m in chart.metrics:
            _check_metric(m, chart.name, ds, res)
        for c in chart.distribute_across:
            _check_column(c, chart.name, ds, res, "distribute_across")
        for g in chart.groupby:
            _check_column(g, chart.name, ds, res, "groupby")
