"""The critic's data model: findings, the rule registry, and a normalized
view of the layout (rows, tabs, and sketches all become the same bands) so
rules are written once, not per layout mode.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Callable, Iterator

from ..resolver import ResolvedDataset, Resolution
from ..spec import DEFAULT_HEIGHT, DashboardSpec, MarkdownBlock

# "3" = the post-review batch: the reconciled grid model and stricter
# table_visible_ratio, `polished` provenance in the payload, and the
# data.unwindowed-history rule. Bumped because all three change what a spec
# is told -- a new warn-severity rule can newly block a `--design strict`
# pipeline, so consumers keying on this get an honest signal.
DESIGN_BRAIN_VERSION = "3"

KPI_TYPES = {"big_number_total", "big_number_trend"}
TIMESERIES_TYPES = {"timeseries_line", "timeseries_bar", "timeseries_area", "timeseries_scatter"}
AXIS_TYPES = TIMESERIES_TYPES | {"bar", "heatmap", "histogram"}
# Charts that are neither KPI nor axis-bearing; a contract test asserts the
# three sets exactly cover CHART_TYPES, so a 15th chart type fails CI until
# someone consciously classifies it (and reviews which rules apply).
STANDALONE_TYPES = {"pie", "table", "pivot_table", "funnel", "treemap"}

# Renamed rule ids keep working in ignore/disable lists forever.
RULE_ALIASES: dict[str, str] = {}


@dataclass
class Finding:
    rule: str
    severity: str            # error | warn | info
    chart: str | None
    where: str
    detail: str
    fix: dict | None = None  # {"chart": name, "set": {field: value}} -- presentation only
    # True for complaints ABOUT a chart's height: only these yield to a
    # human-polished (fractional, absorb-written) height. Width and data
    # complaints survive polish -- absorb can never write widths.
    height_driven: bool = False

    @property
    def key(self) -> str:
        return f"{self.rule}@{self.chart}" if self.chart else self.rule

    @property
    def scope_key(self) -> str:
        """Positional key for band findings (no chart to name):
        'layout.kpi-band@tab-Ops-row-1'. Accepted in ignore lists alongside
        the rule id and rule@Chart forms."""
        slug = re.sub(r"[^A-Za-z0-9]+", "-", self.where).strip("-")
        return f"{self.rule}@{slug}"

    def as_dict(self) -> dict:
        d = asdict(self)
        d.pop("fix")
        d.pop("height_driven")
        d["fixable"] = self.fix is not None
        return d


@dataclass
class AdviceReport:
    ok: bool                 # False iff any error-severity finding
    audience: str
    findings: list[Finding] = field(default_factory=list)
    fixed: list[str] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)

    @property
    def counts(self) -> dict:
        c = {"error": 0, "warn": 0, "info": 0}
        for f in self.findings:
            c[f.severity] += 1
        return c

    def gate(self, strict: bool) -> bool:
        """True when this report should block under the given strictness."""
        c = self.counts
        return c["error"] > 0 or (strict and c["warn"] > 0)

    unmatched_ignores: list[str] = field(default_factory=list)
    # Findings withheld because the chart carries a human-polished (fractional)
    # height. Reported, never silent: a rule that stands down on an INFERRED
    # signal has to say so, or the user reads the silence as approval.
    polished: list[str] = field(default_factory=list)

    def payload(self) -> dict:
        out = {
            "stage": "design",
            "ok": self.ok,
            "design_brain": DESIGN_BRAIN_VERSION,
            "audience": self.audience,
            "counts": self.counts,
            "findings": [f.as_dict() for f in self.findings],
            "fixed": self.fixed,
            "ignored": self.ignored,
        }
        if self.unmatched_ignores:
            out["unmatched_ignores"] = self.unmatched_ignores
        if self.polished:
            out["polished"] = self.polished
        return out


# -- normalized layout --------------------------------------------------------


@dataclass
class BandItem:
    width: int
    height: float            # visual height in the band (a stack sums its charts)
    charts: list[str]        # 1 name for a chart, n for a sketch stack, 0 for markdown
    is_markdown: bool = False


@dataclass
class Band:
    items: list[BandItem]

    @property
    def height(self) -> float:
        return max((i.height for i in self.items), default=0.0)

    @property
    def chart_names(self) -> list[str]:
        return [n for i in self.items for n in i.charts]


@dataclass
class Section:
    title: str | None        # tab title, None for a flat layout
    mode: str                # "rows" | "sketch"
    bands: list[Band]


@dataclass
class Geo:
    width: int
    height: float
    section: int
    band: int


class RuleContext:
    def __init__(self, spec: DashboardSpec, params, resolution: Resolution | None = None,
                 prober=None):
        self.spec = spec
        self.params = params
        self.resolution = resolution
        self.prober = prober
        self.charts = {c.name: c for c in spec.charts}
        self._heights: dict[str, float] = {}
        self.sections: list[Section] = _normalize(spec)
        self.geo: dict[str, Geo] = {}
        for si, sec in enumerate(self.sections):
            for bi, band in enumerate(sec.bands):
                for item in band.items:
                    for name in item.charts:
                        self.geo[name] = Geo(item.width, self.height(name), si, bi)

    # -- lookups ---------------------------------------------------------------

    def height(self, name: str) -> float:
        h = self._heights.get(name)
        if h is None:
            h = self._heights[name] = self.spec.resolved_height(name)
        return h

    def width(self, name: str) -> int:
        return self.geo[name].width

    def where(self, name: str) -> str:
        g = self.geo[name]
        sec = self.sections[g.section]
        prefix = f"tab {sec.title!r} " if sec.title else "layout "
        return f"{prefix}row {g.band}"

    def where_band(self, si: int, bi: int) -> str:
        sec = self.sections[si]
        prefix = f"tab {sec.title!r} " if sec.title else "layout "
        return f"{prefix}row {bi}"

    def is_sketch(self, name: str) -> bool:
        return self.sections[self.geo[name].section].mode == "sketch"

    def human_polished(self, name: str) -> bool:
        """Fractional heights are absorb's signature: a human dragged this
        chart to taste in the UI. Sizing rules stay silent on it."""
        h = self.charts[name].height
        return isinstance(h, float) and not float(h).is_integer()

    def dataset_for(self, chart) -> ResolvedDataset | None:
        if self.resolution is None:
            return None
        return self.resolution.datasets.get(chart.dataset.key())

    def fix_height(self, chart, floor: float) -> dict:
        """A height-raise fix honoring calibrated recommended heights.

        Targets are ceiled to whole units and clamped to the spec's height cap:
        fractional heights are absorb's human-polish signature, and a tool-
        written fix must never mint it (or the fix would silence the very
        rules that produced it -- the echo chamber)."""
        import math

        target = max(floor, self.params.recommended_heights.get(chart.type, 0))
        return {"chart": chart.name, "set": {"height": min(100, math.ceil(target))}}


# -- registry -----------------------------------------------------------------


SEVERITY_RANK = {"error": 0, "warn": 1, "info": 2}


@dataclass
class Rule:
    id: str
    severity: str            # the rule's DEFAULT level; the overlay's `severity`
    #                          map overrides it per deployment.
    doc: str                 # one line; the brief prints these
    fixable: bool
    data_aware: bool         # needs a live resolution (skipped offline)
    fn: Callable[[RuleContext], Iterator[Finding]]
    since: str = "1"         # design_brain version that introduced the rule
    # Every level this rule can actually emit, default first. Four rules vary
    # it per finding (row-density escalates to error when a chart is starved;
    # row-fill and format-bands soften to info), and `ok` is error-driven --
    # so a rule that can produce an error while advertising `warn` understates
    # exactly the case a reader most needs to know about. Declared, printed in
    # the generated table, and checked against the rule's source by a test.
    severities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        self.severities = self.severities or (self.severity,)

    @property
    def severity_label(self) -> str:
        """'warn', or 'warn/error' when the rule varies it per finding."""
        rest = sorted(set(self.severities) - {self.severity}, key=SEVERITY_RANK.get)
        return "/".join([self.severity, *rest])


RULES: dict[str, Rule] = {}


def rule(id: str, severity: str, doc: str, fixable: bool = False, data_aware: bool = False,
         since: str = "1", severities: tuple[str, ...] = ()):
    def deco(fn):
        RULES[id] = Rule(id, severity, doc, fixable, data_aware, fn, since, severities)
        return fn
    return deco


def known_rule_ids() -> set[str]:
    return set(RULES) | set(RULE_ALIASES)


def canonical_rule_id(rule_id: str) -> str:
    return RULE_ALIASES.get(rule_id, rule_id)


# -- layout normalization -----------------------------------------------------


def _normalize(spec: DashboardSpec) -> list[Section]:
    lay = spec.layout
    if lay.rows:
        return [Section(None, "rows", _bands_from_rows(spec, lay.rows))]
    if lay.sketch:
        return [Section(None, "sketch", _bands_from_sketch(spec, lay))]
    sections = []
    for tab in lay.tabs or []:
        if tab.rows:
            sections.append(Section(tab.title, "rows", _bands_from_rows(spec, tab.rows)))
        else:
            sections.append(Section(tab.title, "sketch", _bands_from_sketch(spec, tab)))
    return sections


def _bands_from_rows(spec: DashboardSpec, rows) -> list[Band]:
    bands = []
    for row in rows:
        items = []
        for item in row:
            w = spec.resolved_item_width(item)
            if isinstance(item, MarkdownBlock):
                items.append(BandItem(w, item.height or DEFAULT_HEIGHT["markdown"], [], True))
            else:
                items.append(BandItem(w, spec.resolved_height(item), [item]))
        bands.append(Band(items))
    return bands


def _bands_from_sketch(spec: DashboardSpec, holder) -> list[Band]:
    bands = []
    for srow in holder.parsed_sketch():
        items = []
        for child in srow.children:
            if hasattr(child, "children"):  # SketchColumn: a stack of charts
                names = [sc.name for sc in child.children]
                total = sum(spec.resolved_height(n) for n in names)
                items.append(BandItem(child.width, total, names))
            else:
                items.append(BandItem(child.width, spec.resolved_height(child.name), [child.name]))
        bands.append(Band(items))
    return bands
