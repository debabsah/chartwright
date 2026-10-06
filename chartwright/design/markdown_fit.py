"""How tall Superset draws a dashboard markdown block, estimated offline from its
text: `size.markdown-fit` and `layout.markdown-height` (rules.py) both read it.

What Superset does with a markdown block, at 4.1.4, 5.0.0 and 6.1.0 alike:
- renders the text with react-markdown and GitHub-flavoured markdown (tables,
  strikethrough), raw HTML sanitized (packages/superset-ui-core/src/components/
  SafeMarkdown.tsx at 4.1.4 and 5.0.0, components/SafeMarkdown/SafeMarkdown.tsx at
  6.1.0);
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
- lays the grid out in 12 columns with 16 px gutters (util/constants.ts:38-40). At a
  1600 px viewport the grid is 1536 px wide. Beside the vertical filter bar, which
  opens by default on a dashboard with native filters (DashboardBuilder/state.ts:39-41
  at 4.1.4; OPEN_FILTER_BAR_WIDTH = 260 in dashboard/constants.ts), it is 1276 px at
  4.1.4 and 5.0.0 and 1308 px at 6.1.0.

The type sizes, margins and character widths below were measured on 2026-10-05 in
headless Chromium at that 1600 px viewport, each release with its default theme
(tools/record_markdown_fit.py; docs/DESIGN-BRAIN.md, "Markdown blocks"). The text
wraps word by word as the browser wraps it, with Inter's own advance widths, and
block margins collapse as CSS collapses them. tests/test_markdown_fit.py holds what
each release drew for 17 texts at five widths, beside the filter bar and not, and
checks both bounds below against every one.

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
_EM_OTHER = 0.6   # a character outside ASCII (an accented letter, Greek): about an 'o'
_EM_WIDE = 1.0    # East Asian wide and fullwidth characters, drawn by a fallback font
LOW_SCALE = 0.97  # the low bound's character widths, against the table's
HIGH_SCALE = 1.01  # the high bound's character widths, against the table's
_HEADING_WEIGHT = 1.02  # h1-h3 are drawn at weight 600 (4.1.4, 5.0.0) or 500 (6.1.0)

PAD = 16.0          # the holder's padding, each side
SCROLLBAR = 17.0    # a classic scrollbar's width on Windows
GUTTER = 16.0       # GRID_GUTTER_SIZE
MIN_BOX = 40.0      # GRID_MIN_ROW_UNITS grid rows of GRID_BASE_UNIT (8) px
UNIT = 40.0         # px per spec height unit
BODY = 14.0         # body font size, every release
LIST_INDENT = 40.0  # ul and ol padding-left, every release


@dataclass(frozen=True)
class Typography:
    """One release's default markdown styles, in px. Each pair is (margin above,
    margin below); a heading is (font size, line height, margin above, below)."""

    release: str
    grid: float                  # the grid's width at a 1600 px viewport
    grid_filter_bar: float       # beside the open vertical filter bar
    h: dict[int, tuple[float, float, float, float]]
    line: float = 22.0           # body line height (paragraphs, list items, cells)
    p_m: tuple[float, float] = (0.0, 8.0)
    list_m: tuple[float, float] = (0.0, 9.5)
    cell_pad: float = 0.0        # a table cell's padding, top plus bottom
    table_spacing: float = 0.0   # border-spacing above, between and below rows
    quote_font: float = BODY
    quote_line: float = 22.0
    quote_m: tuple[float, float] = (0.0, 0.0)
    quote_pad: float = 0.0       # top plus bottom
    quote_inset: float = 0.0     # left plus right: padding, border and margin
    code_line: float = 22.0
    code_m: tuple[float, float] = (0.0, 0.0)
    code_pad: float = 0.0        # top plus bottom: padding and border
    hr: tuple[float, float, float] = (1.0, 15.0, 15.0)  # height, margin above, below


_H = {1: (28.0, 39.2, 12.0, 12.0), 3: (16.0, 22.4, 8.0, 4.0), 4: (16.0, 22.4, 8.0, 4.0),
      5: (16.0, 22.4, 8.0, 4.0), 6: (12.0, 16.8, 8.0, 4.0)}
TYPOGRAPHY = (
    # 4.1.4 and 5.0.0 drew every measured block alike.
    Typography(
        "4.1.4 and 5.0.0", grid=1536.0, grid_filter_bar=1276.0,
        h={**_H, 2: (21.0, 29.4, 12.0, 8.0)},
        quote_font=17.5, quote_line=27.5, quote_m=(0.0, 19.0), quote_pad=19.0,
        quote_inset=43.0, code_line=18.2, code_m=(0.0, 9.5), code_pad=20.0,
    ),
    Typography(
        "6.1.0", grid=1536.0, grid_filter_bar=1308.0,
        h={**_H, 2: (20.0, 28.0, 12.0, 8.0)},
        list_m=(14.0, 14.0), cell_pad=2.0, table_spacing=2.0, quote_m=(14.0, 14.0),
        quote_inset=80.0, code_m=(14.0, 14.0), hr=(2.0, 7.0, 7.0),
    ),
)


# -- parsing: markdown to blocks ----------------------------------------------------


@dataclass
class Block:
    kind: str                     # p | h | list | table | quote | code | hr
    level: int = 0                # a heading's level
    texts: list[str] = field(default_factory=list)   # p: its lines between hard breaks
    items: list[tuple[int, str]] = field(default_factory=list)  # list: (depth, text)
    loose: bool = False           # list items separated by blank lines
    rows: list[list[str]] = field(default_factory=list)  # table rows, header first
    lines: int = 0                # code: its lines


_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
_SETEXT = re.compile(r"^ {0,3}(?:=+|-+)[ \t]*$")
_HR = re.compile(r"^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")
_ITEM = re.compile(r"^([ \t]*)(?:[-*+]|\d{1,9}[.)])(?:[ \t]+(.*)|[ \t]*$)")
_QUOTE = re.compile(r"^ {0,3}> ?(.*)$")
_DELIM = re.compile(r"^[ \t]*\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*$")
_HTML_H = re.compile(r"^\s*<h([1-6])\b[^>]*>(.*?)</h\1>\s*$", re.I)
_HARD = re.compile(r"(?: {2,}|\\)$")
_BR = re.compile(r"<br\s*/?>", re.I)
_MEDIA = re.compile(r"!\[[^\]]*\]\([^)]*\)|<(?:img|iframe|video|svg|object|embed)\b", re.I)


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


def _parse_list(lines: list[str], i: int, blocks: list[Block]) -> int:
    """One list from line i, nested items and all; returns the line after it."""
    block = Block("list")
    indents: list[int] = []      # each open level's marker indent
    gap = False                  # a blank line since the last line of an item
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            gap = True
        elif m := _ITEM.match(line):
            indent = len(m.group(1))
            while indents and indent < indents[-1]:
                indents.pop()
            if not indents or indent > indents[-1] + 1:
                indents.append(indent)
            if gap and block.items and len(indents) == 1:
                block.loose = True
            block.items.append((len(indents) - 1, m.group(2) or ""))
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


# -- text width ---------------------------------------------------------------------

_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)|!?\[([^\]]*)\]\[[^\]]*\]")
_AUTOLINK = re.compile(r"<((?:https?|mailto):[^>\s]+)>")
_TAG = re.compile(r"</?[A-Za-z][^>]*>")
_EMPHASIS = re.compile(r"(?<![\w\\])[*_]+|[*_]+(?!\w)|~~")
# A break opportunity follows a space, or a hyphen between letters: Chromium breaks
# "great-circle" there, and long hyphenated URLs too.
_PIECE = re.compile(r"[^-]*-(?=[^\W\d])|.+")


def plain(text: str) -> str:
    """Inline markdown and HTML reduced to the text a reader sees."""
    text = _MEDIA.sub("", text)
    text = _LINK.sub(lambda m: m.group(1) or m.group(2) or "", text)
    text = _AUTOLINK.sub(r"\1", text)
    text = _TAG.sub("", text).replace("`", "")
    text = _EMPHASIS.sub("", text)
    text = re.sub(r"\\([!-/:-@\[-`{-~])", r"\1", text)
    return html.unescape(text)


def em_width(text: str) -> float:
    """A text's width on one line, in em."""
    out = 0.0
    for ch in text:
        o = ord(ch)
        if 32 <= o < 127:
            out += _EM[o - 32]
        elif ch == "\u00a0":
            out += _EM[0]
        else:
            out += _EM_WIDE if unicodedata.east_asian_width(ch) in ("W", "F") else _EM_OTHER
    return out


def wrapped_lines(text: str, width: float, font_px: float = BODY, scale: float = 1.0) -> int:
    """Lines a text takes in `width` px, wrapped greedily at spaces and hyphens as the
    browser wraps it. A word wider than the line takes a line of its own (the holder
    clips it sideways); an empty text still takes its line."""
    px = font_px * scale
    space = _EM[0] * px
    lines, x = 1, 0.0
    for word in re.split(r"[ \t\n]+", text.strip()):
        for k, piece in enumerate(_PIECE.findall(word)):
            w = em_width(piece) * px
            add = w + (space if k == 0 and x > 0 else 0.0)
            if x > 0 and x + add > width:
                lines += 1
                x = w
            else:
                x += add
    return lines


# -- layout: blocks to px -----------------------------------------------------------


def _table(rows: list[list[str]], width: float, t: Typography, scale: float) -> float:
    """Rows of one line while the columns fit side by side. Past that, each column
    gets a share of the width in proportion to its widest cell, roughly as the
    browser's automatic table layout does, and a row is as tall as its tallest cell."""
    texts = [[plain(c) for c in row] for row in rows]
    cols = max(len(r) for r in texts)
    widest = [max(em_width(r[k]) if k < len(r) else 0.0 for r in texts) * BODY * scale
              for k in range(cols)]
    total = sum(widest) or 1.0
    height = t.table_spacing
    for row in texts:
        lines = 1 if total <= width else max(
            wrapped_lines(c, width * widest[k] / total, BODY, scale) for k, c in enumerate(row))
        height += lines * t.line + t.cell_pad + t.table_spacing
    return height


def _box(block: Block, width: float, t: Typography, scale: float) -> tuple[float, float, float]:
    """(margin above, height, margin below) of one block, its text `width` px wide."""
    def lines(text: str, w: float = width, font: float = BODY, factor: float = 1.0) -> int:
        return wrapped_lines(plain(text), w, font, scale * factor)

    if block.kind == "h":
        font, lh, above, below = t.h[block.level]
        weight = _HEADING_WEIGHT if block.level <= 3 else 1.0
        return above, lines(block.texts[0], font=font, factor=weight) * lh, below
    if block.kind == "p":
        return t.p_m[0], sum(lines(s) for s in block.texts) * t.line, t.p_m[1]
    if block.kind == "list":
        # A loose list wraps each item in a paragraph, whose bottom margin parts the
        # items and joins the list's own below the last.
        height = sum(lines(text, width - LIST_INDENT * (depth + 1)) * t.line
                     for depth, text in block.items)
        gap = t.p_m[1] if block.loose else 0.0
        return t.list_m[0], height + gap * (len(block.items) - 1), max(t.list_m[1], gap)
    if block.kind == "table":
        return 0.0, _table(block.rows, width, t, scale), 0.0
    if block.kind == "quote":
        body = sum(lines(s, width - t.quote_inset, t.quote_font) for s in block.texts)
        height = body * t.quote_line + t.p_m[1] * (len(block.texts) - 1) + t.quote_pad
        return t.quote_m[0], height, t.quote_m[1]
    if block.kind == "code":
        return t.code_m[0], block.lines * t.code_line + t.code_pad, t.code_m[1]
    return t.hr[1], t.hr[0], t.hr[2]


def layout(blocks: list[Block], width: float, t: Typography,
           scale: float = 1.0) -> tuple[float, float]:
    """(how far down the last line reaches, the height that shows everything with no
    inner scrollbar), in px from the holder's top edge, for text `width` px wide.
    Margins between blocks collapse to the larger; the first block's margin above and
    the last one's below stay inside the padding."""
    y, below = PAD, None
    for block in blocks:
        above, height, after = _box(block, width, t, scale)
        y += (above if below is None else max(below, above)) + height
        below = after
    return y, y + (below or 0.0) + PAD


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
        """need_high in spec units, up to a whole 8 px grid row (a fifth of a unit)."""
        return math.ceil(round(self.need_high / 8, 6)) / 5


def estimate(markdown: str, cols: int, filter_bar: bool | None = False) -> Fit:
    """How a markdown text fits a block `cols` twelfths wide at a 1600 px viewport,
    beside the open filter bar or not. None says the bar may be there or not: the low
    bound then wraps without it and the high bound beside it."""
    blocks, media = parse(markdown)
    wide = text_width(cols, max(t.grid_filter_bar if filter_bar else t.grid
                                for t in TYPOGRAPHY))
    lows = [layout(blocks, wide, t, LOW_SCALE) for t in TYPOGRAPHY]
    # Windows narrows the grid by the page's own scrollbar too.
    highs = [layout(blocks, text_width(cols, (t.grid if filter_bar is False
                                              else t.grid_filter_bar) - SCROLLBAR) - SCROLLBAR,
                    t, HIGH_SCALE)[1]
             for t in TYPOGRAPHY]
    return Fit(min(a for a, _ in lows), min(b for _, b in lows), max(highs), media)
