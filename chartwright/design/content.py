"""Standards content: spec content a standard writes, and who owns it afterwards
(docs/DESIGN-BRAIN.md sec.18, "Content").

A standards file may carry content for a closed list of slots:

    content:
      header: [rows]                     # above every tab; additive per layer
      footer: [rows]                     # under every tab; additive per layer
      header_by_lifecycle: {deprecated: [rows]}         # banner rows per lifecycle state
      footer_by_classification: {confidential: [rows]}  # footer rows per classification
      css: |                             # one marked block per layer, author CSS after
        .dashboard-markdown { font-family: Inter; }
      color_scheme: supersetColors       # a scalar: the innermost layer wins
      certified_by: Data Platform
      certification_details: Reviewed every quarter
      label_colors: {Revenue: "#1FA8C9"} # per label: keys add up, the innermost wins a key
      number_format: {Revenue: "$,.0f"}  # per metric label; a chart takes it when every
                                         # metric it shows maps to the same format

`chartwright standards apply` writes it INTO each spec (compile never reads a standards
file), and records each item it wrote in design.standard_written: rows and CSS blocks by
hash, everything else by value. That record is the "value written" state machine of
design.filled (sec.16), per item:

- the spec holds the recorded value: the standard's, refreshed when the standard changes;
- an unlocked item holds another value: released to the author, the record dropped
  (a row or a CSS block the author removed or, for a row, changed: a null record, so it
  is never written again);
- an unlocked item the author deleted: a null record (a tombstone);
- a locked item that differs: a violation, reported by `standards check`, rewritten only
  by `standards apply --locked`.

Locks are recomputed from the standards files on every run; the record only tells the
standard's writes from the author's edits. Standards never write geometry or any field a
repair writes: they add or replace whole rows, a CSS block, a dashboard setting, a label
colour or a chart's number_format, which no repair touches (the markdown-height repair
leaves rows a standard owns alone).

Phase-4 plug points, not built yet: a standard's minimum Superset release filters
`expected_items` per instance, and the exceptions file is consulted where a locked
mismatch is decided (`_violation`).
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..spec import (CLASSIFICATION_PATTERN, GRID_WIDTH, LIFECYCLE_STATES, Row, metric_label,
                    row_items, standard_item_kind)

ROW_SLOTS = ("header", "footer")
SCALAR_SLOTS = ("color_scheme", "certified_by", "certification_details")
KEYED_SLOTS = ("label_colors", "number_format")
CONTENT_KEYS = ("header", "footer", "header_by_lifecycle", "footer_by_classification", "css",
                "color_scheme", "certified_by", "certification_details", "label_colors",
                "number_format")
# The pivot aggregations that keep a metric in its own unit, so its format still fits.
_UNIT_AGGREGATES = {"Sum", "Average", "Median", "Minimum", "Maximum", "First", "Last"}
# Charts with one number_format for every metric they show (the count-format fill's set).
NUMBER_FORMAT_TYPES = frozenset({
    "big_number_total", "big_number_trend", "timeseries_line", "timeseries_bar",
    "timeseries_area", "timeseries_scatter", "bar", "pie", "pivot_table", "heatmap", "funnel",
    "treemap"})

_MARK = re.compile(r"/\* cw:(std|end) ([A-Za-z0-9][A-Za-z0-9_-]*)(?: ([0-9a-f]{12}))? \*/")


def content_hash(value) -> str:
    """12 hex digits of SHA-256 over a row's canonical JSON or a CSS block's text."""
    text = value if isinstance(value, str) else json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


_ADAPTER = None


def _row_adapter():
    global _ADAPTER
    if _ADAPTER is None:
        from pydantic import TypeAdapter

        _ADAPTER = TypeAdapter(Row)
    return _ADAPTER


def canonical_row(row) -> Any:
    """A row (a model, or raw JSON) as the spec stores it: validated, then dumped with
    only the keys written. Standard rows are written in this form, and spec rows are
    hashed in it, so a row the standard wrote always hashes the same."""
    adapter = _row_adapter()
    model = row if not isinstance(row, (dict, list)) else adapter.validate_python(row)
    return adapter.dump_python(model, mode="json", exclude_unset=True)


# -- parsing a standards file's content ------------------------------------------


def _rows(raw, where: str) -> list:
    from pydantic import ValidationError

    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{where} must be a non-empty list of layout rows")
    out = []
    for i, row in enumerate(raw):
        try:
            model = _row_adapter().validate_python(row)
        except ValidationError as e:
            raise ValueError(f"{where}[{i}] is not a layout row: "
                             f"{e.errors()[0]['msg']}") from None
        items = row_items(model)
        if items is not None:
            if any(isinstance(x, str) for x in items):
                raise ValueError(
                    f"{where}[{i}] names a chart; a standard's rows hold markdown blocks, "
                    f"headers and dividers only, since it can't know a dashboard's charts")
            explicit = sum(x.width or 0 for x in items)
            implicit = sum(1 for x in items if x.width is None)
            if explicit > GRID_WIDTH or (implicit and GRID_WIDTH - explicit < implicit):
                raise ValueError(f"{where}[{i}]: widths in a row must sum to at most "
                                 f"{GRID_WIDTH}")
        out.append(canonical_row(model))
    return out


def _text(raw, where: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{where} must be a non-empty string")
    return raw


def parse_content(raw, where: str) -> dict:
    """A standards file's `content`, validated and in the form specs store. ValueError
    names the file and key."""
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: content must be a mapping")
    unknown = sorted(set(raw) - set(CONTENT_KEYS))
    if unknown:
        raise ValueError(f"{where}: content: unknown keys {unknown} (known: "
                         f"{list(CONTENT_KEYS)})")
    out: dict = {}
    for slot in ROW_SLOTS:
        if slot in raw:
            out[slot] = _rows(raw[slot], f"{where}: content.{slot}")
    if "header_by_lifecycle" in raw:
        block = raw["header_by_lifecycle"]
        if not isinstance(block, dict) or not block:
            raise ValueError(f"{where}: content.header_by_lifecycle must map a lifecycle state "
                             f"to rows")
        bad = sorted(set(block) - set(LIFECYCLE_STATES))
        if bad:
            raise ValueError(f"{where}: content.header_by_lifecycle: unknown states {bad} "
                             f"(known: {list(LIFECYCLE_STATES)})")
        out["header_by_lifecycle"] = {
            s: _rows(block[s], f"{where}: content.header_by_lifecycle.{s}")
            for s in LIFECYCLE_STATES if s in block}
    if "footer_by_classification" in raw:
        block = raw["footer_by_classification"]
        if not isinstance(block, dict) or not block:
            raise ValueError(f"{where}: content.footer_by_classification must map a "
                             f"classification to rows")
        for c in block:
            if not isinstance(c, str) or not re.match(CLASSIFICATION_PATTERN, c):
                raise ValueError(f"{where}: content.footer_by_classification: {c!r} is not a "
                                 f"classification (letters, digits, spaces, '-', '_', '.')")
        out["footer_by_classification"] = {
            c: _rows(block[c], f"{where}: content.footer_by_classification.{c}")
            for c in sorted(block)}
    if "css" in raw:
        css = _text(raw["css"], f"{where}: content.css").strip()
        if "cw:std" in css or "cw:end" in css:
            raise ValueError(f"{where}: content.css holds a cw:std or cw:end marker; "
                             f"standards apply writes the markers itself")
        out["css"] = css
    for slot in SCALAR_SLOTS:
        if slot in raw:
            out[slot] = _text(raw[slot], f"{where}: content.{slot}")
    if "label_colors" in raw:
        block = raw["label_colors"]
        if not isinstance(block, dict) or not block:
            raise ValueError(f"{where}: content.label_colors must map a series label to a "
                             f"#RRGGBB colour")
        for k, v in block.items():
            if not isinstance(k, str) or not k or not isinstance(v, str) \
                    or not re.fullmatch(r"#[0-9A-Fa-f]{6}", v):
                raise ValueError(f"{where}: content.label_colors[{k!r}] must be #RRGGBB, "
                                 f"got {v!r}")
        out["label_colors"] = dict(block)
    if "number_format" in raw:
        block = raw["number_format"]
        if not isinstance(block, dict) or not block:
            raise ValueError(f"{where}: content.number_format must map a metric label to a "
                             f"d3 format")
        for k, v in block.items():
            if not isinstance(k, str) or not k or not isinstance(v, str) or not v:
                raise ValueError(f"{where}: content.number_format[{k!r}] must be a d3 format "
                                 f"string, e.g. '$,.0f'")
        out["number_format"] = dict(block)
    return out


def parse_classifications(raw, where: str) -> list[str]:
    if (not isinstance(raw, list) or not raw
            or not all(isinstance(c, str) and re.match(CLASSIFICATION_PATTERN, c) for c in raw)):
        raise ValueError(f"{where}: classifications must be a list of words (letters, digits, "
                         f"spaces, '-', '_', '.'), e.g. [public, internal, confidential]")
    if len(set(raw)) != len(raw):
        raise ValueError(f"{where}: classifications lists a value twice")
    return list(raw)


def parse_content_locks(raw, where: str) -> list[str]:
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
        raise ValueError(f"{where}: locked.content must be a list of content slots")
    bad = sorted(set(raw) - set(CONTENT_KEYS))
    if bad:
        raise ValueError(f"{where}: locked.content: unknown slots {bad} (known: "
                         f"{list(CONTENT_KEYS)})")
    return list(raw)


# -- resolution through extends --------------------------------------------------


class LockError(ValueError):
    """A lower layer sets content a layer above it locks."""


def _slot_has(content: dict, slot: str) -> bool:
    return bool(content.get(slot))


def check_layer(name: str, where: str, content: dict, locks: list[str],
                classifications: list[str] | None, std) -> None:
    """Checks one layer's content against the layers above it (std: the Standard so far,
    with content_layers, content_locks and classifications). ValueError names the layer."""
    above = std.content_layers
    # A lower layer can't override what a layer above it locked: a scalar, or a key of
    # label_colors or number_format set at or above the locking layer.
    for slot, lockers in std.content_locks.items():
        top = max(std.chain.index(l) for l in lockers)
        if slot in SCALAR_SLOTS and slot in content:
            raise LockError(f"{where}: content.{slot} is locked by {lockers[0]!r}; only "
                             f"{lockers[0]!r} (or a layer above it) sets it")
        if slot in KEYED_SLOTS and slot in content:
            locked_keys = {k for layer, c in above[:top + 1] for k in c.get(slot, {})}
            clash = sorted(set(content[slot]) & locked_keys)
            if clash:
                raise LockError(f"{where}: content.{slot} sets {clash}, which "
                                 f"{lockers[0]!r} locks; a lower layer may add keys, not "
                                 f"change locked ones")
    # Lock a value, not a slot (sec.18): the slot has content by the locking layer.
    for slot in locks:
        if not (_slot_has(content, slot) or any(_slot_has(c, slot) for _, c in above)):
            raise ValueError(f"{where}: locks content.{slot} without a value; set content."
                             f"{slot} in this file or one it extends")
    if classifications is not None and std.classifications is not None:
        extra = sorted(set(classifications) - set(std.classifications))
        if extra:
            raise LockError(f"{where}: classifications adds {extra} to the list "
                             f"{std.classifications_layer!r} declares; a lower layer may "
                             f"only narrow it")
    allowed = classifications if classifications is not None else std.classifications
    if allowed is not None:
        stray = sorted(set(content.get("footer_by_classification", {})) - set(allowed))
        if stray:
            raise ValueError(f"{where}: content.footer_by_classification names {stray}, "
                             f"which the classifications list {allowed} lacks")


def check_resolved(std) -> None:
    merged = {}
    for _, c in std.content_layers:
        merged.update({s: c[s] for s in SCALAR_SLOTS if s in c})
    if "certification_details" in merged and "certified_by" not in merged:
        raise ValueError(f"standard {std.name!r} sets content.certification_details without "
                         f"content.certified_by; Superset shows the details only on a "
                         f"certified dashboard")


def has_content(std) -> bool:
    return std is not None and any(c for _, c in std.content_layers)


# -- what a standard expects in one spec -----------------------------------------


@dataclass
class Item:
    """One item of content a standard expects in a spec."""

    id: str              # its design.standard_written key
    slot: str            # header | footer | css | a scalar | label_colors | number_format
    layer: str
    value: Any           # canonical row, CSS body, or string
    locked_by: str | None = None
    key: str | None = None   # the label, or the chart's name
    when: str | None = None  # "lifecycle=deprecated", "classification=confidential"

    @property
    def by(self) -> str:
        return "hash" if self.slot in ("header", "footer", "css") else "value"

    @property
    def stamp(self) -> str:
        return content_hash(self.value) if self.by == "hash" else self.value

    @property
    def record(self) -> dict:
        return {"layer": self.layer, self.by: self.stamp}


def _locked_by(std, slot: str, index: int) -> str | None:
    """The highest layer at or below `index` (root first) that locks `slot`: a lock covers
    the locking layer's own items and those of the layers above it."""
    lockers = [std.chain.index(l) for l in std.content_locks.get(slot, [])]
    at = [i for i in lockers if i >= index]
    return std.chain[min(at)] if at else None


def chart_number_format(std, chart) -> tuple[str, int] | None:
    """(format, index of the innermost layer it comes from) for a chart, or None: the
    chart must have one number_format, every metric it shows must have the same format
    in the standard, and it must not plot shares (contribution, a 100 % stack) or a pivot
    aggregation that changes the unit."""
    if chart.type not in NUMBER_FORMAT_TYPES:
        return None
    if getattr(chart, "contribution", None) or getattr(chart, "stack", None) == "expand":
        return None
    if chart.type == "pivot_table" and chart.aggregate_function not in _UNIT_AGGREGATES:
        return None
    merged: dict[str, tuple[str, int]] = {}
    for i, (_, c) in enumerate(std.content_layers):
        for k, v in c.get("number_format", {}).items():
            merged[k] = (v, i)
    shown = [chart.metric] if hasattr(chart, "metric") else list(chart.metrics)
    found = [merged.get(metric_label(m)) for m in shown]
    if not found or any(f is None for f in found) or len({f[0] for f in found}) != 1:
        return None
    return found[0][0], max(f[1] for f in found)


def expected_items(std, spec) -> list[Item]:
    """Every item the standard expects in this spec, each slot in its canonical order:
    header rows root layer first (each layer's lifecycle banner, then its rows), footer
    rows innermost layer first (so the org's legal row sits at the very bottom), CSS
    blocks root first. Phase 4 holds content back here per instance release."""
    layers = std.content_layers
    lifecycle = spec.dashboard.lifecycle
    state = lifecycle.state if lifecycle else "active"
    cls = spec.dashboard.classification
    items: list[Item] = []
    for k, (layer, c) in enumerate(layers):
        for n, row in enumerate(c.get("header_by_lifecycle", {}).get(state, [])):
            items.append(Item(f"layout.header[{layer}][lifecycle={state}][{n}]", "header",
                              layer, row, _locked_by(std, "header_by_lifecycle", k),
                              when=f"lifecycle={state}"))
        for n, row in enumerate(c.get("header", [])):
            items.append(Item(f"layout.header[{layer}][{n}]", "header", layer, row,
                              _locked_by(std, "header", k)))
    for k in reversed(range(len(layers))):
        layer, c = layers[k]
        for n, row in enumerate(c.get("footer", [])):
            items.append(Item(f"layout.footer[{layer}][{n}]", "footer", layer, row,
                              _locked_by(std, "footer", k)))
        if cls is not None:
            for n, row in enumerate(c.get("footer_by_classification", {}).get(cls, [])):
                items.append(Item(f"layout.footer[{layer}][classification={cls}][{n}]",
                                  "footer", layer, row,
                                  _locked_by(std, "footer_by_classification", k),
                                  when=f"classification={cls}"))
    for k, (layer, c) in enumerate(layers):
        if "css" in c:
            items.append(Item(f"dashboard.css[{layer}]", "css", layer, c["css"],
                              _locked_by(std, "css", k)))
    for slot in SCALAR_SLOTS:
        at = [k for k, (_, c) in enumerate(layers) if slot in c]
        if at:
            k = at[-1]
            items.append(Item(f"dashboard.{slot}", slot, layers[k][0], layers[k][1][slot],
                              _locked_by(std, slot, k)))
    labels: dict[str, tuple[str, int]] = {}
    for k, (_, c) in enumerate(layers):
        for key, v in c.get("label_colors", {}).items():
            labels[key] = (v, k)
    for key in sorted(labels):
        v, k = labels[key]
        items.append(Item(f"dashboard.label_colors[{key}]", "label_colors", layers[k][0], v,
                          _locked_by(std, "label_colors", k), key=key))
    for chart in spec.charts:
        found = chart_number_format(std, chart)
        if found is None:
            continue
        fmt, k = found
        items.append(Item(f"charts[{chart.name}].number_format", "number_format",
                          layers[k][0], fmt, _locked_by(std, "number_format", k),
                          key=chart.name))
    return items


# -- dashboard.css blocks --------------------------------------------------------


class MarkerError(ValueError):
    """dashboard.css has cw:std/cw:end markers standards apply can't read."""


@dataclass
class Block:
    layer: str
    stamp: str   # the hash in the opening marker
    body: str


def parse_css(css: str) -> list:
    """The CSS as segments: plain text (str) and marked blocks (Block), in order, so that
    joining render_segment over them gives the text back exactly. A block is
    `/* cw:std LAYER HASH */`, a newline, the body, a newline, `/* cw:end LAYER */`.
    Raises MarkerError for an opening marker without its end, an end without its opening,
    nested blocks, or a layer's second block."""
    out: list = []
    pos = 0
    open_at = None
    seen: set[str] = set()
    for m in _MARK.finditer(css):
        kind, layer, stamp = m.group(1), m.group(2), m.group(3)
        if kind == "std":
            if open_at is not None:
                raise MarkerError(f"the {open_at[1]} block opens again inside itself; close "
                                  f"it with /* cw:end {open_at[1]} */")
            if stamp is None:
                raise MarkerError(f"the cw:std {layer} marker has no hash")
            if layer in seen:
                raise MarkerError(f"two {layer} blocks; keep one")
            if m.start() > pos:
                out.append(css[pos:m.start()])
            open_at = (m, layer, stamp)
        else:
            if open_at is None or open_at[1] != layer:
                raise MarkerError(f"/* cw:end {layer} */ closes no {layer} block")
            body = css[open_at[0].end():m.start()]
            if body.startswith("\n"):
                body = body[1:]
            if body.endswith("\n"):
                body = body[:-1]
            out.append(Block(layer, open_at[2], body))
            seen.add(layer)
            pos = m.end()
            open_at = None
    if open_at is not None:
        raise MarkerError(f"the {open_at[1]} block has no /* cw:end {open_at[1]} */")
    if pos < len(css):
        out.append(css[pos:])
    return out


def render_block(layer: str, body: str) -> str:
    return f"/* cw:std {layer} {content_hash(body)} */\n{body}\n/* cw:end {layer} */"


def render_segment(seg) -> str:
    if isinstance(seg, str):
        return seg
    return f"/* cw:std {seg.layer} {seg.stamp} */\n{seg.body}\n/* cw:end {seg.layer} */"


def join_css(segments: list) -> str:
    return "".join(render_segment(s) for s in segments)


# -- what the spec holds, item by item -------------------------------------------


MISSING = object()

# Decision states. A write is a change to the spec's content; the rest touch at most the
# record.
WRITES = ("add", "refresh", "remove")


@dataclass
class Decision:
    id: str
    slot: str
    layer: str
    state: str           # current | add | refresh | remove | unrecorded | released |
    #                      deleted | author | tombstone | forget
    item: Item | None    # None: the standard no longer has this recorded item
    found: Any = None    # what the spec holds (a row, a CSS body, a string), or None
    record: Any = MISSING  # the new record: MISSING keeps it, None writes null, "drop"
    #                        drops it, a dict writes it
    at: int | None = None  # rows: the spec row this item is (or the edited copy, for a
    #                        released row); None when absent
    unmarked: tuple[int, int] | None = None  # css: the standard's text, found unmarked
    #                                          (segment, offset)

    @property
    def locked_by(self) -> str | None:
        return self.item.locked_by if self.item else None

    @property
    def conforms(self) -> bool:
        """The spec holds the standard's current value for this item."""
        if self.item is None:
            return True
        return self.state in ("current", "unrecorded") or (
            self.state == "tombstone" and self.found is not None
            and _same(self.item, self.found))

    @property
    def violation(self) -> bool:
        """A locked item the author changed or removed: only --locked rewrites it."""
        return (self.locked_by is not None and not self.conforms
                and self.state not in ("add", "refresh"))


def _same(item: Item, value) -> bool:
    if item.by == "hash":
        return content_hash(value) == item.stamp
    return value == item.value


@dataclass
class Analysis:
    decisions: list[Decision] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)   # {"item", "detail"}
    segments: list | None = None    # dashboard.css parsed, when it parsed


def _spec_view(spec) -> dict:
    return spec.model_dump(mode="json", exclude_unset=True)


def _records(spec) -> dict:
    return dict(spec.design.standard_written) if spec.design else {}


def analyze(std, spec) -> Analysis:
    """Every item's state in this spec: what the standard expects against what the spec
    holds and what design.standard_written records. Never mutates anything."""
    view = _spec_view(spec)
    records = _records(spec)
    expected = expected_items(std, spec)
    out = Analysis()
    for slot in ROW_SLOTS:
        _rows_slot(out, slot, view, [i for i in expected if i.slot == slot], records)
    _css_slot(out, view, [i for i in expected if i.slot == "css"], records)
    dash = view["dashboard"]
    filled = (spec.design.filled if spec.design else {})
    charts = {c["name"]: c for c in view["charts"]}

    def scalar_value(item_id: str):
        kind, m = standard_item_kind(item_id)
        if kind == "scalar":
            return dash.get(m.group(1))
        if kind == "label":
            return (dash.get("label_colors") or {}).get(m.group(1))
        value = (charts.get(m.group(1)) or {}).get("number_format")
        brain = filled.get(m.group(1), {})
        if "number_format" in brain:
            # The brain's own fill, or its tombstone over an unset field, gives way to the
            # standard: one owner per field. A fill the author edited is the author's.
            if brain["number_format"] is None and value is None:
                return None
            if brain["number_format"] is not None and value == brain["number_format"]:
                return None
        return value

    scalar_items = [i for i in expected if i.by == "value"]
    for item in scalar_items:
        _scalar(out, item, scalar_value(item.id), records.get(item.id, MISSING))
    seen = {i.id for i in expected}
    for item_id, rec in sorted(records.items()):
        kind, _ = standard_item_kind(item_id)
        if item_id in seen or kind in ("row", "css"):
            continue
        current = scalar_value(item_id)
        slot = _slot_of(item_id)
        layer = rec["layer"] if rec else ""
        if rec is not None and current == rec["value"]:
            out.decisions.append(Decision(item_id, slot, layer, "remove", None,
                                          found=current, record="drop"))
        else:
            out.decisions.append(Decision(item_id, slot, layer, "forget", None,
                                          found=current, record="drop"))
    return out


def _slot_of(item_id: str) -> str:
    kind, m = standard_item_kind(item_id)
    return {"row": lambda: m.group(1), "css": lambda: "css", "scalar": lambda: m.group(1),
            "label": lambda: "label_colors", "number_format": lambda: "number_format"}[kind]()


def _scalar(out: Analysis, item: Item, current, rec) -> None:
    d = Decision(item.id, item.slot, item.layer, "current", item, found=current)
    if current is None:
        if rec is MISSING:
            d.state, d.record = "add", item.record
        elif rec is None:
            d.state = "tombstone"
        else:
            d.state, d.record = "deleted", None
    elif rec is MISSING:
        d.state = "unrecorded" if current == item.value else "author"
    elif rec is None:
        # Written after deleting: the author's, and the null record goes (as in fills).
        d.state = "unrecorded" if current == item.value else "author"
        d.record = "drop"
    elif current == rec["value"]:
        if current != item.value:
            d.state, d.record = "refresh", item.record
        elif rec != item.record:
            d.record = item.record             # a layer renamed or moved
    elif current == item.value:
        d.record = item.record                 # the author caught up with the standard
    else:
        d.state, d.record = "released", "drop"
    out.decisions.append(d)


def _rows_slot(out: Analysis, slot: str, view: dict, expected: list[Item],
               records: dict) -> None:
    rows = (view.get("layout") or {}).get(slot) or []
    hashes = [content_hash(r) for r in rows]
    prefix = f"layout.{slot}["
    recs = {k: v for k, v in records.items() if k.startswith(prefix)}
    claimed: dict[int, str] = {}
    located: dict[str, int] = {}

    def take(item_id: str, stamp: str) -> int | None:
        for i, h in enumerate(hashes):
            if h == stamp and i not in claimed:
                claimed[i] = item_id
                return i
        return None

    ids = [i.id for i in expected] + sorted(k for k in recs if k not in {i.id for i in expected})
    for item_id in ids:
        rec = recs.get(item_id)
        if rec:
            at = take(item_id, rec["hash"])
            if at is not None:
                located[item_id] = at
    matched: dict[str, int] = {}
    for item in expected:
        if item.id not in located:
            at = take(item.id, item.stamp)
            if at is not None:
                matched[item.id] = at
    present = {**located, **matched}

    def gap_copy(k: int) -> int | None:
        """The row that is this item, edited: between the item's present neighbours, not
        any other item's, and of the same shape as the standard's row (the same kind,
        background, and item widths and heights; only text differs). Exactly one such row,
        or none: a row has no identity beyond its content, so anything less certain is
        read as removed."""
        before = [present[e.id] for e in expected[:k] if e.id in present]
        after = [present[e.id] for e in expected[k + 1:] if e.id in present]
        lo, hi = (max(before) + 1 if before else 0), (min(after) if after else len(rows))
        want = row_shape(expected[k].value)
        free = [i for i in range(lo, hi) if i not in claimed and row_shape(rows[i]) == want]
        if len(free) != 1:
            return None
        claimed[free[0]] = expected[k].id
        return free[0]

    for k, item in enumerate(expected):
        rec = recs.get(item.id, MISSING)
        d = Decision(item.id, slot, item.layer, "current", item)
        if item.id in located:
            d.at = located[item.id]
            d.found = rows[d.at]
            if rec["hash"] != item.stamp:
                d.state, d.record = "refresh", item.record
        elif item.id in matched:
            d.at = matched[item.id]
            d.found = rows[d.at]
            if rec is MISSING:
                d.state = "unrecorded"
            elif rec is None:
                d.state = "tombstone"
            else:
                d.record = item.record            # caught up with the standard
        elif rec is MISSING:
            d.state, d.record = "add", item.record
        elif rec is None:
            d.state = "tombstone"
        else:
            # Recorded, and no row holds what was written: the author changed it (one
            # unclaimed row where it stood) or removed it. Either way a null record: a
            # changed row no longer carries the standard's identity, so without the
            # record apply would add the standard's row beside the author's.
            d.at = gap_copy(k)
            d.found = rows[d.at] if d.at is not None else None
            d.state = "released" if d.at is not None else "deleted"
            d.record = None
        out.decisions.append(d)
    for item_id in sorted(set(recs) - {i.id for i in expected}):
        rec = recs[item_id]
        layer = standard_item_kind(item_id)[1].group(2)
        if item_id in located:
            at = located[item_id]
            out.decisions.append(Decision(item_id, slot, layer, "remove", None,
                                          found=rows[at], record="drop", at=at))
        else:
            out.decisions.append(Decision(item_id, slot, layer, "forget", None,
                                          record="drop"))


def row_shape(row) -> tuple:
    """A row without its text: what an author's edit of a standard's row keeps."""
    if isinstance(row, dict) and "header" in row:
        return ("header", row.get("size"), row.get("background"))
    if isinstance(row, dict) and "divider" in row:
        return ("divider",)
    items = row_items(row) or []
    return ("row", row.get("background") if isinstance(row, dict) else None,
            tuple((i.get("width"), i.get("height")) if isinstance(i, dict) else ("chart", i)
                  for i in items))


def _css_slot(out: Analysis, view: dict, expected: list[Item], records: dict) -> None:
    css = view["dashboard"].get("css") or ""
    recs = {k: v for k, v in records.items() if k.startswith("dashboard.css[")}
    if not expected and not recs:
        return
    try:
        segments = parse_css(css)
    except MarkerError as e:
        out.errors.append({"item": "dashboard.css", "detail": str(e),
                           "locked_by": next((i.locked_by for i in expected if i.locked_by),
                                             None)})
        return
    out.segments = segments
    blocks = {s.layer: s for s in segments if isinstance(s, Block)}
    for item in expected:
        rec = recs.get(item.id, MISSING)
        blk = blocks.get(item.layer)
        d = Decision(item.id, "css", item.layer, "current", item)
        if blk is not None:
            d.found = blk.body
            body = content_hash(blk.body)
            if rec is MISSING:
                d.state = "unrecorded" if body == item.stamp else "author"
            elif rec is None:
                d.state, d.record = "author", "drop"   # written after deleting
                if body == item.stamp:
                    d.state = "unrecorded"
            elif body == rec["hash"]:
                if body != item.stamp:
                    d.state, d.record = "refresh", item.record
            elif body == item.stamp:
                d.record = item.record
            else:
                d.state, d.record = "released", "drop"   # edited inside the block
        else:
            spot = _unmarked(segments, item.value)
            if spot is not None:
                # Unmarked, word for word: a UI-built or decompiled dashboard that already
                # carries it. --claim marks and records it; apply never adds a second copy.
                d.found, d.unmarked = item.value, spot
                d.state = "tombstone" if rec is None else "unrecorded"
            elif rec is MISSING:
                d.state, d.record = "add", item.record
            elif rec is None:
                d.state = "tombstone"
            else:
                d.state, d.record = "deleted", None
        out.decisions.append(d)
    for item_id in sorted(set(recs) - {i.id for i in expected}):
        rec = recs[item_id]
        layer = standard_item_kind(item_id)[1].group(1)
        blk = blocks.get(layer)
        if rec is not None and blk is not None and content_hash(blk.body) == rec["hash"]:
            out.decisions.append(Decision(item_id, "css", layer, "remove", None,
                                          found=blk.body, record="drop"))
        else:
            out.decisions.append(Decision(item_id, "css", layer, "forget", None,
                                          found=blk.body if blk else None, record="drop"))


def _unmarked(segments: list, text: str) -> tuple[int, int] | None:
    """The standard's CSS found word for word in plain (unmarked) CSS: a dashboard built
    in the UI, or decompiled, that already carries it."""
    for i, seg in enumerate(segments):
        if isinstance(seg, str):
            at = seg.find(text)
            if at >= 0:
                return i, at
    return None


# -- applying decisions to a spec ------------------------------------------------


@dataclass
class Change:
    """One change apply makes (or, in check mode, would make) to a spec."""

    item: str
    action: str          # add | refresh | remove | claim | rewrite | release | tombstone |
    #                      forget | record
    layer: str
    locked_by: str | None = None
    to: Any = None       # the new value or hash
    was: Any = None      # the value or hash it replaces

    def as_dict(self) -> dict:
        out = {"item": self.item, "action": self.action, "layer": self.layer}
        if self.locked_by:
            out["locked_by"] = self.locked_by
        if self.to is not None:
            out["to"] = self.to
        if self.was is not None:
            out["was"] = self.was
        return out


CONTENT_ACTIONS = ("add", "refresh", "remove", "claim", "rewrite")


def _shown(value, by: str):
    """A value as a change shows it: rows and CSS by hash, the rest as they are."""
    if value is None:
        return None
    return content_hash(value) if by == "hash" else value


def plan_changes(analysis: Analysis, *, locked: bool = False,
                 claim: bool = False) -> list[Change]:
    """The changes apply makes, in item order. Content changes first-class (`add`,
    `refresh`, `remove`, `claim`, `rewrite` for a locked item under --locked); the rest
    keep the record in step (`release`, `tombstone`, `forget`, `record`)."""
    out = []
    for d in analysis.decisions:
        by = "hash" if d.slot in ROW_SLOTS + ("css",) else "value"
        item = d.item
        new = _shown(item.value, by) if item else None
        found = _shown(d.found, by)
        if d.violation:
            if locked:
                out.append(Change(d.id, "rewrite", d.layer, d.locked_by, to=new, was=found))
            continue
        if d.state in ("add", "refresh"):
            out.append(Change(d.id, d.state, d.layer, d.locked_by, to=new,
                              was=found if d.state == "refresh" else None))
        elif d.state == "remove":
            out.append(Change(d.id, "remove", d.layer, was=found))
        elif d.state == "unrecorded" and claim:
            out.append(Change(d.id, "claim", d.layer, d.locked_by, to=new))
        elif d.state == "released":
            out.append(Change(d.id, "release", d.layer, d.locked_by, was=new, to=found))
        elif d.state == "deleted":
            out.append(Change(d.id, "tombstone", d.layer, d.locked_by, was=new))
        elif d.state == "forget":
            out.append(Change(d.id, "forget", d.layer))
        elif d.record is not MISSING:
            out.append(Change(d.id, "record", d.layer, d.locked_by,
                              to=new if isinstance(d.record, dict) else None))
    return out


def stale(analysis: Analysis) -> bool:
    """Apply would change the spec's content (an add, refresh or removal), locked or not."""
    return any(d.state in WRITES for d in analysis.decisions)


def locked_stale(analysis: Analysis) -> list[str]:
    """What `standards apply --check` fails on (the decision record's #7): each locked item
    the spec doesn't hold as the standard has it now, whether never written, written in
    an older version, or changed by its author; and dashboard.css whose markers can't be
    read while a block in it is locked. Unlocked content lags without failing: teams
    take an unlocked change in their own pull request, and `standards check --strict`
    fails on it (a warn) for a team that wants it current."""
    out = [d.id for d in analysis.decisions if d.locked_by and not d.conforms]
    out += [e["item"] for e in analysis.errors if e.get("locked_by")]
    return out


def execute(data: dict, spec, analysis: Analysis, *, locked: bool = False,
            claim: bool = False) -> tuple[dict, list[Change]]:
    """(the patched deep copy of the raw spec data, the changes made). The record is
    written in sorted key order; an emptied record (and design block) is removed, so a
    spec a standard no longer touches carries no trace."""
    out = copy.deepcopy(data)
    changes = plan_changes(analysis, locked=locked, claim=claim)
    by_id = {d.id: d for d in analysis.decisions}
    acting = {c.item: c for c in changes}
    design = out.get("design") or {}
    written = dict(design.get("standard_written") or {})
    filled = {k: dict(v) for k, v in (design.get("filled") or {}).items()}

    def content_op(d: Decision) -> str | None:
        c = acting.get(d.id)
        return c.action if c is not None and c.action in CONTENT_ACTIONS else None

    # Rows: one rebuilt list per slot.
    for slot in ROW_SLOTS:
        ds = [d for d in analysis.decisions if d.slot == slot]
        if not any(content_op(d) for d in ds):
            continue
        rows = (out.get("layout") or {}).get(slot) or []
        entries: list[list] = [["orig", r] for r in rows]   # [kind, row, item id]
        for e in entries:
            e.append(None)
        for d in ds:
            if d.at is not None and d.item is not None and d.state != "released":
                entries[d.at][2] = d.id
        for d in ds:
            op = content_op(d)
            if op == "remove":
                entries[d.at][0] = "gone"
            elif op == "refresh" or (op == "rewrite" and d.at is not None):
                entries[d.at] = ["new", d.item.value, d.id]
        order = [d.id for d in ds if d.item is not None]
        for d in ds:
            op = content_op(d)
            if not (op == "add" or (op == "rewrite" and d.at is None)):
                continue
            pos = {e[2]: i for i, e in enumerate(entries) if e[2] and e[0] != "gone"}
            k = order.index(d.id)
            prev = [pos[i] for i in order[:k] if i in pos]
            nxt = [pos[i] for i in order[k + 1:] if i in pos]
            if prev:
                at = max(prev) + 1
            elif nxt:
                at = min(nxt)
            else:
                at = 0 if slot == "header" else len(entries)
            entries.insert(at, ["new", d.item.value, d.id])
        new_rows = [e[1] for e in entries if e[0] != "gone"]
        layout = out.setdefault("layout", {})
        if new_rows and slot == "header" and "header" not in layout:
            out["layout"] = {"header": new_rows, **layout}   # read first, as drawn
        elif new_rows:
            layout[slot] = new_rows
        else:
            layout.pop(slot, None)

    # CSS: rebuilt from its segments.
    if analysis.segments is not None and any(
            content_op(d) for d in analysis.decisions if d.slot == "css"):
        segs = list(analysis.segments)
        chain_order = [d.layer for d in analysis.decisions if d.slot == "css" and d.item]

        def index_of(layer):
            return next((i for i, s in enumerate(segs)
                         if isinstance(s, Block) and s.layer == layer), None)

        for d in analysis.decisions:
            if d.slot != "css":
                continue
            op = content_op(d)
            if op is None:
                continue
            i = index_of(d.layer)
            if op == "remove":
                _drop_block(segs, i)
            elif op == "claim" and d.unmarked is not None:
                si, off = d.unmarked
                text = segs[si]
                body = d.item.value
                parts = [text[:off], Block(d.layer, content_hash(body), body),
                         text[off + len(body):]]
                segs[si:si + 1] = [p for p in parts if not (isinstance(p, str) and p == "")]
            elif i is not None:
                segs[i] = Block(d.layer, content_hash(d.item.value), d.item.value)
            else:
                _insert_block(segs, Block(d.layer, content_hash(d.item.value), d.item.value),
                              chain_order)
        css = join_css(segs)
        if css.strip():
            out["dashboard"]["css"] = css
        else:
            out["dashboard"].pop("css", None)

    # Scalars, label colours and number formats.
    dash = out["dashboard"]
    charts = {c.get("name"): c for c in out.get("charts", [])}
    for d in analysis.decisions:
        op = content_op(d)
        if op is None or d.slot in ROW_SLOTS + ("css",):
            continue
        kind, m = standard_item_kind(d.id)
        value = None if op == "remove" else d.item.value
        if kind == "scalar":
            target, key = dash, m.group(1)
        elif kind == "label":
            target, key = dash.setdefault("label_colors", {}), m.group(1)
        else:
            target, key = charts[m.group(1)], "number_format"
            brain = filled.get(m.group(1))
            if brain is not None and "number_format" in brain:
                brain.pop("number_format")   # one owner per field: the standard takes it
                if not brain:
                    filled.pop(m.group(1))
        if value is None:
            target.pop(key, None)
        else:
            target[key] = value
    if "label_colors" in dash and not dash["label_colors"]:
        dash.pop("label_colors")

    # The record: a content change records what it wrote (a removal drops the entry);
    # every other change does what the decision says to the record.
    for d in analysis.decisions:
        c = acting.get(d.id)
        if c is None:
            continue
        if c.action in ("add", "refresh", "claim", "rewrite"):
            written[d.id] = d.item.record
        elif d.record == "drop":
            written.pop(d.id, None)
        elif d.record is None:
            written[d.id] = None
        elif isinstance(d.record, dict):
            written[d.id] = d.record
    if written:
        design["standard_written"] = dict(sorted(written.items()))
    else:
        design.pop("standard_written", None)
    if filled:
        design["filled"] = filled
    else:
        design.pop("filled", None)
    if design:
        out["design"] = design
    else:
        out.pop("design", None)
    return out, changes


def _insert_block(segs: list, block: Block, chain_order: list[str]) -> None:
    """A new block goes after the blocks of the layers above it and before those below;
    with no block yet, first in the CSS, with the author's CSS after it."""
    rank = {l: i for i, l in enumerate(chain_order)}
    mine = rank.get(block.layer, len(rank))
    at = None
    for i, s in enumerate(segs):
        if isinstance(s, Block) and rank.get(s.layer, len(rank)) < mine:
            at = i + 1
    if at is None:
        at = next((i for i, s in enumerate(segs)
                   if isinstance(s, Block) and rank.get(s.layer, len(rank)) > mine), 0)
    before = segs[at - 1] if at > 0 else None
    after = segs[at] if at < len(segs) else None
    new: list = []
    if before is not None and not render_segment(before).endswith("\n"):
        new.append("\n")
    new.append(block)
    if after is not None and not render_segment(after).startswith("\n"):
        new.append("\n")
    segs[at:at] = new


def _drop_block(segs: list, i: int) -> None:
    """Remove a block and the one newline that joined it to its neighbour."""
    segs.pop(i)
    if i < len(segs) and isinstance(segs[i], str) and segs[i].startswith("\n"):
        segs[i] = segs[i][1:]
        if not segs[i]:
            segs.pop(i)
    elif i > 0 and isinstance(segs[i - 1], str) and segs[i - 1].endswith("\n"):
        segs[i - 1] = segs[i - 1][:-1]
        if not segs[i - 1]:
            segs.pop(i - 1)


# -- the record's owned rows (for the repairs that must leave them alone) --------


def owned_rows(spec) -> set[tuple[str, int]]:
    """(slot, index) of each header or footer row design.standard_written records as the
    standard's and the spec still holds as written. A repair never edits these: the
    standard is their one owner."""
    records = _records(spec)
    if not records:
        return set()
    view = _spec_view(spec)
    out = set()
    for slot in ROW_SLOTS:
        rows = (view.get("layout") or {}).get(slot) or []
        hashes = [content_hash(r) for r in rows]
        claimed: set[int] = set()
        for key, rec in sorted(records.items()):
            if rec and key.startswith(f"layout.{slot}["):
                at = next((i for i, h in enumerate(hashes)
                           if h == rec["hash"] and i not in claimed), None)
                if at is not None:
                    claimed.add(at)
                    out.add((slot, at))
    return out


def standard_holds(spec, chart: str, field: str) -> bool:
    """design.standard_written has an entry for this chart field (a value or null): the
    standard's, or the author's after a release, never the brain's to fill."""
    return bool(spec.design) and f"charts[{chart}].{field}" in spec.design.standard_written


# -- presentation ----------------------------------------------------------------


def explain_rows(std, spec) -> list[dict]:
    """`explain`'s dashboard section: each item a standard governs in this spec, with its
    value, layer, lock, source (standard: as the standard wrote it; missing: not written
    yet; released: the author took it over; author: the author's own value, never the
    standard's), what standards apply would do, and how to override it."""
    analysis = analyze(std, spec)
    records = _records(spec)
    rows = []
    for d in analysis.decisions:
        if d.item is None:
            source = "standard" if d.state == "remove" else "author"
            pending = ("standards apply removes it: the standard no longer has it"
                       if d.state == "remove" else
                       "standards apply drops its record: the standard no longer has it")
        elif d.state in ("current", "refresh") or (d.state == "tombstone" and d.conforms):
            source = "standard"
            pending = (f"standards apply refreshes it to {describe(d.item.value, d.slot)}"
                       if d.state == "refresh" else None)
        elif d.state == "add":
            source, pending = "missing", "standards apply adds it"
        elif d.state == "unrecorded":
            source = "author"
            pending = "it matches the standard: standards apply --claim records it as the standard's"
        elif d.state == "author" and d.id not in records:
            source, pending = "author", None
        else:
            source, pending = "released", None
        if d.violation:
            pending = "standards apply --locked puts the standard's back"
        if d.locked_by:
            override = (f"{d.locked_by} locks it: change it in {d.locked_by}'s standards "
                        f"file, not in the spec")
        elif source == "standard":
            override = ("edit or delete it in the spec and it is yours; standards apply "
                        "leaves it alone from then on")
        elif source == "missing":
            override = "after standards apply adds it, delete it to keep it off this dashboard"
        else:
            override = "it is yours: edit it freely"
            if records.get(d.id, MISSING) is None:
                override += ("; delete its null entry in design.standard_written to take the "
                             "standard's again")
        row = {"item": d.id, "slot": d.slot, "layer": d.layer, "locked": d.locked_by is not None}
        if d.locked_by:
            row["locked_by"] = d.locked_by
        row["value"] = d.found
        if d.item is not None and not d.conforms:
            row["standard"] = d.item.value
        row["source"] = source
        if d.id in records:
            row["recorded"] = records[d.id]
        if pending:
            row["pending"] = pending
        row["override"] = override
        rows.append(row)
    for err in analysis.errors:
        rows.append({"item": err["item"], "slot": "css", "layer": None,
                     "locked": bool(err.get("locked_by")), "value": None,
                     "source": "unreadable", "pending": None,
                     "override": f"{err['detail']}: fix the markers by hand"})
    return rows


def show_entries(std) -> dict:
    """`standards show`: the resolved standard's content, every item with its layer and
    lock, including the rows each lifecycle state and classification brings (a spec takes
    those for its own state and classification only). Keys read like the items they
    become in a spec; a number format is listed per metric label, since the chart that
    takes it depends on the spec."""
    out: dict = {}
    layers = std.content_layers

    def add(key, slot, layer, value, lock_slot, k):
        out[key] = {"slot": slot, "value": value, "layer": layer,
                    "locked": _locked_by(std, lock_slot, k) is not None}

    for k, (layer, c) in enumerate(layers):
        for state, rows in c.get("header_by_lifecycle", {}).items():
            for n, row in enumerate(rows):
                add(f"content.header[{layer}][lifecycle={state}][{n}]", "header", layer, row,
                    "header_by_lifecycle", k)
        for n, row in enumerate(c.get("header", [])):
            add(f"content.header[{layer}][{n}]", "header", layer, row, "header", k)
    for k in reversed(range(len(layers))):
        layer, c = layers[k]
        for n, row in enumerate(c.get("footer", [])):
            add(f"content.footer[{layer}][{n}]", "footer", layer, row, "footer", k)
        for cls, rows in c.get("footer_by_classification", {}).items():
            for n, row in enumerate(rows):
                add(f"content.footer[{layer}][classification={cls}][{n}]", "footer", layer,
                    row, "footer_by_classification", k)
    for k, (layer, c) in enumerate(layers):
        if "css" in c:
            add(f"content.css[{layer}]", "css", layer, c["css"], "css", k)
    for slot in SCALAR_SLOTS:
        at = [k for k, (_, c) in enumerate(layers) if slot in c]
        if at:
            add(f"content.{slot}", slot, layers[at[-1]][0], layers[at[-1]][1][slot], slot,
                at[-1])
    for slot in KEYED_SLOTS:
        merged: dict = {}
        for k, (_, c) in enumerate(layers):
            for key, v in c.get(slot, {}).items():
                merged[key] = (v, k)
        for key in sorted(merged):
            v, k = merged[key]
            add(f"content.{slot}[{key}]", slot, layers[k][0], v, slot, k)
    return out


def describe(value, slot: str, width: int = 40) -> str:
    """A short text for a row, a CSS body or a value."""
    if value is None:
        return "-"
    if slot in ROW_SLOTS:
        if isinstance(value, dict) and "header" in value:
            text = f"header {value['header']!r}"
        elif isinstance(value, dict) and "divider" in value:
            text = "divider"
        else:
            items = row_items(value) or []
            text = " | ".join(i.get("markdown", "") if isinstance(i, dict) else str(i)
                              for i in items)
            text = json.dumps(" ".join(text.split()), ensure_ascii=False)
    elif slot == "css":
        lines = [l for l in value.splitlines() if l.strip()]
        text = f"{content_hash(value)} ({len(lines)} line{'s' if len(lines) != 1 else ''})"
    else:
        text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= width else text[:width - 3] + "..."
