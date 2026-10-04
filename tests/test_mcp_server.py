"""The MCP server must stay a 1:1 mirror of the core; these tests pin the
tool surface and exercise the offline tools through the real MCP server layer."""

import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from chartwright.mcp_server import mcp  # noqa: E402

FIXTURE = (Path(__file__).parent / "fixtures" / "sales_overview.json").read_text()


def _run(coro):
    return asyncio.run(coro)


def test_tool_surface():
    tools = _run(mcp.list_tools())
    names = sorted(t.name for t in tools)
    assert names == [
        "advise_spec",
        "build_dashboard",
        "check_spec",
        "decompile_dashboard",
        "design_brief",
        "explain_spec",
        "fix_spec",
        "get_spec_schema",
        "plan_dashboard",
        "redesign_dashboard",
        "standards_apply",
        "standards_check",
        "standards_show",
        "validate_spec",
    ]


def _text(out):
    """The tool's text, whichever shape call_tool returns: mcp 2 a CallToolResult,
    mcp 1 a content list, or (with structured output) a (content, structured) pair."""
    if hasattr(out, "content"):
        return out.content[0].text
    return out[0][0].text if isinstance(out, tuple) else out[0].text


def test_validate_spec_ok():
    out = _run(mcp.call_tool("validate_spec", {"spec_json": FIXTURE}))
    payload = json.loads(_text(out))
    assert payload["ok"] is True


def test_validate_spec_schema_error():
    bad = json.loads(FIXTURE)
    bad["charts"][0]["type"] = "sunburst"
    out = _run(mcp.call_tool("validate_spec", {"spec_json": json.dumps(bad)}))
    payload = json.loads(_text(out))
    assert payload["ok"] is False
    assert payload["stage"] == "schema"


def test_get_spec_schema_matches_generator():
    from chartwright.spec import json_schema

    out = _run(mcp.call_tool("get_spec_schema", {}))
    payload = json.loads(_text(out))
    assert payload == json_schema()


def test_design_brief_offline():
    out = _run(mcp.call_tool("design_brief", {"audience": "executive"}))
    assert "# Design brief: executive" in _text(out)
    out = _run(mcp.call_tool("design_brief", {"audience": "board"}))
    payload = json.loads(_text(out))
    assert payload["ok"] is False and payload["errors"][0]["code"] == "audience"


def test_advise_spec_offline(monkeypatch, tmp_path):
    monkeypatch.setenv("CHARTWRIGHT_DESIGN_DIR", str(tmp_path))  # no user overlay
    out = _run(mcp.call_tool("advise_spec", {"spec_json": FIXTURE}))
    payload = json.loads(_text(out))
    assert payload["stage"] == "design" and "findings" in payload


def test_advise_spec_narrows_to_one_chart(monkeypatch, tmp_path):
    """The CLI's advise --chart, for agents."""
    monkeypatch.setenv("CHARTWRIGHT_DESIGN_DIR", str(tmp_path))
    name = json.loads(FIXTURE)["charts"][0]["name"]
    out = _run(mcp.call_tool("advise_spec", {"spec_json": FIXTURE, "chart": name}))
    payload = json.loads(_text(out))
    assert payload["findings"] and {f["chart"] for f in payload["findings"]} == {name}
    out = _run(mcp.call_tool("advise_spec", {"spec_json": FIXTURE, "chart": "Nope"}))
    assert json.loads(_text(out))["errors"][0]["code"] == "unknown_chart"


def test_explain_spec_returns_the_cli_json(monkeypatch, tmp_path):
    monkeypatch.setenv("CHARTWRIGHT_DESIGN_DIR", str(tmp_path))
    from chartwright.design.explain import explain
    from chartwright.spec import load_spec

    out = _run(mcp.call_tool("explain_spec", {"spec_json": FIXTURE}))
    assert json.loads(_text(out)) == explain(load_spec(json.loads(FIXTURE)))
    name = json.loads(FIXTURE)["charts"][0]["name"]
    one = json.loads(_text(_run(mcp.call_tool("explain_spec",
                                              {"spec_json": FIXTURE, "chart": name}))))
    assert [c["chart"] for c in one["charts"]] == [name]
    bad = json.loads(_text(_run(mcp.call_tool("explain_spec",
                                              {"spec_json": FIXTURE, "chart": "Nope"}))))
    assert bad["errors"][0]["code"] == "unknown_chart"


def test_fix_spec_offline(monkeypatch, tmp_path):
    monkeypatch.setenv("CHARTWRIGHT_DESIGN_DIR", str(tmp_path))
    from chartwright.spec import load_spec

    bad = json.loads(FIXTURE)
    for c in bad["charts"]:
        if c["type"].startswith("timeseries"):
            c["height"] = 3
    out = _run(mcp.call_tool("fix_spec", {"spec_json": json.dumps(bad)}))
    payload = json.loads(_text(out))
    assert payload["ok"] is True and payload["advice"]["fixed"]
    load_spec(payload["spec"])  # fixed specs always re-validate


# Tools that sign in return the CLI's typed error JSON, never a raised
# exception (which reached the agent as a bare tool failure).

SIGNING_IN = [
    ("check_spec", {"spec_json": FIXTURE}),
    ("build_dashboard", {"spec_json": FIXTURE}),
    ("plan_dashboard", {"spec_json": FIXTURE}),
    ("advise_spec", {"spec_json": FIXTURE}),
    ("decompile_dashboard", {"dashboard": "sales-overview"}),
    ("redesign_dashboard", {"dashboard": "sales-overview"}),
]


def _call(name, args, profile):
    return json.loads(_text(_run(mcp.call_tool(name, {**args, "profile": profile}))))


def _input_schema(tool):
    """mcp 1 names it inputSchema; mcp 2 input_schema (inputSchema on the wire)."""
    for attr in ("inputSchema", "input_schema"):
        if hasattr(tool, attr):
            return getattr(tool, attr)
    return tool.model_dump(by_alias=True)["inputSchema"]


def test_signing_in_tools_keep_their_parameters():
    schemas = {t.name: _input_schema(t) for t in _run(mcp.list_tools())}
    for name, args in SIGNING_IN:
        assert set(args) | {"profile"} <= set(schemas[name]["properties"]), name


@pytest.mark.parametrize("name,args", SIGNING_IN)
def test_an_unknown_profile_is_a_typed_profile_error(name, args, monkeypatch, tmp_path):
    profiles = tmp_path / "profiles.toml"
    profiles.write_text('[dev]\nbase_url = "http://x"\nusername = "u"\npassword_env = "PW"\n')
    monkeypatch.setenv("CHARTWRIGHT_PROFILES", str(profiles))
    payload = _call(name, args, "prod")
    assert payload["ok"] is False and payload["stage"] == "profile"
    assert payload["errors"][0]["code"] == "profile"
    assert "dev" in payload["errors"][0]["detail"]          # names kept, as at the CLI


def test_an_unset_password_variable_is_a_typed_profile_error(monkeypatch, tmp_path):
    profiles = tmp_path / "profiles.toml"
    profiles.write_text('[dev]\nbase_url = "http://x"\nusername = "u"\npassword_env = "CW_TEST_UNSET_PW"\n')
    monkeypatch.setenv("CHARTWRIGHT_PROFILES", str(profiles))
    monkeypatch.delenv("CW_TEST_UNSET_PW", raising=False)
    payload = _call("check_spec", {"spec_json": FIXTURE}, "dev")
    assert payload["stage"] == "profile"


@pytest.mark.parametrize("exc,code", [("api", "api"), ("bug", "unexpected")])
def test_superset_and_unexpected_errors_are_typed(exc, code, monkeypatch):
    import chartwright.mcp_server as server
    from chartwright.client import SupersetAPIError

    def broken(profile):
        if exc == "api":
            raise SupersetAPIError("login failed", 401, "")
        raise KeyError("boom")

    monkeypatch.setattr(server, "_client", broken)
    payload = _call("build_dashboard", {"spec_json": FIXTURE}, "dev")
    assert payload["ok"] is False and payload["stage"] == "error"
    assert payload["errors"][0]["code"] == code
