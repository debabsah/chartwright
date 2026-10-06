"""Live check of size.markdown-fit: a markdown block at the height `advise --fix` gives
it shows every line, and a block the rule calls cut off is.

Flow: each text of tools/record_markdown_fit.py's corpus at 6/12, at the height the
estimate's fix writes and a unit less, on one dashboard (slug cw-live-mdfit), opened in
headless Chromium at a 1600 px viewport with classic scrollbars on, as Windows draws
them. At the fix's height no block may scroll; a unit less, every block the rule warns
about must have its text cut off by a grid row or more, and every block it reports at
all must scroll. The dashboard is deleted at the end.

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
from record_markdown_fit import CORPUS, verify  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default=os.environ.get("SDC_CI_BASE_URL", "http://localhost:8098"))
    ap.add_argument("--username", default="admin")
    ap.add_argument("--prefix", default="cw-live", help="slug prefix of the dashboard")
    args = ap.parse_args(argv)
    password = os.environ.get("SDC_CI_PASSWORD", "admin")  # sandbox default; never in argv
    base = args.base_url.rstrip("/")
    client = SupersetClient(base, args.username, password)
    client.login()

    fits = {name: estimate(text, 6) for name, text in CORPUS.items()}
    heights = []
    for name, fit in fits.items():
        heights += [(name, "fix", fit.units), (name, "short", round(max(0.2, fit.units - 1), 1))]
    seen = verify(client, base, args.username, password, f"{args.prefix}-mdfit", heights)
    failures: list[str] = []
    for key, got in seen.items():
        name, kind = key.split("@")
        fit, box = fits[name], box_px(got["height"])
        expect = []
        if kind == "fix":
            expect.append(("shows every line", not got["scrolls"]))
        else:
            if fit.text_low - box >= 8:
                expect.append(("has its text cut off", got["text"] - got["box"] >= 8))
            if fit.need_low > box:
                expect.append(("scrolls", got["scrolls"]))
        for what, ok in expect:
            line = f"{name} at {got['height']:g} units {what} (box {got['box']} px, text to {got['text']} px)"
            print(("ok   " if ok else "FAIL ") + line)
            if not ok:
                failures.append(line)
    print(f"{len(failures)} failure(s)" if failures else "markdown fit holds")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
