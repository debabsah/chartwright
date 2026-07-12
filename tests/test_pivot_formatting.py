"""Pivot presentation surface: metric labels, combine_metric, date_format,
conditional_formatting. Shapes mirror a real production pivot chart export."""

import io
import json
import zipfile

import pytest
import yaml
from pydantic import ValidationError

from chartwright.compiler import compile_bundle
from chartwright.decompile import _format_to_spec, _metric_to_spec
from chartwright.spec import load_spec, metric_label, parse_metric
from chartwright.testing import stub_resolution


def _pivot_spec(**pivot_extra):
    return load_spec({
        "spec_version": "1",
        "dashboard": {"title": "T", "slug": "sdc-t"},
        "charts": [{
            "name": "P",
            "type": "pivot_table",
            "dataset": {"database": "examples", "table": "t"},
            "rows": ["klass"],
            "columns": ["week"],
            "metrics": ["MAX(pct_of_goal) AS % of Goal", "SUM(minutes)"],
            **pivot_extra,
        }],
        "layout": {"rows": [["P"]]},
    })


def _params(spec):
    zf = zipfile.ZipFile(io.BytesIO(compile_bundle(spec, stub_resolution(spec))))
    chart = next(n for n in zf.namelist() if "/charts/" in n)
    return json.loads(json.dumps(yaml.safe_load(zf.read(chart))["params"]))


RAG = [
    {"metric": "% of Goal", "operator": "<", "target": 1.0, "color": "green"},
    {"metric": "% of Goal", "operator": "between", "target_left": 1.0, "target_right": 1.25, "color": "amber"},
    {"metric": "% of Goal", "operator": ">", "target": 1.25, "color": "red"},
]


def test_parse_metric_label():
    assert parse_metric("MAX(col) AS Pretty Label") == {"aggregate": "MAX", "column": "col", "label": "Pretty Label"}
    assert parse_metric("MAX(col)") == {"aggregate": "MAX", "column": "col", "label": None}
    assert parse_metric("saved_metric") is None
    assert metric_label("MAX(col) AS Pretty") == "Pretty"
    assert metric_label("MAX(col)") == "MAX(col)"
    assert metric_label("saved_metric") == "saved_metric"


def test_labelled_metric_payload():
    p = _params(_pivot_spec())
    labelled, plain = p["metrics"]
    assert labelled["label"] == "% of Goal"
    assert labelled["hasCustomLabel"] is True
    assert labelled["column"] == {"column_name": "pct_of_goal"}
    assert plain["label"] == "SUM(minutes)"
    assert plain["hasCustomLabel"] is False


def test_plain_pivot_emits_no_new_keys():
    p = _params(_pivot_spec())
    assert "combineMetric" not in p
    assert "date_format" not in p
    assert "conditional_formatting" not in p


def test_pivot_formatting_emission():
    p = _params(_pivot_spec(
        combine_metric=True,
        date_format="%m/%d/%y",
        conditional_formatting=RAG,
    ))
    assert p["combineMetric"] is True
    assert p["date_format"] == "%m/%d/%y"
    assert p["conditional_formatting"] == [
        {"column": "% of Goal", "colorScheme": "#ACE1C4", "operator": "<", "targetValue": 1.0},
        {"column": "% of Goal", "colorScheme": "#FDE380", "operator": "between",
         "targetValueLeft": 1.0, "targetValueRight": 1.25},
        {"column": "% of Goal", "colorScheme": "#EFA1AA", "operator": ">", "targetValue": 1.25},
    ]


def test_format_rule_must_reference_a_metric_label():
    with pytest.raises(ValidationError, match="not one of the chart's metric labels"):
        _pivot_spec(conditional_formatting=[
            {"metric": "MAX(pct_of_goal)", "operator": "<", "target": 1.0, "color": "green"},
        ])


def test_format_rule_target_shapes():
    with pytest.raises(ValidationError, match="needs target_left"):
        _pivot_spec(conditional_formatting=[
            {"metric": "% of Goal", "operator": "between", "target": 1.0, "color": "green"},
        ])
    with pytest.raises(ValidationError, match="needs target "):
        _pivot_spec(conditional_formatting=[
            {"metric": "% of Goal", "operator": "<", "target_left": 1.0, "target_right": 2.0, "color": "red"},
        ])


def test_decompile_roundtrip_helpers():
    losses = []
    m = {"expressionType": "SIMPLE", "aggregate": "MAX",
         "column": {"column_name": "pct_of_goal"}, "label": "% of Goal", "hasCustomLabel": True}
    assert _metric_to_spec(m, losses, "P") == "MAX(pct_of_goal) AS % of Goal"
    assert not losses

    assert _format_to_spec({"column": "% of Goal", "colorScheme": "#ACE1C4",
                            "operator": "<", "targetValue": 1.0}) == \
        {"metric": "% of Goal", "operator": "<", "color": "green", "target": 1.0}
    assert _format_to_spec({"column": "% of Goal", "colorScheme": "#FDE380", "operator": "between",
                            "targetValueLeft": 1.0, "targetValueRight": 1.25}) == \
        {"metric": "% of Goal", "operator": "between", "color": "amber",
         "target_left": 1.0, "target_right": 1.25}
    assert _format_to_spec({"column": "% of Goal", "colorScheme": "#123456",
                            "operator": "<", "targetValue": 1.0}) is None
    assert _format_to_spec({"column": "% of Goal", "colorScheme": "#ACE1C4",
                            "operator": "≤", "targetValue": 1.0}) is None


def test_full_roundtrip_compile_then_decompile():
    from chartwright.decompile import decompile_bundle

    spec = _pivot_spec(combine_metric=True, date_format="%m/%d/%y", conditional_formatting=RAG)
    bundle = compile_bundle(spec, stub_resolution(spec))
    res = stub_resolution(spec)
    ds = res.for_chart(spec.charts[0].dataset)
    lookup = {ds.uuid: {"database": "examples", "schema": None, "table": "t"}}
    result = decompile_bundle(bundle, lambda u: lookup.get(u))
    assert result.losses == [], result.losses_json()
    chart = result.spec["charts"][0]
    assert chart["combine_metric"] is True
    assert chart["date_format"] == "%m/%d/%y"
    assert chart["metrics"] == ["MAX(pct_of_goal) AS % of Goal", "SUM(minutes)"]
    assert chart["conditional_formatting"] == RAG


def test_cli_load_reads_utf8(tmp_path):
    """Spec files are UTF-8; a cp1252 default read mojibakes em-dashes (seen live:
    'Quarterly — Overview' imported as 'Quarterly â€” Overview')."""
    from chartwright.cli import _load

    p = tmp_path / "s.json"
    p.write_text(json.dumps({
        "spec_version": "1",
        "dashboard": {"title": "Quarterly — Overview", "slug": "sdc-t"},
        "charts": [{"name": "P — chart", "type": "big_number_total",
                    "dataset": {"database": "d", "table": "t"}, "metric": "COUNT(*)"}],
        "layout": {"rows": [["P — chart"]]},
    }, ensure_ascii=False), encoding="utf-8")
    spec = _load(str(p))
    assert spec.dashboard.title == "Quarterly — Overview"
    assert spec.charts[0].name == "P — chart"
