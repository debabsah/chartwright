"""Presentation controls: points on a mixed chart, big-number text sizes and a
trendline's date format, axis-title spacing, named markdown blocks, links to a tab,
and per-chart cross-filter scopes. Every key emitted is checked against the Superset
source (paths in the code beside each); each round-trips through decompile and shows
up in `plan` when the spec value changes."""

import io
import json
import sys
import zipfile
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from chartwright import dashdiff, ids
from chartwright.compiler import compile_bundle
from chartwright.dashdiff import _normalize
from chartwright.decompile import decompile_bundle
from chartwright.spec import load_spec
from chartwright.testing import edit_bundle, stub_resolution

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
from params_drift import CONTRACT, check  # noqa: E402

DS = {"database": "examples", "table": "orders"}
LINE = {"name": "Revenue", "type": "timeseries_line", "dataset": DS,
        "metrics": ["SUM(revenue)"], "time_column": "order_date", "time_grain": "P1M"}
KPI = {"name": "Orders", "type": "big_number_total", "dataset": DS, "metric": "COUNT(*)"}
TREND = {"name": "Orders trend", "type": "big_number_trend", "dataset": DS, "metric": "COUNT(*)",
         "time_column": "order_date", "time_grain": "P1M"}
MIXED = {"name": "Volume and delays", "type": "mixed", "dataset": DS, "x_column": "order_date",
         "time_grain": "P1M", "a": {"metrics": ["COUNT(*)"]},
         "b": {"metrics": ["MAX(delay)"], "kind": "line", "axis": "secondary"}}


def _spec(charts=None, dashboard=None, layout=None):
    charts = charts or [KPI, LINE]
    return load_spec({
        "spec_version": "1",
        "dashboard": {"title": "Sales", "slug": "sales", **(dashboard or {})},
        "charts": charts,
        "layout": layout or {"rows": [[c["name"]] for c in charts]},
    })


def _compiled(spec) -> bytes:
    return compile_bundle(spec, stub_resolution(spec))


def _docs(bundle: bytes) -> dict[str, dict]:
    zf = zipfile.ZipFile(io.BytesIO(bundle))
    return {n: yaml.safe_load(zf.read(n)) for n in zf.namelist() if n.endswith(".yaml")}


def _dashboard(spec) -> dict:
    return next(d for n, d in _docs(_compiled(spec)).items() if "/dashboards/" in n)


def _params(spec) -> dict:
    return {d["slice_name"]: d["params"] for n, d in _docs(_compiled(spec)).items() if "/charts/" in n}


def _lookup(spec):
    by_uuid = {ds.uuid: {"database": ds.database_name, "schema": ds.schema, "table": ds.table}
               for ds in stub_resolution(spec).datasets.values()}
    return lambda u: by_uuid.get(u)


def _decompile(spec, edit=None):
    """Decompile `spec`'s bundle, after `edit(path, doc)` when given: what a UI user changed."""
    bundle = _compiled(spec)
    if edit is not None:
        bundle = edit_bundle(bundle, edit)
    return decompile_bundle(bundle, _lookup(spec))


def _edit_chart(name, **params):
    def edit(path, doc):
        if "/charts/" in path and doc["slice_name"] == name:
            doc["params"].update(params)
    return edit


def _edit_metadata(**metadata):
    def edit(path, doc):
        if "/dashboards/" in path:
            doc["metadata"].update(metadata)
    return edit


def _round_trips(spec):
    result = _decompile(spec)
    assert result.losses == [], result.losses_json()
    assert _normalize(load_spec(result.spec)) == _normalize(spec)
    return result


def _contract_clean(emitted: dict[str, set[str]]) -> None:
    """Every key emitted is one the viz type's control panel declares, on every release
    (6.1.0-only keys are allowed, and ignored, before 6.1.0)."""
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    for version in contract:
        assert check(version, contract, emitted) == [], version


def _plan(target, live, monkeypatch) -> dict:
    """`plan` against a live dashboard that is `live` compiled and exported."""
    import chartwright.resolver as resolver

    monkeypatch.setattr(resolver, "resolve", lambda s, c, *_: stub_resolution(s))
    monkeypatch.setattr(dashdiff, "decompile_live", lambda slug, c: _decompile(live))

    class Client:
        def find_dashboard_by_slug(self, slug):
            return {"id": 1, "uuid": str(ids.dashboard_uuid(slug))}

    return json.loads(dashdiff.plan(target, Client()).to_json())


# -- a mixed chart's points ------------------------------------------------------------

def test_mixed_series_can_be_scatter(monkeypatch):
    """kind scatter writes seriesType / seriesTypeB "scatter" (MixedTimeseries/
    controlPanel.tsx seriesType choices; EchartsTimeseriesSeriesType.Scatter)."""
    points = {**MIXED, "b": {**MIXED["b"], "kind": "scatter"}}
    p = _params(_spec([points]))[MIXED["name"]]
    assert (p["seriesType"], p["seriesTypeB"]) == ("bar", "scatter")
    assert "areaB" not in p
    _round_trips(_spec([points]))
    plan = _plan(_spec([points]), _spec([MIXED]), monkeypatch)
    assert plan["charts_changed"] == [MIXED["name"]]
    # a UI chart's points read back as points, not as a line with a loss
    result = _decompile(_spec([MIXED]), _edit_chart(MIXED["name"], seriesType="scatter"))
    assert result.losses == [], result.losses_json()
    assert result.spec["charts"][0]["a"]["kind"] == "scatter"


# -- big-number text sizes and a trendline's dates ---------------------------------------

def test_big_number_sizes_default_to_the_pinned_values():
    p = _params(_spec([KPI, TREND]))
    assert (p["Orders"]["header_font_size"], p["Orders"]["subheader_font_size"]) == (0.4, 0.15)
    assert (p["Orders trend"]["header_font_size"], p["Orders trend"]["subheader_font_size"]) == (0.4, 0.15)
    assert not {"time_format", "force_timestamp_formatting"} & (set(p["Orders"]) | set(p["Orders trend"]))


def test_big_number_sizes_and_the_trendline_date_format(monkeypatch):
    """BigNumber/sharedControls.ts (the size options, 4.1.4 to 6.1.0); a trendline's
    time_format formats its dates (BigNumberWithTrendline/transformProps.ts:245-249, :275
    at 6.1.0) and is never forced, which would print the number itself as a date."""
    last = {**KPI, "name": "Last order", "metric": "MAX(order_date)", "date_format": "%b %d, %Y",
            "header_font_size": 0.3, "subtitle_font_size": 0.2, "subtitle": "most recent"}
    trend = {**TREND, "trend_date_format": "%b %Y", "header_font_size": 0.5}
    spec = _spec([last, trend])
    p = _params(spec)
    assert (p["Last order"]["header_font_size"], p["Last order"]["subheader_font_size"]) == (0.3, 0.2)
    assert p["Orders trend"]["time_format"] == "%b %Y"
    assert "force_timestamp_formatting" not in p["Orders trend"]
    assert p["Orders trend"]["header_font_size"] == 0.5
    _round_trips(spec)
    _contract_clean({"big_number_total": set(p["Last order"]), "big_number": set(p["Orders trend"])})
    plan = _plan(spec, _spec([{**last, "header_font_size": 0.4}, trend]), monkeypatch)
    assert plan["charts_changed"] == ["Last order"]
    plan = _plan(spec, _spec([last, {**trend, "trend_date_format": "%Y"}]), monkeypatch)
    assert plan["charts_changed"] == ["Orders trend"]


def test_written_default_sizes_build_and_plan_as_omitted():
    written = _spec([{**KPI, "header_font_size": 0.4, "subtitle_font_size": 0.15},
                     {**TREND, "header_font_size": 0.4}])
    omitted = _spec([KPI, TREND])
    assert _compiled(written) == _compiled(omitted)
    assert _normalize(written) == _normalize(omitted)


def test_big_number_sizes_take_only_superset_options():
    with pytest.raises(ValidationError):
        _spec([{**KPI, "header_font_size": 0.45}])
    with pytest.raises(ValidationError):
        _spec([{**TREND, "subtitle_font_size": 0.2}])  # a trendline has no subtitle size


def test_big_number_decompile_reads_ui_sizes_and_names_the_rest():
    result = _decompile(_spec([KPI, TREND]), _edit_chart("Orders", header_font_size=0.6,
                                                         subheader_font_size=0.3))
    assert result.losses == [], result.losses_json()
    back = result.spec["charts"][0]
    assert (back["header_font_size"], back["subtitle_font_size"]) == (0.6, 0.3)
    result = _decompile(_spec([KPI]), _edit_chart("Orders", header_font_size=0.45))
    assert [loss.what for loss in result.losses] == [
        "header_font_size 0.45 is not one of Superset's sizes; dropped"]
    # a trendline's comparison size is no field: a changed one is a named setting
    result = _decompile(_spec([TREND]), _edit_chart("Orders trend", subheader_font_size=0.3))
    assert any("subheader_font_size=0.3" in loss.what for loss in result.losses), result.losses_json()


# -- axis-title spacing ----------------------------------------------------------------

def test_unset_title_spacing_is_the_tools_own():
    titled = {**LINE, "x_axis_title": "Month", "y_axis_title": "USD"}
    p = _params(_spec([titled]))["Revenue"]
    assert (p["x_axis_title_margin"], p["y_axis_title_margin"], p["y_axis_title_position"]) == (30, 15, "Top")


def test_title_spacing_wins_and_round_trips(monkeypatch):
    """titleControls (sections/chartTitle.tsx:41-101 at 6.1.0, the same keys at 4.1.4 and
    5.0.0): x_axis_title_margin, y_axis_title_margin and y_axis_title_position."""
    titled = {**LINE, "x_axis_title": "Month", "x_axis_title_margin": 15, "y_axis_title": "USD",
              "y_axis_title_margin": 45, "y_axis_title_position": "Left"}
    mixed = {**MIXED, "y_axis_title_secondary": "Minutes", "y_axis_title_margin": 45,
             "y_axis_title_position": "Left"}
    spec = _spec([titled, mixed])
    p = _params(spec)
    assert (p["Revenue"]["x_axis_title_margin"], p["Revenue"]["y_axis_title_margin"],
            p["Revenue"]["y_axis_title_position"]) == (15, 45, "Left")
    pm = p[MIXED["name"]]
    assert (pm["yAxisTitleSecondary"], pm["y_axis_title_margin"], pm["y_axis_title_position"]) == (
        "Minutes", 45, "Left")
    _round_trips(spec)
    _contract_clean({"echarts_timeseries_line": set(p["Revenue"]), "mixed_timeseries": set(pm)})
    plan = _plan(spec, _spec([{**titled, "y_axis_title_position": "Top"}, mixed]), monkeypatch)
    assert plan["charts_changed"] == ["Revenue"]


def test_written_tool_spacing_builds_and_plans_as_unset(monkeypatch):
    titled = {**LINE, "x_axis_title": "Month", "y_axis_title": "USD"}
    written = _spec([{**titled, "x_axis_title_margin": 30, "y_axis_title_margin": 15,
                      "y_axis_title_position": "Top"}])
    assert _compiled(written) == _compiled(_spec([titled]))
    assert _plan(written, _spec([titled]), monkeypatch)["clean"]
    bar = {"name": "By region", "type": "bar", "dataset": DS, "x_column": "region",
           "metrics": ["SUM(revenue)"], "orientation": "horizontal", "y_axis_title": "USD"}
    assert _plan(_spec([{**bar, "y_axis_title_position": "Left"}]), _spec([bar]), monkeypatch)["clean"]


def test_title_spacing_needs_its_title():
    for extra in ({"x_axis_title_margin": 20}, {"y_axis_title_margin": 20},
                  {"y_axis_title_position": "Left"}):
        with pytest.raises(ValidationError, match="needs"):
            _spec([{**LINE, **extra}])
    with pytest.raises(ValidationError):
        _spec([{**LINE, "y_axis_title": "USD", "y_axis_title_position": "Right"}])


def test_ui_title_spacing_reads_back_beside_its_title():
    spec = _spec([{**LINE, "y_axis_title": "USD"}])
    result = _decompile(spec, _edit_chart("Revenue", y_axis_title_position="Left", y_axis_title_margin=15))
    assert result.losses == [], result.losses_json()
    back = result.spec["charts"][0]
    assert back["y_axis_title_position"] == "Left" and "y_axis_title_margin" not in back
    # without a title they do nothing, and read as nothing
    result = _decompile(_spec([LINE]), _edit_chart("Revenue", x_axis_title_margin=50,
                                                   y_axis_title_position="Left"))
    assert result.losses == [], result.losses_json()
    assert not {"x_axis_title_margin", "y_axis_title_position"} & set(result.spec["charts"][0])
    result = _decompile(spec, _edit_chart("Revenue", y_axis_title_margin="wide"))
    assert [loss.what for loss in result.losses] == ["y_axis_title_margin 'wide' not representable; dropped"]


# -- named markdown blocks -------------------------------------------------------------

NOTE = {"markdown": "Figures refresh nightly.", "width": 12, "height": 1}


def _markdown_nodes(spec) -> dict:
    return {k: n for k, n in _dashboard(spec)["position"].items()
            if isinstance(n, dict) and n.get("type") == "MARKDOWN"}


def test_named_markdown_block_gets_its_component_id(monkeypatch):
    """The component id is the block's DOM id: Markdown/Markdown.tsx:418 at 6.1.0
    (gridComponents/Markdown.jsx:363 at 4.1.4 and 5.0.0), so css can use #MARKDOWN-<id>."""
    layout = {"rows": [[{**NOTE, "id": "refresh-note"}], ["Orders"], ["Revenue"]],
              "footer": [[{**NOTE, "markdown": "Owner: analytics", "id": "owner"}], [NOTE]]}
    css = {"css": "#MARKDOWN-owner { font-size: 12px; }"}
    spec = _spec(layout=layout, dashboard=css)
    nodes = _markdown_nodes(spec)
    assert "MARKDOWN-refresh-note" in nodes and "MARKDOWN-owner" in nodes
    assert nodes["MARKDOWN-owner"]["meta"]["code"] == "Owner: analytics"
    assert sum(k.startswith("MARKDOWN-sdc-") for k in nodes) == 1  # the unnamed block's positional id
    assert nodes["MARKDOWN-owner"]["parents"] == ["ROOT_ID", "GRID_ID", "ROW-sdc-footer-1"]
    result = _round_trips(spec)
    assert result.spec["layout"]["rows"][0][0]["id"] == "refresh-note"
    assert "id" not in result.spec["layout"]["footer"][1][0]
    renamed = {**layout, "rows": [[{**NOTE, "id": "nightly-note"}], ["Orders"], ["Revenue"]]}
    assert _plan(spec, _spec(layout=renamed, dashboard=css), monkeypatch)["layout_changed"]


def test_named_markdown_block_in_a_sketch(monkeypatch):
    layout = {"sketch": ["KKKK LLLLLLLL", "nnnnnnnnnnnn"],
              "legend": {"K": "Orders", "L": "Revenue", "n": {"markdown": NOTE["markdown"], "id": "note"}}}
    spec = _spec(layout=layout)
    assert list(_markdown_nodes(spec)) == ["MARKDOWN-note"]
    _round_trips(spec)
    assert _plan(spec, spec, monkeypatch)["clean"]
    unnamed = {**layout, "legend": {**layout["legend"], "n": {"markdown": NOTE["markdown"]}}}
    assert _plan(spec, _spec(layout=unnamed), monkeypatch)["layout_changed"]


def test_named_markdown_block_validation():
    def rows(*blocks):
        return {"rows": [list(blocks), ["Orders"], ["Revenue"]]}

    for bad in ("Refresh", "1note", "note_1", "a" * 49):
        with pytest.raises(ValidationError):
            _spec(layout=rows({**NOTE, "id": bad}))
    with pytest.raises(ValidationError, match="'sdc-' prefix is reserved"):
        _spec(layout=rows({**NOTE, "id": "sdc-note"}))
    with pytest.raises(ValidationError, match=r"duplicate markdown ids \['note'\]"):
        _spec(layout={**rows({**NOTE, "id": "note"}), "footer": [[{**NOTE, "id": "note"}]]})
    with pytest.raises(ValidationError, match=r"duplicate markdown ids \['note'\]"):
        _spec(layout={"tabs": [{"title": "A", "rows": [[{**NOTE, "id": "note"}], ["Orders"]]},
                               {"title": "B", "sketch": ["RRRR nnnn"], "legend": {
                                   "R": "Revenue", "n": {"markdown": "x", "id": "note"}}}]})


def test_ui_minted_markdown_ids_are_not_names():
    """The UI mints `MARKDOWN-${nanoid()}` (dashboard/util/newComponentFactory.ts:74 at 6.1.0):
    mixed case and '_', which no spec id can be, so such a block decompiles without an id."""
    spec = _spec(layout={"rows": [[{**NOTE, "id": "refresh-note"}], ["Orders"], ["Revenue"]]})
    bundle = _compiled(spec)
    src, out = zipfile.ZipFile(io.BytesIO(bundle)), io.BytesIO()
    with zipfile.ZipFile(out, "w") as dst:
        for n in src.namelist():
            dst.writestr(n, src.read(n).replace(b"MARKDOWN-refresh-note", b"MARKDOWN-V1StGXR8_Z5jdHi6B-myT"))
    block = decompile_bundle(out.getvalue(), _lookup(spec)).spec["layout"]["rows"][0][0]
    assert "id" not in block and block["markdown"] == NOTE["markdown"]


# -- markdown links to a tab -------------------------------------------------------------

PIE = {"name": "By segment", "type": "pie", "dataset": DS, "metric": "SUM(revenue)", "groupby": "segment"}
TABLE = {"name": "Top customers", "type": "table", "dataset": DS, "groupby": ["customer"],
         "metrics": ["SUM(revenue)"]}
LINKS = "[the trend](tab:Trend) · [customers](tab:Customers) · [the mix](tab:Sales/Mix)"


def _with_links(markdown=LINKS, sketch=False):
    customers = ({"title": "Customers", "sketch": ["TTTTTTTT nnnn"],
                  "legend": {"T": "Top customers", "n": {"markdown": markdown}}} if sketch
                 else {"title": "Customers", "rows": [["Top customers"]]})
    return _spec([LINE, PIE, TABLE], layout={
        "tabs": [{"title": "Sales", "tabs": [{"title": "Trend", "rows": [["Revenue"]]},
                                             {"title": "Mix", "rows": [["By segment"]]}]},
                 customers],
        **({} if sketch else {"footer": [[{"markdown": markdown, "width": 12, "height": 1}]]})})


def _tab_ids_by_title(dash) -> dict:
    return {n["meta"]["text"]: k for k, n in dash["position"].items()
            if isinstance(n, dict) and n.get("type") == "TAB"}


def _codes(dash) -> list[str]:
    return [n["meta"]["code"] for n in dash["position"].values()
            if isinstance(n, dict) and n.get("type") == "MARKDOWN"]


def test_tab_links_compile_to_the_dashboard_url_with_the_tab_in_the_hash():
    """Superset opens the tab named in the URL hash on load: getLocationHash (util/
    getLocationHash.ts:20) -> directPathToChild (actions/hydrate.ts:287-294 at 6.1.0,
    hydrate.js:222-226 at 4.1.4 / 5.0.0) -> each Tabs starts on the child on that path
    (gridComponents/Tabs/Tabs.tsx:135-140; Tabs.jsx:190-193 / :139-142)."""
    for sketch in (False, True):
        dash = _dashboard(_with_links(sketch=sketch))
        tab = _tab_ids_by_title(dash)
        assert _codes(dash) == [f"[the trend](/superset/dashboard/sales/#{tab['Trend']}) · "
                                f"[customers](/superset/dashboard/sales/#{tab['Customers']}) · "
                                f"[the mix](/superset/dashboard/sales/#{tab['Mix']})"]
        assert dash["position"][tab["Trend"]]["parents"][-1].startswith("TABS-")  # a sub-tab
    assert _codes(_dashboard(_with_links("[plain](https://example.com)"))) == ["[plain](https://example.com)"]


def test_tab_links_round_trip_and_plan(monkeypatch):
    spec = _with_links()
    result = _round_trips(spec)  # tab:Trend and its canonical tab:Sales/Trend open the same tab
    assert result.spec["layout"]["footer"][0][0]["markdown"] == (
        "[the trend](tab:Sales/Trend) · [customers](tab:Customers) · [the mix](tab:Sales/Mix)")
    assert _plan(spec, spec, monkeypatch)["clean"]
    moved = _with_links(LINKS.replace("tab:Trend", "tab:Customers"))
    assert _plan(spec, moved, monkeypatch)["layout_changed"]


def test_tab_link_validation():
    with pytest.raises(ValidationError, match=r"no such tab; link one of \['Sales', 'Sales/Trend'"):
        _with_links("[x](tab:Nope)")

    def ambiguous(md):  # two sub-tabs titled Trend: the bare title is ambiguous, Parent/Child is not
        return _spec([LINE, PIE], layout={"tabs": [
            {"title": "North", "tabs": [{"title": "Trend", "rows": [["Revenue"]]}]},
            {"title": "South", "tabs": [{"title": "Trend", "rows": [["By segment"]]}]}],
            "footer": [[{"markdown": md}]]})

    with pytest.raises(ValidationError, match="is ambiguous: 2 sub-tabs .* write tab:Parent/Child"):
        ambiguous("[x](tab:Trend)")
    position = _dashboard(ambiguous("[x](tab:South/Trend)"))["position"]
    assert _codes({"position": position}) == ["[x](/superset/dashboard/sales/#TAB-sdc-2-1)"]
    assert position["TAB-sdc-2-1"]["meta"]["text"] == "Trend" and position["TAB-sdc-2"]["meta"]["text"] == "South"
    with pytest.raises(ValidationError, match=r"no such tab; link one of \[\]"):
        _spec(layout={"rows": [[{"markdown": "[x](tab:Sales)"}], ["Orders"], ["Revenue"]]})


def test_links_to_other_dashboards_stay_as_written():
    other = "[elsewhere](/superset/dashboard/other/#TAB-sdc-1)"
    result = _round_trips(_with_links(other))
    assert result.spec["layout"]["footer"][0][0]["markdown"] == other
