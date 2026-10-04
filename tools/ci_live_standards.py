"""Live check of standards content: what `standards apply` writes reaches Superset and
comes back unchanged.

Flow, for tests/fixtures/standards_live (a standards/ folder and a spec):
standards apply (in memory) -> apply to the instance -> plan clean -> decompile reads
the CSS back with its markers and every managed row as written -> the decompiled spec
holds the standard's content (apply would add nothing) and --claim rebuilds the record
-> re-apply -> plan clean again.

    SDC_CI_PASSWORD=admin python tools/ci_live_standards.py --base-url http://host:8098

Exit codes: 0 pass, 1 failure, 2 resolver gate (the spec's datasets don't exist on this
instance's example data).
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from chartwright.apply import apply as run_apply  # noqa: E402
from chartwright.client import SupersetClient  # noqa: E402
from chartwright.dashdiff import plan  # noqa: E402
from chartwright.decompile import decompile_live  # noqa: E402
from chartwright.design.content import row_hash  # noqa: E402
from chartwright.design.standards import StandardsSource, apply_spec  # noqa: E402
from chartwright.resolver import resolve  # noqa: E402
from chartwright.spec import load_spec  # noqa: E402

FIXTURE = REPO / "tests" / "fixtures" / "standards_live"


def standards_apply(data: dict, source: StandardsSource, **flags) -> tuple[dict, dict]:
    spec = load_spec(data)
    return apply_spec(data, spec, source.standard_for(spec), **flags)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default=os.environ.get("SDC_CI_BASE_URL", "http://localhost:8098"))
    ap.add_argument("--username", default="admin")
    ap.add_argument("--dir", default=str(FIXTURE), help="a folder with standards/ and spec.json")
    ap.add_argument("--slug", default=None, help="apply under this slug instead of the spec's")
    args = ap.parse_args(argv)
    password = os.environ.get("SDC_CI_PASSWORD", "admin")  # sandbox default; never in argv

    folder = Path(args.dir)
    source = StandardsSource(folder / "standards")
    data = json.loads((folder / "spec.json").read_text(encoding="utf-8"))
    if args.slug:
        data["dashboard"]["slug"] = args.slug
    failures: list[str] = []

    def check(ok: bool, what: str) -> None:
        print(("ok   " if ok else "FAIL ") + what)
        if not ok:
            failures.append(what)

    written, entry = standards_apply(data, source)
    check(not entry["errors"] and bool(entry["changes"]),
          f"standards apply wrote {len(entry['changes'])} items")
    again, entry2 = standards_apply(written, source)
    check(again == written and not entry2["changes"], "a second standards apply changes nothing")
    spec = load_spec(written)

    client = SupersetClient(args.base_url, args.username, password)
    client.login()
    res = resolve(spec, client)
    if not res.ok:
        print("RESOLVER GATE (spec does not fit this instance's datasets):")
        print(json.dumps([e.as_dict() for e in res.errors], indent=2))
        return 2

    r1 = run_apply(spec, client, "ci")
    check(r1.ok, f"apply ({r1.stage})")
    if not r1.ok:
        print(r1.to_json())
        return 1
    p1 = plan(spec, client)
    check(p1.clean, "plan is clean after apply")

    live = decompile_live(spec.dashboard.slug, client).spec
    check(live["dashboard"].get("css") == written["dashboard"]["css"],
          "decompile reads dashboard.css back as written, markers included")
    managed = {v["hash"] for k, v in written["design"]["standard_written"].items()
               if k.startswith("layout.") and v}
    back = {row_hash(r) for s in ("header", "footer") for r in live["layout"].get(s) or []}
    check(managed <= back, f"decompile reads all {len(managed)} managed rows back")

    # Decompile can't know the spec-only fields or the record; put back what the spec says.
    live = copy.deepcopy(live)
    live.setdefault("design", {})["standard"] = written["design"]["standard"]
    live["dashboard"]["classification"] = written["dashboard"]["classification"]
    _, held = standards_apply(live, source)
    check(not held["stale"] and not held["locked_stale"],
          "the decompiled spec holds every item: standards apply would add nothing")
    claimed, entry3 = standards_apply(live, source, claim=True)
    check(claimed["design"]["standard_written"] == written["design"]["standard_written"],
          "--claim on the decompiled spec rebuilds the record apply wrote")

    r2 = run_apply(spec, client, "ci")
    check(r2.ok, f"re-apply ({r2.stage})")
    check(plan(spec, client).clean, "plan is clean after re-apply")

    if failures:
        print(f"LIVE STANDARDS CHECK FAILED: {failures}")
        return 1
    print(f"LIVE STANDARDS CHECK PASS: {len(entry['changes'])} items written, round trip clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
