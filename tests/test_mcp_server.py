"""The MCP server must stay a 1:1 mirror of the core; these tests pin the
tool surface and exercise the offline tools through the real FastMCP layer."""

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
        "fix_spec",
        "get_spec_schema",
        "plan_dashboard",
        "redesign_dashboard",
        "validate_spec",
    ]


def test_validate_spec_ok():
    out = _run(mcp.call_tool("validate_spec", {"spec_json": FIXTURE}))
    payload = json.loads(out[0][0].text if isinstance(out, tuple) else out[0].text)
    assert payload["ok"] is True


def test_validate_spec_schema_error():
    bad = json.loads(FIXTURE)
    bad["charts"][0]["type"] = "sunburst"
    out = _run(mcp.call_tool("validate_spec", {"spec_json": json.dumps(bad)}))
    payload = json.loads(out[0][0].text if isinstance(out, tuple) else out[0].text)
    assert payload["ok"] is False
    assert payload["stage"] == "schema"


def test_get_spec_schema_matches_generator():
    from chartwright.spec import json_schema

    out = _run(mcp.call_tool("get_spec_schema", {}))
    payload = json.loads(out[0][0].text if isinstance(out, tuple) else out[0].text)
    assert payload == json_schema()


def _text(out):
    return out[0][0].text if isinstance(out, tuple) else out[0].text


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
