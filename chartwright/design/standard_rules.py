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
                          f"blocks {err['locked_by']} locks until the markers are fixed by hand",
                          lock_layer=err["locked_by"])
    for d in a.decisions:
        if not d.locked_by or d.conforms:
            continue
        what, layer = _what(d.slot), d.layer
        expected = _show(d.item.value, d.slot)
        waits = (d.id == "dashboard.certification_details" and d.found is None
                 and ctx.spec.dashboard.certified_by is None)
        if d.state == "held" or waits:
            detail = (f"{d.id}: the {layer} standard's certification details, which "
                      f"{d.locked_by} locks, wait for dashboard.certified_by, which the "
                      f"author removed; put it back, or `chartwright standards apply "
                      f"--locked` writes both if certified_by is locked too")
        elif d.state == "add":
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
            if d.near is not None:
                detail += (f". The row at layout.{d.slot}[{d.near}] has its shape but reads "
                           f"differently, so it stays as yours and --locked adds the "
                           f"standard's beside it")
        if d.id == "dashboard.classification":
            detail += (". The standard assigns this dashboard's classification; a dashboard "
                       "that needs another follows another standard, or gets a waiver in "
                       "standards/waivers.yaml")
        yield Finding("standard.content-locked", "error", None, d.id, detail,
                      lock_layer=d.locked_by)


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
    charts = {c.name: c for c in ctx.spec.charts}
    for d in a.decisions:
        if d.state == "forget" and d.slot == "number_format":
            name = d.id[len("charts["):-len("].number_format")]
            if name not in charts or "number_format" not in type(charts[name]).model_fields:
                yield Finding("standard.content-stale", "warn", None, d.id,
                              f"{d.id}: design.standard_written names a chart the spec no "
                              f"longer has (renamed or removed); `chartwright standards "
                              f"apply` drops the entry")
            continue
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


# CSS that makes an element invisible. CSS selectors are not a Superset contract (a class
# can mean another thing on another release), so this reads declarations, not
# selectors: any of these outside the locking layers' blocks can hide a locked row. It
# is a heuristic that knows the forms listed here; any other way to hide an element
# passes. The real lock on what readers see is a check of the rendered dashboard after
# deploy, which is not built yet (the decision record's #6, phase 4).
_RULE = re.compile(r"([^{}]*)\{([^{}]*)\}")
_OFFSET_PROPS = {"left", "right", "top", "bottom", "text-indent", "margin-left", "margin-top",
                 "margin-right", "margin-bottom"}


def _zero(value: str) -> bool:
    """A number that is zero, in any spelling and unit: 0, .0, 0.00, 0%, 0px."""
    m = re.fullmatch(r"([+-]?(?:\d+\.?\d*|\.\d+))(%|[a-z]+)?", value)
    return bool(m) and float(m.group(1)) == 0


def _hiding(decls: dict[str, str]) -> list[str]:
    """The declarations of one rule that hide its element: `prop: value` as written."""
    out = []
    for prop, value in decls.items():
        v = value.lower()
        if ((prop == "display" and v == "none")
                or (prop == "visibility" and v in ("hidden", "collapse"))
                or (prop == "content-visibility" and v == "hidden")
                or (prop in ("opacity", "font-size") and _zero(v))
                or (prop in ("color", "-webkit-text-fill-color") and v == "transparent")
                or (prop == "clip-path" and v != "none")
                or (prop == "clip" and v != "auto")
                or (prop == "transform" and re.search(
                    r"scale[xy]?\(\s*[+-]?(?:0+\.?0*|\.0+)\s*[,)]", v))
                or (prop in _OFFSET_PROPS and re.fullmatch(r"-\s*\d{4,}(\.\d*)?[a-z%]*", v))):
            out.append(f"{prop}: {value}")
    zero_height = [p for p in ("height", "max-height") if p in decls and _zero(decls[p].lower())]
    hidden = [p for p in ("overflow", "overflow-y") if decls.get(p, "").lower() in
              ("hidden", "clip")]
    if zero_height and hidden:
        out.append(f"{zero_height[0]}: {decls[zero_height[0]]}; {hidden[0]}: {decls[hidden[0]]}")
    return out


def hiding_rules(css: str) -> list[tuple[str, str]]:
    """(selector, declarations) of each rule in this CSS that hides its element, comments
    set aside."""
    out = []
    for m in _RULE.finditer(C.strip_comments(css)):
        selector = " ".join(m.group(1).split(";")[-1].split())
        decls = {}
        for part in m.group(2).split(";"):
            prop, sep, value = part.partition(":")
            if sep:
                value = re.sub(r"!\s*important\s*$", "", " ".join(value.split()), flags=re.I)
                decls[prop.strip().lower()] = value.strip()
        for found in _hiding(decls):
            out.append((selector or "(a rule)", found))
    return out


@rule("standard.css-hides", "warn",
      "CSS outside the locking layers' own blocks has no declaration known to hide "
      "elements while a standard locks header or footer rows (a heuristic: other ways "
      "to hide one pass)", since="6")
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
        for selector, found in hiding_rules(text):
            yield Finding(
                "standard.css-hides", "warn", None, "dashboard.css",
                f"{source} has `{selector} {{ {found} }}`, which can hide the rows "
                f"{', '.join(sorted(lockers))} locks; standards check can't tell what a "
                f"selector matches on a rendered dashboard, so look at it there")


@rule("standard.waiver-expired", "error",
      "no waiver naming this dashboard in standards/waivers.yaml has expired (checked by "
      "standards check and advise; a deploy warns instead)", since="6")
def waiver_expired(ctx: RuleContext):
    std = getattr(ctx, "standard", None)
    if std is None or not std.waivers or not std.enforce_expiry:
        return
    for w in std.waivers:
        if w.expired(std.as_of):
            yield Finding(
                "standard.waiver-expired", "error", None, f"waivers[{w.index}]",
                f"the waiver for {w.rule} on this dashboard ({w.target}) expired on "
                f"{w.expires.isoformat()} (owner: {w.owner}; reason: {w.reason}); the "
                f"finding it covered counts again. Renew or remove it in "
                f"standards/waivers.yaml, or bring the dashboard back to the standard")
