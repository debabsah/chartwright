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
        "build_dashboard",
        "check_spec",
        "decompile_dashboard",
        "get_spec_schema",
        "plan_dashboard",
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
