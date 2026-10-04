"""CLI: the deterministic back-half the LLM front-end drives.

    chartwright schema                          print the JSON Schema the LLM must satisfy
    chartwright validate spec.json              schema-validate only (no network)
    chartwright compile spec.json -o out.zip    compile bundle with a stub resolution (golden/debug)
    chartwright check spec.json --profile P     pre-flight referential resolution
    chartwright apply spec.json --profile P     check -> compile -> import -> smoke
    chartwright brief                           the design brief to read BEFORE authoring a spec
    chartwright advise spec.json                design review (add --profile for data-aware rules)
    chartwright explain spec.json               where each design-default field comes from, and why
    chartwright redesign <slug> --profile P     decompile + audit + safe fixes -> redesigned spec
    chartwright calibrate                       learn recommended heights from absorb history
    chartwright standards check specs/          check specs against the repository's standards
    chartwright standards show [NAME]           a standard after extends, each key's layer and lock
    chartwright standards assign specs/ --standard NAME   write design.standard into specs
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from .spec import json_schema, load_spec


def _load(path: str):
    try:
        # Explicit UTF-8: Windows' locale default (cp1252) would mojibake or
        # reject the non-ASCII titles/markdown LLM-written specs contain.
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        _die({"stage": "parse", "errors": [{"code": "unreadable_spec", "detail": str(e)}]})
    try:
        return load_spec(data)
    except ValidationError as e:
        _die({"stage": "schema", "errors": json.loads(e.json())})


def _release(text: str) -> str:
    """argparse type for --superset-version: a release such as 5.0.0."""
    from .versions import format_version, parse_version

    release = parse_version(text)
    if release is None:
        raise argparse.ArgumentTypeError(f"{text!r} is not a Superset release, e.g. 5.0.0")
    return format_version(release)


_VERSION_HELP = ("the Superset release to hold the spec to, e.g. 5.0.0; fields that release "
                 "can't take are refused, fields it ignores warn")


def _die(payload: dict, code: int = 1) -> None:
    print(json.dumps(payload, indent=2, default=str))
    sys.exit(code)


def _design_blocks(advice: dict) -> str | None:
    """Why `--design strict` should block, or None (design.gate_block)."""
    from .design import gate_block

    return gate_block(advice)


def _advice_payload(spec, resolution=None, strict: bool = False, standards=None) -> dict:
    """The advice block check/apply carry (design.advice_payload). `strict` is
    `--design strict`: the per-machine design.yaml then counts for nothing. `standards`
    is where the spec's standard comes from (a StandardsSource), or None for none."""
    from .design import advice_payload

    return advice_payload(spec, resolution, strict=strict, standards=standards)


def _standards(args, spec_path):
    """The standards for a spec file: --standards DIR, or the repository's standards/
    folder found from the spec's own folder (design/standards.py discover)."""
    from .design.standards import StandardsSource

    return StandardsSource.for_cli(getattr(args, "standards", None), spec_path)


def _standard_or_die(args, spec):
    """The spec's resolved standard, or None; a standard that can't be resolved stops
    the command with a typed error."""
    from .design.standards import StandardsError

    try:
        return _standards(args, args.spec).standard_for(spec)
    except StandardsError as e:
        _die({"stage": "standards", "errors": [e.as_dict()]})


_STANDARDS_HELP = ("the standards directory (default: a standards/ folder at or above the "
                   "spec, inside its git repository)")


def _client(profile_name: str):
    from .client import SupersetClient
    from .profiles import load_profile, ProfileError

    try:
        p = load_profile(profile_name)
    except ProfileError as e:
        _die({"stage": "profile", "errors": [{"code": "profile", "detail": str(e)}]})
    c = SupersetClient.from_profile(p)
    c.login()
    return c


def main(argv: list[str] | None = None) -> None:
    try:
        _main(argv)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 - CLI boundary: tracebacks are not the contract
        from .client import SupersetAPIError

        code = "api" if isinstance(e, SupersetAPIError) else "unexpected"
        detail = str(e)
        if code == "unexpected":
            detail = f"{type(e).__name__}: {e} (please report this; it should have been a typed error)"
        _die({"stage": "error", "errors": [{"code": code, "detail": detail}]})


def _main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="chartwright", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("schema", help="print the JSON Schema a spec must satisfy")

    v = sub.add_parser("validate", help="schema-validate a spec (offline, no network)")
    v.add_argument("spec")

    comp = sub.add_parser("compile", help="compile an import bundle offline (stub dataset ids)")
    comp.add_argument("spec")
    comp.add_argument("-o", "--output", default=None)
    comp.add_argument("--superset-version", type=_release, default=None,
                      help=_VERSION_HELP + " (default: no check; the bundle is the same either way)")

    subhelp = {"check": "pre-flight referential resolution against the live instance",
               "apply": "check -> compile -> import -> verify -> smoke",
               "plan": "diff the spec against the live dashboard (drift detection)"}
    for name in ("check", "apply", "plan"):
        p = sub.add_parser(name, help=subhelp[name])
        p.add_argument("spec")
        p.add_argument("--profile", required=True)
        p.add_argument("--superset-version", type=_release, default=None,
                       help=_VERSION_HELP + " (default: ask the instance)")
        if name != "plan":
            p.add_argument("--design", choices=["off", "warn", "strict"], default="warn",
                           help="design-brain advice: warn (report, default), strict (block), off")
            p.add_argument("--standards", default=None, metavar="DIR", help=_STANDARDS_HELP)

    from .design.presets import AUDIENCE_NAMES

    adv = sub.add_parser("advise", help="design review of a spec against the design-brain rulebook")
    adv.add_argument("spec")
    adv.add_argument("--profile", default=None, help="enable data-aware rules (types, cardinality)")
    adv.add_argument("--audience", choices=AUDIENCE_NAMES, default=None)
    adv.add_argument("--fix", action="store_true", help="apply safe presentation-only fixes to the spec file")
    adv.add_argument("--strict", action="store_true",
                     help="exit 1 on warnings, not just errors; the per-machine design.yaml "
                          "is then set aside")
    adv.add_argument("--ignore", default=None, help="comma-separated rule ids to suppress")
    adv.add_argument("--no-probe", action="store_true", help="skip cardinality queries (metadata only)")
    adv.add_argument("--chart", default=None, metavar="NAME",
                     help="only this chart's findings, and with --fix only its fixes")
    adv.add_argument("--standards", default=None, metavar="DIR", help=_STANDARDS_HELP)

    ex = sub.add_parser("explain",
                        help="where each chart's design-default fields come from, and why (offline)")
    ex.add_argument("spec")
    ex.add_argument("--chart", default=None, metavar="NAME", help="explain this chart only")
    ex.add_argument("--audience", choices=AUDIENCE_NAMES, default=None)
    ex.add_argument("--json", action="store_true", help="the same rows as JSON, for agents")
    ex.add_argument("--standards", default=None, metavar="DIR", help=_STANDARDS_HELP)

    st = sub.add_parser("standards",
                        help="the repository's standards: check specs against them, show one, "
                             "assign one to specs")
    std_verbs = st.add_subparsers(dest="standards_cmd", required=True)
    stc = std_verbs.add_parser(
        "check", help="advise each spec with its standard applied, setting design.yaml aside; "
                      "exit 1 on an error finding (or a warn under --strict)")
    stc.add_argument("specs", nargs="+", help="spec files, folders (every .json beneath) or globs")
    stc.add_argument("--standards", default=None, metavar="DIR", help=_STANDARDS_HELP)
    stc.add_argument("--strict", action="store_true", help="warn findings fail too")
    stc.add_argument("--report", action="store_true",
                     help="the fleet report: per spec its standard, pass or fail, finding counts "
                          "by rule and severity and the locks it hit; then the totals")
    sts = std_verbs.add_parser("show", help="a standard after extends: each key's value, the "
                                            "layer that set it, and whether it is locked")
    sts.add_argument("name", nargs="?", default=None,
                     help="the standard (default: the one marked default: true)")
    sts.add_argument("--for", dest="for_spec", default=None, metavar="SPEC",
                     help="the standard this spec follows")
    sts.add_argument("--standards", default=None, metavar="DIR",
                     help="the standards directory (default: a standards/ folder at or above "
                          "the spec, or the working directory, inside its git repository)")
    sts.add_argument("--json", action="store_true", help="the same as JSON, for agents")
    sta = std_verbs.add_parser("assign", help="write design.standard into each spec")
    sta.add_argument("specs", nargs="+", help="spec files, folders (every .json beneath) or globs")
    sta.add_argument("--standard", required=True, metavar="NAME", help="the standard's name")
    sta.add_argument("--standards", default=None, metavar="DIR", help=_STANDARDS_HELP)

    br = sub.add_parser("brief", help="the design brief to read BEFORE authoring a spec")
    br.add_argument("--audience", choices=AUDIENCE_NAMES, default="analytical")

    rd = sub.add_parser("redesign",
                        help="decompile a live dashboard, audit it, apply safe fixes, write the redesigned spec")
    rd.add_argument("dashboard", help="slug or numeric id of a live dashboard")
    rd.add_argument("--profile", required=True)
    rd.add_argument("--audience", choices=AUDIENCE_NAMES, default=None)
    rd.add_argument("-o", "--output", default=None,
                    help="write the redesigned spec here (default: <slug>.json)")
    rd.add_argument("--no-probe", action="store_true", help="skip cardinality queries (metadata only)")

    cal = sub.add_parser("calibrate", help="propose recommended heights from absorb history")
    cal.add_argument("--write", action="store_true", help="record proposals in the design overlay")
    cal.add_argument("--min-samples", type=int, default=5)
    cal.add_argument("--since", default=None, metavar="90d",
                     help="only consider absorb events newer than this (decay knob)")

    dec = sub.add_parser("decompile")
    dec.add_argument("dashboard", help="slug or numeric id of a live dashboard")
    dec.add_argument("--profile", required=True)
    dec.add_argument("-o", "--output", default=None, help="write spec JSON here; losses go to stdout")

    ab = sub.add_parser("absorb", help="patch LIVE UI height polish back into the spec (heights only)")
    ab.add_argument("spec")
    ab.add_argument("--profile", required=True)
    ab.add_argument("--dry-run", action="store_true", help="report without writing the spec file")

    rst = sub.add_parser("restore", help="re-import a backup bundle written by a prior apply")
    rst.add_argument("bundle", help="path to a bundle zip written by a prior apply")
    rst.add_argument("--profile", required=True)

    args = ap.parse_args(argv)

    if args.cmd == "schema":
        print(json.dumps(json_schema(), indent=2))
        return

    if args.cmd == "validate":
        _load(args.spec)
        print(json.dumps({"ok": True, "stage": "schema"}))
        return

    if args.cmd == "compile":
        spec = _load(args.spec)
        from .compiler import compile_bundle
        from .testing import stub_resolution

        checked = None
        if args.superset_version:
            from .versions import check_spec_version

            checked = check_spec_version(spec, args.superset_version)
            if not checked.ok:
                _die({"ok": False, "stage": "version", "superset_version": checked.version,
                      "errors": checked.errors, "version_warnings": checked.warnings})
        bundle = compile_bundle(spec, stub_resolution(spec))
        out = Path(args.output or f"{spec.dashboard.slug}.zip")
        out.write_bytes(bundle)
        payload = {"ok": True, "stage": "compile", "output": str(out), "bytes": len(bundle),
                   "note": "stub resolution (fake dataset ids); use apply for a real import"}
        if checked is not None:
            payload["superset_version"] = checked.version
            payload["version_warnings"] = checked.warnings
        print(json.dumps(payload))
        return

    if args.cmd == "check":
        spec = _load(args.spec)
        client = _client(args.profile)
        from .apply import check

        res = check(spec, client, args.superset_version)
        payload = {"ok": res.ok, "stage": "resolve", "errors": [e.as_dict() for e in res.errors]}
        if res.superset_version:
            payload["superset_version"] = res.superset_version
        if res.version_warnings:
            payload["version_warnings"] = res.version_warnings
        if res.unchecked_sql:
            # Custom SQL is not checkable by name; say so instead of passing it silently.
            payload["unchecked_sql"] = res.unchecked_sql
        gate = False
        if args.design != "off":
            advice = _advice_payload(spec, resolution=res if res.ok else None,
                                     strict=args.design == "strict",
                                     standards=_standards(args, args.spec))
            payload["advice"] = advice
            blocked = _design_blocks(advice) if args.design == "strict" else None
            gate = blocked is not None
            if gate:
                payload["ok"] = False
                payload["errors"].append({"code": "design_gate", "detail": blocked})
        print(json.dumps(payload, indent=2))
        sys.exit(0 if res.ok and not gate else 1)

    if args.cmd == "apply":
        spec = _load(args.spec)
        advice = None
        if args.design != "off":
            # Pre-flight, offline (no probes: applies stay fast; data-aware
            # advice is `chartwright advise --profile`). Strict blocks BEFORE
            # anything on the instance is touched.
            advice = _advice_payload(spec, strict=args.design == "strict",
                                     standards=_standards(args, args.spec))
            blocked = _design_blocks(advice) if args.design == "strict" else None
            if blocked:
                _die({"stage": "design", "ok": False, "advice": advice,
                      "errors": [{"code": "design_gate", "detail": blocked}]})
        client = _client(args.profile)
        from .apply import apply as run_apply

        report = run_apply(spec, client, args.profile, args.superset_version)
        out = json.loads(report.to_json())
        if advice is not None:
            out["advice"] = advice
        print(json.dumps(out, indent=2))
        sys.exit(0 if report.ok else 1)

    if args.cmd in ("advise", "explain") and args.chart is not None:
        names = [c.name for c in _load(args.spec).charts]
        if args.chart not in names:
            _die({"stage": "design", "errors": [{
                "code": "unknown_chart",
                "detail": f"no chart named {args.chart!r} in the spec; charts: {names}"}]})

    if args.cmd == "explain":
        spec = _load(args.spec)
        from .design.explain import explain, render_text

        standard = _standard_or_die(args, spec)
        try:
            payload = explain(spec, audience=args.audience, chart=args.chart, standard=standard)
        except ValueError as e:
            _die({"stage": "design", "errors": [{"code": "overlay", "detail": str(e)}]})
        print(json.dumps(payload, indent=2, ensure_ascii=False) if args.json
              else render_text(payload), end="\n" if args.json else "")
        return

    if args.cmd == "advise":
        spec = _load(args.spec)
        standard = _standard_or_die(args, spec)
        resolution = prober = None
        if args.profile:
            client = _client(args.profile)
            from .resolver import resolve

            resolution = resolve(spec, client)
            if not args.no_probe:
                from .design.probe import CardinalityProber

                prober = CardinalityProber(client)
        ignore = tuple(s.strip() for s in (args.ignore or "").split(",") if s.strip())
        from .design import advise, advise_and_fix

        written = None
        try:
            if args.fix:
                spec_data = json.loads(Path(args.spec).read_text(encoding="utf-8"))
                new_data, report = advise_and_fix(
                    spec_data, audience=args.audience, ignore=ignore,
                    resolution=resolution, prober=prober, chart=args.chart,
                    strict=args.strict, standard=standard)
                if report.fixed:
                    # --fix rewrites the whole file (normalized JSON formatting,
                    # same as absorb); the payload discloses the path.
                    Path(args.spec).write_text(
                        json.dumps(new_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                    written = str(args.spec)
            else:
                report = advise(spec, audience=args.audience, ignore=ignore,
                                resolution=resolution, prober=prober, chart=args.chart,
                                strict=args.strict, standard=standard)
        except ValueError as e:
            _die({"stage": "design", "errors": [{"code": "overlay", "detail": str(e)}]})
        payload = report.payload()
        if written:
            payload["written"] = written
        if resolution is not None and resolution.errors:
            payload["resolution_errors"] = [e.as_dict() for e in resolution.errors]
        if report.gate(args.strict):
            # `ok` stays error-driven by contract (§10), so the exit code was
            # the ONLY signal that --strict blocked. Name the cause the way
            # check/apply do, or a caller sees exit 1 with nothing to read.
            from .design import advise_gate_detail

            payload.setdefault("errors", []).append({
                "code": "design_gate", "detail": advise_gate_detail(report)})
        print(json.dumps(payload, indent=2))
        sys.exit(1 if report.gate(args.strict) else 0)

    if args.cmd == "standards":
        _standards_cmd(args)
        return

    if args.cmd == "redesign":
        client = _client(args.profile)
        from .decompile import decompile_live

        try:
            result = decompile_live(args.dashboard, client)
        except ValueError as e:
            _die({"stage": "redesign", "errors": [{"code": "decompile", "detail": str(e)}]})
        try:
            spec = load_spec(result.spec)
        except ValidationError as e:
            _die({"stage": "redesign", "losses": result.losses_json(), "errors": [
                {"code": "decompiled_spec_invalid",
                 "detail": "the decompiled spec does not load; redesign by hand from "
                           "`chartwright decompile` output"},
                *json.loads(e.json()),
            ]})
        from .apply import _ownership_guard
        from .design.probe import CardinalityProber
        from .design.redesign import redesign_spec
        from .resolver import resolve

        owned = _ownership_guard(spec, client) is None
        resolution = resolve(spec, client)
        prober = None if args.no_probe else CardinalityProber(client)
        try:
            new_data, payload = redesign_spec(
                result.spec, result.losses_json(), owned=owned,
                audience=args.audience, resolution=resolution, prober=prober)
        except ValueError as e:
            _die({"stage": "design", "errors": [{"code": "overlay", "detail": str(e)}]})
        out = Path(args.output or f"{new_data['dashboard']['slug']}.json")
        if args.output is None and out.exists():
            _die({"stage": "redesign", "errors": [{
                "code": "output_exists",
                "detail": f"{out} already exists (likely a previous redesign); "
                          f"pass -o to choose where to write"}]})
        out.write_text(json.dumps(new_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        payload["output"] = str(out)
        if resolution.errors:
            payload["resolution_errors"] = [e.as_dict() for e in resolution.errors]
        print(json.dumps(payload, indent=2))
        sys.exit(0 if payload["ok"] else 1)

    if args.cmd == "brief":
        from .design.brief import render_brief

        try:
            print(render_brief(args.audience))
        except ValueError as e:
            _die({"stage": "design", "errors": [{"code": "overlay", "detail": str(e)}]})
        return

    if args.cmd == "calibrate":
        from .design.calibrate import calibrate

        try:
            print(json.dumps(calibrate(min_samples=args.min_samples, write=args.write,
                                       since=args.since), indent=2))
        except ValueError as e:
            _die({"stage": "calibrate", "errors": [{"code": "overlay", "detail": str(e)}]})
        return

    if args.cmd == "plan":
        spec = _load(args.spec)
        client = _client(args.profile)
        from .dashdiff import plan as run_plan

        p = run_plan(spec, client, args.superset_version)
        print(p.to_json())
        sys.exit(0 if p.clean else 1)

    if args.cmd == "absorb":
        spec = _load(args.spec)
        client = _client(args.profile)
        from .absorb import absorb_heights
        from .apply import _ownership_guard

        guard = _ownership_guard(spec, client)
        if guard:
            _die({"stage": "absorb", "errors": [{"code": "ownership", "detail": guard}]})
        existing = client.find_dashboard_by_slug(spec.dashboard.slug)
        if existing is None:
            _die({"stage": "absorb", "errors": [{"code": "not_found",
                  "detail": f"no live dashboard at slug {spec.dashboard.slug!r}; apply first"}]})
        detail = client.get(f"/api/v1/dashboard/{existing['id']}")["result"]
        live_position = json.loads(detail.get("position_json") or "{}")
        spec_data = json.loads(Path(args.spec).read_text(encoding="utf-8"))
        new_data, report = absorb_heights(spec, spec_data, live_position)
        # Serialize BEFORE any file side effect: a reporting failure must never
        # follow a silent mutation of the user's spec file.
        payload = report.to_json()
        if report.absorbed and not args.dry_run:
            Path(args.spec).write_text(
                json.dumps(new_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            # Feed the design brain's calibration loop (chartwright calibrate):
            # absorbed heights are ground truth about what humans actually want.
            from .design.calibrate import record_absorb

            record_absorb(args.profile, spec, report.absorbed)
        print(payload)
        sys.exit(0)

    if args.cmd == "decompile":
        client = _client(args.profile)
        from .decompile import decompile_live

        try:
            result = decompile_live(args.dashboard, client)
        except ValueError as e:
            _die({"stage": "decompile", "errors": [{"code": "decompile", "detail": str(e)}]})
        if args.output:
            Path(args.output).write_text(json.dumps(result.spec, indent=2) + "\n")
            print(json.dumps({"ok": True, "stage": "decompile", "output": args.output,
                              "losses": result.losses_json()}, indent=2))
        else:
            print(json.dumps({"spec": result.spec, "losses": result.losses_json()}, indent=2))
        return

    if args.cmd == "restore":
        try:
            blob = Path(args.bundle).read_bytes()
        except OSError as e:
            _die({"stage": "restore", "errors": [{"code": "unreadable_bundle", "detail": str(e)}]})
        # Only tool-owned bundles are restorable: same ownership rule as apply.
        import io
        import zipfile

        import yaml

        from . import ids

        zf = zipfile.ZipFile(io.BytesIO(blob))
        dash_files = [n for n in zf.namelist() if "/dashboards/" in n and n.endswith(".yaml")]
        if not dash_files:
            _die({"stage": "restore", "errors": [{"code": "bad_bundle", "detail": "no dashboard yaml in bundle"}]})
        dash = yaml.safe_load(zf.read(dash_files[0]))
        slug, u = dash.get("slug"), str(dash.get("uuid"))
        if not slug or u != str(ids.dashboard_uuid(slug)):
            _die({"stage": "restore", "errors": [{"code": "not_owned",
                  "detail": f"bundle dashboard (slug={slug!r}) is not owned by this tool; refusing to import"}]})
        client = _client(args.profile)
        from .apply import restore_bundle

        report = restore_bundle(blob, slug, client)
        print(report.to_json())
        sys.exit(0 if report.ok else 1)


def _standards_cmd(args) -> None:
    """`chartwright standards check | show | assign` (design/standards.py)."""
    from .design import standards as st

    def fail(e: st.StandardsError) -> None:
        _die({"stage": "standards", "ok": False, "errors": [e.as_dict()]})

    if args.standards_cmd == "show":
        if args.name and args.for_spec:
            fail(st.StandardsError("usage", "name a standard or pass --for SPEC, not both"))
        spec = _load(args.for_spec) if args.for_spec else None
        # Discovery starts in the spec's folder, or without --for in the working directory.
        source = st.StandardsSource.for_cli(args.standards, args.for_spec or Path.cwd() / "-")
        try:
            payload = st.show(source, args.name, spec, spec_label=args.for_spec or "")
        except st.StandardsError as e:
            fail(e)
        print(json.dumps(payload, indent=2) if args.json else st.render_show(payload),
              end="\n" if args.json else "")
        return

    try:
        paths = st.expand_specs(args.specs)
        if not paths:
            raise st.StandardsError("no_specs", f"no spec files in {args.specs}")
        source = st.source_for_specs(args.standards, paths)
        standards = source.load()
    except st.StandardsError as e:
        fail(e)

    if args.standards_cmd == "assign":
        try:
            payload = st.assign(paths, args.standard, standards)
        except st.StandardsError as e:
            fail(e)
        print(json.dumps(payload, indent=2))
        sys.exit(0 if payload["ok"] else 1)

    entries = []
    for p in paths:
        spec, err = st.load_spec_file(p)
        if err is not None:
            entries.append({"spec": str(p), "ok": False, "standard": None, "errors": [err]})
        else:
            entries.append({"spec": str(p), **st.check_spec(spec, source, strict=args.strict)})
    sdir = st.display(standards.directory)
    if args.report:
        payload = st.fleet_report(entries, strict=args.strict, standards_dir=sdir)
    else:
        passed = sum(1 for e in entries if e["ok"])
        payload = {"stage": "standards", "ok": passed == len(entries), "strict": args.strict,
                   "standards_dir": sdir, "specs": entries,
                   "totals": {"specs": len(entries), "passed": passed,
                              "failed": len(entries) - passed}}
    from .design.presets import overlay_path

    if overlay_path().exists():
        # Named, never read: a per-machine file must not decide a fleet check.
        payload["overlay"] = {"path": str(overlay_path()), "set_aside": True}
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    sys.exit(0 if payload["ok"] else 1)


if __name__ == "__main__":
    main()
