"""Waivers: recorded, expiring exceptions to what a standard locks
(docs/DESIGN-BRAIN.md sec.18, "Waivers").

`standards/waivers.yaml`, beside the standards files and never a standard itself:

    waivers:
      - spec: specs/ops/wallboard.json       # pins the dashboard; slug: may be added
        rule: layout.footer[acme][0]         # a rule id, or a content item's address
        owner: "@acme/data-platform"
        reason: The wallboard has no room for the legal row; legal agreed on 2026-09-30.
        expires: 2026-12-31                  # valid through that day
        layer: acme                          # optional: only what this layer locks

A waiver lets one dashboard deviate from one lock until it expires: a finding of a
locked rule, or a locked content item the spec doesn't hold as the standard has it.
It is the only way past a lock: a spec's design.ignore, --ignore and design.yaml
never silence one. The file belongs to whoever guards the standards (CODEOWNERS);
chartwright can't check who approved a change to it.

Expiry (the decision record's #5): an expired waiver fails `standards check` and
`advise` for the specs they were asked to check, so CI on a pull request fails only
for the dashboards it touches. Everywhere else (standards apply, and the check, apply
and plan that deploy) it still applies, with a warning, so an expiry never blocks a
deploy, a hotfix or a rollback. `standards check --report` lists every expired and
soon-to-expire waiver. `--as-of DATE` fixes the day a run reads expiry against.
"""

from __future__ import annotations

import datetime as dt
import difflib
import re
from dataclasses import dataclass
from pathlib import Path

WAIVERS_FILES = ("waivers.yaml", "waivers.yml")
ENTRY_KEYS = ("slug", "spec", "rule", "owner", "reason", "expires", "layer")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
# The rule that reports an expired waiver: never itself waivable.
EXPIRED_RULE = "standard.waiver-expired"
# How soon a waiver counts as expiring, in --report (--expiring-within changes it).
EXPIRING_DAYS = 30


@dataclass(frozen=True)
class Waiver:
    index: int                  # its place in the file, from 0
    rule: str                   # a canonical rule id, or a content item's address
    kind: str                   # "rule" | "item"
    owner: str
    reason: str
    expires: dt.date
    slug: str | None = None
    spec: str | None = None     # a spec path, relative to the folder holding standards/
    layer: str | None = None

    @property
    def target(self) -> str:
        return f"spec {self.spec}" if self.spec else f"slug {self.slug}"

    def expired(self, as_of: dt.date) -> bool:
        """Past its expiry date: a waiver is valid through the day it names."""
        return as_of > self.expires

    def days_left(self, as_of: dt.date) -> int:
        return (self.expires - as_of).days

    def status(self, as_of: dt.date) -> str:
        return "expired" if self.expired(as_of) else "active"

    def as_dict(self, as_of: dt.date | None = None) -> dict:
        out = {k: v for k, v in (("slug", self.slug), ("spec", self.spec)) if v}
        out.update(rule=self.rule, owner=self.owner, reason=self.reason,
                   expires=self.expires.isoformat())
        if self.layer:
            out["layer"] = self.layer
        if as_of is not None:
            out["status"] = self.status(as_of)
            out["days_left"] = self.days_left(as_of)
        return out

    def covers(self, rule: str, item: str | None, lock_layer: str | None) -> bool:
        """Whether this waiver covers a locked finding of `rule` (on content item `item`,
        if it is one) whose lock `lock_layer` set."""
        if self.layer is not None and self.layer != lock_layer:
            return False
        if self.kind == "rule":
            return self.rule == rule
        return item is not None and self.rule == item


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_date(value) -> dt.date | None:
    """A YAML date (2026-12-31) or the same as text, YYYY-MM-DD exactly; None for anything
    else. Python's fromisoformat also reads 20261231 and week dates (2026-W53-4), which no
    one writes as an expiry or an --as-of on purpose."""
    if isinstance(value, dt.datetime):
        return None
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str) and DATE_RE.match(value.strip()):
        try:
            return dt.date.fromisoformat(value.strip())
        except ValueError:
            return None
    return None


def parse_waivers(path: Path, standard_names, where: str) -> list[Waiver]:
    """The waivers file, validated. `standard_names`: the standards in the folder (a
    `layer`, and the layer in a row or CSS item's address, must name one). Raises
    StandardsError("waivers_file", ...) naming the entry."""
    import yaml

    from ..spec import standard_item_kind
    from .model import RULES, canonical_rule_id
    from .standards import StandardsError

    def fail(detail: str) -> None:
        raise StandardsError("waivers_file", f"{where}: {detail}")

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise StandardsError("waivers_file", f"{where} is not valid YAML: {e}") from e
    except (OSError, UnicodeDecodeError) as e:
        raise StandardsError("waivers_file", f"{where} could not be read: {e}") from e
    if data is None:
        return []
    if not isinstance(data, dict) or set(data) - {"waivers"}:
        fail("must be a mapping with one key, `waivers`, holding a list of waivers")
    entries = data.get("waivers") or []
    if not isinstance(entries, list):
        fail("`waivers` must be a list")
    names = set(standard_names)
    out: list[Waiver] = []
    seen: dict[tuple, int] = {}
    for i, raw in enumerate(entries):
        at = f"waivers[{i}]"
        if not isinstance(raw, dict):
            fail(f"{at} must be a mapping with slug (or spec), rule, owner, reason and expires")
        unknown = sorted(set(raw) - set(ENTRY_KEYS))
        if unknown:
            fail(f"{at}: unknown keys {unknown} (known: {list(ENTRY_KEYS)})")
        slug, spec = raw.get("slug"), raw.get("spec")
        if slug is None and spec is None:
            fail(f"{at} names its dashboard by spec path (preferred) or slug, or both")
        if slug is not None and not (isinstance(slug, str) and SLUG_RE.match(slug)):
            fail(f"{at}: slug {slug!r} is not a dashboard slug (lowercase kebab-case)")
        if spec is not None:
            if not isinstance(spec, str) or not spec.strip() or not spec.endswith(".json"):
                fail(f"{at}: spec {spec!r} must be a spec file's path, relative to the folder "
                     f"that holds the standards folder")
            spec = Path(spec.strip()).as_posix()
        for key in ("owner", "reason"):
            value = raw.get(key)
            if not isinstance(value, str) or not value.strip():
                fail(f"{at}: `{key}` is required: "
                     + ("who approved this exception, e.g. a CODEOWNERS team or a person"
                        if key == "owner" else "why this dashboard may deviate"))
        if "expires" not in raw:
            fail(f"{at}: `expires` is required: the last day the waiver holds, e.g. 2026-12-31")
        expires = parse_date(raw["expires"])
        if expires is None:
            fail(f"{at}: expires {raw['expires']!r} must be a date, YYYY-MM-DD")
        rule = raw.get("rule")
        if not isinstance(rule, str) or not rule.strip():
            fail(f"{at}: `rule` is required: a rule id (size.min-width) or a content item "
                 f"(layout.footer[org][0])")
        canon = canonical_rule_id(rule)
        if canon in RULES:
            if canon == EXPIRED_RULE:
                fail(f"{at}: {EXPIRED_RULE} reports expired waivers; no waiver covers it")
            kind, rule = "rule", canon
        else:
            found = standard_item_kind(rule)
            if found is None:
                near = difflib.get_close_matches(rule, list(RULES), n=1)
                hint = f" (did you mean {near[0]!r}?)" if near else ""
                fail(f"{at}: {rule!r} is no rule id and no content item{hint}; a content item "
                     f"is written as design.standard_written keys it, e.g. "
                     f"layout.footer[org][0] or dashboard.css[org]")
            item_kind, m = found
            layer_in_key = m.group(2) if item_kind == "row" else (
                m.group(1) if item_kind == "css" else None)
            if layer_in_key is not None and layer_in_key not in names:
                fail(f"{at}: {rule!r} names the layer {layer_in_key!r}, which no standards file "
                     f"names (standards: {sorted(names)})")
            kind = "item"
        layer = raw.get("layer")
        if layer is not None and (not isinstance(layer, str) or layer not in names):
            fail(f"{at}: layer {layer!r} names no standard (standards: {sorted(names)})")
        key = (slug, spec, rule, layer)
        if key in seen:
            fail(f"{at} repeats waivers[{seen[key]}]: one waiver per dashboard, rule and "
                 f"layer, so its expiry is never in doubt")
        seen[key] = i
        out.append(Waiver(i, rule, kind, raw["owner"].strip(), raw["reason"].strip(), expires,
                          slug=slug, spec=spec, layer=layer))
    return out


def waivers_path(directory: Path) -> Path | None:
    for name in WAIVERS_FILES:
        p = directory / name
        if p.is_file():
            return p
    return None


def _target(spec_path: Path | None, base: Path | None) -> str | None:
    if spec_path is None or base is None:
        return None
    try:
        return Path(spec_path).resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return None


def resolve_matches(waivers: list[Waiver], spec, spec_path: Path | None,
                    base: Path | None) -> tuple[list[Waiver], list[str]]:
    """(the waivers that name this spec, notes about how they matched).

    The trust boundary: a spec's path is the repository's, a slug is a field the spec's
    author edits in the same pull request. So a waiver with `spec` matches only the file
    at that path (relative to `base`, the folder holding the standards folder), and with
    `slug` too, only while that file keeps the slug; a slug-only waiver matches any spec
    with that slug, and is flagged wherever the path is known. A spec seen without a path
    (an MCP tool's) can only match by slug, and says so."""
    out: list[Waiver] = []
    notes: list[str] = []
    target = _target(spec_path, base)
    slug = spec.dashboard.slug
    for w in waivers:
        if w.spec is not None:
            if target is not None:
                if w.spec == target and (w.slug is None or w.slug == slug):
                    out.append(w)
                elif w.slug is not None and w.slug == slug:
                    notes.append(f"waivers[{w.index}] is for spec {w.spec} (slug {w.slug}); "
                                 f"this spec at {target} carries that slug too, so the "
                                 f"waiver does not apply to it")
            elif w.slug is not None and w.slug == slug:
                out.append(w)
                notes.append(f"waivers[{w.index}] matched by slug {slug} alone: no spec path "
                             f"here to confirm it is {w.spec}")
        elif w.slug == slug:
            out.append(w)
            if target is not None:
                notes.append(f"waivers[{w.index}] matches by slug {slug} alone, a field the "
                             f"spec's author can edit; pin it with spec: {target}")
    return out, notes


def matching(waivers: list[Waiver], spec, spec_path: Path | None, base: Path | None) -> list[Waiver]:
    """The waivers that name this spec (resolve_matches)."""
    return resolve_matches(waivers, spec, spec_path, base)[0]


def pick(waivers: list[Waiver], rule: str, item: str | None, lock_layer: str | None,
         as_of: dt.date) -> Waiver | None:
    """The waiver covering a locked finding: an active one first, else an expired one."""
    hits = [w for w in waivers if w.covers(rule, item, lock_layer)]
    active = [w for w in hits if not w.expired(as_of)]
    return (active or hits or [None])[0]


def file_summary(waivers: list[Waiver], as_of: dt.date, matched: dict[int, list[str]],
                 expiring_within: int = EXPIRING_DAYS) -> dict:
    """`standards check --report`'s waivers block: every waiver in the file, counted, with
    the expired ones, the ones expiring within `expiring_within` days, the ones naming no
    spec the run read, and the ones that matched more than one spec (`matched`: waiver
    index -> the specs it matched). Two specs can share a slug only by mistake or by
    design: a slug is the spec author's to edit, so a waiver matched twice is flagged."""
    expired = [w.as_dict(as_of) for w in waivers if w.expired(as_of)]
    soon = [w.as_dict(as_of) for w in waivers
            if not w.expired(as_of) and w.days_left(as_of) <= expiring_within]
    unmatched = [{**w.as_dict(as_of), "reason": "names no spec this run read"}
                 for w in waivers if not matched.get(w.index)]
    shared = [{**w.as_dict(as_of), "specs": sorted(matched[w.index])}
              for w in waivers if len(matched.get(w.index, ())) > 1]
    by_owner: dict[str, int] = {}
    by_rule: dict[str, int] = {}
    for w in waivers:
        by_owner[w.owner] = by_owner.get(w.owner, 0) + 1
        by_rule[w.rule] = by_rule.get(w.rule, 0) + 1
    return {"as_of": as_of.isoformat(), "total": len(waivers),
            "active": len(waivers) - len(expired), "expired": expired,
            "expiring_within_days": expiring_within, "expiring": soon,
            "unmatched": unmatched, "matched_more_than_once": shared,
            "by_owner": dict(sorted(by_owner.items())),
            "by_rule": dict(sorted(by_rule.items()))}


def past_dated(waivers: list[Waiver], as_of: dt.date) -> list[dict]:
    """Warnings for waivers already expired: an entry written with a past date waives
    nothing in the checks that enforce expiry."""
    return [{"code": "waiver_expired", "waiver": w.index,
             "detail": f"waivers[{w.index}] ({w.target}, {w.rule}) expired on "
                       f"{w.expires.isoformat()}; standards check fails for that dashboard "
                       f"until it is renewed or removed"}
            for w in waivers if w.expired(as_of)]
