"""size.markdown-fit, and the estimate of a markdown block's height behind it
(chartwright/design/markdown_fit.py), against what Superset drew.

tests/fixtures/markdown_fit/measurements.json holds what 4.1.4, 5.0.0 and 6.1.0 drew in
headless Chromium on 2026-10-05 (tools/record_markdown_fit.py), in windows 1600 and
1440 px wide:
- 17 texts (definitions in five sections, one-line strips of each heading level, lists
  tight, loose and nested, a table, a quote with code and a rule, hard breaks, <br>,
  setext headings, a long URL, capitals and digits) at 12, 8, 6, 4 and 3 twelfths, and
  the long text beside the filter bar. Each with the height it needs to show with no
  inner scrollbar and how far down its text reaches, in px;
- a dashboard's own markdown blocks under its own CSS (a sales review: a title,
  section captions with colour keys, a list, notes in sections, a footer), the same
  way;
- every element's computed type with no CSS, with each property the estimate reads
  set on each element from `.dashboard-markdown X`, and under that dashboard's CSS;
- in the 1440 px window, with Windows-style scrollbars on: each text at 6/12 and each
  styled block at its own width, at the height the fix writes and at heights that
  cut it off. Whether it scrolled, and where its text ended. The screenshots behind
  them showed text cut off at the bottom edge exactly where `text` passes `box`.

The low bound must never exceed what any release drew (or the rule warns of a block
that fits), and the high bound never fall short of it (or the fix leaves it cut off).
"""

import json
from pathlib import Path

import pytest

from chartwright.design import advise, advise_and_fix
from chartwright.design import markdown_fit as mf
from chartwright.design.markdown_fit import (RELEASES, VIEWPORT, box_px, estimate, parse,
                                             plain, read_css, text_width, tree, wrapped_lines)
from chartwright.design.model import RULES
from chartwright.design.presets import Overlay
from chartwright.spec import load_spec

MEASURED = json.loads((Path(__file__).parent / "fixtures" / "markdown_fit" /
                       "measurements.json").read_text(encoding="utf-8"))
CORPUS = MEASURED["corpus"]
STYLED = {name: (width, then, text) for name, width, then, text in MEASURED["styled"]}
STYLED_CSS = MEASURED["styled_css"]
DRAWN = MEASURED["releases"]
ABOUT = CORPUS["about"]
EMPTY = Overlay()
DS = {"database": "db", "table": "orders"}
MODEL = {"4.1.4": RELEASES[0], "5.0.0": RELEASES[0], "6.1.0": RELEASES[1]}
WINDOWS = ["1600", "1440"]


def drawn(window: str, part: str, key: str) -> list[dict]:
    return [r["viewports"][window][part][key] for r in DRAWN.values()]


CASES = [(w, "blocks", f"{n}@{c}") for w in WINDOWS for n in CORPUS for c in MEASURED["widths"]]
CASES += [(w, "filter_bar", f"about@{c}") for w in WINDOWS for c in MEASURED["widths"]]
CASES += [(w, "styled", name) for w in WINDOWS for name in STYLED]


def test_every_release_was_measured():
    assert set(DRAWN) == {"4.1.4", "5.0.0", "6.1.0"}
    for r in DRAWN.values():
        for window in WINDOWS:
            assert len(r["viewports"][window]["blocks"]) == len(CORPUS) * len(MEASURED["widths"])
            assert len(r["viewports"][window]["styled"]) == len(STYLED)
        assert len(r["verify"]) == len(CORPUS) * 3 and len(r["verify_css"]) == len(STYLED) * 2


@pytest.mark.parametrize("window,part,key", CASES)
def test_the_bounds_hold_what_every_release_drew(window, part, key):
    """Measured px are whole (scrollHeight rounds), so each bound may sit half a px past."""
    if part == "styled":
        cols, _, text = STYLED[key]
        fit = estimate(text, cols, css=STYLED_CSS, viewport=int(window))
    else:
        name, cols = key.split("@")
        fit = estimate(CORPUS[name], int(cols), part == "filter_bar", viewport=int(window))
    seen = drawn(window, part, key)
    low_text, low_need = min(d["text"] for d in seen), min(d["need"] for d in seen)
    high_need = max(d["need"] for d in seen)
    assert fit.text_low <= low_text + 0.5 and fit.need_low <= low_need + 0.5
    assert fit.need_high >= high_need - 0.5
    # Tight enough to be worth having: the low bound within a line or 4%, the high one
    # within a line (an h1's, 39.2 px) or 14%. Narrow blocks of long text beside the
    # filter bar are where the scrollbar allowance costs the most.
    assert low_need - fit.need_low <= max(23, 0.04 * low_need)
    assert fit.need_high - high_need <= max(40, 0.14 * high_need)


@pytest.mark.parametrize("release", sorted(DRAWN))
def test_the_rule_agrees_with_what_each_release_showed(release):
    """In a 1440 px window with Windows-style scrollbars: a block at the fix's height or
    taller never scrolled, a block the rule calls cut off was, and one it reports at all
    scrolled."""
    checked = {"fits": 0, "warn": 0, "finding": 0}
    r = DRAWN[release]
    for part, css in (("verify", None), ("verify_css", STYLED_CSS)):
        for key, seen in r[part].items():
            name = key.split("@")[0]
            text = CORPUS[name] if part == "verify" else STYLED[name][2]
            fit = estimate(text, seen["width"], css=css)
            box = box_px(seen["height"])
            assert box == seen["box"], key
            if fit.units <= seen["height"]:
                checked["fits"] += 1
                assert not seen["scrolls"], key
            if fit.text_low - box >= 8:
                checked["warn"] += 1
                assert seen["text"] - seen["box"] >= 8, key
            if fit.short(box):
                checked["finding"] += 1
                assert seen["scrolls"], key
    assert min(checked.values()) >= 5, checked


@pytest.mark.parametrize("release", sorted(DRAWN))
def test_the_grid_widths_the_estimate_assumes_are_the_ones_drawn(release):
    r, model = DRAWN[release], MODEL[release]
    for window in WINDOWS:
        at = r["viewports"][window]
        grid = model.grid(int(window), False)
        assert (at["grid_w"], at["grid_w_filter_bar"]) == (grid, model.grid(int(window), True))
        for key, seen in at["blocks"].items():
            assert abs(text_width(int(key.split("@")[1]), grid) - seen["content_w"]) < 1
    # A header row, a sub-tab and a footer row wrap text as wide as layout rows do.
    for where, width in r["containers"].items():
        assert abs(text_width(int(where.split("@")[1]), model.grid(1600, False)) - width) < 1


def test_heights_under_a_unit_still_draw_40_px():
    """The markdown component's minimum (GRID_MIN_ROW_UNITS rows of 8 px)."""
    assert [box_px(h) for h in (0.2, 0.6, 1, 1.6)] == [40, 40, 40, 64]
    shown = {k: v["box"] for r in DRAWN.values() for k, v in r["verify"].items()
             if v["height"] < 1}
    assert shown and set(shown.values()) == {40}


# -- the cascade --------------------------------------------------------------------

STYLE_CASES = [(release, case) for release in sorted(DRAWN)
               for case in ("default", "probe", "line", *STYLED)]


def _blocks(holder):
    out = []

    def walk(node):
        for child in node.children:
            if child.tag in ("p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "table",
                             "thead", "tbody", "tr", "th", "td", "blockquote", "pre", "hr"):
                out.append(child)
            if child.tag != "pre":
                walk(child)
    walk(holder)
    return out


def _row(st):
    return [st.font, st.line, st.margin[0], st.margin[2], st.padding[0], st.padding[2],
            st.border[0], st.border[2], st.inset, st.weight]


@pytest.mark.parametrize("release,case", STYLE_CASES)
def test_the_cascade_gives_each_element_its_drawn_type(release, case):
    """Superset's own styles under the dashboard's CSS, as the estimate applies them,
    against the computed style of every element each release drew: font size, line
    height, vertical margins, padding and borders, horizontal inset and weight."""
    probe = MEASURED["style_probe"]
    if case in STYLED:
        markdown, css, seen = STYLED[case][2], STYLED_CSS, DRAWN[release]["styles"]["styled"][case]
    else:
        markdown, css = probe["markdown"], probe.get(case)
        seen = DRAWN[release]["styles"][case]
    styles = mf._Styles(MODEL[release], read_css(css))
    nodes = _blocks(tree(markdown))
    assert [n.tag for n in nodes] == [b[0] for b in seen["blocks"]]
    for node, drawn_row in zip(nodes, seen["blocks"]):
        assert _row(styles.of(node)) == pytest.approx(drawn_row[1:], abs=0.01), node.tag
    for at, tags, *drawn_row in seen["inline"]:
        got = styles.inline(nodes[at], tuple(tags))
        assert _row(got) == pytest.approx(drawn_row, abs=0.01), (nodes[at].tag, tags)


def sheet(css):
    return read_css(css)


def styled(css, markdown, release=RELEASES[1]):
    styles = mf._Styles(release, css if isinstance(css, mf.Stylesheet) else read_css(css))
    return [(n.tag, styles.of(n)) for n in _blocks(tree(markdown))]


def test_rules_apply_in_cascade_order():
    css = """
    .dashboard-markdown h4 + p { font-size: 12.5px; margin: 5px 0 0; line-height: 1.6; }
    .dashboard-markdown p { font-size: 14px; line-height: 1.5; margin-bottom: 12px; }
    .dashboard-markdown p:last-child { margin-bottom: 0 !important; }
    .dashboard-markdown p:last-child { margin-bottom: 30px; }
    """
    (_, h4), (_, caption), (_, body), (_, last) = styled(
        css, "#### Title\nA caption.\n\nBody one.\n\nBody two.")
    # The adjacent sibling outranks the plain element, later order or not.
    assert (caption.font, round(caption.line, 3), caption.margin[0], caption.margin[2]) == (
        12.5, 20, 5, 0)
    assert (body.font, body.line, body.margin[2]) == (14, 21, 12)
    # !important beats a later rule of the same specificity.
    assert last.margin[2] == 0
    # Superset's own styles fill in what the CSS leaves alone; line heights are factors.
    assert (h4.font, h4.line, h4.margin[0], h4.margin[2]) == (16, 22.4, 8, 4)


def test_values_resolve_as_css_resolves_them():
    css = """
    .dashboard-markdown ul { font-size: 12px; }
    .dashboard-markdown li { line-height: 2; margin: 0.5em 0 1em; padding: 2px 4px 6px; }
    .dashboard-markdown h3 { font-size: 1.5em; line-height: 20px; border-bottom: 2px solid #ccc; }
    .dashboard-markdown h5 { border-top: 3px; letter-spacing: 0.1em; }
    """
    found = dict(styled(css, "### Head\n\n- one\n\n##### Small"))
    li, h3, h5 = found["li"], found["h3"], found["h5"]
    assert (li.font, li.line, li.margin[0], li.margin[2], li.padding[0], li.padding[2]) == (
        12, 24, 6, 12, 2, 6)
    assert li.inset == 8
    assert (h3.font, h3.line, h3.border[2]) == (21, 20, 2)   # 1.5em of the 14 px body
    # A border with no style draws nothing; letter-spacing counts in the element's em.
    assert h5.border[0] == 0 and h5.letter == pytest.approx(1.6)


def test_h6_keeps_supersets_font_size():
    """Superset sets an h6's size inside the markdown component's own class, more
    specifically than `.dashboard-markdown h6`: every release kept 12 px."""
    [(_, h6)] = styled(".dashboard-markdown h6 { font-size: 20px; margin: 0; }", "###### Six")
    assert (h6.font, h6.margin[0]) == (12, 0)
    [(_, h6)] = styled(".dashboard-markdown h6 { font-size: 20px !important; }", "###### Six")
    assert h6.font == 20


def test_what_the_estimate_reads_and_what_it_names():
    s = sheet("""
    @import url('https://fonts.googleapis.com/css2?family=Inter');
    /* .dashboard-markdown p { font-size: 99px; } */
    .dashboard-markdown p { font-size: 15px; color: #333; }
    .dashboard-markdown .safe-markdown > :last-child { margin-bottom: 0; }
    .dashboard-markdown [itemprop]::before { content: ""; width: 10px; margin: 0 6px; }
    .dashboard-markdown h2 { font-family: Georgia, serif; }
    .dashboard-markdown code { font-family: inherit; }
    @media (max-width: 900px) { .dashboard-markdown p { font-size: 12px; } }
    p { margin: 0; }
    .dashboard-markdown .dashboard-component-chart-holder { padding: 4px; border: 0; }
    .superset-chart-table td { border-top: 1px solid #eee; }
    .header-title { font-size: 15px !important; }
    .dashboard-markdown h3 { font-size: var(--title); }
    .dashboard-markdown li { margin: 5% 0; }
    .dashboard-markdown { margin: 8px; }
    """)
    names = {(sel, prop) for sel, prop in s.unread}
    assert names == {
        (".dashboard-markdown li", "margin"),
        (".dashboard-markdown [itemprop]::before", "content"),
        (".dashboard-markdown [itemprop]::before", "width"),
        (".dashboard-markdown [itemprop]::before", "margin"),
        (".dashboard-markdown h2", "font-family"),
        ("@media (max-width: 900px) { .dashboard-markdown p }", "font-size"),
        ("p", "margin"),
        (".dashboard-markdown .dashboard-component-chart-holder", "padding"),
        (".dashboard-markdown h3", "font-size"),
    }
    # The comment, the class markdown never carries and other components' rules are
    # read and match nothing; the holder's border is read.
    [(_, p)] = styled(s, "Text.")
    assert (p.font, p.margin[2]) == (15, 8)


def test_the_holders_border_counts():
    plain_fit = estimate("Figures refresh nightly.", 12)
    framed = estimate("Figures refresh nightly.", 12,
                      css=".dashboard-component-chart-holder { border: 2px solid #ccc; }")
    assert framed.need_low == plain_fit.need_low + 4 and framed.need_high == plain_fit.need_high + 4


def test_smaller_inline_text_needs_a_px_more_per_line_at_most():
    """A 12 px <em> in 15 px text on 22.5 px lines fits inside the line by CSS's
    arithmetic, and Chromium draws such a line up to a px taller: the low bound keeps
    22.5, the high one allows 23.5."""
    css = ".dashboard-markdown p { font-size: 15px; line-height: 1.5; } " \
          ".dashboard-markdown em { font-size: 12px; }"
    fit = estimate("*Updated nightly.*", 12, css=css)
    assert (fit.need_low, fit.need_high) == (16 + 22.5 + 8 + 16, 16 + 23.5 + 8 + 16)


# -- parsing ------------------------------------------------------------------------


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
    assert lst.ordered == [True, False, False, True, True]
    assert lst.items[3][1] == "d more of d"
    assert parse("![chart](x.png)")[1] and not parse("text")[1]


def test_the_tree_is_what_react_markdown_draws():
    holder = tree("1. a\n   - b\n2. c\n\n> q\n\n| x |\n|---|\n| 1 |")
    assert [n.tag for n in _blocks(holder)] == [
        "ol", "li", "ul", "li", "li", "blockquote", "p", "table", "thead", "tr", "th",
        "tbody", "tr", "td"]
    ol = holder.children[0]
    assert (ol.first, ol.last, holder.children[1].prev is ol) == (True, False, True)
    assert holder.parent.parent.classes == {"dashboard-markdown"}


def test_plain_text_is_what_the_reader_sees():
    assert plain("**Bold** and _em_, [a link](http://x), `code`, ~~gone~~ &amp; <b>tag</b>") == \
        "Bold and em, a link, code, gone & tag"
    assert plain("snake_case stays") == "snake_case stays"
    assert plain('<span itemprop="key"></span>**Apr–Jun** \\*not em\\*') == "Apr–Jun *not em*"
    assert mf._runs("a *b* `c*d*`") == [("a ", ()), ("b", ("em",)), (" ", ()), ("c*d*", ("code",))]


def test_text_wraps_at_spaces_and_after_hyphens_between_letters():
    assert wrapped_lines("", 100) == 1
    assert wrapped_lines("word " * 10, 10_000) == 1
    # One unbreakable word wider than the line keeps a line of its own.
    assert wrapped_lines("a " + "x" * 200 + " b", 300) == 3
    assert wrapped_lines("great-circle", 60) == 2 and wrapped_lines("-5", 1) == 1
    assert wrapped_lines("Jul–Sep", 30) == 2


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
    """A full-width block of about 1,000 words in five sections, 10 units high: every
    release drew it over 1,150 px tall; the block showed 400."""
    data = mk(rows({"markdown": ABOUT, "width": 12, "height": 10}))
    [f] = fits(data)
    fit = estimate(ABOUT, 12)
    assert f.severity == "warn" and f.where == "layout row 0" and 30 < fit.units < 33
    hidden = round((fit.text_low - 400) / 22)
    assert f.detail.startswith(f"markdown text runs past its block: about {hidden} lines cut off")
    assert (f"it needs ~{fit.units:g} units at 12/12 in a {VIEWPORT} px wide window, has 10; "
            "raise the height") in f.detail
    fixed, report = advise_and_fix(data, overlay=EMPTY)
    assert fixed["layout"]["rows"][0][0]["height"] == fit.units
    entry = next(e for e in report.fixed if e["rule"] == "size.markdown-fit")
    assert entry["md"] == [None, 0, 0] and entry["was"] == {"height": 10}
    assert not fits(fixed)


def styled_block(name, height=None, **dashboard):
    cols, then, text = STYLED[name]
    return mk(rows({"markdown": text, "width": cols, "height": height or then}),
              css=STYLED_CSS, **dashboard)


@pytest.mark.parametrize("name,level", [
    # As every release drew them in a 1440 px window: the notes 26 to 36 px over at 15
    # units and two three-line captions 26 and 28 px over, their last line cut by a
    # grid row or more; captions 8 to 12 px over, where only the padding or the edge
    # of the last line is (one of them within a few percent of wrapping onto another
    # line, which the high bound allows for); the footer exactly full at 1.2 units, and
    # the title, which fit.
    ("notes", "warn"), ("caption-staff", "warn"), ("caption-floor", "warn"),
    ("caption-stores", "info"), ("caption-change", "info"), ("caption-years", "info"),
    ("footer", None), ("title", None),
])
def test_a_dashboard_read_with_its_own_css(name, level):
    found = fits(styled_block(name))
    assert [f.severity for f in found] == ([level] if level else [])
    if found:
        fixed, _ = advise_and_fix(styled_block(name), overlay=EMPTY)
        height = fixed["layout"]["rows"][0][0]["height"]
        seen = max(r["verify_css"][f"{name}@fix"]["box"] for r in DRAWN.values())
        assert box_px(height) == seen and not fits(fixed)


def test_the_caveat_names_only_what_the_estimate_doesnt_read():
    [f] = fits(styled_block("caption-change"))
    assert f.detail.endswith("; the dashboard's CSS also styles markdown in a way this "
                             "estimate doesn't read (.dashboard-markdown [itemprop]::before), "
                             "so the height may be off")
    read_only = ".dashboard-markdown p { font-size: 15px; line-height: 1.5; }"
    [f] = fits(mk(rows({"markdown": ABOUT, "height": 10}), css=read_only))
    assert f.detail.endswith("raise the height")
    [f] = fits(mk(rows({"markdown": ABOUT, "height": 10}), theme="Brand"))
    assert f.detail.endswith("; this assumes Superset's own text sizes and spacing, which the "
                             "theme 'Brand' may change")


def test_without_its_css_the_estimate_misses_both_ways():
    """The footer: 220 characters of 11.5 px h5 fill its 48 px exactly, where
    Superset's own 16 px h5 needs 22.4 px lines. A caption: Superset's own sizes put
    the low bound above what every release drew, its 13 px caption inside it."""
    cols, _, footer = STYLED["footer"]
    assert fits(mk(rows({"markdown": footer, "width": cols, "height": 1.2})))
    assert not fits(styled_block("footer"))
    text = STYLED["caption-stores"][2]
    drew = max(r["viewports"]["1440"]["styled"]["caption-stores"]["need"] for r in DRAWN.values())
    assert estimate(text, 12).need_low > drew
    read = estimate(text, 12, css=STYLED_CSS)
    assert read.need_low <= drew <= read.need_high


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
    height = estimate(ABOUT, 12).units
    block = {"markdown": ABOUT, "width": 12, "height": height}
    select = [{"name": "Region", "type": "select", "dataset": DS, "column": "region"}]
    assert not fits(mk(rows(block)))
    [f] = fits(mk(rows(block), filters=select))
    assert f.severity == "warn"
    # A horizontal bar sits above the grid; without its feature flag 4.1.4 and 5.0.0 draw
    # it on the left, so nothing is sure enough to warn about...
    horizontal = mk(rows(block), filters=select, filter_bar_orientation="horizontal")
    assert [f.severity for f in fits(horizontal)] in ([], ["info"])
    # ...unless the spec can only go to 6.1.0, which draws it across the top.
    assert not fits(mk(rows(block), filters=select, filter_bar_orientation="horizontal",
                       theme="Brand"))


def test_a_spec_for_newer_releases_is_estimated_on_them_alone():
    """A theme takes 6.0.0 or later, so the estimate leaves out 4.1.4 and 5.0.0's type:
    a 21 px ## heading is 1.4 px taller than 6.1.0's 20 px one."""
    assert estimate("## Punctuality", 12).need_high == pytest.approx(81.4)
    newer = mf.releases_from((6, 0, 0))
    assert newer == RELEASES[1:]
    assert estimate("## Punctuality", 12, releases=newer).need_high == pytest.approx(80)


def test_a_narrow_block_is_told_it_can_widen():
    data = mk({"rows": [[{"markdown": CORPUS["section"], "width": 4, "height": 4}, "K"]]})
    [f] = fits(data)
    assert f"at 4/12 in a {VIEWPORT} px wide window, has 4; raise the height or widen it" in f.detail


def test_no_fix_when_the_height_cannot_be_known_or_written():
    image = fits(mk(rows({"markdown": "![Trend](https://example.org/t.png)\n\n" + ABOUT,
                          "height": 10})))[0]
    assert image.fix is None and "needs at least ~" in image.detail
    huge = fits(mk(rows({"markdown": ABOUT * 3, "width": 3, "height": 50})))[0]
    assert huge.fix is None and "past the 100-unit maximum" in huge.detail


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
