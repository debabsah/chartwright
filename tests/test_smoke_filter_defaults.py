"""Apply's smoke check queries each chart as the dashboard queries it on load: with
the default of every native filter in the chart's scope.

Found live on 6.1.0: a required single-select "Carrier" filter (default "All
carriers") scoped to 88 charts, over tables holding one row set per carrier. Smoke
queried without the filter, so a 9-row status table counted every carrier's rows
(20, its row_limit) and smoke told 30+ charts to grow while the dashboard fit. The
same counts can call a chart empty, or not, wrongly."""

import copy

from chartwright.smoke import SmokeResult, _query_for, _with_time_range, filter_defaults, smoke
from chartwright.spec import load_spec
from chartwright.testing import stub_resolution

SHIP = {"database": "db", "table": "shipments"}
FLEET = {"database": "db", "table": "fleet"}
CARRIERS = ["All carriers", "Acme", "Bolt"]
STATUSES = [f"s{i}" for i in range(9)]
SHIPMENTS = [{"carrier": c, "status": s, "region": "West", "n": 1, "ts": 0}
             for c in CARRIERS for s in STATUSES]
CARRIER = {"type": "select", "name": "Carrier", "dataset": SHIP, "column": "carrier",
           "multi": False, "required": True, "default": ["All carriers"]}
STATUS_TABLE = {"type": "table", "name": "Status", "dataset": SHIP, "columns": ["status", "n"],
                "row_limit": 20, "height": 10}


class Resp:
    def __init__(self, body=None, status_code=200, text=""):
        self.status_code, self.text, self._body = status_code, text, body

    def json(self):
        return self._body


def _match(row, f):
    v, op, val = row[f["col"]], f["op"], f["val"]
    return {"IN": lambda: v in val, "NOT IN": lambda: v not in val, ">=": lambda: v >= val,
            "<=": lambda: v <= val, "==": lambda: v == val}.get(op, lambda: True)()


class Warehouse:
    """POST /api/v1/chart/data over in-memory rows: applies each query's filters by
    column name, dropping one on a column the rows lack as Superset does, then its
    row_limit. A one-column query with an orderby and no metric is a filter's value
    query: its distinct values, ordered. Records every query context it answers."""

    def __init__(self, tables, refuse=None):
        self.tables, self.refuse, self.sent = tables, refuse, []

    def chart_data(self, ctx):
        self.sent.append(copy.deepcopy(ctx))
        if self.refuse and self.refuse(ctx):
            return Resp(status_code=500, text="boom")
        rows = self.tables[ctx["datasource"]["id"]]
        result = []
        for q in ctx["queries"]:
            data = [r for r in rows if all(_match(r, f) for f in q["filters"] if f["col"] in r)]
            if q["orderby"] and not q["metrics"] and len(q["columns"]) == 1:
                col, ascending = q["columns"][0], q["orderby"][0][1]
                data = [{col: v} for v in sorted({r[col] for r in data}, reverse=not ascending)]
            result.append({"data": data[:q["row_limit"]]})
        return Resp({"result": result})


def _spec(charts, filters=()):
    return load_spec({
        "spec_version": "1",
        "dashboard": {"title": "T", "slug": "sdc-t"},
        "charts": charts,
        "filters": list(filters),
        "layout": {"rows": [[c["name"]] for c in charts]},
    })


def _setup(charts, filters=(), main_dttm_col="ts", tables=None):
    spec = _spec(charts, filters)
    res = stub_resolution(spec)
    for key, ds in res.datasets.items():
        ds.main_dttm_col = main_dttm_col if key == stub_key(SHIP) else None
        ds.columns = (["carrier", "status", "region", "n", "ts"] if key == stub_key(SHIP)
                      else ["truck", "n"])
    by_table = {ds.table: ds.id for ds in res.datasets.values()}
    rows = tables or {"shipments": SHIPMENTS, "fleet": [{"truck": "T1", "n": 1}]}
    return spec, res, Warehouse({by_table[t]: r for t, r in rows.items() if t in by_table})


def stub_key(ref):
    return f"{ref['database']}/{ref.get('schema') or ''}/{ref['table']}"


def _by_chart(results):
    return {r.chart: r for r in results}


def _chart_query(wh, n=0):
    """The n-th chart query smoke sent (value queries skipped)."""
    return [c for c in wh.sent if c["queries"][0]["row_limit"] != 1][n]["queries"]


def test_the_status_table_reads_the_rows_the_dashboard_shows():
    spec, res, wh = _setup([STATUS_TABLE])
    (unfiltered,) = smoke(spec, res, wh)
    assert unfiltered.warning and unfiltered.detail.startswith("20 rows; table renders ~20 rows")

    spec, res, wh = _setup([STATUS_TABLE], [CARRIER])
    (r,) = smoke(spec, res, wh)
    assert r.ok and not r.warning, r.detail
    assert r.detail == "9 rows with the dashboard's filter defaults: Carrier = All carriers"
    assert r.filter_defaults == ["Carrier = All carriers"]
    (q,) = wh.sent[0]["queries"]
    assert q["filters"] == [{"col": "carrier", "op": "IN", "val": ["All carriers"]}]
    assert len(wh.sent) == 1  # a set default needs no query of its own


def test_a_filter_reaches_the_charts_in_its_scope_whose_dataset_has_its_column():
    other = {**STATUS_TABLE, "name": "Other"}
    trucks = {"type": "table", "name": "Trucks", "dataset": FLEET, "columns": ["truck"]}
    spec, res, wh = _setup([STATUS_TABLE, other, trucks],
                           [{**CARRIER, "charts": ["Status", "Trucks"]}])
    results = _by_chart(smoke(spec, res, wh))
    assert results["Status"].filter_defaults == ["Carrier = All carriers"]
    # out of scope, and in scope on a dataset without the column: no filter, no note
    for name in ("Other", "Trucks"):
        assert results[name].filter_defaults == [] and "filter defaults" not in results[name].detail
    sent = {c["queries"][0]["columns"][0]: c["queries"][0]["filters"] for c in wh.sent}
    assert sent["truck"] == []


def test_a_mixed_chart_gets_the_default_in_both_queries():
    mixed = {"type": "mixed", "name": "Mixed", "dataset": SHIP, "x_column": "status",
             "a": {"metrics": ["COUNT(*)"]}, "b": {"metrics": ["SUM(n)"], "kind": "line"}}
    spec, res, wh = _setup([mixed], [CARRIER])
    smoke(spec, res, wh)
    qa, qb = wh.sent[0]["queries"]
    assert qa["filters"] == qb["filters"] == [{"col": "carrier", "op": "IN", "val": ["All carriers"]}]


def test_a_multi_value_and_an_inverse_default():
    region = {"type": "select", "name": "Region", "dataset": SHIP, "column": "region",
              "default": ["West", "East"]}
    for extra, op, label in (({}, "IN", "Region = West or East"),
                             ({"inverse_selection": True}, "NOT IN", "Region excludes West and East")):
        spec, res, wh = _setup([STATUS_TABLE], [{**region, **extra}])
        (r,) = smoke(spec, res, wh)
        assert r.filter_defaults == [label]
        assert wh.sent[0]["queries"][0]["filters"] == [{"col": "region", "op": op, "val": ["West", "East"]}]


def test_range_defaults_apply_their_bounds():
    rng = {"type": "range", "name": "Count", "dataset": SHIP, "column": "n"}
    cases = [
        ({"ge": 1}, [{"col": "n", "op": ">=", "val": 1}], "Count >= 1"),
        ({"le": 5.5}, [{"col": "n", "op": "<=", "val": 5.5}], "Count <= 5.5"),
        ({"ge": 1, "le": 5}, [{"col": "n", "op": ">=", "val": 1}, {"col": "n", "op": "<=", "val": 5}],
         "1 <= Count <= 5"),
        ({"ge": 3, "le": 3}, [{"col": "n", "op": "==", "val": 3}], "Count = 3"),
    ]
    for bounds, clauses, label in cases:
        spec, res, wh = _setup([STATUS_TABLE], [{**rng, **bounds}])
        (r,) = smoke(spec, res, wh)
        assert wh.sent[0]["queries"][0]["filters"] == clauses, bounds
        assert r.filter_defaults == [label], bounds
    spec, res, wh = _setup([STATUS_TABLE], [rng])  # no bounds: no default
    assert smoke(spec, res, wh)[0].filter_defaults == []


def test_a_time_range_default_overrides_the_charts_own_and_binds_as_the_chart_does():
    line = {"type": "timeseries_line", "name": "Line", "dataset": SHIP, "metrics": ["COUNT(*)"],
            "time_column": "ts", "time_range": "Last year"}
    total = {"type": "big_number_total", "name": "Total", "dataset": SHIP, "metric": "COUNT(*)"}
    trucks = {"type": "big_number_total", "name": "Trucks", "dataset": FLEET, "metric": "COUNT(*)"}
    period = {"type": "time_range", "name": "Period", "default": "Last month"}
    spec, res, wh = _setup([line, total, trucks], [period])
    results = _by_chart(smoke(spec, res, wh))
    (lq,), (tq,), (fq,) = (c["queries"] for c in wh.sent)
    assert lq["time_range"] == "Last month"
    assert [f for f in lq["filters"] if f["op"] == "TEMPORAL_RANGE"] == [
        {"col": "ts", "op": "TEMPORAL_RANGE", "val": "Last month"}]
    assert tq["time_range"] == "Last month" and tq["granularity"] == "ts"
    assert results["Line"].filter_defaults == results["Total"].filter_defaults == ["Period = Last month"]
    # no time column to bind: the dashboard can't filter it, and smoke says nothing
    assert fq["time_range"] == "No filter" and "granularity" not in fq
    assert results["Trucks"].filter_defaults == []


def test_a_time_grain_default_regrains_the_time_axis():
    line = {"type": "timeseries_line", "name": "Line", "dataset": SHIP, "metrics": ["COUNT(*)"],
            "time_column": "ts", "time_grain": "P1D"}
    total = {"type": "big_number_total", "name": "Total", "dataset": SHIP, "metric": "COUNT(*)"}
    grain = {"type": "time_grain", "name": "Grain", "dataset": SHIP, "default": "P1M"}
    spec, res, wh = _setup([line, total], [grain])
    results = _by_chart(smoke(spec, res, wh))
    (lq,), (tq,) = (c["queries"] for c in wh.sent)
    assert lq["columns"][0]["timeGrain"] == "P1M" and lq["extras"]["time_grain_sqla"] == "P1M"
    assert results["Line"].filter_defaults == ["Grain = P1M"]
    assert results["Total"].filter_defaults == []  # no time axis to regrain


def test_default_to_first_reads_the_first_value_as_the_select_filter_does():
    year = {"type": "select", "name": "Carrier", "dataset": SHIP, "column": "carrier",
            "default_to_first": True, "sort_descending": True,
            "pre_filter": [{"column": "n", "op": ">=", "value": 1}, {"sql": "n < 9"}],
            "time_range": "Last year", "time_column": "ts"}
    spec, res, wh = _setup([STATUS_TABLE], [year])
    (r,) = smoke(spec, res, wh)
    value_ctx, chart_ctx = wh.sent
    assert value_ctx["datasource"] == {"id": res.for_chart(spec.charts[0].dataset).id, "type": "table"}
    assert value_ctx["queries"] == [{
        "columns": ["carrier"], "metrics": [], "orderby": [["carrier", False]],
        "filters": [{"col": "n", "op": ">=", "val": 1}], "extras": {"where": "(n < 9)"},
        "time_range": "Last year", "granularity": "ts", "row_limit": 1}]
    # Z-A, the first is "Bolt"
    assert chart_ctx["queries"][0]["filters"] == [{"col": "carrier", "op": "IN", "val": ["Bolt"]}]
    assert r.detail == "9 rows with the dashboard's filter defaults: Carrier = Bolt (its first value)"


def test_default_to_first_by_a_sort_metric_orders_by_it():
    f = {"type": "select", "name": "Carrier", "dataset": SHIP, "column": "carrier",
         "default_to_first": True, "sort_metric": "shipments"}
    spec, res, wh = _setup([STATUS_TABLE], [f])
    res.datasets[stub_key(SHIP)].metrics = ["shipments"]
    filter_defaults(spec, res, wh)
    (q,) = wh.sent[0]["queries"]
    assert q["metrics"] == ["shipments"] and q["orderby"] == [["shipments", True]]


def test_default_to_first_reads_its_values_under_its_parents_defaults():
    region = {"type": "select", "name": "Region", "dataset": SHIP, "column": "region", "default": ["East"]}
    period = {"type": "time_range", "name": "Period", "default": "Last month"}
    carrier = {"type": "select", "name": "Carrier", "dataset": SHIP, "column": "carrier",
               "default_to_first": True, "dependencies": ["Region", "Period"], "time_column": "ts",
               "time_range": "Last year"}
    # Only Bolt ships East: under Region's default Bolt comes first, where A-Z alone
    # would pick Acme.
    rows = SHIPMENTS + [{"carrier": "Bolt", "status": "s0", "region": "East", "n": 1, "ts": 0}]
    spec, res, wh = _setup([STATUS_TABLE], [carrier, region, period], tables={"shipments": rows})
    (r,) = smoke(spec, res, wh)
    (q,) = wh.sent[0]["queries"]
    assert q["filters"] == [{"col": "region", "op": "IN", "val": ["East"]}]
    assert q["time_range"] == "Last month"  # the parent's range wins over its own pre-filter
    assert r.filter_defaults == ["Carrier = Bolt (its first value)", "Region = East", "Period = Last month"]
    assert r.detail.startswith("1 rows with the dashboard's filter defaults: Carrier = Bolt")


def test_a_first_value_smoke_cannot_read_is_named_and_not_applied():
    first = {"type": "select", "name": "Carrier", "dataset": SHIP, "column": "carrier",
             "default_to_first": True}
    child = {"type": "select", "name": "Status", "dataset": SHIP, "column": "status",
             "default_to_first": True, "dependencies": ["Carrier"]}
    spec, res, wh = _setup([STATUS_TABLE], [first, child])
    wh.refuse = lambda ctx: ctx["queries"][0]["row_limit"] == 1
    (r,) = smoke(spec, res, wh)
    assert len(wh.sent) == 2  # Carrier's value query; Status never reads under an unknown parent
    assert r.filter_defaults == [] and _chart_query(wh)[0]["filters"] == []
    assert r.detail.startswith("20 rows; table renders ~20 rows")
    assert r.detail.endswith(
        "; not applied: Carrier's first value (reading it failed: HTTP 500), "
        "Status's first value (it depends on Carrier, which smoke could not apply)")


def test_a_first_value_of_no_values_applies_nothing():
    first = {"type": "select", "name": "Carrier", "dataset": SHIP, "column": "carrier",
             "default_to_first": True, "pre_filter": [{"column": "n", "op": ">=", "value": 99}]}
    spec, res, wh = _setup([STATUS_TABLE], [first])
    (r,) = smoke(spec, res, wh)
    assert r.filter_defaults == [] and "not applied" not in r.detail


def test_a_time_column_default_is_named_where_it_would_move_the_time_range():
    line = {"type": "timeseries_line", "name": "Line", "dataset": SHIP, "metrics": ["COUNT(*)"],
            "time_column": "ts"}
    total = {"type": "big_number_total", "name": "Total", "dataset": SHIP, "metric": "COUNT(*)"}
    column = {"type": "time_column", "name": "Date by", "dataset": SHIP, "default": "ts"}
    spec, res, wh = _setup([line, total], [column])
    results = _by_chart(smoke(spec, res, wh))
    assert results["Line"].detail.endswith(
        "; not applied: Date by = ts (smoke keeps the chart's own time column)")
    assert "not applied" not in results["Total"].detail  # no time range, no time axis: no change
    spec, res, wh = _setup([{**total, "time_range": "Last year"}], [column])
    assert "not applied: Date by = ts" in smoke(spec, res, wh)[0].detail


def test_a_failed_query_names_the_defaults_it_ran_with():
    spec, res, wh = _setup([STATUS_TABLE], [CARRIER])
    wh.refuse = lambda ctx: True
    (r,) = smoke(spec, res, wh)
    assert not r.ok
    assert r.detail == "HTTP 500: boom; queried with the dashboard's filter defaults: Carrier = All carriers"


def test_an_empty_chart_under_the_default_says_so():
    spec, res, wh = _setup([STATUS_TABLE], [{**CARRIER, "default": ["Nobody"]}])
    (r,) = smoke(spec, res, wh)
    assert r.warning and r.detail == (
        "query succeeded but returned 0 rows with the dashboard's filter defaults: Carrier = Nobody")


def test_without_filter_defaults_the_queries_and_the_report_are_unchanged():
    line = {"type": "timeseries_line", "name": "Line", "dataset": SHIP, "metrics": ["COUNT(*)"],
            "time_column": "ts", "time_range": "Last year"}
    no_default = {**CARRIER, "default": None, "required": False}
    spec, res, wh = _setup([STATUS_TABLE, line], [no_default])
    results = smoke(spec, res, wh)
    assert len(wh.sent) == 2
    for chart, ctx in zip(spec.charts, wh.sent):
        assert ctx["queries"] == [_with_time_range(_query_for(chart, spec), chart,
                                                   res.for_chart(chart.dataset))]
    assert [set(r.as_dict()) for r in results] == [{"chart", "ok", "warning", "detail"}] * 2
    assert SmokeResult("A", True, False, "1 rows", ["X = 1"]).as_dict()["filter_defaults"] == ["X = 1"]
