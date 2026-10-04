"""A chart's own settings: description, certification, cache timeout, tags,
colour scheme, and the shorter title shown on the dashboard
(sliceNameOverride). Each compiles, decompiles back, is compared by `plan`,
and travels in the in-place chart update a re-apply makes."""

import pytest
from pydantic import ValidationError

from chartwright.apply import chart_payloads_from_bundle
from chartwright.compiler import compile_bundle
from chartwright.spec import load_spec
from chartwright.testing import stub_resolution

from test_dashboard_settings import (
    DS, assert_lossless, chart_doc, dashboard_doc, plan_against, roundtrip, spec_data,
)

META = {
    "description": "Net revenue after refunds.",
    "certified_by": "Finance",
    "certification_details": "Ties to the GL",
    "cache_timeout": 600,
    "tags": ["finance"],
    "display_name": "Revenue",
}


def with_chart(**fields):
    data = spec_data()
    data["charts"][1].update(fields)
    return data


def test_omitted_chart_settings_compile_to_what_the_tool_always_wrote():
    doc = chart_doc(load_spec(spec_data()), "Revenue by Region")
    for key in ("description", "certified_by", "certification_details", "cache_timeout"):
        assert doc[key] is None
    assert "tags" not in doc and "color_scheme" not in doc["params"]
    nodes = dashboard_doc(load_spec(spec_data()))["position"].values()
    assert not any("sliceNameOverride" in (n.get("meta") or {}) for n in nodes if isinstance(n, dict))


def test_chart_settings_reach_the_chart_export():
    spec = load_spec(with_chart(**META, color_scheme="bnbColors"))
    doc = chart_doc(spec, "Revenue by Region")
    assert doc["description"] == "Net revenue after refunds."
    assert doc["certified_by"] == "Finance" and doc["certification_details"] == "Ties to the GL"
    assert doc["cache_timeout"] == 600 and doc["tags"] == ["finance"]
    assert doc["params"]["color_scheme"] == "bnbColors"
    chart = next(n for n in dashboard_doc(spec)["position"].values()
                 if isinstance(n, dict) and n.get("type") == "CHART"
                 and n["meta"]["sliceName"] == "Revenue by Region")
    assert chart["meta"]["sliceNameOverride"] == "Revenue"


def test_a_display_name_equal_to_the_name_is_no_override():
    spec = load_spec(with_chart(display_name="Revenue by Region"))
    assert spec.charts[1].display_name is None


def test_display_name_reaches_a_sketch_chart_too():
    data = with_chart(display_name="By region")
    data["layout"] = {"sketch": ["AAAA BBBBBBBB"], "legend": {"A": "Revenue", "B": "Revenue by Region"}}
    nodes = dashboard_doc(load_spec(data))["position"].values()
    metas = [n["meta"] for n in nodes if isinstance(n, dict) and n.get("type") == "CHART"]
    assert {m["sliceName"]: m.get("sliceNameOverride") for m in metas} == {
        "Revenue": None, "Revenue by Region": "By region"}


SCHEME_CHARTS = {
    "timeseries_line": {"time_column": "ts", "metrics": ["SUM(a)"]},
    "timeseries_bar": {"time_column": "ts", "metrics": ["SUM(a)"]},
    "timeseries_area": {"time_column": "ts", "metrics": ["SUM(a)"]},
    "timeseries_scatter": {"time_column": "ts", "metrics": ["SUM(a)"]},
    "bar": {"x_column": "r", "metrics": ["SUM(a)"]},
    "pie": {"metric": "SUM(a)", "groupby": "r"},
    "histogram": {"column": "a"},
    "funnel": {"metric": "SUM(a)", "groupby": "r"},
    "treemap": {"metric": "SUM(a)", "groupby": ["r"]},
    "mixed": {"x_column": "ts", "a": {"metrics": ["SUM(a)"]}, "b": {"metrics": ["SUM(b)"]}},
}


@pytest.mark.parametrize("chart_type", sorted(SCHEME_CHARTS))
def test_color_scheme_on_every_chart_whose_panel_has_it(chart_type):
    chart = {"name": "C", "type": chart_type, "dataset": DS, "color_scheme": "lyftColors",
             **SCHEME_CHARTS[chart_type]}
    spec = load_spec(spec_data(charts=[chart]))
    assert chart_doc(spec, "C")["params"]["color_scheme"] == "lyftColors"
    out = assert_lossless(spec)
    assert out.spec["charts"][0]["color_scheme"] == "lyftColors"


@pytest.mark.parametrize("chart", [
    {"name": "T", "type": "table", "columns": ["a"], "dataset": DS},
    {"name": "K", "type": "big_number_total", "metric": "COUNT(*)", "dataset": DS},
    {"name": "P", "type": "pivot_table", "rows": ["a"], "metrics": ["COUNT(*)"], "dataset": DS},
    {"name": "H", "type": "heatmap", "x_column": "a", "y_column": "b", "metric": "COUNT(*)", "dataset": DS},
])
def test_color_scheme_is_rejected_where_superset_has_no_such_control(chart):
    with pytest.raises(ValidationError, match="color_scheme"):
        load_spec(spec_data(charts=[{**chart, "color_scheme": "bnbColors"}]))


@pytest.mark.parametrize("bad, needle", [
    ({"certification_details": "x"}, "needs certified_by"),
    ({"cache_timeout": 0}, "greater than or equal to 1"),
    ({"display_name": ""}, "at least 1 character"),
    ({"description": ""}, "at least 1 character"),
    ({"tags": ["a", "a"]}, "unique"),
])
def test_bad_chart_settings_are_named(bad, needle):
    with pytest.raises(ValidationError) as e:
        load_spec(with_chart(**bad))
    assert needle in str(e.value)


def test_chart_settings_round_trip_through_decompile():
    out = assert_lossless(load_spec(with_chart(**META, color_scheme="bnbColors")))
    chart = next(c for c in out.spec["charts"] if c["name"] == "Revenue by Region")
    for key, value in {**META, "color_scheme": "bnbColors"}.items():
        assert chart[key] == value, key


def test_ui_values_outside_the_spec_are_named_not_kept():
    def edit(path, doc):
        if "/charts/" in path and doc["slice_name"] == "Revenue by Region":
            doc["cache_timeout"] = -1
            doc["certification_details"] = "orphan details"
            doc["tags"] = ["ok", "owner:1"]
    out = roundtrip(load_spec(spec_data()), edit)
    chart = next(c for c in out.spec["charts"] if c["name"] == "Revenue by Region")
    assert "cache_timeout" not in chart and "certification_details" not in chart
    assert chart["tags"] == ["ok"]
    text = " ".join(loss.what for loss in out.losses)
    assert "cache_timeout -1" in text and "certification_details" in text and "owner:1" in text


def test_a_table_keeps_ignoring_a_stored_color_scheme():
    """Superset can store color_scheme on charts that have no such control;
    it does nothing there, so decompile neither keeps it nor calls it lost."""
    data = spec_data(charts=[{"name": "T", "type": "table", "columns": ["a"], "dataset": DS}])

    def edit(path, doc):
        if "/charts/" in path:
            doc["params"]["color_scheme"] = "bnbColors"
    out = roundtrip(load_spec(data), edit)
    assert "color_scheme" not in out.spec["charts"][0] and out.losses == []


@pytest.mark.parametrize("key, value", [
    ("description", "changed in the UI"), ("certified_by", "Someone"),
    ("cache_timeout", 60), ("tags", ["other"]),
])
def test_plan_reports_a_changed_chart_setting(key, value, monkeypatch):
    def edit(path, doc):
        if "/charts/" in path and doc["slice_name"] == "Revenue by Region":
            doc[key] = value
    out = plan_against(load_spec(with_chart(**META)), edit, monkeypatch)
    assert out["charts_changed"] == ["Revenue by Region"]
    assert out["dashboard_settings_changed"] == [] and out["layout_changed"] is False


def test_plan_reports_a_changed_dashboard_title_for_a_chart(monkeypatch):
    def edit(path, doc):
        if "/dashboards/" in path:
            for node in doc["position"].values():
                if isinstance(node, dict) and node.get("type") == "CHART":
                    node["meta"]["sliceNameOverride"] = "Renamed on the dashboard"
    out = plan_against(load_spec(spec_data()), edit, monkeypatch)
    assert sorted(out["charts_changed"]) == ["Revenue", "Revenue by Region"]


def test_plan_leaves_chart_tags_alone_when_the_spec_omits_them(monkeypatch):
    def edit(path, doc):
        if "/charts/" in path:
            doc["tags"] = ["added-in-ui"]
    assert plan_against(load_spec(spec_data()), edit, monkeypatch)["clean"] is True
    out = plan_against(load_spec(with_chart(tags=[])), edit, monkeypatch)
    assert out["charts_changed"] == ["Revenue by Region"]


def test_plan_is_clean_with_chart_settings(monkeypatch):
    out = plan_against(load_spec(with_chart(**META, color_scheme="bnbColors")), None, monkeypatch)
    assert out["clean"] is True


def test_a_reapply_updates_the_chart_settings_in_place():
    """The importer never overwrites an existing chart, so the in-place PUT
    carries them; omitted ones go as null so a removed description clears."""
    spec = load_spec(with_chart(**META))
    payloads = chart_payloads_from_bundle(compile_bundle(spec, stub_resolution(spec)))
    by_name = {p["slice_name"]: p for p in payloads.values()}
    assert by_name["Revenue by Region"]["description"] == "Net revenue after refunds."
    assert by_name["Revenue by Region"]["certified_by"] == "Finance"
    assert by_name["Revenue by Region"]["certification_details"] == "Ties to the GL"
    assert by_name["Revenue by Region"]["cache_timeout"] == 600
    assert {k: by_name["Revenue"][k] for k in ("description", "certified_by", "cache_timeout")} == {
        "description": None, "certified_by": None, "cache_timeout": None}

