"""layout.footer: rows below the rows / tabs / sketch, outside any tab (offline)."""

import io
import json
import zipfile
from pathlib import Path

import pytest
import yaml

from chartwright.compiler import _position, compile_bundle
from chartwright.dashdiff import _normalize
from chartwright.decompile import decompile_bundle
from chartwright.spec import load_spec
from chartwright.testing import stub_resolution

from test_decompile import _stub_lookup_for

FIXTURES = Path(__file__).parent / "fixtures"
FOOTER = [[{"markdown": "Built by the analytics team · questions: #data-help", "width": 12, "height": 2}]]


def _with_footer(fixture: str, footer=FOOTER) -> dict:
    data = json.loads((FIXTURES / fixture).read_text())
    data["layout"]["footer"] = footer
    return data


def _round_trip(data: dict):
    spec = load_spec(data)
    return spec, decompile_bundle(compile_bundle(spec, stub_resolution(spec)), _stub_lookup_for(spec))


def _renamed(bundle: bytes, old: str, new: str) -> bytes:
    """The same bundle with `old` replaced by `new` in the dashboard file: a
    dashboard whose footer rows were NOT built by chartwright."""
    src, out = zipfile.ZipFile(io.BytesIO(bundle)), io.BytesIO()
    with zipfile.ZipFile(out, "w") as dst:
        for name in src.namelist():
            blob = src.read(name)
            dst.writestr(name, blob.decode().replace(old, new).encode() if "/dashboards/" in name else blob)
    return out.getvalue()


def test_footer_sits_below_the_tabs_at_grid_level():
    pos = _position(load_spec(_with_footer("kitchen_sink.json")))
    assert pos["GRID_ID"]["children"] == ["TABS-sdc-1", "ROW-sdc-footer-1"]
    assert pos["ROW-sdc-footer-1"]["parents"] == ["ROOT_ID", "GRID_ID"]
    md = pos["MARKDOWN-sdc-footer-1-1"]
    assert md["parents"] == ["ROOT_ID", "GRID_ID", "ROW-sdc-footer-1"]
    assert md["meta"]["code"] == FOOTER[0][0]["markdown"]


def test_footer_follows_flat_rows():
    spec = load_spec(_with_footer("sales_overview.json"))
    kids = _position(spec)["GRID_ID"]["children"]
    assert kids[-1] == "ROW-sdc-footer-1"
    assert kids[:-1] == [f"ROW-sdc-{i + 1}" for i in range(len(spec.layout.rows))]


@pytest.mark.parametrize("fixture", ["kitchen_sink.json", "sales_overview.json"])
def test_footer_round_trips_losslessly(fixture):
    spec, result = _round_trip(_with_footer(fixture))
    assert result.losses == [], [loss.as_dict() for loss in result.losses]
    assert result.spec["layout"]["footer"] == FOOTER
    assert _normalize(load_spec(result.spec)) == _normalize(spec)


def test_footer_recompiles_to_the_same_bundle():
    spec, result = _round_trip(_with_footer("kitchen_sink.json"))
    again = load_spec(result.spec)
    assert compile_bundle(again, stub_resolution(again)) == compile_bundle(spec, stub_resolution(spec))


def test_any_rows_after_the_tabs_read_as_the_footer():
    """A dashboard assembled in Superset's UI (no chartwright ids): rows dragged
    below the tabs are a footer too."""
    spec = load_spec(_with_footer("kitchen_sink.json"))
    bundle = _renamed(compile_bundle(spec, stub_resolution(spec)), "sdc-footer-", "ui-built-")
    result = decompile_bundle(bundle, _stub_lookup_for(spec))
    assert result.spec["layout"]["footer"] == FOOTER


def test_untabbed_rows_without_the_marker_stay_in_the_body():
    """Without tabs there is no visual boundary: only chartwright's marked rows
    are a footer; anything else stays a body row (nothing is lost)."""
    spec = load_spec(_with_footer("sales_overview.json"))
    bundle = _renamed(compile_bundle(spec, stub_resolution(spec)), "sdc-footer-", "ui-built-")
    layout = decompile_bundle(bundle, _stub_lookup_for(spec)).spec["layout"]
    assert "footer" not in layout
    assert layout["rows"][-1] == FOOTER[0]


def test_footer_under_a_sketch_layout_round_trips():
    data = json.loads((FIXTURES / "sales_overview.json").read_text())
    names = [c["name"] for c in data["charts"]]
    data["layout"] = {"sketch": ["AAAABBBBCCCC", "DDDDEEEEFFFF"], "legend": dict(zip("ABCDEF", names)), "footer": FOOTER}
    spec, result = _round_trip(data)
    assert result.spec["layout"]["footer"] == FOOTER
    assert _normalize(load_spec(result.spec)) == _normalize(spec)


def test_a_footer_chart_counts_as_placed():
    data = json.loads((FIXTURES / "sales_overview.json").read_text())
    last = data["layout"]["rows"].pop()
    data["layout"]["footer"] = [last]
    spec = load_spec(data)
    assert _position(spec)["GRID_ID"]["children"][-1] == "ROW-sdc-footer-1"


def test_a_chart_cannot_sit_in_the_body_and_the_footer():
    data = json.loads((FIXTURES / "sales_overview.json").read_text())
    data["layout"]["footer"] = [[data["layout"]["rows"][0][0]]]
    with pytest.raises(ValueError, match="more than once"):
        load_spec(data)


def test_footer_rows_obey_the_grid_width():
    wide = [[{"markdown": "a", "width": 8}, {"markdown": "b", "width": 8}]]
    with pytest.raises(ValueError, match="widths sum to 16"):
        load_spec(_with_footer("sales_overview.json", wide))


def test_a_footer_alone_is_not_a_layout():
    data = json.loads((FIXTURES / "sales_overview.json").read_text())
    data["layout"] = {"footer": FOOTER}
    with pytest.raises(ValueError, match="exactly one of rows / tabs / sketch"):
        load_spec(data)


def test_the_bundle_yaml_keeps_the_footer_markdown_verbatim():
    """HTML in a footer block (tables, images, entities) reaches Superset unchanged."""
    html = '<table width="100%"><tr><td><img src="/static/logo.svg" height="28" alt=""></td><td>A&nbsp;B</td></tr></table>'
    spec = load_spec(_with_footer("kitchen_sink.json", [[{"markdown": html, "width": 12, "height": 8}]]))
    zf = zipfile.ZipFile(io.BytesIO(compile_bundle(spec, stub_resolution(spec))))
    dash = yaml.safe_load(zf.read(next(n for n in zf.namelist() if "/dashboards/" in n)))
    assert dash["position"]["MARKDOWN-sdc-footer-1-1"]["meta"]["code"] == html
