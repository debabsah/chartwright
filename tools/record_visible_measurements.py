"""Record what a real browser measures for locked text hidden in each known way, so
`standards verify-visible`'s verdict is tested offline against real measurements
(tests/test_visible.py reads tests/fixtures/visible/measurements.json).

For each instance: apply the standards_live spec once per author stylesheet below (slugs
cw-std4-rec-<variant>), run MEASURE_JS on its two locked footer lines in headless
Chromium, and store the raw measurements under the instance's release.

    pip install -e ".[visual]" && playwright install chromium
    SDC_CI_PASSWORD=admin python tools/record_visible_measurements.py \\
        --base-url http://localhost:8094 --base-url http://localhost:8098
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from chartwright import visible  # noqa: E402
from chartwright.apply import apply as run_apply  # noqa: E402
from chartwright.client import SupersetClient  # noqa: E402
from chartwright.design.standards import StandardsSource, apply_spec  # noqa: E402
from chartwright.spec import load_spec  # noqa: E402

FIXTURE = REPO / "tests" / "fixtures" / "standards_live"
OUT = REPO / "tests" / "fixtures" / "visible" / "measurements.json"
FOOT = '[id^="MARKDOWN-sdc-footer"]'
VARIANTS = {
    "visible": "",
    "display": f"{FOOT} {{ display: none; }}",
    "visibility": f"{FOOT} {{ visibility: hidden; }}",
    "nearwhite": f"{FOOT} p {{ color: #fdfdfd; }}",
    "darkband": f"{FOOT}, {FOOT} * {{ background: #101010 !important; }} {FOOT} p {{ color: #1c1c1c; }}",
    "opacity": f"{FOOT} {{ filter: opacity(0); }}",
    "overlay": (f"{FOOT} {{ position: relative; }} {FOOT}::after {{ content: \"\"; "
                f"position: absolute; inset: 0; background: #ffffff; z-index: 10; }}"),
    "offscreen": f"{FOOT} p {{ position: relative; left: 4000px; }}",
    "textindent": f"{FOOT} p {{ text-indent: -9999px; overflow: hidden; }}",
    "blur": f"{FOOT} p {{ filter: blur(4px); }}",
    "scale": f"{FOOT} p {{ transform: scale(0.05); }}",
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", action="append", required=True)
    ap.add_argument("--username", default="admin")
    args = ap.parse_args(argv)
    password = os.environ.get("SDC_CI_PASSWORD", "admin")  # sandbox default; never in argv

    record = json.loads(OUT.read_text()) if OUT.exists() else {"releases": {}}
    record["about"] = ("Measurements MEASURE_JS returned in headless Chromium for the "
                       "standards_live spec's two locked footer lines, under each author "
                       "stylesheet in css; written by tools/record_visible_measurements.py.")
    record["css"] = VARIANTS
    source = StandardsSource(FIXTURE / "standards")
    data = json.loads((FIXTURE / "spec.json").read_text(encoding="utf-8"))
    std = source.standard_for(load_spec(data))
    data, _ = apply_spec(data, load_spec(data), std)
    for base in args.base_url:
        base = base.rstrip("/")
        client = SupersetClient(base, args.username, password)
        client.login()
        release, measured = client.superset_version(), {}
        for name, css in VARIANTS.items():
            variant = json.loads(json.dumps(data))
            variant["dashboard"]["slug"] = f"cw-std4-rec-{name}"
            variant["dashboard"]["title"] = f"Recorded: {name}"
            variant["dashboard"]["css"] += f"\n{css}" if css else ""
            spec = load_spec(variant)
            report = run_apply(spec, client, "record")
            if not report.ok:
                print(report.to_json())
                return 1
            targets, _ = visible.targets(std, spec)
            facts = visible.measure(base, args.username, password, spec.dashboard.slug,
                                    [t.match for t in targets],
                                    timeout_s=60 if name == "visible" else 20)
            measured[name] = [dataclasses.asdict(f) for f in facts]
            print(release, name, [visible.judge(f) for f in facts], flush=True)
        record["releases"][release] = measured
    OUT.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
