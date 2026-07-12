"""Params-drift checker: our emitted chart params vs the viz-plugin contract.

Superset's backend has no typed model of chart params; the frontend plugin
controlPanels are the only contract. tools/contracts/params-contract.json holds
the formData keys each of our viz_types accepts, extracted from plugin source
at each supported Superset tag (provenance: docs/CONTRACTS.md).

This tool compiles the kitchen-sink fixture offline (stub resolution) and
flags every emitted params key the target version's plugin does not declare.
Exit 1 on drift. Run it whenever the compiler's _chart_params changes, and
re-extract the contract JSON when a new Superset version is added.

Usage:
    python tools/params_drift.py [--version 6.1.0] [--all]
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import yaml

from chartwright.compiler import compile_bundle
from chartwright.spec import load_spec
from chartwright.testing import stub_resolution

CONTRACT = REPO / "tools" / "contracts" / "params-contract.json"
# Two fixtures: kitchen-sink covers every chart type; the demo spec exercises
# the conditional keys kitchen-sink leaves unset (big-number subtitle and
# number_format), which once hid an off-contract key from this checker.
FIXTURES = [
    REPO / "tests" / "fixtures" / "kitchen_sink.json",
    REPO / "examples" / "nyc_taxi_operations.json",
]

# Stored-in-params keys that are NOT plugin controlPanel controls, so they are
# legitimately absent from the contract:
ALLOWED = {
    "datasource",       # app-side injected (datasourceAndVizType section)
    "viz_type",         # app-side injected
    "slice_id",         # app-side injected
    "cache_timeout",    # app-side injected
    "url_params",       # app-side injected
    "custom_params",    # app-side injected
    "extra_form_data",  # runtime (native-filter) channel, never a control
    "time_range",       # query-time key; stored in params by exports; we pin
                        # "No filter" so the default window can't empty charts
    "granularity_sqla", # query-time key: the time binding that lets dashboard
                        # time filters reach charts without their own x_axis
    "sdc_categorical_bar",  # OUR marker: disambiguates categorical bar from
                            # timeseries bar (shared viz_type) for decompile;
                            # opaque to Superset (params is schemaless)
    "subheader",            # 6.1 renamed the control to `subtitle` but still
    "subheader_font_size",  # renders the legacy pair via transformProps
                            # fallback (BigNumberTotal/transformProps.ts:77-80
                            # at tag 6.1.0); 4.1.4/5.0.0 declare both natively
}


def emitted_keys_by_viz_type() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for fixture in FIXTURES:
        spec = load_spec(json.loads(fixture.read_text(encoding="utf-8")))
        bundle = compile_bundle(spec, stub_resolution(spec))
        zf = zipfile.ZipFile(io.BytesIO(bundle))
        for n in zf.namelist():
            if "/charts/" in n and n.endswith(".yaml"):
                cy = yaml.safe_load(zf.read(n))
                vt = cy.get("viz_type")
                out.setdefault(vt, set()).update((cy.get("params") or {}).keys())
    return out


def check(version: str, contract: dict, emitted: dict[str, set[str]]) -> list[str]:
    problems: list[str] = []
    per_version = contract[version]
    for vt, keys in sorted(emitted.items()):
        known = per_version.get(vt)
        if known is None:
            problems.append(f"{vt}: viz_type not in the {version} contract")
            continue
        if known == "ABSENT":
            problems.append(f"{vt}: viz_type ABSENT in Superset {version}")
            continue
        drift = keys - set(known) - ALLOWED
        if drift:
            problems.append(f"{vt}: keys unknown to {version}: {sorted(drift)}")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--version", default="6.1.0")
    ap.add_argument("--all", action="store_true", help="check every contract version")
    args = ap.parse_args(argv)

    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    emitted = emitted_keys_by_viz_type()
    versions = sorted(contract) if args.all else [args.version]

    failed = False
    for v in versions:
        problems = check(v, contract, emitted)
        if problems:
            failed = True
            print(f"DRIFT vs {v}:")
            for p in problems:
                print(f"  {p}")
        else:
            print(f"clean vs {v} ({len(emitted)} viz_types)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
