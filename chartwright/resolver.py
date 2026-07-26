"""Pre-flight referential resolution: where the correctness guarantee is earned.

Every dataset triple, column, saved metric, and ad-hoc aggregate column in the
spec is resolved against the live Superset metadata API. All failures are
collected (not fail-fast) and surfaced as machine-readable errors BEFORE import.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .client import SupersetClient
from .spec import DashboardSpec, DatasetRef, parse_metric


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
    chart: str | None
    ref: str
    detail: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Resolution:
    datasets: dict[str, ResolvedDataset] = field(default_factory=dict)  # DatasetRef.key() -> resolved
    errors: list[ResolutionError] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def for_chart(self, ref: DatasetRef) -> ResolvedDataset:
        return self.datasets[ref.key()]


def resolve(spec: DashboardSpec, client: SupersetClient) -> Resolution:
    res = Resolution()
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
        for f in chart.filters:
            _check_column(f.column, chart.name, ds, res, "filter column")

    for f in spec.filters:
        if f.type in ("select", "range"):
            ds = dataset_for(f.dataset)
            if ds is not None:
                _check_column(f.column, f"filter:{f.name}", ds, res, "native filter column")
    return res


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
    if adhoc is None:
        if metric not in ds.metrics:
            res.errors.append(ResolutionError(
                "metric_not_found", chart_name, metric,
                f"not a saved metric on {ds.table!r} (has: {ds.metrics}) and not an "
                f"ad-hoc aggregate of the form AGG(column), AGG in SUM/AVG/COUNT/COUNT_DISTINCT/MIN/MAX",
            ))
        return
    col = adhoc["column"]
    if col != "*" and col not in ds.columns:
        res.errors.append(ResolutionError(
            "column_not_found", chart_name, col,
            f"ad-hoc metric {metric!r}: column {col!r} not on dataset {ds.table!r}",
        ))


def _check_column(col: str, chart_name: str, ds: ResolvedDataset, res: Resolution, what: str = "column") -> None:
    if col not in ds.columns:
        res.errors.append(ResolutionError(
            "column_not_found", chart_name, col,
            f"{what} {col!r} not on dataset {ds.table!r} (has {len(ds.columns)} columns)",
        ))


def _check_chart_fields(chart, ds: ResolvedDataset, res: Resolution) -> None:
    t = chart.type
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
        _check_column(chart.x_column, chart.name, ds, res, "x_column")
        if chart.groupby:
            _check_column(chart.groupby, chart.name, ds, res, "groupby")
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
