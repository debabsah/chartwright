"""Readability: the type sizes a dashboard draws, against floors for reading it on a
laptop (docs/DESIGN-BRAIN.md sec.17, "Type sizes").

Three rules, each reading what sets a size before judging it:
- readability.table-text: table and pivot cells, their headers and their rows;
- readability.chart-text: axis labels, legends and chart titles;
- readability.kpi-text: a big number's value, subtitle and comparison, which Superset
  sizes by the card's height.

A size the dashboard sets below its floor (its CSS, or its theme's tokens and ECharts
overrides when a theme is named and `advise --profile` read it) is a warn; a size that
is Superset's own default and below the floor is an info. The floors are audience
parameters (presets.py, min_cell_text_px and the rest), so design.yaml and a standard
tune or lock them.

What sets each size at 4.1.4, 5.0.0 and 6.1.0 (measured in headless Chromium at a
1440 px window on 2026-10-05, sec.17):
- Table cells and headers: 12 px. `.table-condensed { font-size: @font-size-s }`
  (superset-frontend/src/assets/stylesheets/superset.less:178-180; @font-size-s is 12px,
  less/variables.less:155) at 4.1.4 and 5.0.0; the table plugin's own
  `table.table-condensed { font-size: theme.fontSizeSM }` (plugin-chart-table/src/
  Styles.tsx:74-77) at 6.0.0 and 6.1.0, 12 px from antd's default seed. Neither sets a
  size on the cells, which inherit it. The table renders as `table table-striped
  table-condensed` (TableChart.tsx:1073 at 4.1.4, :1080 at 5.0.0, :1501 at 6.1.0) inside
  `.superset-chart-table` (components/Chart/ChartRenderer.jsx:277-281 at 4.1.4,
  ChartRenderer.tsx:457-460 at 6.1.0).
- Pivot cells: 12 px, from `table.pvtTable { font-size }` in the pivot plugin's styled
  wrapper (react-pivottable/Styles.js:24-32 at 4.1.4 and 5.0.0, Styles.ts:24-33 at
  6.1.0); its header cells set their own (Styles.js:44-50, Styles.ts:46-51). The
  pivot's chart class is `.pivot_table_v_2`, lodash's snakeCase of pivot_table_v2.
- Axis labels and legends: ECharts draws them on a canvas, so no CSS reaches them.
  Superset sets no size on either, so zrender's default applies: 12 px
  (zrender lib/core/platform.js, DEFAULT_FONT_SIZE). On 6.1.0 a theme's
  echartsOptionsOverrides and echartsOptionsOverridesByChartType are merged over every
  chart's options (plugin-chart-echarts/src/components/Echart.tsx:254-275), and a
  global textStyle.fontSize reaches every label that sets none (echarts 5.6.0
  lib/label/labelStyle.js:312, 386-388). 6.0.0's Echart.tsx reads neither key.
- Chart titles: 16 px, theme.typography.sizes.l on the slice header
  (dashboard/components/SliceHeader/index.tsx:62 at 4.1.4, :67 at 5.0.0) and
  theme.fontSizeLG (:80 at 6.1.0); `.header-title` inherits it.
- Big numbers: BigNumberViz sizes each line to a share of the card's height
  (PROPORTION and computeMaxFontSize, BigNumberViz.tsx:36-57 and :275-321 at 4.1.4), and
  chartwright pins header_font_size 0.4 and subheader_font_size 0.15
  (compiler._pin_big_number_fonts). The size is an inline style, so only CSS marked
  `!important` changes it.

Theme tokens (6.0 and later): Superset lets antd derive every map token from the seed,
then lays the theme's own tokens over them (packages/superset-core/src/theme/
Theme.tsx:109-137 at 6.1.0). antd 5 derives fontSizeSM and fontSizeLG from fontSize
(theme/themes/shared/genFontSizes.js, genFontMapToken.js) and the compact algorithm
derives them from fontSizeSM instead (theme/themes/compact/index.js), so a compact
theme draws table text at 10 px.

The CSS reader is minimal by design: chartwright/design/markdown_fit.py on the
markdown-fit branch has a cascade reader for markdown, which this one doesn't share,
since that branch isn't merged. It reads top-level rules, and only font-size,
line-height, padding and height, on table and pivot cells, rows and tables, on
`.header-title`, and on the big number's lines. A declaration applies where it beats
Superset's own rule for that property on that release (a tie goes to the dashboard's
CSS, measured on all three); a rule it can't place (a pseudo-class, an attribute, a
chart id, an @media block, var()) is named in the finding, never guessed at.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from ..compiler import VIZ_TYPE
from ..versions import parse_version
from .content import strip_comments
from .model import AXIS_TYPES, KPI_TYPES, Finding, RuleContext, rule

# -- how each release draws text ---------------------------------------------------


@dataclass(frozen=True)
class Release:
    name: str
    modern: bool        # antd theme tokens: 6.0 and later
    root_px: float      # 1rem
    table_line: float   # the line-height factor a table chart's cell draws at
    table_pad: float    # a table chart cell's padding, above and below


# 4.1.4 and 5.0.0: `html, body { font-size: @font-size-base }`, 14px (less/index.less
# :26-30, variables.less:151); Bootstrap's `.table > tbody > tr > td` takes Superset's
# @line-height-base 1.4 (variables.less:162) and `.table-condensed` cells
# @table-condensed-cell-padding 5px (less/cosmo/variables.less:130).
LEGACY = Release("4.1.4 and 5.0.0", False, 14.0, 1.4, 5.0)
# 6.0 and later: the browser's 16 px root, `padding: 0.3rem` (Styles.tsx:79-84), and
# the line height cells inherit from the body: antd's (fontSize + 8) / fontSize.
MODERN = Release("6.0 and later", True, 16.0, 22 / 14, 4.8)

DEFAULT_TEXT = 12.0     # table and pivot cells, axis labels, legends
DEFAULT_TITLE = 16.0    # chart titles
PIVOT_LINE = 1.4        # table.pvtTable { line-height: 1.4 } (Styles.js:32, Styles.ts:33)
PIVOT_PAD = 4.0         # theme.gridUnit / sizeUnit (Styles.js:106, Styles.ts:113)
BORDER = 1.0            # each row's 1 px rule
VIEWPORT = 1440.0       # the window the floors assume: a laptop
PAGE_MARGIN = 64.0      # the window's width less the grid's
GUTTER = 16.0           # GRID_GUTTER_SIZE between columns
CARD_PAD = 16.0         # a chart holder's padding, each side
FILTER_BAR = 260.0      # OPEN_FILTER_BAR_WIDTH (dashboard/constants.ts:40 at 4.1.4)

# Superset's own rule for (chart type, element, property): its specificity on
# (LEGACY, MODERN), None where it has none there. A dashboard rule applies where it is
# at least as specific (a tie goes to the dashboard's CSS: measured on all three).
# A styled wrapper's emotion class adds (0,1,0) to the plugin's selectors.
_SUPERSET = {
    # `.table-condensed` (superset.less:178); `.css-x table.table-condensed`
    ("table", "table", "font-size"): ((0, 1, 0), (0, 2, 1)),
    # Bootstrap's `.table > tbody > tr > td` and `.table-condensed > tbody > tr > td`;
    # `.css-x table.table-condensed td` (Styles.tsx:79-84)
    ("table", "td", "line-height"): ((0, 1, 3), None),
    ("table", "th", "line-height"): ((0, 1, 3), None),
    ("table", "td", "padding"): ((0, 1, 3), (0, 2, 2)),
    ("table", "th", "padding"): ((0, 1, 3), (0, 2, 2)),
    # `.css-x table.pvtTable` and its cells (Styles.js:24-50 and :104-106)
    ("pivot_table", "table", "font-size"): ((0, 2, 1), (0, 2, 1)),
    ("pivot_table", "table", "line-height"): ((0, 2, 1), (0, 2, 1)),
    ("pivot_table", "th", "font-size"): ((0, 2, 4), (0, 2, 4)),
    ("pivot_table", "td", "padding"): ((0, 2, 4), (0, 2, 4)),
    ("pivot_table", "th", "padding"): ((0, 2, 4), (0, 2, 4)),
}

GRID_KINDS = ("table", "pivot_table")
KIND_WORDS = {"table": "tables", "pivot_table": "pivots"}
KIND_CLASS = {"table": ".superset-chart-table", "pivot_table": ".pivot_table_v_2"}


def releases(ctx: RuleContext) -> tuple[Release, ...]:
    """The releases this spec can go to: the one resolution checked, 6.0 and later for a
    spec naming a theme (versions.py refuses one before 6.0.0), or all of them."""
    known = parse_version(ctx.resolution.superset_version) if ctx.resolution else None
    if known:
        return (MODERN,) if known >= (6, 0, 0) else (LEGACY,)
    if ctx.spec.dashboard.theme:
        return (MODERN,)
    return (LEGACY, MODERN)


def _echarts_overrides(ctx: RuleContext) -> bool:
    """A theme's ECharts overrides take effect: on 6.1 and later, read as 6.1.0 when the
    release is unknown."""
    known = parse_version(ctx.resolution.superset_version) if ctx.resolution else None
    return known is None or known >= (6, 1, 0)


# -- theme tokens ------------------------------------------------------------------


def _num(value) -> float | None:
    """A token's number: Superset's own config writes some as strings ("8")."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(?:px)?\s*", value)
        return float(m.group(1)) if m else None
    return None


def _antd_sizes(base: float) -> list[float]:
    """antd 5's font scale from a base size (theme/themes/shared/genFontSizes.js)."""
    sizes = []
    for index in range(10):
        size = base * math.exp((index - 1) / 5)
        whole = math.floor(size) if index > 1 else math.ceil(size)
        sizes.append(float(whole // 2 * 2))
    sizes[1] = float(base)
    return sizes


@dataclass(frozen=True)
class Tokens:
    sm: float = DEFAULT_TEXT          # fontSizeSM: table and pivot text
    lg: float = DEFAULT_TITLE         # fontSizeLG: chart titles
    body: float = 14.0                # fontSize
    line: float = 22 / 14             # lineHeight, the factor cells inherit
    theme: str | None = None          # the theme's name when one was read
    written: tuple[str, ...] = ()     # the size tokens it writes (algorithm included)

    def size(self, px: float) -> "Size":
        """A size these tokens give: the theme's when it writes any size token (one it
        writes, or one antd derives from it), else Superset's default."""
        if not self.written:
            return Size(px, "superset")
        return Size(px, "theme", f"theme {self.theme}: {', '.join(self.written)}")


def tokens(theme_json, name: str | None = None) -> Tokens:
    """The size tokens a theme's JSON leaves a 6.x dashboard with."""
    if not isinstance(theme_json, dict):
        return Tokens()
    token = theme_json.get("token") if isinstance(theme_json.get("token"), dict) else {}
    algorithm = theme_json.get("algorithm")
    algorithms = algorithm if isinstance(algorithm, list) else [algorithm]
    compact = "compact" in algorithms
    seed = _num(token.get("fontSize")) or 14.0
    base = _antd_sizes(seed)[0] if compact else seed
    sizes = _antd_sizes(base)
    written = tuple(k for k in ("fontSize", "fontSizeSM", "fontSizeLG", "lineHeight")
                    if _num(token.get(k)) is not None)
    if compact:
        written += ("algorithm: compact",)
    body = _num(token.get("fontSize")) or sizes[1]
    return Tokens(sm=_num(token.get("fontSizeSM")) or sizes[0],
                  lg=_num(token.get("fontSizeLG")) or sizes[2],
                  body=body, line=_num(token.get("lineHeight")) or (sizes[1] + 8) / sizes[1],
                  theme=name, written=written)


def _theme(ctx: RuleContext) -> tuple[Tokens, dict | None]:
    """The spec's theme as resolution read it: its tokens and its JSON, or the defaults
    when the spec names none or this run didn't read it."""
    data = getattr(ctx.resolution, "theme_json", None) if ctx.resolution else None
    if not ctx.spec.dashboard.theme or not isinstance(data, dict):
        return Tokens(), None
    return tokens(data, ctx.spec.dashboard.theme), data


def _theme_unread(ctx: RuleContext) -> str:
    theme = ctx.spec.dashboard.theme
    if theme and _theme(ctx)[1] is None:
        return (f"; the theme {theme!r} was not read in this run (`advise --profile` reads "
                f"its tokens and ECharts overrides)")
    return ""


# -- the dashboard's CSS ------------------------------------------------------------


@dataclass(frozen=True)
class Decl:
    selector: str
    element: str                 # td | th | row | table | title | kpi-value | kpi-label
    kinds: frozenset | None      # the chart types it reaches; None: any
    prop: str                    # font-size | line-height | padding-top | padding-bottom | height
    value: str
    important: bool
    spec: tuple[int, int, int]
    order: int
    area: str | None = None      # head | body: a selector naming thead or tbody


@dataclass(frozen=True)
class Sheet:
    decls: tuple[Decl, ...] = ()
    # (selector, property, why, element) for each rule the reader can't place, which
    # could size the text these rules judge.
    unread: tuple[tuple[str, str, str, str], ...] = ()


# Classes only one chart type's markup carries: a selector naming one reaches only it.
_SCOPE = {
    "superset-chart-table": {"table"}, "table": {"table"}, "table-condensed": {"table"},
    "table-striped": {"table"}, "pivot_table_v_2": {"pivot_table"},
    "pvtTable": {"pivot_table"}, "big_number_total": {"big_number_total"},
    "big_number": {"big_number_trend"}, "echarts_timeseries_line": {"timeseries_line"},
    "echarts_timeseries_bar": {"timeseries_bar", "bar"}, "echarts_area": {"timeseries_area"},
    "echarts_timeseries_scatter": {"timeseries_scatter"}, "pie": {"pie"},
    "heatmap_v_2": {"heatmap"}, "histogram_v_2": {"histogram"}, "funnel": {"funnel"},
    "treemap_v_2": {"treemap"}, "mixed_timeseries": {"mixed"},
}
_TABLE_CLASSES = {"table", "table-condensed", "table-striped", "pvtTable"}
# The layout around every chart, as rendered at 4.1.4 and 6.1.0 (sec.17): naming one
# narrows nothing. Any other class might match nothing, so a rule naming one is unread.
_AROUND = frozenset({
    "dashboard", "dashboard-content", "grid-container", "dashboard-grid", "grid-content",
    "grid-row", "background--transparent", "background--white", "dragdroppable",
    "dragdroppable-row", "dragdroppable-column", "with-popover-menu", "resizable-container",
    "dashboard-component", "dashboard-component-chart-holder", "dashboard-component-tabs",
    "dashboard-component-tabs-content", "dashboard-component-row",
    "dashboard-component-column", "chart-slice", "dashboard-chart", "chart-container",
    "slice_container", "text-container", "superset-legacy-chart-big-number", "no-trendline",
    "ant-tabs", "ant-tabs-top", "ant-tabs-content", "ant-tabs-content-top",
    "ant-tabs-content-holder", "ant-tabs-tabpane", "ant-tabs-tabpane-active", "ant-layout",
    "ant-layout-content"})
_ROLE_CLASSES = {"header-title": "title", "header-line": "kpi-value",
                 "subheader-line": "kpi-label", "subtitle-line": "kpi-label"}
_PROPS = {"font", "font-size", "line-height", "padding", "padding-top", "padding-bottom",
          "height", "min-height"}
_TOKEN = re.compile(r"""
    (?P<comb>\s*[>+~]\s*|\s+)
  | (?P<tag>\*|[A-Za-z][\w-]*)
  | \.(?P<cls>[\w-]+)
  | \#(?P<id>[\w-]+)
  | (?P<attr>\[[^\]]*\])
  | (?P<pe>::[\w-]+(?:\([^)]*\))?)
  | (?P<pc>:[\w-]+(?:\((?:[^()]|\([^()]*\))*\))?)
""", re.X)


def _rules(css: str):
    """(selector list, declarations, the at-rule it sits in or None) per style rule."""
    i, n = 0, len(css)
    while i < n:
        j = css.find("{", i)
        if j < 0:
            return
        prelude = css[i:j].rsplit(";", 1)[-1].strip().lstrip("}").strip()
        depth, k = 1, j + 1
        while k < n and depth:
            depth += {"{": 1, "}": -1}.get(css[k], 0)
            k += 1
        body = css[j + 1:k - 1]
        if prelude.startswith("@"):
            if re.match(r"@(?:media|supports|layer|container|document)\b", prelude, re.I):
                for sel, decls, _ in _rules(body):
                    yield sel, decls, " ".join(prelude.split())
        else:
            yield prelude, body, None
        i = k


def _compounds(selector: str) -> list[dict] | None:
    """A selector as its compounds, or None when it doesn't parse."""
    out = [{"tag": None, "cls": [], "id": [], "attr": [], "pc": [], "pe": []}]
    pos = 0
    while pos < len(selector):
        m = _TOKEN.match(selector, pos)
        if not m or m.end() == pos:
            return None
        kind = m.lastgroup
        if kind == "comb":
            out.append({"tag": None, "cls": [], "id": [], "attr": [], "pc": [], "pe": []})
        elif kind == "tag":
            out[-1]["tag"] = None if m.group("tag") == "*" else m.group("tag").lower()
        else:
            out[-1][kind].append(m.group(kind))
        pos = m.end()
    return out


def _specificity(compounds: list[dict]) -> tuple[int, int, int]:
    return (sum(len(c["id"]) for c in compounds),
            sum(len(c["cls"]) + len(c["attr"]) + len(c["pc"]) for c in compounds),
            sum(bool(c["tag"]) + len(c["pe"]) for c in compounds))


def _place(compounds: list[dict]) -> tuple[str | None, frozenset | None, str | None]:
    """(element, chart types reached, why it can't be read) for a selector."""
    every = [x for c in compounds for x in c["cls"]]
    if "dashboard-markdown" in every or any(i.startswith("MARKDOWN") for c in compounds
                                            for i in c["id"]):
        return None, None, None          # markdown text: not what these rules judge
    subject = compounds[-1]
    if subject["pe"]:
        return None, None, None          # ::before and the like style generated text
    tag, cls = subject["tag"], set(subject["cls"])
    if tag in ("td", "th"):
        element = tag
    elif tag in ("tr", "tbody", "thead", "tfoot"):
        element = "row"
    elif (tag == "table" or (cls & _TABLE_CLASSES)) and tag in (None, "table"):
        element = "table"
    elif cls & set(_ROLE_CLASSES):
        element = _ROLE_CLASSES[sorted(cls & set(_ROLE_CLASSES))[0]]
    else:
        return None, None, None
    kinds = None
    for c in every:
        if c in _SCOPE:
            kinds = set(_SCOPE[c]) if kinds is None else kinds & _SCOPE[c]
    if element == "table" and kinds is None:
        kinds = {"table"} if cls & {"table", "table-condensed", "table-striped"} else (
            {"pivot_table"} if "pvtTable" in cls else set(GRID_KINDS))
    if any(c["pc"] for c in compounds):
        return element, None, "it applies to some rows, cells or states (a pseudo-class)"
    if (any(c["id"] or c["attr"] for c in compounds)
            or any(re.fullmatch(r"dashboard-chart-id-\d+", x) for x in every)):
        return element, None, "it applies to some charts (an id or attribute)"
    if element in ("td", "th", "row") and cls:
        return element, None, "it applies to some cells (a class on the cell)"
    known = set(_SCOPE) | _AROUND | _TABLE_CLASSES | set(_ROLE_CLASSES)
    for x in every:
        if x not in known:
            m = re.fullmatch(r"(\w+)_v(\d+)", x)
            hint = (f"; Superset renders the class `.{m.group(1)}_v_{m.group(2)}`" if m
                    else "")
            return element, None, f"it names `.{x}`, a class this check doesn't know{hint}"
    return element, (frozenset(kinds) if kinds is not None else None), None


def _declarations(body: str):
    for part in re.split(r";(?![^(]*\))", body):
        prop, sep, value = part.partition(":")
        if not sep:
            continue
        value = " ".join(value.split())
        important = bool(re.search(r"!\s*important\s*$", value, re.I))
        value = re.sub(r"\s*!\s*important\s*$", "", value, flags=re.I).strip()
        yield prop.strip().lower(), value, important


def _longhands(prop: str, value: str) -> list[tuple[str, str]] | None:
    """The properties read, from one declaration; None when its value can't be split."""
    if prop in ("font-size", "line-height", "padding-top", "padding-bottom"):
        return [(prop, value)]
    if prop in ("height", "min-height"):
        return [("height", value)]
    if prop == "padding":
        parts = value.split()
        if not 1 <= len(parts) <= 4:
            return None
        return [("padding-top", parts[0]),
                ("padding-bottom", parts[2] if len(parts) > 2 else parts[0])]
    # font: [style variant weight] size[/line-height] family; it resets line-height.
    m = re.search(r"(?:^|\s)(\d*\.?\d+(?:px|rem|em|%))(?:\s*/\s*(\S+))?\s+\S", value)
    return [("font-size", m.group(1)), ("line-height", m.group(2) or "normal")] if m else None


def read_css(css: str | None) -> Sheet:
    """The declarations of a dashboard's CSS these rules apply, and the rules they can't
    place that could size the same text."""
    decls, unread = [], []
    order = 0
    for selectors, body, at in _rules(strip_comments(css or "")):
        wanted = [(p, v, imp) for p, v, imp in _declarations(body) if p in _PROPS]
        if not wanted:
            continue
        for text in re.split(r",(?![^(\[]*[)\]])", selectors):
            text = " ".join(text.split())
            compounds = _compounds(text)
            if compounds is None:
                continue
            element, kinds, why = _place(compounds)
            if element is None:
                continue
            if at and not why:
                why = f"it sits in {at}, which applies at some window sizes"
            for prop, value, important in wanted:
                if why:
                    unread.append((text, prop, why, element))
                    continue
                longs = _longhands(prop, value)
                if longs is None or any(re.search(r"\b(?:var|calc|clamp|min|max)\(", v)
                                        for _, v in longs):
                    unread.append((text, prop, "its value can't be resolved offline",
                                   element))
                    continue
                order += 1
                tags = {c["tag"] for c in compounds}
                area = "head" if "thead" in tags else "body" if "tbody" in tags else None
                for p, v in longs:
                    decls.append(Decl(text, element, kinds, p, v, important,
                                      _specificity(compounds), order, area))
    return Sheet(tuple(decls), tuple(dict.fromkeys(unread)))


def _sheet(ctx: RuleContext) -> Sheet:
    cached = getattr(ctx, "_readability_sheet", None)
    if cached is None:
        cached = ctx._readability_sheet = read_css(ctx.spec.dashboard.css)
    return cached


def _length(value: str, release: Release, font: float) -> float | None:
    """px of a length: px, rem, or em and % of `font`."""
    m = re.fullmatch(r"(-?\d*\.?\d+)(px|rem|em|%)?", value.strip().lower())
    if not m:
        return None
    n, unit = float(m.group(1)), m.group(2)
    if unit == "px" or (unit is None and n == 0):
        return n
    return {"rem": n * release.root_px, "em": n * font, "%": n * font / 100}.get(unit)


def _line(value: str, release: Release, font: float) -> float | None:
    """px of a line height: a factor, a length, or normal (Inter's 1.2)."""
    v = value.strip().lower()
    if v == "normal":
        return 1.2 * font
    if re.fullmatch(r"\d*\.?\d+", v):
        return float(v) * font
    return _length(v, release, font)


def _winner(sheet: Sheet, kind: str, element: str, prop: str,
            area: str | None = None) -> Decl | None:
    """The dashboard's declaration that wins for this property on this element of this
    chart type: !important first, then specificity, then the later one. `area` keeps
    only what reaches the header (head) or the body rows (body)."""
    found = [d for d in sheet.decls if d.element == element and d.prop == prop
             and (d.kinds is None or kind in d.kinds)
             and (area is None or d.area in (None, area))]
    return max(found, key=lambda d: (d.important, d.spec, d.order), default=None)


def _beats(d: Decl, kind: str, element: str, prop: str, release: Release) -> bool:
    base = "padding" if prop.startswith("padding") else prop
    theirs = _SUPERSET.get((kind, element, base))
    rival = theirs[1 if release.modern else 0] if theirs else None
    return d.important or rival is None or d.spec >= rival


def _rival(kind: str, element: str, prop: str, release: Release) -> bool:
    theirs = _SUPERSET.get((kind, element, prop))
    return bool(theirs and theirs[1 if release.modern else 0])


@dataclass
class Size:
    px: float
    source: str                       # superset | theme | css
    by: str = ""                      # the selector or token that set it
    lost: tuple[str, ...] = ()        # dashboard selectors Superset's own rule beat


def _font(sheet: Sheet, kind: str, element: str, release: Release, tok: Tokens,
          area: str | None = None) -> Size:
    """A table or pivot cell's font size: its own rule, else what it inherits from its
    row, else its table's (the dashboard's where it beats Superset's), else the default."""
    lost: list[str] = []
    base = tok.size(tok.sm) if release.modern else Size(DEFAULT_TEXT, "superset")
    size = Size(base.px, base.source, base.by)
    d = _winner(sheet, kind, "table", "font-size")
    if d is not None:
        if _beats(d, kind, "table", "font-size", release):
            px = _length(d.value, release, tok.body if release.modern else 14.0)
            if px is not None:
                size = Size(px, "css", d.selector)
        else:
            lost.append(d.selector)
    d = _winner(sheet, kind, "row", "font-size", area)
    if d is not None:
        px = _length(d.value, release, size.px)
        if px is not None:
            size = Size(px, "css", d.selector)
    if _rival(kind, element, "font-size", release):
        # The cell's own rule (a pivot's header cells): nothing inherited reaches it.
        size = Size(base.px, base.source, base.by)
    d = _winner(sheet, kind, element, "font-size", area)
    if d is not None:
        if _beats(d, kind, element, "font-size", release):
            px = _length(d.value, release, size.px)
            if px is not None:
                size = Size(px, "css", d.selector)
        else:
            lost.append(d.selector)
    size.lost = tuple(lost)
    return size


def _cell_row(sheet: Sheet, kind: str, element: str, release: Release, tok: Tokens,
              font: float, area: str | None = None) -> Size:
    """How tall one cell makes its row: its line box and padding, at least any height
    set on it or its row."""
    line = (tok.line if release.modern else release.table_line) if kind == "table" else PIVOT_LINE
    line_px, by = line * font, ""
    for scope in ("table", "row", element):
        if scope == element and _rival(kind, element, "line-height", release):
            line_px, by = line * font, ""   # the cell's own rule, unless the CSS beats it
        d = _winner(sheet, kind, scope, "line-height", area)
        if d is not None and _beats(d, kind, scope, "line-height", release):
            px = _line(d.value, release, font)
            if px is not None:
                line_px, by = px, d.selector
    pad = release.table_pad if kind == "table" else PIVOT_PAD
    pads = []
    for prop in ("padding-top", "padding-bottom"):
        d = _winner(sheet, kind, element, prop, area)
        px = (_length(d.value, release, font) if d is not None
              and _beats(d, kind, element, prop, release) else None)
        if px is not None:
            by = by or d.selector
        pads.append(pad if px is None else px)
    least = 0.0
    for scope in ("row", element):
        d = _winner(sheet, kind, scope, "height", area)
        px = _length(d.value, release, font) if d is not None else None
        if px is not None and px > least:
            least, by = px, d.selector
    px = max(least, line_px + sum(pads) + BORDER)
    return Size(round(px, 1), "css" if by else "superset", by)


def _row(sheet: Sheet, kind: str, release: Release, tok: Tokens) -> Size:
    """A body row's height: its tallest cell's. A pivot's row labels are header cells
    (th.pvtRowLabel) in the same row as its values. A row the dashboard's own font size
    shrinks is the dashboard's doing, as one its spacing shrinks is."""
    rows = []
    for element in ("td", "th") if kind == "pivot_table" else ("td",):
        font = _font(sheet, kind, element, release, tok, "body")
        row = _cell_row(sheet, kind, element, release, tok, font.px, "body")
        if row.source == "superset" and font.source != "superset":
            row = Size(row.px, font.source, font.by)
        rows.append(row)
    return max(rows, key=lambda s: s.px)


# -- readability.table-text ---------------------------------------------------------


def _charts_of(ctx: RuleContext, kinds) -> str:
    parts = []
    for kind in GRID_KINDS:
        names = [c.name for c in ctx.spec.charts if c.type == kind]
        if kind in kinds and names:
            parts.append(f"{KIND_WORDS[kind]} ({', '.join(names)})")
    return " and ".join(parts)


def _per_release(rows: list[tuple[str, Release, Size]], rels) -> str:
    """'12 px' when it is that size on every release the spec can go to, else each
    release's size: '12 px on 6.0 and later'."""
    by_size: dict[str, list[str]] = {}
    for _, r, s in rows:
        names = by_size.setdefault(f"{s.px:g} px", [])
        if r.name not in names:
            names.append(r.name)
    if len(by_size) == 1 and len(next(iter(by_size.values()))) == len(rels):
        return next(iter(by_size))
    return "; ".join(f"{size} on {' and '.join(names)}" for size, names in by_size.items())


def _setters(rows) -> str:
    by = sorted({s.by for _, _, s in rows if s.by})
    return f" (set by `{'`, `'.join(by)}`)" if by else ""


def _losers(rows, rels) -> str:
    lost = sorted({x for _, _, s in rows for x in s.lost})
    if not lost:
        return ""
    where = sorted({r.name for _, r, s in rows if s.lost})
    there = "" if len(where) == len(rels) else f" on {' and '.join(where)}"
    return (f". `{'`, `'.join(lost)}` loses to Superset's own, more specific rule{there}: "
            f"set the size on the cells")


def _unread(ctx: RuleContext, elements: set[str]) -> str:
    """The rules the reader couldn't place on these elements, named."""
    rows = [(s, p, why) for s, p, why, e in _sheet(ctx).unread if e in elements]
    if not rows:
        return ""
    shown = "; ".join(f"`{s}` {p}: {why}" for s, p, why in rows[:3])
    more = f" and {len(rows) - 3} more" if len(rows) > 3 else ""
    return f". Not read: {shown}{more}"


@rule("readability.table-text", "info",
      "table and pivot cells read at >= 14 px on a laptop, their headers at >= 12 px and "
      "rows at >= 24 px (Superset draws cells at 12 px: dashboard.css "
      "`.superset-chart-table td, .pivot_table_v_2 td { font-size: 14px; }`)",
      since="13", severities=("info", "warn"))
def table_text(ctx: RuleContext):
    kinds = [k for k in GRID_KINDS if any(c.type == k for c in ctx.spec.charts)]
    if not kinds:
        return
    sheet, rels = _sheet(ctx), releases(ctx)
    tok, _ = _theme(ctx)
    p = ctx.params
    cells = {(k, r.name): _font(sheet, k, "td", r, tok, "body") for k in kinds for r in rels}
    heads = {(k, r.name): _font(sheet, k, "th", r, tok, "head") for k in kinds for r in rels}
    rows = {(k, r.name): _row(sheet, k, r, tok) for k in kinds for r in rels}
    roles = (
        ("cells", "td", cells, p.min_cell_text_px, "text read row by row"),
        ("headers", "th", heads, p.min_label_text_px, "labels"),
        ("rows", "row", rows, p.min_row_px, "a row"),
    )
    for where, element, sizes, floor, what in roles:
        low = [(k, r, sizes[(k, r.name)]) for k in kinds for r in rels
               if sizes[(k, r.name)].px < floor]
        if not low:
            continue
        own = [x for x in low if x[2].source != "superset"]
        severity = "warn" if own else "info"
        shown = own or low
        whose = _charts_of(ctx, {k for k, _, _ in shown})
        size = _per_release(shown, rels)
        if element == "row":
            detail = (f"{whose} draw body rows {size} tall{_setters(shown)}, below the "
                      f"{floor:g} px floor for {what}: give the cells room (line-height, "
                      f"padding) or drop the height set on them")
        else:
            source = "" if own else ", Superset's default"
            detail = (f"{whose} draw their {where} at {size}{source}{_setters(shown)}: below "
                      f"the {floor:g} px floor for {what} on a laptop{_losers(shown, rels)}. "
                      + _remedy(element, {k for k, _, _ in shown}, floor, rels))
        detail += _unread(ctx, {element, "row", "table"} if element != "row" else {"td", "row"})
        detail += _theme_unread(ctx)
        yield Finding("readability.table-text", severity, None, f"table {where}", detail)


def _remedy(element: str, kinds: set[str], floor: float, rels) -> str:
    sel = ", ".join(f"{KIND_CLASS[k]} {element}" for k in GRID_KINDS if k in kinds)
    if element == "th" and "pivot_table" in kinds:
        # A pivot's header cells carry Superset's own (0,2,4) rule: match it.
        sel = ", ".join(([".superset-chart-table th"] if "table" in kinds else [])
                        + [".pivot_table_v_2 table.pvtTable thead tr th",
                           ".pivot_table_v_2 table.pvtTable tbody tr th"])
    out = f"Set it in dashboard.css: `{sel} {{ font-size: {floor:g}px; }}`"
    if any(r.modern for r in rels):
        out += ("; on 6.0 and later the theme token fontSizeSM sets it too, with every "
                "other small text on the dashboard")
    if element == "td":
        out += (". Larger cells draw taller rows (at 14 px: 30.6 px on 4.1.4 and 5.0.0, "
                "32.6 px on 6.1.0, against the 29.6 px the table-height rules count, and "
                "long text wraps in narrow columns), so check table heights on the "
                "rendered dashboard")
    return out


# -- readability.chart-text -----------------------------------------------------------



def _components(layer: dict, key: str, path: tuple[str, ...]) -> list[float]:
    value = layer.get(key)
    items = value if isinstance(value, list) else [value]
    out = []
    for item in items:
        for step in path:
            item = item.get(step) if isinstance(item, dict) else None
        if _num(item) is not None:
            out.append(_num(item))
    return out


def echarts_size(theme_json: dict | None, viz: str, role: str) -> tuple[float, str] | None:
    """(px, where it's set) for an axis label or legend a theme's ECharts overrides set on
    one chart type: the component's own size beats a global textStyle, and the chart
    type's overrides beat the global ones (Echart.tsx:267-273)."""
    if not isinstance(theme_json, dict):
        return None
    by = theme_json.get("echartsOptionsOverridesByChartType")
    layers = [(f"echartsOptionsOverridesByChartType.{viz}",
               by.get(viz) if isinstance(by, dict) else None),
              ("echartsOptionsOverrides", theme_json.get("echartsOptionsOverrides"))]
    layers = [(n, l) for n, l in layers if isinstance(l, dict)]
    found = []
    keys = (("xAxis", ("axisLabel", "fontSize")), ("yAxis", ("axisLabel", "fontSize"))) \
        if role == "axis" else (("legend", ("textStyle", "fontSize")),)
    for key, path in keys:
        size = None
        for name, layer in layers:
            got = _components(layer, key, path)
            if got:
                size = (min(got), f"{name}.{key}.{'.'.join(path)}")
                break
        if size is None:
            for name, layer in layers:
                text = layer.get("textStyle")
                if isinstance(text, dict) and _num(text.get("fontSize")) is not None:
                    size = (_num(text["fontSize"]), f"{name}.textStyle.fontSize")
                    break
        if size is not None:
            found.append(size)
    return min(found) if found else None


@rule("readability.chart-text", "info",
      "axis labels and legends read at >= 12 px and chart titles at >= 14 px (a theme's "
      "tokens or ECharts overrides, or dashboard CSS, can set them smaller)",
      since="13", severities=("info", "warn"))
def chart_text(ctx: RuleContext):
    p = ctx.params
    rels = releases(ctx)
    tok, data = _theme(ctx)
    overrides = data if _echarts_overrides(ctx) else None
    roles = (
        ("axis labels", "axis", [c for c in ctx.spec.charts if c.type in AXIS_TYPES],
         p.min_label_text_px),
        # A heatmap's show_legend is its colour scale, not a legend of names.
        ("legends", "legend", [c for c in ctx.spec.charts if getattr(c, "show_legend", False)
                               and c.type != "heatmap"], p.min_label_text_px),
    )
    for where, role, charts, floor in roles:
        low = []
        for c in charts:
            for r in rels:
                # A theme overrides by viz_type, as the compiler writes it.
                got = echarts_size(overrides, VIZ_TYPE[c.type], role) if r.modern else None
                size = Size(got[0], "theme", got[1]) if got else Size(DEFAULT_TEXT, "superset")
                if size.px < floor:
                    low.append((c.name, r, size))
        if not low:
            continue
        own = [x for x in low if x[2].source != "superset"]
        names = sorted({n for n, _, _ in (own or low)})
        if own:
            by = sorted({s.by for _, _, s in own})
            detail = (f"the theme {tok.theme!r} sets {where} on {', '.join(names)} to "
                      f"{_per_release(own, rels)} (`{'`, `'.join(by)}`): below the "
                      f"{floor:g} px floor for labels on a laptop; raise it to {floor:g}")
        else:
            detail = (f"{', '.join(names)} draw {where} at {DEFAULT_TEXT:g} px, ECharts' "
                      f"default, below the {floor:g} px floor set for labels. Nothing in a "
                      f"spec or dashboard CSS reaches them (ECharts draws on a canvas); on "
                      f"6.1 and later a theme's echartsOptionsOverrides.textStyle.fontSize "
                      f"sets them")
        yield Finding("readability.chart-text", "warn" if own else "info", None, where,
                      detail + _theme_unread(ctx))
    yield from _titles(ctx, rels, tok)


def _titles(ctx: RuleContext, rels, tok: Tokens):
    floor = ctx.params.min_title_text_px
    if not ctx.spec.charts:
        return
    low = []
    for r in rels:
        size = tok.size(tok.lg) if r.modern else Size(DEFAULT_TITLE, "superset")
        d = _winner(_sheet(ctx), "any", "title", "font-size")
        if d is not None:
            px = _length(d.value, r, size.px)
            if px is not None:
                size = Size(px, "css", d.selector)
        if size.px < floor:
            low.append(("titles", r, size))
    if not low:
        return
    own = [x for x in low if x[2].source != "superset"]
    shown = own or low
    by = sorted({s.by for _, _, s in shown if s.by})
    detail = (f"chart titles draw at {_per_release(shown, rels)}"
              + (f" (set by `{'`, `'.join(by)}`)" if by else ", Superset's default")
              + f": below the {floor:g} px floor for a chart's heading on a laptop. Set "
              f"`.header-title {{ font-size: {floor:g}px; }}` in dashboard.css"
              + ("; on 6.0 and later the theme token fontSizeLG sets it"
                 if any(r.modern for r in rels) else ""))
    yield Finding("readability.chart-text", "warn" if own else "info", None, "chart titles",
                  detail + _unread(ctx, {"title"}) + _theme_unread(ctx))


# -- readability.kpi-text -----------------------------------------------------------

# What each release drew for a big number at each whole height, the smallest of the
# three (px; sec.17): the value, and the subtitle or comparison under it. A trendline
# KPI gives its line 30% of the card, so its text is smaller. Measured with chartwright's
# pinned proportions at 1440 px, on cards wide enough that height alone set the size.
KPI_TOTAL = {2: (6, 2), 3: (18, 6), 4: (31, 11), 5: (43, 17), 6: (57, 22), 7: (70, 26),
             8: (84, 31), 9: (97, 36), 10: (109, 41)}
KPI_TREND = {2: (5, 2), 3: (12, 4), 4: (22, 8), 5: (31, 11), 6: (39, 14), 7: (50, 19),
             8: (59, 22), 9: (68, 26), 10: (76, 29)}
KPI_MAX_HEIGHT = 6      # size.kpi-height's ceiling: a fix past it would fight that rule
EM_PER_CHAR = 0.5       # a narrow guess at Inter's average width (it measured 0.50-0.56)


def kpi_px(kind: str, height: float) -> tuple[float, float]:
    """(value, subtitle) px at a height in units, between and past the measured ones."""
    table = KPI_TREND if kind == "big_number_trend" else KPI_TOTAL
    if height < 2:
        return 0.0, 0.0
    lo = min(math.floor(height), 9)
    a, b = table[lo], table[lo + 1]
    t = height - lo
    return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t


def kpi_units(kind: str, lines: bool, params) -> int | None:
    """The least whole height at which a big number's value, and its lines when it has
    any, reach the floors, by height alone (None: past KPI_MAX_HEIGHT)."""
    for units in range(2, KPI_MAX_HEIGHT + 1):
        value, label = kpi_px(kind, units)
        if value >= params.min_kpi_value_px and (not lines or label >= params.min_label_text_px):
            return units
    return None


def _card_text_px(ctx: RuleContext, width: int) -> float:
    """The px a card's text may span: computeMaxFontSize's 90% of the card less its
    padding, with the vertical filter bar open when the dashboard has native filters."""
    bar = FILTER_BAR if (ctx.spec.filters and ctx.spec.dashboard.filter_bar_orientation
                         != "horizontal") else 0.0
    pitch = (VIEWPORT - PAGE_MARGIN - bar + GUTTER) / 12
    return 0.9 * (pitch * width - GUTTER - 2 * CARD_PAD)


def _kpi_lines(ctx: RuleContext, c) -> list[tuple[str, str]]:
    """(what, text) of each line under the value the card draws."""
    lines = []
    if c.type == "big_number_trend" and c.compare_lag is not None:
        lines.append(("comparison", f"+00.0% {c.compare_suffix or ''}".strip()))
    if getattr(c, "subtitle", None) and (c.type == "big_number_total"
                                          or any(r.modern for r in releases(ctx))):
        lines.append(("subtitle", c.subtitle))
    return lines


def _kpi_css(ctx: RuleContext, c, element: str) -> Decl | None:
    """An !important size from the dashboard's CSS: the only kind that beats the inline
    style BigNumberViz writes."""
    d = _winner(_sheet(ctx), c.type, element, "font-size")
    return d if d is not None and d.important else None


@dataclass
class _Kpi:
    chart: object
    height: float
    lines: list                  # (what, text) under the value
    short: list                  # what draws below its floor at this height, worded
    css: list                    # (what, px, selector): sizes the CSS pins below a floor
    need: int | None = None      # the whole height that brings it to the floors
    capped: str = ""             # why no height can: the card is too narrow


def _kpi(ctx: RuleContext, c) -> _Kpi:
    p = ctx.params
    h = ctx.height(c.name)
    lines = _kpi_lines(ctx, c)
    css, pinned = [], {}
    for element, what in (("kpi-value", "value"), ("kpi-label", "subtitle")):
        d = _kpi_css(ctx, c, element)
        px = _length(d.value, LEGACY, 14.0) if d is not None else None
        if px is not None:
            pinned[element] = px
            floor = p.min_kpi_value_px if element == "kpi-value" else p.min_label_text_px
            if px < floor and (element == "kpi-value" or lines):
                css.append((what, px, d.selector))
    caps = [math.floor(_card_text_px(ctx, ctx.width(c.name)) / (EM_PER_CHAR * len(t)))
            for _, t in lines if t]
    cap = min(caps) if caps else None

    def short(units: float) -> list[str]:
        """What the card's height draws below its floor; a size the CSS pins is the
        CSS finding's, whatever the height."""
        value, label = kpi_px(c.type, units)
        if cap is not None:
            label = min(label, cap)
        out = ([f"value at {value:.0f} px"]
               if "kpi-value" not in pinned and value < p.min_kpi_value_px else [])
        if lines and "kpi-label" not in pinned and label < p.min_label_text_px:
            out.append(f"{' and '.join(w for w, _ in lines)} at {label:.0f} px")
        return out

    k = _Kpi(c, h, lines, short(h), css)
    if not k.short:
        return k
    if (cap is not None and "kpi-label" not in pinned and cap < p.min_label_text_px):
        k.capped = (f"; at {ctx.width(c.name)}/12 the card is too narrow for "
                    f"{p.min_label_text_px:g} px lines at any height: widen it or shorten "
                    f"the {' and '.join(w for w, _ in lines)}")
        return k
    for units in range(math.floor(h) + 1, KPI_MAX_HEIGHT + 1):
        if not short(units):
            k.need = units
            break
    return k


@rule("readability.kpi-text", "info",
      "Superset sizes a big number's text by the card's height: the value needs >= 24 px "
      "and a subtitle or comparison >= 12 px (5 units with a subtitle, 6 for a trendline "
      "KPI's comparison)", fixable=True, since="13", severities=("info", "warn"))
def kpi_text(ctx: RuleContext):
    p = ctx.params
    kpis = {c.name: _kpi(ctx, c) for c in ctx.spec.charts if c.type in KPI_TYPES}
    if not kpis:
        return
    # One band, one height: KPIs side by side rise together, to what the neediest needs.
    goal: dict[str, int] = {}
    for sec in ctx.sections:
        for band in sec.bands:
            names = [n for n in band.chart_names if n in kpis]
            needs = [kpis[n].need for n in names if kpis[n].need]
            if needs:
                goal.update((n, max(needs)) for n in names)
    floors = f"{p.min_kpi_value_px:g} px for the value, {p.min_label_text_px:g} px for its lines"
    for name, k in kpis.items():
        where = ctx.where(name)
        for what, px, selector in k.css:
            floor = p.min_kpi_value_px if what == "value" else p.min_label_text_px
            yield Finding("readability.kpi-text", "warn", name, where,
                          f"dashboard.css sets this big number's {what} to {px:g} px "
                          f"(`{selector}`), below the {floor:g} px floor; raise it, or drop "
                          f"the rule and let the card's height size it")
        target = goal.get(name)
        if k.short:
            drawn = (f"Superset sizes a big number's text by its card: at {k.height:g} units "
                     f"it draws its {' and its '.join(k.short)}, below the floors ({floors})")
            if k.capped:
                yield Finding("readability.kpi-text", "info", name, where, drawn + k.capped)
            elif k.need is None:
                yield Finding("readability.kpi-text", "info", name, where,
                              f"{drawn}, and no height up to {KPI_MAX_HEIGHT} units (where "
                              f"size.kpi-height stops) reaches them")
            else:
                yield Finding("readability.kpi-text", "info", name, where,
                              f"{drawn}; {target} units reach them"
                              + ("" if target == k.need else
                                 " and keep its KPI band at one height"),
                              fix={"chart": name, "set": {"height": target}},
                              height_driven=True)
        elif target is not None and target > k.height:
            yield Finding("readability.kpi-text", "info", name, where,
                          f"its KPI band rises to {target} units so its neighbours' text "
                          f"reaches the floors ({floors}); one band, one height",
                          fix={"chart": name, "set": {"height": target}}, height_driven=True)
