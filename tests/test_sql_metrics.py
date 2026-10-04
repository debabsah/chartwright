"""Custom SQL: metrics written as SQL(<expression>) AS <Label>, and chart
filters written as {"sql": "<condition>"}. They compile to Superset's
"Custom SQL" metric and filter, decompile back, and are listed by `check` as
unchecked (resolution cannot see columns inside free SQL); apply's data
check runs them."""

import json

import pytest
from pydantic import ValidationError

import chartwright.cli as cli
from chartwright.smoke import _mixed_queries, _query_for
from chartwright.spec import load_spec, metric_label, parse_metric
from chartwright.testing import stub_resolution

from test_dashboard_settings import DS, assert_lossless, chart_doc, roundtrip, spec_data

RATE = "SQL(100.0 * SUM(amount) / NULLIF(COUNT(*), 0)) AS Avg Order"


class FakeClient:
    """One dataset: orders(region, city, amount, ordered_at, active) with a saved metric."""

    def find_datasets(self, table):
        return [{"id": 1, "uuid": "11111111-1111-1111-1111-111111111111", "table_name": table,
                 "schema": None, "database": {"database_name": "warehouse"}}]

    def dataset_detail(self, dataset_id):
        cols = ["region", "city", "amount", "ordered_at", "active"]
        return {"columns": [{"column_name": c} for c in cols], "metrics": [{"metric_name": "order_count"}]}


def table(**fields):
    return spec_data(charts=[{"name": "T", "type": "table", "dataset": DS, **fields}])


def line(**fields):
    return spec_data(charts=[{"name": "L", "type": "timeseries_line", "time_column": "ordered_at",
                              "metrics": [RATE], "dataset": DS, **fields}])


# -- the metric syntax ----------------------------------------------------------------


def test_parse_and_label():
    assert parse_metric(RATE) == {"aggregate": None, "column": None,
                                  "sql": "100.0 * SUM(amount) / NULLIF(COUNT(*), 0)", "label": "Avg Order"}
    assert metric_label(RATE) == "Avg Order"
    # SQL containing AS keeps the last " AS " as the label separator
    assert parse_metric("SQL(CAST(amount AS INT)) AS Whole")["sql"] == "CAST(amount AS INT)"


def test_a_sql_metric_compiles_to_a_custom_sql_metric():
    (m,) = chart_doc(load_spec(line()), "L")["params"]["metrics"]
    assert {k: m[k] for k in ("expressionType", "sqlExpression", "label", "hasCustomLabel")} == {
        "expressionType": "SQL", "sqlExpression": "100.0 * SUM(amount) / NULLIF(COUNT(*), 0)",
        "label": "Avg Order", "hasCustomLabel": True}


@pytest.mark.parametrize("metric", ["SQL(SUM(a))", "SQL() AS Empty", "SQL(SUM(a)) AS  "])
def test_a_sql_metric_needs_sql_and_a_label(metric):
    with pytest.raises(ValidationError, match="SQL\\(<expression>\\) AS <Label>"):
        load_spec(line(metrics=[metric]))


def test_a_sql_metric_works_wherever_a_metric_does():
    data = table(metrics=[RATE], groupby=["region"], sort_by=RATE,
                 conditional_formatting=[{"metric": "Avg Order", "operator": ">", "target": 50, "color": "green"}],
                 number_formats={"Avg Order": ",.2f"})
    params = chart_doc(load_spec(data), "T")["params"]
    assert params["timeseries_limit_metric"]["sqlExpression"].startswith("100.0")
    assert params["conditional_formatting"][0]["column"] == "Avg Order"
    assert_lossless(load_spec(data))


def test_sql_metrics_round_trip_on_every_metric_shape():
    data = spec_data(charts=[
        {"name": "K", "type": "big_number_total", "metric": RATE, "dataset": DS},
        {"name": "M", "type": "mixed", "x_column": "ordered_at", "dataset": DS,
         "a": {"metrics": [RATE]}, "b": {"metrics": ["SQL(MAX(amount) - MIN(amount)) AS Spread"], "kind": "line"}},
    ])
    out = assert_lossless(load_spec(data))
    assert out.spec["charts"][0]["metric"] == RATE


def test_ui_built_sql_metrics_decompile():
    def edit(path, doc):
        if "/charts/" in path:
            doc["params"]["metrics"] = [
                {"expressionType": "SQL", "sqlExpression": "SUM(a)/SUM(b)", "label": "SUM(a)/SUM(b)",
                 "hasCustomLabel": False},
                {"expressionType": "SQL", "sqlExpression": "sum(amount)", "label": "x", "hasCustomLabel": False},
            ]
    out = roundtrip(load_spec(line()), edit)
    assert out.spec["charts"][0]["metrics"] == ["SQL(SUM(a)/SUM(b)) AS SUM(a)/SUM(b)", "SUM(amount)"]
    assert out.losses == []


# -- custom SQL chart filters ------------------------------------------------------------


def test_a_sql_filter_compiles_to_a_custom_sql_where():
    params = chart_doc(load_spec(line(filters=[{"sql": " amount > 0 OR status = 'refunded' "},
                                               {"column": "region", "value": "West"}])), "L")["params"]
    assert params["adhoc_filters"] == [
        {"clause": "WHERE", "expressionType": "SQL", "sqlExpression": "amount > 0 OR status = 'refunded'"},
        {"clause": "WHERE", "expressionType": "SIMPLE", "subject": "region", "operator": "==",
         "comparator": "West"},
    ]


@pytest.mark.parametrize("bad, needle", [
    ({"sql": "a > 1", "column": "a"}, "takes no column"),
    ({"sql": "a > 1", "op": ">"}, "takes no column"),
    ({"sql": "a > 1", "value": 1}, "takes no column"),
    ({"sql": "   "}, "non-empty SQL"),
    ({"op": "=="}, "needs a column"),
])
def test_bad_sql_filters_are_named(bad, needle):
    with pytest.raises(ValidationError) as e:
        load_spec(line(filters=[bad]))
    assert needle in str(e.value)


def test_sql_filters_round_trip_and_having_is_a_loss():
    data = line(filters=[{"sql": "amount > 0"}])
    assert_lossless(load_spec(data))

    def edit(path, doc):
        if "/charts/" in path:
            doc["params"]["adhoc_filters"].append(
                {"clause": "HAVING", "expressionType": "SQL", "sqlExpression": "SUM(amount) > 10"})
    out = roundtrip(load_spec(data), edit)
    assert out.spec["charts"][0]["filters"] == [{"sql": "amount > 0"}]
    assert any("HAVING" in loss.what for loss in out.losses)


def test_the_data_check_sends_sql_filters_as_extra_where():
    spec = load_spec(line(filters=[{"sql": "amount > 0"}, {"sql": "region <> ''"},
                                   {"column": "status", "value": "paid"}]))
    q = _query_for(spec.charts[0], spec)
    assert q["extras"]["where"] == "(amount > 0) AND (region <> '')"
    assert q["filters"] == [{"col": "status", "op": "==", "val": "paid"}]
    plain = load_spec(line())
    assert "where" not in _query_for(plain.charts[0], plain)["extras"]
    mixed = load_spec(spec_data(charts=[{
        "name": "M", "type": "mixed", "x_column": "ordered_at", "dataset": DS,
        "filters": [{"sql": "amount > 0"}], "a": {"metrics": ["SUM(amount)"]}, "b": {"metrics": ["COUNT(*)"]}}]))
    ds = stub_resolution(mixed).for_chart(mixed.charts[0].dataset)
    assert all(q["extras"]["where"] == "(amount > 0)" for q in _mixed_queries(mixed.charts[0], mixed, ds))


# -- check says what it could not check ---------------------------------------------------


def test_resolution_lists_custom_sql_as_unchecked_and_still_checks_columns():
    from chartwright.resolver import resolve

    data = line(filters=[{"sql": "amount > 0"}, {"column": "regoin", "value": "West"}])
    res = resolve(load_spec(data), FakeClient())
    assert [(e.code, e.ref) for e in res.errors] == [("column_not_found", "regoin")]
    assert res.unchecked_sql == [
        {"chart": "L", "sql": "100.0 * SUM(amount) / NULLIF(COUNT(*), 0)", "metric": RATE},
        {"chart": "L", "sql": "amount > 0"},
    ]


def test_check_prints_unchecked_sql(monkeypatch, tmp_path, capsys):
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(line()), encoding="utf-8")
    monkeypatch.setattr(cli, "_client", lambda profile: FakeClient())
    with pytest.raises(SystemExit) as e:
        cli.main(["check", str(path), "--profile", "p", "--design", "off"])
    out = json.loads(capsys.readouterr().out)
    assert e.value.code == 0 and out["ok"] is True
    assert out["unchecked_sql"][0]["metric"] == RATE

    path.write_text(json.dumps(spec_data()), encoding="utf-8")
    with pytest.raises(SystemExit):
        cli.main(["check", str(path), "--profile", "p", "--design", "off"])
    assert "unchecked_sql" not in json.loads(capsys.readouterr().out)
