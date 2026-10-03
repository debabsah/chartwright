"""When `plan` is blocked by the spec, it returns the same typed
`resolution_errors` list as `check` and `apply`, not a Python repr inside
`detail` that no script could parse."""

import json

import chartwright.resolver as resolver
from chartwright import ids
from chartwright.dashdiff import plan
from chartwright.resolver import Resolution, ResolutionError
from chartwright.spec import load_spec

SPEC = load_spec({
    "spec_version": "1",
    "dashboard": {"title": "Orders", "slug": "orders"},
    "charts": [{"name": "Revenue by Region", "type": "bar", "x_column": "Region",
                "metrics": ["SUM(amount)"],
                "dataset": {"database": "warehouse", "table": "orders"}}],
    "layout": {"rows": [["Revenue by Region"]]},
})


class Client:
    def __init__(self, uuid):
        self.uuid = uuid

    def find_dashboard_by_slug(self, slug):
        return {"id": 3, "uuid": self.uuid}


def test_missing_references_come_back_as_a_typed_list(monkeypatch):
    err = ResolutionError("column_not_found", "Revenue by Region", "Region",
                          "x_column 'Region' not on dataset 'orders'", ["region"])
    monkeypatch.setattr(resolver, "resolve", lambda spec, client: Resolution(errors=[err]))
    p = plan(SPEC, Client(str(ids.dashboard_uuid("orders"))))
    out = json.loads(p.to_json())
    assert out["dashboard"] == "blocked" and out["clean"] is False
    assert out["resolution_errors"] == [err.as_dict()]
    assert out["resolution_errors"][0]["candidates"] == ["region"]
    assert "resolution_errors" in out["detail"] and "{" not in out["detail"]


def test_a_slug_chartwright_did_not_build_is_blocked_with_no_errors():
    out = json.loads(plan(SPEC, Client("someone-elses-uuid")).to_json())
    assert out["dashboard"] == "blocked"
    assert out["resolution_errors"] == []


def test_a_new_dashboard_plans_as_create_with_no_errors():
    class Empty:
        def find_dashboard_by_slug(self, slug):
            return None

    out = json.loads(plan(SPEC, Empty()).to_json())
    assert out["dashboard"] == "create"
    assert out["resolution_errors"] == []
