"""CLI: the deterministic back-half the LLM front-end drives.

    chartwright schema                          print the JSON Schema the LLM must satisfy
    chartwright validate spec.json              schema-validate only (no network)
    chartwright compile spec.json -o out.zip    compile bundle with a stub resolution (golden/debug)
    chartwright check spec.json --profile P     pre-flight referential resolution
    chartwright apply spec.json --profile P     check -> compile -> import -> smoke
    chartwright brief                           the design brief to read BEFORE authoring a spec
    chartwright advise spec.json                design review (add --profile for data-aware rules)
    chartwright calibrate                       learn recommended heights from absorb history
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


def _die(payload: dict, code: int = 1) -> None:
    print(json.dumps(payload, indent=2, default=str))
    sys.exit(code)


def _advice_payload(spec, resolution=None) -> dict:
    """Advice riding along check/apply must never break the pipeline: a bad
    overlay degrades to an error note inside the advice block, not a crash."""
    from .design import advise

    try:
        return advise(spec, resolution=resolution).payload()
    except ValueError as e:
        return {"stage": "design", "ok": True, "design_brain": "1",
                "counts": {"error": 0, "warn": 0, "info": 0}, "findings": [],
                "fixed": [], "ignored": [],
                "errors": [{"code": "overlay", "detail": str(e)}]}


def _client(profile_name: str):
    from .client import SupersetClient
    from .profiles import load_profile, ProfileError

    try:
        p = load_profile(profile_name)
    except ProfileError as e:
        _die({"stage": "profile", "errors": [{"code": "profile", "detail": str(e)}]})
    c = SupersetClient(p.base_url, p.username, p.password, auth_provider=p.auth_provider,
                       ca_bundle=p.ca_bundle, verify=p.verify)
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
    ap = argparse.ArgumentParser(prog="chartwright", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("schema")

    v = sub.add_parser("validate")
    v.add_argument("spec")

    comp = sub.add_parser("compile")
    comp.add_argument("spec")
    comp.add_argument("-o", "--output", default=None)

    for name in ("check", "apply", "plan"):
        p = sub.add_parser(name)
        p.add_argument("spec")
        p.add_argument("--profile", required=True)
        if name != "plan":
            p.add_argument("--design", choices=["off", "warn", "strict"], default="warn",
                           help="design-brain advice: warn (report, default), strict (block), off")

    from .design.presets import AUDIENCE_NAMES

    adv = sub.add_parser("advise", help="design review of a spec against the design-brain rulebook")
    adv.add_argument("spec")
    adv.add_argument("--profile", default=None, help="enable data-aware rules (types, cardinality)")
    adv.add_argument("--audience", choices=AUDIENCE_NAMES, default=None)
    adv.add_argument("--fix", action="store_true", help="apply safe presentation-only fixes to the spec file")
    adv.add_argument("--strict", action="store_true", help="exit 1 on warnings, not just errors")
    adv.add_argument("--ignore", default=None, help="comma-separated rule ids to suppress")
    adv.add_argument("--no-probe", action="store_true", help="skip cardinality queries (metadata only)")

    br = sub.add_parser("brief", help="the design brief to read BEFORE authoring a spec")
    br.add_argument("--audience", choices=AUDIENCE_NAMES, default="analytical")

    cal = sub.add_parser("calibrate", help="propose recommended heights from absorb history")
    cal.add_argument("--write", action="store_true", help="record proposals in the design overlay")
    cal.add_argument("--min-samples", type=int, default=5)

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

        bundle = compile_bundle(spec, stub_resolution(spec))
        out = Path(args.output or f"{spec.dashboard.slug}.zip")
        out.write_bytes(bundle)
        print(json.dumps({"ok": True, "stage": "compile", "output": str(out), "bytes": len(bundle),
                          "note": "stub resolution (fake dataset ids); use apply for a real import"}))
        return

    if args.cmd == "check":
        spec = _load(args.spec)
        client = _client(args.profile)
        from .apply import check

        res = check(spec, client)
        payload = {"ok": res.ok, "stage": "resolve", "errors": [e.as_dict() for e in res.errors]}
        gate = False
        if args.design != "off":
            advice = _advice_payload(spec, resolution=res if res.ok else None)
            payload["advice"] = advice
            gate = args.design == "strict" and (advice["counts"]["error"] or advice["counts"]["warn"])
            if gate:
                payload["ok"] = False
        print(json.dumps(payload, indent=2))
        sys.exit(0 if res.ok and not gate else 1)

    if args.cmd == "apply":
        spec = _load(args.spec)
        advice = None
        if args.design != "off":
            # Pre-flight, offline (no probes: applies stay fast; data-aware
            # advice is `chartwright advise --profile`). Strict blocks BEFORE
            # anything on the instance is touched.
            advice = _advice_payload(spec)
            if args.design == "strict" and (advice["counts"]["error"] or advice["counts"]["warn"]):
                _die({"stage": "design", "ok": False, "advice": advice, "errors": [{
                    "code": "design_gate",
                    "detail": "design findings block under --design strict; fix them, "
                              "run `chartwright advise --fix`, or record deliberate "
                              "exceptions in the spec's design.ignore"}]})
        client = _client(args.profile)
        from .apply import apply as run_apply

        report = run_apply(spec, client, args.profile)
        out = json.loads(report.to_json())
        if advice is not None:
            out["advice"] = advice
        print(json.dumps(out, indent=2))
        sys.exit(0 if report.ok else 1)

    if args.cmd == "advise":
        spec = _load(args.spec)
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

        try:
            if args.fix:
                spec_data = json.loads(Path(args.spec).read_text(encoding="utf-8"))
                new_data, report = advise_and_fix(
                    spec_data, audience=args.audience, ignore=ignore,
                    resolution=resolution, prober=prober)
                if report.fixed:
                    Path(args.spec).write_text(
                        json.dumps(new_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            else:
                report = advise(spec, audience=args.audience, ignore=ignore,
                                resolution=resolution, prober=prober)
        except ValueError as e:
            _die({"stage": "design", "errors": [{"code": "overlay", "detail": str(e)}]})
        payload = report.payload()
        if resolution is not None and resolution.errors:
            payload["resolution_errors"] = [e.as_dict() for e in resolution.errors]
        print(json.dumps(payload, indent=2))
        sys.exit(1 if report.gate(args.strict) else 0)

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
            print(json.dumps(calibrate(min_samples=args.min_samples, write=args.write), indent=2))
        except ValueError as e:
            _die({"stage": "calibrate", "errors": [{"code": "overlay", "detail": str(e)}]})
        return

    if args.cmd == "plan":
        spec = _load(args.spec)
        client = _client(args.profile)
        from .dashdiff import plan as run_plan

        p = run_plan(spec, client)
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
        if report.absorbed and not args.dry_run:
            Path(args.spec).write_text(
                json.dumps(new_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            # Feed the design brain's calibration loop (chartwright calibrate):
            # absorbed heights are ground truth about what humans actually want.
            from .design.calibrate import record_absorb

            record_absorb(args.profile, spec, report.absorbed)
        print(report.to_json())
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


if __name__ == "__main__":
    main()
