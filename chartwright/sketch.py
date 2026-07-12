"""ASCII layout sketches: grid-template-areas for Superset's row/column tree.

The user draws each tab as text; the compiler turns it into ROW / COLUMN /
CHART geometry. Superset's real model (4.1 source): widths are TWELFTHS of the
page (GRID_COLUMN_COUNT=12, responsive), heights are absolute 8-px units, and
the layout is a strict tree: rows stack, a COLUMN stacks charts inside a row
slot, and nothing deeper exists. A sketch therefore compiles iff it guillotines
into rows of charts/columns; anything else is a named error.

Semantics:
- Spaces are cosmetic separators, stripped per line; after stripping every
  line must have the SAME cell count (that count = the grid width, scaled to
  12 columns by largest-remainder so each band sums to exactly 12).
- Each sketch line adds `line_units` spec height units (1 unit = 40 px);
  repeat a line to make its regions taller.
- Every symbol's cells must form a solid rectangle.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SketchChart:
    name: str
    width: int        # twelfths
    height: int       # spec height units


@dataclass
class SketchColumn:
    width: int        # twelfths
    children: list[SketchChart] = field(default_factory=list)


@dataclass
class SketchRow:
    children: list[SketchChart | SketchColumn] = field(default_factory=list)


HOLE = "."  # reserved: a deliberately empty cell (grid-template-areas prior art)


class SketchError(ValueError):
    pass


def _widths_to_twelfths(cell_counts: list[int], total_cells: int) -> list[int]:
    """Largest-remainder scaling so each band sums to exactly 12 columns."""
    raw = [c * 12 / total_cells for c in cell_counts]
    floors = [max(1, int(r)) for r in raw]
    while sum(floors) > 12:  # over-min floors: shave the largest
        i = max(range(len(floors)), key=lambda k: floors[k])
        if floors[i] == 1:
            raise SketchError(f"too many charts side by side for a 12-column grid: {cell_counts}")
        floors[i] -= 1
    remainders = sorted(range(len(raw)), key=lambda k: raw[k] - int(raw[k]), reverse=True)
    for i in remainders:
        if sum(floors) == 12:
            break
        floors[i] += 1
    return floors


def parse_sketch(lines: list[str], legend: dict[str, str], line_units: int) -> list[SketchRow]:
    if not lines:
        raise SketchError("sketch is empty")
    grid = [[ch for ch in line if ch != " "] for line in lines]
    width = len(grid[0])
    if width == 0:
        raise SketchError("sketch line 1 is blank")
    for i, row in enumerate(grid):
        if len(row) != width:
            raise SketchError(
                f"sketch line {i + 1} has {len(row)} cells, line 1 has {width} "
                "(spaces are separators; cell counts must match)"
            )

    if HOLE in legend:
        raise SketchError(f"{HOLE!r} is a reserved sketch symbol (empty cell); pick another legend symbol")
    symbols = {ch for row in grid for ch in row} - {HOLE}
    unknown = sorted(symbols - set(legend))
    if unknown:
        raise SketchError(f"sketch symbols not in legend: {unknown}")
    unused = sorted(set(legend) - symbols)
    if unused:
        raise SketchError(f"legend symbols never drawn: {unused}")

    # every symbol must fill a solid rectangle
    boxes: dict[str, tuple[int, int, int, int]] = {}  # r0, r1, c0, c1 inclusive
    for s in symbols:
        cells = [(r, c) for r, row in enumerate(grid) for c, ch in enumerate(row) if ch == s]
        r0, r1 = min(r for r, _ in cells), max(r for r, _ in cells)
        c0, c1 = min(c for _, c in cells), max(c for _, c in cells)
        if len(cells) != (r1 - r0 + 1) * (c1 - c0 + 1):
            raise SketchError(f"symbol {s!r} does not form a solid rectangle")
        boxes[s] = (r0, r1, c0, c1)

    # horizontal bands: cut where no rectangle spans the boundary
    cuts = [0]
    for r in range(1, len(grid)):
        if all(not (b[0] < r <= b[1]) for b in boxes.values()):
            cuts.append(r)
    cuts.append(len(grid))

    rows: list[SketchRow] = []
    for b0, b1 in zip(cuts, cuts[1:]):
        band = {s: box for s, box in boxes.items() if b0 <= box[0] and box[1] < b1}
        if not band:
            raise SketchError(
                f"line {b0 + 1} is entirely empty: Superset has no vertical spacer; "
                "use more lines on neighbors or a markdown block in rows mode"
            )
        # vertical slices: group symbols sharing column extents
        slices: dict[tuple[int, int], list[str]] = {}
        for s, (r0, r1, c0, c1) in sorted(band.items(), key=lambda kv: (kv[1][2], kv[1][0])):
            for (sc0, sc1), members in slices.items():
                if c0 <= sc1 and sc0 <= c1:  # overlaps an existing slice
                    if (c0, c1) != (sc0, sc1):
                        raise SketchError(
                            f"symbol {s!r} partially overlaps the column span of {members}: "
                            "Superset cannot nest a row inside a column; align the columns "
                            "or split into separate full-width rows"
                        )
                    members.append(s)
                    break
            else:
                slices[(c0, c1)] = [s]

        # Holes ('.') are legal only where Superset can express absence:
        # trailing right of the band (rows pack left) or at the BOTTOM of a
        # slice (rows/columns pack upward; row height = tallest child).
        cmax = max(c1 for (_, _, _, c1) in band.values())
        spans = list(slices)
        for r in range(b0, b1):
            for c in range(cmax + 1):
                if grid[r][c] == HOLE and not any(c0 <= c <= c1 for c0, c1 in spans):
                    raise SketchError(
                        f"empty column {c + 1} between charts (line {r + 1}): Superset "
                        "rows pack left; empty space goes rightmost"
                    )
        trailing_holes = width - 1 - cmax

        ordered = sorted(slices.items(), key=lambda kv: kv[0][0])
        counts = [c1 - c0 + 1 for (c0, c1), _ in ordered]
        if trailing_holes:
            counts.append(trailing_holes)  # phantom entry scales real widths correctly
        widths = _widths_to_twelfths(counts, width)

        row = SketchRow()
        for ((_, _), members), w in zip(ordered, widths):
            if len(members) == 1:
                s = members[0]
                r0, r1 = boxes[s][0], boxes[s][1]
                if r0 != b0:
                    raise SketchError(
                        f"empty space above chart {legend[s]!r} (line {b0 + 1}): Superset "
                        "packs upward; empty cells in a column go at the bottom"
                    )
                row.children.append(SketchChart(legend[s], w, (r1 - r0 + 1) * line_units))
            else:
                members.sort(key=lambda s: boxes[s][0])
                # stacked members must tile downward from the band top with no
                # gaps; '.' may only end the stack early (bottom holes)
                expect = b0
                col = SketchColumn(width=w)
                for s in members:
                    r0, r1 = boxes[s][0], boxes[s][1]
                    if r0 != expect:
                        raise SketchError(
                            f"symbols {members} must stack with no vertical gaps "
                            f"(symbol {s!r} starts at line {r0 + 1}, expected {expect + 1}; "
                            "empty cells may only end a stack, not interrupt it)"
                        )
                    expect = r1 + 1
                    col.children.append(SketchChart(legend[s], w, (r1 - r0 + 1) * line_units))
                row.children.append(col)
        rows.append(row)
    return rows
