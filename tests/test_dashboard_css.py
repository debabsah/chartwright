"""dashboard.css: Superset's "Edit CSS", spec-owned. It reaches the dashboard
export's `css` field, decompiles back (blank reads as omitted), and `plan`
reports a difference as a dashboard-level change, like a title change."""

import io
import json
import zipfile

import yaml

import chartwright.dashdiff as dashdiff
import chartwright.resolver as resolver
from chartwright import ids
from chartwright.compiler import compile_bundle
from chartwright.dashdiff import _normalize, plan
from chartwright.decompile import decompile_bundle
from chartwright.spec import load_spec
from chartwright.testing import edit_bundle, stub_resolution

CSS = ".dashboard-markdown h1 { color: #1A1A1A; }\n.header-title { font-weight: 600; }"


def _data(css=None):
    data = {
        "spec_version": "1",
        "dashboard": {"title": "Orders", "slug": "orders"},
        "charts": [{"name": "Revenue by Region", "type": "bar", "x_column": "region",
                    "metrics": ["SUM(amount)"],
                    "dataset": {"database": "warehouse", "table": "orders"}}],
        "layout": {"rows": [["Revenue by Region"]]},
    }
    if css is not None:
        data["dashboard"]["css"] = css
    return data


def _dashboard_yaml(bundle: bytes) -> dict:
    zf = zipfile.ZipFile(io.BytesIO(bundle))
    name = next(n for n in zf.namelist() if "/dashboards/" in n)
    return yaml.safe_load(zf.read(name))


def _lookup(res):
    by_uuid = {
        ds.uuid: {"database": ds.database_name, "schema": ds.schema, "table": ds.table}
        for ds in res.datasets.values()
    }
    return lambda u: by_uuid.get(u)


def _bundle(spec):
    return compile_bundle(spec, stub_resolution(spec))


def test_omitted_css_writes_none():
    spec = load_spec(_data())
    assert spec.dashboard.css is None
    assert _dashboard_yaml(_bundle(spec))["css"] == ""


def test_css_reaches_the_dashboard_export():
    spec = load_spec(_data(CSS))
    assert _dashboard_yaml(_bundle(spec))["css"] == CSS


def test_blank_css_is_omitted():
    for blank in ("", "   ", "\n\t"):
        spec = load_spec(_data(blank))
        assert spec.dashboard.css is None
        assert _dashboard_yaml(_bundle(spec))["css"] == ""


def test_round_trips_through_decompile():
    for css in (None, CSS):
        spec = load_spec(_data(css))
        res = stub_resolution(spec)
        out = decompile_bundle(compile_bundle(spec, res), _lookup(res))
        assert out.losses == []
        assert out.spec["dashboard"].get("css") == css
        assert _normalize(load_spec(out.spec)) == _normalize(spec)


def test_live_blank_or_null_css_decompiles_as_omitted():
    spec = load_spec(_data())
    res = stub_resolution(spec)
    for live in (None, "", "  \n "):
        def edit(path, doc, live=live):
            if "/dashboards/" in path:
                doc["css"] = live
        out = decompile_bundle(edit_bundle(compile_bundle(spec, res), edit), _lookup(res))
        assert "css" not in out.spec["dashboard"]


def _plan(spec, live_css, monkeypatch):
    """plan() against a live dashboard that is the spec's own build with its
    CSS set to `live_css`, as if edited through Superset's "Edit CSS"."""
    res = stub_resolution(spec)

    def edit(path, doc):
        if "/dashboards/" in path:
            doc["css"] = live_css

    live = decompile_bundle(edit_bundle(compile_bundle(spec, res), edit), _lookup(res))
    monkeypatch.setattr(resolver, "resolve", lambda s, c: stub_resolution(s))
    monkeypatch.setattr(dashdiff, "decompile_live", lambda slug, c: live)

    class Client:
        def find_dashboard_by_slug(self, slug):
            return {"id": 3, "uuid": str(ids.dashboard_uuid(slug))}

    return json.loads(plan(spec, Client()).to_json())


def test_plan_is_clean_when_css_matches(monkeypatch):
    out = _plan(load_spec(_data(CSS)), CSS, monkeypatch)
    assert out["clean"] is True and out["css_changed"] is False
    out = _plan(load_spec(_data()), "", monkeypatch)
    assert out["clean"] is True and out["css_changed"] is False


def test_plan_reports_a_css_change_at_dashboard_level(monkeypatch):
    for spec_css, live_css in ((CSS, ""), (None, CSS), (CSS, CSS + "\n.extra { margin: 0; }")):
        out = _plan(load_spec(_data(spec_css)), live_css, monkeypatch)
        assert out["dashboard"] == "update" and out["clean"] is False
        assert out["css_changed"] is True
        assert out["title_changed"] is False and out["layout_changed"] is False
        assert out["charts_changed"] == []
