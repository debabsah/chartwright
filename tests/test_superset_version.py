"""Version-gated spec fields: the registry, the check that holds a spec to a
Superset release, how check/apply/plan learn the instance's release, and the
offline `compile --superset-version`. No network: version detection is mocked."""

import io
import json
import zipfile

import pytest
import requests

import chartwright.cli as cli
from chartwright import ids
from chartwright.apply import apply, check
from chartwright.client import SupersetClient
from chartwright.dashdiff import plan
from chartwright.spec import json_schema, load_spec
from chartwright.versions import GATED_FIELDS, check_spec_version, gated_fields_used, parse_version

from test_dashboard_settings import DS, spec_data

TREND = {"name": "Trend", "type": "big_number_trend", "metric": "SUM(amount)",
         "time_column": "ordered_at", "dataset": DS}
TABLE = {"name": "Table", "type": "table", "metrics": ["SUM(amount)"], "groupby": ["region"],
         "dataset": DS}
LINE = {"name": "Line", "type": "timeseries_line", "metrics": ["SUM(amount)"],
        "time_column": "ordered_at", "dataset": DS}
MIXED = {"name": "Mixed", "type": "mixed", "x_column": "ordered_at", "time_grain": "P1M",
         "dataset": DS, "a": {"metrics": ["SUM(amount)"], "groupby": "region"},
         "b": {"metrics": ["SUM(amount)"], "kind": "line", "axis": "secondary"}}


class FakeClient:
    """orders(region, city, amount, ordered_at, active) on a Superset that reports
    `version`. Any call beyond reading metadata fails the test: a refused spec
    must stop before anything on the instance is written."""

    def __init__(self, version="6.1.0", dashboard=None):
        self.version = version
        self.version_asked = 0
        self.dashboard = dashboard

    def superset_version(self):
        self.version_asked += 1
        return self.version

    def find_datasets(self, table):
        return [{"id": 1, "uuid": "11111111-1111-1111-1111-111111111111", "table_name": table,
                 "schema": None, "database": {"database_name": "warehouse"}}]

    def dataset_detail(self, dataset_id):
        cols = ["region", "city", "amount", "ordered_at", "active"]
        return {"columns": [{"column_name": c} for c in cols], "metrics": []}

    def find_dashboard_by_slug(self, slug):
        return self.dashboard

    def __getattr__(self, name):
        raise AssertionError(f"unexpected client call {name!r}")


def spec(dashboard=None, charts=None):
    return load_spec(spec_data(dashboard, charts))


def codes(errors):
    return [(e["code"], e["chart"], e["ref"]) for e in errors]


# -- the registry ----------------------------------------------------------------------


def test_parse_version():
    assert parse_version("5.0.0") == (5, 0, 0)
    assert parse_version("4.1.4rc1") == (4, 1, 4)
    assert parse_version("v6.1") == (6, 1, 0)
    for text in ("0.0.0-dev", "", "latest", None):  # a development build is no release
        assert parse_version(text) is None


def test_the_registry_gates_the_unsafe_fields_and_warns_for_the_ignored_ones():
    by_field = {g.field: g for g in GATED_FIELDS}
    assert {f: (g.since, g.severity) for f, g in by_field.items()} == {
        "tags": ("6.0.0", "error"),
        "theme": ("6.0.0", "error"),  # no theme in the import schema before 6.0.0
        "show_chart_timestamps": ("6.1.0", "error"),
        "x_label_every": ("6.1.0", "warn"),  # a time axis needs force_max_interval (6.1.0)
        "subtitle": ("6.0.0", "warn"),
        "column_headers": ("6.0.0", "warn"),
        "show_value": ("6.0.0", "warn"),  # on a stacked mixed query: only_total's labels
        "x_order": ("6.1.0", "warn"),  # a heatmap value order: by total from 6.1.0
        "y_order": ("6.1.0", "warn"),
        "steps": ("6.1.0", "error"),  # a bridge needs show_total: false (6.1.0)
        "total_label": ("6.1.0", "warn"),
        "increase_label": ("6.1.0", "warn"),
        "decrease_label": ("6.1.0", "warn"),
        "row_limit": ("6.0.0", "warn"),  # a box plot's: the 4.1.4 and 5.0.0 panel has none
    }
    schema = json.dumps(json_schema())
    for g in GATED_FIELDS:
        assert f'"{g.field}"' in schema, g.field  # each names a real spec field
        assert g.before and g.source


def test_a_spec_without_gated_fields_uses_none():
    assert gated_fields_used(spec()) == []
    assert check_spec_version(spec(), "4.1.4").errors == []


# -- errors: fields an older release can't take ------------------------------------------


@pytest.mark.parametrize("tags", [["finance"], []])  # [] is written too, and the key alone fails
def test_tags_need_6_0(tags):
    s = spec({"tags": tags}, [{**TREND, "tags": tags}])
    for version in ("4.1.4", "5.0.0"):
        out = check_spec_version(s, version)
        assert codes(out.errors) == [("superset_version_too_old", None, "tags"),
                                     ("superset_version_too_old", "Trend", "tags")]
        assert out.version == version and "6.0.0" in out.errors[0]["detail"]
    for version in ("6.0.0", "6.1.0", "7.0"):
        assert check_spec_version(s, version).errors == []


def test_chart_timestamps_need_6_1():
    s = spec({"show_chart_timestamps": True})
    for version in ("4.1.4", "5.0.0", "6.0.0"):
        assert codes(check_spec_version(s, version).errors) == [
            ("superset_version_too_old", None, "show_chart_timestamps")]
    assert check_spec_version(s, "6.1.0").errors == []
    assert gated_fields_used(spec({"show_chart_timestamps": False})) == []


def test_an_unknown_version_refuses_the_unsafe_fields_and_names_the_way_out():
    out = check_spec_version(spec({"show_chart_timestamps": True}), None)
    assert codes(out.errors) == [("superset_version_unknown", None, "show_chart_timestamps")]
    assert "--superset-version" in out.errors[0]["detail"] and out.version is None


# -- warnings: fields an older release ignores -------------------------------------------


def _ignored_fields_spec():
    mixed = {**MIXED, "a": {**MIXED["a"], "show_value": True, "stack": True}}
    return spec(charts=[
        {**TREND, "subtitle": "vs plan"},
        {**TABLE, "column_headers": {"SUM(amount)": "Revenue"}},
        {**LINE, "x_label_every": True},
        mixed,
    ])


def test_fields_older_releases_ignore_warn_and_never_block():
    out = check_spec_version(_ignored_fields_spec(), "5.0.0")
    assert out.ok
    assert sorted((w["field"], w["chart"]) for w in out.warnings) == [
        ("column_headers", "Table"), ("show_value", "Mixed"), ("subtitle", "Trend"),
        ("x_label_every", "Line")]
    assert all(w["code"] == "field_ignored_before_version" for w in out.warnings)
    assert {w["field"]: w["since"] for w in out.warnings} == {
        "column_headers": "6.0.0", "show_value": "6.0.0", "subtitle": "6.0.0",
        "x_label_every": "6.1.0"}
    # 6.0.0 takes all but x_label_every on a time axis, which needs 6.1.0's force_max_interval.
    assert [w["field"] for w in check_spec_version(_ignored_fields_spec(), "6.0.0").warnings] == [
        "x_label_every"]
    assert check_spec_version(_ignored_fields_spec(), "6.1.0").warnings == []
    unknown = check_spec_version(_ignored_fields_spec(), None)
    assert unknown.ok and len(unknown.warnings) == 4


def test_a_heatmaps_label_step_is_no_later_release_field():
    """A heatmap's x_label_every is its own xscale_interval, which every release reads
    (Heatmap/controlPanel.tsx at 4.1.4, 5.0.0 and 6.1.0); only an axis chart's needs 6.1.0."""
    heatmap = {"name": "Heat", "type": "heatmap", "x_column": "region", "y_column": "city",
               "metric": "SUM(amount)", "dataset": DS, "x_label_every": 2, "y_label_every": 1,
               "left_margin": 16}
    assert check_spec_version(spec(charts=[heatmap]), "4.1.4").warnings == []
    out = check_spec_version(spec(charts=[heatmap, {**LINE, "x_label_every": True}]), "4.1.4")
    assert [(w["field"], w["chart"]) for w in out.warnings] == [("x_label_every", "Line")]


def test_stacked_value_labels_warn_on_what_the_author_wrote():
    """only_total defaults to true, so the warning names show_value on the stacked
    query, which the author wrote, and says what the older release draws."""
    stacked = {**MIXED, "a": {**MIXED["a"], "show_value": True, "stack": True}}
    [w] = check_spec_version(spec(charts=[stacked]), "5.0.0").warnings
    assert (w["field"], w["chart"], w["since"]) == ("show_value", "Mixed", "6.0.0")
    assert "only_total on" not in w["detail"] and "this instance runs 5.0.0" in w["detail"]
    assert "this release labels every segment of a stacked mixed chart" in w["detail"]
    [w] = check_spec_version(spec(charts=[stacked]), None).warnings
    assert "a release before 6.0.0 labels every segment" in w["detail"]
    assert check_spec_version(spec(charts=[stacked]), "6.0.0").warnings == []


HEATMAP = {"name": "Heat", "type": "heatmap", "x_column": "region", "y_column": "city",
           "metric": "SUM(amount)", "dataset": DS}


def test_a_heatmap_value_order_warns_before_6_1():
    """Every release takes sort_x_axis and sort_y_axis, but only 6.1.0 sorts each axis
    itself (sortAxisValues, a label's total); before, the labels come in the order the
    query sorts the cells. Label orders mean the same everywhere and never warn."""
    by_value = {**HEATMAP, "x_order": "value_desc", "y_order": "value_asc"}
    for version in ("4.1.4", "5.0.0", "6.0.0"):
        out = check_spec_version(spec(charts=[by_value]), version)
        assert out.ok and [(w["field"], w["chart"], w["since"]) for w in out.warnings] == [
            ("x_order", "Heat", "6.1.0"), ("y_order", "Heat", "6.1.0")]
        assert f"this instance runs {version}" in out.warnings[0]["detail"]
        assert "largest or smallest cell" in out.warnings[0]["detail"]
    assert check_spec_version(spec(charts=[by_value]), "6.1.0").warnings == []
    by_label = {**HEATMAP, "x_order": "z_to_a", "y_order": "a_to_z"}
    assert check_spec_version(spec(charts=[by_label]), "4.1.4").warnings == []


def test_rolling_and_mixed_areas_need_no_release():
    """rolling_type/rolling_periods/min_periods (BigNumberWithTrendline) and area/opacity
    per query (MixedTimeseries) are in every supported release's control panel."""
    trend = {**TREND, "time_grain": "P1M", "rolling_type": "sum", "rolling_periods": 12,
             "compare_lag": 12}
    area = {**MIXED, "a": {**MIXED["a"], "kind": "area", "opacity": 1}}
    out = check_spec_version(spec(charts=[trend, area]), "4.1.4")
    assert out.ok and out.warnings == []


def test_only_total_warns_only_where_older_releases_differ():
    # only_total false asks for every segment, which is all an older release draws.
    every = {**MIXED, "a": {**MIXED["a"], "show_value": True, "stack": True, "only_total": False}}
    unstacked = {**MIXED, "a": {**MIXED["a"], "show_value": True}}
    for chart in (every, unstacked):
        assert check_spec_version(spec(charts=[chart]), "5.0.0").warnings == []


# -- check, apply and plan against an instance ---------------------------------------------


def test_check_asks_the_instance_only_when_a_gated_field_is_used():
    client = FakeClient("4.1.4")
    res = check(spec(), client)
    assert res.ok and client.version_asked == 0 and res.superset_version is None
    res = check(spec({"tags": ["finance"]}), client)
    assert codes([e.as_dict() for e in res.errors]) == [("superset_version_too_old", None, "tags")]
    assert client.version_asked == 1 and res.superset_version == "4.1.4"


def test_a_stated_version_is_used_instead_of_asking():
    client = FakeClient(None)
    res = check(spec({"show_chart_timestamps": True}), client, "6.1.0")
    assert res.ok and client.version_asked == 0 and res.superset_version == "6.1.0"
    res = check(spec({"show_chart_timestamps": True}), client)
    assert [e.code for e in res.errors] == ["superset_version_unknown"]


def test_check_collects_version_errors_with_the_rest():
    bad_column = {**LINE, "time_column": "shipped_at"}
    res = check(spec({"tags": ["x"]}, [bad_column]), FakeClient("5.0.0"))
    assert sorted(e.code for e in res.errors) == ["column_not_found", "superset_version_too_old"]


def test_apply_stops_at_resolve_before_writing():
    # FakeClient fails the test on any call past metadata reads (ownership, import, ...).
    report = apply(spec({"show_chart_timestamps": True}), FakeClient("5.0.0"))
    assert report.ok is False and report.stage == "resolve"
    assert report.superset_version == "5.0.0"
    assert codes(report.resolution_errors) == [
        ("superset_version_too_old", None, "show_chart_timestamps")]


def test_apply_reports_ignored_fields():
    report = apply(_ignored_fields_spec(), FakeClient("5.0.0"), superset_version="5.0.0")
    payload = json.loads(report.to_json())
    assert len(payload["version_warnings"]) == 4 and payload["superset_version"] == "5.0.0"


def test_plan_is_blocked_by_a_field_the_instance_cannot_take():
    owned = {"id": 7, "uuid": str(ids.dashboard_uuid("orders"))}
    p = plan(spec({"tags": ["finance"]}), FakeClient("5.0.0", dashboard=owned))
    assert p.dashboard == "blocked"
    assert codes(p.resolution_errors) == [("superset_version_too_old", None, "tags")]


def test_the_cli_takes_a_stated_version(monkeypatch, tmp_path, capsys):
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec_data({"show_chart_timestamps": True})), encoding="utf-8")
    client = FakeClient("6.1.0")
    monkeypatch.setattr(cli, "_client", lambda profile: client)
    with pytest.raises(SystemExit) as e:
        cli.main(["check", str(path), "--profile", "p", "--design", "off", "--superset-version", "5.0"])
    out = json.loads(capsys.readouterr().out)
    assert e.value.code == 1 and out["superset_version"] == "5.0.0"
    assert out["errors"][0]["code"] == "superset_version_too_old" and client.version_asked == 0
    with pytest.raises(SystemExit) as e:
        cli.main(["check", str(path), "--profile", "p", "--design", "off"])
    out = json.loads(capsys.readouterr().out)
    assert e.value.code == 0 and out["superset_version"] == "6.1.0" and client.version_asked == 1


# -- offline compile -------------------------------------------------------------------------


def test_compile_stays_version_agnostic_and_checks_a_stated_version(tmp_path, capsys):
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec_data({"show_chart_timestamps": True})), encoding="utf-8")
    plain, checked = tmp_path / "plain.zip", tmp_path / "checked.zip"
    cli.main(["compile", str(path), "-o", str(plain)])
    assert "superset_version" not in json.loads(capsys.readouterr().out)
    cli.main(["compile", str(path), "-o", str(checked), "--superset-version", "6.1.0"])
    assert json.loads(capsys.readouterr().out)["superset_version"] == "6.1.0"
    assert plain.read_bytes() == checked.read_bytes()  # the check never changes the bundle

    with pytest.raises(SystemExit) as e:
        cli.main(["compile", str(path), "-o", str(tmp_path / "old.zip"), "--superset-version", "4.1.4"])
    out = json.loads(capsys.readouterr().out)
    assert e.value.code == 1 and out["stage"] == "version" and not (tmp_path / "old.zip").exists()
    assert out["errors"][0]["code"] == "superset_version_too_old"

    with pytest.raises(SystemExit) as e:
        cli.main(["compile", str(path), "--superset-version", "latest"])
    assert e.value.code == 2  # argparse: not a release


def test_compile_reports_ignored_fields(tmp_path, capsys):
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec_data(charts=[{**LINE, "x_label_every": True}])), encoding="utf-8")
    cli.main(["compile", str(path), "-o", str(tmp_path / "b.zip"), "--superset-version", "5.0.0"])
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and [w["field"] for w in out["version_warnings"]] == ["x_label_every"]
    assert zipfile.is_zipfile(io.BytesIO((tmp_path / "b.zip").read_bytes()))


# -- detecting the version -------------------------------------------------------------------


class _Response:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.text = body if isinstance(body, str) else json.dumps(body)
        self.headers = {}

    def json(self):
        if isinstance(self._body, str):
            raise ValueError("not JSON")
        return self._body


def _serve(monkeypatch, pages):
    """requests.Session.get answers from `pages` (path -> (status, body)); returns the log."""
    seen = []

    def get(self, url, headers=None, timeout=None, **_):
        path = url.split("http://superset", 1)[1]
        seen.append((path, dict(self.headers), dict(headers or {})))
        status, body = pages.get(path, (404, "<html>Not Found</html>"))
        return _Response(status, body)

    monkeypatch.setattr(requests.Session, "get", get)
    return seen


def _bootstrap_page(version):
    # Jinja escapes the bootstrap JSON into the attribute (appbuilder/baselayout.html:45).
    data = json.dumps({"common": {"menu_data": {"navbar_right": {"version_string": version}}}})
    return f'<div id="app" data-bootstrap="{data.replace(chr(34), "&#34;")}"></div>'


def _client():
    c = SupersetClient("http://superset", "u", "p")
    c.session.headers["Authorization"] = "Bearer t"  # as after login
    return c


def test_6_1_reports_its_version_at_slash_version(monkeypatch):
    seen = _serve(monkeypatch, {"/version": (200, {"version_string": "6.1.0", "version_sha": "abc"})})
    client = _client()
    assert client.superset_version() == "6.1.0"
    assert client.superset_version() == "6.1.0" and len(seen) == 1  # asked once
    assert "Authorization" not in seen[0][1]  # the probe goes out signed out


@pytest.mark.parametrize("version", ["4.1.4", "5.0.0"])
def test_older_releases_report_it_in_the_sign_in_page(monkeypatch, version):
    seen = _serve(monkeypatch, {"/login/": (200, _bootstrap_page(version))})
    assert _client().superset_version() == version
    assert [path for path, _, _ in seen] == ["/version", "/login/"]


def test_no_version_anywhere_is_unknown(monkeypatch):
    _serve(monkeypatch, {"/login/": (200, "<html>sign in</html>")})
    assert _client().superset_version() is None
    _serve(monkeypatch, {"/version": (200, {"version_string": "0.0.0-dev"}),
                         "/login/": (200, _bootstrap_page("0.0.0-dev"))})
    assert _client().superset_version() is None


def test_a_failing_probe_is_unknown_not_an_error(monkeypatch):
    def down(self, url, **_):
        raise requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr(requests.Session, "get", down)
    assert _client().superset_version() is None


# -- MCP --------------------------------------------------------------------------------------


def test_mcp_tools_take_a_stated_version(monkeypatch):
    pytest.importorskip("mcp")
    import chartwright.mcp_server as server
    from test_mcp_server import _call

    client = FakeClient(None)
    monkeypatch.setattr(server, "_client", lambda profile: client)
    spec_json = json.dumps(spec_data({"tags": ["finance"]}))
    out = _call("check_spec", {"spec_json": spec_json, "superset_version": "5.0.0"}, "p")
    assert out["ok"] is False and out["errors"][0]["code"] == "superset_version_too_old"
    out = _call("build_dashboard", {"spec_json": spec_json}, "p")
    assert out["stage"] == "resolve" and out["resolution_errors"][0]["code"] == "superset_version_unknown"
    out = _call("plan_dashboard", {"spec_json": spec_json, "superset_version": "nope"}, "p")
    assert out["errors"][0]["code"] == "bad_superset_version"
