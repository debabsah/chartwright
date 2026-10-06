"""Live check of size.markdown-fit: a markdown block at the height `advise --fix` gives
it shows every line, and a block the rule calls cut off is.

Flow: each text of tools/record_markdown_fit.py's corpus at 6/12, and each block of
its styled dashboard under that dashboard's own CSS at its own width, at the height
the estimate's fix writes and a unit less. Each set on a dashboard of its own (slugs
cw-live-mdfit and cw-live-mdfit-css), opened in headless Chromium in a 1440 px wide
window with classic scrollbars on, as Windows draws them. At the fix's height no
block may scroll; a unit less, every block the rule warns about must have its text
cut off by a grid row or more, and every block it reports at all must scroll. The
dashboards are deleted at the end.

Needs the visual extra: pip install -e ".[visual]" && playwright install chromium.

    SDC_CI_PASSWORD=admin python tools/ci_live_markdown_fit.py --base-url http://host:8098

Exit codes: 0 pass, 1 failure.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

from chartwright.client import SupersetClient  # noqa: E402
from chartwright.design.markdown_fit import box_px, estimate  # noqa: E402
from record_markdown_fit import CORPUS, STYLED, STYLED_CSS, verify  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default=os.environ.get("SDC_CI_BASE_URL", "http://localhost:8098"))
    ap.add_argument("--username", default="admin")
    ap.add_argument("--prefix", default="cw-live", help="slug prefix of the dashboards")
    args = ap.parse_args(argv)
    password = os.environ.get("SDC_CI_PASSWORD", "admin")  # sandbox default; never in argv
    base = args.base_url.rstrip("/")
    client = SupersetClient(base, args.username, password)
    client.login()

    sets = {
        "": ([(name, text, 6) for name, text in CORPUS.items()], None),
        "-css": ([(name, text, width) for name, width, _, text in STYLED], STYLED_CSS),
    }
    failures: list[str] = []
    for suffix, (texts, css) in sets.items():
        fits = {name: estimate(text, width, css=css) for name, text, width in texts}
        blocks = []
        for name, text, width in texts:
            units = fits[name].units
            blocks += [(f"{name}@fix", text, width, units),
                       (f"{name}@short", text, width, round(max(0.2, units - 1), 1))]
        seen = verify(client, base, args.username, password, f"{args.prefix}-mdfit{suffix}",
                      blocks, css=css)
        for key, got in seen.items():
            name, kind = key.split("@")
            fit, box = fits[name], box_px(got["height"])
            expect = []
            if kind == "fix":
                expect.append(("shows every line", not got["scrolls"]))
            else:
                if fit.text_low - box >= 8:
                    expect.append(("has its text cut off", got["text"] - got["box"] >= 8))
                if fit.short(box):
                    expect.append(("scrolls", got["scrolls"]))
            for what, ok in expect:
                line = (f"{name} at {got['width']}/12, {got['height']:g} units {what} "
                        f"(box {got['box']} px, text to {got['text']} px)")
                print(("ok   " if ok else "FAIL ") + line)
                if not ok:
                    failures.append(line)
    print(f"{len(failures)} failure(s)" if failures else "markdown fit holds")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
