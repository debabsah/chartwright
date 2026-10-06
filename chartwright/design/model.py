"""The critic's data model: findings, the rule registry, and a normalized
view of the layout (rows, tabs, and sketches all become the same bands) so
rules are written once, not per layout mode.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Callable, Iterator

from ..resolver import ResolvedDataset, Resolution
from ..spec import DEFAULT_HEIGHT, DashboardSpec, MarkdownBlock, item_rows

# "3" = the post-review batch: the reconciled grid model and stricter
# table_visible_ratio, `polished` provenance in the payload, and the
# data.unwindowed-history rule. Bumped because all three change what a spec
# is told -- a new warn-severity rule can newly block a `--design strict`
# pipeline, so consumers keying on this get an honest signal.
# "4" = narrative.color-scheme, a new warn-severity rule (same reason).
# "5" = the default.* family: info-severity fills that `advise --fix` writes into
# the spec, tracked in design.filled (sec.16). New fixable findings change what
# `--fix` writes, so consumers keying on this get the signal.
# "6" = the standard.* family (sec.18, "Content"): an error and two warn rules that
# fire when a standard with content applies, so a strict gate can newly block.
# "7" = fills keep a null record when the author edits one (an edit later deleted stays
# deleted), and default.stale-record: a fixable finding for a renamed or removed chart's
# design.filled entry, which now validates. Both change what `--fix` writes.
# "8" = one height question for tables and pivots (sec.15.42): table_visible_ratio 1 and
# every-row targets in size.table-window, size.pivot-window reading pivot rows instead of
# row_limit, size.grid-fit counting every pivot layout. No new rule, but a table can
# newly warn (and block a strict gate) and `--fix` writes different heights.
# "9" = data.rolling-window-span, a new warn-severity rule for a rolling trendline KPI
# whose time range can't hold its window and comparison; default.compare-suffix writes
# 'vs prior 12 months' between trailing windows; chart.ordinal-order leaves a heatmap axis
# ordered by value alone; default.date-tile fills date_format on a big number of a date
# column (data-aware), and a big number shown as a date gets no number format from
# narrative.big-number-format, default.count-format or a standard.
# "9" = the waterfall: chart.waterfall-additive and chart.waterfall-steps (warn) can
# newly block a strict gate, chart.waterfall-order, chart.waterfall-colors and
# chart.waterfall-axis-titles are new findings, and default.value-labels and
# default.count-format now fill a waterfall's show_value and number_format. The box
# plot: chart.box-plot-observations and chart.box-plot-groups (warn), and
# chart.ordinal-order now reads a box plot's groups.
# "10" = no DataTables chrome on a table whose rows all show: default.search-box fills only
# a paged table whose rows outgrow its panel (and --fix removes a box it filled on one
# whose rows all show), and size.table-chrome, a new info rule, names an author's page
# size or search box there.
# "11" = size.markdown-fit, a new warn-severity rule for markdown text cut off by its
# block, and layout.markdown-height fixes a one-line block to the height its line takes
# (1.6 to 2.4 units) instead of 2, from the same estimate.
DESIGN_BRAIN_VERSION = "11"

KPI_TYPES = {"big_number_total", "big_number_trend"}
TIMESERIES_TYPES = {"timeseries_line", "timeseries_bar", "timeseries_area", "timeseries_scatter"}
AXIS_TYPES = TIMESERIES_TYPES | {"bar", "heatmap", "histogram", "mixed", "waterfall", "box_plot"}
# Charts that are neither KPI nor axis-bearing; a contract test asserts the
# three sets exactly cover CHART_TYPES, so a new chart type fails CI until
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
    # Why the fix is right, one line, carried into the `fixed` record. A fill sets it;
    # a repair's reason is its detail.
    why: str | None = None
    # A default.* finding that hands a field to the author (an edit or a deletion of a
    # fill) is a 'release', not a fill.
    release: bool = False
    # Set only when a standard is in play (design/standards.py): the layer that set this
    # finding's severity (a standard's name, "overlay", or "rulebook" for the rule's own
    # level), and whether a standard locks the rule.
    layer: str | None = None
    locked: bool | None = None
    # A standard.content-locked finding: the layer whose lock it reports, so a waiver
    # scoped to one layer (design/waivers.py) can tell. Never in the payload.
    lock_layer: str | None = None

    @property
    def kind(self) -> str:
        """'fill' for a design default (the default.* family), 'release' when one hands a
        filled field to the author, 'repair' otherwise."""
        if self.release:
            return "release"
        return "fill" if self.rule.startswith("default.") else "repair"

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
        d.pop("why")
        d.pop("release")
        d.pop("lock_layer")
        if self.layer is None:
            d.pop("layer")
        if self.locked is None:
            d.pop("locked")
        d["fixable"] = self.fix is not None
        return d


@dataclass
class AdviceReport:
    ok: bool                 # False iff any error-severity finding
    audience: str
    findings: list[Finding] = field(default_factory=list)
    fixed: list[dict] = field(default_factory=list)
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
    # The per-machine design.yaml this run read and what it changed (design/__init__.py
    # _overlay_report); None when no overlay is in play.
    overlay: dict | None = None
    # The standard this run applied (design/standards.py report_block); None without one.
    standard: dict | None = None
    # Locked findings a waiver let pass (design/waivers.py), each with the waiver's owner,
    # reason, expiry and status; and the warnings an expired waiver that still applied
    # (outside standards check and advise) left. Empty without a standards waivers file.
    waived: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

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
        if self.overlay is not None:
            out["overlay"] = self.overlay
        if self.standard is not None:
            out["standard"] = self.standard
        if self.waived:
            out["waived"] = self.waived
        if self.warnings:
            out["warnings"] = self.warnings
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
    footer: bool = False     # layout.footer: below every tab, not a tab of its own
    header: bool = False     # layout.header: above every tab, not a tab of its own

    @property
    def label(self) -> str:
        if self.footer:
            return "footer"
        if self.header:
            return "header"
        return f"tab {self.title!r}" if self.title else "layout"


@dataclass
class Geo:
    width: int
    height: float
    section: int
    band: int


class RuleContext:
    def __init__(self, spec: DashboardSpec, params, resolution: Resolution | None = None,
                 prober=None, standard=None):
        self.spec = spec
        self.params = params
        # The spec's resolved standard (design/standards.py), or None: the standard.*
        # rules read its content.
        self.standard = standard
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
        return self.where_band(g.section, g.band)

    def where_band(self, si: int, bi: int) -> str:
        return f"{self.sections[si].label} row {bi}"

    @property
    def body_sections(self) -> list[Section]:
        """The rows / sketch / tabs, without the header and the footer."""
        return [s for s in self.sections if not (s.footer or s.header)]

    def is_sketch(self, name: str) -> bool:
        return self.sections[self.geo[name].section].mode == "sketch"

    def human_polished(self, name: str) -> bool:
        """Fractional heights are absorb's signature: a human dragged this
        chart to taste in the UI. Sizing rules stay silent on it."""
        h = self.charts[name].height
        return isinstance(h, float) and not float(h).is_integer()

    def written(self, chart, field: str) -> bool:
        """The spec holds a value for this field (validation's model_fields_set: an
        omitted field is unset even where Superset's own default fills it in, and a
        written default counts as written). An explicit null is unset, as compile
        reads it."""
        return field in chart.model_fields_set and getattr(chart, field) is not None

    def filled(self, name: str) -> dict:
        """design.filled for this chart: field -> the value the brain wrote, or None
        for a fill the author edited or deleted (the brain fills that field no more)."""
        design = self.spec.design
        return dict(design.filled.get(name, {})) if design else {}

    def standard_holds(self, chart, field: str) -> bool:
        """design.standard_written has an entry for this chart field: a standard's write,
        or the author's after a release. Either way never the brain's to fill: a field
        has one owner."""
        from .content import standard_holds

        return standard_holds(self.spec, chart.name, field)

    def standard_rows(self) -> set[tuple[str, int]]:
        """(slot, index) of the header and footer rows a standard owns, which no repair
        edits."""
        cached = getattr(self, "_standard_rows", None)
        if cached is None:
            from .content import owned_rows

            cached = self._standard_rows = owned_rows(self.spec)
        return cached

    def brain_owns(self, chart, field: str) -> bool:
        """The chart still holds exactly the value the brain wrote: its fill, which
        --fix keeps up to date. Any other value is the author's."""
        rec = self.filled(chart.name)
        return (rec.get(field) is not None and self.written(chart, field)
                and getattr(chart, field) == rec[field])

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
        sections = [Section(None, "rows", _bands_from_rows(spec, lay.rows))]
    elif lay.sketch:
        sections = [Section(None, "sketch", _bands_from_sketch(spec, lay))]
    else:
        sections = []
        for tab in lay.tabs or []:
            for leaf in tab.tabs or [tab]:
                title = tab.title if leaf is tab else f"{tab.title} > {leaf.title}"
                if leaf.rows:
                    sections.append(Section(title, "rows", _bands_from_rows(spec, leaf.rows)))
                else:
                    sections.append(Section(title, "sketch", _bands_from_sketch(spec, leaf)))
    if lay.header:
        sections.insert(0, Section(None, "rows", _bands_from_rows(spec, lay.header), header=True))
    if lay.footer:
        sections.append(Section(None, "rows", _bands_from_rows(spec, lay.footer), footer=True))
    return sections


def _bands_from_rows(spec: DashboardSpec, rows) -> list[Band]:
    bands = []
    for row in item_rows(rows):  # headers and dividers hold no charts
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
    from ..sketch import SketchBlock, SketchColumn

    def height(sc) -> float:
        # A header counts nothing, as a header row does in rows mode.
        if isinstance(sc, SketchBlock):
            return holder.sketch_block_height(sc) if sc.kind == "markdown" else 0.0
        return spec.resolved_height(sc.name)

    bands = []
    for srow in holder.parsed_sketch():
        if srow.header_band is not None:
            continue  # a section title between bands holds no charts, as in rows
        items = []
        for child in srow.children:
            stack = child.children if isinstance(child, SketchColumn) else [child]
            names = [sc.name for sc in stack if not isinstance(sc, SketchBlock)]
            # A slot of blocks only (a note, a header) reads as markdown does in rows.
            items.append(BandItem(child.width, sum(height(sc) for sc in stack), names,
                                  is_markdown=not names))
        bands.append(Band(items))
    return bands
