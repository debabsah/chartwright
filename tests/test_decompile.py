"""Round-trip and lossy-decompile tests (offline)."""

import json

import pytest
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
    """The full surface (15 chart types, chart filters, native filters,
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


# -- triage I: settings decompile used to drop without a note ------------------------------


def _ui_bundle(edit) -> tuple[bytes, object]:
    """The sales overview fixture, compiled, with chart params edited as the UI would
    store them."""
    from chartwright.testing import edit_bundle

    spec = load_spec(json.loads((FIXTURES / "sales_overview.json").read_text()))
    return edit_bundle(compile_bundle(spec, stub_resolution(spec)), edit), spec


def _settings_losses(blob, spec) -> dict[str, str]:
    result = decompile_bundle(blob, _stub_lookup_for(spec))
    return {l.where: l.what for l in result.losses if "settings not preserved" in l.what}


def test_a_changed_setting_the_spec_cannot_hold_is_named():
    """A rolling sum or a forecast changes the numbers a chart shows; decompile dropped
    both silently, so a copy built from the spec drew different numbers unannounced."""
    def analytics(path, doc):
        if "/charts/" in path and doc["slice_name"] == "Sales Over Time":
            doc["params"].update(rolling_type="cumsum", forecastEnabled=True, legendMargin=50)
    blob, spec = _ui_bundle(analytics)
    what = _settings_losses(blob, spec)["Sales Over Time"]
    assert "rolling_type='cumsum'" in what and "forecastEnabled=True" in what
    assert "legendMargin=50" in what


def test_superset_defaults_are_no_loss():
    """An untouched chart stores these defaults; dropping them changes nothing drawn."""
    def defaults(path, doc):
        if "/charts/" in path:
            doc["params"].update(rolling_type="None", forecastEnabled=False, forecastPeriods=10,
                                 forecastInterval=0.8, rich_tooltip=True, truncateXAxis=True,
                                 tooltipTimeFormat="smart_date", sort_series_type="sum",
                                 y_axis_bounds=[None, None], comparison_type="values",
                                 legendMargin=None)
    blob, spec = _ui_bundle(defaults)
    assert _settings_losses(blob, spec) == {}


def test_a_setting_that_only_matters_with_another_is_named_only_then():
    def comparison(compare):
        def edit(path, doc):
            if "/charts/" in path and doc["slice_name"] == "Sales Over Time":
                doc["params"].update(comparison_type="difference", time_compare=compare)
        return edit
    blob, spec = _ui_bundle(comparison([]))
    assert "Sales Over Time" not in _settings_losses(blob, spec)
    blob, spec = _ui_bundle(comparison(["1 year ago"]))
    assert "comparison_type='difference'" in _settings_losses(blob, spec).get("Sales Over Time", "")


def test_a_value_the_spec_writes_back_is_no_loss():
    """Decompile carries some of these keys under spec fields; the check compiles the
    spec it read, so a key apply writes back with the same value is never reported."""
    blob, spec = _ui_bundle(lambda path, doc: None)
    assert _settings_losses(blob, spec) == {}


def test_a_tab_scoped_filter_is_named():
    data = json.loads((FIXTURES / "sales_overview.json").read_text())
    data["filters"] = [{"type": "select", "name": "Deal", "column": "deal_size",
                        "dataset": data["charts"][0]["dataset"]}]
    spec = load_spec(data)
    from chartwright.testing import edit_bundle

    def tab_scope(path, doc):
        if "/dashboards/" in path:
            doc["metadata"]["native_filter_configuration"][0]["scope"]["rootPath"] = ["TAB-1"]
    blob = edit_bundle(compile_bundle(spec, stub_resolution(spec)), tab_scope)
    losses = [l for l in decompile_bundle(blob, _stub_lookup_for(spec)).losses
              if l.where == "filter:Deal"]
    assert any("scoped to tabs" in l.what for l in losses)


def test_a_6_1_big_number_subtitle_is_read():
    """6.0+ stores a big-number total's subtitle as `subtitle`, not `subheader` (K)."""
    def subtitle(path, doc):
        if "/charts/" in path and doc.get("viz_type") == "big_number_total":
            doc["params"].pop("subheader", None)
            doc["params"]["subtitle"] = "all regions"
    blob, spec = _ui_bundle(subtitle)
    result = decompile_bundle(blob, _stub_lookup_for(spec))
    totals = [c for c in result.spec["charts"] if c["type"] == "big_number_total"]
    assert totals and all(c.get("subtitle") == "all regions" for c in totals)
    assert not any("subtitle" in l.what for l in result.losses)


# -- triage J: charts stacked in a COLUMN beside a taller one ----------------------------------


def _position_of(bundle: bytes) -> dict:
    import io
    import zipfile

    import yaml

    zf = zipfile.ZipFile(io.BytesIO(bundle))
    dash = next(yaml.safe_load(zf.read(n)) for n in zf.namelist() if "/dashboards/" in n)
    return dash["position"]


def _stacked_spec(tabs: bool = False) -> dict:
    data = json.loads((FIXTURES / "sales_overview.json").read_text())
    names = [c["name"] for c in data["charts"]]
    a, b, c, d = names[:4]
    sketch = {"sketch": ["AAAABBBBCCCC", "AAAABBBBDDDD"],
              "legend": {"A": a, "B": b, "C": c, "D": d}, "line": 3}
    rest = [[n] for n in names[4:]]
    if tabs:
        data["layout"] = {"tabs": [{"title": "Stacked", **sketch},
                                   {"title": "Rest", "rows": rest or [[a]]}]}
        if not rest:
            data["layout"]["tabs"] = [{"title": "Stacked", **sketch}]
    else:
        data["layout"] = sketch
        data["charts"] = data["charts"][:4]
    return data


@pytest.mark.parametrize("tabs", [False, True])
def test_a_column_beside_a_tall_chart_round_trips_as_a_sketch(tabs):
    """Decompile flattened a COLUMN (two charts stacked beside a taller one) into one
    row and reported a loss; a copy then drew them side by side. A sketch holds it, and
    the decompiled spec compiles to the same layout."""
    data = _stacked_spec(tabs)
    spec = load_spec(data)
    bundle = compile_bundle(spec, stub_resolution(spec))
    result = decompile_bundle(bundle, _stub_lookup_for(spec))
    assert not [l for l in result.losses if "COLUMN" in l.what], result.losses
    layout = result.spec["layout"]
    section = layout["tabs"][0] if tabs else layout
    assert "sketch" in section and "rows" not in section
    again = load_spec(result.spec)
    assert _position_of(compile_bundle(again, stub_resolution(again))) == _position_of(bundle)
    # plan compares normalized layouts: the spec and its live state read the same.
    assert _normalize(spec)["layout"] == _normalize(again)["layout"]


def test_a_column_beside_markdown_reads_back_as_a_sketch_with_the_block():
    """A sketch holds markdown and headers too: a text row and a header in a section
    with a COLUMN read back as legend blocks, where they used to flatten the COLUMN."""
    data = _stacked_spec()
    spec = load_spec(data)
    from chartwright.testing import edit_bundle

    def text_row(path, doc):
        if "/dashboards/" in path:
            pos = doc["position"]
            pos["MARKDOWN-x"] = {"type": "MARKDOWN", "id": "MARKDOWN-x", "children": [],
                                 "meta": {"code": "Notes", "width": 12, "height": 8}}
            pos["ROW-x"] = {"type": "ROW", "id": "ROW-x", "children": ["MARKDOWN-x"],
                            "meta": {"background": "BACKGROUND_TRANSPARENT"}}
            pos["HEADER-x"] = {"type": "HEADER", "id": "HEADER-x", "children": [],
                               "meta": {"text": "Detail", "headerSize": "MEDIUM_HEADER",
                                        "background": "BACKGROUND_TRANSPARENT"}}
            pos["GRID_ID"]["children"] += ["HEADER-x", "ROW-x"]
    blob = edit_bundle(compile_bundle(spec, stub_resolution(spec)), text_row)
    result = decompile_bundle(blob, _stub_lookup_for(spec))
    assert result.losses == [], result.losses_json()
    layout = result.spec["layout"]
    assert "sketch" in layout and "rows" not in layout
    blocks = [v for v in layout["legend"].values() if isinstance(v, dict)]
    assert blocks == [{"header": "Detail"}, {"markdown": "Notes", "height": 1.6}]
    assert layout["sketch"][-2:] == [layout["sketch"][-2][0] * 12, layout["sketch"][-1][0] * 12]
    again = load_spec(result.spec)
    # The same tree, node for node (the edited nodes' ids were not the compiler's own).
    assert _tree(_position_of(compile_bundle(again, stub_resolution(again)))) == \
        _tree(_position_of(blob))


def _tree(pos: dict, node_id: str = "GRID_ID"):
    """A layout as nested (type, meta, children), ids aside."""
    node = pos[node_id]
    meta = {k: v for k, v in (node.get("meta") or {}).items() if k != "chartId"}
    return (node["type"], meta, [_tree(pos, c) for c in node.get("children", [])])


def test_a_column_beside_a_divider_stays_rows_and_says_so():
    """A sketch holds no divider: with one in the section, the COLUMN is flattened as
    before and named."""
    data = _stacked_spec()
    spec = load_spec(data)
    from chartwright.testing import edit_bundle

    def divider(path, doc):
        if "/dashboards/" in path:
            pos = doc["position"]
            pos["DIVIDER-x"] = {"type": "DIVIDER", "id": "DIVIDER-x", "children": [], "meta": {}}
            pos["GRID_ID"]["children"].append("DIVIDER-x")
    blob = edit_bundle(compile_bundle(spec, stub_resolution(spec)), divider)
    result = decompile_bundle(blob, _stub_lookup_for(spec))
    assert "rows" in result.spec["layout"]
    assert any("COLUMN" in l.what for l in result.losses)


@pytest.mark.parametrize("viz, key, value", [
    ("pie", "outerRadius", 60), ("pie", "show_labels", False),
    ("funnel", "tooltip_label_type", 2), ("funnel", "show_labels", False),
    ("treemap_v2", "show_labels", False),
    ("heatmap_v2", "legend_type", "piecewise"), ("heatmap_v2", "xscale_interval", 2),
    ("histogram_v2", "normalize", True), ("big_number", "show_trend_line", False),
    ("echarts_timeseries_line", "seriesType", "smooth"),
    ("echarts_timeseries_line", "currency_format", {"symbol": "USD", "symbolPosition": "prefix"}),
])
def test_per_chart_settings_changed_in_the_ui_are_named(viz, key, value):
    """The fact-check of the 0.5.0 docs found these dropped with no loss; each is now
    checked against the value an untouched chart of that type stores."""
    from chartwright.testing import edit_bundle

    spec = load_spec(json.loads((FIXTURES / "kitchen_sink.json").read_text()))
    bundle = compile_bundle(spec, stub_resolution(spec))

    def change(path, doc):
        if "/charts/" in path and doc.get("viz_type") == viz:
            doc["params"][key] = value
    losses = decompile_bundle(edit_bundle(bundle, change), _stub_lookup_for(spec)).losses
    assert any(key in l.what and "settings not preserved" in l.what for l in losses), key


def test_an_empty_currency_format_is_no_loss():
    from chartwright.testing import edit_bundle

    spec = load_spec(json.loads((FIXTURES / "kitchen_sink.json").read_text()))

    def empty(path, doc):
        if "/charts/" in path:
            doc["params"]["currency_format"] = {"symbol": None, "symbolPosition": None}
    blob = edit_bundle(compile_bundle(spec, stub_resolution(spec)), empty)
    assert not [l for l in decompile_bundle(blob, _stub_lookup_for(spec)).losses
                if "currency_format" in l.what]
