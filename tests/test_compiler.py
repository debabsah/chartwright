import io
import json
import zipfile
from pathlib import Path

import yaml

from chartwright import ids
from chartwright.compiler import compile_bundle
from chartwright.spec import load_spec
from chartwright.testing import stub_resolution

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = FIXTURES / "sales_overview.golden.zip"


def _spec():
    return load_spec(json.loads((FIXTURES / "sales_overview.json").read_text()))


def _bundle():
    spec = _spec()
    return compile_bundle(spec, stub_resolution(spec))


def test_compile_is_byte_stable():
    assert _bundle() == _bundle()


def test_golden_bytes():
    """The whole guarantee in one assert. Regenerate deliberately with:
    .venv/bin/python -c "from tests.test_compiler import regen; regen()"
    and review the diff."""
    assert GOLDEN.exists(), "golden missing; run regen() and commit the result"
    assert _bundle() == GOLDEN.read_bytes()


def regen():
    GOLDEN.write_bytes(_bundle())
    print(f"regenerated {GOLDEN}")


def test_bundle_structure():
    zf = zipfile.ZipFile(io.BytesIO(_bundle()))
    names = sorted(zf.namelist())
    assert "sdc_bundle/metadata.yaml" in names
    assert "sdc_bundle/dashboards/sdc-sales-overview.yaml" in names
    assert sum(1 for n in names if n.startswith("sdc_bundle/charts/")) == 6
    assert not any("/datasets/" in n or "/databases/" in n for n in names), "rail (a): no dataset embedding"


def test_position_invariants():
    zf = zipfile.ZipFile(io.BytesIO(_bundle()))
    dash = yaml.safe_load(zf.read("sdc_bundle/dashboards/sdc-sales-overview.yaml"))
    pos = dash["position"]
    charts = {k: v for k, v in pos.items() if k.startswith("CHART-")}
    assert len(charts) == 6
    for node in charts.values():
        meta = node["meta"]
        assert isinstance(meta["chartId"], int)          # importer hard-requires this
        assert meta["uuid"]
        assert 1 <= meta["width"] <= 12
        assert meta["height"] > 0
    chart_ids = [c["meta"]["chartId"] for c in charts.values()]
    assert len(set(chart_ids)) == len(chart_ids)
    rows = [k for k in pos if k.startswith("ROW-")]
    assert len(rows) == 3
    assert dash["uuid"] == str(ids.dashboard_uuid("sdc-sales-overview"))


def test_chart_yaml_shape_matches_export_format():
    zf = zipfile.ZipFile(io.BytesIO(_bundle()))
    chart_name = next(n for n in zf.namelist() if "/charts/" in n)
    chart = yaml.safe_load(zf.read(chart_name))
    assert set(chart) == {
        "slice_name", "description", "certified_by", "certification_details",
        "viz_type", "params", "query_context", "cache_timeout", "uuid",
        "version", "dataset_uuid",
    }
    assert chart["params"]["time_range"] == "No filter"   # always explicit, all five types


def test_big_number_fonts_are_pinned():
    """Unset, 6.1 renders the legacy-subheader subtitle at proportion 1 of the
    card (transformProps fallback), so short subtitles blow up and crop."""
    zf = zipfile.ZipFile(io.BytesIO(_bundle()))
    seen = 0
    for n in zf.namelist():
        if "/charts/" in n:
            chart = yaml.safe_load(zf.read(n))
            if chart["viz_type"] in ("big_number_total", "big_number"):
                assert chart["params"]["header_font_size"] == 0.4
                assert chart["params"]["subheader_font_size"] == 0.15
                seen += 1
    assert seen > 0


def test_time_binding_from_main_dttm_col():
    """Charts without their own x_axis bind to the dataset's main temporal
    column, or the dashboard time filter silently skips them (verified live:
    full-month total under a one-week default)."""
    spec = load_spec({
        "spec_version": "1",
        "dashboard": {"title": "T", "slug": "sdc-t"},
        "charts": [
            {"name": "KPI", "type": "big_number_total",
             "dataset": {"database": "examples", "table": "t"}, "metric": "COUNT(*)"},
            {"name": "Trend", "type": "timeseries_line",
             "dataset": {"database": "examples", "table": "t"},
             "metrics": ["COUNT(*)"], "time_column": "ts"},
        ],
        "layout": {"rows": [["KPI", "Trend"]]},
    })
    res = stub_resolution(spec)
    for ds in res.datasets.values():
        ds.main_dttm_col = "ts"
    zf = zipfile.ZipFile(io.BytesIO(compile_bundle(spec, res)))
    by_name = {}
    for n in zf.namelist():
        if "/charts/" in n:
            doc = yaml.safe_load(zf.read(n))
            by_name[doc["slice_name"]] = doc["params"]
    assert by_name["KPI"]["granularity_sqla"] == "ts"
    assert "granularity_sqla" not in by_name["Trend"]   # has x_axis already


def test_uuid_stability_and_ownership():
    u1 = ids.chart_uuid("sdc-sales-overview", "Total Orders")
    u2 = ids.chart_uuid("sdc-sales-overview", "Total Orders")
    assert u1 == u2
    assert ids.chart_uuid("sdc-sales-overview", "Renamed") != u1
