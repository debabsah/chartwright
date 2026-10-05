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


def _standards(as_of=None):
    """The server's standards: the directory $CHARTWRIGHT_STANDARDS_DIR names, or the one
    discovered from the server's working directory, since a tool sees a spec and never
    its path (design/standards.py). Waivers match a spec by its slug here."""
    from .design.standards import StandardsSource

    source = StandardsSource.from_env()
    source.as_of = as_of
    return source


def _as_of(text: str):
    """(date or None, error JSON or None) for a tool's as_of argument."""
    if not text:
        return None, None
    from .design.waivers import parse_date

    day = parse_date(text)
    if day is None:
        return None, json.dumps({"ok": False, "stage": "standards", "errors": [{
            "code": "bad_as_of", "detail": f"as_of {text!r} is not a date, YYYY-MM-DD"}]})
    return day, None


def _standard(spec, as_of=None, enforce_expiry: bool = False):
    """(the spec's resolved standard or None, typed error JSON or None)."""
    from .design.standards import StandardsError

    try:
        return _standards(as_of).standard_for(spec, enforce_expiry=enforce_expiry), None
    except StandardsError as e:
        return None, json.dumps({"ok": False, "stage": "standards", "errors": [e.as_dict()]})


def _advice(spec, resolution=None, audience: str | None = None, strict: bool = False,
            source=None) -> dict:
    """Advice riding along MCP responses degrades, never raises (the CLI's own helper)."""
    from .design import advice_payload

    return advice_payload(spec, resolution, audience=audience, strict=strict,
                          standards=source if source is not None else _standards())


def _for_instance(spec, source, client, version):
    """The spec as this instance gets it, standard content its release can't take held
    back (the CLI's own helper)."""
    from .design.standards import for_instance
    from .versions import stated_release

    return for_instance(spec, source, lambda: stated_release(client.superset_version()),
                        stated=version or None)


def _held(out: dict, inst) -> None:
    if inst.held:
        out["held"] = inst.held
    if inst.warning:
        out.setdefault("warnings", []).append(inst.warning)


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
               design: str = "warn", as_of: str = "") -> str:
    """Pre-flight referential resolution against the live Superset instance:
    every dataset triple, column, and metric must exist, and every field must
    suit the instance's Superset release. Returns typed errors plus a
    design-brain advice block. superset_version (optional, e.g. "5.0.0") names the
    instance's Superset release; omitted, the instance is asked. design is the CLI's
    --design: warn (advice rides along), strict (error and warn findings fail the check;
    the per-machine design.yaml is set aside), off (no advice). Standard content the
    release can't take is held back and listed under held. as_of (YYYY-MM-DD) reads
    waiver expiry as of that day; an expired waiver warns here, never fails."""
    bad = _bad_design(design)
    if bad:
        return bad
    spec, err = _parse_spec(spec_json)
    if err:
        return json.dumps(err)
    version, err = _stated_version(superset_version)
    if err:
        return err
    day, err = _as_of(as_of)
    if err:
        return err
    from .apply import check

    client = _client(profile)
    source = _standards(day)
    inst = _for_instance(spec, source, client, version)
    if inst.error:
        return json.dumps({"ok": False, "stage": "version", "errors": [inst.error]})
    res = check(inst.spec, client, version)
    out = {"ok": res.ok, "stage": "resolve", "errors": [e.as_dict() for e in res.errors]}
    if res.superset_version:
        out["superset_version"] = res.superset_version
    if res.version_warnings:
        out["version_warnings"] = res.version_warnings
    if res.unchecked_sql:
        # Custom SQL is not checkable by name; say so instead of passing it silently.
        out["unchecked_sql"] = res.unchecked_sql
    _held(out, inst)
    if design != "off":
        source.release = inst.release
        _gate(out, _advice(spec, resolution=res if res.ok else None,
                           strict=design == "strict", source=source), design)
    return json.dumps(out)


@mcp.tool()
@_typed_errors
def build_dashboard(spec_json: str, profile: str, superset_version: str = "",
                    design: str = "warn", as_of: str = "", save_queries: bool = False) -> str:
    """Compile the spec and apply it to the live Superset instance
    (resolve -> import -> linkage -> data smoke). Returns the full apply report
    including the dashboard URL. A field the instance's release can't take
    stops the build at resolve, before anything is written. superset_version (optional, e.g. "5.0.0") names the
    instance's Superset release; omitted, the instance is asked. design is the CLI's
    apply --design: warn (offline advice rides along), strict (error and warn findings
    stop the build before anything is written; the per-machine design.yaml is set
    aside), off. Standard content the release can't take is held back and listed under
    held. as_of (YYYY-MM-DD) reads waiver expiry as of that day; an expired waiver warns
    here, never blocks. save_queries saves each chart's query as Superset's Save does,
    after a successful build, so CSV and text reports on the charts work (needs the
    chartwright[visual] extra); the result is under saved_queries."""
    bad = _bad_design(design)
    if bad:
        return bad
    spec, err = _parse_spec(spec_json)
    if err:
        return json.dumps(err)
    version, err = _stated_version(superset_version)
    if err:
        return err
    day, err = _as_of(as_of)
    if err:
        return err
    source = _standards(day)
    advice = None
    if design != "off":
        # Offline, before signing in, as the CLI's apply does: strict blocks first.
        source.release = version
        advice = _advice(spec, strict=design == "strict", source=source)
        blocked: dict = {"stage": "design", "ok": False}
        if _gate(blocked, advice, design):
            return json.dumps(blocked)
    from .apply import apply as run_apply

    client = _client(profile)
    inst = _for_instance(spec, source, client, version)
    if inst.error:
        return json.dumps({"ok": False, "stage": "version", "errors": [inst.error]})
    out = json.loads(run_apply(inst.spec, client, profile, version).to_json())
    _held(out, inst)
    if advice is not None:
        out["advice"] = advice
        out["warnings"] = out.get("warnings", []) + advice.get("warnings", [])
    if save_queries and out.get("ok"):
        from .cli import _save_queries

        out["saved_queries"] = _save_queries(inst.spec, client, profile, 60.0)
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

    client = _client(profile)
    inst = _for_instance(spec, _standards(), client, version)
    if inst.error:
        return json.dumps({"ok": False, "stage": "version", "errors": [inst.error]})
    p = run_plan(inst.spec, client, version)
    p.held, p.warnings = inst.held, [inst.warning] if inst.warning else []
    return p.to_json()


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
                strict: bool = False, as_of: str = "") -> str:
    """Design review of a spec against the design-brain rulebook (offline;
    pass a profile for data-aware rules: column types and cardinality).
    Audiences: executive | analytical | operational. `chart` keeps one chart's
    findings (the CLI's advise --chart). `strict` is advise --strict: warn findings
    fail too (a design_gate error), and the per-machine design.yaml is set aside;
    the `overlay` block says what was set aside. as_of (YYYY-MM-DD) reads waiver expiry
    as of that day: an expired waiver fails here, as in standards check."""
    bad = _bad_audience(audience)
    if bad:
        return bad
    spec, err = _parse_spec(spec_json)
    if err:
        return json.dumps(err)
    bad = _bad_chart(spec, chart)
    if bad:
        return bad
    day, err = _as_of(as_of)
    if err:
        return err
    standard, err = _standard(spec, day, enforce_expiry=True)
    if err:
        return err
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
                        chart=chart or None, strict=strict, standard=standard)
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
def fix_spec(spec_json: str, audience: str = "", strict: bool = False, as_of: str = "") -> str:
    """Apply the design brain's safe, presentation-only fixes to a spec: repairs
    (heights, bar orientation), then design defaults it fills into fields left
    unset (time-axis label format, count number format, table paging, ...),
    recorded in design.filled. Returns {spec, advice}: the patched spec JSON and
    the advice report; each .fixed entry has kind "fill" or "repair" and a why.
    Run it before build_dashboard; keep the returned spec and edit THAT, never a
    regenerated one. Offline. `strict` fixes as advise --fix --strict does: the
    per-machine design.yaml is set aside, parameters included. As in advise, an expired
    waiver counts (standard.waiver-expired); as_of (YYYY-MM-DD) reads expiry as of
    that day."""
    bad = _bad_audience(audience)
    if bad:
        return bad
    spec, err = _parse_spec(spec_json)
    if err:
        return json.dumps(err)
    day, err = _as_of(as_of)
    if err:
        return err
    standard, err = _standard(spec, day, enforce_expiry=True)
    if err:
        return err
    from .design import advise_and_fix

    try:
        new_data, report = advise_and_fix(json.loads(spec_json), audience=audience or None,
                                          strict=strict, standard=standard)
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
    standard, err = _standard(spec)
    if err:
        return err
    from .design.explain import explain

    try:
        return json.dumps(explain(spec, audience=audience or None, chart=chart or None,
                                  standard=standard))
    except ValueError as e:
        return json.dumps({"ok": False, "stage": "design", "errors": [{"code": "overlay", "detail": str(e)}]})


@mcp.tool()
def standards_check(spec_json: str, strict: bool = False, as_of: str = "",
                    superset_version: str = "") -> str:
    """The CLI's `standards check` for one spec, offline: the design review with the
    spec's standard applied (design.standard, or the repository's default standard),
    setting the per-machine design.yaml aside. ok is false on an error finding, or on a
    warn when strict. Returns the standard chain, the findings (each with the layer that
    set its severity and whether a standard locks its rule) and the locks applied. The
    standards directory is the server's $CHARTWRIGHT_STANDARDS_DIR, or the one found
    from its working directory. Waivers in its waivers.yaml that name the spec's slug
    let locked findings pass (listed under waived); an expired one fails, read as of
    as_of (YYYY-MM-DD, default today). superset_version holds the standard's content to
    that release: what it can't take is listed under held."""
    spec, err = _parse_spec(spec_json)
    if err:
        return json.dumps(err)
    day, err = _as_of(as_of)
    if err:
        return err
    version, err = _stated_version(superset_version)
    if err:
        return err
    from .design.standards import StandardsError, check_spec, display

    source = _standards(day)
    source.release = version
    try:
        standards = source.load()
    except StandardsError as e:
        return json.dumps({"ok": False, "stage": "standards", "errors": [e.as_dict()]})
    if standards is None:
        return json.dumps({"ok": False, "stage": "standards", "errors": [{
            "code": "no_standards_dir",
            "detail": f"no standards directory is configured; {source.hint}"}]})
    return json.dumps({"stage": "standards", "standards_dir": display(standards.directory),
                       "strict": strict, **check_spec(spec, source, strict=strict),
                       **({"standards_warnings": standards.warnings} if standards.warnings
                          else {})})


@mcp.tool()
def standards_show(name: str = "", spec_json: str = "") -> str:
    """The CLI's `standards show --json`: a standard after its extends chain, each key
    with its value, the layer that set it and whether it is locked. Name the standard,
    or pass spec_json for the standard that spec follows; neither shows the default
    standard. The standards directory is the server's $CHARTWRIGHT_STANDARDS_DIR, or the
    one found from its working directory."""
    from .design.standards import StandardsError, show

    spec = None
    if spec_json:
        spec, err = _parse_spec(spec_json)
        if err:
            return json.dumps(err)
    try:
        return json.dumps(show(_standards(), name or None, spec))
    except StandardsError as e:
        return json.dumps({"ok": False, "stage": "standards", "errors": [e.as_dict()]})


@mcp.tool()
@_typed_errors
def standards_apply(spec_json: str, check: bool = False, locked: bool = False,
                    claim: bool = False, strict: bool = False, as_of: str = "") -> str:
    """The CLI's `standards apply` for one spec, offline: writes the content of the spec's
    standard into it (header and footer rows, CSS blocks, colour scheme, label colours,
    certification, number formats), recording each item in design.standard_written, and
    returns {spec, changes, stale, locked_stale, locked}. Keep the returned spec. Never
    edit content the standard wrote by hand to make a finding go away: a locked item
    stays the standard's, and an unlocked one you edit becomes yours for good. check
    returns no spec, and ok false when the spec doesn't hold the standard's locked content
    as it is now (the CLI's --check; `stale` says whether unlocked content would change
    too), and with strict, ok false on any change apply would make (--check --strict);
    locked also rewrites locked items the author changed (--locked); claim
    records items that already hold the standard's value (--claim). A locked item a
    waiver in waivers.yaml covers is left as it is and listed under waived (as_of,
    YYYY-MM-DD, is the day expiry is read against). The standards directory is the
    server's $CHARTWRIGHT_STANDARDS_DIR, or the one found from its working directory."""
    spec, err = _parse_spec(spec_json)
    if err:
        return json.dumps(err)
    day, err = _as_of(as_of)
    if err:
        return err
    from .design.standards import StandardsError, apply_spec, display

    source = _standards(day)
    try:
        standards = source.load()
        std = source.standard_for(spec)
    except StandardsError as e:
        return json.dumps({"ok": False, "stage": "standards", "errors": [e.as_dict()]})
    if standards is None:
        return json.dumps({"ok": False, "stage": "standards", "errors": [{
            "code": "no_standards_dir",
            "detail": f"no standards directory is configured; {source.hint}"}]})
    out: dict = {"stage": "standards", "standards_dir": display(standards.directory),
                 "check": check}
    if std is None:
        return json.dumps({**out, "ok": True, "standard": None,
                           "detail": "the spec follows no standard: it has no design.standard "
                                     "and no standard says default: true; nothing to write"})
    data = json.loads(spec_json)
    new, entry = apply_spec(data, spec, std, locked=locked, claim=claim)
    entry.pop("decisions")
    out.update(entry)
    if check:
        out["ok"] = not entry["locked_stale"] and not entry["errors"] and not (
            strict and new != data)
    else:
        out["spec"] = new
        out["written"] = new != data
    return json.dumps(out)


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
def adopt_dashboard(dashboard: str, profile: str, accept_reset: bool = False,
                    allow_shared: bool = False) -> str:
    """Take over an existing dashboard (slug or numeric id) in place: returns a spec
    that names that dashboard and its charts by their own ids, so applying it
    updates the same dashboard (same address and id). Changes nothing in Superset
    itself; run plan_dashboard on the spec before build_dashboard. Lists under
    `resets` what the first apply resets because a spec can't hold it, and refuses
    while there are any unless accept_reset is true; refuses when some charts also
    sit on other dashboards (applying would change them there too) unless
    allow_shared is true. Show the user the resets and ask before setting either."""
    from .adopt import adopt_live

    try:
        result = adopt_live(dashboard, _client(profile), accept_reset=accept_reset,
                            allow_shared=allow_shared)
    except ValueError as e:
        return json.dumps({"ok": False, "stage": "adopt",
                           "errors": [{"code": "decompile", "detail": str(e)}]})
    return json.dumps({**result.payload(), "spec": result.spec})


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
