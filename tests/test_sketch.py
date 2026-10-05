"""ASCII layout sketches: parsing, geometry, guillotine decomposition into
Superset's ROW/COLUMN tree, and compile integration."""

import io
import json
import zipfile

import pytest
import yaml

from chartwright.compiler import compile_bundle
from chartwright.sketch import BlockRef, SketchBlock, SketchChart, SketchColumn, SketchError, parse_sketch
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


# -- markdown and header blocks in a sketch ------------------------------------------------

MD, HEAD = BlockRef("markdown"), BlockRef("header")


def test_blocks_parse_like_charts_and_a_full_width_header_is_a_band():
    rows = parse_sketch(["TTTTTT", "HHHSSS", "LLLSSS", "LLLNNN", "XX....", "FFFFFF"],
                        {"T": HEAD, "H": HEAD, "L": "line", "S": "pie", "N": MD, "X": HEAD,
                         "F": MD}, 2)
    band, stacked, alone, footnote = rows
    assert band.header_band == SketchBlock("T", "header", 12, 2)
    left, right = stacked.children
    assert isinstance(left, SketchColumn) and left.width == 6
    assert left.children == [SketchBlock("H", "header", 6, 2), SketchChart("line", 6, 4)]
    assert right.children == [SketchChart("pie", 6, 4), SketchBlock("N", "markdown", 6, 2)]
    assert stacked.header_band is None
    # a header alone in a slot narrower than the sketch is no band: it sits in a column
    assert alone.children == [SketchBlock("X", "header", 4, 2)] and alone.header_band is None
    assert footnote.children == [SketchBlock("F", "markdown", 12, 2)]


def test_block_errors_are_named():
    with pytest.raises(SketchError, match="header 'H' spans 2 lines: a header is one sketch line"):
        parse_sketch(["HHHH", "HHHH", "AAAA"], {"H": HEAD, "A": "a"}, 2)
    with pytest.raises(SketchError, match="empty space above markdown 'N'"):
        parse_sketch(["AAAA ....", "AAAA NNNN"], {"A": "a", "N": MD}, 2)


DS_BLOCKS = {"database": "examples", "table": "t"}
BLOCK_CHARTS = [
    {"name": n, "type": t, "dataset": DS_BLOCKS, **extra} for n, t, extra in (
        ("Orders", "big_number_total", {"metric": "COUNT(*)"}),
        ("Revenue", "big_number_total", {"metric": "SUM(x)"}),
        ("Trend", "timeseries_line", {"metrics": ["SUM(x)"], "time_column": "ts"}),
        ("Mix", "pie", {"metric": "SUM(x)", "groupby": "g"}),
        ("Table", "table", {"columns": ["g"]}),
    )
]
BLOCK_SKETCH = {
    "sketch": ["TTTTTTTTTTTT",
               "KKKKKK RRRRRR",
               "HHHHHHHH MMMM",
               "LLLLLLLL MMMM",
               "LLLLLLLL NNNN",
               "XXXXXX ......",
               "PPPPPPPPPPPP",
               "FFFFFFFFFFFF"],
    "legend": {"T": {"header": "Headline numbers", "size": "large"},
               "K": "Orders", "R": "Revenue",
               "H": {"header": "Monthly trend", "background": "white"},
               "L": "Trend", "M": "Mix",
               "N": {"markdown": "Large deals are a third of revenue."},
               "X": {"header": "Detail", "size": "small"},
               "P": "Table",
               "F": {"markdown": "Source: ERP", "height": 1.6}},
}


def _blocks_spec(layout=None):
    return load_spec({
        "spec_version": "1",
        "dashboard": {"title": "T", "slug": "sdc-t"},
        "charts": [dict(c) for c in BLOCK_CHARTS],
        "layout": layout or BLOCK_SKETCH,
    })


def _pos(spec) -> dict:
    zf = zipfile.ZipFile(io.BytesIO(compile_bundle(spec, stub_resolution(spec))))
    return yaml.safe_load(zf.read(next(n for n in zf.namelist() if "/dashboards/" in n)))["position"]


def _shape(pos: dict, node_id: str = "GRID_ID"):
    """A layout as nested (type, label, width), ids aside."""
    node = pos[node_id]
    meta = node.get("meta") or {}
    label = meta.get("sliceName") or meta.get("text") or meta.get("code")
    kids = [_shape(pos, c) for c in node.get("children", [])]
    return (node["type"], label, meta.get("width"), *([kids] if kids else []))


def test_blocks_compile_to_superset_headers_markdown_and_columns():
    """A header across the sketch is a HEADER between rows; a header elsewhere sits in a
    COLUMN, stacked or alone, as Superset nests none in a ROW (isValidChild.ts); a
    markdown block is MARKDOWN in its row or column, its height drawn or its own."""
    pos = _pos(_blocks_spec())
    assert _shape(pos) == ("GRID", None, None, [
        ("HEADER", "Headline numbers", None),
        ("ROW", None, None, [("CHART", "Orders", 6), ("CHART", "Revenue", 6)]),
        ("ROW", None, None, [
            ("COLUMN", None, 8, [("HEADER", "Monthly trend", None), ("CHART", "Trend", 8)]),
            ("COLUMN", None, 4, [("CHART", "Mix", 4),
                                 ("MARKDOWN", "Large deals are a third of revenue.", 4)]),
        ]),
        ("ROW", None, None, [("COLUMN", None, 6, [("HEADER", "Detail", None)])]),
        ("ROW", None, None, [("CHART", "Table", 12)]),
        ("ROW", None, None, [("MARKDOWN", "Source: ERP", 12)]),
    ])
    assert pos["HEADER-sdc-1"] == {
        "type": "HEADER", "id": "HEADER-sdc-1", "children": [], "parents": ["ROOT_ID", "GRID_ID"],
        "meta": {"text": "Headline numbers", "headerSize": "LARGE_HEADER",
                 "background": "BACKGROUND_TRANSPARENT"}}
    assert pos["HEADER-sdc-3-1-1"]["meta"]["background"] == "BACKGROUND_WHITE"
    assert pos["HEADER-sdc-3-1-1"]["parents"] == ["ROOT_ID", "GRID_ID", "ROW-sdc-3", "COLUMN-sdc-3-1"]
    assert pos["MARKDOWN-sdc-3-2-2"]["meta"]["height"] == 2 * 5          # one drawn line
    assert pos["MARKDOWN-sdc-6-1"]["meta"]["height"] == 8                # its own 1.6 units
    assert pos["COLUMN-sdc-4-1"]["children"] == ["HEADER-sdc-4-1-1"]


def test_blocks_in_tabs_compile_at_the_tab_level():
    spec = _blocks_spec({"tabs": [{"title": "Main", **BLOCK_SKETCH}]})
    pos = _pos(spec)
    assert pos["TAB-sdc-1"]["children"][0] == "HEADER-sdc-t1-1"
    assert pos["HEADER-sdc-t1-1"]["parents"] == ["ROOT_ID", "GRID_ID", "TABS-sdc-1", "TAB-sdc-1"]


def test_a_chart_only_sketch_compiles_as_before():
    """Blocks add nodes only where a legend holds one: a sketch of charts compiles to
    the bytes it did before blocks (0.5.0), each chart in a column writing its own
    parents list (a shared one would be a YAML alias in the bundle)."""
    import hashlib

    spec = _sketch_spec()
    bundle = compile_bundle(spec, stub_resolution(spec))
    assert hashlib.sha256(bundle).hexdigest() == (
        "30f6d6e2c4c96d89ea1cc944a7205bf05b1c969991e62db6bd3a895543d5f83a")


@pytest.mark.parametrize("legend, message", [
    ({"A": "a", "N": {"markdown": "x", "width": 4}}, "a sketch draws its markdown blocks' widths"),
    ({"A": "a", "N": {"markdown": "x", "height": 1.3}}, "use fifths of a unit"),
    ({"A": "a", "N": {"header": "x", "size": "huge"}}, "small"),
    ({"A": "a", "N": {"header": ""}}, "at least 1 character"),
])
def test_bad_legend_blocks_are_named(legend, message):
    import pydantic

    with pytest.raises(pydantic.ValidationError) as err:
        load_spec({"spec_version": "1", "dashboard": {"title": "T", "slug": "sdc-t"},
                   "charts": [{"name": "a", "type": "big_number_total", "dataset": DS_BLOCKS,
                               "metric": "COUNT(*)"}],
                   "layout": {"sketch": ["AAAA", "NNNN"], "legend": legend}})
    assert message in str(err.value)


def test_a_sketch_of_blocks_needs_its_charts_placed_elsewhere():
    import pydantic

    with pytest.raises(pydantic.ValidationError, match="not placed in layout"):
        _blocks_spec({"sketch": ["TTTT"], "legend": {"T": {"header": "Only a header"}}})


@pytest.mark.parametrize("tabs", [False, True])
def test_blocks_round_trip_through_decompile(tabs):
    """compile -> decompile: no losses, the same layout tree when compiled again, and
    plan reads the two as equal."""
    from test_dashboard_settings import assert_lossless, roundtrip

    from chartwright.dashdiff import _normalize

    spec = _blocks_spec({"tabs": [{"title": "Main", **BLOCK_SKETCH}]} if tabs else None)
    out = assert_lossless(spec)
    section = out.spec["layout"]["tabs"][0] if tabs else out.spec["layout"]
    assert "sketch" in section
    blocks = [v for v in section["legend"].values() if isinstance(v, dict)]
    assert {"header": "Monthly trend", "background": "white"} in blocks
    assert {"markdown": "Source: ERP", "height": 1.6} in blocks
    again = load_spec(out.spec)
    assert _shape(_pos(again)) == _shape(_pos(spec))
    assert _normalize(again) == _normalize(spec)
    assert roundtrip(again).losses == []


def test_a_sketch_of_rows_and_blocks_without_columns_reads_back_as_rows():
    """No COLUMN, no sketch needed: decompile reads the header band and markdown as rows,
    and plan still reads them as the sketch the spec drew."""
    from test_dashboard_settings import assert_lossless

    out = assert_lossless(_blocks_spec({
        "sketch": ["TTTTTTTTTTTT", "KKKKKK RRRRRR", "LLLL MMMM NNNN", "PPPPPPPPPPPP"],
        "legend": {"T": {"header": "Top"}, "K": "Orders", "R": "Revenue", "L": "Trend",
                   "M": "Mix", "N": {"markdown": "Note"}, "P": "Table"}}))
    assert out.spec["layout"]["rows"][0] == {"header": "Top"}
    assert out.spec["layout"]["rows"][2][2] == {"markdown": "Note", "width": 4, "height": 2}


def test_two_columns_of_two_charts_read_back_as_one_band():
    """The even drawing of two equal stacks shares a line boundary, where the sketch
    parser cuts a band, so the copy drew two rows instead of one row of two columns.
    Decompile draws the stacks offset instead, and the layout compiles the same."""
    from test_dashboard_settings import roundtrip

    spec = _blocks_spec({"sketch": ["KKKK LLLLLLLL", "KKKK MMMMMMMM", "RRRR MMMMMMMM",
                                    "PPPPPPPPPPPP"],
                         "legend": {"K": "Orders", "R": "Revenue", "L": "Trend", "M": "Mix",
                                    "P": "Table"}})
    out = roundtrip(spec)
    assert out.losses == []
    assert _shape(_pos(load_spec(out.spec))) == _shape(_pos(spec))


def test_a_header_alone_beside_a_stacked_column_stays_rows_and_says_so():
    """No sketch draws a lone header beside a column of two charts in one band: split
    in two, the second band would open on the dots under the header. Decompile walks
    such a section as rows, flattening the column, and names it."""
    from chartwright.decompile import _section

    position = {
        "ROW-1": {"type": "ROW", "children": ["COLUMN-1", "COLUMN-2"],
                  "meta": {"background": "BACKGROUND_TRANSPARENT"}},
        "COLUMN-1": {"type": "COLUMN", "children": ["HEADER-1"], "meta": {"width": 4}},
        "HEADER-1": {"type": "HEADER", "children": [], "meta": {"text": "Aside"}},
        "COLUMN-2": {"type": "COLUMN", "children": ["CHART-a", "CHART-b"], "meta": {"width": 8}},
        "CHART-a": {"type": "CHART", "children": [], "meta": {"sliceName": "a", "width": 8, "height": 20}},
        "CHART-b": {"type": "CHART", "children": [], "meta": {"sliceName": "b", "width": 8, "height": 20}},
    }
    losses: list = []
    section = _section(position, ["ROW-1"], {"a", "b"}, losses, {})
    assert "rows" in section and "sketch" not in section
    assert any("COLUMN" in loss.what for loss in losses)


def test_plan_reports_a_block_changed_in_the_ui(monkeypatch):
    from test_dashboard_settings import edit_dashboard, plan_against

    spec = _blocks_spec()
    assert plan_against(spec, None, monkeypatch)["clean"] is True
    for change in (
        lambda pos: pos["HEADER-sdc-3-1-1"]["meta"].update(text="Renamed"),
        lambda pos: pos["MARKDOWN-sdc-3-2-2"]["meta"].update(code="Edited"),
        lambda pos: pos["HEADER-sdc-1"]["meta"].update(headerSize="SMALL_HEADER"),
        lambda pos: pos["COLUMN-sdc-4-1"]["meta"].update(width=8),
    ):
        out = plan_against(spec, edit_dashboard(lambda doc: change(doc["position"])), monkeypatch)
        assert out["layout_changed"] is True and out["charts_changed"] == [], out


def test_the_design_brain_reads_blocks_as_it_reads_them_in_rows():
    """A markdown slot is a markdown item of its band, a header across the sketch no
    band at all, and a stack sums the heights of its charts and markdown."""
    from chartwright.design.model import RuleContext
    from chartwright.design.presets import load_overlay, params_for

    ctx = RuleContext(_blocks_spec(), params_for("analytical", load_overlay(None)))
    (section,) = ctx.sections
    bands = section.bands
    assert len(bands) == 5                       # the header band holds no charts
    kpis, middle, detail, table, footnote = bands
    assert [i.charts for i in middle.items] == [["Trend"], ["Mix"]]
    assert middle.items[1].height == ctx.height("Mix") + 2   # the drawn note under the pie
    assert detail.items[0].is_markdown and detail.items[0].charts == []
    assert footnote.items[0].is_markdown and footnote.items[0].height == 1.6
    assert ctx.is_sketch("Trend")


def test_a_lone_kpi_beside_a_sketch_note_is_a_finished_band():
    """layout.kpi-band asks a band of one KPI for a partner: a markdown note beside it,
    in a sketch as in rows."""
    from chartwright.design import advise
    from chartwright.design.presets import load_overlay

    def kpi_band(first_line, extra):
        spec = load_spec({
            "spec_version": "1", "dashboard": {"title": "T", "slug": "sdc-t"},
            "charts": [dict(c) for c in BLOCK_CHARTS if c["name"] != "Revenue"],
            "layout": {"sketch": [first_line, "LLLLLLLL MMMM", "PPPPPPPPPPPP"],
                       "legend": {"K": "Orders", "L": "Trend", "M": "Mix", "P": "Table", **extra}}})
        return [f.detail for f in advise(spec, overlay=load_overlay(None)).findings
                if f.rule == "layout.kpi-band"]

    assert any("looks unfinished" in d for d in kpi_band("KKKK........", {}))
    assert kpi_band("KKKK NNNNNNNN", {"N": {"markdown": "Orders since launch"}}) == []
