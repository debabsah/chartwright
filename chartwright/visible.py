"""`chartwright standards verify-visible`: is the text a standard locks visible on the
rendered dashboard? (docs/DESIGN-BRAIN.md sec.18, "Checking what readers see").

`standard.css-hides` reads CSS declarations and knows a list of ways to hide an element;
any other way passes it, and selectors are no Superset contract. This check looks at
the dashboard itself, after deploy, in a real browser: it signs in, opens the dashboard,
finds each locked header or footer row's text and asks the browser whether a reader can
see it. A text is hidden when it isn't on the page, isn't rendered (display,
visibility, content-visibility), has no size, sits off the page, is cut off by its
container, is transparent, is clipped, is too small, has almost no contrast with its
background, or another element covers it.

Playwright is an optional extra (`pip install 'chartwright[visual]'`, then
`playwright install chromium`); without it the command says how to install it. The
browser half is `measure` (JavaScript run in the page); the verdict is `judge`, plain
Python over what was measured, so the rules are tested without a browser.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MIN_CONTRAST = 2.0       # below this a text all but disappears (white on white is 1.0)
MIN_FONT_PX = 6.0
MIN_VISIBLE_RATIO = 0.5  # of the text's box left after its containers clip it
MIN_OPACITY = 0.1
INSTALL = ("pip install 'chartwright[visual]' && playwright install chromium")


# -- what to look for ---------------------------------------------------------------


_MD_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_MD_MARKS = re.compile(r"[*_`#>~|]")


def normalize(text: str) -> str:
    """Text as compared on both sides: markdown marks and case set aside, whitespace
    collapsed, so `**Confidential**: named` matches the rendered `Confidential: named`."""
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_MARKS.sub("", text)
    return " ".join(text.split()).lower()


def lines_of(markdown: str) -> list[str]:
    """The lines a markdown block renders, each a target of its own: block elements
    join without a space in the page's text, so a paragraph is matched alone."""
    out = []
    for line in markdown.splitlines():
        line = re.sub(r"^\s*(?:[-+*]|\d+[.)])\s+", "", line)   # list markers
        line = re.sub(r"<[^>]+>", " ", line)                   # inline HTML tags
        if normalize(line):
            out.append(" ".join(line.split()))
    return out


@dataclass
class Target:
    item: str              # the design.standard_written key, layout.footer[org][0]
    layer: str
    locked_by: str
    text: str              # one rendered line, as written

    @property
    def match(self) -> str:
        return normalize(self.text)


@dataclass
class Skipped:
    item: str
    why: str


def targets(std, spec) -> tuple[list[Target], list[Skipped]]:
    """The locked header and footer rows of the spec's standard, one target per rendered
    line, for the rows the spec holds as the standard has them. A locked row the spec
    lacks or changed is skipped (standards check reports it), as is one a waiver covers
    or one the run's release holds back."""
    from .design import content as C

    analysis = C.analyze(std, spec)
    out: list[Target] = []
    skipped = [Skipped(i.id, f"held back: {r}") for i, r in analysis.held
               if i.slot in C.ROW_SLOTS and i.locked_by]
    for d in analysis.decisions:
        if d.item is None or d.slot not in C.ROW_SLOTS or not d.locked_by:
            continue
        if d.waiver is not None:
            skipped.append(Skipped(d.id, f"a waiver lets this dashboard differ "
                                         f"(waivers[{d.waiver.index}], {d.waiver.owner})"))
            continue
        if not d.conforms or d.found is None:
            skipped.append(Skipped(d.id, "the spec doesn't hold it as the standard has it; "
                                         "standards check reports that"))
            continue
        row = C.normal_row(d.item.value)
        texts: list[str] = []
        if isinstance(row, dict) and "header" in row:
            texts = [" ".join(row["header"].split())]
        elif isinstance(row, dict) and "row" in row:
            for block in row["row"]:
                texts += lines_of(block.get("markdown", ""))
        for t in texts:
            if t:
                out.append(Target(d.id, d.layer, d.locked_by, t))
    return out, skipped


# -- what the browser measured, and the verdict -----------------------------------


@dataclass
class Facts:
    """One target as the browser saw it (measure). Coordinates are page pixels."""

    found: bool
    rendered: bool = True        # the element's rendered text (innerText) holds it
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0
    page_width: float = 0.0
    page_height: float = 0.0
    visibility: str = "visible"
    display_none: bool = False
    opacity: float = 1.0         # the product down the element's ancestors
    clip: str | None = None      # a clip-path or clip on the element or an ancestor
    visible_ratio: float = 1.0   # of the box left after overflow-clipping containers
    font_px: float = 16.0
    color: tuple | None = None   # (r, g, b, a), the text's own colour
    background: tuple | None = None  # the first opaque background behind it; None: unknown
    covered_by: str | None = None    # the element on top of the text's centre, if not it
    selector: str | None = None      # where the text was found, for the report


def _luminance(rgb) -> float:
    def channel(c: float) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(v) for v in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(color, background) -> float:
    """WCAG's contrast ratio of a text colour over an opaque background, the colour's
    alpha composited first (a transparent text has the background's colour: 1.0)."""
    a = color[3] if len(color) > 3 else 1.0
    fg = [color[i] * a + background[i] * (1 - a) for i in range(3)]
    hi, lo = sorted((_luminance(fg), _luminance(background)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _hex(c) -> str:
    return "#" + "".join(f"{round(v):02x}" for v in c[:3])


def judge(f: Facts, min_contrast: float = MIN_CONTRAST) -> list[str]:
    """Why a reader can't see this text; empty when they can."""
    if not f.found:
        return ["not on the page"]
    out = []
    if f.display_none:
        out.append("not rendered: display none on it or a container")
    elif not f.rendered:
        out.append("not rendered: the page's rendered text lacks it (display, visibility "
                   "or content-visibility on part of it)")
    if f.visibility not in ("visible", ""):
        out.append(f"visibility: {f.visibility}")
    if f.width < 1 or f.height < 1:
        out.append(f"no size ({f.width:.0f}x{f.height:.0f} px)")
    elif (f.x + f.width <= 0 or f.y + f.height <= 0
          or (f.page_width and f.x >= f.page_width) or (f.page_height and f.y >= f.page_height)):
        out.append(f"off the page (at {f.x:.0f}, {f.y:.0f})")
    elif f.visible_ratio < MIN_VISIBLE_RATIO:
        out.append(f"cut off by its container ({f.visible_ratio:.0%} of it shows)")
    if f.opacity < MIN_OPACITY:
        out.append(f"transparent (opacity {f.opacity:.2f})")
    if f.clip:
        out.append(f"clipped ({f.clip})")
    if f.font_px < MIN_FONT_PX:
        out.append(f"too small to read ({f.font_px:g}px)")
    if f.color is not None and f.background is not None:
        ratio = contrast(f.color, f.background)
        if ratio < min_contrast:
            out.append(f"colour contrast {ratio:.2f}:1 ({_hex(f.color)} on "
                       f"{_hex(f.background)}), below {min_contrast:g}:1")
    if f.covered_by:
        out.append(f"covered by {f.covered_by}")
    return out


@dataclass
class Result:
    item: str
    layer: str
    locked_by: str
    text: str
    visible: bool
    reasons: list[str] = field(default_factory=list)
    where: str | None = None
    contrast: float | None = None

    def as_dict(self) -> dict:
        out = {"item": self.item, "layer": self.layer, "locked_by": self.locked_by,
               "text": self.text, "visible": self.visible}
        if self.reasons:
            out["reasons"] = self.reasons
        if self.where:
            out["where"] = self.where
        if self.contrast is not None:
            out["contrast"] = round(self.contrast, 2)
        return out


def verdicts(found: list[Target], facts: list[Facts], min_contrast: float) -> list[Result]:
    out = []
    for t, f in zip(found, facts):
        reasons = judge(f, min_contrast)
        ratio = (contrast(f.color, f.background)
                 if f.found and f.color is not None and f.background is not None else None)
        out.append(Result(t.item, t.layer, t.locked_by, t.text, not reasons, reasons,
                          f.selector, ratio))
    return out


# -- the browser ------------------------------------------------------------------

# Run in the page with the targets (normalized lines); returns one fact dict each. It
# finds the deepest element whose text holds the target, scrolls it into view, and
# reads its box, its styles and its ancestors', and what sits on top of its centre.
MEASURE_JS = r"""
(targets) => {
  const norm = (s) => (s || "").replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/[*_`#>~|]/g, "").replace(/\s+/g, " ").trim().toLowerCase();
  const rgba = (s) => {
    const m = (s || "").match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(/[ ,\/]+/).filter(Boolean).map(Number);
    return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1];
  };
  const describe = (el) => {
    let s = el.tagName.toLowerCase();
    if (el.id) s += "#" + el.id;
    const cls = (typeof el.className === "string" ? el.className : "").trim().split(/\s+/)
      .filter(Boolean).slice(0, 3);
    if (cls.length) s += "." + cls.join(".");
    return s;
  };
  const all = Array.from(document.body.querySelectorAll("*"))
    .filter((el) => !["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE"].includes(el.tagName));
  return targets.map((target) => {
    const holding = all.filter((el) => norm(el.textContent).includes(target));
    const deepest = holding.filter((el) => !holding.some((o) => o !== el && el.contains(o)));
    if (!deepest.length) return {found: false};
    // Prefer a copy a reader sees, when the same text is on the page twice.
    let best = null, bestScore = -1;
    for (const el of deepest) {
      const r = el.getBoundingClientRect();
      const score = (norm(el.innerText).includes(target) ? 2 : 0) + (r.width * r.height > 0 ? 1 : 0);
      if (score > bestScore) { best = el; bestScore = score; }
    }
    const el = best;
    // Scroll the page, never the element's containers: scrollIntoView would also scroll
    // a container the text was pushed out of, and so reveal what a reader can't see.
    const r0 = el.getBoundingClientRect();
    window.scrollTo(0, Math.max(0, r0.top + scrollY - innerHeight / 2));
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    let opacity = 1, displayNone = false, clip = null, background = null, bgUnknown = false;
    let vis = {left: r.left, top: r.top, right: r.right, bottom: r.bottom};
    for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
      const s = getComputedStyle(n);
      opacity *= parseFloat(s.opacity || "1");
      for (const m of (s.filter || "").matchAll(/opacity\(([\d.]+)(%?)\)/g))
        opacity *= parseFloat(m[1]) / (m[2] ? 100 : 1);
      if (/brightness\(0(\.0+)?%?\)/.test(s.filter || "") && !clip) clip = "filter: " + s.filter + " on " + describe(n);
      if (s.display === "none") displayNone = true;
      if (s.contentVisibility === "hidden") displayNone = true;
      if (!clip && s.clipPath && s.clipPath !== "none") clip = "clip-path: " + s.clipPath + " on " + describe(n);
      if (!clip && s.clip && s.clip !== "auto" && (s.position === "absolute" || s.position === "fixed"))
        clip = "clip: " + s.clip + " on " + describe(n);
      if (n !== el && /(hidden|clip|auto|scroll)/.test(s.overflowX + " " + s.overflowY)) {
        const b = n.getBoundingClientRect();
        if (/(hidden|clip|auto|scroll)/.test(s.overflowX)) { vis.left = Math.max(vis.left, b.left); vis.right = Math.min(vis.right, b.right); }
        if (/(hidden|clip|auto|scroll)/.test(s.overflowY)) { vis.top = Math.max(vis.top, b.top); vis.bottom = Math.min(vis.bottom, b.bottom); }
      }
      if (!background && !bgUnknown) {
        if (s.backgroundImage && s.backgroundImage !== "none") bgUnknown = true;
        else {
          const c = rgba(s.backgroundColor);
          if (c && c[3] > 0.5) background = c;
        }
      }
    }
    if (!background && !bgUnknown) background = [255, 255, 255, 1];
    const area = Math.max(0, r.width) * Math.max(0, r.height);
    const visArea = Math.max(0, vis.right - vis.left) * Math.max(0, vis.bottom - vis.top);
    let color = rgba(cs.color);
    const fill = rgba(cs.webkitTextFillColor);
    if (fill && cs.webkitTextFillColor !== cs.color) color = fill;
    let covered = null;
    const cx = (Math.max(vis.left, 0) + Math.min(vis.right, innerWidth)) / 2;
    const cy = (Math.max(vis.top, 0) + Math.min(vis.bottom, innerHeight)) / 2;
    if (visArea > 0 && cx >= 0 && cy >= 0 && cx < innerWidth && cy < innerHeight) {
      const top = document.elementFromPoint(cx, cy);
      if (top && top !== el && !el.contains(top) && !top.contains(el)) covered = describe(top);
      // A ::before or ::after drawn over the text belongs to the text's own element or a
      // container, so the hit test returns that element: look for an opaque positioned
      // pseudo-element on the way up to it.
      if (!covered && top) {
        for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
          for (const pseudo of ["::before", "::after"]) {
            const p = getComputedStyle(n, pseudo);
            const bg = rgba(p.backgroundColor);
            if (p.content && p.content !== "none" && p.content !== "normal"
                && /(absolute|fixed)/.test(p.position) && p.display !== "none"
                && ((bg && bg[3] > 0.5) || (p.backgroundImage && p.backgroundImage !== "none"))) {
              covered = describe(n) + pseudo;
            }
          }
          if (covered || n === top) break;
        }
      }
    }
    const doc = document.documentElement;
    return {
      found: true, rendered: norm(el.innerText).includes(target),
      x: r.left + scrollX, y: r.top + scrollY, width: r.width, height: r.height,
      page_width: innerWidth, page_height: Math.max(doc.scrollHeight, innerHeight),
      visibility: cs.visibility, display_none: displayNone, opacity: opacity, clip: clip,
      visible_ratio: area > 0 ? visArea / area : 0, font_px: parseFloat(cs.fontSize),
      color: color, background: bgUnknown ? null : background, covered_by: covered,
      selector: describe(el)
    };
  });
}
"""


class VisualUnavailable(RuntimeError):
    """Playwright (the visual extra) or its browser is not installed."""


def _playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise VisualUnavailable(
            f"verify-visible needs a browser, which chartwright installs only on request: "
            f"{INSTALL}") from e
    return sync_playwright


def measure(base_url: str, username: str, password: str, slug: str, texts: list[str], *,
            timeout_s: float = 60.0, viewport=(1600, 1200), screenshot: str | None = None,
            verify_tls: bool = True) -> list[Facts]:
    """Sign in to Superset in a headless Chromium, open the dashboard, and measure each
    text. Waits until every text is found or `timeout_s` passes, scrolling the page so a
    dashboard that renders as it scrolls draws its rows."""
    import time

    sync_playwright = _playwright()
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as e:  # noqa: BLE001 - Playwright's own error for a missing browser
            raise VisualUnavailable(f"Chromium could not start ({str(e).splitlines()[0]}); "
                                    f"install it with: playwright install chromium") from e
        try:
            ctx = browser.new_context(viewport={"width": viewport[0], "height": viewport[1]},
                                      ignore_https_errors=not verify_tls)
            page = ctx.new_page()
            page.set_default_timeout(timeout_s * 1000)
            page.goto(f"{base_url}/login/")
            page.wait_for_selector("input[type=password]")
            page.locator("input:not([type=password]):not([type=hidden])").first.fill(username)
            page.fill("input[type=password]", password)
            page.keyboard.press("Enter")
            page.wait_for_load_state("networkidle")
            if "/login" in page.url:
                raise RuntimeError(f"signing in to {base_url} as {username!r} failed")
            page.goto(f"{base_url}/superset/dashboard/{slug}/")
            page.wait_for_load_state("networkidle")
            deadline = time.monotonic() + timeout_s
            facts: list[dict] = []
            while True:
                for _ in range(4):   # draw rows that render on scroll
                    page.mouse.wheel(0, viewport[1])
                    page.wait_for_timeout(250)
                facts = page.evaluate(MEASURE_JS, texts)
                if all(f.get("found") for f in facts) or time.monotonic() > deadline:
                    break
                page.wait_for_timeout(1000)
            if screenshot:
                page.screenshot(path=screenshot, full_page=True)
        finally:
            browser.close()
    return [Facts(**{k: (tuple(v) if isinstance(v, list) else v) for k, v in f.items()})
            for f in facts]


def verify(std, spec, *, base_url: str, username: str, password: str,
           min_contrast: float = MIN_CONTRAST, timeout_s: float = 60.0,
           screenshot: str | None = None, verify_tls: bool = True, measurer=None) -> dict:
    """The verify-visible payload for one spec. `measurer` stands in for the browser in
    tests: (texts) -> [Facts]."""
    found, skipped = targets(std, spec)
    out: dict = {"stage": "visible", "slug": spec.dashboard.slug, "standard": std.name,
                 "min_contrast": min_contrast}
    if not found:
        out.update(ok=True, items=[], skipped=[s.__dict__ for s in skipped],
                   detail="no locked header or footer text to look for on this dashboard")
        return out
    texts = [t.match for t in found]
    facts = (measurer(texts) if measurer is not None else
             measure(base_url, username, password, spec.dashboard.slug, texts,
                     timeout_s=timeout_s, screenshot=screenshot, verify_tls=verify_tls))
    results = verdicts(found, facts, min_contrast)
    hidden = [r for r in results if not r.visible]
    out.update(ok=not hidden, items=[r.as_dict() for r in results],
               skipped=[s.__dict__ for s in skipped],
               hidden=sorted({r.item for r in hidden}))
    if screenshot:
        from pathlib import Path

        out["screenshot"] = Path(screenshot).as_posix()
    return out
