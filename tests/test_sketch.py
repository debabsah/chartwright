"""ASCII layout sketches: parsing, geometry, guillotine decomposition into
Superset's ROW/COLUMN tree, and compile integration."""

import io
import json
import zipfile

import pytest
import yaml

from chartwright.compiler import compile_bundle
from chartwright.sketch import SketchChart, SketchColumn, SketchError, parse_sketch
from chartwright.spec import load_spec
from chartwright.testing import stub_resolution

DS = {"database": "examples", "table": "t"}


def test_simple_row_widths_and_spaces_cosmetic():
    rows = parse_sketch(["CCCC LLLL TTTT"], {"C": "cards", "L": "lines", "T": "totals"}, 2)
    assert [c.width for c in rows[0].children] == [4, 4, 4]
    same = parse_sketch(["CCCCLLLLTTTT"], {"C": "cards", "L": "lines", "T": "totals"}, 2)
    assert [c.width for c in same[0].children] == [4, 4, 4]
    assert [c.name for c in rows[0].children] == ["cards", "lines", "totals"]


def test_proportions_normalize_to_twelfths():
    rows = parse_sketch(["AAAAAAAABBBB"], {"A": "a", "B": "b"}, 2)
    assert [c.width for c in rows[0].children] == [8, 4]
    rows = parse_sketch(["AAB"], {"A": "a", "B": "b"}, 2)
    assert [c.width for c in rows[0].children] == [8, 4]


def test_repeated_lines_add_height():
    rows = parse_sketch(["AA", "AA", "BB"], {"A": "a", "B": "b"}, 3)
    assert len(rows) == 2
    assert rows[0].children[0].height == 6   # 2 lines x 3 units
    assert rows[1].children[0].height == 3


def test_column_nesting():
    rows = parse_sketch(["AAAA BB", "AAAA CC"], {"A": "a", "B": "b", "C": "c"}, 2)
    (row,) = rows
    a, col = row.children
    assert isinstance(a, SketchChart) and a.name == "a" and a.height == 4
    assert isinstance(col, SketchColumn)
    assert [(c.name, c.height) for c in col.children] == [("b", 2), ("c", 2)]
    assert a.width + col.width == 12


def test_errors_are_named():
    with pytest.raises(SketchError, match="cells, line 1 has"):
        parse_sketch(["AAA", "AAAA"], {"A": "a"}, 2)
    with pytest.raises(SketchError, match="solid rectangle"):
        parse_sketch(["AAB", "ABB"], {"A": "a", "B": "b"}, 2)
    with pytest.raises(SketchError, match="not in legend"):
        parse_sketch(["AX"], {"A": "a"}, 2)
    with pytest.raises(SketchError, match="never drawn"):
        parse_sketch(["AA"], {"A": "a", "Z": "z"}, 2)
    with pytest.raises(SketchError, match="nest a row inside a column"):
        # D sits under B AND C's columns -> would need a row inside a column
        parse_sketch(["ABBCC", "ADDDD"], {"A": "a", "B": "b", "C": "c", "D": "d"}, 2)
    with pytest.raises(SketchError, match="too many charts side by side"):
        parse_sketch(["ABCDEFGHIJKLM"], {ch: ch for ch in "ABCDEFGHIJKLM"}, 2)


def _sketch_spec():
    return load_spec({
        "spec_version": "1",
        "dashboard": {"title": "T", "slug": "sdc-t"},
        "charts": [
            {"name": "a", "type": "big_number_total", "dataset": DS, "metric": "COUNT(*)"},
            {"name": "b", "type": "big_number_total", "dataset": DS, "metric": "COUNT(*)"},
            {"name": "c", "type": "big_number_total", "dataset": DS, "metric": "COUNT(*)",
             "height": 9},
        ],
        "layout": {"tabs": [{
            "title": "Main",
            "sketch": ["AAAA BB", "AAAA CC"],
            "legend": {"A": "a", "B": "b", "C": "c"},
            "line": 2,
        }]},
    })


def test_compile_emits_column_tree():
    spec = _sketch_spec()
    zf = zipfile.ZipFile(io.BytesIO(compile_bundle(spec, stub_resolution(spec))))
    dash = next(n for n in zf.namelist() if "/dashboards/" in n)
    pos = yaml.safe_load(zf.read(dash))["position"]
    cols = [v for v in pos.values() if isinstance(v, dict) and v.get("type") == "COLUMN"]
    assert len(cols) == 1
    col = cols[0]
    assert col["meta"] == {"background": "BACKGROUND_TRANSPARENT", "width": 4}
    charts = {v["meta"]["sliceName"]: v["meta"] for v in pos.values()
              if isinstance(v, dict) and v.get("type") == "CHART"}
    assert charts["a"]["width"] == 8 and charts["a"]["height"] == 4 * 5   # 2 lines x 2 units x 5
    assert charts["b"]["width"] == 4 and charts["b"]["height"] == 2 * 5
    # explicit chart height overrides the sketch-derived height
    assert charts["c"]["height"] == 9 * 5
    # column children's parents chain includes the column
    chart_nodes = [v for v in pos.values() if isinstance(v, dict) and v.get("type") == "CHART"]
    b_node = next(v for v in chart_nodes if v["meta"]["sliceName"] == "b")
    assert any(p.startswith("COLUMN-") for p in b_node["parents"])


def test_spec_validators_cover_sketch():
    import pydantic

    base = {
        "spec_version": "1",
        "dashboard": {"title": "T", "slug": "sdc-t"},
        "charts": [{"name": "a", "type": "big_number_total", "dataset": DS, "metric": "COUNT(*)"}],
    }
    with pytest.raises(pydantic.ValidationError, match="needs a legend"):
        load_spec({**base, "layout": {"sketch": ["AA"]}})
    with pytest.raises(pydantic.ValidationError, match="unknown chart"):
        load_spec({**base, "layout": {"sketch": ["AA"], "legend": {"A": "nope"}}})
    with pytest.raises(pydantic.ValidationError, match="not placed in layout"):
        load_spec({**base,
                   "charts": base["charts"] + [
                       {"name": "b", "type": "big_number_total", "dataset": DS, "metric": "COUNT(*)"}],
                   "layout": {"sketch": ["AA"], "legend": {"A": "a"}}})
    with pytest.raises(pydantic.ValidationError, match="exactly one of rows / sketch"):
        load_spec({**base, "layout": {"tabs": [{
            "title": "x", "rows": [["a"]], "sketch": ["AA"], "legend": {"A": "a"}}]}})
    # untabbed sketch layout works end to end
    spec = load_spec({**base, "layout": {"sketch": ["AA"], "legend": {"A": "a"}}})
    bundle = compile_bundle(spec, stub_resolution(spec))
    pos = yaml.safe_load(zipfile.ZipFile(io.BytesIO(bundle)).read(
        next(n for n in zipfile.ZipFile(io.BytesIO(bundle)).namelist() if "/dashboards/" in n)))["position"]
    assert any(v.get("type") == "CHART" for v in pos.values() if isinstance(v, dict))


def test_hole_trailing_right():
    rows = parse_sketch(["AAAAAAAA...."], {"A": "a"}, 2)
    (chart,) = rows[0].children
    assert chart.width == 8  # row deliberately sums to less than 12


def test_hole_bottom_of_slice():
    rows = parse_sketch(["AAAA BBBB", ".... BBBB"], {"A": "a", "B": "b"}, 2)
    a, b = rows[0].children
    assert isinstance(a, SketchChart) and a.height == 2   # 1 line
    assert isinstance(b, SketchChart) and b.height == 4   # 2 lines, side by side, no COLUMN
    assert a.width == b.width == 6


def test_hole_bottom_of_stack():
    rows = parse_sketch(["AABB", "AACC", "AA.."], {"A": "a", "B": "b", "C": "c"}, 2)
    a, col = rows[0].children
    assert a.height == 6                    # 3 lines
    assert isinstance(col, SketchColumn)    # stack ends early at the dots
    assert [(c.name, c.height) for c in col.children] == [("b", 2), ("c", 2)]


def test_hole_errors_named():
    with pytest.raises(SketchError, match="empty space above chart"):
        parse_sketch(["AAAA ....", "AAAA BBBB"], {"A": "a", "B": "b"}, 2)
    with pytest.raises(SketchError, match="may only end a stack"):
        parse_sketch(["AABB", "AA..", "AACC"], {"A": "a", "B": "b", "C": "c"}, 2)
    with pytest.raises(SketchError, match="rows pack left"):
        parse_sketch(["AA .. BB", "AA .. BB"], {"A": "a", "B": "b"}, 2)
    with pytest.raises(SketchError, match="no vertical spacer"):
        parse_sketch(["AA", "..", "BB"], {"A": "a", "B": "b"}, 2)
    with pytest.raises(SketchError, match="reserved sketch symbol"):
        parse_sketch(["AA"], {"A": "a", ".": "dot"}, 2)


def test_holes_compile_end_to_end():
    spec = load_spec({
        "spec_version": "1",
        "dashboard": {"title": "T", "slug": "sdc-t"},
        "charts": [
            {"name": "a", "type": "big_number_total", "dataset": DS, "metric": "COUNT(*)"},
            {"name": "b", "type": "big_number_total", "dataset": DS, "metric": "COUNT(*)"},
        ],
        "layout": {"sketch": ["AAAA BBBB", ".... BBBB"], "legend": {"A": "a", "B": "b"}},
    })
    zf = zipfile.ZipFile(io.BytesIO(compile_bundle(spec, stub_resolution(spec))))
    dash = next(n for n in zf.namelist() if "/dashboards/" in n)
    pos = yaml.safe_load(zf.read(dash))["position"]
    assert not any(v.get("type") == "COLUMN" for v in pos.values() if isinstance(v, dict))
    charts = {v["meta"]["sliceName"]: v["meta"] for v in pos.values()
              if isinstance(v, dict) and v.get("type") == "CHART"}
    assert charts["a"]["height"] == 2 * 2 * 5 // 2  # 1 line x 2 units x 5 row units
    assert charts["b"]["height"] == 2 * charts["a"]["height"]
