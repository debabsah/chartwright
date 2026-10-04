"""The standard.* rules: content a standard writes, checked against the spec
(docs/DESIGN-BRAIN.md sec.18, "Content").

They run in every advice run that applies a standard with content (`advise`,
`standards check`, and the advice `check` and `apply` carry), and nowhere else: without a
standards folder nothing here fires. None has a fix; `chartwright standards apply` writes
the content. Locks are recomputed from the standards files: a finding never trusts what
design.standard_written says about a locked item.
"""

from __future__ import annotations

import json
import re

from . import content as C
from .model import Finding, RuleContext, rule


def _analysis(ctx: RuleContext) -> C.Analysis | None:
    std = getattr(ctx, "standard", None)
    if std is None:
        return None
    cached = getattr(ctx, "_content_analysis", None)
    if cached is None:
        records = ctx.spec.design.standard_written if ctx.spec.design else {}
        if not C.has_content(std) and not records:
            cached = C.Analysis()
        else:
            cached = C.analyze(std, ctx.spec)
        ctx._content_analysis = cached
    return cached


def _show(value, slot: str) -> str:
    return C.describe(value, slot, width=60)


def _what(slot: str) -> str:
    return {"header": "header row", "footer": "footer row", "css": "CSS block",
            "label_colors": "label colour", "number_format": "number format"}.get(slot, slot)


@rule("standard.content-locked", "error",
      "content a standard locks is in the spec as the standard has it (standards apply "
      "writes it; a change goes through the standard's file)", since="6")
def content_locked(ctx: RuleContext):
    a = _analysis(ctx)
    if a is None:
        return
    for err in a.errors:
        if err.get("locked_by"):
            yield Finding("standard.content-locked", "error", None, err["item"],
                          f"{err['item']}: {err['detail']}; standards apply can't read the "
                          f"blocks {err['locked_by']} locks until the markers are fixed by hand")
    for d in a.decisions:
        if not d.locked_by or d.conforms:
            continue
        what, layer = _what(d.slot), d.layer
        expected = _show(d.item.value, d.slot)
        if d.state == "add":
            detail = (f"{d.id}: the {layer} standard's {what} {expected} is missing, and "
                      f"{d.locked_by} locks it; `chartwright standards apply` adds it")
        elif d.state == "refresh":
            detail = (f"{d.id}: holds an older version of the {layer} standard's {what}, "
                      f"which {d.locked_by} locks; `chartwright standards apply` refreshes "
                      f"it to {expected}")
        else:
            found = "missing" if d.found is None else f"found {_show(d.found, d.slot)}"
            detail = (f"{d.id}: differs from the {layer} standard's {what}, which "
                      f"{d.locked_by} locks: expected {expected}, {found}. "
                      f"`chartwright standards apply --locked` puts it back; a change goes "
                      f"through the {d.locked_by} standards file")
        yield Finding("standard.content-locked", "error", None, d.id, detail)


@rule("standard.content-stale", "warn",
      "content a standard wrote is current: standards apply would change nothing", since="6")
def content_stale(ctx: RuleContext):
    a = _analysis(ctx)
    if a is None:
        return
    for err in a.errors:
        if not err.get("locked_by"):
            yield Finding("standard.content-stale", "warn", None, err["item"],
                          f"{err['item']}: {err['detail']}; standards apply can't update the "
                          f"standard's blocks until the markers are fixed by hand")
    for d in a.decisions:
        if d.locked_by or d.state not in C.WRITES:
            continue
        what = _what(d.slot)
        if d.state == "add":
            detail = (f"{d.id}: the {d.layer} standard's {what} {_show(d.item.value, d.slot)} "
                      f"is not in the spec yet; `chartwright standards apply` adds it")
        elif d.state == "refresh":
            detail = (f"{d.id}: the {d.layer} standard changed this {what}; "
                      f"`chartwright standards apply` refreshes it to "
                      f"{_show(d.item.value, d.slot)}")
        else:
            detail = (f"{d.id}: the {d.layer} standard no longer has this {what}; "
                      f"`chartwright standards apply` removes it")
        yield Finding("standard.content-stale", "warn", None, d.id, detail)


@rule("standard.content-released", "info",
      "content a standard has that the author took over (edited or removed): the author's "
      "now, and standards apply leaves it alone", since="6")
def content_released(ctx: RuleContext):
    a = _analysis(ctx)
    if a is None:
        return
    for d in a.decisions:
        if d.locked_by or d.item is None or d.conforms:
            continue
        if d.state not in ("released", "deleted", "author", "tombstone", "held"):
            continue
        what = _what(d.slot)
        if d.state == "held":
            yield Finding("standard.content-released", "info", None, d.id,
                          f"{d.id}: the {d.layer} standard's certification details wait for "
                          f"dashboard.certified_by, which you removed; standards apply adds "
                          f"them once it is back")
            continue
        if d.found is None and d.slot in C.ROW_SLOTS:
            # A row has no identity beyond its content: changed and removed look alike.
            detail = (f"{d.id}: no row holds the {d.layer} standard's {what} as it was "
                      f"written, so you changed or removed it, and standards apply adds it "
                      f"no more")
        elif d.found is None:
            detail = (f"{d.id}: you removed the {d.layer} standard's {what}, so standards "
                      f"apply adds it no more")
        else:
            detail = (f"{d.id}: holds your {_show(d.found, d.slot)}, not the {d.layer} "
                      f"standard's {_show(d.item.value, d.slot)}; standards apply leaves it "
                      f"alone")
        if d.state == "tombstone":
            detail += (f" (delete its entry in design.standard_written to take the "
                       f"standard's again)")
        yield Finding("standard.content-released", "info", None, d.id, detail)


@rule("standard.classification", "error",
      "the dashboard's classification is one the standard lists", since="6")
def classification(ctx: RuleContext):
    std = getattr(ctx, "standard", None)
    if std is None or std.classifications is None:
        return
    value = ctx.spec.dashboard.classification
    if value is not None and value not in std.classifications:
        yield Finding("standard.classification", "error", None, "dashboard.classification",
                      f"classification {value!r} is not one {std.classifications_layer!r} "
                      f"lists: {', '.join(json.dumps(c) for c in std.classifications)}")


# Declarations that make an element invisible. CSS selectors are not a Superset
# contract (a class can mean another thing on another release), so this reads
# declarations, not selectors: any of these outside the locking layers' blocks can hide
# a locked row.
_HIDING = re.compile(
    r"(display\s*:\s*none|visibility\s*:\s*hidden|content-visibility\s*:\s*hidden"
    r"|opacity\s*:\s*0(?:\.0+)?\s*(?:[;}!]|$)|font-size\s*:\s*0(?:px|em|rem|%)?\s*(?:[;}!]|$))",
    re.I)


@rule("standard.css-hides", "warn",
      "no CSS outside the locking standard's own blocks hides elements while a standard "
      "locks header or footer rows", since="6")
def css_hides(ctx: RuleContext):
    a = _analysis(ctx)
    if a is None:
        return
    lockers = {d.locked_by for d in a.decisions
               if d.locked_by and d.slot in C.ROW_SLOTS and d.item is not None}
    if not lockers:
        return
    segments = a.segments
    if segments is None:   # the standard writes no CSS: read the spec's own
        try:
            segments = C.parse_css(ctx.spec.dashboard.css or "")
        except C.MarkerError:
            segments = [ctx.spec.dashboard.css or ""]
    std = ctx.standard
    # The locking layers and the layers above them own their blocks; anything else (a
    # lower layer's block, the author's CSS) is what could hide their rows.
    top = max(std.chain.index(l) for l in lockers)
    trusted = set(std.chain[:top + 1])
    for seg in segments:
        if isinstance(seg, C.Block):
            if seg.layer in trusted:
                continue
            text, source = seg.body, f"the {seg.layer} block"
        else:
            text, source = seg, "the dashboard's own CSS"
        for m in _HIDING.finditer(text):
            start = text.rfind("}", 0, m.start()) + 1
            brace = text.find("{", start, m.start())
            selector = " ".join(text[start:brace].split()) if brace >= 0 else ""
            yield Finding(
                "standard.css-hides", "warn", None, "dashboard.css",
                f"{source} has `{selector or '(a rule)'} {{ {m.group(1).strip()} }}`, which "
                f"can hide the "
                f"rows {', '.join(sorted(lockers))} locks; standards check can't tell what a "
                f"selector matches on a rendered dashboard, so look at it there")
