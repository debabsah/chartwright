"""Strict gates are immune to the per-machine design.yaml.

`~/.config/chartwright/design.yaml` (or `$CHARTWRIGHT_DESIGN_DIR/design.yaml`) can
disable rules, lower severities and move thresholds. Under `advise --strict` and
`check`/`apply --design strict` that used to loosen the gate on one machine with no
trace in the payload, so a local run could pass where CI fails. A strict gate now
takes only severity raises from the overlay; everything else is set aside and
reported. Outside a strict gate the overlay works as before, and every advice payload
names the overlay and what it changed.
"""

import json

import pytest

from chartwright.cli import _advice_payload, _design_blocks, main
from chartwright.design import advise, advise_and_fix
from chartwright.design.presets import Overlay
from chartwright.spec import load_spec

pytest.importorskip("yaml")

DS = {"database": "db", "table": "orders"}

# One warn finding (size.axis-min-height: 4 units < analytical's 6) and one info fill.
DATA = {
    "spec_version": "1",
    "dashboard": {"title": "T", "slug": "t"},
    "charts": [{"type": "timeseries_line", "name": "L", "dataset": DS,
                "metrics": ["COUNT(*)"], "time_column": "ts", "height": 4}],
    "filters": [{"type": "time_range", "name": "Date", "default": "Last month"}],
    "layout": {"rows": [["L"]]},
}
SPEC = load_spec(DATA)
RULE = "size.axis-min-height"


@pytest.fixture
def design_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARTWRIGHT_DESIGN_DIR", str(tmp_path))
    return tmp_path


def write_overlay(design_dir, text: str):
    (design_dir / "design.yaml").write_text(text, encoding="utf-8")
    return design_dir / "design.yaml"


def run_advise(tmp_path, capsys, *flags) -> tuple[int, dict]:
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(DATA), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        main(["advise", str(spec_path), *flags])
    return exc.value.code, json.loads(capsys.readouterr().out)


LOOSENERS = {
    "disable": f"disable: [{RULE}]\n",
    "severity": f"severity: {{{RULE}: info}}\n",
    "params": "params: {min_axis_height: 4}\n",
}


@pytest.mark.parametrize("kind", sorted(LOOSENERS))
def test_advise_strict_ignores_an_overlay_that_loosens_it(kind, design_dir, tmp_path, capsys):
    path = write_overlay(design_dir, LOOSENERS[kind])
    code, payload = run_advise(tmp_path, capsys, "--strict")
    assert code == 1
    assert [f["rule"] for f in payload["findings"] if f["severity"] == "warn"] == [RULE]
    assert payload["errors"][0]["code"] == "design_gate"
    overlay = payload["overlay"]
    assert overlay["path"] == str(path) and overlay["strict"] is True
    assert overlay["changed"] == []
    expected = {"disable": [RULE]} if kind == "disable" else (
        {"severity": {RULE: "info"}} if kind == "severity" else {"params": ["min_axis_height"]})
    assert overlay["set_aside"] == expected


@pytest.mark.parametrize("kind", sorted(LOOSENERS))
def test_advise_without_strict_keeps_the_overlay_and_says_what_it_changed(
        kind, design_dir, tmp_path, capsys):
    path = write_overlay(design_dir, LOOSENERS[kind])
    code, payload = run_advise(tmp_path, capsys)
    assert code == 0
    assert not [f for f in payload["findings"] if f["severity"] == "warn"]
    overlay = payload["overlay"]
    assert overlay["path"] == str(path) and overlay["strict"] is False
    assert "set_aside" not in overlay
    if kind == "disable":
        assert overlay["disable"] == [RULE]
        assert overlay["changed"] == [{"finding": f"{RULE}@L", "disabled": True}]
        assert f"{RULE}@L" in payload["ignored"]
    elif kind == "severity":
        assert overlay["severity"] == {RULE: "info"}
        assert overlay["changed"] == [{"finding": f"{RULE}@L", "severity": ["warn", "info"]}]
    else:
        assert overlay["params"] == ["min_axis_height"]
        assert overlay["changed"] == []


def test_a_strict_gate_takes_a_raised_severity(design_dir, tmp_path, capsys):
    write_overlay(design_dir, "severity: {default.count-format: warn}\n")
    code, payload = run_advise(tmp_path, capsys, "--strict")
    assert code == 1
    assert {f["rule"]: f["severity"] for f in payload["findings"]}["default.count-format"] == "warn"
    assert payload["overlay"]["severity"] == {"default.count-format": "warn"}
    assert payload["overlay"]["changed"] == [
        {"finding": "default.count-format@L", "severity": ["info", "warn"]}]
    assert "set_aside" not in payload["overlay"]


@pytest.mark.parametrize("kind", sorted(LOOSENERS))
def test_check_and_apply_design_strict_ignore_a_loosening_overlay(kind, design_dir):
    write_overlay(design_dir, LOOSENERS[kind])
    strict = _advice_payload(SPEC, strict=True)
    assert _design_blocks(strict) is not None
    assert strict["overlay"]["set_aside"]
    # --design warn reads the overlay as before, and says so.
    warn = _advice_payload(SPEC)
    assert _design_blocks(warn) is None
    assert warn["overlay"]["strict"] is False


def test_no_overlay_no_overlay_block(design_dir, tmp_path, capsys):
    code, payload = run_advise(tmp_path, capsys, "--strict")
    assert code == 1 and "overlay" not in payload
    assert "overlay" not in advise(SPEC, overlay=Overlay()).payload()


def test_strict_fix_targets_the_built_in_presets(design_dir):
    write_overlay(design_dir, "params: {min_axis_height: 4}\n")
    _, loose = advise_and_fix(json.loads(json.dumps(DATA)))
    assert not [f for f in loose.fixed if f["rule"] == RULE]
    fixed, strict = advise_and_fix(json.loads(json.dumps(DATA)), strict=True)
    assert [f["set"] for f in strict.fixed if f["rule"] == RULE] == [{"height": 6}]
    assert strict.overlay["set_aside"] == {"params": ["min_axis_height"]}


def test_an_overlay_disable_typo_is_unmatched_only_where_it_applies(design_dir):
    write_overlay(design_dir, "disable: [size.no-such-rule]\n")
    assert advise(SPEC).unmatched_ignores == ["size.no-such-rule"]
    assert advise(SPEC, strict=True).unmatched_ignores == []


# -- MCP: the same gate and the same overlay block -----------------------------------


def _mcp(name: str, args: dict) -> dict:
    pytest.importorskip("mcp")
    import asyncio

    from chartwright.mcp_server import mcp

    out = asyncio.run(mcp.call_tool(name, args))
    if hasattr(out, "content"):
        text = out.content[0].text
    else:
        text = out[0][0].text if isinstance(out, tuple) else out[0].text
    return json.loads(text)


def test_mcp_advise_spec_strict_matches_the_cli(design_dir, tmp_path, capsys):
    write_overlay(design_dir, LOOSENERS["disable"])
    payload = _mcp("advise_spec", {"spec_json": json.dumps(DATA), "strict": True})
    _, cli = run_advise(tmp_path, capsys, "--strict")
    assert payload == cli
    loose = _mcp("advise_spec", {"spec_json": json.dumps(DATA)})
    assert loose["overlay"]["changed"] == [{"finding": f"{RULE}@L", "disabled": True}]


def test_mcp_fix_spec_reports_the_overlay(design_dir):
    write_overlay(design_dir, "params: {min_axis_height: 4}\n")
    loose = _mcp("fix_spec", {"spec_json": json.dumps(DATA)})
    assert loose["advice"]["overlay"]["params"] == ["min_axis_height"]
    strict = _mcp("fix_spec", {"spec_json": json.dumps(DATA), "strict": True})
    assert strict["spec"]["charts"][0]["height"] == 6
    assert strict["advice"]["overlay"]["set_aside"] == {"params": ["min_axis_height"]}


def test_mcp_build_dashboard_design_strict_blocks_before_signing_in(design_dir, monkeypatch):
    import chartwright.mcp_server as server

    def no_sign_in(profile):
        raise AssertionError("a blocked build must not sign in")

    monkeypatch.setattr(server, "_client", no_sign_in)
    write_overlay(design_dir, LOOSENERS["severity"])
    out = _mcp("build_dashboard", {"spec_json": json.dumps(DATA), "profile": "dev",
                                   "design": "strict"})
    assert out["ok"] is False and out["stage"] == "design"
    assert out["errors"][0]["code"] == "design_gate"
    assert out["advice"]["overlay"]["set_aside"] == {"severity": {RULE: "info"}}


def test_mcp_check_and_build_take_the_clis_design_modes():
    out = _mcp("check_spec", {"spec_json": json.dumps(DATA), "profile": "dev", "design": "loud"})
    assert out["errors"][0]["code"] == "design"
    out = _mcp("build_dashboard", {"spec_json": json.dumps(DATA), "profile": "dev",
                                   "design": "loud"})
    assert out["errors"][0]["code"] == "design"
