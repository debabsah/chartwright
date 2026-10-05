"""size.markdown-fit, and the estimate of a markdown block's height behind it
(chartwright/design/markdown_fit.py), against what Superset drew.

tests/fixtures/markdown_fit/measurements.json holds what 4.1.4, 5.0.0 and 6.1.0 drew in
headless Chromium at a 1600 px viewport on 2026-10-05 (tools/record_markdown_fit.py):
- 17 texts (definitions in five sections, one-line strips of each heading level, lists
  tight, loose and nested, a table, a quote with code and a rule, hard breaks, <br>,
  setext headings, a long URL, capitals and digits) at 12, 8, 6, 4 and 3 twelfths, and
  the long text beside the filter bar. Each with the height it needs to show with no
  inner scrollbar and how far down its text reaches, in px;
- each text at 6/12 at three heights, with Windows-style scrollbars on: the height the
  fix writes, the height it needs (to an 8 px grid row) and a unit less. Whether it
  scrolled, and where its text ended. The screenshots behind them showed text cut off
  at the bottom edge exactly where `text` passes `box`.

The low bound must never exceed what any release drew (or the rule warns of a block
that fits), and the high bound never fall short of it (or the fix leaves it cut off).
"""

import json
from pathlib import Path

import pytest

from chartwright.design import advise, advise_and_fix
from chartwright.design.markdown_fit import (TYPOGRAPHY, box_px, estimate, parse, plain,
                                             text_width, wrapped_lines)
from chartwright.design.model import RULES
from chartwright.design.presets import Overlay
from chartwright.spec import load_spec

MEASURED = json.loads((Path(__file__).parent / "fixtures" / "markdown_fit" /
                       "measurements.json").read_text(encoding="utf-8"))
CORPUS = MEASURED["corpus"]
RELEASES = MEASURED["releases"]
ABOUT = CORPUS["about"]
EMPTY = Overlay()
DS = {"database": "db", "table": "orders"}


def drawn(key: str, filter_bar: bool) -> list[dict]:
    return [r["filter_bar" if filter_bar else "blocks"][key] for r in RELEASES.values()
            if key in r["filter_bar" if filter_bar else "blocks"]]


CASES = [(f"{n}@{w}", False) for n in CORPUS for w in MEASURED["widths"]]
CASES += [(f"about@{w}", True) for w in MEASURED["widths"]]


def test_every_release_was_measured():
    assert set(RELEASES) == {"4.1.4", "5.0.0", "6.1.0"}
    for r in RELEASES.values():
        assert len(r["blocks"]) == len(CORPUS) * len(MEASURED["widths"])
        assert len(r["verify"]) == len(CORPUS) * 3


@pytest.mark.parametrize("key,filter_bar", CASES)
def test_the_bounds_hold_what_every_release_drew(key, filter_bar):
    """Measured px are whole (scrollHeight rounds), so each bound may sit half a px past."""
    name, w = key.split("@")
    seen = drawn(key, filter_bar)
    fit = estimate(CORPUS[name], int(w), filter_bar)
    low_text, low_need = min(d["text"] for d in seen), min(d["need"] for d in seen)
    high_need = max(d["need"] for d in seen)
    assert fit.text_low <= low_text + 0.5 and fit.need_low <= low_need + 0.5
    assert fit.need_high >= high_need - 0.5
    # Tight enough to be worth having: the low bound within a line or 4%, the high one
    # within a line or 11% (narrow blocks of long text beside the filter bar are where
    # the scrollbar allowance costs the most).
    assert low_need - fit.need_low <= max(23, 0.04 * low_need)
    assert fit.need_high - high_need <= max(23, 0.11 * high_need)


@pytest.mark.parametrize("release", sorted(RELEASES))
def test_the_rule_agrees_with_what_each_release_showed(release):
    """At 6/12 with Windows-style scrollbars: a block at the fix's height or taller never
    scrolled, a block the rule calls cut off was, and one it reports at all scrolled."""
    checked = {"fits": 0, "warn": 0, "finding": 0}
    for key, seen in RELEASES[release]["verify"].items():
        fit = estimate(CORPUS[key.split("@")[0]], 6)
        box = box_px(seen["height"])
        assert box == seen["box"], key
        if fit.units <= seen["height"]:
            checked["fits"] += 1
            assert not seen["scrolls"], key
        if fit.text_low - box >= 8:
            checked["warn"] += 1
            assert seen["text"] - seen["box"] >= 8, key
        if fit.need_low > box:
            checked["finding"] += 1
            assert seen["scrolls"], key
    assert min(checked.values()) >= 5, checked


def test_the_grid_widths_the_estimate_assumes_are_the_ones_drawn():
    for release, r in RELEASES.items():
        t = TYPOGRAPHY[1] if release == "6.1.0" else TYPOGRAPHY[0]
        assert (r["grid_w"], r["grid_w_filter_bar"]) == (t.grid, t.grid_filter_bar)
        for key, seen in r["blocks"].items():
            assert abs(text_width(int(key.split("@")[1]), t.grid) - seen["content_w"]) < 1
        # A header row, a sub-tab and a footer row wrap text as wide as layout rows do.
        for where, width in r["containers"].items():
            assert abs(text_width(int(where.split("@")[1]), t.grid) - width) < 1


def test_heights_under_a_unit_still_draw_40_px():
    """The markdown component's minimum (GRID_MIN_ROW_UNITS rows of 8 px)."""
    assert [box_px(h) for h in (0.2, 0.6, 1, 1.6)] == [40, 40, 40, 64]
    shown = {k: v["box"] for r in RELEASES.values() for k, v in r["verify"].items()
             if v["height"] < 1}
    assert shown and set(shown.values()) == {40}


# -- parsing ----------------------------------------------------------------------


def kinds(markdown):
    return [(b.kind, b.level) if b.kind == "h" else b.kind for b in parse(markdown)[0]]


def test_parse_reads_the_common_shapes():
    assert kinds("# A\n## B\nText\nmore text\n\n- a\n- b\n\n| x | y |\n|---|---|\n| 1 | 2 |"
                 "\n\n> q\n\n```\ncode\n```\n\n---\n\nEnd") == [
        ("h", 1), ("h", 2), "p", "list", "table", "quote", "code", "hr", "p"]
    assert kinds("Title\n=====\n\nSub\n---\n\nBody") == [("h", 1), ("h", 2), "p"]
    assert kinds("<h3>Html heading</h3>\n\nText") == [("h", 3), "p"]
    p = parse("one  \ntwo\\\nthree<br>four\nstill four")[0][0]
    assert p.texts == ["one", "two", "three", "four still four"]
    lst = parse("1. a\n   - b\n   - c\n2. d\n\n   more of d\n3. e")[0][0]
    assert [d for d, _ in lst.items] == [0, 1, 1, 0, 0] and lst.loose
    assert lst.items[3][1] == "d more of d"
    assert parse("![chart](x.png)")[1] and not parse("text")[1]


def test_plain_text_is_what_the_reader_sees():
    assert plain("**Bold** and _em_, [a link](http://x), `code`, ~~gone~~ &amp; <b>tag</b>") == \
        "Bold and em, a link, code, gone & tag"
    assert plain("snake_case stays") == "snake_case stays"


def test_text_wraps_at_spaces_and_after_hyphens_between_letters():
    assert wrapped_lines("", 100) == 1
    assert wrapped_lines("word " * 10, 10_000) == 1
    # One unbreakable word wider than the line keeps a line of its own.
    assert wrapped_lines("a " + "x" * 200 + " b", 300) == 3
    assert wrapped_lines("great-circle", 60) == 2 and wrapped_lines("-5", 1) == 1


# -- the rule -----------------------------------------------------------------------


def mk(layout, filters=None, **dashboard):
    data = {"spec_version": "1", "dashboard": {"title": "T", "slug": "t", **dashboard},
            "charts": [{"type": "big_number_total", "name": "K", "dataset": DS,
                        "metric": "COUNT(*)"}],
            "layout": layout}
    if filters:
        data["filters"] = filters
    return data


def rows(*blocks):
    return {"rows": [*([b] for b in blocks), ["K"]]}


def fits(data):
    return [f for f in advise(load_spec(data), overlay=EMPTY).findings
            if f.rule == "size.markdown-fit"]


def test_the_rule_is_registered_with_its_levels():
    r = RULES["size.markdown-fit"]
    assert (r.severity, r.severities, r.fixable, r.data_aware, r.since) == (
        "warn", ("warn", "info"), True, False, "11")


def test_definitions_cut_off_mid_section_warn_and_the_fix_shows_them():
    """The About tab: a full-width block of about 1,000 words in five sections, 10 units
    high. Every release drew it 1,156 px tall; the block showed 400."""
    data = mk(rows({"markdown": ABOUT, "width": 12, "height": 10}))
    [f] = fits(data)
    need = estimate(ABOUT, 12).units
    assert f.severity == "warn" and f.where == "layout row 0" and 28.9 < need < 30
    assert f.detail.startswith("markdown text runs past its block: about 33 lines cut off")
    assert f"it needs ~{need:g} units at 12/12, has 10; raise the height" in f.detail
    fixed, report = advise_and_fix(data, overlay=EMPTY)
    assert fixed["layout"]["rows"][0][0]["height"] == need
    entry = next(e for e in report.fixed if e["rule"] == "size.markdown-fit")
    assert entry["md"] == [None, 0, 0] and entry["was"] == {"height": 10}
    assert not fits(fixed)


@pytest.mark.parametrize("markdown,height,level", [
    # Short strips: the 16 px padding and the heading's margins come before the text.
    ("## Punctuality", 1.2, "warn"), ("## Punctuality", 1.6, "info"), ("## Punctuality", 2, None),
    ("# Network performance", 1.2, "warn"), ("# Network performance", 2, "info"),
    ("# Network performance", 2.4, None),
    ("Figures refresh nightly at 02:00 UTC.", 0.6, "info"),
    ("Figures refresh nightly at 02:00 UTC.", 1, "info"),
    ("Figures refresh nightly at 02:00 UTC.", 1.6, None),
    ("Figures refresh nightly at 02:00 UTC.", None, None),
])
def test_strips_shorter_than_their_line_and_padding(markdown, height, level):
    block = {"markdown": markdown, **({"height": height} if height else {})}
    found = fits(mk(rows(block)))
    assert [f.severity for f in found] == ([level] if level else [])
    if level == "info":
        assert "so the block scrolls, and Windows draws a scrollbar in it" in found[0].detail


def test_every_place_a_block_can_sit_is_checked_and_fixed_in_place():
    strip = {"markdown": "## Section", "height": 1.2}
    data = mk({
        "header": [[dict(strip)]],
        "tabs": [{"title": "Main", "rows": [[dict(strip)], ["K"]]},
                 {"title": "More", "tabs": [{"title": "Sub", "rows": [{"header": "H"}, [dict(strip)]]}]}],
        "footer": [[dict(strip)]],
    })
    assert sorted(f.where for f in fits(data)) == [
        "footer row 0", "header row 0", "tab 'Main' row 0", "tab 'More > Sub' row 1"]
    fixed, _ = advise_and_fix(data, overlay=EMPTY)
    lay = fixed["layout"]
    assert {lay["header"][0][0]["height"], lay["tabs"][0]["rows"][0][0]["height"],
            lay["tabs"][1]["tabs"][0]["rows"][1][0]["height"], lay["footer"][0][0]["height"]} == {2.2}
    assert not fits(fixed)


def test_the_filter_bar_narrows_the_text():
    block = {"markdown": ABOUT, "width": 12, "height": 30}
    select = [{"name": "Region", "type": "select", "dataset": DS, "column": "region"}]
    assert not fits(mk(rows(block)))
    [f] = fits(mk(rows(block), filters=select))
    assert f.severity == "warn"
    # A horizontal bar sits above the grid; without its feature flag 4.1.4 and 5.0.0 draw
    # it on the left, so nothing is sure enough to warn about.
    assert not fits(mk(rows(block), filters=select, filter_bar_orientation="horizontal"))


def test_a_narrow_block_is_told_it_can_widen():
    data = mk({"rows": [[{"markdown": CORPUS["section"], "width": 4, "height": 4}, "K"]]})
    [f] = fits(data)
    assert "at 4/12, has 4; raise the height or widen it" in f.detail


def test_no_fix_when_the_height_cannot_be_known_or_written():
    image = fits(mk(rows({"markdown": "![Trend](https://example.org/t.png)\n\n" + ABOUT,
                          "height": 10})))[0]
    assert image.fix is None and "needs at least ~" in image.detail
    huge = fits(mk(rows({"markdown": ABOUT * 3, "width": 3, "height": 50})))[0]
    assert huge.fix is None and "past the 100-unit maximum" in huge.detail


@pytest.mark.parametrize("dashboard,caveat", [
    ({"css": ".dashboard-markdown p { font-size: 16px; }"}, "the dashboard's CSS sets its own type"),
    ({"theme": "Brand"}, "which the theme 'Brand' may change"),
    ({"css": ".dashboard-markdown h2 { color: #003366; }"}, None),
])
def test_custom_type_is_named_and_the_fix_still_offered(dashboard, caveat):
    [f] = fits(mk(rows({"markdown": ABOUT, "height": 10}), **dashboard))
    assert f.fix is not None
    assert (caveat in f.detail) if caveat else f.detail.endswith("raise the height")


def test_a_finding_can_be_ignored_by_its_position():
    data = mk(rows({"markdown": ABOUT, "height": 10}))
    data["design"] = {"ignore": ["size.markdown-fit@layout-row-0"]}
    rep = advise(load_spec(data), overlay=EMPTY)
    assert not [f for f in rep.findings if f.rule == "size.markdown-fit"]
    assert "size.markdown-fit@layout-row-0" in rep.ignored


def test_the_two_markdown_rules_settle_on_one_height():
    """layout.markdown-height shrinks a one-line block to the height the fit estimate
    gives it, and size.markdown-fit never raises it again."""
    data = mk(rows({"markdown": "# Network performance", "height": 6},
                   {"markdown": "## Section", "height": 1.2}))
    fixed, report = advise_and_fix(data, overlay=EMPTY)
    assert [fixed["layout"]["rows"][i][0]["height"] for i in (0, 1)] == [2.4, 2.2]
    assert sorted(e["rule"] for e in report.fixed if "markdown" in e["rule"]) == [
        "layout.markdown-height", "size.markdown-fit"]
    again, report = advise_and_fix(fixed, overlay=EMPTY)
    assert again == fixed and not report.fixed
