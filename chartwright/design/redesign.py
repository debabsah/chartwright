"""One-shot redesign: decompile -> audit -> safe fixes -> redesigned spec.

The core is client-free: it takes an already-decompiled spec and applies the
brain. Ownership decides where the result can land: a tool-born dashboard
redesigns in place (same slug, chart ids stable); a UI-born one gets a
`-redesign` slug so apply builds it side by side and the original is never
touched (the ownership guard would refuse the original slug anyway).

Structural findings (re-composition, chart-type swaps) stay findings: the
spec author acts on them between redesign and apply. Advice, not authority.
"""

from __future__ import annotations

import copy

from . import advise_and_fix


def redesign_spec(spec_data: dict, losses: list, *, owned: bool,
                  audience: str | None = None, ignore: tuple[str, ...] = (),
                  resolution=None, prober=None, overlay=None) -> tuple[dict, dict]:
    """Returns (redesigned spec data, report payload). Raises ValueError only
    for a broken design overlay; everything else is reported in the payload."""
    data = spec_data
    slug_changed = False
    if not owned:
        data = copy.deepcopy(spec_data)
        data["dashboard"]["slug"] += "-redesign"
        data["dashboard"]["title"] += " (redesigned)"
        slug_changed = True

    new_data, report = advise_and_fix(
        data, audience=audience, ignore=ignore,
        resolution=resolution, prober=prober, overlay=overlay)

    remaining = [f for f in report.findings if f.severity != "info"]
    payload = {
        "stage": "redesign",
        "ok": report.ok,
        "slug": new_data["dashboard"]["slug"],
        "slug_changed": slug_changed,
        "losses": losses,
        "advice": report.payload(),
        "next": (
            ("the original is not tool-owned; this spec applies SIDE BY SIDE under "
             f"slug {new_data['dashboard']['slug']!r}. " if slug_changed else "")
            + (f"{len(report.fixed)} geometry fix(es) applied. " if report.fixed else "")
            + (f"{len(remaining)} structural finding(s) remain: edit the spec for them, then apply."
               if remaining else "no structural findings: review the spec, then apply.")
        ),
    }
    return new_data, payload
