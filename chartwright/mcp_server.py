"""v2: MCP server over the same core, so any MCP client (Claude Desktop/Code,
etc.) can drive the compiler. Tools mirror the CLI verbs 1:1; the server adds
no behavior of its own; the guarantee stays in the core.

Run: chartwright-mcp   (stdio transport; profiles + password env vars as for the CLI)
"""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from .spec import json_schema, load_spec

mcp = FastMCP("chartwright")


def _parse_spec(spec_json: str):
    from pydantic import ValidationError

    try:
        data = json.loads(spec_json)
    except json.JSONDecodeError as e:
        return None, {"ok": False, "stage": "parse", "errors": [{"code": "bad_json", "detail": str(e)}]}
    try:
        return load_spec(data), None
    except ValidationError as e:
        return None, {"ok": False, "stage": "schema", "errors": json.loads(e.json())}


def _client(profile: str):
    from .client import SupersetClient
    from .profiles import load_profile

    p = load_profile(profile)
    c = SupersetClient(p.base_url, p.username, p.password, auth_provider=p.auth_provider,
                       ca_bundle=p.ca_bundle, verify=p.verify)
    c.login()
    return c


@mcp.tool()
def get_spec_schema() -> str:
    """The JSON Schema a dashboard spec must satisfy. Read this before writing a spec."""
    return json.dumps(json_schema())


@mcp.tool()
def validate_spec(spec_json: str) -> str:
    """Schema-validate a dashboard spec (offline). Returns ok or typed errors."""
    _, err = _parse_spec(spec_json)
    return json.dumps(err or {"ok": True, "stage": "schema"})


def _advice(spec, resolution=None, audience: str | None = None) -> dict:
    """Advice riding along MCP responses degrades, never raises (mirrors the CLI)."""
    from .design import advise

    try:
        return advise(spec, audience=audience, resolution=resolution).payload()
    except Exception as e:  # noqa: BLE001
        from .design import DESIGN_BRAIN_VERSION

        return {"stage": "design", "ok": True, "design_brain": DESIGN_BRAIN_VERSION,
                "counts": {"error": 0, "warn": 0, "info": 0}, "findings": [],
                "fixed": [], "ignored": [],
                "errors": [{"code": "overlay" if isinstance(e, ValueError) else "advice",
                            "detail": str(e)}]}


def _bad_audience(audience: str) -> str | None:
    from .design.presets import AUDIENCE_NAMES

    if audience and audience not in AUDIENCE_NAMES:
        return json.dumps({"ok": False, "stage": "design", "errors": [{
            "code": "audience",
            "detail": f"unknown audience {audience!r}; one of {sorted(AUDIENCE_NAMES)}"}]})
    return None


@mcp.tool()
def check_spec(spec_json: str, profile: str) -> str:
    """Pre-flight referential resolution against the live Superset instance:
    every dataset triple, column, and metric must exist. Returns typed errors
    plus a design-brain advice block."""
    spec, err = _parse_spec(spec_json)
    if err:
        return json.dumps(err)
    from .apply import check

    res = check(spec, _client(profile))
    return json.dumps({"ok": res.ok, "stage": "resolve",
                       "errors": [e.as_dict() for e in res.errors],
                       "advice": _advice(spec, resolution=res if res.ok else None)})


@mcp.tool()
def build_dashboard(spec_json: str, profile: str) -> str:
    """Compile the spec and apply it to the live Superset instance
    (resolve -> import -> linkage -> data smoke). Returns the full apply report
    including the dashboard URL."""
    spec, err = _parse_spec(spec_json)
    if err:
        return json.dumps(err)
    from .apply import apply as run_apply

    return run_apply(spec, _client(profile), profile).to_json()


@mcp.tool()
def plan_dashboard(spec_json: str, profile: str) -> str:
    """Diff a spec against the live dashboard at its slug: what would apply
    change? clean=true means no drift."""
    spec, err = _parse_spec(spec_json)
    if err:
        return json.dumps(err)
    from .dashdiff import plan as run_plan

    return run_plan(spec, _client(profile)).to_json()


@mcp.tool()
def design_brief(audience: str = "analytical") -> str:
    """The design brief to read BEFORE authoring a spec: audience budgets,
    chart choice, composition, and what the critic enforces. Audiences:
    executive | analytical | operational."""
    bad = _bad_audience(audience)
    if bad:
        return bad
    from .design.brief import render_brief

    try:
        return render_brief(audience)
    except ValueError as e:
        return json.dumps({"ok": False, "stage": "design", "errors": [{"code": "overlay", "detail": str(e)}]})


@mcp.tool()
def advise_spec(spec_json: str, audience: str = "", profile: str = "") -> str:
    """Design review of a spec against the design-brain rulebook (offline;
    pass a profile for data-aware rules: column types and cardinality).
    Audiences: executive | analytical | operational."""
    bad = _bad_audience(audience)
    if bad:
        return bad
    spec, err = _parse_spec(spec_json)
    if err:
        return json.dumps(err)
    from .design import advise

    resolution = prober = None
    if profile:
        from .design.probe import CardinalityProber
        from .resolver import resolve

        client = _client(profile)
        resolution = resolve(spec, client)
        prober = CardinalityProber(client)
    try:
        report = advise(spec, audience=audience or None, resolution=resolution, prober=prober)
    except ValueError as e:
        return json.dumps({"ok": False, "stage": "design", "errors": [{"code": "overlay", "detail": str(e)}]})
    payload = report.payload()
    if resolution is not None and resolution.errors:
        payload["resolution_errors"] = [e.as_dict() for e in resolution.errors]
    return json.dumps(payload)


@mcp.tool()
def fix_spec(spec_json: str, audience: str = "") -> str:
    """Apply the design brain's safe, presentation-only fixes (heights, bar
    orientation) to a spec. Returns {spec, advice}: the patched spec JSON and
    the advice report with .fixed listing what changed. Offline."""
    bad = _bad_audience(audience)
    if bad:
        return bad
    spec, err = _parse_spec(spec_json)
    if err:
        return json.dumps(err)
    from .design import advise_and_fix

    try:
        new_data, report = advise_and_fix(json.loads(spec_json), audience=audience or None)
    except ValueError as e:
        return json.dumps({"ok": False, "stage": "design", "errors": [{"code": "overlay", "detail": str(e)}]})
    return json.dumps({"ok": True, "stage": "design", "spec": new_data, "advice": report.payload()})


@mcp.tool()
def decompile_dashboard(dashboard: str, profile: str) -> str:
    """Turn a live dashboard (slug or numeric id) into a spec + a named
    lossiness report. Use to pull UI-born dashboards under spec control."""
    from .decompile import decompile_live

    try:
        result = decompile_live(dashboard, _client(profile))
    except ValueError as e:
        return json.dumps({"ok": False, "stage": "decompile", "errors": [{"code": "decompile", "detail": str(e)}]})
    return json.dumps({"ok": True, "spec": result.spec, "losses": result.losses_json()})


@mcp.tool()
def redesign_dashboard(dashboard: str, profile: str, audience: str = "") -> str:
    """One-shot redesign of a live dashboard (slug or numeric id): decompile,
    design-audit with data-aware rules, apply safe geometry fixes. Returns the
    redesigned spec, the decompile losses, and the remaining findings. A
    dashboard the tool does not own gets a '-redesign' slug (applies side by
    side; the original is untouched)."""
    bad = _bad_audience(audience)
    if bad:
        return bad
    from pydantic import ValidationError

    from .apply import _ownership_guard
    from .decompile import decompile_live
    from .design.probe import CardinalityProber
    from .design.redesign import redesign_spec
    from .resolver import resolve
    from .spec import load_spec as _load_spec

    client = _client(profile)
    try:
        result = decompile_live(dashboard, client)
    except ValueError as e:
        return json.dumps({"ok": False, "stage": "redesign",
                           "errors": [{"code": "decompile", "detail": str(e)}]})
    try:
        spec = _load_spec(result.spec)
    except ValidationError:
        return json.dumps({"ok": False, "stage": "redesign", "losses": result.losses_json(),
                           "errors": [{"code": "decompiled_spec_invalid",
                                       "detail": "decompiled spec does not load; use decompile_dashboard"}]})
    owned = _ownership_guard(spec, client) is None
    try:
        new_data, payload = redesign_spec(
            result.spec, result.losses_json(), owned=owned, audience=audience or None,
            resolution=resolve(spec, client), prober=CardinalityProber(client))
    except ValueError as e:
        return json.dumps({"ok": False, "stage": "design",
                           "errors": [{"code": "overlay", "detail": str(e)}]})
    payload["spec"] = new_data
    return json.dumps(payload)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
