"""Colour-rule and table options only some Superset releases read, and the colour-rule
operators every release has.

4.1.4, 5.0.0 and 6.0.0 read none of the rule keys 6.1.0 added: apply_to
(columnFormatting) paints the rule's own cells there, text paint (objectFormatting)
fills the cell instead, a band fades by its distance from the threshold (useGradient),
and a dark fill keeps the dark cell text (6.1.0 turns it white). A hidden column shows
before 6.0.0. Each is a version warning in check, apply, plan and the MCP tools, and
never blocks. >=, <=, != and an inclusive range are comparators in every release, so
the spec takes them and decompile reads them back."""

import io
import json
import zipfile

import pytest
from pydantic import ValidationError

import chartwright.cli as cli
import chartwright.dashdiff as dashdiff
from chartwright import ids
from chartwright.apply import apply, check
from chartwright.compiler import compile_bundle
from chartwright.dashdiff import _normalize, plan
from chartwright.decompile import _format_to_spec, decompile_bundle
from chartwright.resolver import resolve
from chartwright.spec import load_spec
from chartwright.testing import edit_bundle, stub_resolution
from chartwright.versions import check_spec_version

from test_dashboard_settings import DS, lookup_for, spec_data
from test_design_rules_v2 import mk as design_mk
from test_design_rules_v2 import pivot as design_pivot
from test_design_rules_v2 import run as design_run
from test_superset_version import FakeClient
from test_table_sort import _lookup, aggregate, mk, params_for

TABLE = {"name": "Table", "type": "table", "dataset": DS, "groupby": ["region"],
         "metrics": ["SUM(amount) AS Revenue", "MAX(active) AS status"]}
PIVOT = {"name": "Pivot", "type": "pivot_table", "dataset": DS, "rows": ["region"],
         "metrics": ["SUM(amount) AS Revenue"]}


def rule(**over):
    return {"metric": "Revenue", "operator": "=", "target": 1, "color": "green"} | over


def spec(*charts):
    return load_spec(spec_data(charts=list(charts)))


# -- operators ------------------------------------------------------------------------


EVERY_OPERATOR = [
    {"metric": "Val", "operator": ">=", "target": 80, "color": "green"},
    {"metric": "Val", "operator": "<=", "target": 20, "color": "red"},
    {"metric": "Val", "operator": "!=", "target": 0, "color": "#0057B8", "paint": "text"},
    {"metric": "Val", "operator": "between_inclusive", "target_left": 20, "target_right": 80,
     "color": "amber"},
    {"metric": "Val", "operator": "between", "target_left": 20, "target_right": 80,
     "color": "amber"},
]


def test_each_operator_compiles_to_the_comparator_every_release_has():
    p = params_for(aggregate(metrics=["SUM(v) AS Val"], conditional_formatting=EVERY_OPERATOR))
    rules = p["conditional_formatting"]
    assert [r["operator"] for r in rules] == ["≥", "≤", "≠", "≤ x ≤", "< x <"]
    assert rules[0]["targetValue"] == 80.0 and "targetValueLeft" not in rules[0]
    assert (rules[3]["targetValueLeft"], rules[3]["targetValueRight"]) == (20.0, 80.0)
    assert "targetValue" not in rules[3] and all(r["useGradient"] is False for r in rules)


def test_operator_targets_are_checked():
    with pytest.raises(ValidationError, match="'between_inclusive' needs target_left"):
        mk(aggregate(metrics=["SUM(v) AS Val"], conditional_formatting=[
            {"metric": "Val", "operator": "between_inclusive", "target": 1, "color": "red"}]))
    with pytest.raises(ValidationError, match="operator '!=' needs target"):
        mk(aggregate(metrics=["SUM(v) AS Val"], conditional_formatting=[
            {"metric": "Val", "operator": "!=", "target_left": 1, "target_right": 2,
             "color": "red"}]))
    with pytest.raises(ValidationError, match="operator"):
        mk(aggregate(metrics=["SUM(v) AS Val"], conditional_formatting=[
            {"metric": "Val", "operator": "≥", "target": 1, "color": "red"}]))


@pytest.mark.parametrize("chart", [
    aggregate(metrics=["SUM(v) AS Val"], conditional_formatting=EVERY_OPERATOR),
    {"name": "P", "type": "pivot_table", "dataset": {"database": "db", "table": "orders"},
     "rows": ["region"], "metrics": ["SUM(v) AS Val"], "conditional_formatting": EVERY_OPERATOR},
])
def test_every_operator_round_trips_with_no_drift(chart):
    s = mk(chart)
    result = decompile_bundle(compile_bundle(s, stub_resolution(s)), _lookup(s))
    assert result.losses == [], result.losses_json()
    assert [r["operator"] for r in result.spec["charts"][0]["conditional_formatting"]] == [
        ">=", "<=", "!=", "between_inclusive", "between"]
    assert _normalize(load_spec(result.spec))["charts"] == _normalize(s)["charts"]


def test_decompile_reads_the_rules_the_ui_makes():
    base = {"column": "Val", "colorScheme": "#ACE1C4"}
    for op, back in (("≥", ">="), ("≤", "<="), ("≠", "!=")):
        assert _format_to_spec({**base, "operator": op, "targetValue": 5}) == \
            {"metric": "Val", "operator": back, "color": "green", "target": 5}
    assert _format_to_spec({**base, "operator": "≤ x ≤", "targetValueLeft": 1,
                            "targetValueRight": 2})["operator"] == "between_inclusive"
    for half_open in ("≤ x <", "< x ≤"):  # no spec operator: a named loss, as before
        assert _format_to_spec({**base, "operator": half_open, "targetValueLeft": 1,
                                "targetValueRight": 2}) is None


def test_a_gradient_made_in_the_ui_keeps_the_rule_and_is_named():
    """6.1's popover turns the gradient on for a new rule; the spec's rule is a solid
    band, so decompile keeps the rule and says apply paints it solid."""
    s = mk(aggregate(metrics=["SUM(v) AS Val"], conditional_formatting=[
        {"metric": "Val", "operator": ">", "target": 5, "color": "green"},
        {"metric": "Val", "operator": "=", "target": 1, "color": "red"}]))

    def gradient(path, doc):
        if "/charts/" in path:
            for cf in doc["params"]["conditional_formatting"]:
                cf["useGradient"] = True

    result = decompile_bundle(edit_bundle(compile_bundle(s, stub_resolution(s)), gradient), _lookup(s))
    assert len(result.spec["charts"][0]["conditional_formatting"]) == 2
    # an '=' rule is solid either way, so only the '>' rule is a loss
    assert [l.what for l in result.losses] == [
        "conditional format on 'Val' fades by distance from its threshold (gradient), not "
        "preserved: apply paints it one solid colour"]


def test_plan_reads_a_named_shade_written_as_its_hex_as_the_name():
    """Both compile to the same rule and decompile reads the name back, so plan must
    not report the chart changed after every apply."""
    def chart(colour, paint="cell"):
        return mk(aggregate(metrics=["SUM(v) AS Val"], conditional_formatting=[
            {"metric": "Val", "operator": ">", "target": 1, "color": colour, "paint": paint}]))

    assert _normalize(chart("#ACE1C4"))["charts"] == _normalize(chart("green"))["charts"]
    assert _normalize(chart("#1B7F3B", "text"))["charts"] == _normalize(chart("green", "text"))["charts"]
    # the cell shade painting text is another colour: written as the hex, read back as it
    assert _normalize(chart("#ACE1C4", "text"))["charts"] != _normalize(chart("green", "text"))["charts"]


# -- version gating -----------------------------------------------------------------------


def gated_spec():
    table = {**TABLE, "hidden": ["status"], "conditional_formatting": [
        rule(metric="status", target=1, apply_to="region"),        # apply_to
        rule(operator="<", target=10, color="red", paint="text"),   # paint, and a fade
        rule(operator=">", target=100, color="#0057B8"),            # a dark fill, and a fade
    ]}
    pivot = {**PIVOT, "conditional_formatting": [
        rule(operator=">=", target=100), rule(operator="<=", target=10, color="red", paint="text")]}
    return spec(table, pivot)


ALL = [("apply_to", "Table"), ("color", "Table"), ("conditional_formatting", "Pivot"),
       ("conditional_formatting", "Table"), ("hidden", "Table"), ("paint", "Pivot"),
       ("paint", "Table")]


def fields(out):
    return sorted((w["field"], w["chart"]) for w in out.warnings)


@pytest.mark.parametrize("version, expected", [
    ("4.1.4", ALL),
    ("5.0.0", ALL),
    ("6.0.0", [w for w in ALL if w[0] != "hidden"]),  # 6.0.0 hides a column
    ("6.1.0", []),
    (None, ALL),                                      # unknown: warns, never refuses
])
def test_older_releases_warn_for_each_rule_option_they_ignore(version, expected):
    out = check_spec_version(gated_spec(), version)
    assert out.ok and out.errors == []
    assert fields(out) == expected
    assert all(w["code"] == "field_ignored_before_version" for w in out.warnings)
    assert {w["field"]: w["since"] for w in out.warnings} == {
        f: "6.0.0" if f == "hidden" else "6.1.0" for f, _ in expected}


def test_each_warning_says_what_the_older_release_draws():
    detail = {(w["field"], w["chart"]): w["detail"]
              for w in check_spec_version(gated_spec(), "4.1.4").warnings}
    assert "a release before 6.1.0 paints the rule's own metric cells instead" in detail[
        ("apply_to", "Table")]
    assert "this release fills the cell with that colour instead" in detail[("paint", "Pivot")]
    assert "paint: cell reads on every release" in detail[("paint", "Table")]
    assert "this release fades a colour by the value's distance from its threshold" in detail[
        ("conditional_formatting", "Table")]
    assert "under 3:1 contrast on that fill" in detail[("color", "Table")]
    assert "the table shows the column" in detail[("hidden", "Table")]
    assert all("this instance runs 4.1.4" in d for d in detail.values())


def test_rules_every_release_draws_alike_raise_nothing():
    same = spec(
        # '=' rules are solid everywhere; a rule painting its own metric needs no apply_to;
        # the named cell shades and a light hex read under every release's cell text.
        {**TABLE, "conditional_formatting": [
            rule(), rule(target=2, color="amber", apply_to="Revenue"), rule(target=3, color="red"),
            rule(target=4, color="#D9EAF7")]},
        {**PIVOT, "conditional_formatting": [rule(color="red"), rule(target=2, color="green")]},
    )
    assert check_spec_version(same, "4.1.4").warnings == []


def test_a_dark_fill_warns_by_contrast_with_the_release_cell_text():
    """The table's text is near-black and the pivot's a dark teal (Styles.js:105), so the
    pivot's line sits higher; text paint is the paint warning's, not this one's."""
    def colour(chart, hex_, paint="cell"):
        out = check_spec_version(spec({**chart, "conditional_formatting": [
            rule(operator="=", color=hex_, paint=paint)]}), "5.0.0")
        return [w["field"] for w in out.warnings]

    assert colour(TABLE, "#0057B8") == ["color"]        # 2.8:1 under the table's text
    assert colour(TABLE, "#5B9BD5") == []               # 6.1:1
    assert colour(PIVOT, "#5B9BD5") == ["color"]        # 2.3:1 under the pivot's teal
    assert colour(PIVOT, "#0057B8", "text") == ["paint"]


def test_check_apply_plan_and_compile_report_the_warnings(monkeypatch, tmp_path, capsys):
    s = gated_spec()
    res = check(s, FakeClient("4.1.4"))
    assert res.ok and res.superset_version == "4.1.4"
    assert sorted((w["field"], w["chart"]) for w in res.version_warnings) == ALL

    payload = json.loads(apply(s, FakeClient("5.0.0"), superset_version="5.0.0").to_json())
    assert sorted((w["field"], w["chart"]) for w in payload["version_warnings"]) == ALL

    # plan against a dashboard that matches the spec: clean, and still warned
    client = FakeClient("5.0.0", dashboard={"id": 7, "uuid": str(ids.dashboard_uuid("orders"))})
    resolution = resolve(s, client)
    live = decompile_bundle(compile_bundle(s, resolution), lookup_for(resolution))
    monkeypatch.setattr(dashdiff, "decompile_live", lambda slug, c: live)
    p = json.loads(plan(s, client).to_json())
    assert p["clean"] is True, p
    assert sorted((w["field"], w["chart"]) for w in p["version_warnings"]) == ALL

    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec_data(charts=[{**TABLE, "hidden": ["status"]}])), encoding="utf-8")
    cli.main(["compile", str(path), "-o", str(tmp_path / "b.zip"), "--superset-version", "5.0.0"])
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and [w["field"] for w in out["version_warnings"]] == ["hidden"]
    assert zipfile.is_zipfile(io.BytesIO((tmp_path / "b.zip").read_bytes()))


def test_the_mcp_check_reports_the_warnings(monkeypatch):
    pytest.importorskip("mcp")
    import chartwright.mcp_server as server
    from test_mcp_server import _call

    monkeypatch.setattr(server, "_client", lambda profile: FakeClient("4.1.4"))
    spec_json = json.dumps(spec_data(charts=[{**TABLE, "conditional_formatting": [
        rule(operator="<", target=10, color="red", paint="text")]}]))
    out = _call("check_spec", {"spec_json": spec_json, "design": "off"}, "p")
    assert out["ok"] is True and out["superset_version"] == "4.1.4"
    assert sorted(w["field"] for w in out["version_warnings"]) == ["conditional_formatting", "paint"]


# -- the design brain reads each operator's range ------------------------------------------


@pytest.mark.parametrize("rules, overlap", [
    # '=' is one value: two status codes never overlap (it read as everything above)
    ([("=", 1, "green"), ("=", 2, "red")], False),
    ([("=", 1, "green"), (">", 2, "red")], False),
    # a bound both rules take in is shared; one that only one takes in is not
    ([(">=", 80, "green"), ("between_inclusive", (50, 80), "amber")], True),
    ([(">", 80, "green"), ("between_inclusive", (50, 80), "amber")], False),
    ([(">=", 80, "green"), ("between", (50, 80), "amber")], False),
    ([("<=", 50, "red"), (">=", 50, "green")], True),
    ([("<", 50, "red"), (">=", 50, "green")], False),
    # '!=' is everything but its value
    ([("!=", 0, "red"), ("=", 0, "green")], False),
    ([("!=", 0, "red"), (">", 5, "green")], True),
])
def test_format_bands_reads_each_operators_range(rules, overlap):
    def as_rule(op, target, colour):
        bounds = ({"target_left": target[0], "target_right": target[1]}
                  if isinstance(target, tuple) else {"target": target})
        return {"metric": "Val", "operator": op, "color": colour, **bounds}

    p = design_pivot("P", row_limit=20, height=10, metrics=["SUM(v) AS Val"],
                     conditional_formatting=[as_rule(*r) for r in rules])
    found = [f for f in design_run(design_mk([p])).findings
             if f.rule == "chart.format-bands" and f.severity == "warn"]
    assert bool(found) == overlap, [f.detail for f in found]
