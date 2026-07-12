import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from chartwright.spec import load_spec, parse_metric

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "sales_overview.json").read_text())


def _mutate(**overrides):
    data = json.loads(json.dumps(FIXTURE))
    data.update(overrides)
    return data


def test_fixture_valid():
    spec = load_spec(FIXTURE)
    assert spec.dashboard.slug == "sdc-sales-overview"
    assert len(spec.charts) == 6


def test_duplicate_chart_names_rejected():
    data = json.loads(json.dumps(FIXTURE))
    data["charts"][1]["name"] = "Total Orders"
    data["layout"]["rows"] = [["Total Orders"]]
    with pytest.raises(ValidationError, match="duplicate chart name"):
        load_spec(data)


def test_unknown_chart_in_layout_rejected():
    data = json.loads(json.dumps(FIXTURE))
    data["layout"]["rows"][0][0] = "Nope"
    with pytest.raises(ValidationError, match="unknown chart"):
        load_spec(data)


def test_unplaced_chart_rejected():
    data = json.loads(json.dumps(FIXTURE))
    data["layout"]["rows"][0] = data["layout"]["rows"][0][1:]
    with pytest.raises(ValidationError, match="not placed"):
        load_spec(data)


def test_row_width_overflow_rejected():
    data = json.loads(json.dumps(FIXTURE))
    for c in data["charts"]:
        if c["name"] in ("Sales Over Time", "Sales by Deal Size"):
            c["width"] = 8
    with pytest.raises(ValidationError, match="sum to"):
        load_spec(data)


def test_implicit_width_split():
    spec = load_spec(FIXTURE)
    assert spec.resolved_item_width("Total Orders") == 4
    assert spec.resolved_item_width("Sales Over Time") == 8
    assert spec.resolved_item_width("Sales by Product Line") == 12


def test_heights_default_by_type():
    spec = load_spec(FIXTURE)
    assert spec.resolved_height("Total Orders") == 4
    assert spec.resolved_height("Sales Over Time") == 8


def test_table_mode_exclusive():
    data = json.loads(json.dumps(FIXTURE))
    for c in data["charts"]:
        if c["name"] == "Sales by Product Line":
            c["columns"] = ["product_line"]
    with pytest.raises(ValidationError, match="not both"):
        load_spec(data)


def test_extra_fields_forbidden():
    data = json.loads(json.dumps(FIXTURE))
    data["charts"][0]["surprise"] = 1
    with pytest.raises(ValidationError):
        load_spec(data)


def test_parse_metric():
    assert parse_metric("SUM(sales)") == {"aggregate": "SUM", "column": "sales", "label": None}
    assert parse_metric("COUNT_DISTINCT(customer)") == {"aggregate": "COUNT_DISTINCT", "column": "customer", "label": None}
    assert parse_metric("COUNT(*)") == {"aggregate": "COUNT", "column": "*", "label": None}
    assert parse_metric("count") is None          # saved metric name
    assert parse_metric("MEDIAN(x)") is None       # not in whitelist -> treated as saved name
