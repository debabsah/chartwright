"""Formula annotations: goal and trend lines over line, bar, area, scatter and
mixed charts, compiled to Superset's FORMULA annotation layers (the shape the
explore panel's AnnotationLayer editor saves), decompiled back, and compared
by `plan`. Other layer types are named as losses, never kept silently."""

import pytest
from pydantic import ValidationError

from chartwright.spec import load_spec

from test_dashboard_settings import DS, assert_lossless, chart_doc, plan_against, roundtrip, spec_data

GOAL = {"name": "Goal", "value": 80, "style": "dashed", "color": "#1a7f37", "width": 2}
AXIS_CHARTS = {
    "timeseries_line": {"time_column": "ts", "metrics": ["SUM(a)"]},
    "timeseries_bar": {"time_column": "ts", "metrics": ["SUM(a)"]},
    "timeseries_area": {"time_column": "ts", "metrics": ["SUM(a)"]},
    "timeseries_scatter": {"time_column": "ts", "metrics": ["SUM(a)"]},
    "bar": {"x_column": "region", "metrics": ["SUM(a)"]},
    "mixed": {"x_column": "ts", "a": {"metrics": ["SUM(a)"]}, "b": {"metrics": ["SUM(b)"], "kind": "line"}},
}


def one_chart(chart_type="timeseries_line", **fields):
    chart = {"name": "Trend", "type": chart_type, "dataset": DS, **AXIS_CHARTS.get(chart_type, {}), **fields}
    return spec_data(charts=[chart])


def layers(data):
    return chart_doc(load_spec(data), "Trend")["params"].get("annotation_layers")


def test_no_annotations_writes_no_layers():
    assert layers(one_chart()) is None


@pytest.mark.parametrize("chart_type", sorted(AXIS_CHARTS))
def test_a_goal_line_compiles_to_a_formula_layer(chart_type):
    (layer,) = layers(one_chart(chart_type, annotations=[GOAL]))
    assert layer == {
        "name": "Goal", "annotationType": "FORMULA", "sourceType": "", "value": "80",
        "color": "#1A7F37", "opacity": "", "style": "dashed", "width": 2,
        "showMarkers": False, "hideLine": False, "overrides": {}, "show": True,
        "showLabel": False, "titleColumn": "", "descriptionColumns": [], "timeColumn": "",
        "intervalEndColumn": "",
    }


def test_a_threshold_on_a_horizontal_bar_is_the_same_layer_drawn_upright():
    """transformFormulaAnnotation maps each point to [y, x] on a horizontal chart
    (Timeseries/transformers.ts, 4.1.4 :381-383, 5.0.0 :387-389, 6.1.0 :482-484), so a
    value lands on the value axis, which runs across: the line stands upright at 1.0x.
    Seen live on 4.1.4, 5.0.0 and 6.1.0. The layer itself is unchanged."""
    threshold = {"name": "Break-even", "value": 1, "style": "dashed", "color": "#B3261E"}
    data = one_chart("bar", annotations=[threshold], orientation="horizontal")
    p = chart_doc(load_spec(data), "Trend")["params"]
    assert p["orientation"] == "horizontal"
    vertical = layers(one_chart("bar", annotations=[threshold]))
    assert p["annotation_layers"] == vertical and vertical[0]["value"] == "1"
    out = assert_lossless(load_spec(data))
    assert out.spec["charts"][0]["annotations"] == [threshold]


def test_a_formula_and_the_optional_settings():
    (layer,) = layers(one_chart(annotations=[
        {"name": "Trend line", "formula": " 0.5*x + 3 ", "style": "dotted", "opacity": "low", "width": 1.5}]))
    assert layer["value"] == "0.5*x + 3"
    assert (layer["style"], layer["opacity"], layer["width"], layer["color"]) == ("dotted", "opacityLow", 1.5, None)


def test_small_values_are_written_without_an_exponent():
    (layer,) = layers(one_chart(annotations=[{"name": "Tiny", "value": 1e-07}]))
    assert layer["value"] == "0.0000001"


@pytest.mark.parametrize("formula, value", [("80", 80), ("y = 0.95", 0.95), ("y=-3", -3)])
def test_a_plain_number_formula_is_a_value(formula, value):
    spec = load_spec(one_chart(annotations=[{"name": "Goal", "formula": formula}]))
    (a,) = spec.charts[0].annotations
    assert a.value == value and a.formula is None


@pytest.mark.parametrize("bad, needle", [
    ({"name": "Goal"}, "exactly one of value or formula"),
    ({"name": "Goal", "value": 1, "formula": "x"}, "exactly one of value or formula"),
    ({"name": "Goal", "value": 1, "style": "longDashed"}, "solid"),
    ({"name": "Goal", "value": 1, "color": "green"}, "pattern"),
    ({"name": "Goal", "value": 1, "width": 0}, "greater than 0"),
    ({"name": "Goal", "value": 1, "opacity": "half"}, "low"),
    ({"name": "", "value": 1}, "at least 1 character"),
    ({"name": "Goal", "value": float("inf")}, "finite"),
])
def test_bad_annotations_are_named(bad, needle):
    with pytest.raises(ValidationError) as e:
        load_spec(one_chart(annotations=[bad]))
    assert needle in str(e.value)


def test_annotation_names_are_unique_per_chart():
    with pytest.raises(ValidationError, match="duplicate annotation names"):
        load_spec(one_chart(annotations=[{"name": "Goal", "value": 1}, {"name": "Goal", "value": 2}]))


@pytest.mark.parametrize("chart", [
    {"name": "Trend", "type": "pie", "metric": "SUM(a)", "groupby": "r", "dataset": DS},
    {"name": "Trend", "type": "table", "columns": ["a"], "dataset": DS},
    {"name": "Trend", "type": "big_number_total", "metric": "SUM(a)", "dataset": DS},
])
def test_annotations_only_on_charts_superset_draws_them_on(chart):
    with pytest.raises(ValidationError, match="annotations"):
        load_spec(spec_data(charts=[{**chart, "annotations": [GOAL]}]))


@pytest.mark.parametrize("chart_type", sorted(AXIS_CHARTS))
def test_annotations_round_trip(chart_type):
    out = assert_lossless(load_spec(one_chart(chart_type, annotations=[
        GOAL, {"name": "Trend", "formula": "2*x + 1", "opacity": "high"}])))
    assert out.spec["charts"][0]["annotations"] == [
        {"name": "Goal", "value": 80, "color": "#1A7F37", "style": "dashed", "width": 2},
        {"name": "Trend", "formula": "2*x + 1", "opacity": "high"},
    ]


def test_other_layer_types_are_named_losses():
    def edit(path, doc):
        if "/charts/" in path:
            doc["params"]["annotation_layers"] = [
                {"name": "Releases", "annotationType": "EVENT", "sourceType": "NATIVE", "value": 3},
                {"name": "Hidden", "annotationType": "FORMULA", "value": "5", "show": False},
                {"name": "Long", "annotationType": "FORMULA", "value": "5", "style": "longDashed"},
            ]
    out = roundtrip(load_spec(one_chart()), edit)
    assert out.spec["charts"][0]["annotations"] == [{"name": "Long", "value": 5}]
    text = " ".join(loss.what for loss in out.losses)
    assert "'Releases' (EVENT)" in text and "hidden annotation layer 'Hidden'" in text
    assert "line style 'longDashed'" in text


def test_plan_reports_a_changed_goal(monkeypatch):
    def edit(path, doc):
        if "/charts/" in path:
            doc["params"]["annotation_layers"][0]["value"] = "90"
    out = plan_against(load_spec(one_chart(annotations=[GOAL])), edit, monkeypatch)
    assert out["charts_changed"] == ["Trend"]
    out = plan_against(load_spec(one_chart(annotations=[GOAL])), None, monkeypatch)
    assert out["clean"] is True
