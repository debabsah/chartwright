"""Live check of `standards verify-visible`: locked text a reader can see passes, and CSS
that hides it, in a way standard.css-hides doesn't know, is caught.

Flow, for tests/fixtures/standards_live: standards apply (in memory) -> apply the spec
-> verify-visible passes; the same spec with author CSS that turns the locked footer
rows near-white, and again with CSS that indents them off the page, each applied under
its own slug -> verify-visible names both rows hidden.

Needs the visual extra: pip install -e ".[visual]" && playwright install chromium.

    SDC_CI_PASSWORD=admin python tools/ci_live_visible.py --base-url http://host:8098

Exit codes: 0 pass, 1 failure, 2 resolver gate (the spec's datasets don't exist here).
"""

from __future__ import annotations

import argparse
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
from chartwright.resolver import resolve  # noqa: E402
from chartwright.spec import load_spec  # noqa: E402

FIXTURE = REPO / "tests" / "fixtures" / "standards_live"
HIDING_CSS = '\n[id^="MARKDOWN-sdc-footer"] p { color: #fdfdfd; }'
# Pushes the glyphs out of an element that stays in place: caught only by measuring the
# text's own box, which the offline suite can't exercise without a browser.
INDENT_CSS = '\n[id^="MARKDOWN-sdc-footer"] p { text-indent: -9999px; overflow: hidden; }'


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default=os.environ.get("SDC_CI_BASE_URL", "http://localhost:8098"))
    ap.add_argument("--username", default="admin")
    ap.add_argument("--prefix", default="cw-live", help="slug prefix of the two dashboards")
    args = ap.parse_args(argv)
    password = os.environ.get("SDC_CI_PASSWORD", "admin")  # sandbox default; never in argv
    base = args.base_url.rstrip("/")

    source = StandardsSource(FIXTURE / "standards")
    data = json.loads((FIXTURE / "spec.json").read_text(encoding="utf-8"))
    std = source.standard_for(load_spec(data))
    data, _ = apply_spec(data, load_spec(data), std)
    client = SupersetClient(base, args.username, password)
    client.login()
    failures: list[str] = []

    def check(ok: bool, what: str) -> None:
        print(("ok   " if ok else "FAIL ") + what)
        if not ok:
            failures.append(what)

    for slug, css, want_visible in ((f"{args.prefix}-visible", "", True),
                                    (f"{args.prefix}-hidden", HIDING_CSS, False),
                                    (f"{args.prefix}-indented", INDENT_CSS, False)):
        variant = json.loads(json.dumps(data))
        variant["dashboard"]["slug"] = slug
        variant["dashboard"]["css"] += css
        spec = load_spec(variant)
        if not resolve(spec, client).ok:
            print("RESOLVER GATE (spec does not fit this instance's datasets)")
            return 2
        report = run_apply(spec, client, "ci")
        check(report.ok, f"{slug}: apply ({report.stage})")
        if not report.ok:
            print(report.to_json())
            return 1
        out = visible.verify(std, spec, base_url=base, username=args.username,
                             password=password, timeout_s=60 if want_visible else 30)
        if want_visible:
            check(out["ok"], f"{slug}: every locked row is visible "
                             f"({[(i['item'], i.get('reasons')) for i in out['items'] if not i['visible']]})")
        else:
            check(not out["ok"] and len(out["hidden"]) == 2,
                  f"{slug}: hiding CSS on the locked footer is caught ({out.get('hidden')})")
    if failures:
        print(f"LIVE VISIBLE CHECK FAILED: {failures}")
        return 1
    print("LIVE VISIBLE CHECK PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
