"""One-shot redesign core: fixes applied, ownership decides the slug, losses
pass through, redesigned specs always re-validate."""

from chartwright.design.presets import Overlay
from chartwright.design.redesign import redesign_spec
from chartwright.spec import load_spec

DS = {"database": "db", "table": "orders"}
EMPTY = Overlay()


def decompiled():
    """What a legacy UI dashboard typically decompiles to: integer heights
    (decompile rounds), cramped geometry, no design block."""
    return {
        "spec_version": "1",
        "dashboard": {"title": "Ops Board", "slug": "ops-board"},
        "charts": [
            {"type": "timeseries_line", "name": "Trips", "dataset": DS,
             "metrics": ["COUNT(*)"], "time_column": "ts", "width": 8, "height": 3},
            {"type": "pie", "name": "By Region", "dataset": DS, "metric": "COUNT(*)",
             "groupby": "region", "row_limit": 6, "width": 4, "height": 3},
        ],
        "layout": {"rows": [["Trips", "By Region"]]},
        "filters": [{"type": "time_range", "name": "Window"}],
    }


def test_owned_redesigns_in_place_with_fixes():
    new, payload = redesign_spec(decompiled(), losses=[], owned=True, overlay=EMPTY)
    assert payload["stage"] == "redesign" and payload["ok"]
    assert payload["slug"] == "ops-board" and not payload["slug_changed"]
    assert payload["advice"]["fixed"]  # cramped heights got repaired
    heights = {c["name"]: c["height"] for c in new["charts"]}
    assert heights["Trips"] >= 6 and heights["By Region"] >= 8
    load_spec(new)  # redesigned specs always re-validate


def test_foreign_dashboard_lands_side_by_side():
    src = decompiled()
    new, payload = redesign_spec(src, losses=[{"chart": "X", "loss": "legacy viz"}],
                                 owned=False, overlay=EMPTY)
    assert payload["slug_changed"] and payload["slug"] == "ops-board-redesign"
    assert new["dashboard"]["title"] == "Ops Board (redesigned)"
    assert src["dashboard"]["slug"] == "ops-board"  # input never mutated
    assert payload["losses"] == [{"chart": "X", "loss": "legacy viz"}]
    assert "SIDE BY SIDE" in payload["next"]
    load_spec(new)


def test_structural_findings_survive_and_are_counted():
    data = decompiled()
    # bury the summary: a KPI below the detail row is a structural finding,
    # not an autofix
    data["charts"].append({"type": "big_number_total", "name": "Total",
                           "dataset": DS, "metric": "COUNT(*)", "number_format": ",.0f"})
    data["layout"]["rows"].append(["Total"])
    _, payload = redesign_spec(data, losses=[], owned=True, overlay=EMPTY)
    rules = {f["rule"] for f in payload["advice"]["findings"]}
    assert "layout.kpi-first" in rules
    assert "structural finding(s) remain" in payload["next"]


def test_audience_flows_through():
    _, payload = redesign_spec(decompiled(), losses=[], owned=True,
                               audience="executive", overlay=EMPTY)
    assert payload["advice"]["audience"] == "executive"
