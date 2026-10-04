"""Dashboard settings the spec now owns: colour scheme, description,
certification, draft state, auto-refresh, filter-bar orientation, chart
timestamps (6.1) and tags (6.1). Each compiles into the dashboard export,
decompiles back, and shows up in `plan` as dashboard_settings_changed.
Omitted, every one compiles to exactly what the tool wrote before."""

import io
import json
import zipfile
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

import chartwright.dashdiff as dashdiff
import chartwright.resolver as resolver
from chartwright import ids
from chartwright.compiler import compile_bundle
from chartwright.dashdiff import _normalize, plan
from chartwright.decompile import decompile_bundle
from chartwright.design import advise
from chartwright.design.presets import load_overlay
from chartwright.spec import load_spec
from chartwright.testing import edit_bundle, stub_resolution

DS = {"database": "warehouse", "table": "orders"}


def spec_data(dashboard: dict | None = None, charts: list | None = None, **extra) -> dict:
    """A small valid spec; dashboard keys merge into the dashboard block."""
    charts = charts or [
        {"name": "Revenue", "type": "big_number_total", "metric": "SUM(amount)", "dataset": DS},
        {"name": "Revenue by Region", "type": "bar", "x_column": "region",
         "metrics": ["SUM(amount)"], "dataset": DS},
    ]
    data = {
        "spec_version": "1",
        "dashboard": {"title": "Orders", "slug": "orders", **(dashboard or {})},
        "charts": charts,
        "layout": {"rows": [[c["name"] for c in charts]]},
    }
    data.update(extra)
    return data


def lookup_for(res):
    by_uuid = {ds.uuid: {"database": ds.database_name, "schema": ds.schema, "table": ds.table}
               for ds in res.datasets.values()}
    return lambda u: by_uuid.get(u)


def bundle_docs(spec) -> dict[str, dict]:
    """path -> parsed yaml of the compiled bundle."""
    zf = zipfile.ZipFile(io.BytesIO(compile_bundle(spec, stub_resolution(spec))))
    return {n: yaml.safe_load(zf.read(n)) for n in zf.namelist()}


def dashboard_doc(spec) -> dict:
    return next(d for n, d in bundle_docs(spec).items() if "/dashboards/" in n)


def chart_doc(spec, name: str) -> dict:
    return next(d for n, d in bundle_docs(spec).items()
                if "/charts/" in n and d["slice_name"] == name)


def roundtrip(spec, edit=None):
    """Decompile the spec's own bundle, optionally edited first (live drift)."""
    res = stub_resolution(spec)
    blob = compile_bundle(spec, res)
    if edit is not None:
        blob = edit_bundle(blob, edit)
    return decompile_bundle(blob, lookup_for(res))


def assert_lossless(spec):
    out = roundtrip(spec)
    assert out.losses == []
    assert _normalize(load_spec(out.spec)) == _normalize(spec)
    return out


def plan_against(spec, edit, monkeypatch) -> dict:
    """plan() against a live dashboard that is the spec's own build, edited."""
    live = roundtrip(spec, edit)
    monkeypatch.setattr(resolver, "resolve", lambda s, c, *_: stub_resolution(s))
    monkeypatch.setattr(dashdiff, "decompile_live", lambda slug, c: live)

    class Client:
        def find_dashboard_by_slug(self, slug):
            return {"id": 3, "uuid": str(ids.dashboard_uuid(slug))}

    return json.loads(plan(spec, Client()).to_json())


def edit_dashboard(fn):
    def edit(path, doc):
        if "/dashboards/" in path:
            fn(doc)
    return edit


SETTINGS = {
    "color_scheme": "bnbColors",
    "description": "Orders and revenue, by region.",
    "certified_by": "Finance",
    "certification_details": "Ties to the GL",
    "published": False,
    "refresh_frequency": 300,
    "filter_bar_orientation": "horizontal",
    "show_chart_timestamps": True,
    "tags": ["finance", "weekly"],
}


# -- compile ------------------------------------------------------------------


def test_omitted_settings_compile_to_what_the_tool_always_wrote():
    doc = dashboard_doc(load_spec(spec_data()))
    assert doc["description"] is None and doc["certified_by"] is None
    assert doc["certification_details"] is None and doc["published"] is True
    assert "tags" not in doc
    meta = doc["metadata"]
    assert meta["color_scheme"] == "" and meta["refresh_frequency"] == 0
    assert "filter_bar_orientation" not in meta and "show_chart_timestamps" not in meta


def test_every_setting_reaches_the_dashboard_export():
    doc = dashboard_doc(load_spec(spec_data(SETTINGS)))
    assert doc["description"] == "Orders and revenue, by region."
    assert doc["certified_by"] == "Finance" and doc["certification_details"] == "Ties to the GL"
    assert doc["published"] is False
    assert doc["tags"] == ["finance", "weekly"]
    meta = doc["metadata"]
    assert meta["color_scheme"] == "bnbColors"
    assert meta["refresh_frequency"] == 300
    assert meta["filter_bar_orientation"] == "HORIZONTAL"
    assert meta["show_chart_timestamps"] is True


def test_defaults_written_out_compile_like_omitted():
    explicit = {"published": True, "refresh_frequency": 0, "filter_bar_orientation": "vertical",
                "show_chart_timestamps": False}
    assert dashboard_doc(load_spec(spec_data(explicit))) == dashboard_doc(load_spec(spec_data()))


def test_empty_tags_are_written_so_an_apply_clears_them():
    doc = dashboard_doc(load_spec(spec_data({"tags": []})))
    assert doc["tags"] == []


# -- validation -----------------------------------------------------------------


@pytest.mark.parametrize("bad, needle", [
    ({"certification_details": "x"}, "needs certified_by"),
    ({"refresh_frequency": -5}, "greater than or equal to 0"),
    ({"filter_bar_orientation": "left"}, "vertical"),
    ({"color_scheme": ""}, "at least 1 character"),
    ({"tags": ["finance", "finance"]}, "unique"),
    ({"tags": ["owner:1"]}, "':'"),
    ({"tags": [" "]}, "non-empty"),
    ({"published": "maybe"}, "valid boolean"),
])
def test_bad_settings_are_named(bad, needle):
    with pytest.raises(ValidationError) as e:
        load_spec(spec_data(bad))
    assert needle in str(e.value)


# -- decompile ------------------------------------------------------------------


def test_settings_round_trip_through_decompile():
    out = assert_lossless(load_spec(spec_data(SETTINGS)))
    dash = out.spec["dashboard"]
    for key, value in SETTINGS.items():
        assert dash[key] == value, key


def test_defaults_decompile_as_omitted():
    out = assert_lossless(load_spec(spec_data()))
    assert not set(SETTINGS) & set(out.spec["dashboard"])


def test_ui_saved_defaults_decompile_as_omitted():
    """Superset's properties modal writes its defaults out; they read as omitted."""
    def fn(doc):
        doc["metadata"].update({"filter_bar_orientation": "VERTICAL",
                                "show_chart_timestamps": False, "refresh_frequency": 0})
        doc["description"] = ""
        doc["tags"] = []
    out = roundtrip(load_spec(spec_data()), edit_dashboard(fn))
    assert not set(SETTINGS) & set(out.spec["dashboard"])


# -- plan -----------------------------------------------------------------------


def test_plan_is_clean_when_settings_match(monkeypatch):
    out = plan_against(load_spec(spec_data(SETTINGS)), None, monkeypatch)
    assert out["clean"] is True and out["dashboard_settings_changed"] == []


@pytest.mark.parametrize("key, live", [
    ("color_scheme", lambda d: d["metadata"].update(color_scheme="d3Category10")),
    ("description", lambda d: d.update(description="edited in the UI")),
    ("certified_by", lambda d: d.update(certified_by="Someone else")),
    ("certification_details", lambda d: d.update(certification_details="Other details")),
    ("published", lambda d: d.update(published=True)),
    ("refresh_frequency", lambda d: d["metadata"].update(refresh_frequency=60)),
    ("filter_bar_orientation", lambda d: d["metadata"].update(filter_bar_orientation="VERTICAL")),
    ("show_chart_timestamps", lambda d: d["metadata"].update(show_chart_timestamps=False)),
    ("tags", lambda d: d.update(tags=["finance"])),
])
def test_plan_names_each_changed_setting(key, live, monkeypatch):
    out = plan_against(load_spec(spec_data(SETTINGS)), edit_dashboard(live), monkeypatch)
    assert out["clean"] is False and out["dashboard"] == "update"
    assert out["dashboard_settings_changed"] == [key]
    assert out["charts_changed"] == [] and out["layout_changed"] is False


def test_plan_treats_empty_tags_as_none(monkeypatch):
    out = plan_against(load_spec(spec_data({"tags": []})), None, monkeypatch)
    assert out["clean"] is True


def test_plan_leaves_tags_alone_when_the_spec_omits_them(monkeypatch):
    """Omitted tags compile to no `tags` key, so the import leaves live tags as
    they are; reporting them would be drift no apply can repair. [] manages
    them (the import then clears them), so live tags are a change."""
    def tag(doc):
        doc["tags"] = ["added-in-ui"]
    edit = edit_dashboard(tag)
    assert plan_against(load_spec(spec_data()), edit, monkeypatch)["clean"] is True
    out = plan_against(load_spec(spec_data({"tags": []})), edit, monkeypatch)
    assert out["dashboard_settings_changed"] == ["tags"]


# -- design brain: colour scheme names ------------------------------------------


def _scheme_findings(data):
    rep = advise(load_spec(data), overlay=load_overlay(None))
    return [f for f in rep.findings if f.rule == "narrative.color-scheme"]


def test_a_shipped_scheme_name_is_silent():
    data = spec_data({"color_scheme": "supersetColors"})
    data["charts"][1]["color_scheme"] = "echarts5Colors"
    assert _scheme_findings(data) == []


def test_an_unknown_scheme_name_warns_with_a_hint():
    data = spec_data({"color_scheme": "acmeBrand"})
    data["charts"][1]["color_scheme"] = "bnbcolors"
    found = _scheme_findings(data)
    assert {f.severity for f in found} == {"warn"}
    by_chart = {f.chart: f.detail for f in found}
    assert "EXTRA_CATEGORICAL_COLOR_SCHEMES" in by_chart[None]
    assert "did you mean 'bnbColors'" in by_chart["Revenue by Region"]



def test_the_controls_fixture_round_trips_losslessly():
    """tests/fixtures/dashboard_controls.json sets every control on every chart
    type that takes it (the params drift check compiles it too)."""
    path = Path(__file__).parent / "fixtures" / "dashboard_controls.json"
    assert_lossless(load_spec(json.loads(path.read_text(encoding="utf-8"))))
