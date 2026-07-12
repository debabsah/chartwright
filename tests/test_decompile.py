"""Round-trip and lossy-decompile tests (offline)."""

import json
from pathlib import Path

from chartwright.compiler import compile_bundle
from chartwright.dashdiff import _normalize
from chartwright.decompile import decompile_bundle
from chartwright.spec import load_spec
from chartwright.testing import stub_resolution

FIXTURES = Path(__file__).parent / "fixtures"


def _stub_lookup_for(spec):
    res = stub_resolution(spec)
    by_uuid = {}
    for chart in spec.charts:
        ds = res.for_chart(chart.dataset)
        by_uuid[ds.uuid] = {
            "database": chart.dataset.database,
            "schema": chart.dataset.schema_,
            "table": chart.dataset.table,
        }
    return lambda u: by_uuid.get(u)


def test_round_trip_tool_born_bundle():
    """compile(spec) then decompile must reproduce the spec (canonical form),
    with zero losses. This is the Terraform-future invariant."""
    original = load_spec(json.loads((FIXTURES / "sales_overview.json").read_text()))
    bundle = compile_bundle(original, stub_resolution(original))
    result = decompile_bundle(bundle, _stub_lookup_for(original))
    assert result.losses == [], [loss.as_dict() for loss in result.losses]
    recovered = load_spec(result.spec)
    assert _normalize(recovered) == _normalize(original)


def test_round_trip_kitchen_sink():
    """The full surface (14 chart types, chart filters, native filters,
    markdown, tabs) must survive compile->decompile losslessly."""
    original = load_spec(json.loads((FIXTURES / "kitchen_sink.json").read_text()))
    bundle = compile_bundle(original, stub_resolution(original))
    result = decompile_bundle(bundle, _stub_lookup_for(original))
    assert result.losses == [], [loss.as_dict() for loss in result.losses]
    recovered = load_spec(result.spec)
    assert _normalize(recovered) == _normalize(original)


def test_round_trip_is_stable_twice():
    original = load_spec(json.loads((FIXTURES / "sales_overview.json").read_text()))
    bundle = compile_bundle(original, stub_resolution(original))
    once = decompile_bundle(bundle, _stub_lookup_for(original))
    r1 = load_spec(once.spec)
    bundle2 = compile_bundle(r1, stub_resolution(r1))
    assert bundle == bundle2


def test_lossy_decompile_of_real_export():
    """The Featured Charts example dashboard (25 exotic chart types) must
    decompile without crashing, keep the representable charts, and NAME every
    loss instead of silently dropping."""
    blob = (FIXTURES / "featured_charts_export.zip").read_bytes()
    result = decompile_bundle(blob, lambda u: {"database": "examples", "schema": None, "table": "t"})
    assert result.losses, "a 25-chart exotic dashboard cannot decompile lossless"
    from chartwright.spec import CHART_TYPES

    kept_types = {c["type"] for c in result.spec["charts"]}
    assert kept_types <= set(CHART_TYPES)
    # the expanded surface must now recover the mainstream charts incl. pivot/heatmap
    assert {"pivot_table", "heatmap", "histogram", "funnel", "timeseries_line", "pie", "table"} <= kept_types
    skipped = [loss for loss in result.losses if "outside spec surface" in loss.what]
    assert len(skipped) >= 5  # deck.gl/graph/sankey/gauge exotica stay out, NAMED
    # the spec that comes out must be schema-valid
    spec = load_spec(result.spec)
    assert spec.dashboard.slug


def test_labeled_star_metric_round_trips():
    """COUNT(*) AS Trips lost its label on decompile (SQL-expression branch
    ignored hasCustomLabel; found by the NYC demo's plan run, 2026-07-12)."""
    spec = load_spec({
        "spec_version": "1",
        "dashboard": {"title": "T", "slug": "sdc-t"},
        "charts": [{
            "name": "KPI", "type": "big_number_total",
            "dataset": {"database": "examples", "table": "t"},
            "metric": "COUNT(*) AS Trips",
        }],
        "layout": {"rows": [["KPI"]]},
    })
    bundle = compile_bundle(spec, stub_resolution(spec))
    result = decompile_bundle(bundle, _stub_lookup_for(spec))
    assert result.losses == []
    assert result.spec["charts"][0]["metric"] == "COUNT(*) AS Trips"
