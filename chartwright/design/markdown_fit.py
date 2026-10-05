"""How tall Superset draws a dashboard markdown block, estimated offline from its
text and the dashboard's CSS: `size.markdown-fit` and `layout.markdown-height`
(rules.py) both read it.

What Superset does with a markdown block, at 4.1.4, 5.0.0 and 6.1.0 alike:
- renders the text with react-markdown and GitHub-flavoured markdown (tables,
  strikethrough), raw HTML sanitized (packages/superset-ui-core/src/components/
  SafeMarkdown.tsx at 4.1.4 and 5.0.0, components/SafeMarkdown/SafeMarkdown.tsx at
  6.1.0). The elements sit straight inside the holder, with no wrapper of their own;
- puts it in a holder with 16 px of padding on every side (DashboardBuilder.tsx
  :326-332 at 4.1.4, :325-331 at 5.0.0, :311-317 at 6.1.0) that scrolls what doesn't
  fit (overflow-y: auto; gridComponents/Markdown.jsx:106-109 at 4.1.4 and 5.0.0,
  gridComponents/Markdown/Markdown.tsx:126-130 at 6.1.0). Text past the bottom edge
  is cut off. macOS shows no scrollbar until the reader scrolls inside the block, so
  the text just stops mid-line, as in a screenshot; Windows draws a scrollbar;
- never draws it shorter than 5 grid rows, 40 px: the markdown component passes
  GRID_MIN_ROW_UNITS = 5 (util/constants.ts:42) to ResizableContainer as its
  minHeight (resizable/ResizableContainer.jsx:285-286 at 4.1.4,
  ResizableContainer.tsx:287 at 6.1.0);
- lays the grid out in 12 columns with 16 px gutters (util/constants.ts:38-40), as
  wide as the window less 64 px. Beside the vertical filter bar, which opens by
  default on a dashboard with native filters (DashboardBuilder/state.ts:39-41 at
  4.1.4; OPEN_FILTER_BAR_WIDTH = 260 in dashboard/constants.ts), it is 260 px
  narrower at 4.1.4 and 5.0.0 and 228 px at 6.1.0.

The dashboard's CSS reaches the markdown: every `.dashboard-markdown <element>` rule
beats Superset's own type, on every release, except an h6's font size and the weight
of h4 to h6, which the markdown component sets inside its own class (Markdown.jsx
:92-104 at 4.1.4 and 5.0.0, Markdown/Markdown.tsx:112-124 at 6.1.0). So the estimate
reads the CSS the spec carries and applies it in cascade order, with Superset's own
styles (RELEASES) underneath: rules on `.dashboard-markdown` and its elements, with
descendant, child and sibling combinators (`h4 + p`), :first-child and :last-child,
and the properties that size text: font-size, line-height, font-weight, letter- and
word-spacing, margins, padding, borders and a table's border-spacing. Anything else
that could size markdown (a font family, an attribute selector, a pseudo-element, an
@media block, the block's own padding) is listed in `Stylesheet.unread`, and the
finding names it.

Superset's own styles and the character widths below were measured on 2026-10-05 in
headless Chromium, each release with its default theme (tools/record_markdown_fit.py;
docs/DESIGN-BRAIN.md, "Markdown blocks"). Headings' line heights are a factor, 1.4
(dashboard/styles.ts:21-50), the body's antd's 1.5715, so both follow a font size the
CSS sets. The text wraps word by word as the browser wraps it, with Inter's own
advance widths, a line is as tall as the inline text on it needs, and block margins
collapse as CSS collapses them. tests/test_markdown_fit.py holds what each release
drew for 17 texts at five widths in two windows, beside the filter bar and not, and
for a dashboard's own markdown under its own CSS, and checks both bounds below
against every one.

The rule assumes a window 1440 px wide (VIEWPORT), a common 13-inch laptop's
screen. A wider window wraps fewer lines, so a height that fits there fits wider too.

Two answers, because a rule must be sure before it speaks and its fix must work on
every release:
- low: never more than any release drew. Its characters are 3% narrower than the
  table's and it takes the more compact release in the widest grid, so a block it
  calls cut off is cut off on every release;
- high: never less than any release drew, as Windows draws it. It takes the taller
  release, its characters are 1% wider than the table's, and it allows for two
  classic scrollbars of 17 px: the page's own, which narrows the grid, and one in the
  block. A block that overflows while it loads (before its web font arrives, say)
  draws a scrollbar, and the narrower text can need the extra line that keeps it
  there: 5.0.0 kept one on a two-line text whose first line came within 1% of the
  edge. Every block raised to the high bound showed every line with no inner
  scrollbar, on every release, with Windows-style scrollbars on.
"""

from __future__ import annotations

import html
import math
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache

# Inter's advance widths in em for ASCII 32..126 at weight 400, the widest each
# character measured at any release (canvas measureText): 4.1.4 ships Inter 3, 5.0.0
# and 6.1.0 Inter 4, and their letters differ by under 2%.
_EM = tuple(float(f"0{v}") for v in (
    ".281 .288 .466 .633 .642 .982 .644 .300 .365 .365 .501 .662 .288 .460 .288 .360 "
    ".631 .464 .610 .636 .646 .608 .624 .571 .619 .624 .288 .302 .662 .662 .662 .511 "
    ".966 .690 .654 .730 .722 .601 .590 .746 .743 .269 .571 .672 .565 .903 .753 .765 "
    ".639 .765 .644 .642 .646 .744 .690 .985 .682 .679 .629 .365 .360 .365 .471 .456 "
    ".497 .564 .621 .571 .621 .583 .370 .613 .591 .242 .242 .549 .242 .876 .591 .600 "
    ".612 .612 .376 .528 .364 .591 .562 .818 .546 .562 .552 .426 .333 .426 .662").split())
# The same for Latin-1 and common punctuation and symbols, measured the same way.
_EM_MORE = {ch: em for em, chars in (
    (0.223, "′"), (0.241, "‚"), (0.242, "ìíîï"), (0.261, "‘’"), (0.268, "¸"),
    (0.269, "ÌÍÎÏ"), (0.27, "¦"), (0.281, "\u00a0"), (0.288, "¡·"), (0.313, "¹"),
    (0.384, "‹›"), (0.421, "„"), (0.44, "“”"), (0.442, "²″"), (0.446, "³"), (0.454, "ª"),
    (0.456, "°"), (0.478, "¯"), (0.482, "º"), (0.497, "´"), (0.5, "–"), (0.511, "¿"),
    (0.549, "≤≥≈≠√"), (0.55, "¥"), (0.556, "‡"), (0.562, "ýÿγ"), (0.563, "•"),
    (0.564, "àáâãäå"), (0.568, "§"), (0.571, "¢ç✗"), (0.583, "«»èéêëð"), (0.584, "δ"),
    (0.591, "¨ñùúûü"), (0.596, "β"), (0.6, "òóôõöø"), (0.601, "ÈÉÊË"), (0.603, "¶"),
    (0.604, "●○■□"), (0.611, "™"), (0.612, "þ∆"), (0.615, "◇"), (0.616, "ß"), (0.622, "µ"),
    (0.631, "£†π"), (0.636, "Þ"), (0.662, "¬±×÷−"), (0.664, "α"), (0.667, "€"),
    (0.67, "®"), (0.677, "✘"), (0.679, "Ý"), (0.69, "ÀÁÂÃÄÅ"), (0.713, "∑∞"), (0.723, "◆"),
    (0.725, "¤"), (0.73, "Ç"), (0.735, "Ð"), (0.744, "ÙÚÛÜ"), (0.753, "Ñ"), (0.764, "✓"),
    (0.765, "ÒÓÔÕÖØ"), (0.77, "Ω"), (0.846, "✔"), (0.864, "…"), (0.884, "¼"), (0.915, "©"),
    (0.925, "æ"), (0.926, "½"), (0.949, "¾"), (0.955, "↑↓"), (0.99, "▲▼►◄"), (0.994, "Æ"),
    (1.0, "—‰→←↔★☆"), (0.0, "\u00ad\u200b\u200c\u200d\ufeff"),
) for ch in chars}
_EM_MONO = 0.6    # every character of a monospace font (Fira Code, Courier, DejaVu Mono)
# A character the tables don't hold: the low bound takes it narrow, the high one wide,
# East Asian wide and fullwidth characters (drawn by a fallback font) widest of all.
_EM_UNKNOWN = {"low": (0.3, 0.9), "high": (0.8, 1.0)}
LOW_SCALE = 0.97  # the low bound's character widths, against the table's
HIGH_SCALE = 1.01  # the high bound's
VIEWPORT = 1440   # the window the rule assumes, in px

PAD = 16.0          # the holder's padding, each side
SCROLLBAR = 17.0    # a classic scrollbar's width on Windows
GUTTER = 16.0       # GRID_GUTTER_SIZE
PAGE_MARGIN = 64.0  # the window's width less the grid's
MIN_BOX = 40.0      # GRID_MIN_ROW_UNITS grid rows of GRID_BASE_UNIT (8) px
SNAP = 0.4          # px a block may run over and not scroll (scrollHeight is whole px)
UNIT = 40.0         # px per spec height unit
ROOT_FONT = 16.0    # the html element's font size, what rem counts in


def _weight_factor(weight: float) -> float:
    """How much wider Inter is at a weight than at 400, per sentence: 500 is 0.8%
    wider, 600 (and 700, which Superset doesn't load, so the browser draws 600) 1.8%."""
    return 1.02 if weight >= 600 else 1.01 if weight >= 500 else 1.0


# -- Superset's own markdown styles ---------------------------------------------------


@dataclass(frozen=True)
class Release:
    """One release's markdown styles, as CSS on the elements, with the specificity
    Superset's own rules lose by: any `.dashboard-markdown <element>` rule beats them,
    except the PROTECTED ones, given with the specificity they have in Superset."""

    name: str
    newest: tuple[int, int, int]  # the newest release it stands for
    bar: float                    # px the open vertical filter bar takes from the grid
    flag_for_horizontal_bar: bool  # draws the horizontal bar only with a feature flag
    css: str
    protected: tuple[tuple[str, str, tuple[int, int, int]], ...]
    root: tuple[float, float]     # the font size and line height (a factor) inherited

    def grid(self, viewport: float, filter_bar: bool) -> float:
        return viewport - PAGE_MARGIN - (self.bar if filter_bar else 0.0)


# Measured with every property set from `.dashboard-markdown <element>` (the
# "probe" stylesheet in the fixture): each took, h6's font size apart. Line heights
# are factors (headings 1.4 in `body h1`..`h6`, dashboard/styles.ts:21-50; the body's
# 1.5715 from antd), so they follow a font size the CSS sets.
_HEADINGS = """
.dashboard-markdown h1, .dashboard-markdown h2, .dashboard-markdown h3,
.dashboard-markdown h4, .dashboard-markdown h5, .dashboard-markdown h6 { line-height: 1.4; }
.dashboard-markdown h1 { font-size: 28px; margin: 12px 0 12px; }
.dashboard-markdown h3, .dashboard-markdown h4, .dashboard-markdown h5 { font-size: 16px; }
.dashboard-markdown h3, .dashboard-markdown h4, .dashboard-markdown h5,
.dashboard-markdown h6 { margin: 8px 0 4px; }
.dashboard-markdown p { margin: 0 0 8px; }
.dashboard-markdown ul, .dashboard-markdown ol { padding-left: 40px; }
.dashboard-markdown li ul, .dashboard-markdown li ol { margin: 0; }
.dashboard-markdown code, .dashboard-markdown pre { font-family: monospace; }
"""
_PROTECTED = (
    # Markdown.jsx:92-104 at 4.1.4 and 5.0.0, Markdown/Markdown.tsx:112-124 at 6.1.0:
    # inside the component's styled class, so (0,2,1).
    (".dashboard-markdown h6", "font-size: 12px", (0, 2, 1)),
    (".dashboard-markdown h4, .dashboard-markdown h5, .dashboard-markdown h6",
     "font-weight: 400", (0, 2, 1)),
)
RELEASES = (
    Release(
        "4.1.4 and 5.0.0", (5, 0, 0), bar=260.0, flag_for_horizontal_bar=True,
        css=_HEADINGS + """
.dashboard-markdown h1, .dashboard-markdown h2, .dashboard-markdown h3 { font-weight: 600; }
.dashboard-markdown h2 { font-size: 21px; margin: 12px 0 8px; }
.dashboard-markdown ul, .dashboard-markdown ol { margin: 0 0 9.5px; }
.dashboard-markdown strong, .dashboard-markdown b, .dashboard-markdown th { font-weight: 600; }
.dashboard-markdown table { border-collapse: collapse; }
.dashboard-markdown blockquote { font-size: 17.5px; margin: 0 0 19px; padding: 9.5px 19px;
  border-left: 5px solid; }
.dashboard-markdown pre { font-size: 13px; line-height: 1.4; margin: 0 0 9.5px; padding: 9px;
  border: 1px solid; }
.dashboard-markdown code { font-size: 90%; padding: 2px 4px; }
.dashboard-markdown pre code { font-size: inherit; padding: 0; }
.dashboard-markdown hr { margin: 15px 0; border-top: 1px solid; }
""",
        # Bootstrap's `blockquote p:last-child` (0,1,2): no gap under a quote's text.
        protected=_PROTECTED + (
            (".dashboard-markdown blockquote p:last-child, "
             ".dashboard-markdown blockquote ul:last-child, "
             ".dashboard-markdown blockquote ol:last-child", "margin-bottom: 0", (0, 1, 2)),),
        root=(14.0, 1.5715),
    ),
    Release(
        "6.1.0", (6, 1, 0), bar=228.0, flag_for_horizontal_bar=False,
        css=_HEADINGS + """
.dashboard-markdown h1, .dashboard-markdown h2, .dashboard-markdown h3 { font-weight: 500; }
.dashboard-markdown h2 { font-size: 20px; margin: 12px 0 8px; }
.dashboard-markdown ul, .dashboard-markdown ol { margin: 14px 0; }
.dashboard-markdown strong, .dashboard-markdown b { font-weight: 600; }
.dashboard-markdown th { font-weight: 500; }
.dashboard-markdown table { border-spacing: 2px; }
.dashboard-markdown td, .dashboard-markdown th { padding: 1px; }
.dashboard-markdown blockquote { margin: 14px 40px; }
.dashboard-markdown pre { margin: 14px 0; }
.dashboard-markdown hr { margin: 7px 0; border: 1px solid; }
""",
        protected=_PROTECTED, root=(14.0, 22 / 14),
    ),
)


# -- reading CSS --------------------------------------------------------------------

_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_SPLIT_TOP = re.compile(r",(?![^(\[]*[)\]])")
_COMPOUND = re.compile(r"^(\*|[a-zA-Z][\w-]*)?((?:\.[\w-]+)*)((?::(?:first-child|last-child|only-child))*)$")
_COMBINATOR = re.compile(r"\s*([>+~])\s*|\s+")
# Classes on the markdown block itself and the layout around it: a selector naming
# any other class targets something else, and never reaches the markdown's text.
_CHAIN = ("dashboard-markdown", "resizable-container", "dashboard-component",
          "dashboard-component-chart-holder")
_AROUND = frozenset(_CHAIN) | {
    "dashboard", "dashboard-content", "grid-container", "grid-content", "grid-row",
    "dashboard-grid", "dashboard-component-tabs", "dashboard-component-tabs-content",
    "dashboard-component-row", "dashboard-component-column", "dragdroppable",
    "dragdroppable-row", "dragdroppable-column", "background--white",
    "background--transparent", "ant-tabs", "ant-tabs-content", "ant-tabs-content-holder",
    "ant-tabs-content-top", "ant-tabs-tabpane", "ant-tabs-tabpane-active",
    "dashboard-markdown--editing"}
# What the estimate reads, and what else would change a markdown block's size.
_READ = re.compile(r"^(?:font-size|line-height|font-weight|letter-spacing|word-spacing|"
                   r"margin(?:-(?:top|right|bottom|left))?|padding(?:-(?:top|right|bottom|left))?|"
                   r"border(?:-(?:top|right|bottom|left))?(?:-(?:width|style))?|border-spacing|"
                   r"border-collapse|font-family)$")
_SIZING = re.compile(r"^(?:font|font-(?:size|family|weight|stretch|variant-numeric)|line-height|"
                     r"letter-spacing|word-spacing|margin.*|padding.*|border|border-spacing|"
                     r"border-collapse|border-(?:top|right|bottom|left)(?:-(?:width|style))?|"
                     r"border-(?:width|style)|text-transform|text-indent|white-space|word-break|"
                     r"overflow-wrap|word-wrap|hyphens|display|width|min-width|max-width|height|"
                     r"min-height|max-height|float|columns|column-[\w-]+|content|zoom|"
                     r"writing-mode|box-sizing)$")
_SIDES = ("top", "right", "bottom", "left")


@dataclass(frozen=True)
class _Selector:
    compounds: tuple[tuple[str | None, frozenset, tuple[str, ...]], ...]
    combinators: tuple[str, ...]           # between compound k and k+1
    specificity: tuple[int, int, int]


@dataclass(frozen=True)
class _Rule:
    selector: _Selector
    decls: tuple[tuple[str, str, bool], ...]  # longhand property, value, !important
    specificity: tuple[int, int, int]
    origin: int                              # 0 Superset's, 1 the dashboard's
    order: int


@dataclass(frozen=True)
class Stylesheet:
    """The parts of a dashboard's CSS the estimate reads, and the rest that could
    size markdown text: (selector, property) for each."""

    rules: tuple[_Rule, ...] = ()
    unread: tuple[tuple[str, str], ...] = ()


def _parse_selector(text: str) -> _Selector | None:
    """A selector the estimate can match against markdown elements, or None."""
    text = text.strip()
    if not text or re.search(r"[\[\]#()\"']|::", text):
        return None
    parts = _COMBINATOR.split(text)
    # re.split with a group alternates compound, combinator-or-None, compound...
    compounds, combinators = [], []
    for k, part in enumerate(parts):
        if k % 2:
            combinators.append((part or " ").strip() or " ")
            continue
        m = _COMPOUND.match(part or "")
        if not part or not m:
            return None
        tag = m.group(1).lower() if m.group(1) and m.group(1) != "*" else None
        classes = frozenset(c for c in m.group(2).split(".") if c)
        pseudo = tuple(p for p in m.group(3).split(":") if p)
        compounds.append((tag, classes, pseudo))
    spec = (0, sum(len(c[1]) + len(c[2]) for c in compounds), sum(1 for c in compounds if c[0]))
    return _Selector(tuple(compounds), tuple(combinators), spec)


def _declarations(body: str) -> list[tuple[str, str, bool]]:
    out = []
    for decl in re.split(r";(?![^(]*\))", body):
        if ":" not in decl:
            continue
        prop, value = decl.split(":", 1)
        value = value.strip()
        important = bool(re.search(r"!\s*important\s*$", value, re.I))
        value = re.sub(r"!\s*important\s*$", "", value, flags=re.I).strip()
        out.append((prop.strip().lower(), value, important))
    return out


def _longhands(prop: str, value: str) -> list[tuple[str, str]] | None:
    """A property as its longhands, or None when the value can't be read."""
    if "var(" in value or "calc(" in value or "env(" in value:
        return None
    parts = value.split()
    if prop in ("margin", "padding", "border-width", "border-style"):
        if not 1 <= len(parts) <= 4:
            return None
        top = parts[0]
        right = parts[1] if len(parts) > 1 else top
        bottom = parts[2] if len(parts) > 2 else top
        left = parts[3] if len(parts) > 3 else right
        name = prop if prop in ("margin", "padding") else "border"
        suffix = "" if prop in ("margin", "padding") else "-" + prop.split("-")[1]
        return [(f"{name}-{s}{suffix}", v) for s, v in zip(_SIDES, (top, right, bottom, left))]
    if prop.startswith("border") and prop not in ("border-spacing", "border-collapse"):
        bits = prop.split("-")[1:]
        sides = [bits[0]] if bits and bits[0] in _SIDES else list(_SIDES)
        if bits and bits[-1] in ("width", "style"):
            return [(prop, parts[0])] if len(parts) == 1 else None
        width, style = "medium", "none"
        for p in parts:
            if p in _STYLES:
                style = p
            elif p in ("thin", "medium", "thick") or re.fullmatch(r"-?[\d.]+(?:px|em|rem)?", p):
                width = p
        return [(f"border-{s}-{k}", v) for s in sides for k, v in (("width", width),
                                                                   ("style", style))]
    return [(prop, value)]


def _rules_of(css: str):
    """(selector list, declarations, the @media or other at-rule it sits in) for each
    style rule."""
    css = _COMMENT.sub("", css)
    i, n = 0, len(css)
    while i < n:
        j = css.find("{", i)
        if j < 0:
            return
        prelude = css[i:j].rsplit(";", 1)[-1].strip()
        depth, k = 1, j + 1
        while k < n and depth:
            depth += {"{": 1, "}": -1}.get(css[k], 0)
            k += 1
        body = css[j + 1:k - 1]
        if prelude.startswith("@"):
            if re.match(r"@(?:media|supports|layer|container|document)\b", prelude, re.I):
                for sel, decls, _ in _rules_of(body):
                    yield sel, decls, " ".join(prelude.split())
        else:
            yield prelude, body, None
        i = k


_STYLES = ("none", "hidden", "solid", "dashed", "dotted", "double", "groove", "ridge",
           "inset", "outset")
_LENGTH = r"(?:0|-?(?:\d+\.?\d*|\.\d+)(?:px|em|rem)|thin|medium|thick|auto)"
_VALUES = {
    "font-size": rf"inherit|{_LENGTH}|\d+\.?\d*%",
    "line-height": rf"inherit|normal|\d+\.?\d*|\.\d+|{_LENGTH}|\d+\.?\d*%",
    "font-weight": r"inherit|normal|bold|bolder|lighter|\d{3}",
    "letter-spacing": rf"inherit|normal|{_LENGTH}",
    "word-spacing": rf"inherit|normal|{_LENGTH}",
    "border-spacing": rf"{_LENGTH}(?: {_LENGTH})?",
    "border-collapse": r"inherit|collapse|separate",
}


def _readable(prop: str, value: str) -> bool:
    """Whether the estimate can resolve this value: lengths in px, em or rem (a
    percentage only for a font size or line height), and the keywords it knows."""
    v = value.strip().lower()
    if prop.endswith("-style"):
        return v in _STYLES
    if prop.startswith(("margin", "padding", "border")) and prop not in _VALUES:
        return bool(re.fullmatch(_LENGTH, v))
    return prop == "font-family" or bool(re.fullmatch(_VALUES[prop], v))


def _family(value: str, origin: int) -> list[tuple[str, str]] | None:
    """A font family the estimate knows: Superset's own, `inherit`, Inter (its body
    font) or a monospace font, whose characters are all 0.6 em."""
    first = value.split(",")[0].strip().strip("'\"").lower()
    if not origin or first in ("inherit", "inter") or re.search(r"mono|courier|code|consol", first):
        return [("font-family", value)]
    return None


def _reaches_markdown(selector: str) -> bool:
    """Whether a selector the estimate can't match might still style markdown text:
    it names the block, or names only elements and layout around it."""
    if "dashboard-markdown" in selector or "MARKDOWN-" in selector:
        return True
    # Superset's test hooks (data-test, data-test-chart-name) mark its other components.
    if "[data-test" in selector:
        return False
    classes = set(re.findall(r"\.([\w-]+)", re.sub(r"\[[^\]]*\]", "", selector)))
    return not (classes - _AROUND) and "#" not in selector


@lru_cache(maxsize=256)
def read_css(css: str | None, origin: int = 1, start: int = 0) -> Stylesheet:
    """The rules of a stylesheet the estimate applies, in source order, and the
    selectors and properties it leaves unread that could size markdown text."""
    rules, unread = [], []
    order = start
    for selectors, body, nested in _rules_of(css or ""):
        decls = _declarations(body)
        sizing = [(p, v, imp) for p, v, imp in decls if _SIZING.match(p)]
        if not sizing:
            continue
        for text in _SPLIT_TOP.split(selectors):
            text = " ".join(text.split())
            sel = None if nested else _parse_selector(text)
            if sel is None or (origin and sel.specificity[1] == 0):
                # A selector with no class can't be ordered against Superset's own, and
                # whether an at-rule applies depends on the window.
                if _reaches_markdown(text):
                    shown = f"{nested} {{ {text} }}" if nested else text
                    unread += [(shown, p) for p, _, _ in sizing]
                continue
            # The block's own boxes: their padding is Superset's, set more specifically
            # than a stylesheet can be sure to beat, so a change to it isn't read; their
            # margins sit outside the text.
            box = bool(sel.compounds[-1][1] & set(_CHAIN))
            longs = []
            for prop, value, imp in sizing:
                parts = None
                if box and prop.startswith("margin"):
                    continue
                if prop == "font-family":
                    parts = _family(value, origin)
                elif _READ.match(prop) and not (box and prop.startswith("padding")):
                    parts = _longhands(prop, value)
                if parts is not None and not all(_readable(p, v) for p, v in parts):
                    parts = None
                if parts is None:
                    if _reaches_markdown(text):
                        unread.append((text, prop))
                    continue
                longs += [(p, v, imp) for p, v in parts]
            if longs:
                order += 1
                rules.append(_Rule(sel, tuple(longs), sel.specificity, origin, order))
    return Stylesheet(tuple(rules), tuple(dict.fromkeys(unread)))


@lru_cache(maxsize=8)
def _base(release: Release) -> tuple[_Rule, ...]:
    """Superset's own rules, all below any dashboard rule with a class in it (their
    real selectors are elements, `body h1`), in their own order among themselves,
    and the protected ones at the specificity they have in Superset."""
    rules = [_Rule(r.selector, r.decls, (0, 0, r.specificity[1] * 100 + r.specificity[2]), 0,
                   r.order) for r in read_css(release.css, origin=0).rules]
    for k, (selector, decls, spec) in enumerate(release.protected):
        for r in read_css(f"{selector} {{ {decls} }}", origin=0, start=10_000 + k * 10).rules:
            rules.append(_Rule(r.selector, r.decls, spec, 0, r.order))
    return tuple(rules)


# -- parsing: markdown to blocks ----------------------------------------------------


@dataclass
class Block:
    kind: str                     # p | h | list | table | quote | code | hr
    level: int = 0                # a heading's level
    texts: list[str] = field(default_factory=list)   # p: its lines between hard breaks
    items: list[tuple[int, str]] = field(default_factory=list)  # list: (depth, text)
    ordered: list[bool] = field(default_factory=list)  # list: each item's list is numbered
    loose: bool = False           # list items separated by blank lines
    rows: list[list[str]] = field(default_factory=list)  # table rows, header first
    lines: int = 0                # code: its lines


_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
_SETEXT = re.compile(r"^ {0,3}(?:=+|-+)[ \t]*$")
_HR = re.compile(r"^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")
_ITEM = re.compile(r"^([ \t]*)([-*+]|\d{1,9}[.)])(?:[ \t]+(.*)|[ \t]*$)")
_QUOTE = re.compile(r"^ {0,3}> ?(.*)$")
_DELIM = re.compile(r"^[ \t]*\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*$")
_HTML_H = re.compile(r"^\s*<h([1-6])\b[^>]*>(.*?)</h\1>\s*$", re.I)
_HARD = re.compile(r"(?: {2,}|\\)$")
_BR = re.compile(r"<br\s*/?>", re.I)
_MEDIA = re.compile(r"!\[[^\]]*\]\([^)]*\)|<(?:img|iframe|video|svg|object|embed)\b[^>]*>", re.I)


def _starts_block(line: str) -> bool:
    return bool(_FENCE.match(line) or _ATX.match(line) or _HR.match(line)
                or _ITEM.match(line) or _QUOTE.match(line))


def _cells(line: str) -> list[str]:
    s = line.strip()
    s = s[1:] if s.startswith("|") else s
    s = s[:-1] if s.endswith("|") and not s.endswith("\\|") else s
    return [c.strip() for c in re.split(r"(?<!\\)\|", s)]


def parse(markdown: str) -> tuple[list[Block], bool]:
    """The blocks a markdown text draws, and whether it holds an image or embedded
    media, whose height the text can't tell. CommonMark's common shapes: ATX and
    setext headings, paragraphs with soft and hard breaks, lists (nested, tight or
    loose), GFM tables, block quotes, fenced code and rules. HTML reads as its text,
    with <br> a line break."""
    lines = markdown.replace("\r\n", "\n").replace("\t", "    ").split("\n")
    blocks: list[Block] = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
        elif m := _FENCE.match(line):
            j = i + 1
            while j < n and not lines[j].strip().startswith(m.group(1)):
                j += 1
            blocks.append(Block("code", lines=max(1, j - i - 1)))
            i = j + 1
        elif m := _ATX.match(line) or _HTML_H.match(line):
            level = len(m.group(1)) if m.re is _ATX else int(m.group(1))
            blocks.append(Block("h", level=level, texts=[m.group(2) or ""]))
            i += 1
        elif _HR.match(line):
            blocks.append(Block("hr"))
            i += 1
        elif _QUOTE.match(line):
            body = []
            while i < n and (m := _QUOTE.match(lines[i])):
                body.append(m.group(1))
                i += 1
            inner, _ = parse("\n".join(body))
            texts = [t for b in inner for t in (b.texts or [it for _, it in b.items])]
            blocks.append(Block("quote", texts=texts or [""]))
        elif "|" in line and i + 1 < n and "-" in lines[i + 1] and _DELIM.match(lines[i + 1]):
            rows = [_cells(line)]
            i += 2
            while i < n and lines[i].strip() and "|" in lines[i]:
                rows.append(_cells(lines[i]))
                i += 1
            blocks.append(Block("table", rows=rows))
        elif _ITEM.match(line):
            i = _parse_list(lines, i, blocks)
        else:
            # A paragraph runs to a blank line or another block; an underline of = or -
            # makes it a heading (setext).
            para = [line]
            i += 1
            while (i < n and lines[i].strip() and not _starts_block(lines[i])
                   and not _SETEXT.match(lines[i])):
                para.append(lines[i])
                i += 1
            if i < n and _SETEXT.match(lines[i]):
                level = 1 if lines[i].strip()[0] == "=" else 2
                blocks.append(Block("h", level=level, texts=[" ".join(l.strip() for l in para)]))
                i += 1
            else:
                blocks.append(Block("p", texts=_paragraph_lines(para)))
    return blocks, bool(_MEDIA.search(markdown))


def _paragraph_lines(raw: list[str]) -> list[str]:
    """A paragraph's lines between hard breaks (two trailing spaces, a backslash or
    <br>); a soft break joins its lines with a space."""
    out, cur = [], []
    for k, line in enumerate(raw):
        cur.append(line.strip().rstrip("\\").strip())
        if _HARD.search(line) and k < len(raw) - 1:
            out.append(" ".join(cur))
            cur = []
    out.append(" ".join(cur))
    return [seg for text in out for seg in _BR.split(text)]


def _marker(marker: str) -> str:
    """What makes items one list: the same bullet, or numbers with the same delimiter."""
    return marker[-1] if marker[0].isdigit() else marker


def _parse_list(lines: list[str], i: int, blocks: list[Block]) -> int:
    """One list from line i, nested items and all; returns the line after it. Another
    bullet or delimiter at the list's own level starts another list."""
    block = Block("list")
    indents: list[int] = []      # each open level's marker indent
    kind = None                  # the top level's marker
    gap = False                  # a blank line since the last line of an item
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            gap = True
        elif m := _ITEM.match(line):
            indent = len(m.group(1))
            if kind is not None and indent <= indents[0] and _marker(m.group(2)) != kind:
                break
            kind = kind or _marker(m.group(2))
            while indents and indent < indents[-1]:
                indents.pop()
            if not indents or indent > indents[-1] + 1:
                indents.append(indent)
            if gap and block.items and len(indents) == 1:
                block.loose = True
            block.items.append((len(indents) - 1, m.group(3) or ""))
            block.ordered.append(m.group(2)[0].isdigit())
            gap = False
        elif (gap and not line.startswith(" ")) or (not gap and _starts_block(line)):
            break                # the list has ended
        else:
            depth, text = block.items[-1]
            block.items[-1] = (depth, f"{text} {line.strip()}".strip())
            block.loose = block.loose or gap   # a second paragraph in an item
            gap = False
        i += 1
    blocks.append(block)
    return i


# -- the element tree ---------------------------------------------------------------


@dataclass(eq=False)
class Node:
    """An element as react-markdown draws it, with what selectors match on."""

    tag: str
    classes: frozenset = frozenset()
    children: list[Node] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)   # its lines of inline text
    code_lines: int = 0                               # pre: its lines
    parent: Node | None = None
    prev: Node | None = None       # the element before it in its parent
    first: bool | None = None
    last: bool | None = None

    def add(self, *kids: Node) -> Node:
        self.children.extend(kids)
        return self


def _li(text: str, loose: bool) -> Node:
    return Node("li").add(Node("p", texts=_BR.split(text)) if loose
                          else Node("#text", texts=_BR.split(text)))


def tree(markdown: str) -> Node:
    """The markdown block's elements under its holder, the holder under the
    resizable container under `.dashboard-markdown`, as Superset draws them."""
    holder = Node("div", frozenset({"dashboard-component", "dashboard-component-chart-holder"}))
    for b in parse(markdown)[0]:
        if b.kind == "p":
            holder.add(Node("p", texts=b.texts))
        elif b.kind == "h":
            holder.add(Node(f"h{b.level}", texts=_BR.split(b.texts[0])))
        elif b.kind == "hr":
            holder.add(Node("hr"))
        elif b.kind == "code":
            holder.add(Node("pre", code_lines=b.lines).add(Node("code")))
        elif b.kind == "quote":
            holder.add(Node("blockquote").add(*(Node("p", texts=_BR.split(t)) for t in b.texts)))
        elif b.kind == "table":
            head = Node("tr").add(*(Node("th", texts=[c]) for c in b.rows[0]))
            body = [Node("tr").add(*(Node("td", texts=[c]) for c in r)) for r in b.rows[1:]]
            table = Node("table").add(Node("thead").add(head))
            if body:
                table.add(Node("tbody").add(*body))
            holder.add(table)
        else:
            stack: list[Node] = []
            for (depth, text), ordered in zip(b.items, b.ordered or [False] * len(b.items)):
                while len(stack) > depth + 1:
                    stack.pop()
                if len(stack) <= depth:
                    lst = Node("ol" if ordered else "ul")
                    (stack[-1].children[-1] if stack else holder).add(lst)
                    stack.append(lst)
                stack[-1].add(_li(text, b.loose and depth == 0))
    resizable = Node("div", frozenset({"resizable-container"})).add(holder)
    root = Node("div", frozenset({"dashboard-markdown"})).add(resizable)
    _link(root)
    return holder


def _link(node: Node) -> None:
    elements = [c for c in node.children if c.tag != "#text"]
    for k, child in enumerate(node.children):
        child.parent = node
        if child.tag != "#text":
            at = elements.index(child)
            child.prev = elements[at - 1] if at else None
            child.first, child.last = at == 0, at == len(elements) - 1
        _link(child)


# -- the cascade --------------------------------------------------------------------


def _match_compound(compound, node: Node) -> bool:
    tag, classes, pseudo = compound
    if (tag and tag != node.tag) or not classes <= node.classes:
        return False
    known = {"first-child": node.first, "last-child": node.last,
             "only-child": node.first and node.last if None not in (node.first, node.last)
             else None}
    return all(known[p] is True for p in pseudo)


def _around(compound) -> bool:
    """A compound that may match an element above `.dashboard-markdown`, which the
    estimate doesn't see: a plain element, or layout classes."""
    tag, classes, pseudo = compound
    return not pseudo and tag in (None, "div", "body", "html", "main", "section") and \
        classes <= (_AROUND - set(_CHAIN))


def _matches(sel: _Selector, k: int, node: Node | None) -> bool:
    if node is None:
        return all(_around(c) for c in sel.compounds[:k + 1])
    if not _match_compound(sel.compounds[k], node):
        return False
    if k == 0:
        return True
    comb = sel.combinators[k - 1]
    if comb == ">":
        return _matches(sel, k - 1, node.parent)
    if comb == " ":
        up = node.parent
        while up is not None:
            if _matches(sel, k - 1, up):
                return True
            up = up.parent
        return _matches(sel, k - 1, None)
    sib = node.prev
    while sib is not None:
        if _matches(sel, k - 1, sib):
            return True
        sib = sib.prev if comb == "~" else None
    return False


@dataclass(frozen=True)
class Style:
    font: float
    line: float                   # px
    line_factor: float | None     # how the line height inherits: a factor, or None (px)
    weight: float
    letter: float = 0.0
    word: float = 0.0
    mono: bool = False
    spacing: float = 0.0          # a table's vertical border-spacing (inherited)
    collapse: bool = False        # border-collapse (inherited)
    margin: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    padding: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    border: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)

    @property
    def inset(self) -> float:
        """Horizontal room it takes from its parent's width."""
        return sum(s[1] + s[3] for s in (self.margin, self.padding, self.border))

    @property
    def edge_top(self) -> float:
        return self.padding[0] + self.border[0]

    @property
    def edge_bottom(self) -> float:
        return self.padding[2] + self.border[2]


_LEN = re.compile(r"^(-?(?:\d+\.?\d*|\.\d+))(px|em|rem|%)?$")


def _length(value: str, font: float, parent_font: float | None = None) -> float | None:
    """A length in px; em counts in `font`, or in `parent_font` for a font size."""
    v = value.strip().lower()
    if v in ("auto", "0"):
        return 0.0
    if v in ("thin", "medium", "thick"):
        return {"thin": 1.0, "medium": 3.0, "thick": 5.0}[v]
    m = _LEN.match(v)
    if not m:
        return None
    n, unit = float(m.group(1)), m.group(2)
    if unit == "px":
        return n
    if unit == "em":
        return n * (parent_font if parent_font is not None else font)
    if unit == "rem":
        return n * ROOT_FONT
    if unit == "%" and parent_font is not None:
        return n * parent_font / 100
    return None if n else 0.0


def _compute(node: Node, parent: Style, rules: tuple[_Rule, ...]) -> Style:
    won: dict[str, tuple] = {}
    for r in rules:
        if not _matches(r.selector, len(r.selector.compounds) - 1, node):
            continue
        for prop, value, imp in r.decls:
            key = (imp, r.specificity, r.origin, r.order)
            if prop not in won or key >= won[prop][0]:
                won[prop] = (key, value)
    get = {p: v for p, (_, v) in won.items()}
    font = parent.font
    if "font-size" in get:
        v = get["font-size"].lower()
        font = parent.font if v == "inherit" else (_length(v, font, parent.font) or font)
    factor, line = parent.line_factor, parent.line if parent.line_factor is None else None
    if "line-height" in get:
        v = get["line-height"].lower()
        if v == "normal":
            factor, line = 1.21, None        # Inter's ascent and descent
        elif re.fullmatch(r"\d+\.?\d*|\.\d+", v):
            factor, line = float(v), None
        elif v.endswith("%") and _LEN.match(v):
            factor, line = None, float(v[:-1]) * font / 100
        elif v != "inherit" and (px := _length(v, font)) is not None:
            factor, line = None, px
    line = factor * font if factor is not None else line
    weight = parent.weight
    if "font-weight" in get:
        v = get["font-weight"].lower()
        weight = {"normal": 400, "bold": 700, "lighter": 400,
                  "bolder": 700 if parent.weight < 600 else 900}.get(
            v, float(v) if v.replace(".", "").isdigit() else weight)

    def spacing(prop: str, current: float) -> float:
        v = get.get(prop)
        if v is None or v.lower() == "inherit":
            return current
        return 0.0 if v.lower() == "normal" else (_length(v, font) or 0.0)

    def sides(prop: str) -> tuple[float, ...]:
        out = []
        for s in _SIDES:
            v = _length(get.get(f"{prop}-{s}", "0"), font) or 0.0
            out.append(v if prop == "margin" else max(0.0, v))
        return tuple(out)

    border = tuple(0.0 if get.get(f"border-{s}-style", "none").lower() in ("none", "hidden")
                   else (_length(get.get(f"border-{s}-width", "medium"), font) or 0.0)
                   for s in _SIDES)
    fam = get.get("font-family", "").split(",")[0].strip().strip("'\"").lower()
    mono = parent.mono if not fam or fam == "inherit" else bool(
        re.search(r"mono|courier|code|consol", fam))
    gap = parent.spacing
    if "border-spacing" in get:
        parts = get["border-spacing"].split()
        gap = _length(parts[-1], font) or 0.0
    collapse = get.get("border-collapse", "collapse" if parent.collapse else "separate")
    return Style(font, line, factor, weight, spacing("letter-spacing", parent.letter),
                 spacing("word-spacing", parent.word), mono, gap,
                 collapse.lower() == "collapse", sides("margin"), sides("padding"), border)


class _Styles:
    """Every element's computed style for one release and one dashboard stylesheet."""

    def __init__(self, release: Release, sheet: Stylesheet):
        self.rules = _base(release) + sheet.rules
        font, factor = release.root
        self.root = Style(font, font * factor, factor, 400.0)
        self.cache: dict[tuple, Style] = {}

    def of(self, node: Node) -> Style:
        key = (node, ())     # the node itself, not its id: a freed node's id comes back
        if key not in self.cache:
            parent = self.of(node.parent) if node.parent is not None else self.root
            self.cache[key] = parent if node.tag == "#text" else _compute(node, parent, self.rules)
        return self.cache[key]

    def inline(self, node: Node, tags: tuple[str, ...]) -> Style:
        """The style of text inside these inline elements, innermost last."""
        key = (node, tags)
        if key not in self.cache:
            if not tags:
                self.cache[key] = self.of(node)
            else:
                at = node
                for tag in tags:
                    at = Node(tag, parent=at)
                parent = self.inline(node, tags[:-1])
                self.cache[key] = _compute(at, parent, self.rules)
        return self.cache[key]


# -- text width ---------------------------------------------------------------------

_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)|!?\[([^\]]*)\]\[[^\]]*\]")
_AUTOLINK = re.compile(r"<((?:https?|mailto):[^>\s]+)>")
_TAG = re.compile(r"<(/?)([A-Za-z][\w-]*)[^>]*?(/?)>")
_INLINE_TAGS = {"a", "abbr", "b", "cite", "code", "del", "em", "i", "ins", "kbd", "mark",
                "q", "s", "samp", "small", "span", "strong", "sub", "sup", "u", "var"}
_ESCAPED = re.compile(r"\\([!-/:-@\[-`{-~])")


def _runs(text: str) -> list[tuple[str, tuple[str, ...]]]:
    """Inline markdown and HTML as runs of the text a reader sees, each with the
    inline elements it sits in, outermost first."""
    keep: list[str] = []

    def hold(s: str) -> str:
        keep.append(s)
        return f"\ue000{len(keep) - 1}\ue001"

    text = _ESCAPED.sub(lambda m: hold(html.escape(m.group(1))), text)
    text = re.sub(r"(`+)(.+?)\1", lambda m: f"<code>{hold(html.escape(m.group(2).strip()))}</code>",
                  text)
    text = _MEDIA.sub("", text)
    text = _LINK.sub(lambda m: f"<a>{m.group(1) or m.group(2) or ''}</a>", text)
    text = _AUTOLINK.sub(r"<a>\1</a>", text)
    for _ in range(2):
        text = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*|(?<![\w\\])__(?=\S)(.+?)(?<=\S)__(?!\w)",
                      lambda m: f"<strong>{m.group(1) or m.group(2)}</strong>", text)
        text = re.sub(r"~~(?=\S)(.+?)(?<=\S)~~", r"<del>\1</del>", text)
        text = re.sub(r"\*(?=[^\s*])(.+?)(?<=[^\s*])\*|(?<![\w\\])_(?=\S)(.+?)(?<=\S)_(?!\w)",
                      lambda m: f"<em>{m.group(1) or m.group(2)}</em>", text)
    out: list[tuple[str, tuple[str, ...]]] = []
    stack: list[str] = []
    at = 0
    for m in _TAG.finditer(text + "<x>"):
        chunk = text[at:m.start()] if m.end() <= len(text) else text[at:]
        if chunk:
            chunk = re.sub(r"\ue000(\d+)\ue001", lambda h: keep[int(h.group(1))], chunk)
            out.append((html.unescape(chunk), tuple(stack)))
        if m.end() > len(text):
            break
        at = m.end()
        closing, tag, selfclose = m.group(1), m.group(2).lower(), m.group(3)
        if tag not in _INLINE_TAGS or selfclose:
            continue
        if not closing:
            stack.append(tag)
        elif tag in stack:
            del stack[len(stack) - 1 - stack[::-1].index(tag):]
    return out


def plain(text: str) -> str:
    """Inline markdown and HTML reduced to the text a reader sees."""
    return "".join(t for t, _ in _runs(text))


def _char_em(ch: str, bound: str = "high") -> float:
    o = ord(ch)
    if 32 <= o < 127:
        return _EM[o - 32]
    if ch in _EM_MORE:
        return _EM_MORE[ch]
    narrow, wide = _EM_UNKNOWN[bound]
    return wide if unicodedata.east_asian_width(ch) in ("W", "F") else narrow


def em_width(text: str) -> float:
    """A text's width on one line, in em of Inter at 400."""
    return sum(_char_em(ch) for ch in text)


# A break opportunity follows a space, an en or em dash, or a hyphen before a letter:
# Chromium breaks "great-circle" and "Jul–Sep" there, and long hyphenated URLs too.
_BREAK_AFTER = "–—"


def _wrap(chars: list[tuple], width: float) -> list[set]:
    """The lines (character, px, style) take in `width` px, wrapped greedily as the
    browser wraps them, each as the set of inline styles on it. A word wider than the
    line takes a line of its own (the holder clips it sideways); an empty text still
    takes its line."""
    words: list[tuple[float, list[tuple[float, set]]]] = []  # (space before, pieces)
    space, pieces, piece, on = 0.0, [], 0.0, set()
    for k, (ch, w, st) in enumerate(chars):
        if ch in " \t\n":
            if pieces or piece:
                words.append((space, pieces + [(piece, on)]))
                pieces, piece, space, on = [], 0.0, 0.0, set()
            space = space or w
            continue
        piece += w
        on.add(st)
        nxt = chars[k + 1][0] if k + 1 < len(chars) else ""
        if ch in _BREAK_AFTER or (ch == "-" and nxt.isalpha()):
            pieces.append((piece, on))
            piece, on = 0.0, set()
    if pieces or piece:
        words.append((space, pieces + [(piece, on)]))
    lines, x = [set()], 0.0
    for space, parts in words:
        for k, (w, styles) in enumerate(parts):
            add = w + (space if k == 0 and x > 0 else 0.0)
            if x > 0 and x + add > width:
                lines.append(set())
                x = w
            else:
                x += add
            lines[-1] |= styles
    return lines


# Inter's ascent and descent in em: where an inline box sits on the line.
_ASCENT, _DESCENT = 0.96875, 0.2421875


def _line_box(block: Style, styles: set, bound: str) -> float:
    """A line's height: its block's line height, or more where inline text of
    another size or line height on it reaches past it (CSS 2.1 10.8). Chromium draws
    such a line up to a px taller than the arithmetic says (a smaller <em> in a
    paragraph); the high bound allows a px for it."""
    def span(s: Style) -> tuple[float, float]:
        lead = (s.line - (_ASCENT + _DESCENT) * s.font) / 2
        return -(_ASCENT * s.font + lead), _DESCENT * s.font + lead

    others = [s for s in styles if (s.font, s.line) != (block.font, block.line)]
    if not others:
        return block.line
    spans = [span(block)] + [span(s) for s in others]
    height = max(b for _, b in spans) - min(t for t, _ in spans)
    return height + (1.0 if bound == "high" else 0.0)


def _measure(runs, style_of, scale: float, bound: str) -> list[tuple]:
    """Each character of the runs with its width in px and its style, an inline
    element's padding, border and margin on its first and last character."""
    out: list[tuple] = []
    prev: tuple[str, ...] = ()
    for k, (text, tags) in enumerate(runs):
        st = style_of(tags)
        px = st.font * _weight_factor(st.weight) * scale
        nxt = runs[k + 1][1] if k + 1 < len(runs) else ()
        start = len(out)
        for ch in text:
            em = _EM_MONO if st.mono and ch not in " \t\n" else _char_em(ch, bound)
            out.append((ch, em * px + st.letter + (st.word if ch == " " else 0.0), st))
        if len(out) > start:
            opened = [t for d, t in enumerate(tags) if d >= len(prev) or prev[d] != t]
            closed = [t for d, t in enumerate(tags) if d >= len(nxt) or nxt[d] != t]
            for depth in range(len(tags)):
                edge = style_of(tags[:depth + 1])
                left = edge.margin[3] + edge.padding[3] + edge.border[3]
                right = edge.margin[1] + edge.padding[1] + edge.border[1]
                if tags[depth] in opened and left:
                    ch, w, s = out[start]
                    out[start] = (ch, w + left, s)
                if tags[depth] in closed and right:
                    ch, w, s = out[-1]
                    out[-1] = (ch, w + right, s)
        prev = tags
    return out


def wrapped_lines(text: str, width: float, font_px: float = 14.0, scale: float = 1.0) -> int:
    """Lines a plain text takes in `width` px at `font_px` in Inter at 400."""
    return len(_wrap([(ch, _char_em(ch) * font_px * scale, None) for ch in text], width))


# -- layout: elements to px ---------------------------------------------------------


def _collapse(margins: list[float]) -> float:
    """Adjoining margins collapse: the largest positive one plus the most negative."""
    return max([m for m in margins if m > 0], default=0.0) + min([m for m in margins if m < 0],
                                                                  default=0.0)


class _Layout:
    def __init__(self, styles: _Styles, scale: float, bound: str):
        self.styles, self.scale, self.bound = styles, scale, bound

    def lines(self, node: Node, text: str, width: float) -> list[set]:
        runs = _runs(text) or [("", ())]
        chars = _measure(runs, lambda tags: self.styles.inline(node, tags), self.scale,
                         self.bound)
        return _wrap(chars, width)

    def text_height(self, node: Node, texts: list[str], width: float) -> float:
        st = self.styles.of(node)
        return sum(_line_box(st, line, self.bound)
                   for t in texts for line in self.lines(node, t, width))

    def table(self, node: Node, width: float) -> float:
        """Rows of one line while the columns fit side by side. Past that, each column
        gets a share of the width in proportion to its widest cell, roughly as the
        browser's automatic table layout does, and a row is as tall as its tallest cell."""
        st = self.styles.of(node)
        gap = 0.0 if st.collapse else st.spacing
        rows = [tr for group in node.children for tr in group.children]
        cells = [[(c, self.styles.of(c)) for c in tr.children] for tr in rows]
        cols = max(len(r) for r in cells)
        inset = max((cs.inset for r in cells for _, cs in r), default=0.0)
        room = width - gap * (cols + 1) - inset * cols

        def one_line(c: Node, cs: Style) -> float:
            runs = _runs(c.texts[0]) or [("", ())]
            return sum(w for _, w, _ in _measure(runs, lambda t: self.styles.inline(c, t),
                                                 self.scale, self.bound))

        widest = [max((one_line(*r[k]) if k < len(r) else 0.0) for r in cells)
                  for k in range(cols)]
        total = sum(widest) or 1.0
        height = gap
        for r in cells:
            tallest = 0.0
            for k, (c, cs) in enumerate(r):
                share = room if total <= room else room * widest[k] / total
                tallest = max(tallest, self.text_height(c, c.texts, share) + cs.edge_top
                              + cs.edge_bottom)
            height += tallest + gap
        return height

    def box(self, node: Node, width: float) -> tuple[list[float], float, list[float]]:
        """(the margins at its top edge, its height, the margins at its bottom edge):
        the margins that collapse with whatever sits above or below it."""
        st = self.styles.of(node)
        inner = width - (0.0 if node.tag == "#text" else st.inset)
        if node.tag == "table":
            height = self.table(node, inner)
        elif node.tag == "pre":
            height = node.code_lines * st.line
        elif node.texts or node.tag == "#text":
            height = self.text_height(node, node.texts or [""], inner)
        else:
            height, first, last = self.stack(node.children, inner, st.edge_top == 0,
                                             st.edge_bottom == 0)
            return ([st.margin[0], *first], height + st.edge_top + st.edge_bottom,
                    [st.margin[2], *last])
        if node.tag == "#text":
            return [], height, []
        return [st.margin[0]], height + st.edge_top + st.edge_bottom, [st.margin[2]]

    def stack(self, children: list[Node], width: float, open_top: bool,
              open_bottom: bool) -> tuple[float, list[float], list[float]]:
        """Children stacked with their margins collapsed: (the height to the last one's
        bottom edge, the margins passed up through an open top, through an open
        bottom). A closed edge (padding or a border) keeps them inside."""
        y, through_top, pending = 0.0, [], None
        for child in children:
            top, height, bottom = self.box(child, width)
            if pending is None:
                if open_top:
                    through_top = top
                else:
                    y += _collapse(top)
            else:
                y += _collapse(pending + top)
            y += height
            pending = bottom
        if pending is None:
            return 0.0, [], []
        if open_bottom:
            return y, through_top, pending
        return y + _collapse(pending), through_top, []


def layout(holder: Node, width: float, styles: _Styles, scale: float = 1.0,
           bound: str = "high") -> tuple[float, float]:
    """(how far down the last line reaches, the height that shows everything with no
    inner scrollbar), in px from the holder's top edge, for text `width` px wide.
    The holder is a scroll container: its padding keeps the first block's margin
    above and the last one's below inside it."""
    lay = _Layout(styles, scale, bound)
    text, _, below = lay.stack(holder.children, width, False, True)
    border = styles.of(holder).border
    top = PAD + border[0] + text
    return top, top + _collapse(below) + PAD + border[2]


# -- the estimate -------------------------------------------------------------------


def text_width(cols: int, grid: float) -> float:
    """The width a block `cols` twelfths wide wraps its text in, on a grid `grid` px."""
    column = (grid - 11 * GUTTER) / 12
    return cols * column + (cols - 1) * GUTTER - 2 * PAD


def box_px(height: float) -> float:
    """The px Superset gives a block of this spec height: never under 40."""
    return max(MIN_BOX, height * UNIT)


@dataclass(frozen=True)
class Fit:
    text_low: float     # px: the last line reaches at least this far down
    need_low: float     # px: the block needs at least this much
    need_high: float    # px: this much shows it whole on every release
    media: bool         # an image or embed adds height the text can't tell

    @property
    def units(self) -> float:
        """need_high in spec units, up to a whole 8 px grid row (a fifth of a unit).
        Chromium's scroll height is whole px, so a block under half a px over doesn't
        scroll: an 11.5 px footer line takes 48.1 px and fits 48."""
        return math.ceil(round((self.need_high - SNAP) / 8, 6)) / 5

    def short(self, box: float) -> bool:
        """Whether the block needs more than a box this tall: it scrolls."""
        return self.need_low > box + SNAP


def releases_from(floor: tuple[int, int, int] | None) -> tuple[Release, ...]:
    """The releases a spec can go to, given the oldest one it needs."""
    return tuple(r for r in RELEASES if floor is None or r.newest >= floor) or RELEASES[-1:]


def estimate(markdown: str, cols: int, filter_bar: bool | None = False,
             css: str | Stylesheet | None = None, viewport: float = VIEWPORT,
             releases: tuple[Release, ...] = RELEASES) -> Fit:
    """How a markdown text fits a block `cols` twelfths wide in a window `viewport`
    px wide, beside the open vertical filter bar or not, under the dashboard's CSS.
    None says the bar may be there or not: the low bound then wraps without it and
    the high bound beside it."""
    sheet = css if isinstance(css, Stylesheet) else read_css(css)
    holder = tree(markdown)
    media = parse(markdown)[1]
    lows, highs = [], []
    wide = max(r.grid(viewport, bool(filter_bar)) for r in releases)
    for r in releases:
        styles = _Styles(r, sheet)
        lows.append(layout(holder, text_width(cols, wide), styles, LOW_SCALE, "low"))
        # Windows narrows the grid by the page's own scrollbar too.
        grid = r.grid(viewport, filter_bar is not False) - SCROLLBAR
        highs.append(layout(holder, text_width(cols, grid) - SCROLLBAR, styles, HIGH_SCALE,
                            "high")[1])
    return Fit(min(a for a, _ in lows), min(b for _, b in lows), max(highs), media)
