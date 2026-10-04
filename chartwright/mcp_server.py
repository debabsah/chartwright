"""v2: MCP server over the same core, so any MCP client (Claude Desktop/Code,
etc.) can drive the compiler. Tools mirror the CLI verbs 1:1; the server adds
no behavior of its own; the guarantee stays in the core.

Run: chartwright-mcp   (stdio transport; profiles + password env vars as for the CLI)
"""

from __future__ import annotations

import functools
import json

try:  # mcp 2 renamed FastMCP to MCPServer; the tool API chartwright uses is the same
    from mcp.server.mcpserver import MCPServer
except ImportError:  # mcp 1
    from mcp.server.fastmcp import FastMCP as MCPServer

from .spec import json_schema, load_spec

mcp = MCPServer("chartwright")


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
    c = SupersetClient.from_profile(p)
    c.login()
    return c


def _stated_version(superset_version: str):
    """(release or None, error JSON or None) for a tool's superset_version argument."""
    if not superset_version:
        return None, None
    from .versions import not_a_release, stated_release

    release = stated_release(superset_version)
    if release is None:
        return None, json.dumps({"ok": False, "stage": "version", "errors": [{
            "code": "bad_superset_version", "detail": not_a_release(superset_version)}]})
    return release, None


def _typed_errors(fn):
    """Return the CLI's typed error JSON instead of raising, for tools that
    sign in. A raised exception reached the agent as a bare tool failure it
    couldn't tell apart from any other; the CLI's shape names the kind:
    stage "profile" for profile and password problems, code "api" for sign-in
    and Superset errors, code "unexpected" for anything else (a bug)."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        from .client import SupersetAPIError
        from .profiles import ProfileError

        try:
            return fn(*args, **kwargs)
        except ProfileError as e:
            return json.dumps({"ok": False, "stage": "profile",
                               "errors": [{"code": "profile", "detail": str(e)}]})
        except SupersetAPIError as e:
            return json.dumps({"ok": False, "stage": "error",
                               "errors": [{"code": "api", "detail": str(e)}]})
        except Exception as e:  # noqa: BLE001 - tool boundary, as at the CLI's
            return json.dumps({"ok": False, "stage": "error", "errors": [{
                "code": "unexpected",
                "detail": f"{type(e).__name__}: {e} (please report this; it should have been a typed error)"}]})

    return wrapper


@mcp.tool()
def get_spec_schema() -> str:
    """The JSON Schema a dashboard spec must satisfy. Read this before writing a spec."""
    return json.dumps(json_schema())


@mcp.tool()
def validate_spec(spec_json: str) -> str:
    """Schema-validate a dashboard spec (offline). Returns ok or typed errors."""
    _, err = _parse_spec(spec_json)
    return json.dumps(err or {"ok": True, "stage": "schema"})


def _advice(spec, resolution=None, audience: str | None = None, strict: bool = False) -> dict:
    """Advice riding along MCP responses degrades, never raises (the CLI's own helper)."""
    from .design import advice_payload

    return advice_payload(spec, resolution, audience=audience, strict=strict)


def _bad_design(design: str) -> str | None:
    if design not in ("off", "warn", "strict"):
        return json.dumps({"ok": False, "stage": "design", "errors": [{
            "code": "design", "detail": f"design must be off, warn or strict, not {design!r}"}]})
    return None


def _gate(out: dict, advice: dict, design: str) -> bool:
    """Attach the advice block; under design="strict", block the way the CLI's
    --design strict does (ok false, a design_gate error). True when it blocked."""
    from .design import gate_block

    out["advice"] = advice
    blocked = gate_block(advice) if design == "strict" else None
    if blocked:
        out["ok"] = False
        out.setdefault("errors", []).append({"code": "design_gate", "detail": blocked})
    return blocked is not None


def _bad_audience(audience: str) -> str | None:
    from .design.presets import AUDIENCE_NAMES

    if audience and audience not in AUDIENCE_NAMES:
        return json.dumps({"ok": False, "stage": "design", "errors": [{
            "code": "audience",
            "detail": f"unknown audience {audience!r}; one of {sorted(AUDIENCE_NAMES)}"}]})
    return None


def _bad_chart(spec, chart: str) -> str | None:
    names = [c.name for c in spec.charts]
    if chart and chart not in names:
        return json.dumps({"ok": False, "stage": "design", "errors": [{
            "code": "unknown_chart",
            "detail": f"no chart named {chart!r} in the spec; charts: {names}"}]})
    return None


@mcp.tool()
@_typed_errors
def check_spec(spec_json: str, profile: str, superset_version: str = "",
               design: str = "warn") -> str:
    """Pre-flight referential resolution against the live Superset instance:
    every dataset triple, column, and metric must exist, and every field must
    suit the instance's Superset release. Returns typed errors plus a
    design-brain advice block. superset_version (optional, e.g. "5.0.0") names the
    instance's Superset release; omitted, the instance is asked. design is the CLI's
    --design: warn (advice rides along), strict (error and warn findings fail the check;
    the per-machine design.yaml is set aside), off (no advice)."""
    bad = _bad_design(design)
    if bad:
        return bad
    spec, err = _parse_spec(spec_json)
    if err:
        return json.dumps(err)
    version, err = _stated_version(superset_version)
    if err:
        return err
    from .apply import check

    res = check(spec, _client(profile), version)
    out = {"ok": res.ok, "stage": "resolve", "errors": [e.as_dict() for e in res.errors]}
    if res.superset_version:
        out["superset_version"] = res.superset_version
    if res.version_warnings:
        out["version_warnings"] = res.version_warnings
    if res.unchecked_sql:
        # Custom SQL is not checkable by name; say so instead of passing it silently.
        out["unchecked_sql"] = res.unchecked_sql
    if design != "off":
        _gate(out, _advice(spec, resolution=res if res.ok else None,
                           strict=design == "strict"), design)
    return json.dumps(out)


@mcp.tool()
@_typed_errors
def build_dashboard(spec_json: str, profile: str, superset_version: str = "",
                    design: str = "warn") -> str:
    """Compile the spec and apply it to the live Superset instance
    (resolve -> import -> linkage -> data smoke). Returns the full apply report
    including the dashboard URL. A field the instance's release can't take
    stops the build at resolve, before anything is written. superset_version (optional, e.g. "5.0.0") names the
    instance's Superset release; omitted, the instance is asked. design is the CLI's
    apply --design: warn (offline advice rides along), strict (error and warn findings
    stop the build before anything is written; the per-machine design.yaml is set
    aside), off."""
    bad = _bad_design(design)
    if bad:
        return bad
    spec, err = _parse_spec(spec_json)
    if err:
        return json.dumps(err)
    version, err = _stated_version(superset_version)
    if err:
        return err
    advice = None
    if design != "off":
        # Offline, before signing in, as the CLI's apply does: strict blocks first.
        advice = _advice(spec, strict=design == "strict")
        blocked: dict = {"stage": "design", "ok": False}
        if _gate(blocked, advice, design):
            return json.dumps(blocked)
    from .apply import apply as run_apply

    out = json.loads(run_apply(spec, _client(profile), profile, version).to_json())
    if advice is not None:
        out["advice"] = advice
    return json.dumps(out, indent=2)


@mcp.tool()
@_typed_errors
def plan_dashboard(spec_json: str, profile: str, superset_version: str = "") -> str:
    """Diff a spec against the live dashboard at its slug: what would apply
    change? clean=true means no drift. superset_version (optional, e.g. "5.0.0") names the
    instance's Superset release; omitted, the instance is asked."""
    spec, err = _parse_spec(spec_json)
    if err:
        return json.dumps(err)
    version, err = _stated_version(superset_version)
    if err:
        return err
    from .dashdiff import plan as run_plan

    return run_plan(spec, _client(profile), version).to_json()


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
@_typed_errors
def advise_spec(spec_json: str, audience: str = "", profile: str = "", chart: str = "",
                strict: bool = False) -> str:
    """Design review of a spec against the design-brain rulebook (offline;
    pass a profile for data-aware rules: column types and cardinality).
    Audiences: executive | analytical | operational. `chart` keeps one chart's
    findings (the CLI's advise --chart). `strict` is advise --strict: warn findings
    fail too (a design_gate error), and the per-machine design.yaml is set aside;
    the `overlay` block says what was set aside."""
    bad = _bad_audience(audience)
    if bad:
        return bad
    spec, err = _parse_spec(spec_json)
    if err:
        return json.dumps(err)
    bad = _bad_chart(spec, chart)
    if bad:
        return bad
    from .design import advise

    resolution = prober = None
    if profile:
        from .design.probe import CardinalityProber
        from .resolver import resolve

        client = _client(profile)
        resolution = resolve(spec, client)
        prober = CardinalityProber(client)
    try:
        report = advise(spec, audience=audience or None, resolution=resolution, prober=prober,
                        chart=chart or None, strict=strict)
    except ValueError as e:
        return json.dumps({"ok": False, "stage": "design", "errors": [{"code": "overlay", "detail": str(e)}]})
    payload = report.payload()
    if resolution is not None and resolution.errors:
        payload["resolution_errors"] = [e.as_dict() for e in resolution.errors]
    if report.gate(strict):
        from .design import advise_gate_detail

        payload.setdefault("errors", []).append({"code": "design_gate",
                                                 "detail": advise_gate_detail(report)})
    return json.dumps(payload)


@mcp.tool()
def fix_spec(spec_json: str, audience: str = "", strict: bool = False) -> str:
    """Apply the design brain's safe, presentation-only fixes to a spec: repairs
    (heights, bar orientation), then design defaults it fills into fields left
    unset (time-axis label format, count number format, table paging, ...),
    recorded in design.filled. Returns {spec, advice}: the patched spec JSON and
    the advice report; each .fixed entry has kind "fill" or "repair" and a why.
    Run it before build_dashboard; keep the returned spec and edit THAT, never a
    regenerated one. Offline. `strict` fixes as advise --fix --strict does: the
    per-machine design.yaml is set aside, parameters included."""
    bad = _bad_audience(audience)
    if bad:
        return bad
    spec, err = _parse_spec(spec_json)
    if err:
        return json.dumps(err)
    from .design import advise_and_fix

    try:
        new_data, report = advise_and_fix(json.loads(spec_json), audience=audience or None,
                                          strict=strict)
    except ValueError as e:
        return json.dumps({"ok": False, "stage": "design", "errors": [{"code": "overlay", "detail": str(e)}]})
    return json.dumps({"ok": True, "stage": "design", "spec": new_data, "advice": report.payload()})


@mcp.tool()
def explain_spec(spec_json: str, chart: str = "", audience: str = "") -> str:
    """Where each design-default field's value comes from (the CLI's `explain
    --json`), offline: per chart, one row per field a default.* rule governs with
    its value, source (spec | filled | superset default), the rule, a reason, and
    how to override it; a filled field also shows the value design.filled recorded."""
    bad = _bad_audience(audience)
    if bad:
        return bad
    spec, err = _parse_spec(spec_json)
    if err:
        return json.dumps(err)
    bad = _bad_chart(spec, chart)
    if bad:
        return bad
    from .design.explain import explain

    try:
        return json.dumps(explain(spec, audience=audience or None, chart=chart or None))
    except ValueError as e:
        return json.dumps({"ok": False, "stage": "design", "errors": [{"code": "overlay", "detail": str(e)}]})


@mcp.tool()
@_typed_errors
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
@_typed_errors
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
