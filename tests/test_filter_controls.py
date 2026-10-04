"""Native filter controls: description, cascading dependencies, search-all,
inverse selection, pre-filters and sort metric, time grain and time column
filters, and chart scoping on the time range picker. Each compiles to the
filter configuration Superset's own filter form saves, decompiles back, and
is compared by `plan`."""

import pytest
from pydantic import ValidationError

from chartwright.apply import scoped_filter_fixup
from chartwright.compiler import filter_id
from chartwright.resolver import resolve
from chartwright.spec import load_spec

from test_dashboard_settings import (
    DS, assert_lossless, dashboard_doc, edit_dashboard, plan_against, roundtrip, spec_data,
)
from test_sql_metrics import FakeClient

REGION = {"type": "select", "name": "Region", "column": "region", "dataset": DS}
CITY = {"type": "select", "name": "City", "column": "city", "dataset": DS}
AMOUNT = {"type": "range", "name": "Amount", "column": "amount", "dataset": DS}
PERIOD = {"type": "time_range", "name": "Period"}
GRAIN = {"type": "time_grain", "name": "Grain", "dataset": DS}
BASIS = {"type": "time_column", "name": "Date Basis", "dataset": DS}


def with_filters(*filters):
    return spec_data(filters=[dict(f) for f in filters])


def configs(data) -> dict[str, dict]:
    nf = dashboard_doc(load_spec(data))["metadata"]["native_filter_configuration"]
    return {f["name"]: f for f in nf}


def decompiled_filters(data, edit=None) -> dict[str, dict]:
    out = roundtrip(load_spec(data), edit)
    return {f["name"]: f for f in out.spec.get("filters", [])}, out.losses


# -- defaults ---------------------------------------------------------------------


def test_omitted_controls_compile_to_what_the_tool_always_wrote():
    c = configs(with_filters(REGION, AMOUNT, PERIOD))
    for f in c.values():
        assert f["description"] == "" and f["cascadeParentIds"] == []
        assert not {"adhoc_filters", "time_range", "granularity_sqla", "sortMetric",
                    "sdc_scope_charts"} & set(f)
    assert c["Region"]["controlValues"] == {
        "multiSelect": True, "defaultToFirstItem": False, "enableEmptyFilter": False,
        "inverseSelection": False, "searchAllOptions": False}


# -- description, search-all, inverse selection ---------------------------------------


def test_description_and_select_flags():
    c = configs(with_filters({**REGION, "description": "Departure region",
                              "search_all_options": True, "inverse_selection": True}))
    assert c["Region"]["description"] == "Departure region"
    assert c["Region"]["controlValues"]["searchAllOptions"] is True
    assert c["Region"]["controlValues"]["inverseSelection"] is True


def test_description_works_on_every_filter_type():
    filters = [{**f, "description": f"about {f['name']}"} for f in (REGION, AMOUNT, PERIOD, GRAIN, BASIS)]
    c = configs(with_filters(*filters))
    assert {n: f["description"] for n, f in c.items()} == {
        n: f"about {n}" for n in ("Region", "Amount", "Period", "Grain", "Date Basis")}
    assert_lossless(load_spec(with_filters(*filters)))


# -- dependencies ------------------------------------------------------------------


def test_dependencies_compile_to_parent_filter_ids():
    c = configs(with_filters(REGION, PERIOD, {**CITY, "dependencies": ["Region", "Period"]},
                             {**AMOUNT, "dependencies": ["City"]}))
    assert c["City"]["cascadeParentIds"] == [filter_id("orders", "Region"), filter_id("orders", "Period")]
    assert c["Amount"]["cascadeParentIds"] == [filter_id("orders", "City")]


@pytest.mark.parametrize("filters, needle", [
    ([REGION, {**CITY, "dependencies": ["Nope"]}], "unknown filter 'Nope'"),
    ([{**REGION, "dependencies": ["Region"]}], "cannot depend on itself"),
    ([{**REGION, "dependencies": ["City"]}, {**CITY, "dependencies": ["Region"]}], "cycle"),
    ([GRAIN, {**CITY, "dependencies": ["Grain"]}], "a time_grain filter"),
    ([REGION, {**CITY, "dependencies": []}], "omitted or non-empty"),
    ([REGION, {**CITY, "dependencies": ["Region", "Region"]}], "duplicate"),
    ([REGION, {**PERIOD, "dependencies": ["Region"]}], "Extra inputs"),
])
def test_bad_dependencies_are_named(filters, needle):
    with pytest.raises(ValidationError) as e:
        load_spec(with_filters(*filters))
    assert needle in str(e.value)


def test_dependencies_round_trip_and_a_missing_parent_is_a_loss():
    data = with_filters(REGION, {**CITY, "dependencies": ["Region"]})
    assert_lossless(load_spec(data))

    def drop_region(doc):
        nf = doc["metadata"]["native_filter_configuration"]
        doc["metadata"]["native_filter_configuration"] = [f for f in nf if f["name"] != "Region"]
    found, losses = decompiled_filters(data, edit_dashboard(drop_region))
    assert "dependencies" not in found["City"]
    assert any("dependency on" in loss.what for loss in losses)


# -- pre-filter and sort metric -----------------------------------------------------


def test_pre_filter_time_range_and_sort_metric():
    c = configs(with_filters(
        {**REGION, "pre_filter": [{"column": "active", "value": True}, {"sql": "region <> ''"}],
         "time_range": "Last year", "time_column": "ordered_at", "sort_metric": "order_count"},
        {**AMOUNT, "pre_filter": [{"column": "status", "op": "!=", "value": "void"}]}))
    region = c["Region"]
    assert region["adhoc_filters"] == [
        {"clause": "WHERE", "expressionType": "SIMPLE", "subject": "active", "operator": "==", "comparator": True},
        {"clause": "WHERE", "expressionType": "SQL", "sqlExpression": "region <> ''"},
    ]
    assert (region["time_range"], region["granularity_sqla"]) == ("Last year", "ordered_at")
    # 6.1.0 reads controlValues.sortMetric, 4.1.4/5.0.0 the top level: both are written
    assert region["sortMetric"] == region["controlValues"]["sortMetric"] == "order_count"
    assert c["Amount"]["adhoc_filters"][0]["operator"] == "!="
    assert "sortMetric" not in c["Amount"]


@pytest.mark.parametrize("bad, needle", [
    ({**REGION, "time_range": "Last year"}, "go together"),
    ({**AMOUNT, "time_column": "ordered_at"}, "go together"),
    ({**AMOUNT, "sort_metric": "order_count"}, "Extra inputs"),
    ({**REGION, "pre_filter": [{"column": "a", "op": "IN", "value": []}]}, "non-empty list"),
])
def test_bad_pre_filters_are_named(bad, needle):
    with pytest.raises(ValidationError) as e:
        load_spec(with_filters(bad))
    assert needle in str(e.value)


def test_pre_filters_round_trip():
    assert_lossless(load_spec(with_filters(
        {**REGION, "pre_filter": [{"column": "active", "value": True}], "time_range": "Last year",
         "time_column": "ordered_at", "sort_metric": "order_count", "search_all_options": True,
         "inverse_selection": True},
        {**AMOUNT, "pre_filter": [{"sql": "amount > 0"}], "time_range": "Last month",
         "time_column": "ordered_at"})))


def test_an_older_release_sort_metric_location_decompiles():
    def move(doc):
        for f in doc["metadata"]["native_filter_configuration"]:
            f["controlValues"].pop("sortMetric", None)  # 4.1.4/5.0.0 keep only the top level
    found, _ = decompiled_filters(with_filters({**REGION, "sort_metric": "order_count"}), edit_dashboard(move))
    assert found["Region"]["sort_metric"] == "order_count"


def test_a_pre_filter_time_range_without_its_column_is_a_loss():
    def strip(doc):
        doc["metadata"]["native_filter_configuration"][0].pop("granularity_sqla")
    found, losses = decompiled_filters(
        with_filters({**REGION, "time_range": "Last year", "time_column": "ordered_at"}), edit_dashboard(strip))
    assert "time_range" not in found["Region"]
    assert any("without a time column" in loss.what for loss in losses)


# -- time grain and time column filters ----------------------------------------------


def test_time_grain_and_time_column_filters_compile():
    c = configs(with_filters({**GRAIN, "default": "P1M"}, {**BASIS, "default": "ordered_at", "required": True}))
    grain, basis = c["Grain"], c["Date Basis"]
    assert grain["filterType"] == "filter_timegrain" and basis["filterType"] == "filter_timecolumn"
    assert set(grain["targets"][0]) == {"datasetUuid"} and set(basis["targets"][0]) == {"datasetUuid"}
    assert grain["defaultDataMask"] == {"extraFormData": {"time_grain_sqla": "P1M"},
                                        "filterState": {"value": ["P1M"]}, "ownState": {}}
    assert basis["defaultDataMask"]["extraFormData"] == {"granularity_sqla": "ordered_at"}
    assert grain["controlValues"] == {"enableEmptyFilter": False}
    assert basis["controlValues"] == {"enableEmptyFilter": True}


def test_time_grain_and_time_column_filters_round_trip():
    assert_lossless(load_spec(with_filters(
        {**GRAIN, "default": "P1W", "charts": ["Revenue"]}, {**BASIS, "default": "ordered_at", "required": True},
        GRAIN | {"name": "No default"})))


def test_a_time_grain_filter_without_its_dataset_is_a_loss():
    def strip(doc):
        doc["metadata"]["native_filter_configuration"][0]["targets"] = [{}]
    found, losses = decompiled_filters(with_filters(GRAIN), edit_dashboard(strip))
    assert found == {} and any("time_grain filter dataset" in loss.what for loss in losses)


# -- time range scoping --------------------------------------------------------------


def test_the_time_range_picker_scopes_to_named_charts():
    data = with_filters({**PERIOD, "default": "Last month", "charts": ["Revenue by Region"]})
    assert configs(data)["Period"]["sdc_scope_charts"] == ["Revenue by Region"]
    assert_lossless(load_spec(data))
    meta = {"native_filter_configuration": list(configs(data).values())}
    fixed, errors = scoped_filter_fixup(meta, load_spec(data), {"Revenue": 7, "Revenue by Region": 8})
    assert errors == []
    (nf,) = fixed["native_filter_configuration"]
    assert nf["scope"] == {"rootPath": ["ROOT_ID"], "excluded": [7]} and nf["chartsInScope"] == [8]


def test_a_time_range_scope_names_known_charts():
    with pytest.raises(ValidationError, match="scopes unknown chart 'Nope'"):
        load_spec(with_filters({**PERIOD, "charts": ["Nope"]}))


# -- plan ------------------------------------------------------------------------------


@pytest.mark.parametrize("name, change", [
    ("Region", lambda f: f.update(description="edited")),
    ("City", lambda f: f.update(cascadeParentIds=[])),
    ("Region", lambda f: f["controlValues"].update(searchAllOptions=False)),
    ("Region", lambda f: f.update(time_range="Last week")),
    ("Grain", lambda f: f["defaultDataMask"].update(filterState={"value": ["P1D"]})),
])
def test_plan_reports_a_changed_filter_control(name, change, monkeypatch):
    data = with_filters({**REGION, "description": "d", "search_all_options": True,
                         "time_range": "Last year", "time_column": "ordered_at"},
                        {**CITY, "dependencies": ["Region"]}, {**GRAIN, "default": "P1M"})

    def edit(doc):
        change(next(f for f in doc["metadata"]["native_filter_configuration"] if f["name"] == name))
    assert plan_against(load_spec(data), None, monkeypatch)["clean"] is True
    out = plan_against(load_spec(data), edit_dashboard(edit), monkeypatch)
    assert out["filters_changed"] == [name]


# -- resolution ------------------------------------------------------------------------


def _codes(data):
    return [(e.code, e.chart, e.ref) for e in resolve(load_spec(data), FakeClient()).errors]


def test_resolution_checks_pre_filter_columns_sort_metric_and_time_column_default():
    data = with_filters(
        {**REGION, "pre_filter": [{"column": "activ", "value": True}], "time_range": "Last year",
         "time_column": "orderd_at", "sort_metric": "SUM(amount)"},
        {**BASIS, "default": "shipped_at"}, GRAIN)
    assert _codes(data) == [
        ("column_not_found", "filter:Region", "activ"),
        ("column_not_found", "filter:Region", "orderd_at"),
        ("metric_not_found", "filter:Region", "SUM(amount)"),
        ("column_not_found", "filter:Date Basis", "shipped_at"),
    ]


def test_resolution_passes_good_filter_controls():
    data = with_filters({**REGION, "pre_filter": [{"column": "active", "value": True}],
                         "sort_metric": "order_count"}, {**BASIS, "default": "ordered_at"}, GRAIN)
    assert _codes(data) == []
